'use client';

import { useEffect, useRef, useState, type DragEvent } from 'react';
import { Button, Chip, Input, Label, TextField } from '@heroui/react';
import { ArrowRight, Check, FileJson, History, TriangleAlert, UploadCloud, X } from 'lucide-react';
import { EmptyState, ErrorNotice, Notice, PageHeader, Panel, Segmented, SkeletonList, StatusBadge } from '@/components/app/primitives';
import { ProcessingProgress, aboutMinutes } from '@/components/app/processing-progress';
import { LottiePlayer } from '@/components/lottie/lottie-player';
import { Confetti, type ConfettiRef } from '@/components/magicui/confetti';
import { ApiError, errorMessage } from '@/lib/api';
import { countLabel, dateTime, decimal, formatPhone, number } from '@/lib/format';
import { ROUTES, type Navigate } from '@/lib/router';
import { useCompany } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { ImportRow, ImportStatus, MatchingMode, MatchStatus } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

const STATUS: Record<ImportStatus, { label: string; color: 'default' | 'accent' | 'success' | 'danger' | 'warning' }> = {
  queued: { label: 'Waiting', color: 'warning' },
  processing: { label: 'Processing', color: 'accent' },
  completed: { label: 'Done', color: 'success' },
  failed: { label: 'Failed', color: 'danger' },
};
const MODE_LABELS: Record<MatchingMode, string> = { company: 'Company keywords', sales: 'Sales watchlist' };

/*
 * The stored error of a failed upload is a technical message for the team that runs the service
 * (database, search or parser errors). People only see what to do next.
 */
const UNREADABLE_FILE = /line \d+ column \d+ \(char \d+\)|codec can't decode/i; // JSON syntax or text encoding errors
function importFailure(error?: string | null) {
  return error && UNREADABLE_FILE.test(error)
    ? 'This chat could not be processed because the file could not be read. Export the chat again and upload the new file.'
    : 'This chat could not be processed. Please upload it again. If it keeps failing, let us know.';
}

/** The upload request itself was refused: never show the parser's raw JSON error. */
function uploadFailure(reason: unknown) {
  if (reason instanceof ApiError && reason.status === 400 && /^Unrecognised JSON format/i.test(reason.message)) {
    return 'This file is not a chat export we can read. Use the cleaned.json file from the WhatsApp chat export.';
  }
  return errorMessage(reason, 'The upload did not work. Check the file and try again.');
}

function steps(item: ImportRow) {
  const stage = item.progress?.stage;
  const reading = item.status === 'processing' && (stage ? stage === 'read' || stage === 'parse' || stage === 'index' : item.processed_messages < item.total_messages);
  const matching = item.status === 'processing' && (stage ? stage === 'match' || stage === 'finish' : item.total_messages > 0 && item.processed_messages >= item.total_messages);
  const done = item.status === 'completed';
  return [
    { label: 'Received', state: 'done' as const },
    { label: 'Reading messages', state: done || matching ? ('done' as const) : reading || item.status === 'processing' ? ('active' as const) : ('todo' as const) },
    { label: 'Finding matches', state: done ? ('done' as const) : matching ? ('active' as const) : ('todo' as const) },
    { label: 'Done', state: done ? ('done' as const) : ('todo' as const) },
  ];
}

/** New pairs by status, e.g. Hot · 3  Warm · 12  Custom · 2 (only the statuses that have any). */
function NewMatchCounts({ item }: { item: Pick<ImportRow, 'new_hot' | 'new_warm' | 'new_custom'> }) {
  const counts: [MatchStatus, number][] = [['hot', item.new_hot], ['warm', item.new_warm], ['custom', item.new_custom ?? 0]];
  return (
    <span className="inline-flex flex-wrap gap-1.5">
      {counts.filter(([, count]) => count > 0).map(([status, count]) => <StatusBadge key={status} status={status} count={count} size="sm" explain={false} />)}
    </span>
  );
}

export default function UploadPage({ navigate }: { navigate?: Navigate }) {
  const api = useApi();
  const { company, refresh } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const [selectedMode, setSelectedMode] = useState<MatchingMode | null>(null);
  const matchingMode = selectedMode ?? company?.matching_mode ?? 'company';
  const noWatchlist = matchingMode === 'sales' && !company?.tracked_phones.length;
  const input = useRef<HTMLInputElement>(null);
  const confetti = useRef<ConfettiRef>(null);
  const [agent, setAgent] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [celebrate, setCelebrate] = useState<ImportRow | null>(null);
  // The up-front estimate for the chosen file: messages counted from the JSON, seconds from GET /imports/estimate.
  const [estimate, setEstimate] = useState<{ name: string; messages: number; seconds: number | null } | null>(null);
  const seen = useRef<Record<string, string>>({});

  const imports = useData((signal) => api.get<ImportRow[]>('/imports', signal), []);
  const reload = imports.reload;
  const active = imports.data?.some((item) => item.status === 'queued' || item.status === 'processing');

  // While something is being processed, look again every few seconds; announce the moment it finishes.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(reload, 2500);
    return () => window.clearInterval(timer);
  }, [active, reload]);
  useEffect(() => {
    for (const item of imports.data ?? []) {
      const before = seen.current[item.id];
      seen.current[item.id] = item.status;
      if (before && before !== 'completed' && item.status === 'completed') {
        refresh();
        setCelebrate(item);
        if (item.new_matches > 0) void confetti.current?.fire({ particleCount: 120, spread: 75, origin: { y: 0.35 }, colors: ['#0a3be0', '#5dd0ea', '#f59e0b', '#dc2626', '#7c3aed'] });
      }
    }
  }, [imports.data, refresh]);

  function choose(candidate?: File | null) {
    setError(''); setNotice('');
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith('.json')) { setError('Use a .json file (the cleaned.json from the chat export).'); return; }
    setFile(candidate);
    setEstimate(null);
    // Exports are named "Sales - Office - … - cleaned.json"; the first part is usually the person.
    if (!agent.trim()) setAgent(candidate.name.split(' - ')[0]?.trim().slice(0, 60) ?? '');
    void estimateFor(candidate);
  }

  /** Count the messages in the chosen export and ask how long such an upload usually takes here. */
  async function estimateFor(candidate: File) {
    try {
      const parsed = JSON.parse(await candidate.text()) as { chats?: Record<string, { messages?: unknown[] }> };
      const chats = parsed?.chats && typeof parsed.chats === 'object' ? Object.values(parsed.chats) : [];
      const messages = chats.reduce((sum, chat) => sum + (Array.isArray(chat?.messages) ? chat.messages.length : 0), 0);
      setEstimate({ name: candidate.name, messages, seconds: null });
      const result = await api.get<{ estimate_s: number }>(`/imports/estimate?messages=${messages}`);
      setEstimate((current) => (current?.name === candidate.name ? { ...current, seconds: result.estimate_s } : current));
    } catch { /* not a readable export: the upload itself explains */ }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault(); setDragging(false);
    choose(event.dataTransfer.files?.[0]);
  }

  function clearFile() {
    setFile(null);
    setEstimate(null);
    if (input.current) input.current.value = '';
  }

  async function upload() {
    if (!file) return;
    setBusy(true); setError(''); setNotice(''); setCelebrate(null);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('agent_name', agent.trim());
      form.append('matching_mode', matchingMode);
      const result = await api.form<{ duplicate?: boolean; recomputing?: boolean; agent_name?: string; estimate_s?: number }>('/imports', form);
      setNotice(result.recomputing
        ? 'This file was processed before. The new matching mode is applied and results are updating in the background.'
        : result.duplicate
          ? 'This file was already uploaded and processed, so it was not added again.'
          : `The chat from ${result.agent_name ?? agent.trim()} is uploaded and being processed. This usually takes ${aboutMinutes(result.estimate_s)}. You can keep working.`);
      if (result.recomputing) refresh();
      clearFile();
      reload();
    } catch (reason) { setError(uploadFailure(reason)); } finally { setBusy(false); }
  }

  const running = imports.data?.filter((item) => item.status === 'queued' || item.status === 'processing') ?? [];
  const history = imports.data ?? [];
  const ready = !!file && !!agent.trim() && !busy && !!company && !noWatchlist;
  const current = running.find((item) => item.status === 'processing') ?? running[0];

  return (
    <div className="space-y-6">
      <Confetti ref={confetti} manualstart className="pointer-events-none fixed inset-0 z-[100] size-full" aria-hidden="true" />
      <PageHeader title="Upload" description="Add a WhatsApp chat export (cleaned.json). Once it is processed, new buyer and listing pairs appear in Recent matches." />
      {current && <ProcessingProgress progress={current.progress} title={`Processing ${current.file_name}…`} />}

      {celebrate && (
        <section aria-live="polite" className="rounded-2xl border border-success/40 bg-success-soft/60 p-4 sm:p-6">
          <div className="flex flex-col items-center gap-4 text-center sm:flex-row sm:text-left">
            <LottiePlayer name={celebrate.new_matches > 0 ? 'match-found' : 'success'} className="w-24 shrink-0" label="Done" />
            <div className="min-w-0 flex-1 space-y-1.5">
              <p className="text-section font-semibold text-foreground">{celebrate.new_matches > 0 ? `${countLabel(celebrate.new_matches, 'new match', 'new matches')} found!` : 'Your chat is processed'}</p>
              <p className="text-sm break-words text-foreground/80">
                {celebrate.file_name} — {countLabel(celebrate.request_count, 'buyer request')} and {countLabel(celebrate.listing_count, 'listing')} read.
                {celebrate.new_matches > 0 ? '' : ' No new pairs came from this chat.'}
              </p>
              {celebrate.new_matches > 0 && <NewMatchCounts item={celebrate} />}
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              <Button variant="tertiary" onPress={() => setCelebrate(null)}>Close</Button>
              <Button onPress={() => navigate?.(ROUTES.recent)}>See recent matches<ArrowRight className="size-4" aria-hidden="true" /></Button>
            </div>
          </div>
        </section>
      )}

      <Panel title="Upload a chat" description="Drop the file in the box, or tap the box to choose it.">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <input ref={input} type="file" accept=".json,application/json" className="sr-only" id="upload-file" onChange={(event) => choose(event.target.files?.[0])} />
            <div role="presentation" onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
              <label htmlFor="upload-file"
                className={cn('flex min-h-44 cursor-pointer flex-col sm:min-h-60 items-center justify-center gap-2 rounded-2xl border-2 border-dashed p-6 text-center transition-colors',
                  dragging ? 'border-accent bg-accent-soft' : file ? 'border-success/60 bg-success-soft/40' : 'border-border bg-surface-secondary/50 hover:border-accent/60 hover:bg-accent-soft/40')}>
                {file ? (
                  <>
                    <FileJson className="size-10 text-success" aria-hidden="true" />
                    <span className="max-w-full text-body font-semibold break-all text-foreground">{file.name}</span>
                    <span className="text-sm text-muted">{decimal(file.size / 1024 / 1024, 1)} MB · tap to choose another file</span>
                    {estimate?.name === file.name && (
                      <span className="text-sm font-medium text-foreground/80">
                        {countLabel(estimate.messages, 'message')}{estimate.seconds != null ? ` · this upload usually takes ${aboutMinutes(estimate.seconds)}` : ''}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <UploadCloud className="size-10 text-accent" aria-hidden="true" />
                    <span className="text-body font-semibold text-foreground">Drop the chat file here</span>
                    <span className="text-sm text-muted">or tap to choose a .json file</span>
                  </>
                )}
              </label>
            </div>
            {file && <Button className="mt-2" size="sm" variant="tertiary" isDisabled={busy} onPress={clearFile}><X className="size-4" aria-hidden="true" />Remove file</Button>}
          </div>

          <form className="min-w-0 space-y-5" onSubmit={(event) => { event.preventDefault(); if (ready) void upload(); }}>
            <TextField value={agent} onChange={setAgent} fullWidth isRequired>
              <Label className="text-sm font-semibold">Sales person who exported the chat</Label>
              <Input placeholder="e.g. Caesar" maxLength={60} />
              <p className="mt-1 text-caption text-muted">Shown in the upload history and on every match found in this chat.</p>
            </TextField>

            <div className="space-y-2">
              <p className="text-sm font-semibold text-foreground">Recognise your buyers and listings by</p>
              <Segmented<MatchingMode> label="Recognise your buyers and listings by" value={matchingMode} onChange={setSelectedMode} fullWidth isDisabled={busy || !admin}
                options={[{ id: 'company', label: MODE_LABELS.company }, { id: 'sales', label: MODE_LABELS.sales }]} />
              {matchingMode === 'company' ? (
                <p className="text-caption leading-relaxed text-muted">
                  Messages that mention a company keyword: <strong className="font-semibold text-foreground">{company?.search_terms.join(' · ') || 'every message (no keywords set)'}</strong>.
                </p>
              ) : noWatchlist ? (
                <Notice status="warning">Your sales watchlist is empty. <button type="button" className="font-semibold underline" onClick={() => navigate?.(ROUTES.settings, { tab: 'sales' })}>Add sales numbers</button> before uploading in Sales mode.</Notice>
              ) : (
                <p className="text-caption leading-relaxed break-words text-muted">Messages with a contact number on your watchlist: {company?.tracked_phones.map(formatPhone).join(' · ')}.</p>
              )}
              {admin && selectedMode && selectedMode !== company?.matching_mode && (
                <p className="text-caption text-muted">This becomes the company’s matching mode once the file is processed.</p>
              )}
            </div>

            <Button type="submit" size="lg" fullWidth isPending={busy} isDisabled={!ready}>
              {!busy && <UploadCloud className="size-5" aria-hidden="true" />}{busy ? 'Uploading…' : 'Upload chat'}
            </Button>
            {error && <Notice status="danger" title="The upload did not work">{error}</Notice>}
            {notice && <Notice status="success">{notice}</Notice>}
          </form>
        </div>
      </Panel>

      {running.map((item) => (
        <Panel key={item.id} title="Processing" description={<span className="break-all">{item.file_name} · {item.agent_name}</span>}
          actions={<Chip size="sm" variant="soft" color={STATUS[item.status].color}>{STATUS[item.status].label}</Chip>}>
          <ol className="mb-5 grid grid-cols-4 gap-2" aria-label="Steps">
            {steps(item).map((step) => (
              <li key={step.label} className="text-center" aria-current={step.state === 'active' ? 'step' : undefined}>
                <span className={cn('mx-auto flex size-8 items-center justify-center rounded-full',
                  step.state === 'done' ? 'bg-success text-success-foreground' : step.state === 'active' ? 'animate-pulse bg-accent text-accent-foreground' : 'bg-default text-muted')}>
                  {step.state === 'done' ? <Check className="size-4" strokeWidth={3} aria-hidden="true" /> : <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />}
                </span>
                <span className="mt-1.5 block text-caption leading-tight font-medium text-foreground">{step.label}</span>
              </li>
            ))}
          </ol>
          <ProcessingProgress progress={item.progress} title={item.status === 'queued' ? 'Waiting to start…' : 'Processing…'} />
          <p className="mt-3 text-caption text-muted">You can leave this page. Processing continues in the background and new pairs appear in Recent matches.</p>
        </Panel>
      ))}

      <Panel title={<span className="flex items-center gap-2"><History className="size-5 text-accent" aria-hidden="true" />Upload history</span>} description="Newest first.">
        <div className="space-y-3">
          <ErrorNotice message={imports.error} onRetry={imports.reload} />
          {imports.loading && !imports.data && <SkeletonList rows={2} />}
          {history.length === 0 && imports.data && <EmptyState compact icon={<UploadCloud />} title="No uploads yet" description="Chats you upload are listed here." />}
          {history.length > 0 && (
            <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border">
              {history.map((item) => (
                <li key={item.id} className="space-y-2 px-4 py-3.5 sm:px-5">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-body font-semibold break-all text-foreground">{item.file_name}</p>
                      <p className="text-caption text-muted">{[item.agent_name, dateTime(item.created_at), item.matching_mode ? MODE_LABELS[item.matching_mode] : ''].filter(Boolean).join(' · ')}</p>
                    </div>
                    <Chip size="sm" variant="soft" color={STATUS[item.status]?.color ?? 'default'}>{STATUS[item.status]?.label ?? item.status}</Chip>
                  </div>
                  {item.status === 'failed' && (
                    <p className="flex items-start gap-2 text-sm text-danger">
                      <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                      <span className="min-w-0 break-words">{importFailure(item.error)}</span>
                    </p>
                  )}
                  {item.status === 'completed' && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
                      <span><strong className="font-semibold tabular-nums">{number(item.request_count)}</strong> {item.request_count === 1 ? 'buyer request' : 'buyer requests'}</span>
                      <span><strong className="font-semibold tabular-nums">{number(item.listing_count)}</strong> {item.listing_count === 1 ? 'listing' : 'listings'}</span>
                      <span className="text-muted">{countLabel(item.duplicate_count, 'repeat')} · {number(item.ignored_count)} skipped (not a buyer or listing)</span>
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-accent">{countLabel(item.new_matches, 'new match', 'new matches')}</span>
                        {item.new_matches > 0 && <NewMatchCounts item={item} />}
                      </span>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>
    </div>
  );
}
