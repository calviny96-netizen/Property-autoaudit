'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Avatar } from '@heroui/react';
import { Activity, Building2, LogOut, UserRound, type LucideIcon } from 'lucide-react';
import { DATA_CHANGED_EVENT } from '@/lib/api';
import { ROLE_LABELS, initials } from '@/lib/format';
import { ROUTES } from '@/lib/router';
import { useSession } from '@/lib/session';
import type { CompanySummary } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

type NavItem = { id: string; label: string; short?: string; icon: LucideIcon; badge?: number };

/** How many companies are waiting for approval: on load, every 30 s, when the tab comes back, and after any save. */
function usePendingCount() {
  const api = useApi();
  const [count, setCount] = useState(0);
  const ticket = useRef(0);
  const load = useCallback(() => {
    const mine = ++ticket.current;
    void api.get<CompanySummary[]>('/admin/companies')
      .then((rows) => { if (mine === ticket.current) setCount(rows.filter((row) => row.status === 'pending').length); })
      .catch(() => {});
  }, [api]);
  useEffect(() => {
    load();
    let soon = 0;
    const onWrite = () => { window.clearTimeout(soon); soon = window.setTimeout(load, 600); };
    const onBack = () => { if (document.visibilityState === 'visible') load(); };
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') load(); }, 30_000);
    window.addEventListener(DATA_CHANGED_EVENT, onWrite);
    window.addEventListener('focus', onBack);
    document.addEventListener('visibilitychange', onBack);
    return () => {
      ticket.current += 1;
      window.clearTimeout(soon);
      window.clearInterval(timer);
      window.removeEventListener(DATA_CHANGED_EVENT, onWrite);
      window.removeEventListener('focus', onBack);
      document.removeEventListener('visibilitychange', onBack);
    };
  }, [load]);
  return count;
}

function Logo({ className }: { className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src="/brand-auto-audit.png" alt="Property Auto Audit" width={1024} height={418} className={cn('h-9 w-auto object-contain', className)} />;
}

function Badge({ count }: { count: number }) {
  return (
    <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-hot px-1.5 text-[0.6875rem] font-bold text-white tabular-nums">
      {count > 99 ? '99+' : count}<span className="sr-only"> registrations waiting for review</span>
    </span>
  );
}

function NavLink({ item, active, onNavigate }: { item: NavItem; active: boolean; onNavigate: (id: string) => void }) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={() => onNavigate(item.id)}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group relative flex min-h-10 w-full items-center gap-3 rounded-xl px-3 text-left text-[0.9375rem] transition-colors',
        active ? 'bg-accent-soft font-semibold text-accent-soft-foreground' : 'font-medium text-foreground/80 hover:bg-default hover:text-foreground')}
    >
      {active && <span aria-hidden="true" className="absolute top-2 bottom-2 -left-3 w-1 rounded-r-full bg-accent" />}
      <Icon className={cn('size-[1.125rem] shrink-0', active ? 'text-accent' : 'text-muted group-hover:text-foreground')} aria-hidden="true" />
      <span className="flex-1 truncate">{item.label}</span>
      {!!item.badge && <Badge count={item.badge} />}
    </button>
  );
}

function UserCard({ onAccount, onLogout }: { onAccount: () => void; onLogout: () => void }) {
  const { user } = useSession();
  return (
    <div className="rounded-2xl border border-border bg-surface-secondary/60 p-3">
      <div className="flex items-center gap-3">
        <Avatar color="accent" size="sm" className="shrink-0"><Avatar.Fallback>{initials(user.display_name)}</Avatar.Fallback></Avatar>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-foreground">{user.display_name}</p>
          <p className="truncate text-caption text-muted">{ROLE_LABELS[user.role]}</p>
        </div>
      </div>
      <div className="mt-2.5 grid grid-cols-2 gap-1.5">
        <button type="button" onClick={onAccount} className="flex min-h-9 items-center justify-center gap-1.5 rounded-lg text-caption font-semibold text-foreground/80 hover:bg-default hover:text-foreground">
          <UserRound className="size-3.5" aria-hidden="true" />Account
        </button>
        <button type="button" onClick={onLogout} className="flex min-h-9 items-center justify-center gap-1.5 rounded-lg text-caption font-semibold text-danger hover:bg-danger-soft">
          <LogOut className="size-3.5" aria-hidden="true" />Sign out
        </button>
      </div>
    </div>
  );
}

/**
 * The platform admin's own layout: companies, usage and logs, and the admin's account.
 * No company pages here; those appear once the admin opens a company (the normal AppShell).
 */
export function AdminShell({ route, navigate, children }: { route: string; navigate: (path: string, params?: Record<string, string | undefined>) => void; children: ReactNode }) {
  const pending = usePendingCount();
  const { logout, user } = useSession();
  const items: NavItem[] = [
    { id: ROUTES.companies, label: 'Companies', icon: Building2, badge: pending },
    { id: ROUTES.usage, label: 'Usage & logs', short: 'Usage', icon: Activity },
    { id: ROUTES.account, label: 'My account', short: 'Account', icon: UserRound },
  ];
  const go = (id: string) => navigate(id);

  return (
    <div className="min-h-dvh">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-(--sidebar-width) flex-col border-r border-border bg-surface lg:flex">
        <div className="flex h-16 shrink-0 items-center px-5">
          <button type="button" onClick={() => navigate(ROUTES.companies)} className="rounded-lg" aria-label="Go to Companies"><Logo /></button>
        </div>
        <nav aria-label="Main menu" className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pt-2 pb-4">
          <p className="px-3 pt-1 pb-2 text-overline font-semibold text-muted uppercase">Platform admin</p>
          <div className="space-y-0.5">
            {items.map((item) => <NavLink key={item.id} item={item} active={route === item.id} onNavigate={go} />)}
          </div>
        </nav>
        <div className="shrink-0 p-3">
          <UserCard onAccount={() => go(ROUTES.account)} onLogout={logout} />
        </div>
      </aside>

      {/* Phone / tablet top bar */}
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur lg:hidden">
        <button type="button" onClick={() => navigate(ROUTES.companies)} className="shrink-0 rounded-lg" aria-label="Go to Companies"><Logo className="h-7" /></button>
        <p className="min-w-0 flex-1 truncate text-right text-caption font-medium text-muted">Platform admin · {user.display_name}</p>
        <button type="button" onClick={logout} aria-label="Sign out" className="flex size-10 shrink-0 items-center justify-center rounded-lg text-danger hover:bg-danger-soft">
          <LogOut className="size-5" aria-hidden="true" />
        </button>
      </header>

      <main className="px-4 pt-5 pb-28 sm:px-6 lg:ml-(--sidebar-width) lg:px-10 lg:pt-8 lg:pb-12">
        <div className="mx-auto max-w-[1280px]">{children}</div>
      </main>

      {/* Phone bottom navigation: three pages, always with visible labels */}
      <nav aria-label="Main menu" className="pb-safe fixed inset-x-0 bottom-0 z-30 grid grid-cols-3 border-t border-border bg-surface/95 px-1 pt-1 backdrop-blur lg:hidden">
        {items.map((item) => {
          const Icon = item.icon;
          const active = route === item.id;
          return (
            <button key={item.id} type="button" onClick={() => go(item.id)} aria-current={active ? 'page' : undefined}
              className={cn('relative flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-xl text-[0.75rem]', active ? 'font-semibold text-accent' : 'font-medium text-muted')}>
              <span className={cn('relative flex h-7 w-12 items-center justify-center rounded-full transition-colors', active && 'bg-accent-soft')}>
                <Icon className="size-5" aria-hidden="true" />
                {!!item.badge && <span className="absolute -top-1 right-0.5"><Badge count={item.badge} /></span>}
              </span>
              {item.short ?? item.label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
