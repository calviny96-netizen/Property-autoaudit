'use client';

import { useState } from 'react';
import { Button, Chip, Drawer } from '@heroui/react';
import { ArrowUpRight, Building2, ChevronDown, ContactRound, UsersRound, X } from 'lucide-react';
import { ErrorNotice, PageHeader, SkeletonList, StatusBadge } from '@/components/app/primitives';
import { useApi } from '@/lib/workspace-context';
import { useData } from '@/lib/use-data';
import { dateTime } from '@/lib/format';
import type { Temperature } from '@/lib/types';

type Group = { owner: string; name: string; phone: string; buyers: number; listings: number };
type Pair = { id: number; buyer: string; listing: string; decision: string; reviewed_at: string; engine_status: Temperature | null; buyer_text: string; listing_text: string };

export function PairHistory({ back }: { back: () => void }) {
  const api = useApi();
  const groups = useData((signal) => api.get<{ group_by: string; groups: Group[] }>('/ai/pair-history', signal), []);
  const [selected, select] = useState<Group | null>(null);
  return <div className="space-y-5">
    <PageHeader title="AI matching history" description={groups.data?.group_by === 'sender' ? 'Reviewed buyers and listings by message sender.' : 'Reviewed buyers and listings by sales contact.'}
      actions={<Button variant="secondary" onPress={back}>Back to AI Review</Button>} />
    <ErrorNotice message={groups.error} onRetry={groups.reload} />
    {groups.loading && <SkeletonList rows={2} />}
    {groups.data?.groups.length === 0 && <p>No reviewed pairs for this grouping yet.</p>}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{groups.data?.groups.map(g =>
      <button key={g.owner} type="button" aria-haspopup="dialog" onClick={() => select(g)}
        className="group rounded-2xl border border-border bg-surface p-5 text-left shadow-sm transition hover:border-accent">
        <span className="flex items-center gap-3">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent"><ContactRound /></span>
          <span className="min-w-0 flex-1"><strong className="block break-words">{g.name || '-'}</strong>
            <span className="text-sm tabular-nums text-muted">{g.phone || '-'}</span></span>
          <ArrowUpRight className="size-4 text-muted group-hover:text-accent" />
        </span>
        <span className="mt-5 grid grid-cols-2 gap-3 border-t border-border pt-3">
          <span><strong className="block text-xl tabular-nums">{g.buyers}</strong><span className="flex items-center gap-1 text-caption text-muted"><UsersRound className="size-4" />Buyer Match</span></span>
          <span><strong className="block text-xl tabular-nums">{g.listings}</strong><span className="flex items-center gap-1 text-caption text-muted"><Building2 className="size-4" />Listing Match</span></span>
        </span>
      </button>)}</div>
    <Drawer.Backdrop isOpen={!!selected} onOpenChange={open => { if (!open) select(null); }}>
      <Drawer.Content placement="right">
        <Drawer.Dialog className="w-screen max-w-xl">
          <Drawer.Header className="flex flex-row items-center justify-between border-b border-border">
            <div><Drawer.Heading>{selected?.name || '-'}</Drawer.Heading><p className="text-sm text-muted">{selected?.phone || '-'}</p></div>
            <Button isIconOnly variant="ghost" aria-label="Close history" onPress={() => select(null)}><X /></Button>
          </Drawer.Header>
          <Drawer.Body>{selected && <PairDetails key={selected.owner} group={selected} />}</Drawer.Body>
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  </div>;
}

function PairDetails({ group }: { group: Group }) {
  const api = useApi();
  const [offset, setOffset] = useState(0);
  const [decision, setDecision] = useState('all');
  const [level, setLevel] = useState('all');
  const result = useData((signal) => api.get<{ rows: Pair[]; has_more: boolean }>(
    '/ai/pair-history?' + new URLSearchParams({ group: group.owner, offset: String(offset), decision, level }), signal), [group.owner, offset, decision, level]);
  return <div className="space-y-3">
    <div className="grid grid-cols-2 gap-3">
      <label className="text-sm font-semibold">AI decision
        <select className="mt-1 block w-full rounded-xl border border-border bg-surface p-2" value={decision}
          onChange={e => { setDecision(e.target.value); setOffset(0); }}>
          <option value="all">All decisions</option><option value="recommend">Match</option>
          <option value="reject">Reject</option><option value="unsure">Unsure</option>
        </select>
      </label>
      <label className="text-sm font-semibold">Match level
        <select className="mt-1 block w-full rounded-xl border border-border bg-surface p-2" value={level}
          onChange={e => { setLevel(e.target.value); setOffset(0); }}>
          <option value="all">All levels</option><option value="hot">Hot</option>
          <option value="warm">Warm</option><option value="custom">Custom</option>
        </select>
      </label>
    </div>
    {!result.loading && !result.error && result.data?.rows.length === 0 && <p>No pairs match these filters.</p>}
    <ErrorNotice message={result.error} onRetry={result.reload} />
    {result.loading ? <SkeletonList rows={2} /> : !result.error && result.data?.rows.map(row =>
      <HistoryPair key={row.id} row={row} />)}
    <div className="flex gap-2">
      {offset > 0 && <Button variant="secondary" onPress={() => setOffset(offset - 50)}>Previous</Button>}
      {result.data?.has_more && <Button variant="secondary" isDisabled={result.loading} onPress={() => setOffset(offset + 50)}>Next</Button>}
    </div>
  </div>;
}

function HistoryPair({ row }: { row: Pair }) {
  const api = useApi();
  const [open, setOpen] = useState(false);
  const messages = useData((signal) => api.get<{ buyer: string | null; listing: string | null }>(
    `/ai/pair-history/${row.id}/messages`, signal), [row.id], open);
  const match = row.decision === 'recommend';
  const reject = row.decision === 'reject';
  return <section className="rounded-xl border border-border bg-surface">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
      className="flex w-full flex-wrap items-center gap-2 p-4 text-left">
      <strong className="flex-1 text-sm">{row.buyer} × {row.listing}</strong>
      <Chip size="sm" variant="soft" color={match ? 'success' : reject ? 'danger' : 'warning'}>
        {match ? '✓ Match' : reject ? '✕ Reject' : 'Unsure'}
      </Chip>
      {row.engine_status && <StatusBadge status={row.engine_status} size="sm" explain={false} />}
      <span className="flex w-full items-center gap-1 text-caption font-semibold text-accent">
        {open ? 'Hide full messages' : 'Read full messages'} <ChevronDown className={open ? 'size-4 rotate-180' : 'size-4'} />
      </span>
    </button>
    {open && <div className="space-y-3 border-t border-border p-4">
      <p className="text-caption text-muted">{dateTime(row.reviewed_at)}</p>
      <ErrorNotice message={messages.error} onRetry={messages.reload} />
      {messages.loading && !messages.data && <SkeletonList rows={2} />}
      {messages.data && (['buyer', 'listing'] as const).map(side =>
        <section key={side} className="rounded-xl bg-surface-secondary p-3">
          <h3 className="mb-2 text-sm font-semibold">{side === 'buyer' ? 'Buyer' : 'Listing'} · {row[side]}</h3>
          {!messages.data?.[side] && <p className="text-caption text-muted">Original message unavailable. Saved review excerpt:</p>}
          <p className="whitespace-pre-wrap break-words text-sm">{messages.data?.[side] || row[side === 'buyer' ? 'buyer_text' : 'listing_text'] || '-'}</p>
        </section>)}
    </div>}
  </section>;
}
