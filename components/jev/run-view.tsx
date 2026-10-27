'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Chip, ProgressBar, toast } from '@heroui/react';
import { ArrowRight, Ban, FileArchive, FileText, RefreshCw, Undo2 } from 'lucide-react';
import {
  Collapsible, DetailList, EmptyState, ErrorNotice, IdChip, Notice, PageHeader, Panel, ReasonList, SkeletonList, StatusBadge,
} from '@/components/app/primitives';
import { DecisionBadge } from '@/components/jev/jev-badge';
import { errorMessage } from '@/lib/api';
import { countLabel, dateTime, number, prettyRange, todayWib } from '@/lib/format';
import { DECISION_LABELS, DECISION_STYLES, DECISION_SYMBOLS, RUN_STATUS_COLORS, RUN_STATUS_LABELS, highlightPieces, isActiveRun, percent, usd } from '@/lib/jev';
import type { Direction, JevDecision, JevResultGroup, JevResults, JevReview, JevRun } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { cn } from '@/lib/utils';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/workspace-context';

type Filter = 'all' | JevDecision | 'failed' | 'pending';
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'recommend', label: '✓ Recommended' }, { id: 'reject', label: '✗ Rejected' },
  { id: 'unsure', label: '? Unsure' }, { id: 'failed', label: 'Failed' }, { id: 'pending', label: 'Waiting' },
];
const DIRECTION_TITLES: Record<Direction, string> = { property: 'Listing → Buyer', buyer: 'Buyer → Listing' };
const ITEMS_STEP = 40;

function countFor(run: JevRun, filter: Filter) {
  if (filter === 'all') return run.total;
  if (filter === 'pending') return run.pending;
  return run[filter];
}

/** One review run: progress, cost, Jev's verdicts per sender / sales contact, overrides and the Jev-approved export. */
export function RunView({ runId, onBack }: { runId: string; onBack: () => void }) {
  const api = useApi();
  const { user } = useSession();
  const technical = user.role === 'admin';
  const [filter, setFilter] = useState<Filter>('all');
  const [tick, setTick] = useState(0);
  const [resultsTick, setResultsTick] = useState(0);
  const summary = useData((signal) => api.get<JevRun>(`/ai/review-runs/${runId}`, signal), [runId, tick]);
  const results = useData((signal) => api.get<JevResults>(`/ai/review-runs/${runId}/results?decision=${filter}`, signal), [runId, filter, resultsTick]);
  const run = summary.data ?? results.data?.run;
  const active = isActiveRun(run?.status);
  const [cancelling, setCancelling] = useState(false);
  const [exporting, setExporting] = useState<'' | 'grouped' | 'single'>('');

  // Live progress every 3 s; the (heavier) result list every 9 s and once more when the run ends.
  useEffect(() => {
    if (!active) return;
    let beats = 0;
    const timer = window.setInterval(() => {
      beats += 1;
      setTick((value) => value + 1);
      if (beats % 3 === 0) setResultsTick((value) => value + 1);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [active]);
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) setResultsTick((value) => value + 1);
    wasActive.current = active;
  }, [active]);

  async function cancel() {
    setCancelling(true);
    try {
      summary.setData(await api.post<JevRun>(`/ai/review-runs/${runId}/cancel`));
      toast('Review cancelled', { description: 'Pairs Jev already answered stay in the results.' });
    } catch (reason) { toast.danger(errorMessage(reason, 'The review could not be cancelled.')); } finally { setCancelling(false); }
  }

  async function exportApproved(output: 'grouped' | 'single') {
    setExporting(output);
    try {
      const blob = await api.file(`/ai/review-runs/${runId}/export?output=${output}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `Jev-Approved-${todayWib()}.${output === 'grouped' ? 'zip' : 'pdf'}`; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      toast.success(output === 'grouped' ? 'ZIP with one PDF per person downloaded' : 'PDF downloaded');
    } catch (reason) { toast.danger(errorMessage(reason, 'The export could not be created.')); } finally { setExporting(''); }
  }

  function updated(next: JevReview & { also_updated?: number[] }) {
    results.setData((current) => current ? {
      ...current, groups: current.groups.map((group) => ({ ...group, items: group.items.map((item) => (item.id === next.id ? next : item)) })),
    } : current);
    setTick((value) => value + 1);
    // The same pair in the other direction (under the other person) changed too.
    if (next.also_updated?.length) setResultsTick((value) => value + 1);
  }

  const fraction = run && run.total ? (run.done + run.failed) / run.total : 0;
  const payload = run?.payload ?? {};
  const statuses = (payload.statuses ?? []).map((status) => status[0].toUpperCase() + status.slice(1)).join(', ');
  const dates = (from?: string, to?: string) => (from || to ? prettyRange(from ?? '', to ?? '') : 'All dates');

  return (
    <div className="space-y-6">
      <PageHeader back={{ label: 'All AI Reviews', onPress: onBack }} title="AI Review"
        description={run ? `${run.model_label} · started ${dateTime(run.created_at)}${run.created_by_name ? ` by ${run.created_by_name}` : ''}` : undefined}
        actions={run && (
          <>
            {active && <Button variant="danger-soft" isPending={cancelling} onPress={cancel}><Ban className="size-4" aria-hidden="true" />Cancel review</Button>}
            <Button isDisabled={!run.recommend || !!exporting} isPending={exporting === 'grouped'} onPress={() => void exportApproved('grouped')}>
              <FileArchive className="size-4" aria-hidden="true" />Export Jev-approved
            </Button>
            <Button variant="secondary" isDisabled={!run.recommend || !!exporting} isPending={exporting === 'single'} onPress={() => void exportApproved('single')}>
              <FileText className="size-4" aria-hidden="true" />One PDF
            </Button>
          </>
        )} />

      <ErrorNotice message={summary.error && !run ? summary.error : ''} onRetry={summary.reload} title="The review could not be loaded" />
      {!run && !summary.error && <SkeletonList rows={2} />}

      {run && (
        <Panel title={<span className="flex flex-wrap items-center gap-2">Progress <Chip size="sm" variant="soft" color={RUN_STATUS_COLORS[run.status]}>{RUN_STATUS_LABELS[run.status]}</Chip></span>}
          actions={<Button isIconOnly size="sm" variant="ghost" aria-label="Refresh" onPress={() => { setTick((v) => v + 1); setResultsTick((v) => v + 1); }}><RefreshCw className="size-4" /></Button>}>
          <div className="space-y-5">
            {run.status === 'failed' && run.error && <Notice status="danger" title="The review stopped">{run.error}</Notice>}
            <ProgressBar aria-label="Review progress" value={Math.round(fraction * 100)} color="accent" size="lg">
              <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
                <span className="font-semibold">{number(run.done + run.failed)} of {countLabel(run.total, 'pair')} reviewed</span>
                <ProgressBar.Output className="font-semibold tabular-nums" />
              </div>
              <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
            </ProgressBar>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="✓ Recommended" value={number(run.recommend)} tone="text-success" />
              <Stat label="✗ Rejected" value={number(run.reject)} tone="text-danger" />
              <Stat label="? Unsure" value={number(run.unsure)} tone="text-warning" />
              <Stat label="Failed" value={number(run.failed)} />
              <Stat label={active ? 'Waiting' : 'Not reviewed'} value={number(run.pending)} />
              <Stat label={run.cost_estimated ? 'Cost (part estimated)' : 'Cost'} value={usd(run.cost, technical)} hint={`${number(run.tokens_in + run.tokens_out)} tokens`} />
            </div>
            <Collapsible title="What was reviewed" description={`${countLabel(run.total, 'pair')} · ${number(run.cached)} reused answers`}>
              <DetailList columns={2} items={[
                { label: 'Model', value: <span>{run.model_label} <span className="font-mono text-caption text-muted">{run.model}</span></span> },
                ...(technical ? [{ label: 'API key', value: run.key_label ? `${run.key_label} ${run.key_masked ?? ''}` : 'Deleted' }] : []),
                { label: 'Directions', value: payload.directions === 'both' ? 'Both directions' : DIRECTION_TITLES[(payload.directions ?? 'buyer') as Direction] },
                { label: 'Pair statuses', value: statuses },
                { label: 'Buyers posted', value: dates(payload.buyer_date_from, payload.buyer_date_to) },
                { label: 'Listings posted', value: dates(payload.listing_date_from, payload.listing_date_to) },
                { label: 'People', value: payload.group_keys ? countLabel(payload.group_keys.length, 'selected person', 'selected people') : 'Everyone' },
                { label: 'Prompt version', value: run.prompt_version },
              ]} />
            </Collapsible>
          </div>
        </Panel>
      )}

      {run && (
        <section aria-label="Jev's results" className="space-y-4">
          <fieldset aria-label="Show" className="xm-scroll-x -mx-4 flex min-w-0 gap-2 border-0 px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
            {FILTERS.map((item) => {
              const on = filter === item.id;
              return (
                <Button key={item.id} variant={on ? 'primary' : 'secondary'} className="shrink-0" aria-pressed={on} onPress={() => setFilter(item.id)}>
                  {item.id === 'pending' && !active ? 'Not reviewed' : item.label} <span className="tabular-nums opacity-75">{number(countFor(run, item.id))}</span>
                </Button>
              );
            })}
          </fieldset>
          <ErrorNotice message={results.error} onRetry={results.reload} />
          {results.loading && !results.data && <SkeletonList rows={3} />}
          {results.data && results.data.groups.length === 0 && (
            <EmptyState compact title={active ? 'Jev is still working' : 'Nothing here'} description={active ? 'Answers appear here as AI Reviews the pairs.' : 'No pair has this result.'} />
          )}
          {results.data?.groups.map((group, index) => (
            <GroupSection key={`${filter}:${group.key}`} group={group} defaultOpen={index === 0} active={active} onUpdated={updated} />
          ))}
        </section>
      )}
    </div>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-surface-secondary px-3 py-2.5">
      <p className="text-caption text-muted">{label}</p>
      <p className={cn('text-xl font-semibold tabular-nums', tone ?? 'text-foreground')}>{value}</p>
      {hint && <p className="text-caption text-muted">{hint}</p>}
    </div>
  );
}

function GroupSection({ group, defaultOpen, active, onUpdated }: { group: JevResultGroup; defaultOpen: boolean; active: boolean; onUpdated: (item: JevReview) => void }) {
  const [open, setOpen] = useState(defaultOpen);
  const [shown, setShown] = useState(ITEMS_STEP);
  // Counted here (not taken from the server) so an override shows up at once.
  const counts = useMemo(() => {
    const result: JevResultGroup['counts'] = { recommend: 0, reject: 0, unsure: 0, failed: 0, pending: 0 };
    for (const item of group.items) result[item.status === 'done' ? item.effective_decision ?? 'unsure' : item.status] += 1;
    return result;
  }, [group.items]);
  return (
    <Collapsible open={open} onOpenChange={setOpen} title={group.title || group.key || 'Unknown sender'}
      description={countLabel(group.items.length, 'pair')}
      badge={(
        <span className="hidden flex-wrap gap-1.5 sm:flex">
          {(['recommend', 'reject', 'unsure'] as const).map((decision) => (counts[decision] ? (
            <span key={decision} title={DECISION_LABELS[decision]} className={cn('inline-flex h-6 items-center gap-1 rounded-full px-2 text-xs font-semibold tabular-nums', DECISION_STYLES[decision])}>
              <span aria-hidden="true">{DECISION_SYMBOLS[decision]}</span>{number(counts[decision])}<span className="sr-only"> {DECISION_LABELS[decision].toLowerCase()}</span>
            </span>
          ) : null))}
        </span>
      )}>
      {open && (
        <div className="space-y-3">
          <p className="text-caption text-muted">
            {(['recommend', 'reject', 'unsure'] as const).map((decision) => `${number(counts[decision])} ${DECISION_LABELS[decision].toLowerCase()}`).join(' · ')}
            {counts.failed ? ` · ${number(counts.failed)} failed` : ''}{counts.pending ? ` · ${number(counts.pending)} ${active ? 'waiting' : 'not reviewed'}` : ''}
          </p>
          {group.items.slice(0, shown).map((item) => <ReviewCard key={item.id} item={item} active={active} onUpdated={onUpdated} />)}
          {group.items.length > shown && <Button variant="secondary" fullWidth onPress={() => setShown((value) => value + ITEMS_STEP)}>Show {number(Math.min(ITEMS_STEP, group.items.length - shown))} more</Button>}
        </div>
      )}
    </Collapsible>
  );
}

function Highlighted({ text, phrases }: { text: string; phrases?: string[] }) {
  const pieces = useMemo(() => highlightPieces(text, phrases), [text, phrases]);
  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/85">
      {pieces.map((piece, index) => (piece.mark ? <mark key={index} className="rounded bg-yellow-200/80 px-0.5 text-foreground">{piece.text}</mark> : <span key={index}>{piece.text}</span>))}
    </p>
  );
}

function ReviewCard({ item, active, onUpdated }: { item: JevReview; active: boolean; onUpdated: (item: JevReview) => void }) {
  const api = useApi();
  const [busy, setBusy] = useState<JevDecision | 'reset' | ''>('');
  const sourceKind = item.direction === 'buyer' ? 'buyer' : 'property';
  const targetKind = item.direction === 'buyer' ? 'property' : 'buyer';
  const buyerPhrases = item.direction === 'buyer' ? item.evidence.source : item.evidence.target;
  const listingPhrases = item.direction === 'buyer' ? item.evidence.target : item.evidence.source;
  const done = item.status === 'done';

  async function override(decision: JevDecision | null) {
    setBusy(decision ?? 'reset');
    try {
      onUpdated(await api.put<JevReview>(`/ai/reviews/${item.id}/override`, { decision }));
      toast.success(decision ? `Marked as ${DECISION_LABELS[decision].toLowerCase()}` : 'Back to Jev’s decision',
        { description: 'Applies to this pair in both directions.' });
    } catch (reason) { toast.danger(errorMessage(reason, 'The decision could not be changed.')); } finally { setBusy(''); }
  }

  return (
    <article className="rounded-2xl border border-border bg-surface p-4 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <DecisionBadge decision={item.effective_decision} status={item.status} pendingLabel={active ? 'Waiting' : 'Not reviewed'} />
        {done && item.confidence !== null && <span className="text-sm text-muted">Confidence <strong className="font-semibold text-foreground tabular-nums">{percent(item.confidence)}</strong></span>}
        {item.override_decision && <Chip size="sm" variant="soft" color="accent">Set by {item.override_by_name ?? 'an admin'}{item.decision ? ` · Jev: ${DECISION_LABELS[item.decision]}` : ''}</Chip>}
        {item.cached && done && <span className="text-caption text-muted">Reused answer</span>}
        <span className="ml-auto flex items-center gap-1.5 text-caption text-muted">Rules <StatusBadge status={item.engine_status} size="sm" explain={false} /></span>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <span className="text-caption font-semibold text-muted">{DIRECTION_TITLES[item.direction]}</span>
        <IdChip id={item.source_public_id ?? undefined} kind={sourceKind} />
        <ArrowRight className="size-4 text-muted" aria-hidden="true" />
        <IdChip id={item.target_public_id ?? undefined} kind={targetKind} />
      </div>
      {!!item.reasons.length && (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-foreground/90" lang="id" aria-label="Jev's reasons">
          {item.reasons.map((reason, index) => <li key={index}>{reason}</li>)}
        </ul>
      )}
      {!!item.issues.length && (
        <ul className="mt-2 space-y-1 text-sm" lang="id" aria-label="Jev's notes to check">
          {item.issues.map((issue, index) => <li key={index}><span className="font-semibold text-warning-soft-foreground">Check: </span>{issue}</li>)}
        </ul>
      )}
      {item.error && <p className="mt-2 text-caption break-words text-danger">{item.error}</p>}
      <details className="mt-3 rounded-xl border border-border">
        <summary className="xm-summary flex min-h-10 cursor-pointer items-center px-3 text-sm font-semibold">Messages and the rules’ reasons</summary>
        <div className="space-y-4 border-t border-separator p-3">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="min-w-0"><p className="mb-1 text-caption font-semibold text-muted">Buyer request</p><Highlighted text={item.buyer_text} phrases={buyerPhrases} /></div>
            <div className="min-w-0"><p className="mb-1 text-caption font-semibold text-muted">Property listing</p><Highlighted text={item.listing_text} phrases={listingPhrases} /></div>
          </div>
          <div>
            <p className="mb-1 text-caption font-semibold text-muted">Matching rules: {item.engine_status}{item.engine_score !== null ? ` (score ${number(item.engine_score)})` : ''}</p>
            <ReasonList reasons={item.engine_explanation} limit={8} />
          </div>
        </div>
      </details>
      {done && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-separator pt-3">
          <span className="text-caption text-muted">Your decision:</span>
          {(['recommend', 'reject', 'unsure'] as const).map((decision) => {
            const on = item.effective_decision === decision;
            return (
              <Button key={decision} size="sm" variant={on ? 'primary' : 'secondary'} aria-pressed={on} isDisabled={!!busy} isPending={busy === decision}
                onPress={() => { if (!on) void override(decision === item.decision ? null : decision); }}>{DECISION_LABELS[decision]}</Button>
            );
          })}
          {item.override_decision && (
            <Button size="sm" variant="tertiary" isDisabled={!!busy} isPending={busy === 'reset'} onPress={() => void override(null)}><Undo2 className="size-4" aria-hidden="true" />Use Jev’s decision</Button>
          )}
        </div>
      )}
    </article>
  );
}
