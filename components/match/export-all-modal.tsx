'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Label, Modal, ProgressBar, SearchField, toast, type useOverlayState } from '@heroui/react';
import { CircleCheck, Download, FileArchive, FileText, Sparkles } from 'lucide-react';
import { EmptyState, ErrorNotice, FilterGroup, LoadingIndicator, Notice, Segmented, SkeletonList, StatusBadge } from '@/components/app/primitives';
import { BlurFade } from '@/components/magicui/blur-fade';
import { errorMessage } from '@/lib/api';
import { countLabel, formatPhone, number, prettyRange, todayWib } from '@/lib/format';
import type { Direction, ExportPartResult, ExportPlan, ExportPlanGroup, ExportPlanSection, GroupBy, MatchStatus } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

type Stage = 'confirm' | 'running' | 'merging' | 'done' | 'failed';
/** Backend values: 'grouped' = a ZIP with one PDF per group, 'single' = one combined PDF. */
type Output = 'grouped' | 'single';
/** 'both' = per person a Listing → Buyer section, then a Buyer → Listing section; 'one' = the open direction only. */
type Scope = 'both' | 'one';

/** Rough speed before the first parts have been timed; the estimate follows the real speed after that. */
const PAGES_PER_SECOND = 25;
const PHRASE_SECONDS = 6;
/** Rows rendered at once in the group checklist; the rest come with "Show more". */
const ROWS_STEP = 100;
const STATUS_ORDER: readonly MatchStatus[] = ['hot', 'warm', 'custom', 'unmatched'];
const DIRECTION_TITLES: Record<Direction, string> = { property: 'Listing → Buyer', buyer: 'Buyer → Listing' };

/** Thirty lines at six seconds each: three minutes before the first one comes round again. */
function phrases(one: string, many: string, targets: string) {
  return [
    'Analysing your matches…', `Reading every ${one} in your filters…`, `Collecting Hot ${targets}…`, `Collecting Warm ${targets}…`,
    `Collecting Custom ${targets}…`, 'Laying out the pages…', 'Comparing locations…', 'Comparing prices and budgets…',
    'Checking land and building sizes…', 'Putting the best matches first…', 'Adding WhatsApp buttons to each page…',
    'Adding the original messages…', `Checking which ${targets} are still Ready…`, 'Preparing a cover page for each group…',
    'Merging identical messages…', 'Numbering the pages…', `Fetching the next ${many}…`, 'Arranging two tidy columns…',
    'Marking Hot matches…', 'Highlighting the words that match…', 'Making sure nothing is missed…', 'Tidying long messages…',
    'Adding buyer and listing IDs…', 'Saving the finished parts…', 'Almost done with this group…', 'Moving on to the next group…',
    'More data takes a little longer. Thanks for waiting…', 'Please keep this window open…', 'Writing down why each pair fits…', 'Preparing the PDF file…',
  ];
}

function duration(seconds: number) {
  if (seconds < 50) return 'less than a minute';
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `about ${minutes} min` : `about ${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

const text = (value: unknown) => (typeof value === 'string' ? value : '');
/** "on 18 Oct 2026", "in 12–18 Oct 2026", "on any date". */
function foundText(from: string, to: string) {
  if (!from && !to) return 'on any date';
  return `${!to || from === to ? 'on' : 'in'} ${prettyRange(from, to)}`;
}
const digits = (value: string) => value.replace(/\D/g, '');
/** Plans from before sections existed: one section in the open direction, one cover. */
const sectionsOf = (group: ExportPlanGroup, direction: Direction): ExportPlanSection[] => group.sections ?? [{ ...group, direction }];
const coversOf = (group: ExportPlanGroup) => group.covers ?? 1;
/** "2 listings · 1 buyer" for a person in the export of both directions. */
function sourcesText(sections: readonly { direction: Direction; sources: number }[]) {
  return sections.map((section) => countLabel(section.sources, section.direction === 'buyer' ? 'buyer' : 'listing')).join(' · ');
}

/**
 * Choose the senders or sales contacts to export, then build the PDFs with real progress per group.
 * ``combined`` opens it on the export of both directions (one file per person, two sections).
 */
export function ExportAllModal({ state, direction, filters, combined = false }: {
  state: ReturnType<typeof useOverlayState>; direction: Direction; filters: Record<string, unknown>; combined?: boolean;
}) {
  const [locked, setLocked] = useState(false);
  return (
    <Modal.Backdrop isOpen={state.isOpen} isDismissable={!locked} isKeyboardDismissDisabled={locked} onOpenChange={(open) => { if (open || !locked) state.setOpen(open); }}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog>{state.isOpen ? <Body direction={direction} filters={filters} initialScope={combined && !filters.recent ? 'both' : 'one'} onLock={setLocked} onClose={() => { setLocked(false); state.close(); }} /> : null}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function Body({ direction, filters, initialScope, onLock, onClose }: {
  direction: Direction; filters: Record<string, unknown>; initialScope: Scope; onLock: (locked: boolean) => void; onClose: () => void;
}) {
  const api = useApi();
  // null = the company's own grouping; the plan answers with the grouping it used.
  const [groupBy, setGroupBy] = useState<GroupBy | null>(null);
  const [scope, setScope] = useState<Scope>(initialScope);
  const both = scope === 'both';
  const plan = useData((signal) => api.post<ExportPlan>('/export/all/plan', { ...filters, ...(groupBy ? { group_by: groupBy } : {}), combined: both }, signal), [groupBy, both]);
  const [output, setOutput] = useState<Output>('grouped');
  // The choice belongs to one grouping and scope; a new plan with another one starts with every group selected.
  const [picks, setPicks] = useState<{ groupBy: GroupBy; both: boolean; keys: string[] } | null>(null);
  const [search, setSearch] = useState('');
  const [shown, setShown] = useState(ROWS_STEP);
  const [stage, setStage] = useState<Stage>('confirm');
  const [current, setCurrent] = useState(0);
  const [pages, setPages] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [problem, setProblem] = useState('');
  const [file, setFile] = useState('');
  const [exported, setExported] = useState<{ groups: number; pages: number; name: string } | null>(null);
  const cancelled = useRef(false);
  const startedAt = useRef(0);
  const working = stage === 'running' || stage === 'merging';

  // Leaving the page or closing the window stops the loop; unfinished parts are thrown away by the server later.
  useEffect(() => () => { cancelled.current = true; }, []);
  useEffect(() => () => { if (file) URL.revokeObjectURL(file); }, [file]);
  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => setElapsed((Date.now() - startedAt.current) / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [working]);

  const source = direction === 'buyer' ? 'buyer' : 'listing';
  const target = direction === 'buyer' ? 'listing' : 'buyer';
  const data = plan.data;
  // While a new grouping or scope loads, the old plan stays visible but cannot be exported.
  const replanning = plan.loading && !!data;
  const planBoth = !!data?.combined;
  const planGroupBy: GroupBy = data?.group_by ?? groupBy ?? 'sender';
  const byPhone = planGroupBy === 'phone';
  const unit: [string, string] = byPhone ? ['sales contact', 'sales contacts'] : ['sender', 'senders'];
  const groups = useMemo(() => data?.groups ?? [], [data]);
  const selected = useMemo(() => {
    const all = groups.map((group) => group.key);
    if (!picks || picks.groupBy !== planGroupBy || picks.both !== planBoth) return new Set(all);
    const chosen = new Set(picks.keys);
    return new Set(all.filter((key) => chosen.has(key)));
  }, [groups, picks, planGroupBy, planBoth]);
  const chosen = useMemo(() => groups.filter((group) => selected.has(group.key)), [groups, selected]);

  const query = search.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!query) return groups;
    const queryDigits = digits(query);
    return groups.filter((group) => {
      const haystack = `${group.title} ${group.key} ${group.contact_name ?? ''}`.toLowerCase();
      if (haystack.includes(query)) return true;
      // "0812 3456" finds +62 812-3456-…: compare digits without the country code.
      const national = queryDigits.replace(/^(?:62|0)/, '');
      return national.length >= 3 && [group.phone, group.key].some((value) => digits(value ?? '').includes(national));
    });
  }, [groups, query]);

  const totals = useMemo(() => chosen.reduce((sum, group) => ({
    sources: sum.sources + group.sources, hot: sum.hot + group.hot, warm: sum.warm + group.warm,
    custom: sum.custom + (group.custom ?? 0), unmatched: sum.unmatched + group.unmatched, pages: sum.pages + group.pages,
    covers: sum.covers + coversOf(group),
  }), { sources: 0, hot: 0, warm: 0, custom: 0, unmatched: 0, pages: 0, covers: 0 }), [chosen]);
  // Sources per direction, for "12 listings · 30 buyers" in the export of both directions.
  const sectionTotals = useMemo(() => {
    const sums: Record<Direction, number> = { property: 0, buyer: 0 };
    for (const group of chosen) for (const section of sectionsOf(group, direction)) sums[section.direction] += section.sources;
    return (['property', 'buyer'] as const).filter((key) => sums[key] > 0).map((key) => ({ direction: key, sources: sums[key] }));
  }, [chosen, direction]);

  const fileName = `${filters.recent ? 'Recent-Matches' : 'All-Matches'}${planBoth ? '-Both' : ''}-${todayWib()}.${output === 'grouped' ? 'zip' : 'pdf'}`;
  // Every section of a group starts with a cover page.
  const totalPages = totals.pages + totals.covers;
  const maxPages = data?.max_pages ?? 0;
  const tooBig = !!data && chosen.length > 0 && (output === 'single' ? totalPages > maxPages : chosen.some((group) => group.pages + coversOf(group) > maxPages));
  const fraction = stage === 'done' || stage === 'merging' ? 1 : totalPages ? Math.min(pages / totalPages, 1) : 0;
  const estimate = totalPages / PAGES_PER_SECOND + chosen.length * 0.3 + 5;
  // After a few seconds of real work, the remaining time follows the measured speed.
  const remaining = elapsed > 4 && fraction > 0.02 ? (elapsed / fraction) * (1 - fraction) : Math.max(estimate - elapsed, 0);
  const lines = planBoth ? phrases('listing and buyer', 'listings and buyers', 'matches') : phrases(source, `${source}s`, `${target}s`);
  const phrase = stage === 'merging'
    ? (output === 'grouped' ? 'Packing the PDFs into one ZIP file…' : 'Combining every part into one PDF…')
    : lines[Math.floor(elapsed / PHRASE_SECONDS) % lines.length];

  function choose(keys: Iterable<string>) {
    setPicks({ groupBy: planGroupBy, both: planBoth, keys: [...keys] });
  }
  function toggle(key: string) {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key); else next.add(key);
    choose(next);
  }
  function selectFiltered() { choose(new Set([...selected, ...filtered.map((group) => group.key)])); }
  function clearFiltered() {
    const hidden = new Set(filtered.map((group) => group.key));
    choose([...selected].filter((key) => !hidden.has(key)));
  }
  function changeGrouping(next: GroupBy) {
    if (next === (groupBy ?? planGroupBy)) { if (plan.error) plan.reload(); return; }
    setGroupBy(next);
    setSearch(''); setShown(ROWS_STEP);
    if (stage === 'failed') { setStage('confirm'); setProblem(''); }
  }
  function changeScope(next: Scope) {
    if (next === scope) { if (plan.error) plan.reload(); return; }
    setScope(next);
    setSearch(''); setShown(ROWS_STEP);
    if (stage === 'failed') { setStage('confirm'); setProblem(''); }
  }

  function save(url: string, name: string) {
    const link = document.createElement('a');
    link.href = url; link.download = name; link.click();
  }

  async function start() {
    if (!data || !chosen.length || replanning || plan.error) return;
    const run = chosen;
    const name = fileName;
    // A retry reuses the plan's token: clear the parts of the failed attempt first.
    if (stage === 'failed') await api.post('/export/all/cancel', { token: data.token }).catch(() => {});
    cancelled.current = false;
    startedAt.current = Date.now();
    setStage('running'); setProblem(''); setPages(0); setElapsed(0); setCurrent(0);
    onLock(true);
    try {
      let written = 0;
      for (let index = 0; index < run.length; index += 1) {
        setCurrent(index);
        // Page and pair numbers start again for every person (both sections share one count).
        let page = 1;
        let pair = 1;
        for (const section of sectionsOf(run[index], direction)) {
          let offset: number | null = 0;
          while (offset !== null) {
            if (cancelled.current) return;
            const done: ExportPartResult = await api.post('/export/all/part', {
              ...filters, combined: data.combined ?? false, direction: section.direction, group_by: data.group_by, token: data.token,
              index, group_key: run[index].key, offset, first_page: page, first_pair: pair,
            });
            page += done.pages; pair += done.pairs ?? 0; written += done.pages; offset = done.next_offset;
            setPages(written);
          }
        }
      }
      if (cancelled.current) return;
      setStage('merging');
      const blob = await api.file(`/export/all/${data.token}/download?output=${output}`);
      if (cancelled.current) return;
      const url = URL.createObjectURL(blob);
      setFile(url); save(url, name);
      setExported({ groups: run.length, pages: written, name });
      setStage('done');
      toast.success(output === 'grouped' ? 'ZIP with one PDF per group downloaded' : 'Combined PDF downloaded');
    } catch (reason) {
      if (cancelled.current) return;
      setProblem(errorMessage(reason, 'The export could not be finished. Please try again.'));
      setStage('failed');
      void api.post('/export/all/cancel', { token: data.token }).catch(() => {});
    } finally { onLock(false); }
  }

  function stop() {
    cancelled.current = true;
    if (data) void api.post('/export/all/cancel', { token: data.token }).catch(() => {});
    toast('Export cancelled');
    onClose();
  }

  const recentEvents = Array.isArray(filters.recent_events) ? filters.recent_events.length : 0;
  const recentSources = Array.isArray(filters.recent_sources) ? filters.recent_sources.length : 0;
  const intro = recentEvents ? `Export the ${countLabel(recentEvents, 'selected match', 'selected matches')}.`
    : recentSources ? `Export the matches of ${countLabel(recentSources, 'selected card')}.`
    : filters.recent ? `Export the pairs found ${foundText(text(filters.found_from), text(filters.found_to))}, with your current filters.`
    : both ? 'Export every match in both directions that fits your current filters.'
    : `Export every ${DIRECTION_TITLES[direction]} match that fits your current filters.`;
  const heading = stage === 'done' ? 'Export finished' : working ? 'Creating your PDFs…' : filters.recent ? 'Export recent matches'
    : both ? 'Export both directions' : 'Export all matches';
  const visibleRows = filtered.slice(0, shown);
  const allFilteredOn = filtered.length > 0 && filtered.every((group) => selected.has(group.key));
  const noneFilteredOn = filtered.every((group) => !selected.has(group.key));
  const canEdit = stage === 'confirm' || stage === 'failed';

  return (
    <>
      {!working && <Modal.CloseTrigger />}
      <Modal.Header>
        <Modal.Heading>{heading}</Modal.Heading>
        {canEdit && <p className="mt-1 text-sm text-muted">{intro}</p>}
      </Modal.Header>
      <Modal.Body className="space-y-5">
        {!filters.recent && canEdit && (
          <FilterGroup label="What to export" hint={both
            ? 'One file per person with two sections: Listing → Buyer (dark blue cover), then Buyer → Listing (sky blue cover).'
            : `Only the ${DIRECTION_TITLES[direction]} matches you are looking at.`}>
            <Segmented label="What to export" value={scope} onChange={changeScope} fullWidth
              options={[{ id: 'both', label: 'Both directions' }, { id: 'one', label: `${DIRECTION_TITLES[direction]} only` }]} />
          </FilterGroup>
        )}
        <ErrorNotice message={plan.error} onRetry={plan.reload} title="The export could not be prepared" />
        {plan.loading && !data && <SkeletonList rows={3} />}

        {data && canEdit && (
          <>
            <FilterGroup label="Group the PDFs by" hint={byPhone ? 'The name and phone number written in the message signature.' : 'The person who posted the message in WhatsApp.'}>
              <Segmented label="Group the PDFs by" value={groupBy ?? planGroupBy} onChange={changeGrouping} fullWidth
                options={[{ id: 'sender', label: 'Sender' }, { id: 'phone', label: 'Sales contact' }]} />
            </FilterGroup>
            {replanning && <LoadingIndicator message={`Loading ${(groupBy ?? planGroupBy) === 'phone' ? 'sales contacts' : 'senders'}…`} />}

            {groups.length === 0 && !replanning && (
              <EmptyState compact title="Nothing to export for these filters" description="Change the dates or turn on more statuses (Hot, Warm, Custom or No match), then try again." />
            )}

            {groups.length > 0 && (
              <section aria-label={`Choose ${unit[1]}`} className={cn('space-y-3', replanning && 'pointer-events-none opacity-60')}>
                <div className="flex flex-wrap items-end justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold text-foreground">{byPhone ? 'Sales contacts' : 'Senders'}</h3>
                    <p className="text-caption text-muted"><span className="font-semibold text-foreground tabular-nums">{number(chosen.length)}</span> of {countLabel(groups.length, unit[0], unit[1])} selected</p>
                  </div>
                  <div className="flex gap-1">
                    <Button size="sm" variant="tertiary" isDisabled={allFilteredOn} onPress={selectFiltered}>{query ? 'Select shown' : 'Select all'}</Button>
                    <Button size="sm" variant="tertiary" isDisabled={noneFilteredOn} onPress={clearFiltered}>{query ? 'Clear shown' : 'Clear'}</Button>
                  </div>
                </div>
                {groups.length > 6 && (
                  <SearchField value={search} onChange={(value) => { setSearch(value); setShown(ROWS_STEP); }} aria-label={byPhone ? 'Search sales contacts' : 'Search senders'}>
                    <Label className="sr-only">{byPhone ? 'Search sales contacts' : 'Search senders'}</Label>
                    <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder={byPhone ? 'Search by name or phone' : 'Search by name'} /><SearchField.ClearButton /></SearchField.Group>
                  </SearchField>
                )}
                <ul className="max-h-[min(26rem,55dvh)] divide-y divide-separator overflow-y-auto overscroll-contain rounded-2xl border border-border bg-surface">
                  {visibleRows.map((group) => (
                    <GroupRow key={group.key || '__none'} group={group} byPhone={byPhone} sources={planBoth ? sourcesText(sectionsOf(group, direction)) : countLabel(group.sources, source)}
                      checked={selected.has(group.key)} onToggle={() => toggle(group.key)} />
                  ))}
                  {filtered.length === 0 && <li className="px-4 py-6 text-center text-sm text-muted">No {unit[1]} match “{search.trim()}”.</li>}
                  {filtered.length > visibleRows.length && (
                    <li className="p-2"><Button variant="secondary" size="sm" fullWidth onPress={() => setShown((value) => value + ROWS_STEP)}>Show {number(Math.min(ROWS_STEP, filtered.length - visibleRows.length))} more</Button></li>
                  )}
                </ul>
              </section>
            )}

            {groups.length > 0 && (
              <fieldset className="mx-0 min-w-0 space-y-2 border-0 p-0">
                <legend className="mb-2 text-sm font-semibold text-foreground">File type</legend>
                {([
                  ['grouped', FileArchive, `ZIP file — one PDF per ${unit[0]}`, planBoth
                    ? `Each PDF is named after the ${unit[0]}, holds both sections and starts at page 1.`
                    : `Each PDF is named after the ${unit[0]} and starts at page 1.`],
                  ['single', FileText, 'One combined PDF', planBoth
                    ? `Every selected ${unit[0]} in one file, each with a dark blue and a sky blue section cover.`
                    : `Every selected ${unit[0]} in one file, each starting with a cover page.`],
                ] as const).map(([value, Icon, title, hint]) => (
                  <label key={value} className={cn('flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors', output === value ? 'border-accent bg-accent-soft/50' : 'border-border hover:border-border-secondary')}>
                    <input type="radio" name="export-output" value={value} checked={output === value} onChange={() => setOutput(value)} className="mt-1 size-4 shrink-0 accent-[var(--accent)]" />
                    <Icon className="mt-0.5 size-5 shrink-0 text-muted" aria-hidden="true" />
                    <span className="min-w-0"><span className="block text-sm font-semibold text-foreground">{title}</span><span className="block text-caption text-muted">{hint}</span></span>
                  </label>
                ))}
              </fieldset>
            )}

            {groups.length > 0 && chosen.length > 0 && (
              <div className="space-y-3 rounded-2xl bg-surface-secondary p-4">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-1 text-sm font-semibold text-foreground">{planBoth ? sourcesText(sectionTotals) : countLabel(totals.sources, source)}</span>
                  {STATUS_ORDER.map((status) => (totals[status] > 0 ? <StatusBadge key={status} status={status} count={totals[status]} size="sm" explain={false} /> : null))}
                </div>
                <p className="text-sm text-muted">
                  About <strong className="font-semibold text-foreground tabular-nums">{countLabel(totalPages, 'page')}</strong> · takes <strong className="font-semibold text-foreground">{duration(estimate)}</strong>.
                  {output === 'single' && !tooBig && totalPages > 3000 ? ' This is a large file. Narrow the dates or choose fewer groups for a quicker export.' : ''}
                </p>
                <p className="text-caption text-muted">Every pair page is numbered per {unit[0]}, explains why it was recommended and highlights the matching words in both messages.</p>
              </div>
            )}
            {groups.length > 0 && chosen.length === 0 && <p className="text-sm text-muted">Select at least one {unit[0]} to export.</p>}
            {tooBig && (
              <Notice status="warning" title={output === 'grouped' ? `One ${unit[0]} has too many pages` : 'Too large for one PDF'}>
                The limit is {countLabel(maxPages, 'page')}{output === 'grouped' ? ' per file' : ''}. Narrow the posting dates, choose fewer statuses{output === 'single' ? ` or fewer ${unit[1]}` : ''}, then try again.
              </Notice>
            )}
            {stage === 'failed' && <ErrorNotice message={problem} title="The export stopped" />}
          </>
        )}

        {data && working && (
          <div className="space-y-4 py-2" aria-live="polite">
            <div className="flex items-center gap-3">
              <span className="flex size-12 shrink-0 animate-float items-center justify-center rounded-2xl bg-accent-soft text-accent"><Sparkles className="size-6" aria-hidden="true" /></span>
              <BlurFade key={phrase} duration={0.5} className="min-w-0 flex-1"><p className="text-body leading-snug font-semibold">{phrase}</p></BlurFade>
            </div>
            <ProgressBar aria-label="Export progress" value={Math.round(fraction * 100)} color="accent" size="lg">
              <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
                <span className="font-semibold">{stage === 'merging' ? 'Finishing the file' : `${titleUnit(unit[0])} ${number(Math.min(current + 1, chosen.length || 1))} of ${number(chosen.length)}`}</span>
                <ProgressBar.Output className="font-semibold tabular-nums" />
              </div>
              <ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
            </ProgressBar>
            <p className="truncate text-sm text-muted">{stage === 'merging' ? `All ${unit[1]} are done.` : chosen[current]?.title}</p>
            <p className="text-sm text-muted">
              <span className="tabular-nums">{number(Math.min(pages, totalPages))}</span> of <span className="tabular-nums">{countLabel(totalPages, 'page')}</span> · <strong className="font-semibold text-foreground">{stage === 'merging' ? 'almost done' : `${duration(remaining)} left`}</strong>
            </p>
            <p className="text-caption text-muted">Please keep this window open until the download starts.</p>
          </div>
        )}

        {stage === 'done' && exported && (
          <div className="flex flex-col items-center gap-2 py-4 text-center">
            <CircleCheck className="size-14 text-success" aria-hidden="true" />
            <p className="text-section font-semibold">{countLabel(exported.pages, 'page')} for {countLabel(exported.groups, unit[0], unit[1])}</p>
            <p className="text-sm text-muted"><strong className="font-semibold break-all text-foreground">{exported.name}</strong> has been downloaded. If it didn’t appear, tap Download again.</p>
          </div>
        )}
      </Modal.Body>
      <Modal.Footer className="flex flex-wrap justify-end gap-2">
        {working && <Button variant="tertiary" onPress={stop}>Cancel export</Button>}
        {!working && <Button variant="tertiary" onPress={onClose}>{stage === 'done' ? 'Close' : 'Cancel'}</Button>}
        {stage === 'done' && exported && <Button onPress={() => save(file, exported.name)}><Download className="size-4" aria-hidden="true" />Download again</Button>}
        {canEdit && (
          <Button isDisabled={!data || !chosen.length || tooBig || replanning || !!plan.error} onPress={start}>
            {output === 'grouped' ? <FileArchive className="size-4" aria-hidden="true" /> : <FileText className="size-4" aria-hidden="true" />}
            {stage === 'failed' ? 'Try again' : chosen.length && chosen.length < groups.length ? `Export ${countLabel(chosen.length, unit[0], unit[1])}` : 'Start export'}
          </Button>
        )}
      </Modal.Footer>
    </>
  );
}

const titleUnit = (unit: string) => unit[0].toUpperCase() + unit.slice(1);

/** One sender or sales contact in the checklist: the whole row is the touch target. */
function GroupRow({ group, byPhone, sources, checked, onToggle }: { group: ExportPlanGroup; byPhone: boolean; sources: string; checked: boolean; onToggle: () => void }) {
  const title = group.title || group.key || (byPhone ? 'No phone number' : 'Unknown sender');
  const phone = !byPhone && group.phone ? formatPhone(group.phone) : '';
  const counts: [MatchStatus, number][] = [['hot', group.hot], ['warm', group.warm], ['custom', group.custom ?? 0], ['unmatched', group.unmatched]];
  return (
    <li>
      <label className={cn('grid min-h-14 cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 px-3 py-2.5 transition-colors sm:px-4', checked ? 'bg-accent-soft/35' : 'hover:bg-surface-secondary/70')}>
        <input type="checkbox" checked={checked} onChange={onToggle} className="row-span-2 mt-0.5 size-5 shrink-0 cursor-pointer accent-[var(--accent)]" />
        <span className="truncate text-sm font-semibold text-foreground" title={title}>{title}</span>
        <span className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="text-caption text-muted">{sources}{phone ? ` · ${phone}` : ''}</span>
          {counts.map(([status, count]) => (count > 0 ? <StatusBadge key={status} status={status} count={count} size="sm" explain={false} showIcon={false} /> : null))}
        </span>
      </label>
    </li>
  );
}
