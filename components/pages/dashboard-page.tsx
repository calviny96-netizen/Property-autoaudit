'use client';

import { useMemo, useRef, useState } from 'react';
import { Button, toast } from '@heroui/react';
import { ArrowRight, Boxes, ChevronRight, FileDown, Lightbulb, RefreshCw, Sparkles, Target, TrendingUp, UploadCloud, Users } from 'lucide-react';
import { BarsChart, RingChart, TrendChart } from '@/components/charts/charts';
import { colors, statusColors } from '@/components/charts/chart-setup';
import { EmptyState, ErrorNotice, FilterGroup, IdChip, PageHeader, Panel, Segmented, SkeletonChart, StatCard, StatusBadge } from '@/components/app/primitives';
import { AnalysisWindowNotice } from '@/components/app/analysis-window-notice';
import { DEFAULT_DASHBOARD_PERIOD, PeriodPicker, dashboardPresets, type Period } from '@/components/app/period-picker';
import { categoryLabel, cleanName, countLabel, displaySender, formatPhone, greeting, number, prettyRange, relativeDate, shiftDay, shortDay, structuredSummary, titleCase, todayWib } from '@/lib/format';
import { errorMessage, query } from '@/lib/api';
import { ROUTE_PARAMS, ROUTES, type Navigate } from '@/lib/router';
import { useCompany, useSession } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { Dashboard, MatchStatus, RecentResponse } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
const labelFor = (iso: string, bucket: 'day' | 'week') => (bucket === 'week' ? `Wk of ${shortDay(iso)}` : shortDay(iso));

/** "Albert · 4390" when the message names the sales, otherwise the formatted phone; a sender's clean WhatsApp name. */
function salesChartLabel(groupBy: Dashboard['top_sales']['group_by'], row: Dashboard['top_sales']['rows'][number]) {
  if (groupBy !== 'phone') return displaySender(row.label).slice(0, 28);
  const digits = (row.key ?? '').replace(/\D/g, '');
  const name = cleanName(row.contact_name);
  if (!digits) return name || 'No phone number';
  return name ? `${name.slice(0, 20)} · ${digits.slice(-4)}` : formatPhone(digits);
}

/** Hot · Warm · Custom counts as small status pills (safe inside cards: never interactive). */
function StatusCounts({ hot, warm, custom }: { hot: number; warm: number; custom: number }) {
  const items: [MatchStatus, number][] = [['hot', hot], ['warm', warm], ['custom', custom]];
  return (
    <span className="flex flex-wrap gap-1.5">
      {items.map(([status, count]) => <StatusBadge key={status} status={status} count={count} size="sm" explain={false} />)}
    </span>
  );
}

export default function DashboardPage({ navigate, params }: { navigate?: Navigate; params?: URLSearchParams }) {
  const api = useApi();
  const { user } = useSession();
  const { company, stats, refreshStats } = useCompany();
  const [refreshingStats, setRefreshingStats] = useState(false);
  const forceOverview = useRef(false);
  const scopeKey = JSON.stringify([company?.matching_mode, company?.search_terms, company?.tracked_phones]);
  // Home opens on LAST WEEK (a complete, comparable week). A link such as #/beranda?dari=2026-09-01&sampai=2026-09-30 opens that period.
  const [period, setPeriod] = useState<Period>(() => {
    const from = params?.get(ROUTE_PARAMS.from) ?? '';
    const to = params?.get(ROUTE_PARAMS.to) || from;
    return ISO_DAY.test(from) && ISO_DAY.test(to) ? { preset: 'custom', from, to } : DEFAULT_DASHBOARD_PERIOD;
  });
  const [budgetKind, setBudgetKind] = useState<'sale' | 'rent'>('sale');
  const [exporting, setExporting] = useState(false);
  const periodQuery = query({ period: period.preset === 'custom' ? 'custom' : period.preset, date_from: period.preset === 'custom' ? period.from : undefined, date_to: period.preset === 'custom' ? period.to : undefined });

  const overview = useData((signal) => {
    const force = forceOverview.current;
    forceOverview.current = false;
    return api.get<Dashboard>(`/dashboard/overview?${periodQuery}${force ? '&refresh=true' : ''}`, signal)
      .then((value) => ({ scopeKey, value }));
  }, [scopeKey, period.preset, period.from, period.to], !!company);
  const { error, loading, reload } = overview;
  const data = overview.data?.scopeKey === scopeKey ? overview.data.value : undefined;
  // A ring with a single "Ready" slice says nothing: show the status split only when something is on hold or sold.
  const hasStatusMix = !!data && ['listing', 'buyer'].some((side) => {
    const counts = data.status[side as 'listing' | 'buyer'];
    return counts.on_hold > 0 || counts.sold > 0;
  });

  const canUpload = !!company?.permissions.upload_data;
  const trendLabels = useMemo(() => (data ? data.trend.labels.map((iso) => labelFor(iso, data.period.bucket)) : []), [data]);
  const empty = !!data && data.kpi.buyers === 0 && data.kpi.listings === 0 && data.kpi.matches === 0;
  const displayName = user.display_name.trim();
  const first = (displayName === displayName.toLowerCase() ? titleCase(displayName) : displayName).split(/\s+/)[0] || 'there';
  const matchCounts = data ? { hot: data.matches.hot, warm: data.matches.warm, custom: data.matches.custom ?? 0 } : null;

  /** The Home page of this period as an Indonesian PDF report. */
  async function exportPdf() {
    setExporting(true);
    try {
      const blob = await api.file(`/dashboard/export.pdf?${periodQuery}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = 'laporan-dashboard.pdf'; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast.success('PDF downloaded');
    } catch (reason) {
      toast.danger(errorMessage(reason, 'The PDF could not be created. Please try again.'));
    } finally { setExporting(false); }
  }

  async function refreshStatistics() {
    if (refreshingStats || loading) return;
    setRefreshingStats(true);
    try {
      await refreshStats();
      forceOverview.current = true;
      reload();
    } catch (reason) {
      toast.danger(errorMessage(reason, 'Statistics could not be refreshed.'));
    } finally { setRefreshingStats(false); }
  }

  function jumpToLatest() {
    if (!data?.latest_data_date) return;
    setPeriod({ preset: 'custom', from: shiftDay(data.latest_data_date, -29), to: data.latest_data_date });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${greeting()}, ${first}`}
        description={stats
          ? <>Saved statistics: <strong className="font-semibold text-foreground">{countLabel(stats.buyers_ready, 'buyer')}</strong> and <strong className="font-semibold text-foreground">{countLabel(stats.listings_ready, 'listing')}</strong> are ready.</>
          : 'Here is how your company is doing.'}
        actions={(
          <>
            <Button variant="secondary" isPending={refreshingStats || loading} isDisabled={!company} onPress={refreshStatistics}><RefreshCw className="size-4" aria-hidden="true" />Refresh statistics</Button>
            <Button variant="secondary" isPending={exporting} isDisabled={!company} onPress={exportPdf}><FileDown className="size-4" aria-hidden="true" />Export PDF</Button>
            <Button variant={canUpload ? 'secondary' : 'primary'} onPress={() => navigate?.(ROUTES.match)}><Target className="size-4" aria-hidden="true" />Start matching</Button>
            {canUpload && <Button onPress={() => navigate?.(ROUTES.upload)}><UploadCloud className="size-4" aria-hidden="true" />Upload chat</Button>}
          </>
        )}
      />

      <p className="text-caption text-muted">Statistics are saved daily. Use Refresh statistics to recalculate.{stats?.statistics_updated_at ? ` Last updated: ${new Date(stats.statistics_updated_at).toLocaleString('en-GB', { timeZone: 'Asia/Jakarta' })} WIB.` : ''}</p>
      <Panel bodyClassName="space-y-3">
        <FilterGroup label="Period" hint={data
          ? <>{data.period.label}: <strong className="font-semibold text-foreground">{prettyRange(data.period.date_from, data.period.date_to)}</strong> · compared with {prettyRange(data.period.compare_from, data.period.compare_to)}</>
          : undefined}>
          <PeriodPicker value={period} presets={dashboardPresets()} onChange={setPeriod} max={todayWib()} label="Period" />
        </FilterGroup>
      </Panel>

      <ErrorNotice message={error} onRetry={reload} />
      {/* Data older than the analysis window (last months by default) is stored but not analysed yet. */}
      <AnalysisWindowNotice periodStart={data?.period.date_from ?? period.from} />

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <StatCard label="Buyer requests" value={data?.kpi.buyers ?? 0} icon={<Users />} tone="indigo" change={data ? data.kpi.buyers_change : undefined} loading={loading && !data} hint="The same message counts once" />
        <StatCard label="New listings" value={data?.kpi.listings ?? 0} icon={<Boxes />} tone="cyan" change={data ? data.kpi.listings_change : undefined} loading={loading && !data} />
        <StatCard label="New matches" value={data?.kpi.matches ?? 0} icon={<Sparkles />} tone="hot" change={data ? data.kpi.matches_change : undefined} loading={loading && !data}
          hint={matchCounts ? <StatusCounts {...matchCounts} /> : undefined} onPress={() => navigate?.(ROUTES.recent)} />
        <StatCard label="Ready listings" value={data?.status.listing.ready ?? 0} icon={<TrendingUp />} tone="green" loading={loading && !data}
          hint={data ? `${number(data.status.listing.on_hold)} on hold · ${number(data.status.listing.sold)} sold` : undefined} />
      </div>

      {data && !empty && (
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-sm text-muted">
          <span className="font-semibold text-foreground">Unique buyer requests</span>
          <span>This week <strong className="font-semibold text-foreground tabular-nums">{number(data.quick.this_week)}</strong></span>
          <span aria-hidden="true">·</span>
          <span>This month <strong className="font-semibold text-foreground tabular-nums">{number(data.quick.this_month)}</strong></span>
        </p>
      )}

      {empty && (
        <Panel>
          <EmptyState title="No data in this period"
            description={data?.latest_data_date
              ? `Your latest data is from ${prettyRange(data.latest_data_date, data.latest_data_date)}. Pick another period or jump to the last 30 days of data.`
              : 'Upload a WhatsApp chat export to see your numbers here.'}
            action={data?.latest_data_date
              ? <Button onPress={jumpToLatest}>Show latest data</Button>
              : canUpload ? <Button onPress={() => navigate?.(ROUTES.upload)}><UploadCloud className="size-4" aria-hidden="true" />Upload chat</Button> : undefined} />
        </Panel>
      )}

      {data && data.insights.length > 0 && !empty && (
        <Panel title={<span className="flex items-center gap-2"><Lightbulb className="size-5 text-warning" aria-hidden="true" />Highlights</span>} description="Worked out from the data of this period.">
          <ul className="space-y-2.5">
            {data.insights.map((line) => (
              <li key={line} className="flex gap-2.5 text-body"><ChevronRight className="mt-1 size-4 shrink-0 text-accent" aria-hidden="true" />{line}</li>
            ))}
          </ul>
        </Panel>
      )}

      <RecentFeed key={scopeKey} onOpen={() => navigate?.(ROUTES.recent)} />

      {loading && !data && <div className="grid gap-6 lg:grid-cols-2">{[0, 1, 2, 3].map((n) => <SkeletonChart key={n} />)}</div>}

      {data && !empty && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel className="lg:col-span-2" title="New matches" description={`Pairs found from your uploads, per ${data.period.bucket === 'week' ? 'week' : 'day'}.`}>
            {data.kpi.matches ? (
              <>
                <BarsChart stacked height={240} ariaLabel="Stacked bar chart of new Hot, Warm and Custom matches" labels={trendLabels}
                  series={[
                    { label: 'Hot', data: data.trend.matches_hot, color: statusColors.hot },
                    { label: 'Warm', data: data.trend.matches.map((total, index) => Math.max(0, total - (data.trend.matches_hot[index] ?? 0) - (data.trend.matches_custom?.[index] ?? 0))), color: statusColors.warm },
                    { label: 'Custom', data: data.trend.matches_custom ?? data.trend.matches.map(() => 0), color: statusColors.custom },
                  ]} />
                {matchCounts && <div className="mt-3"><StatusCounts {...matchCounts} /></div>}
              </>
            ) : <EmptyState compact icon={<Sparkles />} title="No new matches in this period" description="New matches appear after you upload chats." />}
          </Panel>
          <Panel title="Buyer demand" description={`Unique buyer requests per ${data.period.bucket === 'week' ? 'week' : 'day'}.`}>
            <TrendChart labels={trendLabels} height={260} ariaLabel="Line chart of buyer requests over time" series={[{ label: 'Buyer requests', data: data.trend.buyers, color: colors.blue }]} />
          </Panel>

          <Panel title="New listings" description={`Unique listings posted per ${data.period.bucket === 'week' ? 'week' : 'day'}.`}>
            <TrendChart labels={trendLabels} height={260} ariaLabel="Line chart of new listings over time" series={[{ label: 'New listings', data: data.trend.listings, color: colors.cyan }]} />
          </Panel>

          <Panel title="Most wanted property types" description="Property types in the buyer requests of this period, and whether buyers want to buy or rent.">
            {data.categories.length ? (
              <div className="space-y-5">
                <RingChart caption="buyers" ariaLabel="Ring chart of the property types buyers ask for" items={data.categories.map((item) => ({ label: categoryLabel(item.key), value: item.value }))} />
                {data.transactions.length > 0 && <BuyRentSplit rows={data.transactions} />}
              </div>
            ) : <EmptyState compact title="No buyer requests yet" />}
          </Panel>

          <Panel title="Most wanted locations" description="Buyers asking for a location compared with the ready listings there.">
            {data.locations.length ? (
              <BarsChart horizontal height={Math.max(260, data.locations.length * 46)} ariaLabel="Bar chart of the most wanted locations compared with ready listings"
                labels={data.locations.map((item) => item.label)}
                series={[{ label: 'Buyers', data: data.locations.map((item) => item.value), color: colors.blue }, { label: 'Ready listings', data: data.locations.map((item) => item.stock), color: colors.cyan }]} />
            ) : <EmptyState compact title="No locations found yet" />}
          </Panel>

          <Panel title="Buyer budgets" description="Buyers by the budget they mention. Buy budgets are whole-property prices; rent budgets are per year (monthly rent counts as 12 months). Prices per m² are left out." bodyClassName="space-y-3">
            <Segmented label="Budget type" value={budgetKind} onChange={setBudgetKind} options={[{ id: 'sale', label: 'Buy' }, { id: 'rent', label: 'Rent' }]} />
            <BarsChart horizontal height={260} ariaLabel="Bar chart of buyer budgets" labels={budgetKind === 'rent' ? (data.budget.rent_labels ?? data.budget.labels) : data.budget.labels}
              series={[{ label: budgetKind === 'sale' ? 'Buyers (buy)' : 'Buyers (rent)', data: data.budget[budgetKind], color: budgetKind === 'sale' ? colors.blue : colors.teal }]} />
            {data.budget.skipped > 0 && <p className="text-caption text-muted">{countLabel(data.budget.skipped, 'buyer')} did not mention a budget that can be compared.</p>}
          </Panel>


          <Panel title="Opportunities" description="Wanted by buyers, but with fewer than three ready listings per buyer. Good places to look for new listings.">
            {data.gap_categories.length === 0 && data.gap_locations.length === 0 ? (
              <EmptyState compact icon={<Target />} title="Stock covers the demand" description="Every property type and location buyers asked for has enough ready listings." />
            ) : (
              <>
                <GapList title="Property types" rows={data.gap_categories.map((row) => ({ ...row, label: categoryLabel(row.key) }))} />
                <div className="my-5 h-px bg-separator" />
                <GapList title="Locations" rows={data.gap_locations} />
              </>
            )}
          </Panel>

          <Panel title="Listings per sales" description={data.top_sales.group_by === 'phone' ? 'Ready listings, grouped by the phone number in the message.' : 'Ready listings, grouped by who posted them.'}>
            {data.top_sales.rows.length ? (
              <BarsChart horizontal height={Math.max(260, data.top_sales.rows.length * 44)} ariaLabel="Bar chart of ready listings per sales"
                labels={data.top_sales.rows.map((row) => salesChartLabel(data.top_sales.group_by, row))}
                series={[{ label: 'Ready listings', data: data.top_sales.rows.map((row) => row.value), color: colors.navy }]} />
            ) : <EmptyState compact title="No listings yet" />}
          </Panel>

          <Panel title="Ready stock by property type" description="Every ready listing counted once, by property type.">
            {data.stock_categories?.length ? (
              <RingChart caption="ready listings" ariaLabel="Ring chart of ready listings per property type" items={data.stock_categories.map((item) => ({ label: categoryLabel(item.key), value: item.value }))} />
            ) : <EmptyState compact title="No ready listings yet" />}
          </Panel>

          {hasStatusMix && <Panel title="Stock status" description="How many are ready, on hold or sold.">
            <div className="grid gap-6 sm:grid-cols-2">
              <div>
                <p className="mb-3 text-sm font-semibold text-foreground">Listings</p>
                <RingChart caption="listings" ariaLabel="Ring chart of listing status" items={[
                  { label: 'Ready', value: data.status.listing.ready, color: colors.ready }, { label: 'On hold', value: data.status.listing.on_hold, color: colors.onHold }, { label: 'Sold', value: data.status.listing.sold, color: colors.sold }]} />
              </div>
              <div>
                <p className="mb-3 text-sm font-semibold text-foreground">Buyers</p>
                <RingChart caption="buyers" ariaLabel="Ring chart of buyer status" items={[
                  { label: 'Ready', value: data.status.buyer.ready, color: colors.ready }, { label: 'On hold', value: data.status.buyer.on_hold, color: colors.onHold }, { label: 'Closed', value: data.status.buyer.sold, color: colors.sold }]} />
              </div>
            </div>
          </Panel>}
        </div>
      )}

      <p className="text-center text-caption text-muted">Dates and times are in WIB. Weeks start on Monday.</p>
    </div>
  );
}

/** Buy vs rent share of the buyer requests, as one bar with both numbers. */
function BuyRentSplit({ rows }: { rows: { key: string; label: string; value: number }[] }) {
  const total = rows.reduce((sum, row) => sum + row.value, 0) || 1;
  const palette: Record<string, string> = { sale: colors.blue, rent: colors.teal, both: colors.violet, unknown: colors.slate };
  return (
    <div>
      <p className="mb-2 text-sm font-semibold text-foreground">Buy or rent</p>
      <div className="flex h-3 overflow-hidden rounded-full bg-default" role="presentation">
        {rows.map((row) => <div key={row.key} style={{ width: `${(row.value / total) * 100}%`, background: palette[row.key] ?? colors.slate }} />)}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {rows.map((row) => (
          <li key={row.key} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: palette[row.key] ?? colors.slate }} />
            <span className="font-medium">{row.label}</span>
            <span className="font-bold tabular-nums">{number(row.value)}</span>
            <span className="text-muted tabular-nums">{Math.round((row.value / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function GapList({ title, rows }: { title: string; rows: { key: string; label: string; buyers: number; listings: number; pressure: number }[] }) {
  if (!rows.length) return <div><p className="text-sm font-semibold text-foreground">{title}</p><p className="mt-1 text-sm text-muted">Not enough requests yet.</p></div>;
  const top = Math.max(...rows.map((row) => row.buyers), 1);
  return (
    <div>
      <p className="mb-3 text-sm font-semibold text-foreground">{title}</p>
      <ul className="space-y-3">
        {rows.slice(0, 5).map((row) => (
          <li key={row.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate font-medium text-foreground">{row.label}</span>
              <span className="shrink-0 text-muted tabular-nums">{countLabel(row.buyers, 'buyer')} · {countLabel(row.listings, 'listing')}</span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-default" role="presentation">
              <div className="h-full rounded-full" style={{ width: `${Math.max(6, (row.buyers / top) * 100)}%`, background: row.pressure >= 2 ? colors.red : row.pressure >= 1.2 ? colors.amber : colors.green }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The newest matches of the last 7 days. Each card opens Recent matches; the IDs stay copyable. */
function RecentFeed({ onOpen }: { onOpen: () => void }) {
  const api = useApi();
  const today = todayWib();
  const feed = useData((signal) => api.get<RecentResponse>(`/matches/recent?${query({ direction: 'buyer', date_from: shiftDay(today, -6), date_to: today, temps: 'hot,warm,custom', limit: 5, per_source: 1 })}`, signal), [today]);
  const groups = feed.data?.groups ?? [];
  if (!groups.length) return null;
  return (
    <Panel title="Latest matches" description="Your buyers and the listings that fit them, from the last 7 days."
      footer={<Button variant="tertiary" size="sm" onPress={onOpen}>See all recent matches<ArrowRight className="size-4" aria-hidden="true" /></Button>}>
      <ul className="space-y-3">
        {groups.map((group) => {
          const match = group.matches[0];
          if (!match) return null;
          const buyer = cleanName(group.source.contact_name) || 'Buyer';
          return (
            <li key={group.source.entity_id ?? group.source.id} className="xm-card-hover relative rounded-2xl border border-border bg-surface p-3.5 sm:p-4">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={match.temperature} size="sm" explain={false} />
                <span className="relative z-10 flex flex-wrap items-center gap-1.5">
                  <IdChip id={group.source.public_id} kind="buyer" />
                  <span aria-hidden="true" className="text-muted">⇄</span>
                  <IdChip id={match.target.public_id} kind="property" />
                </span>
                <span className="ml-auto text-caption text-muted">{relativeDate(match.found_at)}</span>
              </div>
              <button type="button" onClick={onOpen} className="mt-2 block w-full text-left text-sm leading-relaxed text-foreground after:absolute after:inset-0 after:rounded-2xl focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-accent/40">
                <span className="line-clamp-2"><strong className="font-semibold">{buyer}</strong> · {structuredSummary(match.target) || 'Listing details not stated'}</span>
                <span className="sr-only">. Open recent matches</span>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
