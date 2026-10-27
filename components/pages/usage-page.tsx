'use client';

import { useRef, useState, type ReactNode } from 'react';
import { RangeButton } from '@/components/app/period-picker';
import { todayWib } from '@/lib/format';
import { Button, Chip, Input, Label, ListBox, Select, TextField } from '@heroui/react';
import { Activity, Bot, Coins, Download, LogIn, RefreshCw, ScrollText, Upload } from 'lucide-react';
import { categorical } from '@/components/charts/chart-setup';
import {
  EmptyState, ErrorNotice, FilterGroup, PageHeader, PagerBar, Panel, Segmented, SkeletonList, SkeletonStats, StatCard,
} from '@/components/app/primitives';
import { COMPANY_STATUS_LABELS, companyStatusColor, countLabel, dateTime, number, relativeDateTime } from '@/lib/format';
import { usd } from '@/lib/jev';
import { ROUTE_PARAMS, useRoute, type Navigate } from '@/lib/router';
import { useData, useDebounced } from '@/lib/use-data';
import type { ActivityLog, ActivityRow, CompanyUsage, UsageMonthly, UsageReport } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

type Period = '7' | '30' | '90';
type LogPeriod = Period | 'all' | 'custom';
const PERIODS: { id: Period; label: string }[] = [{ id: '7', label: '7 days' }, { id: '30', label: '30 days' }, { id: '90', label: '90 days' }];
const LOG_PERIODS: { id: LogPeriod; label: string }[] = [...PERIODS, { id: 'all', label: 'All time' }, { id: 'custom', label: 'Custom' }];
const LOG_PAGE = 50;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "$1.25" / "$0.0042" without the rupiah conversion, for dense cells and chart labels. */
const dollars = (value: number) => (value === 0 ? '$0' : value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`);
/** "2026-10" → "Oct 2026". */
const monthLabel = (month: string) => { const m = Number(month.slice(5, 7)); return m >= 1 && m <= 12 ? `${MONTHS[m - 1]} ${month.slice(0, 4)}` : month; };
const byCost = (a: CompanyUsage, b: CompanyUsage) => b.cost_period - a.cost_period || a.name.localeCompare(b.name);

/** Platform administrator only: what every company costs and does, plus the activity log. */
export default function UsagePage({ params }: { params?: URLSearchParams; navigate?: Navigate } = {}) {
  const route = useRoute();
  const search = params ?? route.params;
  const api = useApi();
  const [days, setDays] = useState<Period>('30');
  const usage = useData((signal) => api.get<UsageReport>(`/admin/usage?days=${days}`, signal), [days]);
  const monthly = useData((signal) => api.get<UsageMonthly>('/admin/usage/monthly?months=6', signal), []);
  const logRef = useRef<HTMLDivElement>(null);

  // The company filter of the log can be set from the hash (#/penggunaan?company=…) and by "Show logs" in the table.
  const companyParam = search.get(ROUTE_PARAMS.company) ?? '';
  const [seenParam, setSeenParam] = useState(companyParam);
  const [company, setCompany] = useState(companyParam);
  if (companyParam !== seenParam) { setSeenParam(companyParam); setCompany(companyParam); }

  const companies = [...(usage.data?.companies ?? [])].sort(byCost);
  const totals = usage.data?.totals;

  function showLogs(id: string) {
    setCompany(id);
    logRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Usage & logs" description="AI spend, activity and sign-ins per company, and everything the platform recorded."
        actions={<Segmented<Period> label="Period" value={days} onChange={setDays} options={PERIODS} />} />

      <ErrorNotice message={usage.error} onRetry={usage.reload} />
      {usage.loading && !usage.data ? <SkeletonStats count={4} /> : totals && (
        <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <StatCard label={`AI cost, last ${days} days`} value={usd(totals.cost_period)} icon={<Coins />} tone="amber" hint={`All time ${usd(totals.cost_total)}`} className="col-span-2 lg:col-span-2" />
          <StatCard label="Tokens in" value={totals.tokens_in_period} icon={<Bot />} tone="violet" hint={`All time ${number(totals.tokens_in_total)}`} />
          <StatCard label="Tokens out" value={totals.tokens_out_period} icon={<Bot />} tone="indigo" hint={`All time ${number(totals.tokens_out_total)}`} />
          <StatCard label="AI reviews" value={totals.reviews_period} icon={<Activity />} tone="cyan" hint={`All time ${number(totals.reviews_total)}`} />
          <StatCard label="Uploads" value={totals.imports_period} icon={<Upload />} tone="blue" />
          <StatCard label="Exports" value={totals.exports_period} icon={<Download />} tone="green" />
          <StatCard label="Sign-ins" value={totals.logins_period} icon={<LogIn />} tone="slate" />
        </div>
      )}

      <Panel title="Per company" description={`Sorted by AI cost in the last ${days} days. Costs are US dollars.`} flush>
        {usage.loading && !usage.data && <div className="p-4 sm:p-6"><SkeletonList rows={3} /></div>}
        {usage.data && companies.length === 0 && <EmptyState compact title="No companies yet" description="Usage appears here once a company exists." />}
        {companies.length > 0 && <CompanyTable companies={companies} days={days} onLogs={showLogs} />}
      </Panel>

      <Panel title="AI cost per month" description="The last 6 months, per company.">
        <ErrorNotice message={monthly.error} onRetry={monthly.reload} />
        {monthly.loading && !monthly.data && <SkeletonList rows={3} />}
        {monthly.data && <MonthlyChart data={monthly.data} />}
      </Panel>

      <div ref={logRef} className="scroll-mt-20">
        <ActivityLogPanel companies={companies} company={company} onCompanyChange={setCompany} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ per company */

function CompanyTable({ companies, days, onLogs }: { companies: CompanyUsage[]; days: Period; onLogs: (id: string) => void }) {
  const head = 'px-3 py-2.5 font-semibold whitespace-nowrap';
  const cell = 'px-3 py-3 align-top tabular-nums';
  return (
    <>
      {/* Phones: one card per company. */}
      <ul className="divide-y divide-separator md:hidden">
        {companies.map((row) => (
          <li key={row.company_id} className="space-y-3 px-4 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 text-body font-semibold break-words text-foreground">{row.name}</span>
              <Chip size="sm" variant="soft" color={companyStatusColor(row.status)}>{COMPANY_STATUS_LABELS[row.status]}</Chip>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <Item label={`Cost, ${days} days`}><Cost value={row.cost_period} estimated={row.cost_estimated} /></Item>
              <Item label="Cost, all time"><Cost value={row.cost_total} estimated={row.cost_estimated} /></Item>
              <Item label="Members">{number(row.members)}</Item>
              <Item label="Buyers / listings">{number(row.buyers)} / {number(row.listings)}</Item>
              <Item label="Uploads">{number(row.imports_period)}</Item>
              <Item label="Exports">{number(row.exports_period)}</Item>
              <Item label="Sign-ins">{number(row.logins_period)}</Item>
              <Item label="AI reviews">{number(row.reviews_period)}</Item>
              <Item label="Tokens in / out">{number(row.tokens_in_period)} / {number(row.tokens_out_period)}</Item>
              <Item label="Last sign-in">{relativeDateTime(row.last_login_at)}</Item>
            </dl>
            <Button size="sm" variant="secondary" onPress={() => onLogs(row.company_id)}><ScrollText className="size-4" aria-hidden="true" />Show logs</Button>
          </li>
        ))}
      </ul>
      {/* Larger screens: a table that scrolls sideways when it must. */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[64rem] text-left text-sm">
          <thead className="bg-surface-secondary text-caption text-muted">
            <tr>
              <th scope="col" className={head}>Company</th>
              <th scope="col" className={cn(head, 'text-right')}>Members</th>
              <th scope="col" className={cn(head, 'text-right')}>Buyers / listings</th>
              <th scope="col" className={cn(head, 'text-right')}>Uploads</th>
              <th scope="col" className={cn(head, 'text-right')}>Exports</th>
              <th scope="col" className={cn(head, 'text-right')}>Sign-ins</th>
              <th scope="col" className={cn(head, 'text-right')}>AI reviews</th>
              <th scope="col" className={cn(head, 'text-right')}>Tokens in / out</th>
              <th scope="col" className={cn(head, 'text-right')}>Cost, {days} days</th>
              <th scope="col" className={cn(head, 'text-right')}>Cost, all time</th>
              <th scope="col" className={head}>Last sign-in</th>
              <th scope="col" className={head}><span className="sr-only">Logs</span></th>
            </tr>
          </thead>
          <tbody>
            {companies.map((row) => (
              <tr key={row.company_id} className="border-t border-separator">
                <td className={cn(cell, 'min-w-[14rem]')}>
                  <span className="block font-semibold break-words text-foreground">{row.name}</span>
                  <Chip size="sm" variant="soft" color={companyStatusColor(row.status)} className="mt-1">{COMPANY_STATUS_LABELS[row.status]}</Chip>
                </td>
                <td className={cn(cell, 'text-right')}>{number(row.members)}</td>
                <td className={cn(cell, 'text-right whitespace-nowrap')}>{number(row.buyers)} / {number(row.listings)}</td>
                <td className={cn(cell, 'text-right')}>{number(row.imports_period)}</td>
                <td className={cn(cell, 'text-right')}>{number(row.exports_period)}</td>
                <td className={cn(cell, 'text-right')}>{number(row.logins_period)}</td>
                <td className={cn(cell, 'text-right')}>{number(row.reviews_period)}</td>
                <td className={cn(cell, 'text-right whitespace-nowrap')}>{number(row.tokens_in_period)} / {number(row.tokens_out_period)}</td>
                <td className={cn(cell, 'text-right whitespace-nowrap font-semibold text-foreground')}><Cost value={row.cost_period} estimated={row.cost_estimated} /></td>
                <td className={cn(cell, 'text-right whitespace-nowrap')}><Cost value={row.cost_total} estimated={row.cost_estimated} /></td>
                <td className={cn(cell, 'whitespace-nowrap')}>{relativeDateTime(row.last_login_at)}</td>
                <td className={cn(cell, 'whitespace-nowrap')}>
                  <Button size="sm" variant="tertiary" onPress={() => onLogs(row.company_id)}><ScrollText className="size-4" aria-hidden="true" />Show logs</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-muted">{label}</dt>
      <dd className="mt-0.5 font-medium break-words text-foreground tabular-nums">{children}</dd>
    </div>
  );
}

/** A dollar amount with the full "$ (≈ Rp)" text on hover; estimated costs get a small marker. */
function Cost({ value, estimated }: { value: number; estimated?: boolean }) {
  return <span title={`${usd(value)}${estimated ? ' (estimated from token counts)' : ''}`}>{dollars(value)}{estimated && value > 0 && <span className="ml-1 text-caption font-normal text-muted">est.</span>}</span>;
}

/* ------------------------------------------------------------------ monthly chart */

const MAX_SERIES = 6;

/** Stacked horizontal bars, one per month, without Chart.js: costs are cents, which its integer axes would hide. */
function MonthlyChart({ data }: { data: UsageMonthly }) {
  const months = [...new Set(data.rows.map((row) => row.month))].sort();
  const perCompany = new Map<string, { name: string; total: number }>();
  for (const row of data.rows) {
    const entry = perCompany.get(row.company_id) ?? { name: row.name, total: 0 };
    entry.total += row.cost;
    perCompany.set(row.company_id, entry);
  }
  const ranked = [...perCompany.entries()].sort((a, b) => b[1].total - a[1].total);
  const series = ranked.slice(0, MAX_SERIES).map(([id, entry], index) => ({ id, name: entry.name, color: categorical[index % categorical.length] }));
  const others = new Set(ranked.slice(MAX_SERIES).map(([id]) => id));
  if (others.size) series.push({ id: '__other', name: `Other (${countLabel(others.size, 'company', 'companies')})`, color: categorical[categorical.length - 1] });
  const bars = months.map((month) => {
    const values = new Map<string, number>();
    for (const row of data.rows) {
      if (row.month !== month) continue;
      const key = others.has(row.company_id) ? '__other' : row.company_id;
      values.set(key, (values.get(key) ?? 0) + row.cost);
    }
    return { month, values, total: [...values.values()].reduce((sum, value) => sum + value, 0) };
  });
  const max = Math.max(0, ...bars.map((bar) => bar.total));

  if (!data.rows.length || max === 0) return <EmptyState compact icon={<Coins />} title="No AI cost yet" description="Costs appear here once a company runs AI reviews." />;
  return (
    <div className="space-y-4">
      <ol className="space-y-2.5" aria-label="AI cost per month">
        {bars.map((bar) => (
          <li key={bar.month} className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-3 text-sm">
            <span className="font-medium text-muted">{monthLabel(bar.month)}</span>
            <span className="flex h-6 overflow-hidden rounded-lg bg-surface-secondary">
              {series.map((item) => {
                const value = bar.values.get(item.id) ?? 0;
                if (!value) return null;
                return <span key={item.id} title={`${item.name}: ${dollars(value)}`} className="h-full" style={{ width: `${(value / max) * 100}%`, background: item.color }} />;
              })}
            </span>
            <span className="w-16 text-right font-semibold text-foreground tabular-nums" title={usd(bar.total)}>{dollars(bar.total)}</span>
          </li>
        ))}
      </ol>
      <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm" aria-label="Companies">
        {series.map((item) => (
          <li key={item.id} className="flex items-center gap-2">
            <span aria-hidden="true" className="size-3 shrink-0 rounded-full" style={{ background: item.color }} />
            <span className="min-w-0 font-medium">{item.name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ activity log */

function ActivityLogPanel({ companies, company, onCompanyChange }: { companies: CompanyUsage[]; company: string; onCompanyChange: (id: string) => void }) {
  const api = useApi();
  const [action, setAction] = useState('');
  const [userText, setUserText] = useState('');
  const user = useDebounced(userText.trim(), 400);
  const [period, setPeriod] = useState<LogPeriod>('30');
  const [range, setRange] = useState({ from: '', to: '' });
  const [offset, setOffset] = useState(0);
  // Any filter change starts from the first page again.
  const filterKey = `${company}|${action}|${user}|${period}|${range.from}|${range.to}`;
  const [seenKey, setSeenKey] = useState(filterKey);
  if (filterKey !== seenKey) { setSeenKey(filterKey); setOffset(0); }

  const query = new URLSearchParams({ limit: String(LOG_PAGE), offset: String(offset) });
  if (company) query.set('company_id', company);
  if (action) query.set('action', action);
  if (user) query.set('user', user);
  if (period === 'custom') { if (range.from) query.set('from', range.from); if (range.to) query.set('to', range.to); }
  else if (period !== 'all') query.set('days', period);
  const logs = useData((signal) => api.get<ActivityLog>(`/admin/logs?${query.toString()}`, signal), [company, action, user, period, range.from, range.to, offset]);

  const rows = logs.data?.rows ?? [];
  const total = logs.data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + rows.length, total);
  // A company not in the usage list (deleted, or only in the hash) still shows as a choice so the filter stays visible.
  const choices = companies.some((item) => item.company_id === company) || !company ? companies : [...companies, { company_id: company, name: company } as CompanyUsage];

  return (
    <Panel title="Activity log" description="Everything the platform recorded: sign-ins, uploads, exports, AI reviews and changes. Times are in WIB."
      actions={<Button size="sm" variant="secondary" isPending={logs.loading && !!logs.data} onPress={logs.reload}><RefreshCw className="size-4" aria-hidden="true" />Refresh</Button>}>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_auto]">
        <Select value={company || 'all'} onChange={(value) => onCompanyChange(value === null || value === 'all' ? '' : String(value))} fullWidth>
          <Label className="text-sm font-semibold">Company</Label>
          <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover>
            <ListBox>
              <ListBox.Item id="all" textValue="All companies">All companies<ListBox.ItemIndicator /></ListBox.Item>
              {choices.map((item) => <ListBox.Item key={item.company_id} id={item.company_id} textValue={item.name}>{item.name}<ListBox.ItemIndicator /></ListBox.Item>)}
            </ListBox>
          </Select.Popover>
        </Select>
        <Select value={action || 'all'} onChange={(value) => setAction(value === null || value === 'all' ? '' : String(value))} fullWidth>
          <Label className="text-sm font-semibold">Action</Label>
          <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover>
            <ListBox>
              <ListBox.Item id="all" textValue="All actions">All actions<ListBox.ItemIndicator /></ListBox.Item>
              {(logs.data?.actions ?? []).map((item) => <ListBox.Item key={item.id} id={item.id} textValue={item.label}>{item.label}<ListBox.ItemIndicator /></ListBox.Item>)}
            </ListBox>
          </Select.Popover>
        </Select>
        <TextField value={userText} onChange={setUserText} fullWidth>
          <Label className="text-sm font-semibold">User email</Label>
          <Input placeholder="Part of an email address" autoComplete="off" />
        </TextField>
        <FilterGroup label="Period">
          <div className="flex flex-wrap items-center gap-2">
            <Segmented<LogPeriod> label="Log period" value={period} onChange={(next) => { setPeriod(next); if (next !== 'custom') setRange({ from: '', to: '' }); }} options={LOG_PERIODS} />
            {period === 'custom' && <RangeButton value={range} active={!!range.from} max={todayWib()} onChange={(next) => setRange(next)} label="Pick dates" />}
          </div>
        </FilterGroup>
      </div>

      <div className="mt-5 space-y-3">
        <ErrorNotice message={logs.error} onRetry={logs.reload} />
        {logs.loading && !logs.data && <SkeletonList rows={4} />}
        {logs.data && rows.length === 0 && <EmptyState compact icon={<ScrollText />} title="Nothing recorded" description="No activity matches these filters." />}
        {rows.length > 0 && (
          <ol className={cn('divide-y divide-separator overflow-hidden rounded-2xl border border-border', logs.loading && 'opacity-60')} aria-busy={logs.loading}>
            {rows.map((row) => <LogRow key={row.id} row={row} />)}
          </ol>
        )}
        {total > LOG_PAGE && (
          <PagerBar from={from} to={to} total={total} hasPrev={offset > 0} hasNext={to < total} loading={logs.loading}
            onPrev={() => setOffset(Math.max(0, offset - LOG_PAGE))} onNext={() => setOffset(offset + LOG_PAGE)} />
        )}
      </div>
    </Panel>
  );
}

function LogRow({ row }: { row: ActivityRow }) {
  const failed = row.status !== null && row.status >= 400;
  const actingFor = typeof row.detail?.acting_for === 'string' && row.detail.acting_for;
  return (
    <li className="grid gap-x-4 gap-y-1 px-4 py-3 text-sm md:grid-cols-[10.5rem_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.3fr)]">
      <time dateTime={row.created_at} title={dateTime(row.created_at)} className="text-muted tabular-nums">{relativeDateTime(row.created_at)}</time>
      <span className="min-w-0 font-medium break-words text-foreground">{row.company_name ?? <span className="text-muted">Platform</span>}</span>
      <span className="min-w-0">
        <span className="block font-medium break-words text-foreground">{row.user_name ?? row.user_email ?? <span className="text-muted">Unknown user</span>}</span>
        {row.user_email && row.user_name && row.user_name !== row.user_email && <span className="block break-all text-caption text-muted">{row.user_email}</span>}
      </span>
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-semibold text-foreground">{row.label}</span>
          {actingFor && <Chip size="sm" variant="soft" color="warning">by platform admin</Chip>}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-caption text-muted">
          {(row.method || row.path) && <span className="font-mono break-all">{[row.method, row.path].filter(Boolean).join(' ')}</span>}
          {row.status !== null && <span className={cn('font-semibold tabular-nums', failed ? 'text-danger' : 'text-muted')}>{row.status}</span>}
          {row.duration_ms !== null && <span className="tabular-nums">{number(row.duration_ms)} ms</span>}
          {row.ip && <span>{row.ip}</span>}
        </span>
      </span>
    </li>
  );
}
