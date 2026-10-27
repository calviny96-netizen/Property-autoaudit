'use client';

/*
 * The shared "something is being processed" view: a progress bar, the stage, "about N min left", and a
 * sentence that rotates every few seconds (lib/processing-messages.ts). Fed by the `progress` JSON that
 * api/progress.py writes for reprocess jobs and uploads, polled by the page that shows it.
 */

import { useEffect, useState } from 'react';
import { Label, ProgressBar } from '@heroui/react';
import { processingMessage } from '@/lib/processing-messages';
import type { JobProgress } from '@/lib/types';
import { cn } from '@/lib/utils';

/** "about 8 min left", "less than a minute left", "estimating…" (null/undefined ETA). */
export function etaText(seconds: number | null | undefined, suffix = ' left') {
  if (seconds == null || !Number.isFinite(seconds)) return 'estimating…';
  if (seconds < 60) return `less than a minute${suffix}`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} min${suffix}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `about ${hours} h${rest ? ` ${rest} min` : ''}${suffix}`;
}

/** Short form for pills and cards: "8 min", "<1 min", "2 h 05 min". */
export function etaShort(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return '';
  if (seconds < 60) return '<1 min';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
}

/** "about 3 minutes" for the up-front estimate of a queued job. */
export function aboutMinutes(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return 'a few minutes';
  if (seconds < 60) return 'less than a minute';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
  const hours = Math.floor(minutes / 60);
  return `about ${hours} ${hours === 1 ? 'hour' : 'hours'}${minutes % 60 ? ` ${minutes % 60} min` : ''}`;
}

function elapsedText(seconds: number) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} min ${String(Math.round(seconds % 60)).padStart(2, '0')} s` : `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
}

/**
 * Seconds since the job started, kept ticking between polls. Polled values arrive every few seconds; the
 * clock keeps moving from `started_at` (or from the last `elapsed_s`) so the sentence and ETA advance smoothly.
 */
function useElapsed(progress: JobProgress | null | undefined) {
  const startedAt = progress?.started_at ? Date.parse(progress.started_at) : Number.NaN;
  const base = progress?.elapsed_s ?? 0;
  const [now, setNow] = useState(() => Date.now());
  const [seenAt] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (Number.isFinite(startedAt)) return Math.max(0, (now - startedAt) / 1000);
  return base + (now - seenAt) / 1000;
}

/** The rotating sentence: a new sentence mounts with a CSS opacity fade-in (no extra dependencies). */
function RotatingSentence({ elapsed, className }: { elapsed: number; className?: string }) {
  const message = processingMessage(elapsed);
  return (
    <p aria-live="polite" lang="id" className={cn('min-h-5 truncate text-sm text-muted', className)}>
      <span key={message} className="block animate-[xm-sentence-in_0.6s_ease-out]">{message}</span>
    </p>
  );
}

/** The elapsed-second clock and remaining ETA, adjusted for the time since the last poll. */
function remainingEta(progress: JobProgress | null | undefined, elapsed: number) {
  if (!progress || progress.eta_s == null) return null;
  const drift = Math.max(0, elapsed - (progress.elapsed_s ?? 0));
  return Math.max(0, progress.eta_s - drift);
}

export function ProcessingProgress({ progress, compact = false, title, className }: {
  progress: JobProgress | null | undefined; compact?: boolean; title?: string; className?: string;
}) {
  const elapsed = useElapsed(progress);
  const queued = !progress || progress.stage === 'queued' || !progress.started_at;
  const percent = Math.max(0, Math.min(100, Math.round(progress?.percent ?? 0)));
  const eta = queued ? (progress?.estimate_s ?? progress?.eta_s ?? null) : remainingEta(progress, elapsed);
  const stage = queued ? 'Waiting to start' : progress?.stage_label || 'Processing';
  const counted = !!progress?.total && !queued;

  if (compact) {
    return (
      <output aria-live="polite" className={cn('flex min-w-0 items-center gap-3 rounded-xl border border-accent/15 bg-accent-soft/60 px-3 py-2', className)}>
        <ProgressBar aria-label={title ?? 'Processing'} value={percent} isIndeterminate={queued && percent === 0} className="w-24 shrink-0 sm:w-36">
          <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
        </ProgressBar>
        <span className="shrink-0 text-sm font-semibold tabular-nums text-accent-soft-foreground">{percent}%</span>
        <span className="hidden shrink-0 text-sm text-accent-soft-foreground sm:inline">· {etaText(eta)}</span>
        <RotatingSentence elapsed={elapsed} className="min-w-0 flex-1" />
      </output>
    );
  }
  return (
    <output aria-live="polite" className={cn('block space-y-2.5 rounded-2xl border border-accent/15 bg-accent-soft/60 p-4', className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="text-body font-semibold text-accent-soft-foreground">{title ?? 'Updating results…'}</span>
        <span className="text-sm font-semibold text-accent-soft-foreground">{etaText(eta)}</span>
      </div>
      <ProgressBar aria-label={title ?? 'Processing'} value={percent} isIndeterminate={queued && percent === 0}>
        <Label className="text-sm text-foreground/80">
          {stage}{counted ? ` · ${progress!.done.toLocaleString('en-US')} of ${progress!.total.toLocaleString('en-US')}` : ''}
        </Label>
        <ProgressBar.Output className="text-sm font-semibold tabular-nums" />
        <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
      </ProgressBar>
      <div className="flex min-w-0 items-center justify-between gap-3">
        <RotatingSentence elapsed={elapsed} className="min-w-0 flex-1" />
        <span className="shrink-0 text-caption tabular-nums text-muted">{queued ? '' : `elapsed ${elapsedText(elapsed)}`}</span>
      </div>
    </output>
  );
}
