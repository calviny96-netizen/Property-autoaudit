'use client';

import { useState } from 'react';
import { Button } from '@heroui/react';
import { History } from 'lucide-react';
import { EmptyState, ErrorNotice, PageHeader, Panel, Segmented, SkeletonList, StatusBadge } from '@/components/app/primitives';
import { PeriodPicker, periodFor, periodPresets, type Period } from '@/components/app/period-picker';
import { query } from '@/lib/api';
import { dateTime, formatPhone, number } from '@/lib/format';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';
import type { Direction, Temperature } from '@/lib/types';

type Counts = { total: number; matched: number; unmatched: number; pairs: number; matching_rate: number };
type Sales = Counts & { phone: string; label: string | null; last_match_at: string | null };
type Overview = { totals: Counts; sales: Sales[]; days: { day: string; pairs: number; matched: number }[] };
type Entity = { entity_id: string; public_id: string; entity_status: string; pairs: number; message: string | null; first_match_at: string | null; last_match_at: string | null };
type Event = { id: number; entity_id: string; public_id: string; message: string | null; found_at: string; last_seen_at: string; first_status: Temperature; last_status: Temperature; active: boolean; source: string; agent_name: string | null; last_score: number; first_score: number };
type Filters = { direction: Direction; phone: string; date_from: string; date_to: string; include_archived: boolean };

export default function MatchingTrackerPage() {
  const api = useApi();
  const [direction, setDirection] = useState<Direction>('property');
  const [period, setPeriod] = useState<Period>(() => periodFor('month'));
  const [phone, setPhone] = useState('');
  const [includeArchived, setIncludeArchived] = useState(false);
  const filters: Filters = { direction, phone, date_from: period.from, date_to: period.to, include_archived: includeArchived };
  const data = useData((signal) => api.get<Overview>(`/matching-tracker?${query(filters)}`, signal), [direction, period, phone, includeArchived]);
  const allSales = useData((signal) => api.get<{ sales: { phone: string; label: string | null }[] }>('/matching-tracker/sales', signal), []);
  const totals = data.data?.totals;
  const noun = direction === 'property' ? 'listings' : 'buyers';
  return <div className="space-y-6">
    <PageHeader title="Matching tracker" description="Track watchlist inventory, matching coverage and the history of each buyer–listing pair." />
    <Panel>
      <div className="flex flex-wrap items-end gap-5">
        <Segmented label="Track" value={direction} onChange={setDirection} options={[{ id: 'property', label: 'Listings → Buyers' }, { id: 'buyer', label: 'Buyers → Listings' }]} />
        <label className="grid gap-1 text-sm font-medium">Sales watchlist
          <select className="h-10 max-w-full rounded-xl border border-border bg-surface px-3" value={phone} onChange={(e) => setPhone(e.target.value)}>
            <option value="">All sales</option>
            {allSales.data?.sales.map((s) => <option key={s.phone} value={s.phone}>{s.label || formatPhone(s.phone)} · {formatPhone(s.phone)}</option>)}
          </select>
        </label>
        <PeriodPicker value={period} onChange={setPeriod} presets={periodPresets(['all', 'today', 'week', 'month', 'last_month', 'last_3_months'])} label="First match found" />
      </div>
      <label className="mt-4 flex items-center gap-2 text-sm"><input type="checkbox" checked={includeArchived} onChange={(e) => setIncludeArchived(e.target.checked)} className="size-4 accent-[var(--accent)]" />Include archived / deleted inventory</label>
      <p className="mt-4 text-sm text-muted">Period = when a pair was first found (WIB). Matching rate = unique {noun} with at least one pair found in this period ÷ {includeArchived ? 'all recorded' : 'current'} watchlist {noun}. Each item counts once; inactive pairs remain in history.</p>
    </Panel>
    <ErrorNotice message={data.error || allSales.error} onRetry={() => { data.reload(); allSales.reload(); }} />
    {data.loading && !data.data && <SkeletonList rows={3} />}
    {totals && <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[['Watchlist ' + noun, number(totals.total)], ['Matched ' + noun, number(totals.matched)], ['Matching rate', totals.matching_rate + '%'], ['Unique pairs', number(totals.pairs)]].map(([label, value]) =>
          <div key={label} className="rounded-2xl border border-border bg-surface p-5"><p className="text-sm text-muted">{label}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p></div>)}
      </div>
      <Panel title="Matching by sales" description={`${number(totals.unmatched)} ${noun} have no pair first discovered in this period. Items shared by sales count once in the totals above.`}>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead className="text-muted"><tr>{['Sales', noun === 'listings' ? 'Listings' : 'Buyers', 'Matched', 'Rate', 'Pairs', 'Last new match'].map((v) => <th className="px-3 py-3 font-medium" key={v}>{v}</th>)}</tr></thead>
          <tbody>{data.data?.sales.map((s) => <tr key={s.phone} className="border-t border-separator">
            <td className="px-3 py-3"><button onClick={() => setPhone(s.phone)} className="text-left font-semibold text-accent hover:underline">{s.label || formatPhone(s.phone)}</button><p className="text-xs text-muted">{formatPhone(s.phone)}</p></td>
            <td className="px-3 py-3">{number(s.total)}</td><td className="px-3 py-3">{number(s.matched)}</td>
            <td className="min-w-28 px-3 py-3"><span>{s.matching_rate}%</span><div className="mt-1 h-1.5 rounded-full bg-default"><div className="h-full rounded-full bg-accent" style={{ width: `${s.matching_rate}%` }} /></div></td>
            <td className="px-3 py-3">{number(s.pairs)}</td><td className="whitespace-nowrap px-3 py-3 text-muted">{dateTime(s.last_match_at)}</td>
          </tr>)}</tbody>
        </table></div>
        {!data.data?.sales.length && <EmptyState compact icon={<History />} title="No tracked sales" description="Add sales phone numbers to the company watchlist to start tracking." />}
      </Panel>
      <Panel title="New pairs over time" description="Daily first discoveries. A pair found again on a later run does not count as a new pair.">
        <div className="max-h-64 space-y-2 overflow-y-auto">{data.data?.days.map((day) => <div className="flex items-center gap-3 text-sm" key={day.day}>
          <span className="w-28 shrink-0 text-muted">{day.day}</span><div className="h-3 flex-1 rounded-full bg-default"><div className="h-full rounded-full bg-accent/70" style={{ width: `${100 * day.pairs / Math.max(1, ...data.data!.days.map((d) => d.pairs))}%` }} /></div><span className="w-16 text-right tabular-nums">{number(day.pairs)}</span>
        </div>)}</div>
        {!data.data?.days.length && <p className="text-sm text-muted">No new pairs in this period. Choose All dates to see earlier history.</p>}
      </Panel>
      <EntityList key={query(filters)} filters={filters} />
    </>}
  </div>;
}

function EntityList({ filters }: { filters: Filters }) {
  const api = useApi();
  const [offset, setOffset] = useState(0);
  const [opened, setOpened] = useState('');
  const result = useData((signal) => api.get<{ rows: Entity[]; has_more: boolean }>(`/matching-tracker/entities?${query({ ...filters, offset })}`, signal), [filters.direction, filters.phone, filters.date_from, filters.date_to, filters.include_archived, offset]);
  return <Panel title="Inventory & matching history" description="Open an ID to see every counterpart first matched in the selected period, including pairs that are no longer active.">
    <ErrorNotice message={result.error} onRetry={result.reload} />
    {result.loading && <SkeletonList rows={3} />}
    {!result.loading && result.data?.rows.map((row) => <div key={row.entity_id} className="border-b border-separator py-4">
      <button onClick={() => setOpened(opened === row.entity_id ? '' : row.entity_id)} aria-expanded={opened === row.entity_id} className="flex w-full items-start justify-between gap-4 text-left">
        <span className="min-w-0"><span className="font-mono font-semibold text-accent">{row.public_id}</span><span className="ml-2 text-xs text-muted">{row.entity_status}</span><span className="mt-1 block line-clamp-2 text-sm">{row.message || 'Original message unavailable'}</span></span>
        <span className="shrink-0 rounded-full bg-accent-soft px-3 py-1 text-sm font-medium">{number(row.pairs)} pairs</span>
      </button>
      {opened === row.entity_id && <PairHistory filters={filters} entity={row} />}
    </div>)}
    {!result.loading && !result.error && !result.data?.rows.length && <p className="py-4 text-sm text-muted">No watchlist inventory for this selection.</p>}
    <div className="mt-4 flex items-center justify-between"><Button variant="secondary" isDisabled={offset === 0 || result.loading} onPress={() => setOffset((n) => Math.max(0, n - 50))}>Previous</Button><span className="text-sm text-muted">Page {offset / 50 + 1}</span><Button variant="secondary" isDisabled={!result.data?.has_more || result.loading} onPress={() => setOffset((n) => n + 50)}>Next</Button></div>
  </Panel>;
}

function PairHistory({ filters, entity }: { filters: Filters; entity: Entity }) {
  const api = useApi();
  const [offset, setOffset] = useState(0);
  const result = useData((signal) => api.get<{ rows: Event[]; has_more: boolean }>(`/matching-tracker/history?${query({ direction: filters.direction, date_from: filters.date_from, date_to: filters.date_to, entity_id: entity.entity_id, offset })}`, signal), [entity.entity_id, filters.direction, filters.date_from, filters.date_to, filters.include_archived, offset]);
  return <div className="mt-3 space-y-3 rounded-xl bg-surface-secondary p-4">
    <ErrorNotice message={result.error} onRetry={result.reload} />
    {result.loading && <SkeletonList rows={2} />}
    {!result.loading && result.data?.rows.map((event) => <article className="rounded-xl border border-border bg-surface p-4" key={event.id}>
      <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm font-semibold">{entity.public_id} → {event.public_id}</span><StatusBadge status={event.last_status} size="sm" /><span className="text-xs text-muted">{event.active ? 'Active pair' : 'Inactive pair'}</span></div>
      <p className="mt-2 text-xs text-muted">First matched: {dateTime(event.found_at)} · Last confirmed: {dateTime(event.last_seen_at)}</p>
      <p className="mt-1 text-xs text-muted">First result: {event.first_status} ({number(event.first_score)}) · Latest result: {event.last_status} ({number(event.last_score)}) · {event.source === 'import' ? 'Import' : 'Reprocess'}{event.agent_name ? ` · ${event.agent_name}` : ''}</p>
      <details className="mt-2 text-sm"><summary className="cursor-pointer font-medium text-accent">View matched message</summary><p className="mt-2 whitespace-pre-wrap break-words">{event.message || 'Original message unavailable'}</p></details>
    </article>)}
    {!result.loading && !result.error && !result.data?.rows.length && <p className="text-sm text-muted">No pair first discovered in this period.</p>}
    {(offset > 0 || result.data?.has_more) && <div className="flex justify-between gap-3"><Button size="sm" variant="secondary" isDisabled={!offset || result.loading} onPress={() => setOffset((v) => v - 50)}>Previous pairs</Button><Button size="sm" variant="secondary" isDisabled={!result.data?.has_more || result.loading} onPress={() => setOffset((v) => v + 50)}>Next pairs</Button></div>}
  </div>;
}
