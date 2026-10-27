/** One place that talks to the Python API and turns failures into short, readable English messages. */

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export const UNAUTHORIZED_EVENT = 'xm:unauthorized';
export const LOCKED_EVENT = 'xm:locked';
/**
 * Fired on `window` when the API answers 403 with a code that means the account's state changed
 * (a temporary password, or the company is pending / inactive / rejected): the session re-reads /auth/me.
 */
export const REFRESH_USER_EVENT = 'xm:refresh-user';
const REFRESH_USER_CODES = new Set(['password_change_required', 'company_pending', 'company_inactive', 'company_rejected']);
/** Fired on `window` after every successful write (POST/PUT/PATCH/DELETE) to the API. */
export const DATA_CHANGED_EVENT = 'xm:data-changed';
export type DataChangedDetail = { path: string; method: string };

type Detail = string | { msg?: string }[] | undefined;

function readableDetail(detail: Detail, fallback: string) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    // Validation errors from the API: show the first one, not the raw structure.
    return 'Please check your input' + (detail[0]?.msg ? ` — ${detail[0].msg}` : '.');
  }
  return fallback;
}

export async function readResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!response.ok) {
    const data = (body ?? {}) as { detail?: Detail; code?: string };
    if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    if (response.status === 403 && data.code === 'account_locked' && typeof window !== 'undefined') window.dispatchEvent(new Event(LOCKED_EVENT));
    if (response.status === 403 && data.code && REFRESH_USER_CODES.has(data.code) && typeof window !== 'undefined') window.dispatchEvent(new Event(REFRESH_USER_EVENT));
    throw new ApiError(readableDetail(data.detail, response.status >= 500 ? 'Something went wrong on our side. Please try again in a moment.' : 'This request could not be completed.'), response.status, data.code);
  }
  return body as T;
}

export function query(params: Record<string, string | number | boolean | undefined | null>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    search.set(key, String(value));
  }
  return search.toString();
}

export function errorMessage(reason: unknown, fallback = 'Something went wrong. Please try again.') {
  if (reason instanceof Error && reason.name !== 'AbortError') return reason.message || fallback;
  return fallback;
}

export function isAbort(reason: unknown) {
  return reason instanceof DOMException && reason.name === 'AbortError';
}
