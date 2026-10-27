'use client';

/*
 * Saving a setting only saves it: matches are brought up to date when a company admin presses
 * "Reprocess now" (POST /reprocess). This file holds the shared status for the Settings page,
 * the banner that explains it, and the toast every save shows.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button, toast } from '@heroui/react';
import { RefreshCw } from 'lucide-react';
import { ProcessingProgress } from '@/components/app/processing-progress';
import { Notice } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { relativeDateTime } from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { MaintenanceJob, ReprocessStatus } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

/** GET /reprocess/status and POST /reprocess. */
export type { ReprocessStatus } from '@/lib/types';

type ReprocessValue = {
  status: ReprocessStatus | null;
  running: boolean;
  starting: boolean;
  admin: boolean;
  refresh: () => void;
  reprocess: () => void;
};

const ReprocessContext = createContext<ReprocessValue | null>(null);
const isRunning = (job?: Pick<MaintenanceJob, 'status'> | null) => job?.status === 'queued' || job?.status === 'processing';

/**
 * True only when a change was saved after the running update was queued, so that update does not cover it.
 * The server keeps `pending` until a job finishes; a change it covers has `since` at or before the job's
 * `created_at`, while a change saved during processing is stamped later.
 */
function savedDuringUpdate(status: ReprocessStatus) {
  if (!status.pending || !status.since || !status.job?.created_at) return false;
  const since = Date.parse(status.since);
  const queued = Date.parse(status.job.created_at);
  return Number.isFinite(since) && Number.isFinite(queued) && since > queued;
}

/** "Reprocess now" pressed while an update was running: the server runs another one right after it. */
const followUpQueued = (job: MaintenanceJob | null) => !!(job?.result as { scope_changed?: boolean } | null | undefined)?.scope_changed;

/** Keeps the "matches are not updated yet" status fresh while the Settings page is open. */
export function ReprocessProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const { company, refresh: refreshCompany } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const [status, setStatus] = useState<ReprocessStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const job = status?.job ?? null;
  const running = isRunning(job);

  const load = useCallback(() => {
    void api.get<ReprocessStatus>('/reprocess/status').then(setStatus).catch(() => {});
  }, [api]);

  useEffect(() => { load(); }, [load]);

  // Quick checks while results are updating (the maintenance status is cheap), slow ones otherwise.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!running) { load(); return; }
      void api.get<MaintenanceJob | null>('/index/status').then((latest) => {
        if (!latest) return;
        if (isRunning(latest)) setStatus((current) => (current ? { ...current, job: latest } : current));
        else load();
      }).catch(() => {});
    }, running ? 3000 : 30_000);
    return () => window.clearInterval(timer);
  }, [api, running, load]);

  // Announce the moment an update we watched finishes.
  const watched = useRef<{ id?: string; status?: string }>({});
  useEffect(() => {
    const before = watched.current;
    watched.current = { id: job?.id, status: job?.status };
    const wasRunning = before.status === 'queued' || before.status === 'processing';
    if (!job || before.id !== job.id || !wasRunning) return;
    if (job.status === 'completed') { toast.success('Results updated'); refreshCompany(); }
    if (job.status === 'failed') toast.danger('Results could not be updated. Please try again.');
  }, [job, refreshCompany]);

  const reprocess = useCallback(() => {
    setStarting(true);
    void api.post<ReprocessStatus>('/reprocess')
      .then((next) => { setStatus(next); toast('Updating results…', { description: 'You can keep working. This takes a few minutes.' }); })
      .catch((reason) => toast.danger(errorMessage(reason, 'Results could not be updated. Please try again.')))
      .finally(() => setStarting(false));
  }, [api]);

  const value = useMemo(() => ({ status, running, starting, admin, refresh: load, reprocess }), [status, running, starting, admin, load, reprocess]);
  return <ReprocessContext.Provider value={value}>{children}</ReprocessContext.Provider>;
}

/** The shared status; outside the Settings page it is inert (refresh does nothing). */
export function useReprocess(): ReprocessValue {
  return useContext(ReprocessContext) ?? { status: null, running: false, starting: false, admin: false, refresh: () => {}, reprocess: () => {} };
}

/**
 * The toast after a save. `pending: false` when the save changed nothing that needs a reprocess
 * (or the company has no data yet); otherwise it reminds people that results update on reprocess.
 */
export function useSaveNotice() {
  const context = useContext(ReprocessContext);
  const refresh = context?.refresh;
  return useCallback((title = 'Saved', options: { pending?: boolean } = {}) => {
    toast.success(title, options.pending === false ? undefined : { description: 'Results will update when you reprocess.' });
    refresh?.();
  }, [refresh]);
}

/** "Settings changed — matches are not updated yet" with the reprocess action, or the running update. */
export function ReprocessBanner({ className }: { className?: string }) {
  const { status, running, starting, admin, reprocess } = useReprocess();
  if (!status) return null;
  // Reasons are short English phrases from the server; "location index" is internal wording.
  const reasons = status.reasons.length ? `${status.reasons.map((reason) => reason.replace(/location index/i, 'Locations')).join(' · ')}.` : '';

  if (running) {
    return (
      <div className={className}>
        <ProcessingProgress progress={status.job?.progress} title="Updating results… you can keep working." />
        {savedDuringUpdate(status) && (
          <p className="mt-2 px-1 text-caption text-muted">
            {followUpQueued(status.job)
              ? 'Some changes were saved after this update started. Results update again right after it.'
              : `Some changes were saved after this update started. ${admin ? 'Reprocess again when it is done.' : 'An admin needs to reprocess again when it is done.'}`}
          </p>
        )}
      </div>
    );
  }
  if (status.pending) {
    return (
      <Notice status="warning" className={className} title="Settings changed — matches are not updated yet">
        {reasons && <span className="block">{reasons}</span>}
        <span className="block text-muted">
          {status.since ? `Changed ${relativeDateTime(status.since).replace(/^(Today|Yesterday)/, (word) => word.toLowerCase())}. ` : ''}
          {admin ? 'Reprocess when you have finished your changes. It takes a few minutes and you can keep working.' : 'Matches still follow the earlier settings until then.'}
        </span>
        <span className="mt-3 block">
          {admin
            ? <Button size="sm" isPending={starting} onPress={reprocess}><RefreshCw className="size-4" aria-hidden="true" />Reprocess now</Button>
            : <span className="text-sm font-semibold text-foreground">An admin needs to reprocess.</span>}
        </span>
      </Notice>
    );
  }
  // The latest update failed (and nothing newer is pending): results may still be the old ones.
  if (status.job?.status === 'failed') {
    return (
      <Notice status="danger" className={className} title="Results could not be updated">
        Your settings are saved. {admin ? 'Please try updating the results again.' : 'Ask an admin to reprocess.'}
        {admin && <span className="mt-3 block"><Button size="sm" variant="danger-soft" isPending={starting} onPress={reprocess}>Try again</Button></span>}
      </Notice>
    );
  }
  return null;
}
