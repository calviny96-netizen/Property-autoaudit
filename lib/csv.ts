import type { GlossaryEntry, GlossaryKind } from '@/lib/types';

/** Small CSV reader for glossary uploads (quotes, commas or semicolons, and both line endings handled). */
export function parseCsv(text: string, separator?: ',' | ';' | '\t'): string[][] {
  const firstLine = text.slice(0, text.search(/\r?\n|$/));
  const sep = separator ?? (firstLine.includes('\t') ? '\t' : !firstLine.includes(',') && firstLine.includes(';') ? ';' : ',');
  const rows: string[][] = [];
  let row: string[] = [];
  let value = '';
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') { value += '"'; index += 1; }
      else quoted = !quoted;
    } else if (char === sep && !quoted) {
      row.push(value.trim()); value = '';
    } else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && text[index + 1] === '\n') index += 1;
      row.push(value.trim());
      if (row.some(Boolean)) rows.push(row);
      row = []; value = '';
    } else value += char;
  }
  row.push(value.trim());
  if (row.some(Boolean)) rows.push(row);
  return rows;
}

export type CsvPreview = { fileName: string; rows: [string, string][]; invalid: number; duplicates: number };

const HEADER_WORDS = new Set(['alias', 'istilah', 'sinonim', 'kolom a', 'asal', 'from', 'term', 'word', 'written as', 'in chats']);

/** Legacy two-column glossary: column A = word as written in chats, column B = the standard name. */
export function prepareGlossaryCsv(fileName: string, text: string): CsvPreview {
  let grid = parseCsv(text);
  if (grid[0] && HEADER_WORDS.has((grid[0][0] || '').toLowerCase())) grid = grid.slice(1);
  const entries: [string, string][] = [];
  const seen = new Set<string>();
  let invalid = 0;
  let duplicates = 0;
  for (const cells of grid) {
    const alias = (cells[0] || '').trim();
    const canonical = (cells[1] || '').trim();
    if (!alias || !canonical || alias.length > 100 || canonical.length > 100) { invalid += 1; continue; }
    const key = alias.toLowerCase();
    if (seen.has(key)) { duplicates += 1; continue; }
    seen.add(key);
    entries.push([alias, canonical]);
  }
  return { fileName, rows: entries, invalid, duplicates };
}

const KIND_WORDS: Record<string, GlossaryKind> = {
  synonym: 'synonym', sinonim: 'synonym', alias: 'synonym', word: 'synonym', kata: 'synonym', '': 'synonym',
  location: 'location', place: 'location', lokasi: 'location', area: 'location', tempat: 'location',
  category: 'category', kategori: 'category', type: 'category', jenis: 'category', 'property type': 'category',
};

/** Reads "synonym" / "Location" / "kategori" … as a glossary kind; null when it is not a known kind. */
export function glossaryKind(value: string | undefined): GlossaryKind | null {
  return KIND_WORDS[(value ?? '').trim().toLowerCase()] ?? null;
}

export type GlossaryCsvPreview = {
  fileName: string;
  entries: GlossaryEntry[];
  /** Rows skipped because a cell was missing, too long, or the kind was unknown (1-based line numbers). */
  invalid: number;
  invalidLines: number[];
  duplicates: number;
};

/**
 * Structured glossary CSV: term, canonical, kind (optional, default synonym), parent area (optional), note (optional).
 * A header row is detected and skipped. Category rows must use a category key when `categoryKeys` is given.
 */
export function prepareGlossaryEntriesCsv(fileName: string, text: string, categoryKeys?: readonly string[]): GlossaryCsvPreview {
  let grid = parseCsv(text);
  let offset = 1;
  if (grid[0] && HEADER_WORDS.has((grid[0][0] || '').toLowerCase())) { grid = grid.slice(1); offset = 2; }
  const allowed = categoryKeys ? new Set(categoryKeys) : null;
  const entries: GlossaryEntry[] = [];
  const seen = new Set<string>();
  const invalidLines: number[] = [];
  let duplicates = 0;
  grid.forEach((cells, index) => {
    const term = (cells[0] || '').trim();
    const kind = glossaryKind(cells[2]);
    let canonical = (cells[1] || '').trim();
    const parent = (cells[3] || '').trim();
    const note = (cells[4] || '').trim();
    if (kind === 'category') canonical = canonical.toLowerCase().replace(/\s+/g, '_');
    const bad = !term || !canonical || !kind || term.length > 100 || canonical.length > 100 || parent.length > 100 || note.length > 500
      || (kind === 'category' && allowed !== null && !allowed.has(canonical));
    if (bad) { invalidLines.push(index + offset); return; }
    const key = term.toLowerCase();
    if (seen.has(key)) { duplicates += 1; return; }
    seen.add(key);
    entries.push({ term, canonical, kind, parent: parent || null, note: note || null });
  });
  return { fileName, entries, invalid: invalidLines.length, invalidLines, duplicates };
}
