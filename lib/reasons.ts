/*
 * Match explanations come from the matching engine as plain strings, in this order:
 * the purpose and unit lines ("Purpose: buy ✓", "Unit: apartment ✓"), positive reasons
 * (location, "Legal status: SHM ✓", "Unit type: 2BR ✓", "Tower A ✓", "Floor 7 ✓",
 * "Financing: KPA possible ✓", price), then "Needs check" items — first the ones that make a
 * pair Custom ("Needs check: tower not stated — buyer only accepts Tower A or C"), then the
 * softer ones — then internal summary lines.
 * Customers see the first groups only; summary lines ("Location 100%",
 * "Asset category matches: house", "Land area: not requested …") and technical
 * wording are hidden. Older stored explanations may still be Indonesian until the
 * next recompute, so both languages are recognised.
 */

/** Lines that are internal scoring detail, never shown to customers. */
const INTERNAL = [
  /^(?:location|lokasi)\s*:?\s*\d+(?:[.,]\d+)?\s*%/i,
  /^(?:asset\s+categor(?:y|ies)|kategori\s+aset)\b/i,
  /^(?:semantic|semantik|text\s+similarity|kemiripan\s+teks|data\s+quality|kualitas\s+data|score|skor)\b/i,
  /\bnot\s+requested\b|\btidak\s+diminta\b/i,
  /\b(?:extraction|ekstraksi|confidence|vector|qdrant|embedding|worker)\b/i,
  /^\s*[\w\s]+:\s*\d+(?:[.,]\d+)?\s*%\s*$/i,
];

/** Wording that marks something the agent still has to verify. */
const NEEDS_CHECK = [
  /^needs?\s+check\b/i,
  /\b(?:not\s+(?:yet\s+)?(?:confirmed|verified|stated|available|known|clear|read|met)|unconfirmed|unverified|unknown|needs?\s+(?:to\s+be\s+)?(?:checked|check|confirm(?:ed|ation)?|verif(?:ied|ication|y))|please\s+check|held\s+back|on\s+hold\s+until|tolerance|negotiat)/i,
  /\b(?:belum|perlu|ditahan|toleransi|negosiasi)\b/i,
];

/** The engine ends a proven requirement with a check mark ("Tower A ✓"); the list draws its own icon. */
const CHECK_MARK = /\s*[✓✔☑✅]️?\s*$/u;
const NEEDS_PREFIX = /^needs?\s+check\s*[:：\-–—]\s*/i;

/** Technical fragments trimmed from otherwise useful reasons (mostly older stored explanations). */
const NOISE: [RegExp, string][] = [
  [/\s*(?:menurut|according\s+to\s+the|per\s+the)\s+(?:indeks(?:\s+lokasi)?|location\s+index|index)\b/gi, ''],
  [/\s*(?:menurut|according\s+to|per)\s+(?:data\s+impor|imported\s+data)\b/gi, ''],
  [/\s*\((?:batas|limit)\s+4\s*km\)|\s*\(4\s*km\s+limit\)/gi, ''],
  // Price or size outside the Hot range but still allowed: Settings call that the Warm range.
  [/\(within\s+(?:the\s+)?tolerance\)/gi, '(within the Warm range)'],
  [/\s{2,}/g, ' '],
];

/** Whole-line rewrites so a reason reads naturally next to its icon. */
const REWRITES: [RegExp, string][] = [
  // "Purpose: buy (not confirmed)" → "Purpose not confirmed (buy)"; "Purpose: not stated" → "Purpose not stated".
  [/^purpose\s*:\s*(.+?)\s*\(not\s+confirmed\)$/i, 'Purpose not confirmed ($1)'],
  [/^purpose\s*:\s*not\s+stated$/i, 'Purpose not stated'],
  // "Floor level ✓" (a floor range inside the buyer's limit) has nothing after the label.
  [/^floor\s+level$/i, 'Floor level fits'],
];

export type ReasonKind = 'positive' | 'check' | 'internal';

export function reasonKind(text: string): ReasonKind {
  const line = text.trim();
  if (!line || INTERNAL.some((pattern) => pattern.test(line))) return 'internal';
  // A proven requirement ("Tower Anderson ✓", a place name that happens to contain "perlu") is positive.
  if (CHECK_MARK.test(line) && !NEEDS_PREFIX.test(line)) return 'positive';
  return NEEDS_CHECK.some((pattern) => pattern.test(line)) ? 'check' : 'positive';
}

/** "Needs check: rent period not stated" → "Rent period not stated"; "Legal status: SHM ✓" → "Legal status: SHM". */
function tidy(text: string, kind: ReasonKind) {
  let line = text.trim().replace(CHECK_MARK, '');
  for (const [pattern, replacement] of NOISE) line = line.replace(pattern, replacement);
  if (kind === 'check') line = line.replace(NEEDS_PREFIX, '');
  line = line.trim();
  for (const [pattern, replacement] of REWRITES) line = line.replace(pattern, replacement);
  return line ? line[0].toUpperCase() + line.slice(1) : line;
}

/** Customer-facing reasons, positives first, then items to verify. Duplicates and internal lines are removed. */
export function splitReasons(reasons: readonly string[] | null | undefined) {
  const positive: string[] = [];
  const checks: string[] = [];
  const seen = new Set<string>();
  for (const raw of reasons ?? []) {
    const kind = reasonKind(raw);
    if (kind === 'internal') continue;
    const text = tidy(raw, kind);
    const key = text.toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    (kind === 'check' ? checks : positive).push(text);
  }
  return { positive, checks };
}

/** True when a pair has something the agent must verify before forwarding it. */
export const hasChecks = (reasons: readonly string[] | null | undefined) => splitReasons(reasons).checks.length > 0;
