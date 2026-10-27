'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Avatar, Button, Drawer, Spinner, toast, useOverlayState } from '@heroui/react';
import { Bot, Boxes, Building2, ChevronRight, LayoutDashboard, LogOut, Menu, Settings, Sparkles, UploadCloud, UserRound, UserSearch, Users, Workflow } from 'lucide-react';
import { etaShort } from '@/components/app/processing-progress';
import { DATA_CHANGED_EVENT, type DataChangedDetail } from '@/lib/api';
import { ROLE_LABELS, initials } from '@/lib/format';
import { ROUTES } from '@/lib/router';
import { useCompany, useManage, useSession } from '@/lib/session';
import type { JobProgress, MaintenanceJob, ReprocessStatus } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

type NavItem = { id: string; label: string; short?: string; icon: typeof LayoutDashboard; badge?: number; hint?: string };

/* ------------------------------------------------- "matches not updated yet" (saving ≠ reprocessing) */

type ResultsState = { state: 'pending' | 'running' | null; reasons: string; admin: boolean; progress: JobProgress | null };

const isRunning = (job?: Pick<MaintenanceJob, 'status'> | { status?: string } | null) => job?.status === 'queued' || job?.status === 'processing';
/** Writes that never change whether results are out of date (searches, exports, "seen" marks, sign-in). */
const READ_LIKE_WRITE = /^\/(?:workspace\/recommendations|buyers\/recommendations|export\/|matches\/recent\/seen|entities\/status|location-index\/preview|auth\/)/;
const PENDING_TITLE = 'Matches not updated yet';

/**
 * Keeps the shell's small "not updated yet" indicator fresh: on load, every minute (every few seconds
 * while results update), when the tab comes back, and right after any save. The Settings page shows the
 * full banner and polls by itself, so the shell steps back there (no double polling, no double toasts).
 */
function useResultsState(route: string): ResultsState {
  const api = useApi();
  const { company, refresh: refreshCompany } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const enabled = !!company && route !== ROUTES.settings;
  const [status, setStatus] = useState<ReprocessStatus | null>(null);
  const ticket = useRef(0);
  const lastLoad = useRef(0);
  const job = status?.job ?? null;
  const running = isRunning(job);

  const load = useCallback(() => {
    const mine = ++ticket.current;
    lastLoad.current = Date.now();
    void api.get<ReprocessStatus>('/reprocess/status').then((next) => { if (mine === ticket.current) setStatus(next); }).catch(() => {});
  }, [api]);

  useEffect(() => {
    if (!enabled) return;
    load();
    let soon = 0;
    const onWrite = (event: Event) => {
      const path = (event as CustomEvent<DataChangedDetail>).detail?.path ?? '';
      if (READ_LIKE_WRITE.test(path)) return;
      window.clearTimeout(soon);
      soon = window.setTimeout(load, 600);
    };
    const onBack = () => { if (document.visibilityState === 'visible' && Date.now() - lastLoad.current > 5000) load(); };
    window.addEventListener(DATA_CHANGED_EVENT, onWrite);
    window.addEventListener('focus', onBack);
    document.addEventListener('visibilitychange', onBack);
    return () => {
      ticket.current += 1; // ignore answers that arrive after the shell stepped back
      setStatus(null);
      window.clearTimeout(soon);
      window.removeEventListener(DATA_CHANGED_EVENT, onWrite);
      window.removeEventListener('focus', onBack);
      document.removeEventListener('visibilitychange', onBack);
    };
  }, [enabled, load]);

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') load(); }, running ? 5000 : 60_000);
    return () => window.clearInterval(timer);
  }, [enabled, running, load]);

  // Announce an update we watched finish (people may have left Settings while it ran).
  const watched = useRef<{ id?: string; status?: string }>({});
  useEffect(() => {
    const before = watched.current;
    watched.current = { id: job?.id, status: job?.status };
    if (!job || before.id !== job.id || !isRunning(before)) return;
    if (job.status === 'completed') { toast.success('Results updated'); refreshCompany(); }
    if (job.status === 'failed' && admin) toast.danger('Results could not be updated', { description: 'Open Settings to try again.' });
  }, [job, admin, refreshCompany]);

  // Server reasons are short English phrases; "location index" is internal wording.
  const reasons = (status?.reasons ?? []).map((reason) => reason.replace(/location index/i, 'Locations')).join(' · ');
  return { state: running ? 'running' : status?.pending ? 'pending' : null, reasons, admin, progress: running ? job?.progress ?? null : null };
}

/** "42% · ~8 min" from the live progress (blank until the job reports something). */
function progressText(progress: JobProgress | null, long = false) {
  if (!progress) return '';
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent ?? 0)));
  const eta = progress.stage === 'queued' ? progress.estimate_s ?? progress.eta_s : progress.eta_s;
  const time = etaShort(eta);
  return `${percent}%${time ? ` · ${long ? '~' : ''}${time}` : ''}`;
}

/** Desktop sidebar card above the account card. */
function ResultsCard({ results, onOpen }: { results: ResultsState; onOpen: () => void }) {
  if (results.state === 'running') {
    return (
      <button type="button" onClick={onOpen} className="flex w-full items-center gap-2.5 rounded-xl border border-accent/15 bg-accent-soft/60 px-3 py-2.5 text-left text-accent-soft-foreground hover:bg-accent-soft">
        <Spinner size="sm" color="accent" />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold">Updating…{results.progress ? ` ${progressText(results.progress, true)}` : ''}</span>
          <span className="block truncate text-caption">{results.progress?.stage_label || 'You can keep working.'}</span>
        </span>
      </button>
    );
  }
  if (results.state !== 'pending') return null;
  return (
    <button type="button" onClick={onOpen} title={results.reasons || undefined}
      className="flex w-full items-start gap-2.5 rounded-xl border border-warning/50 bg-warning-soft px-3 py-2.5 text-left text-warning-soft-foreground hover:border-warning">
      <span aria-hidden="true" className="mt-1.5 size-2 shrink-0 rounded-full bg-warning" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold">{PENDING_TITLE}</span>
        <span className="block truncate text-caption">{results.reasons || 'Settings were changed.'}</span>
        <span className="mt-1 block text-caption font-semibold text-foreground">{results.admin ? 'Reprocess in Settings' : 'An admin needs to reprocess'}</span>
      </span>
      <ChevronRight className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
    </button>
  );
}

/** Phone / tablet top bar pill. */
function ResultsPill({ results, onOpen }: { results: ResultsState; onOpen: () => void }) {
  if (!results.state) return null;
  const running = results.state === 'running';
  return (
    <button type="button" onClick={onOpen}
      aria-label={running ? 'Updating results. Open Settings' : `${PENDING_TITLE}. ${results.admin ? 'Open Settings to reprocess' : 'An admin needs to reprocess'}`}
      className={cn('inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-caption font-semibold',
        running ? 'bg-accent-soft text-accent-soft-foreground' : 'bg-warning-soft text-warning-soft-foreground')}>
      {running ? <Spinner size="sm" color="accent" /> : <span aria-hidden="true" className="size-2 rounded-full bg-warning" />}
      {running ? progressText(results.progress) || 'Updating…' : 'Not updated'}
    </button>
  );
}

/* ------------------------------------------------------------------ navigation */

function useNav(settingsHint?: string) {
  const { company, unseen } = useCompany();
  const { user } = useSession();
  const { target } = useManage();
  const perms = company?.permissions;
  // Members see only the pages their company admin granted; until the settings arrive (or on an older server) every page shows.
  const pages = perms?.pages;
  const allowed = (page: keyof NonNullable<typeof pages>) => !pages || pages[page] !== false;
  const main: NavItem[] = [
    ...(allowed('home') ? [{ id: ROUTES.home, label: 'Home', icon: LayoutDashboard }] : []),
    ...(allowed('match') ? [{ id: ROUTES.match, label: 'Match', icon: Workflow }] : []),
    ...(allowed('recent') ? [{ id: ROUTES.recent, label: 'Recent matches', short: 'Recent', icon: Sparkles, badge: unseen }] : []),
    ...(allowed('stock') ? [{ id: ROUTES.stock, label: 'Sales stock', short: 'Stock', icon: Boxes }] : []),
    ...(allowed('buyers') ? [{ id: ROUTES.buyers, label: 'Buyers', icon: UserSearch }] : []),
  ];
  const manage: NavItem[] = [
    ...(perms?.upload_data ? [{ id: ROUTES.upload, label: 'Upload', icon: UploadCloud }] : []),
    ...(perms?.manage_team ? [{ id: ROUTES.team, label: 'Team', icon: Users }] : []),
    ...(perms?.jev_review ? [{ id: ROUTES.jev, label: 'AI Review', icon: Bot }] : []),
    { id: ROUTES.settings, label: 'Settings', icon: Settings, hint: settingsHint },
  ];
  const platform: NavItem[] = user.role === 'admin' ? [{ id: ROUTES.companies, label: target ? 'All companies' : 'Companies', icon: Building2 }] : [];
  return { main, manage, platform };
}

function Logo({ className }: { className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src="/brand-auto-audit.png" alt="Property Auto Audit" width={1024} height={418} className={cn('h-9 w-auto object-contain', className)} />;
}

function Badge({ count, inverted }: { count: number; inverted?: boolean }) {
  return (
    <span className={cn('inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[0.6875rem] font-bold tabular-nums', inverted ? 'bg-accent text-accent-foreground' : 'bg-hot text-white')}>
      {count > 99 ? '99+' : count}<span className="sr-only"> new matches not seen yet</span>
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
      {item.hint && <><span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-warning" /><span className="sr-only">: {item.hint}</span></>}
    </button>
  );
}

function NavGroup({ label, items, route, onNavigate }: { label?: string; items: NavItem[]; route: string; onNavigate: (id: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="space-y-0.5">
      {label && <p className="px-3 pt-5 pb-1.5 text-overline font-semibold text-muted uppercase">{label}</p>}
      {items.map((item) => <NavLink key={item.id} item={item} active={route === item.id} onNavigate={onNavigate} />)}
    </div>
  );
}

function UserCard({ onAccount, onLogout }: { onAccount: () => void; onLogout: () => void }) {
  const { user } = useSession();
  const { company } = useCompany();
  return (
    <div className="rounded-2xl border border-border bg-surface-secondary/60 p-3">
      <div className="flex items-center gap-3">
        <Avatar color="accent" size="sm" className="shrink-0"><Avatar.Fallback>{initials(user.display_name)}</Avatar.Fallback></Avatar>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-foreground">{user.display_name}</p>
          <p className="truncate text-caption text-muted">{ROLE_LABELS[user.role]}{company?.company_name ? ` · ${company.company_name}` : ''}</p>
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

function ManagingBanner() {
  const { target, leave } = useManage();
  const { user } = useSession();
  if (!target || user.role !== 'admin') return null;
  return (
    <div className="sticky top-16 z-20 mb-5 flex items-center justify-between gap-3 rounded-xl border border-warning/60 bg-[color-mix(in_oklab,var(--warning)_22%,var(--surface))] py-1.5 pr-1.5 pl-3.5 text-foreground shadow-md lg:top-4">
      <p className="min-w-0 truncate text-sm"><Building2 className="mr-1.5 inline size-4 align-[-3px]" aria-hidden="true" /><span className="hidden sm:inline">You are managing </span><strong className="font-semibold">{target.name}</strong></p>
      <Button size="sm" variant="secondary" className="shrink-0" onPress={leave}>All companies</Button>
    </div>
  );
}

export function AppShell({ route, navigate, children }: { route: string; navigate: (path: string, params?: Record<string, string | undefined>) => void; children: ReactNode }) {
  const results = useResultsState(route);
  const pending = results.state === 'pending';
  const { main, manage, platform } = useNav(pending ? PENDING_TITLE : undefined);
  const { logout, user } = useSession();
  const { company, unseen } = useCompany();
  const menu = useOverlayState();
  const go = (id: string) => { menu.close(); navigate(id); };
  const openSettings = () => go(ROUTES.settings);
  const account = () => { menu.close(); navigate(ROUTES.settings, { tab: 'akun' }); };
  const inMore = [...manage, ...platform].some((item) => item.id === route);

  return (
    <div className="min-h-dvh">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-(--sidebar-width) flex-col border-r border-border bg-surface lg:flex">
        <div className="flex h-16 shrink-0 items-center px-5">
          <button type="button" onClick={() => navigate(ROUTES.home)} className="rounded-lg" aria-label="Go to Home"><Logo /></button>
        </div>
        <nav aria-label="Main menu" className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pt-2 pb-4">
          <NavGroup items={main} route={route} onNavigate={go} />
          <NavGroup label="Manage" items={manage} route={route} onNavigate={go} />
          <NavGroup label="Platform" items={platform} route={route} onNavigate={go} />
        </nav>
        <div className="shrink-0 space-y-2 p-3">
          <ResultsCard results={results} onOpen={openSettings} />
          <UserCard onAccount={account} onLogout={logout} />
        </div>
      </aside>

      {/* Phone / tablet top bar */}
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur lg:hidden">
        <button type="button" onClick={() => navigate(ROUTES.home)} className="shrink-0 rounded-lg" aria-label="Go to Home"><Logo className="h-7" /></button>
        <p className="min-w-0 flex-1 truncate text-right text-caption font-medium text-muted">{company?.company_name ?? ''}</p>
        <ResultsPill results={results} onOpen={openSettings} />
        <Button isIconOnly variant="tertiary" aria-label="Open menu" onPress={() => menu.open()}><Menu className="size-5" aria-hidden="true" /></Button>
      </header>

      <main className="px-4 pt-5 pb-28 sm:px-6 lg:ml-(--sidebar-width) lg:px-10 lg:pt-8 lg:pb-12">
        <div className="mx-auto max-w-[1280px]"><ManagingBanner />{children}</div>
      </main>

      {/* Phone bottom navigation: the granted main pages plus "More", always with visible labels */}
      <nav aria-label="Main menu" style={{ gridTemplateColumns: `repeat(${main.length + 1}, minmax(0, 1fr))` }}
        className="pb-safe fixed inset-x-0 bottom-0 z-30 grid border-t border-border bg-surface/95 px-1 pt-1 backdrop-blur lg:hidden">
        {main.map((item) => {
          const Icon = item.icon;
          const active = route === item.id;
          return (
            <button key={item.id} type="button" onClick={() => go(item.id)} aria-current={active ? 'page' : undefined}
              className={cn('relative flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-xl text-[0.75rem]', active ? 'font-semibold text-accent' : 'font-medium text-muted')}>
              <span className={cn('relative flex h-7 w-12 items-center justify-center rounded-full transition-colors', active && 'bg-accent-soft')}>
                <Icon className="size-5" aria-hidden="true" />
                {item.id === ROUTES.recent && unseen > 0 && <span className="absolute -top-1 right-0.5"><Badge count={unseen} /></span>}
              </span>
              {item.short ?? item.label}
            </button>
          );
        })}
        <button type="button" onClick={() => menu.open()} className={cn('flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-xl text-[0.75rem]', inMore ? 'font-semibold text-accent' : 'font-medium text-muted')}>
          <span className={cn('relative flex h-7 w-12 items-center justify-center rounded-full', inMore && 'bg-accent-soft')}>
            <Menu className="size-5" aria-hidden="true" />
            {pending && <span aria-hidden="true" className="absolute top-0.5 right-2.5 size-2 rounded-full bg-warning ring-2 ring-surface" />}
          </span>
          More{pending && <span className="sr-only">: {PENDING_TITLE}</span>}
        </button>
      </nav>

      <Drawer.Backdrop isOpen={menu.isOpen} onOpenChange={(open) => menu.setOpen(open)}>
        <Drawer.Content placement="bottom">
          <Drawer.Dialog className="max-h-[88dvh]">
            <Drawer.Handle />
            <Drawer.Header><Drawer.Heading>Menu</Drawer.Heading></Drawer.Header>
            <Drawer.Body className="space-y-4 pb-6">
              <div className="flex items-center gap-3 rounded-2xl bg-surface-secondary p-3">
                <Avatar color="accent"><Avatar.Fallback>{initials(user.display_name)}</Avatar.Fallback></Avatar>
                <div className="min-w-0">
                  <p className="truncate font-semibold">{user.display_name}</p>
                  <p className="truncate text-caption text-muted">{ROLE_LABELS[user.role]}{company?.company_name ? ` · ${company.company_name}` : ''}</p>
                </div>
              </div>
              <div className="divide-y divide-separator overflow-hidden rounded-2xl border border-border bg-surface">
                {[...manage, ...platform].map((item) => (
                  <button key={item.id} type="button" onClick={() => go(item.id)} aria-current={route === item.id ? 'page' : undefined}
                    className={cn('flex min-h-13 w-full items-center gap-3 px-4 text-left text-body font-medium', route === item.id && 'bg-accent-soft font-semibold text-accent-soft-foreground')}>
                    <item.icon className="size-5 text-accent" aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className="block">{item.label}</span>
                      {item.hint && <span className="flex items-center gap-1.5 text-caption font-semibold text-warning-soft-foreground"><span aria-hidden="true" className="size-2 rounded-full bg-warning" />{item.hint}</span>}
                    </span>
                    <ChevronRight className="size-4 text-muted" aria-hidden="true" />
                  </button>
                ))}
                <button type="button" onClick={account} className="flex min-h-13 w-full items-center gap-3 px-4 text-left text-body font-medium">
                  <UserRound className="size-5 text-accent" aria-hidden="true" /><span className="flex-1">My account</span><ChevronRight className="size-4 text-muted" aria-hidden="true" />
                </button>
              </div>
              <Button variant="danger-soft" size="lg" fullWidth onPress={() => { menu.close(); logout(); }}><LogOut className="size-4" aria-hidden="true" />Sign out</Button>
            </Drawer.Body>
          </Drawer.Dialog>
        </Drawer.Content>
      </Drawer.Backdrop>
    </div>
  );
}
