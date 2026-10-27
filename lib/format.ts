import type { CompanyStatus, EntityStatus, Feature, MatchStatus, OwnershipTier, Role, Row, Temperature } from '@/lib/types';

/*
 * Every user-visible number, price, date and label goes through here, so the whole
 * product reads the same: English copy, Jakarta (WIB) dates, US-style digit grouping.
 *
 * Rupiah amounts are written the way Indonesian agents write them, not in English words:
 * "Rp 1,25 M" (miliar), "Rp 350 jt" (juta), "Rp 750.000" — decimal comma, dot grouping.
 */

const whole = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const upToTwo = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });

/** 1234567 → "1,234,567". */
export const number = (value: number | null | undefined) => whole.format(Number(value ?? 0));

/** 1.5 → "1.5", 2 → "2" (at most `digits` decimals). */
export function decimal(value: number | null | undefined, digits = 1) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(Number(value ?? 0));
}

/** Word for a count: plural(1, 'listing') → "listing", plural(3, 'match', 'matches') → "matches". */
export function plural(count: number | null | undefined, singular: string, pluralForm = `${singular}s`) {
  return Number(count) === 1 ? singular : pluralForm;
}

/** countLabel(1200, 'buyer') → "1,200 buyers". */
export function countLabel(count: number | null | undefined, singular: string, pluralForm?: string) {
  return `${number(count)} ${plural(count, singular, pluralForm)}`;
}

/* ------------------------------------------------------------------ money */

const UNITS = [
  { size: 1e12, label: 'T' },
  { size: 1e9, label: 'M' },
  { size: 1e6, label: 'jt' },
] as const;

const idDecimal = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 2 });
const idWhole = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 });

function unitOf(amount: number) {
  return UNITS.find((unit) => amount >= unit.size);
}

/** "Rp 1,25 M", "Rp 350 jt", "Rp 750.000". Null for missing or zero. */
export function money(value?: number | null) {
  const amount = Number(value);
  if (!amount || !Number.isFinite(amount)) return null;
  const unit = unitOf(amount);
  return unit ? `Rp ${idDecimal.format(amount / unit.size)} ${unit.label}` : `Rp ${idWhole.format(amount)}`;
}

/** Compact form for chart axes and dense tables: "Rp 1,25 M", "Rp 350 jt", "Rp 750 rb". */
export function moneyShort(value?: number | null) {
  const amount = Number(value);
  if (!amount || !Number.isFinite(amount)) return null;
  const unit = unitOf(amount);
  if (unit) return `Rp ${idDecimal.format(amount / unit.size)} ${unit.label}`;
  return amount >= 1e3 ? `Rp ${idDecimal.format(amount / 1e3)} rb` : `Rp ${idWhole.format(amount)}`;
}

/** A price range that repeats the unit only when it changes: "Rp 0,9–1,49 M", "Rp 900 jt – 1,2 M". */
export function moneyRange(min?: number | null, max?: number | null) {
  const low = Number(min) || 0;
  const high = Number(max) || 0;
  if (!low || !high || low === high) return money(high || low);
  const [a, b] = low < high ? [low, high] : [high, low];
  const unitA = unitOf(a);
  const unitB = unitOf(b);
  if (unitB && unitA === unitB) return `Rp ${idDecimal.format(a / unitB.size)}–${idDecimal.format(b / unitB.size)} ${unitB.label}`;
  if (unitB && !unitA && a >= unitB.size / 100) return `Rp ${idDecimal.format(a / unitB.size)}–${idDecimal.format(b / unitB.size)} ${unitB.label}`;
  return `${money(a)} – ${(money(b) ?? '').replace(/^Rp /, '')}`;
}

export const PRICE_BASIS_SUFFIX: Record<string, string> = { total: '', per_m2: '/m²', per_year: '/tahun', per_month: '/bulan' };

/** Price of a row with its basis: "Rp 2,5 M", "Rp 150 jt/tahun", "Rp 19,5 jt/m²". */
export function priceText(min?: number | null, max?: number | null, basis?: string | null) {
  const price = moneyRange(min, max);
  return price ? `${price}${PRICE_BASIS_SUFFIX[basis ?? ''] ?? ''}` : null;
}

export function area(min?: number | null, max?: number | null) {
  if (!min && !max) return null;
  const low = Number(min ?? max);
  const high = Number(max ?? 0);
  return `${upToTwo.format(low)}${high && low !== high ? `–${upToTwo.format(high)}` : ''} m²`;
}

/* ------------------------------------------------------------------ labels */

export const CATEGORY_LABELS: Record<string, string> = {
  house: 'House', warehouse: 'Warehouse', apartment: 'Apartment', shophouse: 'Shophouse', land: 'Land', factory: 'Factory',
  office: 'Office', villa: 'Villa', commercial_building: 'Commercial building', hotel: 'Hotel', unknown: 'Not stated',
};
export const categoryLabel = (key: string) => CATEGORY_LABELS[key] ?? titleCase(key.replace(/_/g, ' '));

export const TRANSACTION_LABELS: Record<string, string> = { sale: 'Sale', rent: 'Rent', both: 'Sale or rent', unknown: 'Not stated' };
export const transactionLabel = (key?: string | null) => TRANSACTION_LABELS[key ?? 'unknown'] ?? TRANSACTION_LABELS.unknown;

/** The purpose on a card: what a buyer wants ("Buy", "Rent") or what a listing offers ("For sale", "For rent"). */
export const PURPOSE_LABELS: Record<'buyer' | 'property', Record<'sale' | 'rent' | 'both', string>> = {
  buyer: { sale: 'Buy', rent: 'Rent', both: 'Buy or rent' },
  property: { sale: 'For sale', rent: 'For rent', both: 'Sale or rent' },
};
/** Null when the message does not say (transaction 'unknown' or missing). */
export function purposeLabel(transaction: string | null | undefined, kind: 'buyer' | 'property') {
  const labels = PURPOSE_LABELS[kind];
  return transaction && Object.hasOwn(labels, transaction) ? labels[transaction as keyof typeof labels] : null;
}

/* ------------------------------------------------------------------ message parts */

type PartFields = Pick<Row, 'raw_text' | 'normalized_text' | 'segment_index' | 'segment_count' | 'segment_text'>;

/** One WhatsApp message can hold several requests or offers ("#Buyer 1 … #Renter 1 …"); each part is its own card. */
export function isMessagePart(row: Pick<Row, 'segment_count' | 'segment_text'>) {
  return Number(row.segment_count) > 1 && !!row.segment_text?.trim();
}

/** The text a card is about: the part's own text for one part of a split message, otherwise the whole message. */
export function messageText(row: PartFields) {
  return (isMessagePart(row) ? row.segment_text?.trim() : '') || row.raw_text || row.normalized_text || '';
}

/** "Part 1 of 2" for one part of a split message; null for a message with a single request or offer. */
export function partLabel(row: Pick<Row, 'segment_index' | 'segment_count' | 'segment_text'>) {
  if (!isMessagePart(row)) return null;
  const count = Math.trunc(Number(row.segment_count));
  const index = Math.min(Math.max(Math.trunc(Number(row.segment_index) || 0), 0), count - 1);
  return `Part ${index + 1} of ${count}`;
}

/* ------------------------------------------------------------------ matching tolerances */

/**
 * A range widened by "below"/"above" percentages: lower = (min ?? max) × (1 − below %),
 * upper = max × (1 + above %); without a max there is no upper limit. The matcher's Warm range is
 * the Hot range widened by the Warm allowance: sizes LT 300 with 15 % / 15 % → [255, 345]; price
 * with a single budget B starts from the Hot range [B × Hot floor %, B], so 2 M with an 80 % floor
 * and 15 % / 15 % → toleranceBand(1,6 M, 2 M, 15, 15) = [1,36 M, 2,3 M].
 */
export function toleranceBand(min: number | null, max: number | null, belowPct: number, abovePct: number) {
  const base = min ?? max;
  if (base == null || !Number.isFinite(belowPct) || !Number.isFinite(abovePct)) return null;
  const lower = Math.max(0, base * (1 - belowPct / 100));
  const upper = max == null ? null : max * (1 + abovePct / 100);
  return { lower, upper };
}

/** Stock status of a buyer or listing. */
export const STATUS_LABELS: Record<EntityStatus, string> = { ready: 'Ready', on_hold: 'On hold', sold: 'Sold', deleted: 'Deleted' };

export const ROLE_LABELS: Record<Role, string> = { admin: 'Platform admin', company_admin: 'Company admin', user: 'Team member' };

/** Match status order used in every filter, legend and count row. */
export const MATCH_STATUSES: readonly MatchStatus[] = ['hot', 'warm', 'custom', 'unmatched'];
export const MATCH_STATUS_LABELS: Record<MatchStatus, string> = { hot: 'Hot', warm: 'Warm', custom: 'Custom', unmatched: 'No match' };
/** One-line meaning of each status (badges, filters, legends). Hot/Warm ranges: Settings → Matching → Advanced. */
export const MATCH_STATUS_HINTS: Record<MatchStatus, string> = {
  hot: 'Everything clearly matches (score 90+), every buyer requirement is proven, price 80–100% of the budget.',
  warm: 'Everything is proven; only the price or the land/building size is slightly outside the Hot range.',
  custom: 'Something is unclear: a requirement the buyer asked for (location, tower, legal status, unit type, condition, furnishing, a "wajib" or "prefer" wish) is not stated or not proven in the listing, the request is too vague, or it is a nearby alternative — check manually.',
  unmatched: 'Nothing passes the matching rules yet.',
};

/** Stored status of a recommended pair; falls back to the score for rows from before statuses existed. */
export function matchStatusOf(row: Pick<Row, 'status' | 'temperature' | 'score'>): Temperature {
  return row.status ?? row.temperature ?? (Number(row.score) >= 80 ? 'hot' : 'warm');
}

/* -------------------------------------------------------- ownership tiers */

/** Recommendation order: the source's own stock first, then the sales watchlist, then company posts, then everyone else. */
export const OWNERSHIP_TIERS: readonly OwnershipTier[] = ['own', 'team', 'company', 'other'];

/** `kind` is the recommended side: 'property' = a listing, 'buyer' = a buyer request. */
type TargetKind = 'buyer' | 'property';

/** Label on a recommendation card: "Own stock" / "Own buyer", "Team", the company name (or "Company"), "Other agent". */
export function ownershipLabel(tier: OwnershipTier, kind: TargetKind, companyName?: string | null) {
  if (tier === 'own') return kind === 'buyer' ? 'Own buyer' : 'Own stock';
  if (tier === 'team') return 'Team';
  if (tier === 'company') return companyName?.trim() || 'Company';
  return 'Other agent';
}

/** Heading above a group of recommendations: "Own stock", "Own buyers", "Team", "Konig", "Other agents". */
export function ownershipGroupLabel(tier: OwnershipTier, kind: TargetKind, companyName?: string | null) {
  if (tier === 'own') return kind === 'buyer' ? 'Own buyers' : 'Own stock';
  if (tier === 'other') return 'Other agents';
  return ownershipLabel(tier, kind, companyName);
}

/** What one tier means, for the label's explanation. */
export function ownershipHint(tier: OwnershipTier, kind: TargetKind, keywords: readonly string[] = []) {
  const noun = kind === 'buyer' ? 'buyer' : 'listing';
  const source = kind === 'buyer' ? 'this listing' : 'this buyer';
  if (tier === 'own') return `A ${noun} from the same agent as ${source} (same number or WhatsApp sender).`;
  if (tier === 'team') return `A ${noun} from a number on your sales watchlist.`;
  if (tier === 'company') {
    const terms = keywords.filter(Boolean);
    return terms.length ? `The message mentions your company ${plural(terms.length, 'keyword')}: ${terms.join(', ')}.` : 'The message mentions your company keywords.';
  }
  return `A ${noun} from another agent or brand in your data.`;
}

/**
 * Why the list is in this order (tooltips and aria text). "Own" is relative to whoever sent the source buyer/listing,
 * not to the person viewing the page.
 */
export function ownershipOrderHint(kind: TargetKind) {
  const own = kind === 'buyer' ? 'buyers from the agent who sent this listing' : 'stock from the agent who sent this buyer';
  return `Listed first: ${own}, then your sales watchlist, then company posts, then other agents.`;
}

/** "House · Citraland / Wiyung · Land 200 m² · Building 150 m² · Rp 2,5 M" (sale and rent when offered for both). */
export function structuredSummary(row: Row) {
  const sale = priceText(row.price_min, row.price_max, row.price_basis);
  const rent = priceText(row.rent_price_min, row.rent_price_max, row.rent_price_basis ?? 'per_year');
  const price = row.transaction_type === 'both' && rent ? [sale && `Sale ${sale}`, `Rent ${rent}`].filter(Boolean).join(' · ') : sale;
  return [
    row.categories?.map(categoryLabel).join(', '),
    row.locations?.map(titleCase).join(' / '),
    area(row.land_area_min, row.land_area_max) && `Land ${area(row.land_area_min, row.land_area_max)}`,
    area(row.building_area_min, row.building_area_max) && `Building ${area(row.building_area_min, row.building_area_max)}`,
    price,
  ].filter(Boolean).join(' · ');
}

/** The parser sometimes keeps the signature label ("More info: Albert", "*Hubungi* Andi 📞"); show just the name. */
export function cleanName(name?: string | null) {
  let text = (name ?? '').split('\n')[0].replace(/[*_~`|]+/g, ' ').replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, ' ');
  const label = /^\s*(?:more\s+info(?:rmation)?|info(?:rmasi)?\s+lanjut|informasi|info\s+call|info|contact(?:\s+person)?|cp|kontak|hubungi|marketing|call|telp|tlp|hp|wa|whatsapp)\b\s*[:：\-–.]?\s*/i;
  for (let guard = 0; guard < 3 && label.test(text); guard += 1) text = text.replace(label, '');
  text = text.replace(/[\s:：\-–,.]*\+?\d[\d\s().-]{6,}$/, '');
  text = text.replace(/^[~\s:：\-–,.]+/, '').replace(/\s+/g, ' ').trim();
  // "albert" → "Albert"; names written with their own capitals are left alone.
  return text === text.toLowerCase() ? titleCase(text) : text;
}

/** WhatsApp author for display: "~ Budi Santoso, +62 812-3456-7890" → "Budi Santoso". */
export function displaySender(author?: string | null) {
  const text = (author ?? '').replace(/^[~\s]+/, '').replace(/,?\s*\+?\d[\d\s().-]{6,}$/, '').trim();
  return text || (author ?? '').replace(/^[~\s]+/, '').trim();
}

export function titleCase(text: string) {
  return text.replace(/\b\p{L}/gu, (char) => char.toUpperCase());
}

export function initials(name?: string | null) {
  const parts = (name ?? '').replace(/^[~\s]+/, '').trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase();
}

/* ------------------------------------------------------------------ phones + WhatsApp */

/** 6281234567890 -> +62 812-3456-7890 */
export function formatPhone(phone?: string | null) {
  const digits = (phone ?? '').replace(/\D/g, '');
  if (!digits) return '';
  const national = digits.startsWith('62') ? digits.slice(2) : digits.startsWith('0') ? digits.slice(1) : digits;
  const parts = [national.slice(0, 3), national.slice(3, 7), national.slice(7)].filter(Boolean);
  return `+62 ${parts.join('-')}`;
}

export function normalizePhone(value?: string | null) {
  const digits = (value ?? '').replace(/\D/g, '');
  if (digits.startsWith('0')) return `62${digits.slice(1)}`;
  if (digits.startsWith('8')) return `62${digits}`;
  return digits;
}

export function waLink(phone: string | null | undefined, text: string) {
  const digits = normalizePhone(phone);
  return digits ? `https://wa.me/${digits}?text=${encodeURIComponent(text)}` : null;
}

/* The prefilled WhatsApp message goes to another Indonesian agent, so it stays in Indonesian
   and uses the way they write prices ("Rp 1,8 M", "Rp 450 jt"). */
const CATEGORY_ID: Record<string, string> = {
  house: 'Rumah', warehouse: 'Gudang', apartment: 'Apartemen', shophouse: 'Ruko', land: 'Tanah', factory: 'Pabrik',
  office: 'Kantor', villa: 'Villa', commercial_building: 'Gedung komersial', hotel: 'Hotel',
};
const decimalId = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 2 });
function moneyId(value?: number | null) {
  const amount = Number(value);
  if (!amount) return null;
  if (amount >= 1e12) return `Rp ${decimalId.format(amount / 1e12)} T`;
  if (amount >= 1e9) return `Rp ${decimalId.format(amount / 1e9)} M`;
  if (amount >= 1e6) return `Rp ${decimalId.format(amount / 1e6)} jt`;
  return `Rp ${decimalId.format(amount)}`;
}
const BASIS_ID: Record<string, string> = { per_m2: '/m²', per_year: '/tahun', per_month: '/bulan' };
function summaryId(row: Row) {
  const areaId = (min?: number | null, max?: number | null) => (area(min, max) ?? '').replace(/,/g, '');
  const sale = moneyId(row.price_max ?? row.price_min);
  const rent = moneyId(row.rent_price_max ?? row.rent_price_min);
  const salePart = sale && `${sale}${BASIS_ID[row.price_basis ?? ''] ?? ''}`;
  const price = row.transaction_type === 'both' && rent
    ? [salePart && `Jual ${salePart}`, `Sewa ${rent}${BASIS_ID[row.rent_price_basis ?? 'per_year'] ?? ''}`].filter(Boolean).join(' · ')
    : salePart;
  return [
    row.categories?.map((key) => CATEGORY_ID[key] ?? key).join(', '),
    row.locations?.map(titleCase).join(' / '),
    (row.land_area_min || row.land_area_max) && `LT ${areaId(row.land_area_min, row.land_area_max)}`,
    (row.building_area_min || row.building_area_max) && `LB ${areaId(row.building_area_min, row.building_area_max)}`,
    price,
  ].filter(Boolean).join(' · ');
}

/** Prefilled follow-up message for wa.me links (Indonesian on purpose, see above). */
export function followUpMessage(row: Row, kind: 'buyer' | 'property') {
  const summary = summaryId(row) || messageText(row).slice(0, 500);
  const id = row.public_id ? ` (ID ${row.public_id})` : '';
  return `Halo ${cleanName(row.contact_name) || 'Bapak/Ibu'}, saya ingin menindaklanjuti ${kind === 'buyer' ? 'kebutuhan properti' : 'listing properti'}${id} yang Anda bagikan:\n\n${summary}\n\nApakah masih tersedia? Saya memiliki calon pasangan yang sesuai. Boleh saya meminta informasi lebih lanjut? Terima kasih.`;
}

/* ------------------------------------------------------------------ dates (always WIB) */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WIB_MS = 7 * 3_600_000;

/** Chat timestamps have no zone; they are Jakarta time. */
export function asWib(value?: string | null) {
  if (!value) return null;
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}+07:00`;
  const date = new Date(zoned);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** The calendar fields of a moment as seen in Jakarta (WIB has no daylight saving). */
function wibParts(date: Date) {
  const shifted = new Date(date.getTime() + WIB_MS);
  return { year: shifted.getUTCFullYear(), month: shifted.getUTCMonth(), day: shifted.getUTCDate(), hour: shifted.getUTCHours(), minute: shifted.getUTCMinutes() };
}
const wibDay = (time: number) => Math.floor((time + WIB_MS) / 86_400_000);
const pad = (value: number) => String(value).padStart(2, '0');

/** YYYY-MM-DD of a timestamp in Jakarta. */
export function isoDateWib(value?: string | Date | null) {
  const date = value instanceof Date ? value : asWib(value);
  if (!date) return '';
  const parts = wibParts(date);
  return `${parts.year}-${pad(parts.month + 1)}-${pad(parts.day)}`;
}

/** "Today", "Yesterday", "3 days ago", "2 weeks ago", "5 months ago". */
export function relativeDate(value?: string | null) {
  const date = asWib(value);
  if (!date) return 'Date not available';
  const days = wibDay(Date.now()) - wibDay(date.getTime());
  if (days < 0) return 'Upcoming';
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days} days ago`;
  if (days < 30) { const weeks = Math.floor(days / 7); return `${weeks} ${plural(weeks, 'week')} ago`; }
  if (days < 365) { const months = Math.floor(days / 30); return `${months} ${plural(months, 'month')} ago`; }
  const years = Math.floor(days / 365);
  return `${years} ${plural(years, 'year')} ago`;
}

/** "14:05" in Jakarta time. */
export function timeOnly(value?: string | null) {
  const date = asWib(value);
  if (!date) return '';
  const parts = wibParts(date);
  return `${pad(parts.hour)}:${pad(parts.minute)}`;
}

/** "12 Oct 2026, 14:05 WIB" (or without the zone). */
export function dateTime(value?: string | null, withZone = true) {
  const date = asWib(value);
  if (!date) return '—';
  const parts = wibParts(date);
  const text = `${parts.day} ${MONTHS[parts.month]} ${parts.year}, ${pad(parts.hour)}:${pad(parts.minute)}`;
  return withZone ? `${text} WIB` : text;
}

/** "Today 14:05 WIB", "Yesterday 09:12 WIB", otherwise "12 Oct 2026, 14:05 WIB". */
export function relativeDateTime(value?: string | null) {
  const date = asWib(value);
  if (!date) return '—';
  const days = wibDay(Date.now()) - wibDay(date.getTime());
  if (days === 0) return `Today ${timeOnly(value)} WIB`;
  if (days === 1) return `Yesterday ${timeOnly(value)} WIB`;
  return dateTime(value);
}

/** "12 Oct 2026" (long: "12 October 2026"). */
export function dateOnly(value?: string | null, long = false) {
  const date = asWib(value);
  if (!date) return '—';
  const parts = wibParts(date);
  return `${parts.day} ${(long ? MONTHS_LONG : MONTHS)[parts.month]} ${parts.year}`;
}

/** Jakarta calendar helpers (YYYY-MM-DD). */
export function todayWib() {
  return isoDateWib(new Date());
}
export function shiftDay(iso: string, days: number) {
  const date = new Date(`${iso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
export function shiftMonth(iso: string, months: number) {
  const date = new Date(`${iso}T12:00:00Z`);
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() + months);
  const last = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(day, last));
  return date.toISOString().slice(0, 10);
}
export function mondayOf(iso: string) {
  const date = new Date(`${iso}T12:00:00Z`);
  return shiftDay(iso, -((date.getUTCDay() + 6) % 7));
}
export function monthStart(iso: string) { return `${iso.slice(0, 8)}01`; }
export function monthEnd(iso: string) {
  const date = new Date(`${iso.slice(0, 8)}01T12:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + 1, 0);
  return date.toISOString().slice(0, 10);
}

export type DateRange = { from: string; to: string };
/** Monday–Sunday of the current week (weeks start on Monday). */
export function weekRange(today = todayWib()): DateRange { const monday = mondayOf(today); return { from: monday, to: shiftDay(monday, 6) }; }
/** Monday–Sunday of the previous week. */
export function lastWeekRange(today = todayWib()): DateRange { const monday = mondayOf(today); return { from: shiftDay(monday, -7), to: shiftDay(monday, -1) }; }
export function monthRange(today = todayWib()): DateRange { return { from: monthStart(today), to: monthEnd(today) }; }
export function lastMonthRange(today = todayWib()): DateRange { const end = shiftDay(monthStart(today), -1); return { from: monthStart(end), to: end }; }

/** "12 Oct 2026", "12–18 Oct 2026", "28 Sep – 4 Oct 2026", "28 Dec 2025 – 3 Jan 2026"; "All dates" when empty. */
export function prettyRange(from: string, to: string) {
  const parse = (iso: string) => (/^\d{4}-\d{2}-\d{2}$/.test(iso) ? { y: Number(iso.slice(0, 4)), m: Number(iso.slice(5, 7)) - 1, d: Number(iso.slice(8, 10)) } : null);
  const a = parse(from);
  const b = parse(to || from);
  const full = (p: { y: number; m: number; d: number }) => `${p.d} ${MONTHS[p.m]} ${p.y}`;
  if (!a) return b ? `Until ${full(b)}` : 'All dates';
  if (!b) return `From ${full(a)}`;
  if (a.y === b.y && a.m === b.m && a.d === b.d) return full(a);
  if (a.y === b.y && a.m === b.m) return `${a.d}–${b.d} ${MONTHS[a.m]} ${a.y}`;
  if (a.y === b.y) return `${a.d} ${MONTHS[a.m]} – ${b.d} ${MONTHS[b.m]} ${b.y}`;
  return `${full(a)} – ${full(b)}`;
}

/** "12 Oct" — short day label for chart axes. */
export function shortDay(iso: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return match ? `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]}` : iso;
}

export function greeting() {
  const hour = wibParts(new Date()).hour;
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

/* ------------------------------------------------------------------ member features */

/** Menu / feature names a company admin grants to a member (GET /team/features uses the same labels). */
export const FEATURE_LABELS: Record<Feature, string> = {
  home: 'Home', match: 'Match', recent: 'Recent matches', stock: 'Sales stock', buyers: 'Buyers',
  mark_status: 'Mark status', upload: 'Upload', settings: 'Settings', team: 'Team', jev: 'AI Review',
};

/* ------------------------------------------------------------------ companies (platform admin) */

/** Review state of a company as seen by the platform admin. */
export const COMPANY_STATUS_LABELS: Record<CompanyStatus, string> = { pending: 'Pending review', active: 'Active', inactive: 'Inactive', rejected: 'Rejected' };

/** HeroUI Chip colour for a company status. */
export function companyStatusColor(status: CompanyStatus): 'warning' | 'success' | 'default' | 'danger' {
  if (status === 'pending') return 'warning';
  if (status === 'active') return 'success';
  if (status === 'rejected') return 'danger';
  return 'default';
}
