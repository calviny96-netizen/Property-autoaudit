'use client';

import { useCallback, useState } from 'react';
import { Avatar, Button, Label, SearchField, ToggleButton, ToggleButtonGroup } from '@heroui/react';
import { Ban, CircleCheck, Clock3, ExternalLink, SearchX, UserSearch } from 'lucide-react';
import { EmptyState, ErrorNotice, IdChip, PageHeader, Panel, SkeletonList, StatCard, StatusBadge, StatusChip } from '@/components/app/primitives';
import { StatusMenu, statusLabel, useStatusActions } from '@/components/app/status-menu';
import { query } from '@/lib/api';
import { cleanName, displaySender, formatPhone, initials, messageText, number, relativeDate, structuredSummary } from '@/lib/format';
import { ROUTES, ROUTE_PARAMS, type Navigate } from '@/lib/router';
import { useData, useDebounced } from '@/lib/use-data';
import type { EntityStatus, Row } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

type BuyerList = { rows: Row[]; has_more: boolean; counts: Record<EntityStatus, number>; page_size: number };

/** Ready / On hold / Disabled (the soft "deleted" status); Sold stays reachable as a filter. */
const FILTERS: EntityStatus[] = ['ready', 'on_hold', 'deleted', 'sold'];
const QUICK: { status: EntityStatus; label: string; icon: typeof CircleCheck }[] = [
  { status: 'ready', label: 'Ready', icon: CircleCheck },
  { status: 'on_hold', label: 'On hold', icon: Clock3 },
  { status: 'deleted', label: 'Disable', icon: Ban },
];

export default function BuyersPage({ navigate }: { navigate?: Navigate }) {
  const api = useApi();
  const [search, setSearch] = useState('');
  const [statuses, setStatuses] = useState<EntityStatus[]>(['ready', 'on_hold']);
  const [offset, setOffset] = useState(0);
  const [tick, setTick] = useState(0);
  const term = useDebounced(search.trim(), 300);
  const bump = useCallback(() => setTick((value) => value + 1), []);
  const { request, dialog } = useStatusActions(bump);

  const list = useData((signal) => api.get<BuyerList>(`/buyers/list?${query({ search: term, status: statuses.join(','), offset })}`, signal),
    [term, statuses, offset, tick]);
  const counts = list.data?.counts;
  const rows = list.data?.rows ?? [];
  const page = list.data?.page_size ?? 50;

  const change = (row: Row, status: EntityStatus) => request({
    refs: [String(row.entity_id ?? row.public_id)], status, name: `Buyer ${row.public_id ?? ''}`.trim(), previous: row.entity_status ?? 'ready', kind: 'buyer',
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Buyers" description="Every buyer request, one card per buyer ID. Disable a buyer to take it out of matching, counts, recent matches and exports straight away; you can enable it again any time." />

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <StatCard label="Ready" value={counts?.ready ?? 0} icon={<CircleCheck />} tone="green" />
        <StatCard label="On hold" value={counts?.on_hold ?? 0} icon={<Clock3 />} tone="amber" />
        <StatCard label="Disabled" value={counts?.deleted ?? 0} icon={<Ban />} tone="slate" />
        <StatCard label="Sold / closed" value={counts?.sold ?? 0} icon={<UserSearch />} tone="blue" />
      </div>

      <Panel>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end">
          <SearchField value={search} onChange={(value) => { setSearch(value); setOffset(0); }} name="buyer-search" className="min-w-0 flex-1">
            <Label className="text-sm font-semibold">Search buyers</Label>
            <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder="ID (B-AB908), name, phone or text" /><SearchField.ClearButton /></SearchField.Group>
          </SearchField>
          <ToggleButtonGroup aria-label="Buyer status" selectionMode="multiple" isDetached selectedKeys={new Set(statuses)} className="max-w-full flex-wrap gap-2"
            onSelectionChange={(keys) => { const next = FILTERS.filter((status) => keys.has(status)); setStatuses(next.length ? next : ['ready']); setOffset(0); }}>
            {FILTERS.map((status) => (
              <ToggleButton key={status} id={status} className="min-h-11 px-4 text-sm font-semibold sm:min-h-10">
                {statusLabel(status, 'buyer')}{counts ? ` · ${number(counts[status])}` : ''}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </div>
      </Panel>

      <ErrorNotice message={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <SkeletonList rows={4} />}
      {list.data && rows.length === 0 && (
        <Panel><EmptyState icon={<SearchX />} title="No buyers found" description={term ? 'Try another ID, name, phone number or word, or include more statuses.' : 'No buyer requests with the chosen status yet.'} /></Panel>
      )}

      {rows.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {rows.map((row) => <BuyerCard key={row.id} row={row} onStatus={(status) => change(row, status)}
            onOpen={navigate && row.public_id ? () => navigate(ROUTES.match, { [ROUTE_PARAMS.direction]: 'buyer', [ROUTE_PARAMS.id]: row.public_id ?? undefined }) : undefined} />)}
        </div>
      )}

      {list.data && (offset > 0 || list.data.has_more) && (
        <div className="flex items-center justify-center gap-3">
          <Button variant="secondary" isDisabled={offset === 0} onPress={() => setOffset(Math.max(0, offset - page))}>Previous</Button>
          <span className="text-sm text-muted">{number(offset + 1)}–{number(offset + rows.length)}</span>
          <Button variant="secondary" isDisabled={!list.data.has_more} onPress={() => setOffset(offset + page)}>Next</Button>
        </div>
      )}
      {dialog}
    </div>
  );
}

function BuyerCard({ row, onStatus, onOpen }: { row: Row; onStatus: (status: EntityStatus) => void; onOpen?: () => void }) {
  const status = row.entity_status ?? 'ready';
  const name = cleanName(row.contact_name) || displaySender(row.author) || 'Unnamed buyer';
  const sender = displaySender(row.author);
  const summary = structuredSummary(row) || messageText(row);
  const counts: ['hot' | 'warm' | 'custom', number][] = [['hot', Number(row.hot_count ?? 0)], ['warm', Number(row.warm_count ?? 0)], ['custom', Number(row.custom_count ?? 0)]];
  const matched = counts.some(([, n]) => n > 0);
  return (
    <article className="relative rounded-2xl border border-border bg-surface p-4 shadow-card">
      <div className="absolute top-2 right-2"><StatusMenu status={status} kind="buyer" name={`Buyer ${row.public_id ?? name}`} onChoose={onStatus} /></div>
      <div className="flex items-start gap-3 pr-10">
        <Avatar color="accent" variant="soft" size="md" className="hidden shrink-0 sm:flex"><Avatar.Fallback>{initials(name)}</Avatar.Fallback></Avatar>
        <div className="min-w-0 flex-1">
          <p className="text-body leading-snug font-semibold break-words text-foreground">{name}</p>
          <p className="mt-0.5 text-caption text-muted">
            {relativeDate(row.sent_at)}{sender && sender !== name ? ` · ${sender}` : ''}{row.contact_phone ? ` · ${formatPhone(row.contact_phone)}` : ''}
          </p>
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <IdChip id={row.public_id ?? undefined} kind="buyer" />
        {status !== 'ready' && (status === 'deleted'
          ? <span className="inline-flex h-7 items-center rounded-full bg-default px-2.5 text-[0.8125rem] font-semibold text-muted">Disabled</span>
          : <StatusChip status={status} />)}
        {counts.filter(([, n]) => n > 0).map(([key, n]) => <StatusBadge key={key} status={key} count={n} size="sm" explain={false} />)}
        {!matched && <StatusBadge status="unmatched" size="sm" explain={false} />}
      </div>
      <p className="mt-2.5 line-clamp-3 text-sm leading-relaxed text-foreground/85">{summary}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {QUICK.filter((item) => item.status !== status).map((item) => (
          <Button key={item.status} size="sm" variant={item.status === 'deleted' ? 'danger-soft' : 'secondary'} onPress={() => onStatus(item.status)}>
            <item.icon className="size-4" aria-hidden="true" />{status === 'deleted' && item.status === 'ready' ? 'Enable' : item.label}
          </Button>
        ))}
        {onOpen && <Button size="sm" variant="tertiary" onPress={onOpen}><ExternalLink className="size-4" aria-hidden="true" />Open in Match</Button>}
      </div>
    </article>
  );
}
