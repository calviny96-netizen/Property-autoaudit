'use client';

/*
 * "Import from CSV / Excel" for the sales watchlist. The server only reads the file into rows
 * (POST /stock/tracked/parse, nothing is saved). Here the admin picks the sheet (asked only when
 * the file has several), the phone and name columns, sees what will change, and the numbers are
 * saved with the same PUT /stock/tracked that "Paste many numbers" uses.
 */

import { useId, useMemo, useState, type DragEvent, type ReactNode } from 'react';
import { Button, Chip, Label, ListBox, Select, Switch } from '@heroui/react';
import { ChevronRight, FileSpreadsheet, Sheet } from 'lucide-react';
import { LoadingIndicator, Notice } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { countLabel, formatPhone, normalizePhone, number } from '@/lib/format';
import type { ParsedSheet, ParsedSheetFile, StockOverview, StockTracked } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

const PHONE = /^628\d{8,12}$/;
const MAX_FILE_MB = 5;
const ACCEPT = '.csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12';
const PREVIEW_ROWS = 8;
const NO_NAME = 'none';
const NAME_LENGTH = 80;

/* ------------------------------------------------------------------ reading cells */

/** A run of digits that can be a phone number: "+62 812-3456-7890", "(0812) 3456 7890". */
const NUMBER_RUN = /\+?\(?\d[\d\s().-]{5,}\d/g;
/** What separates several numbers in one cell: "0812…, 0813…", "0812… / 0813…", "0812… atau 0813…". */
const SEPARATORS = /[\n,;/|&]+|\s+(?:or|atau|dan|and)\s+/i;

function toPhone(text: string) {
  const phone = normalizePhone(text);
  return phone.startsWith('620') ? `62${phone.slice(3)}` : phone; // "+62 (0) 812…"
}

/** The number in a cell, normalised to 62…; with several numbers, the first mobile number. */
function phoneFromCell(value?: string) {
  let first = '';
  for (const part of (value ?? '').split(SEPARATORS)) {
    for (const run of part.match(NUMBER_RUN) ?? []) {
      const phone = toPhone(run);
      if (PHONE.test(phone)) return { phone, valid: true };
      if (!first) first = phone;
    }
  }
  return { phone: first, valid: false };
}

const looksLikePhone = (value?: string) => phoneFromCell(value).valid;
const looksLikeName = (value?: string) => {
  const text = (value ?? '').trim();
  return text.length > 1 && text.length <= NAME_LENGTH && /\p{L}/u.test(text) && (text.match(/\d/g) ?? []).length < 5;
};
const nameOf = (value?: string) => (value ?? '').replace(/\s+/g, ' ').trim().slice(0, NAME_LENGTH);

/** 0 → "A", 25 → "Z", 26 → "AA". */
function columnLetter(index: number) {
  let label = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) label = String.fromCharCode(65 + ((n - 1) % 26)) + label;
  return label;
}

/* ------------------------------------------------------------------ guessing the columns */

const words = (text: string) => text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
const PHONE_WORDS = ['telp', 'telepon', 'telpon', 'tlp', 'phone', 'handphone', 'hp', 'wa', 'whatsapp', 'mobile', 'ponsel'];
const NUMBER_WORDS = ['no', 'nomor', 'nomer', 'number'];
const NAME_WORDS = ['nama', 'name'];
const ROLE_WORDS = ['sales', 'agent', 'agen', 'marketing'];

/** 2: the header says phone ("No HP", "WhatsApp", "Telp"); 1: only "No" / "Nomor" / "Number" (often a row number); 0: neither. */
function phoneHeaderRank(header: string) {
  const tokens = words(header);
  if (tokens.some((token) => PHONE_WORDS.includes(token) || /^no(?:hp|wa|telp|tlp)$/.test(token)
    || PHONE_WORDS.some((word) => word.length > 4 && token.includes(word)))) return 2;
  return tokens.some((token) => NUMBER_WORDS.includes(token)) ? 1 : 0;
}

/** 2: "Nama" / "Name"; 1: "Sales" / "Agent" / "Marketing"; 0: neither. */
function nameHeaderRank(header: string) {
  const tokens = words(header);
  if (tokens.some((token) => NAME_WORDS.some((word) => token.startsWith(word)))) return 2;
  return tokens.some((token) => ROLE_WORDS.some((word) => token.startsWith(word))) ? 1 : 0;
}

type Columns = { phone: number; name: number | null };

/** On when the first row has no phone number in it. */
const detectHeader = (rows: readonly string[][]) => rows.length > 0 && !rows[0].some(looksLikePhone);

/** Phone column: a header that says phone and has numbers below it, else the column with the most mobile numbers. */
function suggestColumns(rows: readonly string[][], header: boolean): Columns {
  const width = Math.max(1, ...rows.map((row) => row.length));
  const head = header ? rows[0] ?? [] : [];
  const data = (header ? rows.slice(1) : rows).slice(0, 300);
  const share = (index: number, test: (value?: string) => boolean) => (data.length ? data.filter((row) => test(row[index])).length / data.length : 0);
  const columns = Array.from({ length: width }, (_, index) => ({
    index, phoneRank: phoneHeaderRank(head[index] ?? ''), nameRank: nameHeaderRank(head[index] ?? ''),
    phones: share(index, looksLikePhone), names: share(index, looksLikeName),
  }));
  const byHeader = columns.filter((column) => column.phoneRank > 0 && (column.phones >= 0.2 || !data.length))
    .sort((a, b) => b.phoneRank - a.phoneRank || b.phones - a.phones)[0];
  const byContent = [...columns].sort((a, b) => b.phones - a.phones)[0];
  const phone = byHeader ?? (byContent.phones > 0 ? byContent : columns.find((column) => column.phoneRank > 0) ?? columns[0]);
  const others = columns.filter((column) => column.index !== phone.index && column.phones < 0.5);
  const nameByHeader = others.filter((column) => column.nameRank > 0).sort((a, b) => b.nameRank - a.nameRank || b.names - a.names)[0];
  // Without a header row: the column that is mostly names ("Budi", "Sari Dewi").
  const nameByContent = header ? undefined : others.filter((column) => column.names >= 0.6).sort((a, b) => b.names - a.names)[0];
  return { phone: phone.index, name: (nameByHeader ?? nameByContent)?.index ?? null };
}

/* ------------------------------------------------------------------ what the import will do */

type Status = 'new' | 'existing' | 'repeat' | 'invalid';
type Line = { row: number; name: string; raw: string; phone: string; status: Status };
type Entry = { phone: string; name: string };
type Mode = 'add' | 'replace';

function analyse(rows: readonly string[][], header: boolean, columns: Columns, onList: ReadonlySet<string>) {
  const data = header ? rows.slice(1) : rows;
  const unique = new Map<string, Entry>();
  const lines: Line[] = [];
  const invalid: string[] = [];
  let repeats = 0;
  let blank = 0;
  data.forEach((row, index) => {
    const raw = (row[columns.phone] ?? '').trim();
    const name = columns.name === null ? '' : nameOf(row[columns.name]);
    if (!raw) { blank += 1; return; }
    const { phone, valid } = phoneFromCell(raw);
    const known = valid ? unique.get(phone) : undefined;
    let status: Status;
    if (!valid) { status = 'invalid'; invalid.push(raw); }
    else if (known) { status = 'repeat'; repeats += 1; if (!known.name && name) known.name = name; }
    else { status = onList.has(phone) ? 'existing' : 'new'; unique.set(phone, { phone, name }); }
    if (lines.length < PREVIEW_ROWS) lines.push({ row: index, name, raw, phone, status });
  });
  const entries = [...unique.values()];
  return { lines, entries, invalid, repeats, blank, existing: entries.filter((entry) => onList.has(entry.phone)).length, rows: data.length };
}

/** The PUT /stock/tracked body and what it changes. `limit` is how many of the `wanted` numbers fit. */
function planImport(mode: Mode, entries: readonly Entry[], current: readonly StockTracked[], max: number) {
  const onList = new Set(current.map((item) => item.phone));
  const saved: Record<string, string> = Object.fromEntries(current.filter((item) => item.label).map((item) => [item.phone, item.label as string]));
  const renamed = entries.filter((entry) => onList.has(entry.phone) && entry.name && entry.name !== (saved[entry.phone] ?? ''));
  if (mode === 'add') {
    const fresh = entries.filter((entry) => !onList.has(entry.phone));
    const room = Math.max(0, max - current.length);
    const added = fresh.slice(0, room);
    const labels = { ...saved };
    for (const entry of [...renamed, ...added]) if (entry.name) labels[entry.phone] = entry.name;
    return {
      phones: [...current.map((item) => item.phone), ...added.map((entry) => entry.phone)], labels,
      added: added.length, renamed: renamed.length, removed: 0, wanted: fresh.length, limit: room, total: current.length + added.length,
    };
  }
  const kept = entries.slice(0, max);
  const keep = new Set(kept.map((entry) => entry.phone));
  const labels: Record<string, string> = {};
  for (const entry of kept) {
    const label = entry.name || saved[entry.phone];
    if (label) labels[entry.phone] = label;
  }
  return {
    phones: kept.map((entry) => entry.phone), labels,
    added: kept.filter((entry) => !onList.has(entry.phone)).length, renamed: renamed.filter((entry) => keep.has(entry.phone)).length,
    removed: current.filter((item) => !keep.has(item.phone)).length, wanted: entries.length, limit: max, total: kept.length,
  };
}

/* ------------------------------------------------------------------ the flow */

export type WatchlistImportResult = { title: string; phonesChanged: boolean };

/** Choose a file → (choose a sheet) → choose the columns, check the preview and import. */
export function WatchlistImport({ current, maxTracked, onImported }: {
  current: readonly StockTracked[]; maxTracked: number; onImported: (overview: StockOverview, result: WatchlistImportResult) => void;
}) {
  const api = useApi();
  const inputId = useId();
  const helpId = useId();
  const [file, setFile] = useState<ParsedSheetFile | null>(null);
  const [sheetIndex, setSheetIndex] = useState<number | null>(null);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');

  function reset() { setFile(null); setSheetIndex(null); setError(''); }

  async function choose(chosen?: File | null) {
    if (!chosen || reading) return;
    setError('');
    const name = chosen.name.toLowerCase();
    if (name.endsWith('.xls')) { setError('Old Excel files (.xls) cannot be read. In Excel, choose File > Save As > Excel Workbook (.xlsx) or CSV, then choose that file.'); return; }
    if (!/\.(csv|xlsx|xlsm)$/.test(name)) { setError('Please choose a CSV or Excel file (.csv or .xlsx).'); return; }
    if (chosen.size > MAX_FILE_MB * 1024 * 1024) { setError(`The file is too large. Use a file of at most ${MAX_FILE_MB} MB.`); return; }
    setReading(true);
    try {
      const data = new FormData();
      data.append('file', chosen);
      const parsed = await api.form<ParsedSheetFile>('/stock/tracked/parse', data);
      setFile(parsed);
      setSheetIndex(parsed.sheets.length === 1 ? 0 : null);
    } catch (reason) { setError(errorMessage(reason, 'The file could not be read. Please try again.')); } finally { setReading(false); }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void choose(event.dataTransfer.files?.[0]);
  }

  const sheet = file && sheetIndex !== null ? file.sheets[sheetIndex] : undefined;
  if (file && sheet) {
    return (
      <ColumnsStep key={`${file.file_name}:${sheetIndex}`} fileName={file.file_name} sheet={sheet} current={current} maxTracked={maxTracked}
        onChangeSheet={file.sheets.length > 1 ? () => setSheetIndex(null) : undefined} onCancel={reset}
        onImported={(overview, result) => { reset(); onImported(overview, result); }} />
    );
  }
  if (file) return <SheetStep file={file} onPick={setSheetIndex} onCancel={reset} />;

  return (
    <div className="space-y-3">
      <p id={helpId} className="text-sm leading-relaxed text-muted">
        Use a spreadsheet with a column of phone numbers (08… or 628…). A column with the sales names is optional. You choose the columns in the next step.
      </p>
      <div role="presentation" onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
        <input id={inputId} type="file" accept={ACCEPT} className="peer sr-only" disabled={reading} aria-describedby={helpId}
          onChange={(event) => { const picked = event.target.files?.[0]; event.target.value = ''; void choose(picked); }} />
        <label htmlFor={inputId}
          className={cn('flex min-h-32 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-2xl border-2 border-dashed p-5 text-center transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-accent/40',
            dragging ? 'border-accent bg-accent-soft' : 'border-border bg-surface-secondary/50 hover:border-accent/60 hover:bg-accent-soft/40',
            reading && 'pointer-events-none opacity-60')}>
          <FileSpreadsheet className="size-8 text-accent" aria-hidden="true" />
          <span className="text-body font-semibold text-foreground">Choose a CSV or Excel file</span>
          <span className="text-sm text-muted">or drop it here · .csv or .xlsx, up to {MAX_FILE_MB} MB</span>
        </label>
      </div>
      {reading && <LoadingIndicator message="Reading the file…" />}
      {error && <Notice status="danger">{error}</Notice>}
    </div>
  );
}

function FileLine({ name, sheet }: { name: string; sheet?: string }) {
  return (
    <p className="flex min-w-0 items-center gap-2 text-sm text-muted">
      <FileSpreadsheet className="size-4 shrink-0 text-accent" aria-hidden="true" />
      <span className="min-w-0 truncate"><span className="font-semibold text-foreground">{name}</span>{sheet ? ` · ${sheet}` : ''}</span>
    </p>
  );
}

function SheetStep({ file, onPick, onCancel }: { file: ParsedSheetFile; onPick: (index: number) => void; onCancel: () => void }) {
  const headingId = useId();
  return (
    <div className="space-y-4">
      <FileLine name={file.file_name} />
      <div>
        <h3 id={headingId} className="text-body font-semibold text-foreground">Which sheet has the sales numbers?</h3>
        <p className="text-sm text-muted">This file has {number(file.sheets.length)} sheets with data.</p>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2" aria-labelledby={headingId}>
        {file.sheets.map((item, index) => {
          const firstRow = item.rows[0]?.filter(Boolean).slice(0, 3).join(' · ');
          return (
            <li key={`${index}:${item.name}`}>
              <button type="button" onClick={() => onPick(index)}
                className="flex min-h-16 w-full items-center gap-3 rounded-2xl border border-border bg-surface px-4 py-3 text-left transition-colors outline-none hover:border-accent/60 hover:bg-accent-soft/40 focus-visible:ring-2 focus-visible:ring-accent/40">
                <Sheet className="size-5 shrink-0 text-accent" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body font-semibold text-foreground">{item.name}</span>
                  <span className="block truncate text-caption text-muted">{countLabel(item.total_rows, 'row')}{firstRow ? ` · ${firstRow}` : ''}</span>
                </span>
                <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>
      <Button variant="tertiary" onPress={onCancel}>Choose another file</Button>
    </div>
  );
}

const STATUS_TEXT: Record<Status, { label: string; className: string }> = {
  new: { label: 'New', className: 'font-semibold text-success' },
  existing: { label: 'Already on the watchlist', className: 'text-muted' },
  repeat: { label: 'Duplicate, skipped', className: 'text-muted' },
  invalid: { label: 'Not a mobile number, skipped', className: 'text-danger' },
};

function ColumnsStep({ fileName, sheet, current, maxTracked, onChangeSheet, onCancel, onImported }: {
  fileName: string; sheet: ParsedSheet; current: readonly StockTracked[]; maxTracked: number;
  onChangeSheet?: () => void; onCancel: () => void; onImported: (overview: StockOverview, result: WatchlistImportResult) => void;
}) {
  const api = useApi();
  const modeName = useId();
  const rows = sheet.rows;
  const [header, setHeader] = useState(() => detectHeader(rows));
  const [columns, setColumns] = useState<Columns>(() => suggestColumns(rows, detectHeader(rows)));
  const [touched, setTouched] = useState(false);
  const [mode, setMode] = useState<Mode>('add');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const onList = useMemo(() => new Set(current.map((item) => item.phone)), [current]);
  const result = useMemo(() => analyse(rows, header, columns, onList), [rows, header, columns, onList]);
  const plan = useMemo(() => planImport(mode, result.entries, current, maxTracked), [mode, result.entries, current, maxTracked]);
  const options = useMemo(() => {
    const width = Math.max(1, ...rows.map((row) => row.length));
    const head = header ? rows[0] ?? [] : [];
    const data = header ? rows.slice(1) : rows;
    return Array.from({ length: width }, (_, index) => {
      const title = head[index]?.trim();
      const sample = data.find((row) => row[index]?.trim())?.[index]?.trim();
      return {
        id: String(index), label: title || `Column ${columnLetter(index)}`,
        detail: [title ? `Column ${columnLetter(index)}` : '', sample ? `e.g. ${sample}` : ''].filter(Boolean).join(' · '),
      };
    });
  }, [rows, header]);

  const over = plan.wanted > plan.limit;
  const canImport = mode === 'add' ? plan.added > 0 || plan.renamed > 0 : plan.total > 0;
  const count = mode === 'add' ? plan.added : plan.total;
  const importLabel = mode === 'add' && plan.added === 0 && plan.renamed > 0 ? `Update ${countLabel(plan.renamed, 'name')}`
    : over && plan.limit > 0 ? `Import the first ${number(plan.limit)}`
    : count > 0 ? `Import ${countLabel(count, 'number')}` : 'Import';
  const unreadRows = Math.max(0, sheet.total_rows - rows.length);

  function changeHeader(next: boolean) {
    setHeader(next);
    if (!touched) setColumns(suggestColumns(rows, next));
  }
  function changeColumns(next: Partial<Columns>) {
    setTouched(true);
    setColumns((value) => ({ ...value, ...next }));
  }

  async function save() {
    setBusy(true); setError('');
    try {
      const overview = await api.put<StockOverview>('/stock/tracked', { phones: plan.phones, labels: plan.labels });
      const title = mode === 'add' && plan.added === 0
        ? `Updated ${countLabel(plan.renamed, 'sales name')}`
        : `Imported ${countLabel(mode === 'add' ? plan.added : plan.total, 'sales number')}`;
      onImported(overview, { title, phonesChanged: plan.added > 0 || plan.removed > 0 });
    } catch (reason) { setError(errorMessage(reason, 'The numbers could not be imported. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <FileLine name={fileName} sheet={onChangeSheet ? sheet.name : undefined} />
        <div className="-ml-2 flex flex-wrap sm:ml-0">
          {onChangeSheet && <Button size="sm" variant="ghost" isDisabled={busy} onPress={onChangeSheet}>Change sheet</Button>}
          <Button size="sm" variant="ghost" isDisabled={busy} onPress={onCancel}>Choose another file</Button>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-body font-semibold text-foreground">Which columns hold the numbers and names?</h3>
        <Switch isSelected={header} onChange={changeHeader} isDisabled={busy}>
          <Switch.Content>
            <Switch.Control><Switch.Thumb /></Switch.Control>
            <span className="text-sm font-semibold">First row is a header
              <span className="mt-0.5 block font-normal text-muted">{header ? 'The first row has column titles and is not imported.' : 'The first row is imported like the others.'}</span></span>
          </Switch.Content>
        </Switch>
        <div className="grid gap-3 sm:grid-cols-2">
          <ColumnSelect label="Phone number column" value={String(columns.phone)} options={options} isRequired isDisabled={busy}
            onChange={(value) => { const phone = Number(value); changeColumns(columns.name === phone ? { phone, name: null } : { phone }); }} />
          <ColumnSelect label={<>Sales name column <span className="font-normal text-muted">(optional)</span></>} value={columns.name === null ? NO_NAME : String(columns.name)}
            options={[{ id: NO_NAME, label: 'No name column', detail: '' }, ...options.filter((option) => option.id !== String(columns.phone))]} isDisabled={busy}
            onChange={(value) => changeColumns({ name: value === NO_NAME ? null : Number(value) })} />
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="sr-only">Preview</h3>
        <div className="flex flex-wrap gap-2">
          <Chip variant="soft" color="success">{countLabel(result.entries.length, 'valid number')}</Chip>
          {result.existing > 0 && <Chip variant="soft" color="accent">{number(result.existing)} already on the watchlist</Chip>}
          {result.repeats > 0 && <Chip variant="soft">{countLabel(result.repeats, 'duplicate')}</Chip>}
          {result.invalid.length > 0 && <Chip variant="soft" color="danger">{number(result.invalid.length)} invalid</Chip>}
        </div>
        {result.invalid.length > 0 && (
          <p className="text-sm break-words text-muted">
            Not recognised as Indonesian mobile numbers: <span className="font-semibold text-foreground">{result.invalid.slice(0, 3).join(', ')}</span>
            {result.invalid.length > 3 ? ` and ${number(result.invalid.length - 3)} more` : ''}. These rows are skipped.
          </p>
        )}
        {result.blank > 0 && <p className="text-sm text-muted">{countLabel(result.blank, 'row')} without a number in this column {result.blank === 1 ? 'is' : 'are'} skipped.</p>}
        {result.lines.length > 0 ? (
          <div className="overflow-x-auto rounded-2xl border border-border">
            <table className="w-full min-w-[28rem] text-left text-sm">
              <caption className="sr-only">The first rows of {sheet.name} as they will be imported</caption>
              <thead className="bg-surface-secondary text-caption text-muted">
                <tr><th scope="col" className="px-3 py-2 font-semibold">Name</th><th scope="col" className="px-3 py-2 font-semibold">Phone</th><th scope="col" className="px-3 py-2 font-semibold">Result</th></tr>
              </thead>
              <tbody>
                {result.lines.map((line) => (
                  <tr key={line.row} className="border-t border-separator">
                    <td className="px-3 py-2 font-semibold break-words text-foreground">{line.name || <span className="font-normal text-muted">—</span>}</td>
                    <td className="px-3 py-2 whitespace-nowrap tabular-nums">{line.status === 'invalid' ? <span className="text-danger">{line.raw}</span> : formatPhone(line.phone)}</td>
                    <td className={cn('px-3 py-2', STATUS_TEXT[line.status].className)}>{STATUS_TEXT[line.status].label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Notice status="warning">No phone numbers in this column. Choose another column{onChangeSheet ? ' or sheet' : ''}.</Notice>
        )}
        {result.rows > result.lines.length && result.lines.length > 0 && (
          <p className="text-caption text-muted">Showing the first {number(result.lines.length)} of {countLabel(result.rows, 'row')}.{unreadRows ? ` Only the first ${number(rows.length)} rows of the sheet are read.` : ''}</p>
        )}
      </div>

      {result.entries.length > 0 && (
        <fieldset className="space-y-3 border-0 p-0">
          <legend className="mb-3 text-body font-semibold text-foreground">How should they be added?</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {([
              ['add', 'Add to the current watchlist', 'Keeps the numbers you have. New numbers are added; names from the file replace the saved ones.'],
              ['replace', 'Replace the whole watchlist', 'Only the numbers in this file stay on the watchlist. Their stock history is kept.'],
            ] as const).map(([id, title, hint]) => (
              <label key={id} className={cn('flex cursor-pointer items-start gap-3 rounded-2xl border p-3.5 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent/40',
                mode === id ? 'border-accent/50 bg-accent-soft/40' : 'border-border bg-surface hover:border-border-secondary', busy && 'pointer-events-none opacity-60')}>
                <input type="radio" name={modeName} value={id} checked={mode === id} disabled={busy} onChange={() => setMode(id)} className="mt-0.5 size-4 shrink-0 accent-accent" />
                <span className="min-w-0 text-sm font-semibold text-foreground">{title}
                  <span className="mt-0.5 block text-caption font-normal text-muted">{hint}</span>
                </span>
              </label>
            ))}
          </div>
          <p className="text-sm text-foreground">
            {mode === 'add'
              ? <>{countLabel(plan.added, 'new number')}{plan.renamed ? ` · ${countLabel(plan.renamed, 'name')} updated` : ''} · the watchlist will have {number(plan.total)} of {number(maxTracked)} numbers.</>
              : <>The watchlist will have {number(plan.total)} of {number(maxTracked)} numbers: {number(plan.added)} new{plan.removed ? <>, <span className="font-semibold text-danger">{number(plan.removed)} removed</span></> : ''}.</>}
          </p>
          {mode === 'add' && over && plan.limit === 0 && (
            <Notice status="danger" title="The watchlist is full">
              It already has {number(current.length)} numbers, the most a company can follow. Remove numbers first, or choose “Replace the whole watchlist”.
            </Notice>
          )}
          {over && plan.limit > 0 && (
            <Notice status="warning" title="Not every number fits">
              {mode === 'add'
                ? <>The watchlist can hold {number(maxTracked)} numbers and already has {number(current.length)}, so only the first {number(plan.limit)} of the {number(plan.wanted)} new numbers can be added.</>
                : <>The watchlist can hold {number(maxTracked)} numbers, so only the first {number(plan.limit)} of the {number(plan.wanted)} numbers in this file can be imported.</>}
              {' '}Import those, or cancel and shorten the file.
            </Notice>
          )}
          {mode === 'add' && !over && !canImport && <Notice status="accent">Every number in this file is already on the watchlist with the same name.</Notice>}
        </fieldset>
      )}

      {error && <Notice status="danger">{error}</Notice>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="tertiary" isDisabled={busy} onPress={onCancel}>Cancel</Button>
        <Button isPending={busy} isDisabled={!canImport} onPress={() => void save()}>{importLabel}</Button>
      </div>
    </div>
  );
}

function ColumnSelect({ label, value, options, onChange, isRequired, isDisabled }: {
  label: ReactNode; value: string; options: { id: string; label: string; detail: string }[];
  onChange: (value: string) => void; isRequired?: boolean; isDisabled?: boolean;
}) {
  return (
    <Select value={value} onChange={(next) => { if (next !== null) onChange(String(next)); }} fullWidth isRequired={isRequired} isDisabled={isDisabled}>
      <Label className="text-sm font-semibold">{label}</Label>
      <Select.Trigger><Select.Value>{({ selectedText }) => selectedText}</Select.Value><Select.Indicator /></Select.Trigger>
      <Select.Popover>
        <ListBox>
          {options.map((option) => (
            <ListBox.Item key={option.id} id={option.id} textValue={option.label}>
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{option.label}</span>
                {option.detail && <span className="truncate text-caption text-muted">{option.detail}</span>}
              </span>
              <ListBox.ItemIndicator />
            </ListBox.Item>
          ))}
        </ListBox>
      </Select.Popover>
    </Select>
  );
}
