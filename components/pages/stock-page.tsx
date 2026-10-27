'use client';

import { useMemo, useState } from 'react';
import { Avatar, Button, Label, ListBox, Select, toast, useOverlayState } from '@heroui/react';
import { Boxes, CircleCheck, ClipboardList, Clock3, Download, Eye, History, PackageCheck, Pencil, PhoneCall, Plus, Tag, UploadCloud } from 'lucide-react';
import { BarsChart, Sparkline } from '@/components/charts/charts';
import { colors } from '@/components/charts/chart-setup';
import { EmptyState, ErrorNotice, FilterGroup, IconBadge, LoadingIndicator, PageHeader, Panel, SkeletonList, SkeletonStats, StatCard } from '@/components/app/primitives';
import { SalesListingsModal } from '@/components/app/sales-listings-modal';
import { TrackedEditor } from '@/components/app/tracked-editor';
import { PeriodPicker, periodPresets, type Period } from '@/components/app/period-picker';
import { errorMessage, query } from '@/lib/api';
import { STATUS_LABELS, cleanName, countLabel, dateTime, formatPhone, initials, number, relativeDate, relativeDateTime, todayWib } from '@/lib/format';
import type { Navigate } from '@/lib/router';
import { useCompany } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { StockLogRow, StockOverview, StockTracked } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

const EVENT = {
  import: { label: 'New upload', icon: UploadCloud, tone: 'blue' },
  status: { label: 'Listing status changed', icon: Tag, tone: 'amber' },
  tracking: { label: 'Tracking started', icon: Eye, tone: 'green' },
  manual: { label: 'Logged manually', icon: Pencil, tone: 'slate' },
} as const;

const LOG_PAGE = 30;
const logPresets = () => periodPresets(['all', 'last_7_days', 'last_30_days'], { labels: { all: 'All entries' } });
const salesName = (item: { label: string | null; contact_name: string | null; phone: string }) => item.label || cleanName(item.contact_name) || 'Sales';
/** "Budi Santoso" → "Budi-Santoso" for file names. */
const fileSafe = (text: string) => text.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '');

export default function StockPage(_props: { navigate?: Navigate }) {
  const api = useApi();
  const { company, refresh } = useCompany();
  const canEdit = !!company?.permissions.manage_settings;
  const [tick, setTick] = useState(0);
  const editor = useOverlayState();
  const listings = useOverlayState();
  const [viewing, setViewing] = useState<StockTracked | null>(null);
  const [phone, setPhone] = useState('');
  const [period, setPeriod] = useState<Period>({ preset: 'all', from: '', to: '' });
  const [limit, setLimit] = useState(LOG_PAGE);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);

  const overview = useData((signal) => api.get<StockOverview>('/stock/overview', signal), [tick]);
  const log = useData((signal) => api.get<{ rows: StockLogRow[]; has_more: boolean }>(`/stock/log?${query({ phone, date_from: period.from, date_to: period.to, limit })}`, signal), [phone, period.from, period.to, limit, tick]);

  const tracked = useMemo(() => overview.data?.tracked ?? [], [overview.data]);
  const totals = overview.data?.totals;
  const chartRows = useMemo(() => tracked.map((item) => ({ name: salesName(item).slice(0, 18), tail: item.phone.slice(-4), ...item.counts })), [tracked]);
  const today = todayWib();

  async function snapshot() {
    setSnapshotBusy(true);
    try {
      const result = await api.post<{ logged: number }>('/stock/snapshot');
      toast.success(`Stock logged for ${countLabel(result.logged, 'sales number')}`);
      setTick((value) => value + 1);
    } catch (reason) { toast.danger(errorMessage(reason, 'The stock could not be logged. Please try again.')); } finally { setSnapshotBusy(false); }
  }

  /** Excel of the listings: every tracked sales number, or only the one given. */
  async function download(item?: StockTracked) {
    setDownloading(item?.phone ?? 'all');
    try {
      const blob = await api.file(`/stock/export?${query({ phone: item?.phone })}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const who = item ? fileSafe(salesName(item)) || item.phone : 'All-Sales';
      link.href = url; link.download = `Sales-Listings-${who}-${today}.xlsx`; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast.success('Excel file downloaded');
    } catch (reason) {
      toast.danger(errorMessage(reason, 'The Excel file could not be created. Please try again.'));
    } finally { setDownloading(null); }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sales stock"
        description="How many listings each tracked sales number has, based on the phone number in their messages. Updated after every upload and status change."
        actions={<>
          {tracked.length > 0 && <Button variant="secondary" isPending={downloading === 'all'} isDisabled={!!downloading} onPress={() => download()}><Download className="size-4" aria-hidden="true" />Download all (Excel)</Button>}
          {canEdit && tracked.length > 0 && <Button variant="secondary" isPending={snapshotBusy} onPress={snapshot}><ClipboardList className="size-4" aria-hidden="true" />Log stock now</Button>}
          {canEdit && <Button onPress={() => editor.open()}><PhoneCall className="size-4" aria-hidden="true" />Manage sales numbers</Button>}
        </>}
      />

      <ErrorNotice message={overview.error} onRetry={overview.reload} />
      {overview.loading && !overview.data && <><SkeletonStats /><SkeletonList rows={2} /></>}

      {overview.data && tracked.length === 0 && (
        <Panel>
          <EmptyState icon={<PhoneCall />} title="No sales numbers tracked yet"
            description={canEdit ? 'Add the phone numbers of the sales people you want to follow. Their listings are counted from the phone number written in each message. This applies to the whole company.' : 'A company admin can add the sales numbers you want to follow.'}
            action={canEdit ? <Button onPress={() => editor.open()}><Plus className="size-4" aria-hidden="true" />Add sales numbers</Button> : undefined} />
        </Panel>
      )}

      {totals && tracked.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            <StatCard label="Tracked listings" value={totals.total} icon={<Boxes />} tone="indigo" hint={countLabel(tracked.length, 'sales number')} />
            <StatCard label={STATUS_LABELS.ready} value={totals.ready} icon={<CircleCheck />} tone="green" />
            <StatCard label={STATUS_LABELS.on_hold} value={totals.on_hold} icon={<Clock3 />} tone="amber" />
            <StatCard label={STATUS_LABELS.sold} value={totals.sold} icon={<PackageCheck />} tone="blue" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {tracked.map((item) => (
              <SalesCard key={item.phone} item={item} onOpen={() => { setViewing(item); listings.open(); }} onDownload={() => download(item)} downloading={downloading === item.phone} busy={!!downloading} />
            ))}
          </div>

          {tracked.length > 1 && (
            <Panel title="Stock per sales number" description="Ready, On hold and Sold listings for each number.">
              <BarsChart horizontal stacked height={Math.max(220, tracked.length * 58)} ariaLabel="Stacked bar chart of listings per sales number"
                labels={chartRows.map((row) => `${row.name} ···${row.tail}`)}
                series={[
                  { label: STATUS_LABELS.ready, data: chartRows.map((row) => row.ready), color: colors.green },
                  { label: STATUS_LABELS.on_hold, data: chartRows.map((row) => row.on_hold), color: colors.amber },
                  { label: STATUS_LABELS.sold, data: chartRows.map((row) => row.sold), color: colors.blue },
                ]} />
            </Panel>
          )}
        </>
      )}

      {tracked.length > 0 && (
        <Panel title={<span className="flex items-center gap-2"><History className="size-5 text-muted" aria-hidden="true" />Stock history</span>}
          description="Each entry shows how many listings that sales number had at that moment." bodyClassName="space-y-5"
          footer={<p className="text-caption text-muted">Dates and times are in WIB.</p>}>
          <div className="grid gap-5 md:grid-cols-[minmax(220px,300px)_1fr]">
            <Select value={phone || 'all'} onChange={(value) => { setPhone(value === 'all' ? '' : String(value)); setLimit(LOG_PAGE); }} fullWidth>
              <Label className="text-sm font-semibold">Sales number</Label>
              <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
              <Select.Popover>
                <ListBox>
                  <ListBox.Item id="all" textValue="All sales numbers">All sales numbers<ListBox.ItemIndicator /></ListBox.Item>
                  {tracked.map((item) => <ListBox.Item key={item.phone} id={item.phone} textValue={`${salesName(item)} ${item.phone}`}>{salesName(item)} · {formatPhone(item.phone)}<ListBox.ItemIndicator /></ListBox.Item>)}
                </ListBox>
              </Select.Popover>
            </Select>
            <FilterGroup label="Period">
              <PeriodPicker label="Period" value={period} onChange={(next) => { setPeriod(next); setLimit(LOG_PAGE); }} presets={logPresets()} max={today} />
            </FilterGroup>
          </div>
          <ErrorNotice message={log.error} onRetry={log.reload} />
          {log.loading && !log.data && <SkeletonList rows={3} />}
          {log.loading && log.data && <LoadingIndicator />}
          {!log.loading && log.data && log.data.rows.length === 0 && <EmptyState compact icon={<History />} title="No entries for this filter" description="Try another sales number or period." />}
          {!!log.data?.rows.length && (
            <ul className={cn('divide-y divide-separator rounded-2xl border border-border bg-surface transition-opacity', log.loading && 'opacity-60')}>
              {log.data.rows.map((row) => <LogRow key={row.id} row={row} name={salesName({ label: row.label, contact_name: tracked.find((item) => item.phone === row.phone)?.contact_name ?? null, phone: row.phone })} />)}
            </ul>
          )}
          {log.data?.has_more && <Button variant="secondary" fullWidth isDisabled={log.loading} onPress={() => setLimit((value) => value + LOG_PAGE)}>Show more</Button>}
        </Panel>
      )}

      <SalesListingsModal state={listings} phone={viewing?.phone ?? ''} name={viewing ? salesName(viewing) : ''} />
      <TrackedEditor state={editor} current={tracked} onSaved={() => { setTick((value) => value + 1); refresh(); }} />
    </div>
  );
}

function Delta({ value }: { value: number }) {
  if (!value) return null;
  return (
    <span className={cn('rounded-full px-1.5 py-px text-xs font-semibold tabular-nums', value > 0 ? 'bg-success-soft text-success-soft-foreground' : 'bg-danger-soft text-danger-soft-foreground')}>
      {value > 0 ? '+' : '−'}{number(Math.abs(value))}<span className="sr-only"> since the previous entry</span>
    </span>
  );
}

function SalesCard({ item, onOpen, onDownload, downloading, busy }: { item: StockTracked; onOpen: () => void; onDownload: () => void; downloading: boolean; busy: boolean }) {
  const name = salesName(item);
  const history = item.history.map((point) => point.ready);
  return (
    <article className="xm-card flex h-full flex-col p-4 sm:p-5">
      <div className="flex items-center gap-3">
        <Avatar color="accent" variant="soft" size="md"><Avatar.Fallback>{initials(name)}</Avatar.Fallback></Avatar>
        <div className="min-w-0 flex-1">
          <p className="truncate text-body font-semibold text-foreground">{name}</p>
          <p className="text-sm text-muted tabular-nums">{formatPhone(item.phone)}</p>
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
        {([['ready', item.counts.ready, 'bg-success-soft text-success-soft-foreground'], ['on_hold', item.counts.on_hold, 'bg-warning-soft text-warning-soft-foreground'], ['sold', item.counts.sold, 'bg-accent-soft text-accent-soft-foreground']] as const).map(([key, value, tone]) => (
          <div key={key} className={cn('flex flex-col-reverse rounded-xl px-2 py-3', tone)}>
            <dt className="mt-1 text-caption font-semibold">{STATUS_LABELS[key]}</dt>
            <dd className="text-2xl leading-none font-semibold tracking-tight tabular-nums">{number(value)}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm text-muted">
        {item.new_7d > 0 ? <><strong className="font-semibold text-foreground">+{number(item.new_7d)}</strong> new {item.new_7d === 1 ? 'listing' : 'listings'} in the last 7 days</> : 'No new listings in the last 7 days'}
        {item.last_posted_at ? ` · last posted ${relativeDate(item.last_posted_at).toLowerCase()}` : ''}
      </p>
      <div className="mt-3 flex-1">
        <p className="mb-1 text-caption font-medium text-muted">Ready listings over time</p>
        <Sparkline values={history} className="h-14" color={colors.green} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Button variant="secondary" fullWidth onPress={onOpen}>View listings</Button>
        <Button variant="secondary" fullWidth isPending={downloading} isDisabled={busy} onPress={onDownload}><Download className="size-4" aria-hidden="true" />Excel</Button>
      </div>
    </article>
  );
}

function LogRow({ row, name }: { row: StockLogRow; name: string }) {
  const meta = EVENT[row.event_type] ?? EVENT.manual;
  return (
    <li className="flex gap-3 p-3.5 sm:p-4">
      <IconBadge icon={<meta.icon />} tone={meta.tone} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <p className="text-sm font-semibold text-foreground">{meta.label}{row.event_type === 'import' && row.agent_name ? <span className="font-normal text-muted"> · {row.agent_name}</span> : null}</p>
          <time className="text-caption text-muted" dateTime={row.logged_at} title={dateTime(row.logged_at)}>{relativeDateTime(row.logged_at)}</time>
        </div>
        <p className="text-caption text-muted">{name} · {formatPhone(row.phone)}</p>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span>{STATUS_LABELS.ready} <strong className="font-semibold tabular-nums">{number(row.ready)}</strong> <Delta value={row.delta_ready} /></span>
          <span>{STATUS_LABELS.on_hold} <strong className="font-semibold tabular-nums">{number(row.on_hold)}</strong></span>
          <span>{STATUS_LABELS.sold} <strong className="font-semibold tabular-nums">{number(row.sold)}</strong></span>
          <span className="text-muted">Total <span className="tabular-nums">{number(row.total)}</span> <Delta value={row.delta_total} /></span>
        </p>
      </div>
    </li>
  );
}
