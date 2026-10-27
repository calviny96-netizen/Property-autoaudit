import type { AiModel, AiPricing, JevDecision, JevRunStatus } from '@/lib/types';

/* Labels and number formats of AI Review (UI English; Jev's own reasons stay Indonesian). */

export const DECISION_LABELS: Record<JevDecision, string> = { recommend: 'Recommended', reject: 'Rejected', unsure: 'Unsure' };
export const DECISION_SYMBOLS: Record<JevDecision, string> = { recommend: '✓', reject: '✗', unsure: '?' };
export const DECISION_STYLES: Record<JevDecision | 'failed' | 'pending', string> = {
  recommend: 'bg-success-soft text-success-soft-foreground',
  reject: 'bg-danger-soft text-danger-soft-foreground',
  unsure: 'bg-warning-soft text-warning-soft-foreground',
  failed: 'bg-default text-foreground',
  pending: 'bg-default text-muted',
};
export const RUN_STATUS_LABELS: Record<JevRunStatus, string> = {
  queued: 'Waiting to start', running: 'Reviewing', completed: 'Finished', failed: 'Stopped', cancelled: 'Cancelled',
};
export const RUN_STATUS_COLORS: Record<JevRunStatus, 'default' | 'accent' | 'success' | 'warning' | 'danger'> = {
  queued: 'default', running: 'accent', completed: 'success', failed: 'danger', cancelled: 'warning',
};
export const isActiveRun = (status?: JevRunStatus) => status === 'queued' || status === 'running';

/* USD → IDR rate typed by the admin in Settings → AI keys. Kept in this browser only (not in the database). */
const RATE_KEY = 'xm.ai.usd-idr-rate';
export const DEFAULT_USD_IDR_RATE = 30000;
export function usdIdrRate(): number {
  try {
    const value = Number(window.localStorage.getItem(RATE_KEY));
    return Number.isFinite(value) && value > 0 ? value : DEFAULT_USD_IDR_RATE;
  } catch {
    return DEFAULT_USD_IDR_RATE;
  }
}
export function setUsdIdrRate(value: number | null) {
  try {
    if (value && value > 0) window.localStorage.setItem(RATE_KEY, String(value));
    else window.localStorage.removeItem(RATE_KEY);
  } catch {
    /* storage blocked: the rate simply is not remembered */
  }
}
const rupiah = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 });

/** OpenRouter credits are US dollars: "$0.0042", "$1.25"; with a rate set: "$1.25 (≈ Rp 20.375)". */
export function usd(value: number | null | undefined, technical = false) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const dollars = value === 0 ? '$0' : value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
  const rate = usdIdrRate();
  if (!technical) return `Rp ${rupiah.format(value > 0 ? Math.max(1, Math.round(value * rate)) : 0)}`;
  return rate && value > 0 ? `${dollars} (≈ Rp ${rupiah.format(Math.max(1, Math.round(value * rate)))})` : dollars;
}

/** "$0.20 in · $0.80 out per 1M tokens", or "price varies" for routers and aliases. */
export function priceText(pricing: AiPricing | null | undefined) {
  if (!pricing) return 'price varies';
  const per = (value: number) => usd(value * 1e6);
  if (!pricing.completion) return `${per(pricing.prompt)} in per 1M tokens`; // decision models: input tokens only
  return `${per(pricing.prompt)} in · ${per(pricing.completion)} out per 1M tokens`;
}

export function modelName(model: Pick<AiModel, 'id' | 'name'> | undefined, id: string) {
  return model?.name ?? id;
}

export const percent = (value: number | null | undefined) => (value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`);

/** Text split into plain and highlighted pieces for Jev's evidence phrases (case and spacing tolerant). */
export function highlightPieces(text: string, phrases: readonly string[] | undefined) {
  const spans: [number, number][] = [];
  for (const phrase of phrases ?? []) {
    const words = phrase.split(/\s+/).filter(Boolean);
    if (!words.length) continue;
    const pattern = new RegExp(words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+'), 'i');
    const found = pattern.exec(text);
    if (found) spans.push([found.index, found.index + found[0].length]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  const pieces: { text: string; mark: boolean }[] = [];
  let last = 0;
  for (const [start, end] of spans) {
    if (end <= last) continue;
    const from = Math.max(start, last);
    if (from > last) pieces.push({ text: text.slice(last, from), mark: false });
    pieces.push({ text: text.slice(from, end), mark: true });
    last = end;
  }
  if (last < text.length) pieces.push({ text: text.slice(last), mark: false });
  return pieces;
}
