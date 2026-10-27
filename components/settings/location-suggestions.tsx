'use client';

/*
 * "Unrecognized locations": place names that appear often in the company's chats but that
 * nothing recognises yet (GET /glossary/suggestions). One tap adds a phrase as a place
 * (glossary, optionally inside an area) or as another name of a cluster (POST /location-index/aliases).
 * "Ignore" only hides a phrase in this browser.
 */

import { useId, useMemo, useState } from 'react';
import { Button, Input, Label, TextField } from '@heroui/react';
import { EyeOff, MapPin, MapPinPlus, RefreshCw, Link2, Undo2 } from 'lucide-react';
import { EmptyState, ErrorNotice, Panel, SkeletonList } from '@/components/app/primitives';
import { ClusterPicker } from '@/components/settings/cluster-picker';
import { normText, PlaceOptions, type GlossaryState } from '@/components/settings/glossary-editor';
import { useSaveNotice } from '@/components/settings/reprocess-banner';
import { errorMessage } from '@/lib/api';
import { countLabel, number, titleCase } from '@/lib/format';
import { useCompany } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { GlossarySuggestion, GlossarySuggestionsResponse, LocationCluster, LocationWriteResponse } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

const SHOWN = 10;

function readIgnored(key: string): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(key) ?? '[]');
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
  } catch { return []; }
}
function writeIgnored(key: string, phrases: string[]) {
  try { window.localStorage.setItem(key, JSON.stringify(phrases.slice(-500))); } catch { /* private mode: keep it for this visit only */ }
}

export function LocationSuggestions({ glossary, onLocationsChanged }: { glossary: GlossaryState; onLocationsChanged?: () => void }) {
  const api = useApi();
  const { company } = useCompany();
  const storageKey = `xm.ignored-places.${company?.company_name ?? 'company'}`;
  const [version, setVersion] = useState(0);
  const [ignored, setIgnored] = useState<string[]>(() => readIgnored(storageKey));
  const [handled, setHandled] = useState<string[]>([]);
  const [showIgnored, setShowIgnored] = useState(false);
  const [limit, setLimit] = useState(SHOWN);
  const suggestions = useData((signal) => api.get<GlossarySuggestionsResponse>('/glossary/suggestions?limit=50', signal), [version]);

  const items = useMemo(() => (suggestions.data?.items ?? []).filter((item) => !handled.includes(item.phrase)), [suggestions.data, handled]);
  const open = items.filter((item) => !ignored.includes(item.phrase));
  const hidden = items.filter((item) => ignored.includes(item.phrase));
  const list = showIgnored ? hidden : open;
  const shown = list.slice(0, limit);

  function ignore(phrase: string, on: boolean) {
    const next = on ? [...new Set([...ignored, phrase])] : ignored.filter((item) => item !== phrase);
    setIgnored(next); writeIgnored(storageKey, next);
  }

  return (
    <Panel
      title="Unrecognized locations"
      description="Place names that come up often in your chats but are not recognised yet. Add them so buyers and listings in these places can be matched."
      bodyClassName="space-y-4"
    >
      <ErrorNotice message={suggestions.error} onRetry={suggestions.reload} title="The suggestions could not be loaded" />
      {suggestions.loading && !suggestions.data ? <SkeletonList rows={2} /> : list.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border">
          <EmptyState compact icon={<MapPin />}
            title={showIgnored ? 'Nothing is ignored' : 'Every frequent place name is recognised'}
            description={showIgnored ? 'Phrases you ignore are listed here, so you can bring them back.' : 'New suggestions appear after you upload more chats.'} />
        </div>
      ) : (
        <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border" aria-label={showIgnored ? 'Ignored place names' : 'Unrecognized place names'}>
          {shown.map((item) => (
            <SuggestionRow key={item.phrase} item={item} glossary={glossary} ignoredView={showIgnored}
              onIgnore={(on) => ignore(item.phrase, on)}
              onDone={(locations) => { setHandled((current) => [...current, item.phrase]); if (locations) onLocationsChanged?.(); }} />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {list.length > shown.length && <Button variant="secondary" size="sm" onPress={() => setLimit((value) => value + SHOWN)}>Show more ({number(list.length - shown.length)} left)</Button>}
        {(hidden.length > 0 || showIgnored) && (
          <Button variant="tertiary" size="sm" onPress={() => { setShowIgnored((value) => !value); setLimit(SHOWN); }}>
            {showIgnored ? 'Back to suggestions' : `Show ignored (${number(hidden.length)})`}
          </Button>
        )}
        <Button variant="tertiary" size="sm" isDisabled={suggestions.loading} onPress={() => { setHandled([]); setVersion((value) => value + 1); }}><RefreshCw className="size-4" aria-hidden="true" />Refresh</Button>
      </div>
    </Panel>
  );
}

type Mode = 'idle' | 'location' | 'alias';

function SuggestionRow({ item, glossary, ignoredView, onIgnore, onDone }: {
  item: GlossarySuggestion; glossary: GlossaryState; ignoredView: boolean;
  onIgnore: (on: boolean) => void; onDone: (locationsChanged: boolean) => void;
}) {
  const api = useApi();
  const notice = useSaveNotice();
  const listId = useId();
  const [mode, setMode] = useState<Mode>('idle');
  const [canonical, setCanonical] = useState(item.phrase);
  const [parent, setParent] = useState('');
  const [cluster, setCluster] = useState<LocationCluster | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const name = titleCase(item.phrase);

  async function addLocation() {
    const standard = normText(canonical);
    const area = normText(parent);
    if (!glossary.ready) { setError('The glossary has not loaded yet. Reload it, then try again.'); return; }
    if (!standard) { setError('Enter the standard place name.'); return; }
    if (area && (area === standard || area === normText(item.phrase))) { setError('A place cannot be inside itself.'); return; }
    setBusy(true); setError('');
    try {
      const result = await glossary.addNow({ term: item.phrase, canonical: standard, kind: 'location', parent: area || null, note: null });
      notice(`“${name}” added as a location`, { pending: result.changed === false ? false : result.reprocess?.pending !== false });
      onDone(false);
    } catch (reason) { setError(errorMessage(reason, 'The location could not be added. Please try again.')); } finally { setBusy(false); }
  }

  async function addAlias() {
    if (!cluster) { setError('Choose the cluster this name belongs to.'); return; }
    setBusy(true); setError('');
    try {
      const result = await api.post<LocationWriteResponse & { unchanged?: boolean; reprocess?: { pending?: boolean } }>('/location-index/aliases', { cluster_id: cluster.id, alias: item.phrase });
      notice(`“${name}” added as another name for ${cluster.name}`, { pending: result.unchanged ? false : result.reprocess?.pending !== false });
      onDone(true);
    } catch (reason) { setError(errorMessage(reason, 'The name could not be added. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <li className="space-y-3 px-4 py-3.5 sm:px-5">
      <div className="space-y-3">
        <div className="min-w-0">
          <p className="text-body font-semibold break-words text-foreground">{name}</p>
          <p className="text-caption text-muted">Seen in {countLabel(item.count, 'message')}</p>
          {item.sample && <p className="mt-1 line-clamp-2 text-sm break-words text-muted">“{item.sample}”</p>}
        </div>
        {mode === 'idle' && (
          <div className="flex flex-wrap gap-2">
            {ignoredView ? (
              <Button size="sm" variant="secondary" onPress={() => onIgnore(false)}><Undo2 className="size-4" aria-hidden="true" />Bring back</Button>
            ) : (
              <>
                <Button size="sm" variant="secondary" isDisabled={!glossary.ready} onPress={() => { setMode('location'); setError(''); }}><MapPinPlus className="size-4" aria-hidden="true" />Add as location</Button>
                <Button size="sm" variant="secondary" onPress={() => { setMode('alias'); setError(''); }}><Link2 className="size-4" aria-hidden="true" />Alias of cluster…</Button>
                <Button size="sm" variant="tertiary" onPress={() => onIgnore(true)}><EyeOff className="size-4" aria-hidden="true" />Ignore</Button>
              </>
            )}
          </div>
        )}
      </div>

      {mode === 'location' && (
        <form className="space-y-3 rounded-2xl border border-accent/30 bg-accent-soft/30 p-3 sm:p-4" onSubmit={(event) => { event.preventDefault(); void addLocation(); }} aria-label={`Add ${name} as a location`}>
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField value={canonical} onChange={setCanonical} fullWidth isRequired isDisabled={busy}>
              <Label className="text-sm font-semibold">Standard place name</Label>
              <Input maxLength={100} autoComplete="off" list={listId} />
            </TextField>
            <TextField value={parent} onChange={setParent} fullWidth isDisabled={busy}>
              <Label className="text-sm font-semibold">Inside area <span className="font-normal text-muted">(optional)</span></Label>
              <Input maxLength={100} placeholder="e.g. surabaya barat" autoComplete="off" list={listId} />
            </TextField>
          </div>
          <PlaceOptions id={listId} names={glossary.places} />
          <p className="text-caption text-muted">Keep the name as it is to add a new place, or type an existing place to treat “{item.phrase}” as another spelling of it.</p>
          {error && <p className="text-sm font-medium text-danger" role="alert">{error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="tertiary" isDisabled={busy} onPress={() => setMode('idle')}>Cancel</Button>
            <Button type="submit" isPending={busy} isDisabled={!glossary.ready}>Add location</Button>
          </div>
        </form>
      )}

      {mode === 'alias' && (
        <form className="space-y-3 rounded-2xl border border-accent/30 bg-accent-soft/30 p-3 sm:p-4" onSubmit={(event) => { event.preventDefault(); void addAlias(); }} aria-label={`Add ${name} as another name of a cluster`}>
          <ClusterPicker label={`Which cluster is “${name}”?`} value={cluster} onChange={(next) => { setCluster(next); setError(''); }} isDisabled={busy} />
          {error && <p className="text-sm font-medium text-danger" role="alert">{error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="tertiary" isDisabled={busy} onPress={() => setMode('idle')}>Cancel</Button>
            <Button type="submit" isPending={busy} isDisabled={!cluster}>Add as other name</Button>
          </div>
        </form>
      )}
    </li>
  );
}
