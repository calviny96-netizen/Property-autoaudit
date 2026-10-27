'use client';

/*
 * The company glossary (GET/PUT /glossary/entries): words the team writes and what they mean.
 *   Synonym  — rewrites a word before a message is read ("rgcy" → "regency").
 *   Location — marks a place, optionally inside a larger area ("cl" → "citraland", in "surabaya barat").
 *   Category — maps a word to a property type ("kost" → House).
 * Edits stay local until "Save glossary"; saving marks results out of date (see reprocess-banner).
 */

import { useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button, Chip, Input, Label, ListBox, Modal, SearchField, Select, TextField, toast, useOverlayState } from '@heroui/react';
import { ArrowRight, Download, FileSpreadsheet, MapPin, Pencil, Plus, Repeat2, Save, Shapes, Trash2 } from 'lucide-react';
import { Collapsible, EmptyState, ErrorNotice, Notice, Panel, SkeletonList } from '@/components/app/primitives';
import { useSaveNotice, type ReprocessStatus } from '@/components/settings/reprocess-banner';
import { errorMessage } from '@/lib/api';
import { prepareGlossaryEntriesCsv, type GlossaryCsvPreview } from '@/lib/csv';
import { categoryLabel, countLabel, number, titleCase } from '@/lib/format';
import { useData } from '@/lib/use-data';
import type { GlossaryCategory, GlossaryDefault, GlossaryEntry, GlossaryKind, GlossaryResponse, GlossarySaveResponse, LocationAreasResponse } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

/* ------------------------------------------------------------------ helpers */

export const KIND_LABELS: Record<GlossaryKind, string> = { synonym: 'Synonym', location: 'Location', category: 'Category' };
const KIND_HINTS: Record<GlossaryKind, string> = {
  synonym: 'Rewrites a word before the message is read, e.g. “rgcy” → “regency”.',
  location: 'Marks a place. Optionally say which larger area it is in, e.g. “cl” → “citraland”, inside “surabaya barat”.',
  category: 'Maps a word to a property type, e.g. “kost” → House.',
};
const KIND_ICON: Record<GlossaryKind, typeof MapPin> = { synonym: Repeat2, location: MapPin, category: Shapes };
const MAX_ENTRIES = 2000;
const MAX_TEXT = 100;
const MAX_NOTE = 500;
const PAGE = 50;
/** Saving replaces the whole glossary, so it must never start from a list that did not load. */
const NOT_LOADED = 'The glossary has not loaded yet. Reload it, then try again.';

type Row = GlossaryEntry & { key: string };
type SaveResponse = GlossarySaveResponse & { changed?: boolean; reprocess?: ReprocessStatus | null };
type KindFilter = 'all' | GlossaryKind;

export const normText = (value?: string | null) => (value ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
const toRow = (entry: GlossaryEntry): Row => ({ ...entry, key: `t:${normText(entry.term)}` });

/** The entry as the API stores it (lower case, parent only for places). */
function clean(entry: GlossaryEntry): GlossaryEntry {
  return {
    term: normText(entry.term),
    canonical: normText(entry.canonical),
    kind: entry.kind,
    parent: entry.kind === 'location' ? normText(entry.parent) || null : null,
    note: (entry.note ?? '').trim() || null,
  };
}

function same(a: GlossaryEntry, b: GlossaryEntry) {
  const x = clean(a);
  const y = clean(b);
  return x.term === y.term && x.canonical === y.canonical && x.kind === y.kind && x.parent === y.parent && x.note === y.note;
}

function diff(base: readonly Row[], draft?: readonly Row[]) {
  if (!draft) return { added: 0, changed: 0, removed: 0, total: 0 };
  const before = new Map(base.map((row) => [normText(row.term), row]));
  const after = new Set<string>();
  let added = 0;
  let changed = 0;
  for (const row of draft) {
    const term = normText(row.term);
    after.add(term);
    const old = before.get(term);
    if (!old) added += 1;
    else if (!same(old, row)) changed += 1;
  }
  const removed = [...before.keys()].filter((term) => !after.has(term)).length;
  return { added, changed, removed, total: added + changed + removed };
}

/** Row-level problems, keyed by row key, in the same words as the API's checks. */
function findProblems(rows: readonly Row[], categoryKeys: ReadonlySet<string>) {
  const problems = new Map<string, string>();
  const seen = new Map<string, number>();
  for (const row of rows) seen.set(normText(row.term), (seen.get(normText(row.term)) ?? 0) + 1);
  for (const row of rows) {
    const entry = clean(row);
    let problem = '';
    if (!entry.term) problem = 'Enter the word as it is written in chats.';
    else if (entry.term.length > MAX_TEXT) problem = `The word can have at most ${MAX_TEXT} characters.`;
    else if ((seen.get(entry.term) ?? 0) > 1) problem = `“${entry.term}” appears more than once. Keep one entry per word.`;
    else if (!entry.canonical) problem = entry.kind === 'category' ? 'Choose a property type.' : 'Enter what the word means.';
    else if (entry.canonical.length > MAX_TEXT) problem = `The meaning can have at most ${MAX_TEXT} characters.`;
    else if (entry.kind === 'category' && categoryKeys.size && !categoryKeys.has(entry.canonical)) problem = 'Choose one of the property types.';
    else if (entry.parent && entry.parent.length > MAX_TEXT) problem = `The area can have at most ${MAX_TEXT} characters.`;
    else if (entry.parent && (entry.parent === entry.canonical || entry.parent === entry.term)) problem = 'A place cannot be inside itself.';
    else if (entry.note && entry.note.length > MAX_NOTE) problem = `The note can have at most ${MAX_NOTE} characters.`;
    if (problem) problems.set(row.key, problem);
  }
  return problems;
}

function placeNames(entries: readonly GlossaryEntry[], defaults: readonly GlossaryDefault[], areas: readonly string[]) {
  const names = new Set<string>();
  for (const entry of entries) {
    if (entry.kind !== 'location') continue;
    names.add(normText(entry.canonical));
    if (entry.parent) names.add(normText(entry.parent));
  }
  for (const entry of defaults) {
    if (entry.kind !== 'location') continue;
    names.add(normText(entry.canonical));
    if (entry.parent) names.add(normText(entry.parent));
  }
  for (const area of areas) names.add(normText(area));
  names.delete('');
  return [...names].sort();
}

/** A <datalist> of place names for "inside area" inputs (light, works on phones). */
export function PlaceOptions({ id, names }: { id: string; names: readonly string[] }) {
  return <datalist id={id} aria-label="Known places">{names.map((name) => <option key={name} value={name} aria-label={name} />)}</datalist>;
}

function meaningOf(entry: Pick<GlossaryEntry, 'kind' | 'canonical'>) {
  return entry.kind === 'category' ? categoryLabel(entry.canonical) : entry.kind === 'location' ? titleCase(entry.canonical) : entry.canonical;
}

/* ------------------------------------------------------------------ state */

/** Loads the glossary and keeps an unsaved draft next to what is saved. Shared with the place suggestions. */
export function useGlossary() {
  const api = useApi();
  const notice = useSaveNotice();
  const loaded = useData((signal) => api.get<GlossaryResponse>('/glossary/entries', signal), []);
  const areas = useData((signal) => api.get<LocationAreasResponse>('/location-index/areas', signal).catch(() => ({ areas: [] })), []);
  const [draft, setDraft] = useState<Row[] | undefined>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // PUT /glossary/entries replaces every saved entry: edits and saves wait until the saved list is here.
  const ready = !!loaded.data && !loaded.error;
  const base = loaded.data?.entries;
  const setData = loaded.setData;
  const baseRows = useMemo(() => (base ?? []).map(toRow), [base]);
  const rows = draft ?? baseRows;
  const changes = useMemo(() => diff(baseRows, draft), [baseRows, draft]);
  const defaults = useMemo(() => loaded.data?.defaults ?? [], [loaded.data]);
  const categories = useMemo(() => loaded.data?.categories ?? [], [loaded.data]);
  const categoryKeys = useMemo(() => new Set(categories.map((item) => item.key)), [categories]);
  const places = useMemo(() => placeNames(rows, defaults, (areas.data?.areas ?? []).map((item) => item.area)), [rows, defaults, areas.data]);

  async function put(entries: readonly GlossaryEntry[]) {
    if (!ready) throw new Error(NOT_LOADED);
    const result = await api.put<SaveResponse>('/glossary/entries', { entries: entries.map(clean) });
    setData((current) => current && { ...current, entries: result.entries });
    return result;
  }

  /** Saves the whole draft. Returns the problems found before sending (empty when it was sent). */
  async function save() {
    if (!draft) return new Map<string, string>();
    if (!ready) { setError(NOT_LOADED); return new Map<string, string>(); }
    const problems = findProblems(draft, categoryKeys);
    if (problems.size) { setError(`Fix ${countLabel(problems.size, 'entry', 'entries')} before saving.`); return problems; }
    if (draft.length > MAX_ENTRIES) { setError(`The glossary can have at most ${number(MAX_ENTRIES)} entries.`); return problems; }
    setSaving(true); setError('');
    try {
      const result = await put(draft);
      setDraft(undefined);
      notice('Glossary saved', { pending: result.changed === false ? false : result.reprocess?.pending !== false });
    } catch (reason) {
      setError(errorMessage(reason, 'The glossary could not be saved. Please try again.'));
    } finally { setSaving(false); }
    return problems;
  }

  /** Saves one entry straight away on top of what is saved; unsaved edits in the editor stay as they are. */
  async function addNow(entry: GlossaryEntry) {
    if (!ready || !base) throw new Error(NOT_LOADED);
    const term = normText(entry.term);
    const result = await put([...base.filter((item) => normText(item.term) !== term), clean(entry)]);
    setDraft((current) => current && [toRow(clean(entry)), ...current.filter((row) => normText(row.term) !== term)]);
    return result;
  }

  const edit = (update: (rows: Row[]) => Row[]) => {
    if (!ready) return;
    setError(''); setDraft((current) => update(current ?? baseRows));
  };
  const discard = () => { setDraft(undefined); setError(''); };

  return {
    loading: loaded.loading && !loaded.data, loadError: loaded.error, reload: loaded.reload, ready,
    baseRows, rows, defaults, categories, categoryKeys, places, changes, dirty: changes.total > 0,
    saving, error, save, addNow, edit, discard,
  };
}
export type GlossaryState = ReturnType<typeof useGlossary>;

/* ------------------------------------------------------------------ editor */

export function GlossaryEditor({ state, canEdit }: { state: GlossaryState; canEdit: boolean }) {
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<KindFilter>('all');
  const [limit, setLimit] = useState(PAGE);
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [problems, setProblems] = useState<Map<string, string>>(new Map());
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [csv, setCsv] = useState<GlossaryCsvPreview | null>(null);
  const csvDialog = useOverlayState();
  const file = useRef<HTMLInputElement>(null);
  const listId = useId();

  const baseByTerm = useMemo(() => new Map(state.baseRows.map((row) => [normText(row.term), row])), [state.baseRows]);
  const defaultTerms = useMemo(() => new Set(state.defaults.map((item) => normText(item.term))), [state.defaults]);
  const counts = useMemo(() => {
    const result = { all: state.rows.length, synonym: 0, location: 0, category: 0 };
    for (const row of state.rows) result[row.kind] += 1;
    return result;
  }, [state.rows]);
  const needle = normText(search);
  const showingProblems = onlyProblems && problems.size > 0;
  const filtered = useMemo(() => state.rows.filter((row) => {
    if (row.key === editing) return true; // the open editor never disappears behind a filter
    if (showingProblems) return problems.has(row.key);
    if (kind !== 'all' && row.kind !== kind) return false;
    if (!needle) return true;
    return [row.term, row.canonical, row.kind === 'category' ? categoryLabel(row.canonical) : '', row.parent, row.note].some((value) => normText(value).includes(needle));
  }), [state.rows, kind, needle, showingProblems, problems, editing]);
  const shown = filtered.slice(0, limit);
  const busy = state.saving;

  function resetView() { setLimit(PAGE); setOnlyProblems(false); }

  function applyEdit(key: string, entry: GlossaryEntry) {
    state.edit((rows) => rows.map((row) => (row.key === key ? { ...entry, key } : row)));
    setEditing(null);
    setProblems((current) => { if (!current.has(key)) return current; const next = new Map(current); next.delete(key); return next; });
  }
  function addEntry(entry: GlossaryEntry) {
    const term = normText(entry.term);
    const existing = state.rows.find((row) => normText(row.term) === term);
    if (existing) {
      toast.warning(`“${term}” is already in the glossary`, { description: 'Edit the existing entry instead.' });
      setSearch(term); setKind('all'); resetView();
      return false;
    }
    state.edit((rows) => [{ ...entry, key: `n:${Date.now()}:${term}` }, ...rows]);
    setAdding(false);
    return true;
  }
  function remove(row: Row) {
    state.edit((rows) => rows.filter((item) => item.key !== row.key));
    if (editing === row.key) setEditing(null);
  }
  async function save() {
    const found = await state.save();
    setProblems(found);
    if (found.size) { setOnlyProblems(true); setLimit(PAGE); }
    else setOnlyProblems(false);
  }

  async function chooseCsv(chosen?: File) {
    if (file.current) file.current.value = '';
    if (!chosen) return;
    if (chosen.size > 2_000_000) { toast.danger('The file is too large. Use a CSV file of at most 2 MB.'); return; }
    try {
      const prepared = prepareGlossaryEntriesCsv(chosen.name, await chosen.text(), [...state.categoryKeys]);
      if (!prepared.entries.length) { toast.danger('No usable rows found', { description: 'Use the columns: word, meaning, type, inside area, note.' }); return; }
      setCsv(prepared); csvDialog.open();
    } catch (reason) { toast.danger(errorMessage(reason, 'The file could not be read.')); }
  }
  function importCsv() {
    if (!csv) return;
    const incoming = new Map(csv.entries.map((entry) => [normText(entry.term), entry]));
    state.edit((rows) => [
      ...csv.entries.map((entry) => {
        const current = rows.find((row) => normText(row.term) === normText(entry.term));
        return { ...clean(entry), key: current?.key ?? `n:${Date.now()}:${normText(entry.term)}` };
      }),
      ...rows.filter((row) => !incoming.has(normText(row.term))),
    ]);
    csvDialog.close(); setSearch(''); setKind('all'); resetView();
    toast.success(`${countLabel(csv.entries.length, 'entry', 'entries')} added from ${csv.fileName}`, { description: 'Save the glossary to apply them.' });
  }

  return (
    <div className="space-y-4">
      <Panel
        title="Glossary"
        description="Teach the matching how your team writes: other spellings, place names and words for property types."
        bodyClassName="space-y-4"
      >
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <input ref={file} type="file" accept=".csv,.tsv,.txt,text/csv" className="sr-only" aria-label="Choose a glossary CSV file" onChange={(event) => void chooseCsv(event.target.files?.[0])} />
            <Button variant="secondary" isDisabled={busy || adding || !state.ready} onPress={() => { setAdding(true); setEditing(null); }}><Plus className="size-4" aria-hidden="true" />Add entry</Button>
            <Button variant="tertiary" isDisabled={busy || !state.ready} onPress={() => file.current?.click()}><FileSpreadsheet className="size-4" aria-hidden="true" />Import CSV</Button>
            <Button variant="tertiary" onPress={downloadExample}><Download className="size-4" aria-hidden="true" />Example CSV</Button>
          </div>
        )}
        {!canEdit && <Notice status="accent">You can look up entries here. Only company admins can change the glossary.</Notice>}
        <ErrorNotice message={state.loadError} onRetry={state.reload} title="The glossary could not be loaded" />
        {canEdit && state.loadError && <p className="text-sm text-muted">You can add or change entries once the glossary has loaded.</p>}

        <div className="flex flex-col gap-3 xl:flex-row xl:items-end">
          <SearchField value={search} onChange={(value) => { setSearch(value); resetView(); }} aria-label="Search the glossary" className="min-w-0 flex-1" fullWidth>
            <Label className="text-sm font-semibold">Search</Label>
            <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder="Word, meaning or area" /><SearchField.ClearButton /></SearchField.Group>
          </SearchField>
          <fieldset aria-label="Show entries of type" className="m-0 flex min-w-0 flex-wrap gap-2 border-0 p-0">
            {([['all', 'All'], ['synonym', 'Synonyms'], ['location', 'Locations'], ['category', 'Categories']] as const).map(([id, label]) => (
              <button key={id} type="button" aria-pressed={kind === id} onClick={() => { setKind(id); resetView(); }}
                className={cn('inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3.5 text-sm font-semibold transition-colors sm:min-h-10',
                  kind === id ? 'border-accent/40 bg-accent-soft text-accent-soft-foreground' : 'border-border bg-surface text-muted hover:text-foreground')}>
                {label}<span className="tabular-nums opacity-75">{number(counts[id])}</span>
              </button>
            ))}
          </fieldset>
        </div>

        {adding && (
          <EntryEditor isNew initial={{ term: needle, canonical: '', kind: kind === 'all' ? 'synonym' : kind, parent: null, note: null }}
            categories={state.categories} listId={listId} onCancel={() => setAdding(false)} onDone={addEntry} />
        )}

        {showingProblems && (
          <Notice status="danger" title="Some entries need a fix">
            Only the entries with a problem are shown. Edit or remove them, then save again.
            {' '}<button type="button" className="font-semibold underline" onClick={() => setOnlyProblems(false)}>Show all entries</button>
          </Notice>
        )}

        {state.loading ? <SkeletonList rows={3} /> : filtered.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border">
            <EmptyState compact icon={<Repeat2 />}
              title={state.rows.length ? 'No entries match your search' : 'No glossary entries yet'}
              description={state.rows.length ? 'Try another word or show all types.' : canEdit ? 'Add the spellings and place names your team uses, or import them from a CSV file.' : 'Your company admin has not added any entries yet.'} />
          </div>
        ) : (
          <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border" aria-label="Glossary entries">
            {shown.map((row) => {
              const before = baseByTerm.get(normText(row.term));
              const change = !before ? 'New' : !same(before, row) ? 'Edited' : '';
              return editing === row.key ? (
                <li key={row.key} className="bg-surface-secondary/50 p-3 sm:p-4">
                  <EntryEditor initial={row} categories={state.categories} listId={listId} onCancel={() => setEditing(null)}
                    onDone={(entry) => {
                      const term = normText(entry.term);
                      if (state.rows.some((other) => other.key !== row.key && normText(other.term) === term)) {
                        toast.warning(`“${term}” is already in the glossary`, { description: 'Use another word or edit that entry.' });
                        return false;
                      }
                      applyEdit(row.key, entry);
                      return true;
                    }} />
                </li>
              ) : (
                <EntryRow key={row.key} row={row} change={change} problem={problems.get(row.key)} overridesDefault={defaultTerms.has(normText(row.term))}
                  canEdit={canEdit} disabled={busy || !state.ready} onEdit={() => { setEditing(row.key); setAdding(false); }} onRemove={() => remove(row)} />
              );
            })}
          </ul>
        )}
        {filtered.length > shown.length && (
          <Button variant="secondary" fullWidth onPress={() => setLimit((value) => value + PAGE)}>
            Show more ({number(filtered.length - shown.length)} left)
          </Button>
        )}

        <PlaceOptions id={listId} names={state.places} />
        <BuiltInEntries defaults={state.defaults} search={needle} />
      </Panel>

      {canEdit && state.dirty && (
        <div className="pb-safe sticky bottom-20 z-20 flex flex-col gap-3 rounded-2xl border border-border bg-surface/95 p-3 shadow-raised backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-4 lg:bottom-4">
          <div className="min-w-0 text-sm">
            <p className="font-semibold text-foreground">Unsaved glossary changes</p>
            <p className="text-muted">{[
              state.changes.added && `${number(state.changes.added)} added`,
              state.changes.changed && `${number(state.changes.changed)} edited`,
              state.changes.removed && `${number(state.changes.removed)} removed`,
            ].filter(Boolean).join(' · ')}{editing || adding ? ' · finish the open entry first' : ''}</p>
            {state.error && <p className="mt-1 font-medium text-danger" role="alert">{state.error}</p>}
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="tertiary" isDisabled={busy} onPress={() => { state.discard(); setProblems(new Map()); setOnlyProblems(false); setEditing(null); }}>Discard</Button>
            <Button isPending={busy} isDisabled={!!editing || adding || !state.ready} onPress={() => void save()}><Save className="size-4" aria-hidden="true" />Save glossary</Button>
          </div>
        </div>
      )}

      <Modal.Backdrop isOpen={csvDialog.isOpen} onOpenChange={(open) => csvDialog.setOpen(open)}>
        <Modal.Container size="lg" scroll="inside">
          <Modal.Dialog>
            <Modal.CloseTrigger />
            <Modal.Header><Modal.Heading>Import glossary entries</Modal.Heading></Modal.Header>
            <Modal.Body className="space-y-4">
              {csv && <CsvSummary csv={csv} existing={baseByTerm} />}
            </Modal.Body>
            <Modal.Footer>
              <Button slot="close" variant="tertiary">Cancel</Button>
              <Button onPress={importCsv} isDisabled={!csv?.entries.length}><FileSpreadsheet className="size-4" aria-hidden="true" />Add {csv ? countLabel(csv.entries.length, 'entry', 'entries') : 'entries'}</Button>
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </div>
  );
}

export function KindChip({ kind, className }: { kind: GlossaryKind; className?: string }) {
  const Icon = KIND_ICON[kind];
  return (
    <span className={cn('inline-flex h-6 shrink-0 items-center gap-1 rounded-full px-2 text-xs font-semibold',
      kind === 'location' ? 'bg-accent-soft text-accent-soft-foreground' : 'bg-default text-default-foreground', className)}>
      <Icon className="size-3" aria-hidden="true" />{KIND_LABELS[kind]}
    </span>
  );
}

function EntryRow({ row, change, problem, overridesDefault, canEdit, disabled, onEdit, onRemove }: {
  row: Row; change: string; problem?: string; overridesDefault: boolean; canEdit: boolean; disabled?: boolean; onEdit: () => void; onRemove: () => void;
}) {
  return (
    <li className={cn('flex items-start gap-3 px-4 py-3 sm:px-5', problem && 'bg-danger-soft/40')}>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-body text-foreground">
          <span className="font-semibold break-all">{row.term || <em className="font-normal text-muted">No word yet</em>}</span>
          <ArrowRight className="size-4 shrink-0 text-muted" aria-label="means" />
          <span className="break-words">{row.canonical ? meaningOf(row) : <em className="text-muted">Not set</em>}</span>
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
          <KindChip kind={row.kind} />
          {row.kind === 'location' && row.parent && <span className="text-caption text-muted">Inside {titleCase(row.parent)}</span>}
          {change && <Chip size="sm" variant="soft" color={change === 'New' ? 'success' : 'warning'}>{change}</Chip>}
        </div>
        {row.note && <p className="mt-1 text-caption break-words text-muted">{row.note}</p>}
        {overridesDefault && <p className="mt-1 text-caption text-muted">Replaces the built-in entry for this word.</p>}
        {problem && <p className="mt-1 text-caption font-medium text-danger">{problem}</p>}
      </div>
      {canEdit && (
        <div className="-my-1 -mr-2 flex shrink-0">
          <Button isIconOnly variant="ghost" isDisabled={disabled} aria-label={`Edit ${row.term || 'entry'}`} onPress={onEdit}><Pencil className="size-4" /></Button>
          <Button isIconOnly variant="ghost" isDisabled={disabled} aria-label={`Remove ${row.term || 'entry'}`} onPress={onRemove}><Trash2 className="size-4" /></Button>
        </div>
      )}
    </li>
  );
}

/** Add or edit one entry. The draft only changes on "Done", so typing never re-renders the whole list. */
function EntryEditor({ initial, isNew, categories, listId, onDone, onCancel }: {
  initial: GlossaryEntry; isNew?: boolean; categories: readonly GlossaryCategory[]; listId: string;
  onDone: (entry: GlossaryEntry) => boolean; onCancel: () => void;
}) {
  const [entry, setEntry] = useState<GlossaryEntry>({ term: initial.term, canonical: initial.canonical, kind: initial.kind, parent: initial.parent, note: initial.note });
  const [tried, setTried] = useState(false);
  const set = (patch: Partial<GlossaryEntry>) => setEntry((current) => ({ ...current, ...patch }));
  const term = normText(entry.term);
  const canonical = normText(entry.canonical);
  const parent = normText(entry.parent);
  const problem = !term ? 'Enter the word as it is written in chats.'
    : term.length > MAX_TEXT ? `Use at most ${MAX_TEXT} characters for the word.`
      : !canonical ? (entry.kind === 'category' ? 'Choose a property type.' : 'Enter what the word means.')
        : canonical.length > MAX_TEXT ? `Use at most ${MAX_TEXT} characters for the meaning.`
          : entry.kind === 'location' && parent && (parent === canonical || parent === term) ? 'A place cannot be inside itself.'
            : (entry.note ?? '').trim().length > MAX_NOTE ? `Use at most ${MAX_NOTE} characters for the note.` : '';

  function changeKind(kind: GlossaryKind) {
    if (kind === entry.kind) return;
    const fromCategory = entry.kind === 'category';
    set({ kind, canonical: kind === 'category' ? (categories.some((item) => item.key === canonical) ? canonical : '') : fromCategory ? '' : entry.canonical, parent: kind === 'location' ? entry.parent : null });
  }
  function done() {
    setTried(true);
    if (problem) return;
    onDone(clean(entry));
  }

  return (
    <form className={cn('space-y-4', isNew && 'rounded-2xl border border-accent/30 bg-accent-soft/30 p-3 sm:p-4')}
      onSubmit={(event) => { event.preventDefault(); done(); }} aria-label={isNew ? 'New glossary entry' : `Edit ${initial.term}`}>
      {isNew && <p className="text-body font-semibold text-foreground">New entry</p>}
      <div className="grid gap-4 sm:grid-cols-2">
        <Select value={entry.kind} onChange={(value) => changeKind(value as GlossaryKind)} fullWidth aria-label="Type">
          <Label className="text-sm font-semibold">Type</Label>
          <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover>
            <ListBox>
              {(Object.keys(KIND_LABELS) as GlossaryKind[]).map((kind) => (
                <ListBox.Item key={kind} id={kind} textValue={KIND_LABELS[kind]}>{KIND_LABELS[kind]}<ListBox.ItemIndicator /></ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>
        <p className="text-caption text-muted sm:self-end sm:pb-2">{KIND_HINTS[entry.kind]}</p>

        <TextField value={entry.term} onChange={(value) => set({ term: value })} fullWidth isRequired>
          <Label className="text-sm font-semibold">Written in chats as</Label>
          <Input maxLength={MAX_TEXT + 20} placeholder={entry.kind === 'location' ? 'e.g. cl' : entry.kind === 'category' ? 'e.g. kost' : 'e.g. rgcy'} autoComplete="off" />
        </TextField>

        {entry.kind === 'category' ? (
          <Select value={entry.canonical || null} onChange={(value) => set({ canonical: String(value ?? '') })} fullWidth isRequired aria-label="Property type" placeholder="Choose a type">
            <Label className="text-sm font-semibold">Property type</Label>
            <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
            <Select.Popover>
              <ListBox>
                {categories.map((item) => <ListBox.Item key={item.key} id={item.key} textValue={item.label}>{item.label}<ListBox.ItemIndicator /></ListBox.Item>)}
              </ListBox>
            </Select.Popover>
          </Select>
        ) : (
          <TextField value={entry.canonical} onChange={(value) => set({ canonical: value })} fullWidth isRequired>
            <Label className="text-sm font-semibold">{entry.kind === 'location' ? 'Standard place name' : 'Means'}</Label>
            <Input maxLength={MAX_TEXT + 20} placeholder={entry.kind === 'location' ? 'e.g. citraland' : 'e.g. regency'} autoComplete="off" list={entry.kind === 'location' ? listId : undefined} />
          </TextField>
        )}

        {entry.kind === 'location' && (
          <TextField value={entry.parent ?? ''} onChange={(value) => set({ parent: value })} fullWidth>
            <Label className="text-sm font-semibold">Inside area <span className="font-normal text-muted">(optional)</span></Label>
            <Input maxLength={MAX_TEXT + 20} placeholder="e.g. surabaya barat" autoComplete="off" list={listId} />
          </TextField>
        )}

        <TextField value={entry.note ?? ''} onChange={(value) => set({ note: value })} fullWidth className={entry.kind === 'location' ? '' : 'sm:col-span-2'}>
          <Label className="text-sm font-semibold">Note <span className="font-normal text-muted">(optional)</span></Label>
          <Input maxLength={MAX_NOTE} placeholder="Only for your team" autoComplete="off" />
        </TextField>
      </div>
      {tried && problem && <p className="text-sm font-medium text-danger" role="alert">{problem}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="tertiary" onPress={onCancel}>Cancel</Button>
        <Button type="submit">{isNew ? 'Add to glossary' : 'Done'}</Button>
      </div>
    </form>
  );
}

/** Built-in entries: always on, shown for reference (collapsed). */
function BuiltInEntries({ defaults, search }: { defaults: readonly GlossaryDefault[]; search: string }) {
  const [limit, setLimit] = useState(60);
  const filtered = useMemo(() => defaults.filter((item) => !search || [item.term, item.canonical, item.parent].some((value) => normText(value).includes(search))), [defaults, search]);
  if (!defaults.length) return null;
  const shown = filtered.slice(0, limit);
  return (
    <Collapsible title="Built-in words and places" description="Always on. An entry of your own for the same word replaces the built-in one."
      badge={<Chip size="sm" variant="soft">{number(defaults.length)}</Chip>} bodyClassName="space-y-3 p-0 sm:p-0">
      {search && <p className="px-4 pt-3 text-caption text-muted sm:px-5">{countLabel(filtered.length, 'built-in entry', 'built-in entries')} {filtered.length === 1 ? 'matches' : 'match'} “{search}”.</p>}
      {filtered.length === 0 ? <p className="px-4 py-4 text-sm text-muted sm:px-5">No built-in entry matches your search.</p> : (
        <ul className="divide-y divide-separator" aria-label="Built-in entries">
          {shown.map((item) => (
            <li key={`${item.kind}:${item.term}`} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 py-2.5 sm:px-5">
              <KindChip kind={item.kind} />
              <span className="text-sm font-semibold text-foreground">{item.term}</span>
              {item.kind === 'location' && normText(item.canonical) === normText(item.term)
                ? item.parent && <span className="text-sm text-muted">in {titleCase(item.parent)}</span>
                : <><ArrowRight className="size-3.5 text-muted" aria-label="means" /><span className="text-sm text-foreground">{meaningOf(item)}</span>{item.parent && <span className="text-sm text-muted">in {titleCase(item.parent)}</span>}</>}
            </li>
          ))}
        </ul>
      )}
      {filtered.length > shown.length && (
        <div className="px-4 pb-4 sm:px-5"><Button variant="secondary" size="sm" fullWidth onPress={() => setLimit((value) => value + 120)}>Show more ({number(filtered.length - shown.length)} left)</Button></div>
      )}
    </Collapsible>
  );
}

const EXAMPLE_CSV = 'word,meaning,type,inside area,note\nrgcy,regency,synonym,,\ncl,citraland,location,surabaya barat,CitraLand estate\nkost,house,category,,Boarding houses count as houses\n';

function downloadExample() {
  const url = URL.createObjectURL(new Blob([EXAMPLE_CSV], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = 'glossary-example.csv'; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function CsvSummary({ csv, existing }: { csv: GlossaryCsvPreview; existing: ReadonlyMap<string, Row> }) {
  const replacing = csv.entries.filter((entry) => existing.has(normText(entry.term))).length;
  const preview = csv.entries.slice(0, 100);
  const skipped = csv.invalidLines.slice(0, 12).join(', ') + (csv.invalidLines.length > 12 ? ', …' : '');
  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Chip variant="soft">{csv.fileName}</Chip>
        <Chip variant="soft" color="success">{countLabel(csv.entries.length, 'entry', 'entries')} ready</Chip>
        {replacing > 0 && <Chip variant="soft" color="warning">{number(replacing)} replace existing</Chip>}
        {csv.duplicates > 0 && <Chip variant="soft">{countLabel(csv.duplicates, 'repeated row')} ignored</Chip>}
        {csv.invalid > 0 && <Chip variant="soft" color="danger">{countLabel(csv.invalid, 'row')} skipped</Chip>}
      </div>
      {csv.invalid > 0 && <p className="text-sm text-muted">Skipped lines: {skipped}. A row needs a word and a meaning; a category row needs a property type such as “house” or “warehouse”.</p>}
      <div className="max-h-80 overflow-auto rounded-2xl border border-border">
        <table className="w-full min-w-[32rem] text-left text-sm">
          <thead className="sticky top-0 bg-surface-secondary text-caption text-muted">
            <tr><th className="px-3 py-2 font-semibold">Written as</th><th className="px-3 py-2 font-semibold">Type</th><th className="px-3 py-2 font-semibold">Means</th><th className="px-3 py-2 font-semibold">Inside area</th></tr>
          </thead>
          <tbody>
            {preview.map((entry) => (
              <tr key={entry.term} className="border-t border-separator">
                <td className="px-3 py-2 font-semibold">{entry.term}</td>
                <td className="px-3 py-2">{KIND_LABELS[entry.kind]}</td>
                <td className="px-3 py-2">{meaningOf(entry)}</td>
                <td className="px-3 py-2 text-muted">{entry.parent ? titleCase(entry.parent) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {csv.entries.length > preview.length && <p className="text-caption text-muted">Showing the first {number(preview.length)} rows.</p>}
      <HelpLine>
        Columns: word, meaning, type (synonym, location or category), inside area, note. Entries for a word you already have replace it. Nothing is saved until you press “Save glossary”.
        {' '}<button type="button" onClick={downloadExample} className="inline-flex items-center gap-1 font-semibold text-accent hover:underline"><Download className="size-3.5" aria-hidden="true" />Example file</button>
      </HelpLine>
    </>
  );
}

function HelpLine({ children }: { children: ReactNode }) {
  return <p className="text-caption leading-relaxed text-muted">{children}</p>;
}
