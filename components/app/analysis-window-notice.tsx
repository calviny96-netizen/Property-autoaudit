'use client';

/*
 * Only the last few months are analysed by default (api/analysis_window.py). When a page looks at a period
 * that starts before that, this notice says so and lets a company admin extend the window ("Analyse older
 * data" → POST /reprocess {from}); the progress of that run then shows in place. `RunningJobBar` is the slim
 * version for list pages: it only appears while a reprocess is running.
 */

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@heroui/react';
import { History } from 'lucide-react';
import { ProcessingProgress } from '@/components/app/processing-progress';
import { ConfirmDialog, Notice } from '@/components/app/primitives';
import { errorMessage, DATA_CHANGED_EVENT, type DataChangedDetail } from '@/lib/api';
import { dateOnly } from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { MaintenanceJob, ReprocessStatus } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

const isRunning = (job?: Pick<MaintenanceJob, 'status'> | null) => job?.status === 'queued' || job?.status === 'processing';
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The reprocess status with the latest job kept fresh: every 3 s while a job runs (GET /index/status),
 * otherwise on mount and after any write. Shared by the notice and the slim bar.
 */
export function useAnalysisStatus(enabled = true) {
  const api = useApi();
  const [status, setStatus] = useState<ReprocessStatus | null>(null);
  const running = isRunning(status?.job);

  const load = useCallback(() => {
    void api.get<ReprocessStatus>('/reprocess/status').then(setStatus).catch(() => {});
  }, [api]);

  useEffect(() => {
    if (!enabled) return;
    load();
    const onWrite = (event: Event) => {
      const path = (event as CustomEvent<DataChangedDetail>).detail?.path ?? '';
      if (/^\/(?:reprocess|index\/recompute|settings|imports)/.test(path)) load();
    };
    window.addEventListener(DATA_CHANGED_EVENT, onWrite);
    return () => window.removeEventListener(DATA_CHANGED_EVENT, onWrite);
  }, [enabled, load]);

  useEffect(() => {
    if (!enabled || !running) return;
    const timer = window.setInterval(() => {
      void api.get<MaintenanceJob | null>('/index/status').then((latest) => {
        if (!latest) return;
        if (isRunning(latest)) setStatus((current) => (current ? { ...current, job: latest } : current));
        else load();
      }).catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [api, enabled, running, load]);

  return { status, running, setStatus, refresh: load };
}

/** True when `periodStart` (ISO day, '' = all dates) reaches before the analysed window. */
export function beforeWindow(periodStart: string, status: ReprocessStatus | null) {
  if (!status?.analysis_from) return false;
  if (!periodStart) return !!status.has_older_data;
  return ISO_DAY.test(periodStart) && periodStart < status.analysis_from && !!status.has_older_data;
}

/**
 * "Data before {date} has not been analysed yet" for a period that starts earlier than the analysis window,
 * with "Analyse older data" for admins. Shows the run's progress once started. Renders nothing otherwise.
 */
export function AnalysisWindowNotice({ periodStart, className, showProgress = true }: {
  periodStart: string; className?: string;
  /** Show the running job's progress here (off when the page has its own RunningJobBar). */
  showProgress?: boolean;
}) {
  const api = useApi();
  const { company } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const { status, running, setStatus } = useAnalysisStatus(!!company);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (!status) return null;
  if (running && status.job) {
    return showProgress ? <ProcessingProgress className={className} progress={status.job.progress} title="Analysing data…" /> : null;
  }
  if (!beforeWindow(periodStart, status)) return null;
  const from = dateOnly(status.analysis_from);
  // "All dates" extends as far back as the oldest stored message; the server never moves the start later.
  const start = periodStart && ISO_DAY.test(periodStart) ? periodStart : '1970-01-01';

  const analyse = () => {
    setBusy(true); setError('');
    void api.post<ReprocessStatus>('/reprocess', { from: start })
      .then((next) => { setStatus(next); setConfirm(false); })
      .catch((reason) => setError(errorMessage(reason, 'The analysis could not be started. Please try again.')))
      .finally(() => setBusy(false));
  };

  return (
    <>
      <Notice status="warning" className={className} title={`Data before ${from} has not been analysed yet`}
        action={admin
          ? <Button size="sm" variant="secondary" onPress={() => setConfirm(true)}><History className="size-4" aria-hidden="true" />Analyse older data</Button>
          : <span className="text-sm font-semibold text-foreground">Ask your company admin</span>}>
        Matches and insights only cover messages from {from}.
        {error && <span className="mt-1 block text-danger">{error}</span>}
      </Notice>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Analyse older data?" status="accent" confirmLabel="Analyse older data" busy={busy} onConfirm={analyse}>
        This reprocesses everything from {periodStart && ISO_DAY.test(periodStart) ? dateOnly(periodStart) : 'the oldest message'}.
        It can take a while; you will see the progress and the estimated time.
      </ConfirmDialog>
    </>
  );
}

/** A slim progress bar above a list while a reprocess runs; nothing otherwise. */
export function RunningJobBar({ className }: { className?: string }) {
  const { company } = useCompany();
  const { status, running } = useAnalysisStatus(!!company);
  if (!running || !status?.job) return null;
  return <ProcessingProgress compact className={className} progress={status.job.progress} title="Updating results" />;
}
