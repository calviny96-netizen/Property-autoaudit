'use client';

import { useState } from 'react';
import { Button, Chip } from '@heroui/react';
import { Bot, ChevronRight, KeyRound, RefreshCw } from 'lucide-react';
import { EmptyState, ErrorNotice, LoadingRows, Notice, PageHeader, Panel } from '@/components/app/primitives';
import { NewReview } from '@/components/jev/new-review';
import { RunView } from '@/components/jev/run-view';
import { countLabel, number, relativeDateTime } from '@/lib/format';
import { RUN_STATUS_COLORS, RUN_STATUS_LABELS, isActiveRun, usd } from '@/lib/jev';
import { ROUTES, ROUTE_PARAMS, useRoute, type Navigate } from '@/lib/router';
import { useCompany, useSession } from '@/lib/session';
import type { AiKey, AiModels, JevRun } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';

/** AI Review (super admins): an AI second opinion on the pairs the matching rules found. */
export default function JevPage({ params, navigate }: { params?: URLSearchParams; navigate?: Navigate }) {
  const { navigate: go } = useRoute();
  const open = navigate ?? go;
  const { company } = useCompany();
  const runId = params?.get(ROUTE_PARAMS.run) ?? '';
  if (!company?.permissions.jev_review) return <Notice>Only company admins can use AI Review.</Notice>;
  if (runId) return <RunView key={runId} runId={runId} onBack={() => open(ROUTES.jev)} />;
  return <Overview onOpenRun={(id) => open(ROUTES.jev, { [ROUTE_PARAMS.run]: id })} onOpenKeys={() => open(ROUTES.settings, { [ROUTE_PARAMS.tab]: 'ai' })} />;
}

function Overview({ onOpenRun, onOpenKeys }: { onOpenRun: (id: string) => void; onOpenKeys: () => void }) {
  const api = useApi();
  const { user } = useSession();
  const technical = user.role === 'admin';
  // "Reload" asks the server to fetch the OpenRouter list again instead of its one-hour copy.
  const [forced, setForced] = useState(0);
  const models = useData((signal) => api.get<AiModels>(`/ai/models${forced ? '?refresh=true' : ''}`, signal), [forced]);
  const keys = useData((signal) => api.get<{ keys: AiKey[] }>('/ai/keys', signal), [technical], technical);
  const runs = useData((signal) => api.get<{ runs: JevRun[] }>('/ai/review-runs', signal), []);
  const list = runs.data?.runs ?? [];
  const keyList = keys.data?.keys ?? [];

  return (
    <div className="space-y-6">
      <PageHeader title="AI Review"
        description="Jev is an AI reviewer. It reads the pairs the matching rules found, the way an experienced agent would, and tells you which ones are worth recommending." />
      {technical && keys.data && keyList.length === 0 && (
        <Notice status="warning" title="Add an OpenRouter API key first" action={<Button size="sm" onPress={onOpenKeys}><KeyRound className="size-4" aria-hidden="true" />Open AI keys</Button>}>
          Jev uses your company’s OpenRouter account. Keys are stored encrypted and are never shown again.
        </Notice>
      )}
      <ErrorNotice message={keys.error} onRetry={keys.reload} title="The API keys could not be loaded" />
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <NewReview models={models.data} modelsError={models.error} modelsLoading={models.loading} reloadModels={() => setForced((value) => value + 1)}
          keys={keyList} onStarted={(run) => onOpenRun(run.id)} onOpenKeys={onOpenKeys} />
        <Panel title="Reviews" description="Newest first." flush
          actions={<Button isIconOnly size="sm" variant="ghost" aria-label="Refresh the reviews" isDisabled={runs.loading} onPress={runs.reload}><RefreshCw className="size-4" /></Button>}>
          <div className="px-4 pb-4 sm:px-6">
            <ErrorNotice message={runs.error} onRetry={runs.reload} />
            {runs.loading && !runs.data && <LoadingRows rows={2} />}
          </div>
          {runs.data && list.length === 0 && <EmptyState compact icon={<Bot />} title="No reviews yet" description="Start your first review on the left." />}
          {list.length > 0 && (
            <ul className="divide-y divide-separator border-t border-separator">
              {list.map((run) => (
                <li key={run.id}>
                  <button type="button" onClick={() => onOpenRun(run.id)} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-surface-secondary/70 sm:px-6">
                    <span className="min-w-0 flex-1 space-y-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-foreground">{run.model_label}</span>
                        <Chip size="sm" variant="soft" color={RUN_STATUS_COLORS[run.status]}>{RUN_STATUS_LABELS[run.status]}</Chip>
                      </span>
                      <span className="block text-caption text-muted">{relativeDateTime(run.created_at)}{run.created_by_name ? ` · ${run.created_by_name}` : ''}</span>
                      <span className="block text-caption text-muted">
                        {isActiveRun(run.status) ? `${number(run.done + run.failed)} of ${countLabel(run.total, 'pair')}` : countLabel(run.total, 'pair')}
                        {' · '}<span className="font-semibold text-success">✓ {number(run.recommend)}</span>
                        {' · '}<span className="font-semibold text-danger">✗ {number(run.reject)}</span>
                        {' · '}? {number(run.unsure)} · {usd(run.cost, technical)}
                      </span>
                    </span>
                    <ChevronRight className="mt-1 size-4 shrink-0 text-muted" aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
