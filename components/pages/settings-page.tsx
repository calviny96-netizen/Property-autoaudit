'use client';

import { Tabs } from '@heroui/react';
import { PageHeader } from '@/components/app/primitives';
import { AccountTab } from '@/components/settings/account-tab';
import { AiKeysTab } from '@/components/settings/ai-keys-tab';
import { GeneralTab } from '@/components/settings/general-tab';
import { IntegrationTab } from '@/components/settings/integration-tab';
import { KnowledgeTab } from '@/components/settings/knowledge-tab';
import { MatchingTab } from '@/components/settings/matching-tab';
import { ReprocessBanner, ReprocessProvider } from '@/components/settings/reprocess-banner';
import { SalesTab } from '@/components/settings/sales-tab';
import { ROUTES, useRoute, type Navigate } from '@/lib/router';
import { useCompany, useSession } from '@/lib/session';

/* Tab ids are internal and stay stable for links (the shell's "Account" opens tab=akun). */
const TABS = [
  { id: 'umum', label: 'General' },
  { id: 'pencocokan', label: 'Matching' },
  { id: 'glossary', label: 'Words & places' },
  { id: 'sales', label: 'Sales watchlist' },
] as const;

export default function SettingsPage({ params, navigate }: { params?: URLSearchParams; navigate?: Navigate }) {
  const { navigate: go } = useRoute();
  const { company } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const { user } = useSession();
  const jev = user.role === 'admin';
  const tabs = [...TABS, ...(admin ? [{ id: 'integrasi', label: 'Integration' }] : []), ...(jev ? [{ id: 'ai', label: 'AI keys' }] : []),
    { id: 'akun', label: 'My account' }];
  const requested = params?.get('tab');
  const active = tabs.some((tab) => tab.id === requested) ? (requested as string) : 'umum';
  const open = navigate ?? go;

  return (
    <ReprocessProvider>
      <div className="space-y-6">
        <PageHeader title="Settings"
          description="How matching recognises your buyers and listings, the words and places your team uses, and your account." />
        <ReprocessBanner />
        <Tabs selectedKey={active} onSelectionChange={(key) => open(ROUTES.settings, { tab: String(key) })} variant="secondary">
          <Tabs.ListContainer className="xm-scroll-x -mx-4 mb-6 px-4 sm:mx-0 sm:px-0">
            <Tabs.List aria-label="Settings sections">
              {tabs.map((tab) => <Tabs.Tab key={tab.id} id={tab.id} className="min-h-11 px-4 text-sm font-semibold whitespace-nowrap">{tab.label}<Tabs.Indicator /></Tabs.Tab>)}
            </Tabs.List>
          </Tabs.ListContainer>
          <Tabs.Panel id="umum"><GeneralTab /></Tabs.Panel>
          <Tabs.Panel id="pencocokan"><MatchingTab navigate={open} /></Tabs.Panel>
          <Tabs.Panel id="glossary"><KnowledgeTab /></Tabs.Panel>
          <Tabs.Panel id="sales"><SalesTab navigate={open} /></Tabs.Panel>
          {admin && <Tabs.Panel id="integrasi"><IntegrationTab /></Tabs.Panel>}
          {jev && <Tabs.Panel id="ai"><AiKeysTab /></Tabs.Panel>}
          <Tabs.Panel id="akun"><AccountTab /></Tabs.Panel>
        </Tabs>
      </div>
    </ReprocessProvider>
  );
}
