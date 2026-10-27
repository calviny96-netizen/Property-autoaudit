'use client';

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Accordion, Button, Label, SearchField, Switch, ToggleButton, ToggleButtonGroup, toast, useOverlayState } from '@heroui/react';
import { ArrowLeft, Building2, Download, ListChecks, MousePointerClick, RotateCcw, SearchX, Trash2, UsersRound, X } from 'lucide-react';
import {
  Collapsible, EmptyState, ErrorNotice, FilterGroup, IdChip, PageHeader, PagerBar, Panel, Segmented, SkeletonList, StatusBadge, StatusFilter,
} from '@/components/app/primitives';
import { AnalysisWindowNotice, RunningJobBar } from '@/components/app/analysis-window-notice';
import { PRESET_LABELS, PeriodPicker, periodFor, periodPresets, type Period, type PresetId } from '@/components/app/period-picker';
import { useStatusActions } from '@/components/app/status-menu';
import { PartText, PurposeTags, RecommendationCard, SourceCard, StrictNote } from '@/components/match/cards';
import { ExportAllModal } from '@/components/match/export-all-modal';
import { JevMatchBadge } from '@/components/jev/jev-badge';
import { SelectionTray } from '@/components/match/selection-tray';
import { errorMessage, isAbort, query } from '@/lib/api';
import {
  MATCH_STATUSES, STATUS_LABELS, cleanName, countLabel, displaySender, formatPhone, isMessagePart, matchStatusOf, messageText, monthStart, number, prettyRange,
  relativeDate, shiftDay, structuredSummary, todayWib,
} from '@/lib/format';
import { ROUTES, ROUTE_PARAMS, type Navigate } from '@/lib/router';
import { useCompany, useManage, useSession } from '@/lib/session';
import { BASKET_MAX, basketItem, basketKey, useSelectionBasket, type SelectionBasket } from '@/lib/selection-basket';
import { useData, useDebounced } from '@/lib/use-data';
import type { Direction, EntityStatus, Group, GroupBy, JevLookup, MatchFilter, Row, RowGroupSummary, Temperature } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

const PAGE = 200;
const STOCK: EntityStatus[] = ['ready', 'on_hold', 'sold', 'deleted'];
const PAIR_STATUSES: Temperature[] = ['hot', 'warm', 'custom'];
const DEFAULT_TEMPS: MatchFilter[] = ['hot', 'warm', 'custom'];
const ALL_TEMPS: MatchFilter[] = ['hot', 'warm', 'custom', 'unmatched'];
/** Raw group key of messages without a WhatsApp author (see api/workspace_cache.py). */
const NO_SENDER = 'Tanpa nama pengirim';
/** Custom is new: a saved status choice from before it existed gets it once; after that the person's choice stands. */
const CUSTOM_ADDED_KEY = 'xm.match.custom-added';

const presets = () => periodPresets(['all', 'today', 'yesterday', 'today_yesterday', 'week', 'month', 'last_month', 'last_1_month', 'last_3_months']);

type ListFilters = { temps: MatchFilter[]; stock: EntityStatus[]; buyerPeriod: Period; listingPeriod: Period; publicId: string; phones: string };
type RowsResponse = { rows: Row[]; has_more: boolean };
type Base = Record<string, string>;
const ALL_PERIOD: Period = { preset: 'all', from: '', to: '' };
const OPEN_FILTERS: Partial<ListFilters> = { buyerPeriod: ALL_PERIOD, listingPeriod: ALL_PERIOD, temps: ALL_TEMPS, stock: ['ready'], publicId: '', phones: '' };
const EMPTY_MARKERS: Record<Direction, Record<string, number>> = { buyer: {}, property: {} };

const periodText = (period: Period) => (period.preset !== 'custom' && PRESET_LABELS[period.preset as PresetId]) || prettyRange(period.from, period.to);

// Decided once per page load (repeated preference loads agree); a manual status change ends it.
let addCustom: boolean | undefined;
function withCustom(statuses: MatchFilter[]): MatchFilter[] {
  if (addCustom === undefined) {
    let seen = false;
    try {
      seen = window.localStorage.getItem(CUSTOM_ADDED_KEY) === '1';
      window.localStorage.setItem(CUSTOM_ADDED_KEY, '1');
    } catch { /* storage blocked: Custom is simply included */ }
    addCustom = !seen;
  }
  if (!addCustom || statuses.includes('custom') || !statuses.includes('warm')) return statuses;
  return MATCH_STATUSES.filter((status) => status === 'custom' || statuses.includes(status));
}

export default function MatchPage({ params, navigate }: { params?: URLSearchParams; navigate?: Navigate }) {
  const api = useApi();
  const { company } = useCompany();
  const { user } = useSession();
  const { target: managed } = useManage();
  // Ticked pairs outlive the list below (it remounts on every filter change) and the page itself.
  const basket = useSelectionBasket(`xm.match.basket:${user.id}:${managed?.userId ?? user.workspace_id ?? user.id}`);
  const hasCompany = !!company;
  const scopeKey = JSON.stringify([company?.matching_mode, company?.search_terms, company?.tracked_phones]);
  const linkId = params?.get(ROUTE_PARAMS.id) ?? '';
  const linkPhone = params?.get(ROUTE_PARAMS.phone) ?? '';
  const urlDirection = params?.get(ROUTE_PARAMS.direction);

  const [direction, setDirection] = useState<Direction>(urlDirection === 'property' ? 'property' : 'buyer');
  // A link naming one item or one sales number must show everything of theirs, not only matches that are still Ready.
  const fromLink = !!(linkId || linkPhone);
  const [filters, setFilters] = useState<ListFilters>({
    temps: fromLink ? ALL_TEMPS : DEFAULT_TEMPS, stock: fromLink ? ['ready', 'on_hold', 'sold'] : ['ready'],
    // Buyers default to today and yesterday (a link to one item shows everything of theirs).
    buyerPeriod: fromLink ? ALL_PERIOD : periodFor('today_yesterday'), listingPeriod: ALL_PERIOD, publicId: linkId, phones: linkPhone,
  });
  const [prefsReady, setPrefsReady] = useState(false);
  // Arriving with a specific ID or phone number: show the plain list so the card is right there.
  const [grouped, setGrouped] = useState(!fromLink);
  // Arriving from a link that names one item opens straight into its matches.
  const [mobileDetail, setMobileDetail] = useState(() => !!linkId);
  const [markerCache, setMarkerCache] = useState<Record<string, Record<Direction, Record<string, number>>>>({});
  const markers = markerCache[scopeKey] ?? EMPTY_MARKERS;
  const exportAll = useOverlayState();
  // "Export both directions" (one file per person, Listing → Buyer then Buyer → Listing) or the open direction only.
  const [exportBoth, setExportBoth] = useState(true);
  // Phones only: the filters fold into a one-line summary so the list is close to the top.
  const [filtersOpen, setFiltersOpen] = useState(false);

  // Remember the last direction and the status choice per person.
  useEffect(() => {
    void api.get<{ direction: Direction; statuses: MatchFilter[] }>('/preferences').then((prefs) => {
      if (!urlDirection && (prefs.direction === 'buyer' || prefs.direction === 'property')) setDirection(prefs.direction);
      const saved = (prefs.statuses ?? []).filter((status) => ALL_TEMPS.includes(status));
      if (saved.length && !fromLink) setFilters((current) => ({ ...current, temps: withCustom(saved) }));
    }).catch(() => {}).finally(() => setPrefsReady(true));
  }, [api, urlDirection, fromLink]);
  useEffect(() => {
    if (!prefsReady || fromLink) return;
    const timer = window.setTimeout(() => { void api.put('/preferences', { direction, statuses: filters.temps.length ? filters.temps : DEFAULT_TEMPS }).catch(() => {}); }, 500);
    return () => window.clearTimeout(timer);
  }, [api, direction, filters.temps, prefsReady, fromLink]);

  const groupBy: GroupBy = company?.listing_group_by ?? 'sender';
  const salesMode = company?.matching_mode === 'sales';
  const search = salesMode ? '' : (company?.effective_terms ?? company?.search_terms ?? []).join('\n');
  const debouncedId = useDebounced(filters.publicId, 350);
  const debouncedPhones = useDebounced(filters.phones, 500);
  const useGroups = direction === 'property' && grouped;
  const base = useMemo<Base>(() => ({
    direction, search, statuses: filters.temps.join(','), stock_status: filters.stock.join(',') || 'ready',
    public_id: debouncedId, phones: direction === 'property' ? (debouncedPhones || (salesMode ? company?.tracked_phones.join(',') ?? '' : '')) : '',
    buyer_date_from: filters.buyerPeriod.from, buyer_date_to: filters.buyerPeriod.to,
    listing_date_from: filters.listingPeriod.from, listing_date_to: filters.listingPeriod.to,
  }), [direction, search, salesMode, company?.tracked_phones, filters.temps, filters.stock, debouncedId, debouncedPhones, filters.buyerPeriod, filters.listingPeriod]);

  // Every change of a filter goes through here, so the open detail view closes with it.
  const update = (patch: Partial<ListFilters>) => { setFilters((current) => ({ ...current, ...patch })); setMobileDetail(false); };
  function changeDirection(next: Direction) {
    if (next === direction) return;
    setDirection(next);
    setFilters((current) => ({ ...current, phones: '', publicId: '' }));
    setMobileDetail(false);
  }

  const loadMarkers = useCallback((kind: Direction, monthIso: string) => {
    void api.get<{ counts: Record<string, number> }>(`/workspace/dates?${query({ direction: kind, date_from: monthIso, date_to: shiftDay(monthIso, 62) })}`)
      .then((result) => setMarkerCache((current) => {
        const saved = current[scopeKey] ?? EMPTY_MARKERS;
        return { ...current, [scopeKey]: { ...saved, [kind]: { ...saved[kind], ...result.counts } } };
      })).catch(() => {});
  }, [api, scopeKey]);

  useEffect(() => {
    if (!hasCompany) return;
    const month = monthStart(todayWib());
    loadMarkers('buyer', month);
    loadMarkers('property', month);
  }, [hasCompany, loadMarkers]);

  const sourceLabel = direction === 'buyer' ? 'buyer' : 'listing';
  const targetLabel = direction === 'buyer' ? 'listing' : 'buyer';
  const moreActive = (filters.stock.length !== 1 || filters.stock[0] !== 'ready' ? 1 : 0) + (filters.publicId ? 1 : 0) + (filters.phones ? 1 : 0);
  const canManage = !!company?.permissions.manage_settings;

  const scopeNote = (
    <p className="text-caption leading-relaxed text-muted">
      {salesMode
        ? <>Only {sourceLabel}s whose contact number is on your sales watchlist: <strong className="font-semibold text-foreground">{company?.tracked_phones.map(formatPhone).join(' · ') || 'no numbers yet'}</strong>.</>
        : company?.search_terms.length
          ? <>Only {sourceLabel}s that mention your company keywords: <strong className="font-semibold text-foreground">{company.search_terms.join(' · ')}</strong>.</>
          : <>Showing every {sourceLabel} in your company data. No company keywords are set yet.</>}
      {' '}This applies to the list, the matches and every export.
      {canManage
        ? <> <button type="button" className="font-semibold text-accent hover:underline" onClick={() => navigate?.(ROUTES.settings, { tab: salesMode ? 'sales' : 'pencocokan' })}>Change in Settings</button></>
        : <> Your company admin manages these rules.</>}
    </p>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Match"
        description={`Pick one of your ${sourceLabel}s to see the ${targetLabel}s that fit best.`}
        actions={(
          <div className="flex flex-wrap gap-2">
            <Button isDisabled={!company} onPress={() => { setExportBoth(true); exportAll.open(); }}><Download className="size-4" aria-hidden="true" />Export both directions</Button>
            <Button variant="secondary" isDisabled={!company} onPress={() => { setExportBoth(false); exportAll.open(); }}>{direction === 'buyer' ? 'Buyer → Listing' : 'Listing → Buyer'} only</Button>
          </div>
        )}
      />

      <fieldset aria-label="What do you want to match?" className="mx-0 grid min-w-0 grid-cols-2 gap-3 border-0 p-0">
        {([
          ['buyer', 'Buyer → Listing', 'Find listings for your buyers.', UsersRound],
          ['property', 'Listing → Buyer', 'Find buyers for your listings.', Building2],
        ] as const).map(([id, title, text, Icon]) => {
          const on = direction === id;
          return (
            <button key={id} type="button" aria-pressed={on} onClick={() => changeDirection(id)}
              className={cn('flex min-h-14 min-w-0 items-center gap-3 rounded-2xl border p-3 text-left transition sm:p-4',
                on ? 'border-accent bg-accent-soft/60 ring-2 ring-accent/20' : 'border-border bg-surface hover:border-accent/50')}>
              <span aria-hidden="true" className={cn('hidden size-10 shrink-0 items-center justify-center rounded-xl sm:flex', on ? 'bg-accent text-accent-foreground' : 'bg-default text-muted')}><Icon className="size-5" /></span>
              <span className="min-w-0">
                <span className={cn('block text-sm font-semibold sm:text-body', on ? 'text-accent-soft-foreground' : 'text-foreground')}>{title}</span>
                <span className="mt-0.5 hidden text-sm text-muted sm:block">{text}</span>
              </span>
            </button>
          );
        })}
      </fieldset>

      <Panel title="Filters" className={cn(mobileDetail && 'hidden xl:block')} footer={scopeNote}
        actions={<Button size="sm" variant="tertiary" className="sm:hidden" aria-expanded={filtersOpen} onPress={() => setFiltersOpen((open) => !open)}>{filtersOpen ? 'Hide' : 'Change'}</Button>}>
        {!filtersOpen && (
          <button type="button" onClick={() => setFiltersOpen(true)} className="-mt-1 block w-full space-y-2 text-left sm:hidden">
            <span className="block text-sm text-foreground">Buyers: <strong className="font-semibold">{periodText(filters.buyerPeriod)}</strong> · Listings: <strong className="font-semibold">{periodText(filters.listingPeriod)}</strong></span>
            <span className="flex flex-wrap items-center gap-1.5">
              {filters.temps.map((status) => <StatusBadge key={status} status={status} size="sm" explain={false} />)}
              {moreActive > 0 && <span className="text-caption font-semibold text-accent">+{moreActive} more {moreActive === 1 ? 'filter' : 'filters'}</span>}
            </span>
          </button>
        )}
        <div className={cn('space-y-5', !filtersOpen && 'max-sm:hidden')}>
          <FilterGroup label="Quick picks">
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" onPress={() => update({ buyerPeriod: periodFor('today'), listingPeriod: periodFor('last_3_months') })}>Today’s buyers × 3 months of listings</Button>
              <Button variant="secondary" size="sm" onPress={() => update({ buyerPeriod: periodFor('last_1_month'), listingPeriod: periodFor('today') })}>Today’s listings × past month of buyers</Button>
            </div>
          </FilterGroup>
          <div className="grid gap-5 2xl:grid-cols-2">
            {([{ kind: 'buyer', key: 'buyerPeriod', label: 'Buyer posted' }, { kind: 'property', key: 'listingPeriod', label: 'Listing posted' }] as const).map(({ kind, key, label }) => (
              <FilterGroup key={key} label={label}>
                <PeriodPicker label={label} value={filters[key]} onChange={(period) => update({ [key]: period })} presets={presets()} max={todayWib()} markers={markers[kind]} onMonthChange={(month) => loadMarkers(kind, month)} />
              </FilterGroup>
            ))}
          </div>
          <p className="-mt-2 text-caption text-muted">Both dates apply in either direction, including the matches and PDFs. “Last month” is the previous calendar month; “Past month” counts back from today.</p>
          <FilterGroup label="Show">
            <StatusFilter value={filters.temps} onChange={(temps) => { addCustom = false; update({ temps }); }} />
          </FilterGroup>
          <Collapsible title="More filters" description={direction === 'property' ? 'Stock status, ID, sales phone' : 'Stock status, ID'} defaultOpen={moreActive > 0}
            badge={moreActive > 0 ? <span className="rounded-full bg-accent-soft px-2 py-0.5 text-caption font-semibold text-accent-soft-foreground">{moreActive} active</span> : undefined}
            bodyClassName="space-y-5">
            <FilterGroup label={`${sourceLabel === 'buyer' ? 'Buyer' : 'Listing'} and ${targetLabel} status`} hint="Only Ready by default. Choose On hold, Sold or Deleted to see or restore those.">
              <ToggleButtonGroup aria-label="Stock status" selectionMode="multiple" isDetached selectedKeys={new Set(filters.stock)} className="max-w-full flex-wrap gap-2"
                onSelectionChange={(keys) => { const next = STOCK.filter((status) => keys.has(status)); update({ stock: next.length ? next : ['ready'] }); }}>
                {STOCK.map((status) => <ToggleButton key={status} id={status} className="min-h-11 px-4 text-sm font-semibold sm:min-h-10">{STATUS_LABELS[status]}</ToggleButton>)}
              </ToggleButtonGroup>
            </FilterGroup>
            <div className="grid gap-4 sm:grid-cols-2">
              <SearchField value={filters.publicId} onChange={(value) => update({ publicId: value })} name="id">
                <Label className="text-sm font-semibold">Find ID</Label>
                <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder="e.g. AB908 or L-AB908" /><SearchField.ClearButton /></SearchField.Group>
              </SearchField>
              {direction === 'property' && (
                <SearchField value={filters.phones} onChange={(value) => update({ phones: value })} name="phones">
                  <Label className="text-sm font-semibold">Sales phone</Label>
                  <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder="Separate several numbers with commas" inputMode="tel" /><SearchField.ClearButton /></SearchField.Group>
                </SearchField>
              )}
            </div>
            <p className="text-caption text-muted">The ID and phone filters only narrow down the {sourceLabel}s that already follow your company rules.</p>
          </Collapsible>
        </div>
      </Panel>

      {/* Earliest of the two periods ('' = all dates): older data than the analysis window may not be matched yet. */}
      <AnalysisWindowNotice periodStart={[filters.buyerPeriod.from, filters.listingPeriod.from].some((from) => !from) ? '' : [filters.buyerPeriod.from, filters.listingPeriod.from].sort()[0]} showProgress={false} />
      <RunningJobBar />
      {/* Remounting on every new list resets the selection, paging and open groups without any syncing effects. */}
      <MatchWorkspace
        key={`${scopeKey}|${JSON.stringify(base)}|${useGroups}|${groupBy}`}
        direction={direction} base={base} useGroups={useGroups} groupBy={groupBy} enabled={prefsReady && !!company}
        temps={filters.temps} grouped={grouped} onGrouped={setGrouped} canSetGrouping={canManage}
        mobileDetail={mobileDetail} onMobileDetail={setMobileDetail} linkedId={linkId}
        onReset={() => update(OPEN_FILTERS)} basket={basket}
      />
      <ExportAllModal state={exportAll} direction={direction} filters={base} combined={exportBoth} />
    </div>
  );
}

type WorkspaceProps = {
  direction: Direction; base: Base; useGroups: boolean; groupBy: GroupBy; enabled: boolean; temps: MatchFilter[];
  grouped: boolean; onGrouped: (value: boolean) => void; canSetGrouping: boolean; mobileDetail: boolean; onMobileDetail: (value: boolean) => void;
  linkedId: string; onReset: () => void; basket: SelectionBasket;
};
const FULL_MESSAGE = `Your selection is full (${BASKET_MAX} pairs, the most one export takes). Download it or remove some pairs first.`;

function MatchWorkspace({ direction, base, useGroups, groupBy, enabled, temps, grouped, onGrouped, canSetGrouping, mobileDetail, onMobileDetail, linkedId, onReset, basket }: WorkspaceProps) {
  const api = useApi();
  const [groupSearch, setGroupSearch] = useState('');
  const [expanded, setExpanded] = useState<Set<string | number>>(new Set());
  const [offset, setOffset] = useState(0);
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<Row | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [includeHold, setIncludeHold] = useState(false);
  const [bulkMode, setBulkMode] = useState(false);
  const [bulk, setBulk] = useState<Record<string, Row>>({});
  const [selectingAll, setSelectingAll] = useState(false);
  const debouncedGroupSearch = useDebounced(groupSearch, 300);

  const bump = useCallback(() => setTick((value) => value + 1), []);
  const { request: requestStatus, dialog: statusDialog } = useStatusActions(bump);

  const flat = useData((signal) => api.get<RowsResponse>(`/workspace?${query({ ...base, offset })}`, signal), [base, offset, tick], enabled && !useGroups);
  const groups = useData((signal) => api.get<{ groups: RowGroupSummary[]; has_more: boolean }>(`/workspace/groups?${query({ ...base, group_by: groupBy, group_search: debouncedGroupSearch })}`, signal),
    [base, groupBy, debouncedGroupSearch, tick], enabled && useGroups);
  // A link from another page ("Open in Match") that names exactly one item opens it without a click.
  const linked = !dismissed && linkedId && flat.data?.rows.length === 1 ? flat.data.rows[0] : null;
  const active = selected ?? linked;
  const recs = useData((signal) => api.post<{ groups: Group[] }>('/workspace/recommendations', {
    direction, ids: [active?.id], target_status: includeHold ? ['ready', 'on_hold'] : ['ready'],
    buyer_date_from: base.buyer_date_from, buyer_date_to: base.buyer_date_to,
    listing_date_from: base.listing_date_from, listing_date_to: base.listing_date_to,
  }, signal), [active?.id, direction, includeHold, tick, base], !!active);

  const select = useCallback((row: Row) => {
    setSelected(row);
    onMobileDetail(true);
    // Side by side on a wide screen the panel is already in view; on a phone it replaces the list.
    if (!window.matchMedia('(min-width: 1280px)').matches) window.requestAnimationFrame(() => window.scrollTo({ top: 0 }));
  }, [onMobileDetail]);

  const group = recs.data?.groups[0];
  const all = useMemo(() => group?.recommendations ?? [], [group]);
  const visible = useMemo(() => all.filter((row) => temps.includes(matchStatusOf(row))), [all, temps]);
  const jev = useJevBadges(direction, group?.source, visible);
  const statusCounts = useMemo(() => {
    const counts: Record<Temperature, number> = { hot: 0, warm: 0, custom: 0 };
    for (const row of all) counts[matchStatusOf(row)] += 1;
    return counts;
  }, [all]);
  const unmatched = !!group && all.length === 0;
  const keyFor = (target?: Row) => basketKey(direction, group?.source.id ?? '', target?.id);
  const keys = visible.length ? visible.map((target) => keyFor(target)) : unmatched && temps.includes('unmatched') ? [keyFor()] : [];
  const picked = keys.filter((key) => basket.has(key));
  const allPicked = keys.length > 0 && picked.length === keys.length;

  /** Ticks add to (or remove from) the selection kept across every buyer and listing, in both directions. */
  function togglePair(target: Row | null) {
    if (!group) return;
    const key = keyFor(target ?? undefined);
    if (basket.has(key)) { basket.remove([key]); return; }
    if (basket.add([basketItem(direction, group.source, target)]).skipped) toast.danger(FULL_MESSAGE);
  }
  function toggleAll() {
    if (!group) return;
    if (allPicked) { basket.remove(keys); return; }
    const { added, skipped } = basket.add(visible.map((target) => basketItem(direction, group.source, target)));
    if (skipped) toast.warning(added ? `Added ${countLabel(added, 'pair')}; ${number(skipped)} did not fit. ${FULL_MESSAGE}` : FULL_MESSAGE);
  }

  const sourceLabel = direction === 'buyer' ? 'buyer' : 'listing';
  const targetLabel = direction === 'buyer' ? 'listing' : 'buyer';
  const statusOf = (row: Row): EntityStatus => row.entity_status ?? 'ready';
  const refOf = (row: Row) => row.entity_id ?? row.public_id ?? '';
  const changeSource = (row: Row, status: EntityStatus) => {
    requestStatus({ refs: [refOf(row)], status, name: `${direction === 'buyer' ? 'Buyer' : 'Listing'} ${row.public_id ?? ''}`.trim(), previous: statusOf(row), kind: direction === 'buyer' ? 'buyer' : 'property' });
    if (active?.id === row.id && status !== 'ready') { setSelected(null); setDismissed(true); onMobileDetail(false); }
  };
  const changeTarget = (row: Row, status: EntityStatus) => requestStatus({ refs: [refOf(row)], status, name: `${direction === 'buyer' ? 'Listing' : 'Buyer'} ${row.public_id ?? ''}`.trim(), previous: statusOf(row), kind: direction === 'buyer' ? 'property' : 'buyer' });
  const bulkRows = Object.values(bulk);

  function applyBulk(status: EntityStatus) {
    if (!bulkRows.length) return;
    const previous = bulkRows.every((row) => statusOf(row) === statusOf(bulkRows[0])) ? statusOf(bulkRows[0]) : undefined;
    requestStatus({ refs: bulkRows.map(refOf), status, name: countLabel(bulkRows.length, sourceLabel), previous, kind: direction === 'buyer' ? 'buyer' : 'property' });
    setBulk({});
    setBulkMode(false);
  }
  // The same buyer or listing can be shown through different copies of its message, so a choice follows the entity.
  const bulkKey = (row: Row) => row.entity_id ?? row.id;
  const bulkProps = (row: Row) => bulkMode ? { checked: !!bulk[bulkKey(row)], onToggle: () => setBulk((current) => { const next = { ...current }; if (next[bulkKey(row)]) delete next[bulkKey(row)]; else next[bulkKey(row)] = row; return next; }) } : undefined;

  /** Everything the filters return: later pages and unopened groups too, not only the cards on screen. */
  async function selectAll() {
    setSelectingAll(true);
    try {
      const everything: Record<string, Row> = {};
      for (let next = 0; ; next += PAGE) {
        const page = await api.get<RowsResponse>(`/workspace?${query({ ...base, offset: next })}`);
        for (const row of page.rows) everything[bulkKey(row)] = row;
        if (!page.has_more) break;
      }
      setBulk(everything);
    } catch (reason) { toast.danger(errorMessage(reason, 'Could not select everything. Please try again.')); } finally { setSelectingAll(false); }
  }
  const card = (row: Row, withSender = true) => (
    <SourceCard key={row.id} row={row} direction={direction} selected={active?.id === row.id} showSender={withSender}
      onSelect={() => select(row)} onStatus={(status) => changeSource(row, status)} bulk={bulkProps(row)} />
  );
  const shownCount = !useGroups && flat.data ? `${number(offset + flat.data.rows.length)}${flat.data.has_more ? '+' : ''}` : '';

  return (
    <>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(380px,42%)_1fr]">
        {/* ------------------------------------------------------------ list */}
        <section aria-label={`${sourceLabel === 'buyer' ? 'Buyers' : 'Listings'}`} aria-busy={useGroups ? groups.loading : flat.loading} className={cn('min-w-0 space-y-3', mobileDetail && 'hidden xl:block')}>
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <h2 className="text-section font-semibold text-foreground">
              Your {sourceLabel}s
              {!useGroups && flat.data && <span className="ml-2 text-sm font-normal text-muted tabular-nums">{shownCount} shown</span>}
            </h2>
            <div className="flex flex-wrap items-center gap-2">
              {direction === 'property' && (
                <Switch isSelected={grouped} onChange={onGrouped}>
                  <Switch.Content><Switch.Control><Switch.Thumb /></Switch.Control><span className="text-sm font-semibold">Group by {groupBy === 'phone' ? 'sales contact' : 'sender'}</span></Switch.Content>
                </Switch>
              )}
              <Button size="sm" variant={bulkMode ? 'primary' : 'secondary'} onPress={() => { setBulkMode((value) => !value); setBulk({}); }}>
                <ListChecks className="size-4" aria-hidden="true" />{bulkMode ? 'Done' : 'Select several'}
              </Button>
            </div>
          </div>
          {direction === 'property' && grouped && canSetGrouping && <GroupBySwitch value={groupBy} />}

          {useGroups ? (
            <>
              <SearchField value={groupSearch} onChange={setGroupSearch} aria-label={groupBy === 'phone' ? 'Search sales contacts' : 'Search senders'}>
                <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder={groupBy === 'phone' ? 'Search by sales name or phone' : 'Search by sender name'} /><SearchField.ClearButton /></SearchField.Group>
              </SearchField>
              <ErrorNotice message={groups.error} onRetry={groups.reload} />
              {groups.loading && !groups.data && <SkeletonList rows={4} />}
              {groups.data && groups.data.groups.length === 0 && !groups.loading && (debouncedGroupSearch ? <SearchEmpty term={debouncedGroupSearch} onClear={() => setGroupSearch('')} /> : <ListEmpty onReset={onReset} />)}
              {groups.data && groups.data.groups.length > 0 && (
                <div className={cn('xm-card overflow-clip px-1 transition-opacity sm:px-2', groups.loading && 'opacity-60')}>
                  <Accordion allowsMultipleExpanded expandedKeys={expanded} onExpandedChange={setExpanded} className="w-full">
                    {groups.data.groups.map((item) => (
                      <Accordion.Item key={item.key || '__none'} id={item.key || '__none'}>
                        <Accordion.Heading>
                          <Accordion.Trigger className="min-h-16 py-3">
                            <span className="flex min-w-0 flex-1 flex-col items-start gap-1.5 text-left">
                              <span className="min-w-0 text-body font-semibold break-words text-foreground">{groupTitle(item, groupBy)}</span>
                              <span className="flex flex-wrap items-center gap-1.5">
                                <span className="mr-0.5 text-caption text-muted">{countLabel(item.count, 'listing')}{item.latest_at ? ` · last posted ${relativeDate(item.latest_at).toLowerCase()}` : ''}</span>
                                {item.hot > 0 && <StatusBadge status="hot" count={item.hot} size="sm" explain={false} />}
                                {item.warm > 0 && <StatusBadge status="warm" count={item.warm} size="sm" explain={false} />}
                                {(item.custom ?? 0) > 0 && <StatusBadge status="custom" count={item.custom ?? 0} size="sm" explain={false} />}
                              </span>
                            </span>
                            <Accordion.Indicator />
                          </Accordion.Trigger>
                        </Accordion.Heading>
                        <Accordion.Panel>
                          <Accordion.Body>
                            {expanded.has(item.key || '__none') && <GroupRows key={tick} base={base} groupBy={groupBy} groupKey={item.key} render={(row) => card(row, false)} />}
                          </Accordion.Body>
                        </Accordion.Panel>
                      </Accordion.Item>
                    ))}
                  </Accordion>
                </div>
              )}
              {groups.data?.has_more && <p className="text-caption text-muted">Showing the largest groups first. Search to find a specific {groupBy === 'phone' ? 'sales contact' : 'sender'}.</p>}
            </>
          ) : (
            <>
              <ErrorNotice message={flat.error} onRetry={flat.reload} />
              {flat.loading && !flat.data && <SkeletonList rows={5} />}
              {!flat.loading && flat.data && flat.data.rows.length === 0 && <ListEmpty onReset={onReset} />}
              <div aria-busy={flat.loading} className={cn('space-y-3 transition-opacity', flat.loading && 'pointer-events-none opacity-60')}>{flat.data?.rows.map((row) => card(row))}</div>
              {flat.data && (offset > 0 || flat.data.has_more) && (
                <PagerBar from={flat.data.rows.length ? offset + 1 : offset} to={offset + flat.data.rows.length} hasPrev={offset > 0} hasNext={flat.data.has_more} loading={flat.loading}
                  onPrev={() => { setOffset(Math.max(0, offset - PAGE)); window.scrollTo({ top: 0 }); }} onNext={() => { setOffset(offset + PAGE); window.scrollTo({ top: 0 }); }} />
              )}
            </>
          )}
        </section>

        {/* ----------------------------------------------------- matches of the chosen source */}
        <section aria-label={`Matching ${targetLabel}s`} aria-busy={recs.loading} className={cn('min-w-0 xl:sticky xl:top-4', !mobileDetail && 'hidden xl:block')}>
          <div className="xm-card overflow-clip">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-separator px-3 py-2.5 sm:px-4">
              <div className="flex min-w-0 items-center gap-1">
                <Button isIconOnly variant="tertiary" className="xl:hidden" aria-label="Back to the list" onPress={() => { setDismissed(true); onMobileDetail(false); }}><ArrowLeft className="size-5" aria-hidden="true" /></Button>
                <h2 className="text-section font-semibold text-foreground">Matching {targetLabel}s{group && all.length > 0 ? <span className="ml-2 text-sm font-normal text-muted tabular-nums">{number(all.length)}</span> : null}</h2>
              </div>
              <Switch isSelected={includeHold} onChange={setIncludeHold}>
                <Switch.Content><Switch.Control><Switch.Thumb /></Switch.Control><span className="text-sm font-semibold">Include on hold</span></Switch.Content>
              </Switch>
            </div>
            <div className="space-y-3 bg-background/60 p-3 sm:p-4 xl:max-h-[calc(100dvh-13rem)] xl:overflow-y-auto">
              {!active && (
                <EmptyState icon={<MousePointerClick />} title={`Pick a ${sourceLabel} to see its matches`}
                  description={`Hot, Warm and Custom ${targetLabel}s for that ${sourceLabel} appear here — or No match if nothing fits yet.`} />
              )}
              {active && recs.loading && !group && <SkeletonList rows={2} />}
              <ErrorNotice message={recs.error} onRetry={recs.reload} />
              {group && (
                <div className={cn('space-y-3 transition-opacity', recs.loading && 'pointer-events-none opacity-60')}>
                  <SourceSummary source={group.source} kind={direction} counts={statusCounts} />
                  {keys.length > 0 && (
                    <div className="flex min-h-9 flex-wrap items-center justify-between gap-2 px-1">
                      <span className="text-caption text-muted">
                        {picked.length ? `${number(picked.length)} of these in your selection.` : 'Tick matches to add them to your PDF selection.'}
                        {' '}Ticks stay when you open another {sourceLabel} or switch direction.
                      </span>
                      {keys.length > 1 && <Button size="sm" variant="tertiary" onPress={toggleAll}>{allPicked ? 'Untick all' : 'Tick all'}</Button>}
                    </div>
                  )}
                  {unmatched && temps.includes('unmatched') && (
                    <label className="grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 rounded-2xl border border-border bg-surface p-4 text-sm">
                      <input type="checkbox" className="row-span-2 mt-0.5 size-5 shrink-0 accent-[var(--accent)]" checked={basket.has(keyFor())} onChange={() => togglePair(null)} />
                      <span className="font-semibold text-foreground">No {targetLabel} passes the matching rules yet.</span>
                      <span className="text-muted">Tick to include this {sourceLabel} in the PDF anyway.</span>
                    </label>
                  )}
                  {unmatched && !temps.includes('unmatched') && (
                    <EmptyState compact icon={<SearchX />} title={`No matching ${targetLabel}s yet`} description="Turn on “No match” in the filters to include it in a PDF." />
                  )}
                  {!unmatched && visible.length === 0 && (
                    <EmptyState compact icon={<SearchX />} title="Nothing for the statuses you chose"
                      description={`${countLabel(all.length, 'match', 'matches')} ${all.length === 1 ? 'is' : 'are'} hidden by the status filter. Turn on Hot, Warm and Custom to see them.`} />
                  )}
                  {visible.map((target) => (
                    <RecommendationCard key={target.id} target={target} source={group.source} direction={direction}
                      checked={basket.has(keyFor(target))} onCheck={() => togglePair(target)}
                      onStatus={(status) => changeTarget(target, status)}
                      extra={jevBadge(jev(target))} />
                  ))}
                  {visible.length > 0 && visible.length < all.length && (
                    <p className="px-1 text-caption text-muted">{countLabel(all.length - visible.length, 'more match', 'more matches')} hidden by the status filter.</p>
                  )}
                </div>
              )}
            </div>
          </div>
        </section>
      </div>

      {bulkMode && (
        <div role="toolbar" aria-label="Change several at once" className="pb-safe fixed inset-x-3 bottom-20 z-40 mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-2 rounded-2xl border border-border bg-surface p-3 shadow-raised lg:inset-x-auto lg:right-10 lg:bottom-6 lg:left-[calc(var(--sidebar-width)+2.5rem)]">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="px-1.5 text-sm font-semibold text-foreground tabular-nums">{number(bulkRows.length)} selected</span>
            <Button size="sm" variant="secondary" isPending={selectingAll} onPress={selectAll}>Select all</Button>
            <Button size="sm" variant="tertiary" isDisabled={!bulkRows.length || selectingAll} onPress={() => setBulk({})}>Clear</Button>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {(['ready', 'on_hold', 'sold'] as const).map((status) => <Button key={status} size="sm" variant="secondary" isDisabled={!bulkRows.length} onPress={() => applyBulk(status)}>{STATUS_LABELS[status]}</Button>)}
            <Button size="sm" variant="danger-soft" isDisabled={!bulkRows.length} onPress={() => applyBulk('deleted')}><Trash2 className="size-4" aria-hidden="true" />{direction === 'buyer' ? 'Disable' : 'Delete'}</Button>
            <Button size="sm" isIconOnly variant="tertiary" aria-label="Stop selecting" onPress={() => { setBulk({}); setBulkMode(false); }}><X className="size-4" aria-hidden="true" /></Button>
          </div>
        </div>
      )}
      {!bulkMode && <SelectionTray basket={basket} groupBy={groupBy} />}
      {statusDialog}
    </>
  );
}

/** The chosen buyer or listing, above its matches. */
const jevBadge = (review?: JevLookup) => (review ? <JevMatchBadge review={review} /> : undefined);

/** Super admins: the latest AI Review of each visible pair (one request per opened buyer or listing). */
function useJevBadges(direction: Direction, source: Row | undefined, targets: Row[]) {
  const api = useApi();
  const { company } = useCompany();
  const enabled = !!company?.permissions.jev_review && !!source?.entity_id;
  const pairs = useMemo(() => (enabled ? targets.filter((target) => target.entity_id).slice(0, 500).map((target) => (direction === 'buyer'
    ? { buyer_entity_id: source!.entity_id!, listing_entity_id: target.entity_id! }
    : { buyer_entity_id: target.entity_id!, listing_entity_id: source!.entity_id! })) : []), [enabled, targets, direction, source]);
  const found = useData((signal) => api.post<{ reviews: JevLookup[] }>('/ai/reviews/lookup', { pairs }, signal), [pairs], pairs.length > 0);
  return useMemo(() => {
    const byPair = new Map<string, JevLookup>();
    for (const review of found.data?.reviews ?? []) byPair.set(`${review.buyer_entity_id}:${review.listing_entity_id}`, review);
    // Keyed by both sides, so an answer for the previously opened card never shows on this one.
    return (target: Row) => (enabled && target.entity_id
      ? byPair.get(direction === 'buyer' ? `${source!.entity_id}:${target.entity_id}` : `${target.entity_id}:${source!.entity_id}`) : undefined);
  }, [found.data, direction, enabled, source]);
}

function SourceSummary({ source, kind, counts }: { source: Row; kind: Direction; counts: Record<Temperature, number> }) {
  const label = kind === 'buyer' ? 'buyer' : 'listing';
  const summary = structuredSummary(source);
  return (
    <div className="rounded-2xl border border-accent/20 bg-accent-soft/40 p-4">
      <p className="text-overline font-semibold text-accent uppercase">Matches for this {label}</p>
      <p className="mt-1 text-body font-semibold break-words text-foreground">{cleanName(source.contact_name) || (kind === 'buyer' ? 'Unnamed buyer' : 'Unnamed listing')}</p>
      {(summary || !isMessagePart(source)) && <p className="mt-0.5 text-sm leading-relaxed text-foreground/80">{summary || messageText(source).slice(0, 200)}</p>}
      <PartText row={source} className="mt-2 bg-surface/70" />
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <IdChip id={source.public_id} kind={kind} />
        <PurposeTags row={source} kind={kind} />
        <StrictNote row={source} />
        {PAIR_STATUSES.map((status) => (counts[status] > 0 ? <StatusBadge key={status} status={status} count={counts[status]} size="sm" explain={false} /> : null))}
      </div>
    </div>
  );
}

function groupTitle(item: RowGroupSummary, by: GroupBy) {
  if (item.title) return item.title;
  if (by === 'phone') return item.key ? `${cleanName(item.contact_name) ? `${cleanName(item.contact_name)} · ` : ''}${formatPhone(item.key)}` : 'No phone number';
  return item.key === NO_SENDER ? 'Unknown sender' : displaySender(item.key) || 'Unknown sender';
}

function ListEmpty({ onReset }: { onReset: () => void }) {
  return (
    <div className="xm-card">
      <EmptyState icon={<SearchX />} title="Nothing fits these filters" description="Try another period, turn on more statuses, or clear the ID search."
        action={<Button variant="secondary" onPress={onReset}><RotateCcw className="size-4" aria-hidden="true" />Reset filters</Button>} />
    </div>
  );
}

function SearchEmpty({ term, onClear }: { term: string; onClear: () => void }) {
  return (
    <div className="xm-card">
      <EmptyState compact icon={<SearchX />} title={`No group matches “${term}”`} description="Check the spelling or search for part of the name or number."
        action={<Button variant="secondary" onPress={onClear}>Clear search</Button>} />
    </div>
  );
}

/** Company admins choose here what "group" means. Everyone else just sees the result. */
function GroupBySwitch({ value }: { value: GroupBy }) {
  const api = useApi();
  const { refresh } = useCompany();
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-2xl border border-border bg-surface px-3 py-2.5">
      <span className="text-sm font-semibold text-foreground">Group listings by</span>
      <Segmented label="Group listings by" value={value} options={[{ id: 'sender', label: 'Sender' }, { id: 'phone', label: 'Sales contact' }]}
        onChange={(next) => { void api.put('/company/settings', { listing_group_by: next }).then(() => { refresh(); toast.success('Grouping changed for the whole company'); }).catch((reason) => toast.danger(errorMessage(reason))); }} />
    </div>
  );
}

/** The listings of one opened group, loaded only when it is opened. */
function GroupRows({ base, groupBy, groupKey, render }: { base: Base; groupBy: GroupBy; groupKey: string; render: (row: Row) => ReactNode }) {
  const api = useApi();
  const [extra, setExtra] = useState<Row[]>([]);
  const [next, setNext] = useState(PAGE);
  const [done, setDone] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const first = useData((signal) => api.get<RowsResponse>(`/workspace?${query({ ...base, group_by: groupBy, group_key: groupKey, offset: 0 })}`, signal), [base, groupBy, groupKey]);

  async function more() {
    setLoadingMore(true);
    try {
      const page = await api.get<RowsResponse>(`/workspace?${query({ ...base, group_by: groupBy, group_key: groupKey, offset: next })}`);
      setExtra((current) => [...current, ...page.rows]);
      setNext(next + PAGE);
      setDone(!page.has_more);
    } catch (reason) { if (!isAbort(reason)) toast.danger(errorMessage(reason)); } finally { setLoadingMore(false); }
  }

  if (first.error) return <ErrorNotice message={first.error} onRetry={first.reload} />;
  if (!first.data) return <SkeletonList rows={2} />;
  const rows = [...first.data.rows, ...extra];
  const hasMore = !done && first.data.has_more;
  return (
    <div className="space-y-3 pb-2">
      {rows.length === 0 && <p className="py-4 text-center text-sm text-muted">No listings with these filters.</p>}
      {rows.map(render)}
      {hasMore && <Button variant="secondary" fullWidth isPending={loadingMore} onPress={more}>Show more</Button>}
    </div>
  );
}
