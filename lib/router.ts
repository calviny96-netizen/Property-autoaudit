'use client';

import { useCallback, useMemo, useSyncExternalStore } from 'react';

/**
 * Tiny hash router: `#/cocokkan?arah=property`. Works with a static export and keeps the back button useful.
 * Route ids stay as they were (old bookmarks and shared links keep working); new code should use ROUTES.
 * English paths such as `#/match` or `#/recent` are accepted too and mapped to the same pages.
 */
export const ROUTES = {
  home: 'beranda',
  match: 'cocokkan',
  recent: 'match-baru',
  stock: 'stok',
  buyers: 'buyers',
  upload: 'data',
  team: 'tim',
  settings: 'pengaturan',
  companies: 'perusahaan',
  jev: 'jev',
  /** Platform admin only: usage and the activity log per company. */
  usage: 'penggunaan',
  /** Platform admin only: the admin's own account (companies have Settings → My account). */
  account: 'akun',
} as const;
export type RouteId = (typeof ROUTES)[keyof typeof ROUTES];

/** Query keys used in links between pages (kept for compatibility with existing links). */
export const ROUTE_PARAMS = {
  /** Match: 'buyer' | 'property' */
  direction: 'arah',
  /** Match: a public ID such as L-AB908 */
  id: 'id',
  /** Match: sales phone number(s) */
  phone: 'nomor',
  /** Home: custom period start / end (YYYY-MM-DD) */
  from: 'dari',
  to: 'sampai',
  /** Settings: tab id */
  tab: 'tab',
  /** AI Review: the open run */
  run: 'run',
  /** Companies: which list (pending | active | inactive); Usage: company filter */
  status: 'status',
  company: 'company',
} as const;

const ALIASES: Record<string, RouteId> = {
  home: ROUTES.home, dashboard: ROUTES.home, match: ROUTES.match, recent: ROUTES.recent, 'recent-matches': ROUTES.recent,
  stock: ROUTES.stock, 'sales-stock': ROUTES.stock, 'buyer-requests': ROUTES.buyers, upload: ROUTES.upload, team: ROUTES.team, settings: ROUTES.settings, companies: ROUTES.companies,
  'jev-review': ROUTES.jev, usage: ROUTES.usage, logs: ROUTES.usage, account: ROUTES.account,
};

export type Route = { path: string; params: URLSearchParams };
export type Navigate = (path: string, params?: Record<string, string | undefined>) => void;

function subscribe(listener: () => void) {
  window.addEventListener('hashchange', listener);
  return () => window.removeEventListener('hashchange', listener);
}
const snapshot = () => window.location.hash;
const serverSnapshot = () => '';

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const [path, search = ''] = raw.split('?');
  const id = path || ROUTES.home;
  return { path: ALIASES[id] ?? id, params: new URLSearchParams(search) };
}

export function buildHash(path: string, params?: Record<string, string | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) if (value) search.set(key, value);
  const text = search.toString();
  return `#/${path}${text ? `?${text}` : ''}`;
}

export function useRoute() {
  const hash = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  const route = useMemo(() => parseHash(hash), [hash]);
  const navigate = useCallback((path: string, params?: Record<string, string | undefined>) => {
    window.location.hash = buildHash(path, params);
    window.scrollTo({ top: 0 });
  }, []);
  return { ...route, navigate };
}
