'use client';

import dynamic from 'next/dynamic';
import { Spinner, Toast } from '@heroui/react';
import { AdminShell } from '@/components/app/admin-shell';
import { AppShell } from '@/components/app/app-shell';
import { InactiveScreen, PendingScreen, SetPasswordScreen } from '@/components/app/blocked-screens';
import { Notice, SkeletonList } from '@/components/app/primitives';
import { LockedScreen } from '@/components/app/locked-screen';
import { LoginScreen } from '@/components/app/login-screen';
import { ROUTES, useRoute } from '@/lib/router';
import { CompanyProvider, ManageProvider, SessionProvider, useAuth, useCompany, useManage, useSession } from '@/lib/session';
import type { Permissions } from '@/lib/types';

const loading = () => <SkeletonList rows={3} />;
const DashboardPage = dynamic(() => import('@/components/pages/dashboard-page'), { ssr: false, loading });
const MatchPage = dynamic(() => import('@/components/pages/match-page'), { ssr: false, loading });
const RecentPage = dynamic(() => import('@/components/pages/recent-page'), { ssr: false, loading });
const StockPage = dynamic(() => import('@/components/pages/stock-page'), { ssr: false, loading });
const BuyersPage = dynamic(() => import('@/components/pages/buyers-page'), { ssr: false, loading });
const UploadPage = dynamic(() => import('@/components/pages/upload-page'), { ssr: false, loading });
const TeamPage = dynamic(() => import('@/components/pages/team-page'), { ssr: false, loading });
const SettingsPage = dynamic(() => import('@/components/pages/settings-page'), { ssr: false, loading });
const CompaniesPage = dynamic(() => import('@/components/pages/companies-page'), { ssr: false, loading });
const JevPage = dynamic(() => import('@/components/pages/jev-page'), { ssr: false, loading });
const UsagePage = dynamic(() => import('@/components/pages/usage-page'), { ssr: false, loading });
const AccountPage = dynamic(() => import('@/components/pages/account-page'), { ssr: false, loading });

/** The five main pages a company admin can switch off per member, in the order the sidebar shows them. */
const MAIN_PAGES = [ROUTES.home, ROUTES.match, ROUTES.recent, ROUTES.stock, ROUTES.buyers] as const;
const PAGE_FLAG: Record<(typeof MAIN_PAGES)[number], keyof NonNullable<Permissions['pages']>> = {
  [ROUTES.home]: 'home', [ROUTES.match]: 'match', [ROUTES.recent]: 'recent', [ROUTES.stock]: 'stock', [ROUTES.buyers]: 'buyers',
};

/** Which main page to show for `page`, honouring `permissions.pages`; null = none allowed. */
function allowedMainPage(page: string, pages: Permissions['pages'] | undefined): string | null {
  const main = MAIN_PAGES.find((id) => id === page) ?? ROUTES.home;
  if (!pages) return main;
  if (pages[PAGE_FLAG[main]] !== false) return main;
  return MAIN_PAGES.find((id) => pages[PAGE_FLAG[id]] !== false) ?? null;
}

/** Everything inside one company: the normal shell for members, company admins, or the platform admin managing a company. */
function CompanyWorkspace() {
  const { path, params, navigate } = useRoute();
  const { user } = useSession();
  const { company } = useCompany();
  const perms = company?.permissions;

  // Pages a role may not open fall back to Home rather than showing an error.
  let page = path;
  if (!company) page = ROUTES.home;
  else if (page === ROUTES.upload && !perms?.upload_data) page = ROUTES.home;
  else if (page === ROUTES.team && !perms?.manage_team) page = ROUTES.home;
  else if (page === ROUTES.companies && user.role !== 'admin') page = ROUTES.home;
  else if (page === ROUTES.jev && !perms?.jev_review) page = ROUTES.home;
  else if ((page === ROUTES.usage || page === ROUTES.account) && user.role !== 'admin') page = ROUTES.home;

  // Main pages the company admin switched off for this member → the first one that is still on.
  let blocked = false;
  if (company && (MAIN_PAGES as readonly string[]).includes(page)) {
    const allowed = allowedMainPage(page, perms?.pages);
    if (allowed === null) blocked = true;
    else page = allowed;
  }

  const body = (() => {
    if (blocked) return <Notice status="warning">Your account has no pages enabled yet. Ask your company admin.</Notice>;
    switch (page) {
      case ROUTES.match: return <MatchPage params={params} navigate={navigate} />;
      case ROUTES.recent: return <RecentPage navigate={navigate} />;
      case ROUTES.stock: return <StockPage navigate={navigate} />;
      case ROUTES.buyers: return <BuyersPage navigate={navigate} />;
      case ROUTES.upload: return <UploadPage navigate={navigate} />;
      case ROUTES.team: return <TeamPage />;
      case ROUTES.settings: return <SettingsPage params={params} navigate={navigate} />;
      case ROUTES.companies: return <CompaniesPage />;
      case ROUTES.usage: return <UsagePage params={params} navigate={navigate} />;
      case ROUTES.account: return <AccountPage />;
      case ROUTES.jev: return <JevPage params={params} navigate={navigate} />;
      default: return <DashboardPage navigate={navigate} params={params} />;
    }
  })();
  return <AppShell route={page} navigate={navigate}>{body}</AppShell>;
}

/** The platform admin's own area: companies, usage and logs, the admin's account. No company pages. */
function AdminWorkspace() {
  const { path, params, navigate } = useRoute();
  const page = path === ROUTES.usage || path === ROUTES.account ? path : ROUTES.companies;
  const body = (() => {
    switch (page) {
      case ROUTES.usage: return <UsagePage params={params} navigate={navigate} />;
      case ROUTES.account: return <AccountPage />;
      default: return <CompaniesPage />;
    }
  })();
  return <AdminShell route={page} navigate={navigate}>{body}</AdminShell>;
}

function Workspace() {
  const { user } = useSession();
  const { target } = useManage();
  if (user.role === 'admin' && target === null) return <AdminWorkspace />;
  return <CompanyWorkspace />;
}

export default function Home() {
  const { user, setUser, logout, failed, refreshUser } = useAuth();
  let screen;
  if (user === undefined) {
    screen = (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-5 bg-background" aria-busy="true">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brand-auto-audit.png" alt="Property Auto Audit" width={1024} height={418} className="h-10 w-auto" />
        <Spinner size="md" color="accent" aria-label="Loading the app" />
      </main>
    );
  } else if (!user) {
    screen = <LoginScreen onLogin={setUser} offline={failed} />;
  } else if (user.is_locked) {
    screen = <LockedScreen onLogout={logout} />;
  } else if (user.must_change_password) {
    screen = <SetPasswordScreen user={user} onDone={() => { void refreshUser(); }} onLogout={logout} />;
  } else if (user.role !== 'admin' && user.company_status === 'pending') {
    screen = <PendingScreen user={user} onLogout={logout} />;
  } else if (user.role !== 'admin' && (user.company_status === 'inactive' || user.company_status === 'rejected')) {
    screen = <InactiveScreen status={user.company_status} user={user} onLogout={logout} />;
  } else {
    screen = (
      <SessionProvider key={user.id} user={user} logout={logout}>
        <ManageProvider user={user}>
          <CompanyProvider>
            <Workspace />
          </CompanyProvider>
        </ManageProvider>
      </SessionProvider>
    );
  }
  return <>{screen}<Toast.Provider placement="bottom end" /></>;
}
