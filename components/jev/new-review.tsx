'use client';

import { useMemo, useState } from 'react';
import { Button, Input, Label, ListBox, SearchField, Select, TextField, toast } from '@heroui/react';
import { Bot, KeyRound, Play, UsersRound } from 'lucide-react';
import {
  Collapsible, EmptyState, ErrorNotice, FilterGroup, LoadingIndicator, Notice, Panel, Segmented, StatusBadge, StatusFilter,
} from '@/components/app/primitives';
import { PeriodPicker, periodFor, periodPresets, type Period } from '@/components/app/period-picker';
import { PromptPicker } from '@/components/jev/prompt-picker';
import { ModelPicker } from '@/components/jev/model-picker';
import { errorMessage } from '@/lib/api';
import { countLabel, number } from '@/lib/format';
import { usd } from '@/lib/jev';
import type { AiKey, AiModels, ExportPlan, JevDirections, JevEstimate, JevRun, JevScope, MatchStatus, Temperature } from '@/lib/types';
import { useData, useDebounced } from '@/lib/use-data';
import { cn } from '@/lib/utils';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/workspace-context';

const STATUSES: readonly Temperature[] = ['hot', 'warm', 'custom'];
const presets = () => periodPresets(['all', 'today', 'yesterday', 'today_yesterday', 'week', 'month', 'last_month', 'last_1_month', 'last_3_months']);
const DIRECTION_OPTIONS: { id: JevDirections; label: string }[] = [
  { id: 'both', label: 'Both directions' }, { id: 'property', label: 'Listing → Buyer' }, { id: 'buyer', label: 'Buyer → Listing' },
];
const ROWS = 100;

/** Choose model, key and scope; see what it costs; start a review run. */
export function NewReview({ models, modelsError, modelsLoading, reloadModels, keys, onStarted, onOpenKeys }: {
  models?: AiModels; modelsError?: string; modelsLoading?: boolean; reloadModels: () => void;
  keys: AiKey[]; onStarted: (run: JevRun) => void; onOpenKeys: () => void;
}) {
  const api = useApi();
  const { user } = useSession();
  const technical = user.role === 'admin';
  const [modelChoice, setModel] = useState<string | null>(null);
  const model = modelChoice ?? models?.default_model ?? 'typesafe/jev-1.13';
  const [keyChoice, setKey] = useState<string | null>(null);
  const keyId = keyChoice && keys.some((key) => key.id === keyChoice) ? keyChoice : keys.find((key) => key.is_default)?.id ?? keys[0]?.id ?? '';
  const [source, setSource] = useState<'recent' | 'match'>('recent');
  const [foundPeriod, setFoundPeriod] = useState<Period>(() => periodFor('today'));
  const [customPrompt, setCustomPrompt] = useState('');
  const [directions, setDirections] = useState<JevDirections>('both');
  const [buyerPeriod, setBuyerPeriod] = useState<Period>(() => periodFor('today_yesterday'));
  const [listingPeriod, setListingPeriod] = useState<Period>(() => periodFor('last_3_months'));
  const [statuses, setStatuses] = useState<Temperature[]>([...STATUSES]);
  const [maxPairs, setMaxPairs] = useState('150');
  // null = every sender / sales contact; a set = only these raw group keys.
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');

  const limit = Number(maxPairs);
  const limitOk = Number.isInteger(limit) && limit >= 1 && limit <= 2000;
  const periods = useMemo(() => ({
    recent: source === 'recent', found_from: foundPeriod.from, found_to: foundPeriod.to,
    buyer_date_from: buyerPeriod.from, buyer_date_to: buyerPeriod.to, listing_date_from: listingPeriod.from, listing_date_to: listingPeriod.to,
  }), [source, foundPeriod, buyerPeriod, listingPeriod]);

  // The same people list as "Export both directions" (the company's sender / sales contact grouping).
  const plan = useData(async (signal) => {
    const load = (direction: 'buyer' | 'property') => api.post<ExportPlan>('/export/all/plan', {
      direction, combined: false, statuses: statuses.join(','), ...periods,
    }, signal);
    const first = await load(directions === 'property' ? 'property' : 'buyer');
    if (directions !== 'both') return first;
    const second = await load('property');
    const merged = new Map(first.groups.map((group) => [group.key, { ...group }]));
    for (const group of second.groups) {
      const previous = merged.get(group.key);
      merged.set(group.key, previous ? { ...previous, hot: previous.hot + group.hot,
        warm: previous.warm + group.warm, custom: (previous.custom ?? 0) + (group.custom ?? 0),
        unmatched: previous.unmatched + group.unmatched, pages: previous.pages + group.pages,
        sources: previous.sources + group.sources } : group);
    }
    return { ...first, combined: true, groups: [...merged.values()] };
  }, [directions, statuses, periods]);
  const groups = useMemo(() => plan.data?.groups ?? [], [plan.data]);
  const groupKeys = useMemo(() => (picked ? groups.map((group) => group.key).filter((key) => picked.has(key)) : undefined), [picked, groups]);

  const scope: JevScope = useMemo(() => ({
    source, custom_prompt: customPrompt, model, key_id: technical ? keyId || undefined : undefined, directions, statuses, max_pairs: limitOk ? limit : 150,
    buyer_date_from: periods.buyer_date_from, buyer_date_to: periods.buyer_date_to,
    listing_date_from: periods.listing_date_from, listing_date_to: periods.listing_date_to,
    found_from: periods.found_from, found_to: periods.found_to,
    ...(groupKeys ? { group_keys: groupKeys } : {}),
  }), [source, customPrompt, model, keyId, technical, directions, statuses, limit, limitOk, periods, groupKeys]);
  const debounced = useDebounced(scope, 450);
  const estimate = useData((signal) => api.post<JevEstimate>('/ai/review-runs/estimate', { ...debounced, key_id: undefined }, signal),
    [debounced], !plan.loading && !plan.error && limitOk && !(groupKeys && groupKeys.length === 0));
  const stale = debounced !== scope;

  async function start() {
    if (starting || (technical && !keyId)) return;
    setStarting(true); setStartError('');
    try {
      const run = await api.post<JevRun>('/ai/review-runs', scope);
      toast.success('AI Review started', { description: `${countLabel(run.total, 'pair')} are being reviewed. You can leave this page.` });
      onStarted(run);
    } catch (reason) { setStartError(errorMessage(reason, 'The review could not be started. Please try again.')); } finally { setStarting(false); }
  }

  const data = estimate.data;
  const byPhone = (plan.data?.group_by ?? data?.group_by) === 'phone';
  const unit: [string, string] = byPhone ? ['sales contact', 'sales contacts'] : ['sender', 'senders'];
  const canStart = !plan.loading && !plan.error && (!technical || !!keyId) && limitOk && !!data && data.pairs > 0 && !stale && !estimate.loading && !(groupKeys && groupKeys.length === 0);

  return (
    <Panel title={<span className="flex items-center gap-2"><Bot className="size-5 text-accent" aria-hidden="true" />New review</span>}
      description="Jev reads each pair the matching rules found and says whether it is worth recommending. Nothing changes in your matches.">
      <div className="space-y-6 [&_.toggle-button]:min-w-0 [&_.toggle-button]:whitespace-normal [&_.toggle-button]:px-2 [&_.search-field__input]:min-w-0 [&_.search-field__input]:w-full">
        <FilterGroup label="Review source" hint="Recent Match uses the date a new pair was found during upload. Match uses the posting dates you choose.">
          <Segmented label="Review source" value={source} onChange={(next) => { setSource(next); setPicked(null); }} options={[{ id: 'recent', label: 'Recent Match' }, { id: 'match', label: 'Match · custom dates' }]} fullWidth />
        </FilterGroup>
        <PromptPicker value={customPrompt} onChange={setCustomPrompt} />
        <FilterGroup label="Model">
          <ModelPicker value={model} onChange={setModel} models={models} error={modelsError} loading={modelsLoading} onRefresh={reloadModels} />
        </FilterGroup>

        {technical && <FilterGroup label="API key" hint={keys.length ? undefined : 'Add an OpenRouter key in Settings → AI keys first.'}>
          {keys.length ? (
            <Select value={keyId || null} onChange={(next) => { if (next !== null) setKey(String(next)); }} aria-label="API key" fullWidth className="sm:max-w-md">
              <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
              <Select.Popover>
                <ListBox>
                  {keys.map((key) => (
                    <ListBox.Item key={key.id} id={key.id} textValue={`${key.label} ${key.masked}`}>
                      <span className="truncate">{key.label} <span className="font-mono text-muted">{key.masked}</span>{key.is_default ? <span className="text-muted"> · default</span> : null}</span>
                      <ListBox.ItemIndicator />
                    </ListBox.Item>
                  ))}
                </ListBox>
              </Select.Popover>
            </Select>
          ) : <Button variant="secondary" onPress={onOpenKeys}><KeyRound className="size-4" aria-hidden="true" />Add an API key</Button>}
        </FilterGroup>}

        <FilterGroup label="Directions" hint={directions === 'both' ? 'Pairs from the listings’ side and from the buyers’ side. The same two messages are only sent to Jev once.' : undefined}>
          <Segmented label="Directions" value={directions} onChange={(next) => { setDirections(next); setPicked(null); }} options={DIRECTION_OPTIONS} fullWidth />
        </FilterGroup>

        {source === 'recent' ? <FilterGroup label="New matches found" hint="The same import matches, company rules and active statuses as Recent Match. Message posting dates do not remove these pairs.">
          <PeriodPicker value={foundPeriod} onChange={(next) => { setFoundPeriod(next); setPicked(null); }} presets={periodPresets(['today', 'yesterday', 'today_yesterday', 'week', 'last_week', 'month'], { untilToday: true })} label="Match discovery dates" />
        </FilterGroup> : <div className="grid gap-5 lg:grid-cols-2">
          <FilterGroup label="Buyers posted">
            <PeriodPicker value={buyerPeriod} onChange={(next) => { setBuyerPeriod(next); setPicked(null); }} presets={presets()} label="Buyer posting dates" />
          </FilterGroup>
          <FilterGroup label="Listings posted">
            <PeriodPicker value={listingPeriod} onChange={(next) => { setListingPeriod(next); setPicked(null); }} presets={presets()} label="Listing posting dates" />
          </FilterGroup>
        </div>}

        <div className="grid gap-5 sm:grid-cols-[minmax(0,1fr)_auto]">
          <FilterGroup label="Pairs to review">
            <StatusFilter value={statuses} options={STATUSES} label="Pairs to review"
              onChange={(next: MatchStatus[]) => { setStatuses(next.filter((s): s is Temperature => s !== 'unmatched')); setPicked(null); }} />
          </FilterGroup>
          <TextField type="number" value={maxPairs} onChange={setMaxPairs} isInvalid={!limitOk} className="w-full sm:w-44">
            <Label className="text-sm font-semibold">Most pairs</Label>
            <Input min={1} max={2000} inputMode="numeric" />
            {!limitOk && <p className="mt-1 text-caption text-danger">1 to 2,000.</p>}
          </TextField>
        </div>

        <PeoplePicker groups={groups} loading={plan.loading} error={plan.error} onRetry={plan.reload} unit={unit} picked={picked} onPick={setPicked} />

        <EstimateBox estimate={data} loading={plan.loading || estimate.loading || stale} error={estimate.error} onRetry={estimate.reload} empty={!!groupKeys && groupKeys.length === 0} unit={unit} />

        <ErrorNotice message={startError} title="The review did not start" />
        <div className="flex flex-wrap items-center justify-end gap-3">
          {technical && !keyId && <p className="text-sm text-muted">An API key is needed to start.</p>}
          <Button size="lg" isPending={starting} isDisabled={!canStart} onPress={start}><Play className="size-4" aria-hidden="true" />
            {data && data.pairs ? `Start review of ${countLabel(data.pairs, 'pair')}` : 'Start review'}</Button>
        </div>
      </div>
    </Panel>
  );
}

function PeoplePicker({ groups, loading, error, onRetry, unit, picked, onPick }: {
  groups: ExportPlan['groups']; loading: boolean; error: string; onRetry: () => void; unit: [string, string];
  picked: Set<string> | null; onPick: (next: Set<string> | null) => void;
}) {
  const [search, setSearch] = useState('');
  const [shown, setShown] = useState(ROWS);
  const query = search.trim().toLowerCase();
  const filtered = useMemo(() => (query ? groups.filter((group) => `${group.title} ${group.key} ${group.phone ?? ''}`.toLowerCase().includes(query)) : groups), [groups, query]);
  const isOn = (key: string) => !picked || picked.has(key);
  const count = picked ? groups.filter((group) => picked.has(group.key)).length : groups.length;

  function toggle(key: string) {
    const next = new Set(picked ?? groups.map((group) => group.key));
    if (next.has(key)) next.delete(key); else next.add(key);
    onPick(next.size === groups.length ? null : next);
  }

  return (
    <Collapsible title={`Only some ${unit[1]} (optional)`} icon={<UsersRound />}
      description={loading && !groups.length ? `Loading ${unit[1]}…` : picked ? `${number(count)} of ${countLabel(groups.length, unit[0], unit[1])} selected` : `All ${countLabel(groups.length, unit[0], unit[1])}`}>
      <div className="space-y-3">
        <ErrorNotice message={error} onRetry={onRetry} />
        {!loading && !groups.length && !error && <p className="text-sm text-muted">No {unit[1]} have pairs with these filters.</p>}
        {groups.length > 0 && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-caption text-muted">The same people list as “Export both directions”.</p>
              <div className="flex gap-1">
                <Button size="sm" variant="tertiary" isDisabled={!picked} onPress={() => onPick(null)}>Select all</Button>
                <Button size="sm" variant="tertiary" isDisabled={count === 0} onPress={() => onPick(new Set())}>Clear</Button>
              </div>
            </div>
            {groups.length > 6 && (
              <SearchField value={search} onChange={(value) => { setSearch(value); setShown(ROWS); }} aria-label={`Search ${unit[1]}`}>
                <Label className="sr-only">Search {unit[1]}</Label>
                <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder="Search by name or phone" /><SearchField.ClearButton /></SearchField.Group>
              </SearchField>
            )}
            <ul className="max-h-80 divide-y divide-separator overflow-y-auto overscroll-contain rounded-2xl border border-border bg-surface">
              {filtered.slice(0, shown).map((group) => (
                <li key={group.key || '__none'}>
                  <label className={cn('grid min-h-12 cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 px-3 py-2.5 sm:px-4', isOn(group.key) ? 'bg-accent-soft/35' : 'hover:bg-surface-secondary/70')}>
                    <input type="checkbox" checked={isOn(group.key)} onChange={() => toggle(group.key)} className="row-span-2 mt-0.5 size-5 shrink-0 cursor-pointer accent-[var(--accent)]" />
                    <span className="truncate text-sm font-semibold text-foreground" title={group.title}>{group.title || group.key}</span>
                    <span className="mt-1 flex flex-wrap items-center gap-1.5">
                      {(['hot', 'warm', 'custom'] as const).map((status) => ((group[status] ?? 0) > 0
                        ? <StatusBadge key={status} status={status} count={group[status] ?? 0} size="sm" explain={false} showIcon={false} /> : null))}
                    </span>
                  </label>
                </li>
              ))}
              {filtered.length === 0 && <li className="px-4 py-5 text-center text-sm text-muted">No {unit[1]} match “{search.trim()}”.</li>}
              {filtered.length > shown && <li className="p-2"><Button size="sm" variant="secondary" fullWidth onPress={() => setShown((value) => value + ROWS)}>Show more</Button></li>}
            </ul>
          </>
        )}
      </div>
    </Collapsible>
  );
}

function EstimateBox({ estimate, loading, error, onRetry, empty, unit }: {
  estimate?: JevEstimate; loading: boolean; error: string; onRetry: () => void; empty: boolean; unit: [string, string];
}) {
  if (empty) return <Notice status="warning">Select at least one {unit[0]}, or select all.</Notice>;
  if (error) return <ErrorNotice message={error} onRetry={onRetry} title="The estimate could not be made" />;
  if (!estimate) return loading ? <LoadingIndicator message="Counting the pairs…" /> : null;
  if (!estimate.pairs) {
    return <EmptyState compact title="No pairs to review" description="Widen the posting dates, choose more statuses or more people." />;
  }
  return (
    <section aria-label="Estimate" aria-busy={loading} className={cn('space-y-3 rounded-2xl bg-surface-secondary p-4 transition-opacity', loading && 'opacity-60')}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-body font-semibold text-foreground">{countLabel(estimate.pairs, 'pair')} to review</span>
        {STATUSES.map((status) => (estimate.by_status[status] ? <StatusBadge key={status} status={status} count={estimate.by_status[status]} size="sm" explain={false} /> : null))}
      </div>
      {estimate.to_call === 0 ? (
        <p className="text-sm text-muted">Every pair was already reviewed with this model and prompt. Jev’s earlier answers are reused at no cost.</p>
      ) : (
        <p className="text-sm text-muted">
          Jev reads <strong className="font-semibold text-foreground">{countLabel(estimate.to_call, 'pair')}</strong>
          {estimate.unique_inputs < estimate.pairs ? <> ({number(estimate.pairs - estimate.unique_inputs)} share their two messages with another pair and are answered once)</> : null}
          {estimate.cached ? <>; {countLabel(estimate.cached, 'answer')} from earlier reviews with this model {estimate.cached === 1 ? 'is' : 'are'} reused for free</> : null}.
          {' '}About <strong className="font-semibold text-foreground tabular-nums">{number(estimate.tokens_in + estimate.tokens_out)} tokens</strong>
          {estimate.price_varies
            ? <> · <strong className="font-semibold text-foreground">price varies</strong> for this model; the real cost from OpenRouter shows while the review runs.</>
            : <> · about <strong className="font-semibold text-foreground tabular-nums">{usd(estimate.cost)}</strong>.</>}
        </p>
      )}
      {estimate.capped && (
        <p className="text-caption text-muted">Only the first {number(estimate.pairs)} of {countLabel(estimate.available, 'pair')}: Hot first, then Warm, then Custom, newest messages first. Raise “Most pairs” to review more.</p>
      )}
      {estimate.scan_truncated && <p className="text-caption text-warning-soft-foreground">Very many pairs match: only the newest were counted. Narrow the posting dates for a complete review.</p>}
    </section>
  );
}
