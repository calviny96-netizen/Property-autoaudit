'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Switch, toast, useOverlayState } from '@heroui/react';
import { ArrowRight, CalendarSearch, Download, Flame, GitCompareArrows, ListChecks, MapPinSearch, Sparkles, ThermometerSun } from 'lucide-react';
import {
  EmptyState, ErrorNotice, FilterGroup, LoadingIndicator, PageHeader, Panel, Section, Segmented, SkeletonList, StatCard, StatusBadge, StatusFilter,
} from '@/components/app/primitives';
import { PRESET_LABELS, PeriodPicker, periodFor, periodPresets, type Period, type PresetId } from '@/components/app/period-picker';
import { SourceCard } from '@/components/match/cards';
import { useStatusActions } from '@/components/app/status-menu';
import { RecentDetailModal } from '@/components/match/recent-detail-modal';
import { ExportAllModal } from '@/components/match/export-all-modal';
import { query } from '@/lib/api';
import { countLabel, formatPhone, isoDateWib, number, prettyRange, relativeDateTime, shiftDay, todayWib } from '@/lib/format';
import { ROUTES, ROUTE_PARAMS, type Navigate } from '@/lib/router';
import { useCompany } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { Direction, EntityStatus, MatchStatus, RecentGroup, RecentResponse, RecentSummary, Row, Temperature } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

type Days = { days: Record<string, { total: number; hot: number; warm: number; custom?: number }>; latest_date: string | null };

const PAGE = 30;
const MAX_PICK = 200;
const PAIR_STATUSES: Temperature[] = ['hot', 'warm', 'custom'];
const presets = () => periodPresets(['today', 'yesterday', 'today_yesterday', 'week', 'last_week', 'month'], { untilToday: true });
const isPairStatus = (status: MatchStatus): status is Temperature => status !== 'unmatched';
const periodText = (period: Period) => (period.preset !== 'custom' && PRESET_LABELS[period.preset as PresetId]) || prettyRange(period.from, period.to);

export default function RecentPage({ navigate }: { navigate?: Navigate }) {
  const api = useApi();
  const { markSeen, company } = useCompany();
  const today = todayWib();
  const [direction, setDirection] = useState<Direction>('buyer');
  const [temps, setTemps] = useState<Temperature[]>(['hot', 'warm', 'custom']);
  const [period, setPeriod] = useState<Period>(() => periodFor('today'));
  const [includeInactive, setIncludeInactive] = useState(false);
  const scopeKey = JSON.stringify([company?.matching_mode, company?.search_terms, company?.tracked_phones]);
  const filterKey = [scopeKey, direction, period.from, period.to, temps.join(','), includeInactive].join('|');
  // Paging restarts by itself whenever a filter changes: the extra rows belong to one filter key.
  const [more, setMore] = useState({ key: '', extra: 0 });
  const limit = PAGE + (more.key === filterKey ? more.extra : 0);
  const markerKey = `${scopeKey}|${direction}|${includeInactive}`;
  const [calendar, setCalendar] = useState<{ key: string; markers: Record<string, number>; latest: string | null }>({ key: '', markers: {}, latest: null });
  const markers = calendar.key === markerKey ? calendar.markers : {};
  const latest = calendar.key === markerKey ? calendar.latest : null;
  const currentMarkerKey = useRef(markerKey);
  useEffect(() => { currentMarkerKey.current = markerKey; }, [markerKey]);
  const [opened, setOpened] = useState<{ key: string; group: RecentGroup } | null>(null);
  const active = opened?.key === filterKey ? opened.group : null;
  const [selection, setSelection] = useState<{ key: string; ids: string[]; enabled: boolean }>({ key: '', ids: [], enabled: false });
  const selected = selection.key === filterKey ? selection.ids : [];
  const bulkMode = selection.key === filterKey && selection.enabled;
  const exportState = useOverlayState();
  // Phones only: the filters fold into a one-line summary so the cards are close to the top.
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [exportFilters, setExportFilters] = useState<Record<string, unknown>>({});
  const [exportDirection, setExportDirection] = useState<Direction>('buyer');

  const summary = useData((signal) => api.get<RecentSummary>(`/matches/recent/summary?${query({ direction })}`, signal), [scopeKey, direction]);
  const data = useData(async (signal) => {
    const groups: RecentGroup[] = [];
    let result: RecentResponse;
    for (let offset = 0; ; offset += PAGE) {
      result = await api.get<RecentResponse>(`/matches/recent?${query({ direction, date_from: period.from, date_to: period.to, temps: temps.join(','), include_inactive: includeInactive, limit: PAGE, offset, per_source: 0 })}`, signal);
      groups.push(...result.groups);
      if (!result.has_more || groups.length >= limit) break;
    }
    return { ...result, groups, filterKey };
  }, [filterKey, limit]);

  const results = data.data?.filterKey === filterKey ? data.data : undefined;

  // Reading the page counts as seeing the matches; give the person a moment first.
  const since = summary.data?.since;
  const marked = useRef(false);
  useEffect(() => {
    if (!summary.data || marked.current) return;
    const timer = window.setTimeout(() => { marked.current = true; markSeen(); }, 4000);
    return () => window.clearTimeout(timer);
  }, [summary.data, markSeen]);

  const loadMarkers = useCallback((monthIso: string) => {
    void api.get<Days>(`/matches/recent/days?${query({ direction, include_inactive: includeInactive, date_from: monthIso, date_to: shiftDay(monthIso, 62) })}`).then((result) => {
      if (currentMarkerKey.current !== markerKey) return;
      setCalendar((current) => ({ key: markerKey, latest: result.latest_date, markers: { ...(current.key === markerKey ? current.markers : {}), ...Object.fromEntries(Object.entries(result.days).map(([day, value]) => [day, value.total])) } }));
    }).catch(() => {});
  }, [api, direction, includeInactive, markerKey]);
  useEffect(() => { loadMarkers(`${shiftDay(today, -45).slice(0, 8)}01`); }, [loadMarkers, today]);

  const totals = results?.totals;
  const last = summary.loading ? null : summary.data?.last_import;
  const isNew = useCallback((found: string) => !!since && new Date(found).getTime() > new Date(since).getTime(), [since]);
  const { request: requestStatus, dialog: statusDialog } = useStatusActions(() => { setOpened(null); data.reload(); summary.reload(); });
  const changeStatus = (row: Row, status: EntityStatus) => requestStatus({ refs: [row.entity_id ?? row.id], status, previous: row.entity_status ?? 'ready', name: `${row.document_type === 'buyer_request' ? 'Buyer' : 'Listing'} ${row.public_id ?? ''}`.trim() });
  const exportResults = (pick: Record<string, unknown> = {}) => {
    setExportDirection(direction);
    setExportFilters({ recent: true, direction, found_from: period.from, found_to: period.to, statuses: temps.join(','), stock_status: includeInactive ? 'ready,on_hold,sold' : 'ready', ...pick });
    setOpened(null);
    exportState.open();
  };
  const toggleSource = (id: string) => {
    if (selected.length >= MAX_PICK && !selected.includes(id)) { toast(`You can select up to ${MAX_PICK} cards. Use Export PDF for everything.`); return; }
    setSelection({ key: filterKey, enabled: true, ids: selected.includes(id) ? selected.filter((value) => value !== id) : [...selected, id] });
  };
  const sourceLabel = direction === 'buyer' ? 'buyer' : 'listing';
  const hasResults = !data.loading && !data.error && !!results?.groups.length;
  const lastCounts: [Temperature, number][] = last ? [['hot', last.hot], ['warm', last.warm], ['custom', last.custom ?? 0]] : [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Recent matches"
        description="New buyer and listing pairs found each time you upload chats. Pick a date, then open a card to see its matches."
        actions={<>
          <Button variant="secondary" isDisabled={!hasResults} onPress={() => setSelection({ key: filterKey, enabled: !bulkMode, ids: [] })}><ListChecks className="size-4" aria-hidden="true" />{bulkMode ? 'Done' : 'Select several'}</Button>
          <Button isDisabled={!hasResults} onPress={() => exportResults()}><Download className="size-4" aria-hidden="true" />Export PDF</Button>
        </>}
      />

      {last && (
        <section aria-label="Latest upload" className="flex flex-col gap-4 rounded-2xl border border-accent/20 bg-accent-soft/50 p-4 sm:flex-row sm:items-center sm:p-5">
          <span aria-hidden="true" className="hidden size-11 shrink-0 items-center justify-center rounded-2xl bg-accent text-accent-foreground sm:flex"><Sparkles className="size-5" /></span>
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-2 text-overline font-semibold text-accent uppercase">
              Latest upload
              {!!summary.data?.unseen && <span className="rounded-full bg-accent px-1.5 py-px text-[0.6875rem] text-accent-foreground normal-case tabular-nums" title="New since your last visit">{number(summary.data.unseen)} new</span>}
            </p>
            <p className="mt-1 truncate text-body font-semibold text-foreground">{last.file_name ?? 'Chat export'}{last.agent_name ? <span className="font-normal text-muted"> · {last.agent_name}</span> : null}</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-sm text-muted">{relativeDateTime(last.found_at)} · {countLabel(last.total, 'new match', 'new matches')}</span>
              {lastCounts.map(([status, value]) => (value > 0 ? <StatusBadge key={status} status={status} count={value} size="sm" explain={false} /> : null))}
            </div>
          </div>
          <Button variant="secondary" className="shrink-0" onPress={() => { const day = isoDateWib(last.found_at) || today; setPeriod({ preset: 'custom', from: day, to: day }); }}>
            Show this upload<ArrowRight className="size-4" aria-hidden="true" />
          </Button>
        </section>
      )}

      <Panel title="Filters"
        actions={<Button size="sm" variant="tertiary" className="sm:hidden" aria-expanded={filtersOpen} onPress={() => setFiltersOpen((open) => !open)}>{filtersOpen ? 'Hide' : 'Change'}</Button>}
        footer={<p className="text-caption leading-relaxed text-muted">{company?.matching_mode === 'sales'
          ? `Only ${sourceLabel}s whose contact number is on your sales watchlist${company.tracked_phones.length ? ` (${company.tracked_phones.map(formatPhone).join(', ')})` : ''}.`
          : company?.search_terms?.length ? `Only ${sourceLabel}s that mention your company keywords: ${company.search_terms.join(', ')}.` : `Showing every ${sourceLabel} in your company data.`} The same rules apply to the details and exports. Dates and times are in WIB.</p>}>
        {!filtersOpen && (
          <button type="button" onClick={() => setFiltersOpen(true)} className="-mt-1 block w-full space-y-2 text-left sm:hidden">
            <span className="block text-sm text-foreground">
              <strong className="font-semibold">{direction === 'buyer' ? 'Buyer → Listing' : 'Listing → Buyer'}</strong> · Found <strong className="font-semibold">{periodText(period)}</strong>
              {includeInactive ? ' · Including on hold and sold' : ''}
            </span>
            <span className="flex flex-wrap gap-1.5">{temps.map((status) => <StatusBadge key={status} status={status} size="sm" explain={false} />)}</span>
          </button>
        )}
        <div className={cn('space-y-5', !filtersOpen && 'max-sm:hidden')}>
          <FilterGroup label="Direction">
            <Segmented label="Direction" value={direction} onChange={setDirection} options={[{ id: 'buyer', label: 'Buyer → Listing' }, { id: 'property', label: 'Listing → Buyer' }]} />
          </FilterGroup>
          <FilterGroup label="Date found" hint={<>{prettyRange(period.from, period.to)} · Green numbers in the calendar show how many new matches were found that day.</>}>
            {/* Wrapped so the hint keeps its spacing below the picker. */}
            <div><PeriodPicker label="Date found" value={period} onChange={setPeriod} presets={presets()} max={today} markers={markers} onMonthChange={loadMarkers} /></div>
          </FilterGroup>
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
            <FilterGroup label="Show">
              <StatusFilter value={temps} options={PAIR_STATUSES} onChange={(next) => setTemps(next.filter(isPairStatus))}
                counts={totals ? { hot: totals.hot, warm: totals.warm, custom: totals.custom ?? 0 } : undefined} />
            </FilterGroup>
            <Switch isSelected={includeInactive} onChange={setIncludeInactive} className="min-h-11 sm:min-h-10">
              <Switch.Content><Switch.Control><Switch.Thumb /></Switch.Control><span className="text-sm font-semibold">Include on hold and sold</span></Switch.Content>
            </Switch>
          </div>
        </div>
      </Panel>

      {(totals || data.loading) && (
        <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
          <StatCard label="New pairs" value={totals?.pairs ?? 0} icon={<GitCompareArrows />} tone="blue" loading={!totals} />
          <StatCard label="Hot" value={totals?.hot ?? 0} icon={<Flame />} tone="hot" loading={!totals} />
          <StatCard label="Warm" value={totals?.warm ?? 0} icon={<ThermometerSun />} tone="warm" loading={!totals} />
          <StatCard label="Custom" value={totals?.custom ?? 0} icon={<MapPinSearch />} tone="custom" loading={!totals} hint="Check the location" />
        </div>
      )}

      <Section title={direction === 'buyer' ? 'Buyers with new matches' : 'Listings with new matches'} description={`Open a card to see its matches: the sender's own ${direction === 'buyer' ? 'stock' : 'buyers'} first, then your team, company posts and other agents.`}>
        {bulkMode && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-accent/20 bg-accent-soft/50 p-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="px-1 text-sm font-semibold text-foreground tabular-nums">{countLabel(selected.length, 'card')} selected</span>
              <Button size="sm" variant="tertiary" onPress={() => setSelection({ key: filterKey, enabled: true, ids: (results?.groups ?? []).map((group) => group.source.entity_id).filter((id): id is string => !!id).slice(0, MAX_PICK) })}>Select shown</Button>
              <Button size="sm" variant="tertiary" isDisabled={!selected.length} onPress={() => setSelection({ key: filterKey, enabled: true, ids: [] })}>Clear</Button>
            </div>
            <Button size="sm" isDisabled={data.loading || !selected.length} onPress={() => exportResults({ recent_sources: selected })}><Download className="size-4" aria-hidden="true" />Export {selected.length ? countLabel(selected.length, 'card') : 'selected'}</Button>
          </div>
        )}
        <ErrorNotice message={data.error} onRetry={data.reload} />
        {data.loading && !results && <SkeletonList rows={3} />}
        {data.loading && results && <LoadingIndicator message="Updating the list…" />}

        {!data.loading && results && results.groups.length === 0 && (
          <div className="xm-card">
            <EmptyState icon={<CalendarSearch />} title={`No new matches for ${prettyRange(results.date_from, results.date_to)}`}
              description={latest ? `The most recent new matches were found on ${prettyRange(latest, latest)}.` : 'New matches appear here after you upload a chat export and new buyer–listing pairs are found.'}
              action={latest
                ? <Button onPress={() => setPeriod({ preset: 'custom', from: latest, to: latest })}>Show {prettyRange(latest, latest)}</Button>
                : navigate ? <Button variant="secondary" onPress={() => navigate(ROUTES.upload)}>Upload a chat</Button> : undefined} />
          </div>
        )}

        <div aria-busy={data.loading} inert={data.loading || !!data.error} className={cn('grid items-start gap-4 transition-opacity sm:grid-cols-2 2xl:grid-cols-3', data.loading && results && 'opacity-60')}>
          {results?.groups.map((group) => {
            const id = group.source.entity_id ?? group.source.id;
            return (
              <SourceCard key={id} direction={direction} showSender selected={active?.source.entity_id === group.source.entity_id}
                row={{ ...group.source, hot_count: group.hot_count, warm_count: group.warm_count, custom_count: group.custom_count ?? 0, match_count: group.pair_count }}
                onSelect={() => setOpened({ key: filterKey, group })} onStatus={(status) => changeStatus(group.source, status)}
                bulk={bulkMode ? { checked: selected.includes(id), onToggle: () => toggleSource(id) } : undefined}
                extra={(
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-separator pt-3">
                    <span className="flex items-center gap-1.5 text-caption text-muted">
                      Found {relativeDateTime(group.latest_found_at)}
                      {isNew(group.latest_found_at) && <span className="rounded-full bg-accent px-1.5 py-px text-[0.6875rem] font-semibold text-accent-foreground uppercase">New</span>}
                    </span>
                    <span className="text-sm font-semibold text-accent">See {countLabel(group.pair_count, 'match', 'matches')} →</span>
                  </div>
                )} />
            );
          })}
        </div>
        {results?.has_more && <Button variant="secondary" fullWidth isDisabled={data.loading} onPress={() => setMore({ key: filterKey, extra: limit })}>Show more</Button>}
      </Section>

      {active && (
        <RecentDetailModal key={filterKey + active.source.entity_id} group={active} direction={direction}
          filters={{ date_from: period.from, date_to: period.to, temps: temps.join(','), include_inactive: includeInactive }}
          onClose={() => setOpened(null)} onStatus={changeStatus}
          onExport={(ids) => exportResults(ids ? { recent_events: ids } : { recent_sources: [active.source.entity_id] })}
          onManual={navigate ? () => { setOpened(null); navigate(ROUTES.match, { [ROUTE_PARAMS.direction]: direction, [ROUTE_PARAMS.id]: active.source.public_id }); } : undefined} />
      )}
      <ExportAllModal state={exportState} direction={exportDirection} filters={exportFilters} />
      {statusDialog}
    </div>
  );
}
