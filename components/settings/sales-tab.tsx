'use client';

import { Button } from '@heroui/react';
import { ArrowRight, PhoneCall } from 'lucide-react';
import { ErrorNotice, Panel, SkeletonList } from '@/components/app/primitives';
import { TrackedManager } from '@/components/app/tracked-editor';
import { ROUTES, type Navigate } from '@/lib/router';
import { useCompany } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { StockOverview } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

/** Settings → Sales watchlist: the company-wide list of sales numbers, edited row by row. */
export function SalesTab({ navigate }: { navigate?: Navigate }) {
  const api = useApi();
  const { company } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const overview = useData((signal) => api.get<StockOverview>('/stock/overview', signal), []);
  const tracked = overview.data?.tracked ?? [];

  return (
    <div className="space-y-6">
      <Panel
        title={<span className="flex items-center gap-2"><PhoneCall className="size-5 text-accent" aria-hidden="true" />Sales watchlist</span>}
        description="The sales numbers your company follows. Their listings are counted on every upload and whenever a status changes. In Sales mode, only their buyers and listings are matched."
      >
        <ErrorNotice message={overview.error} onRetry={overview.reload} />
        {overview.loading && !overview.data ? <SkeletonList rows={2} /> : overview.data && (
          <TrackedManager items={tracked} maxTracked={overview.data.max_tracked} canEdit={admin} onChange={(next) => overview.setData(next)} />
        )}
        {!admin && <p className="mt-4 text-caption text-muted">Only company admins can change the watchlist.</p>}
        {tracked.length > 0 && <Button className="mt-4" variant="tertiary" onPress={() => navigate?.(ROUTES.stock)}>Open Sales stock<ArrowRight className="size-4" aria-hidden="true" /></Button>}
      </Panel>
    </div>
  );
}
