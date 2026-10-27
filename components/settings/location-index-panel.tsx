'use client';

/*
 * The company's locations: housing clusters, their other names, the area/development they belong
 * to, and which clusters are within 4 km of each other (GET /location-index and friends).
 * Admins add clusters, other names and nearby links here, or import a whole table.
 * Every change only saves; results follow on the next reprocess (see reprocess-banner).
 */

import { useId, useRef, useState } from 'react';
import { Button, Chip, Input, Label, Modal, SearchField, TextArea, TextField, toast, useOverlayState } from '@heroui/react';
import { FileSpreadsheet, MapPin, Pencil, Plus, Trash2, X } from 'lucide-react';
import { Collapsible, ConfirmDialog, EmptyState, ErrorNotice, Notice, PagerBar, Panel, SkeletonList } from '@/components/app/primitives';
import { ClusterPicker } from '@/components/settings/cluster-picker';
import { PlaceOptions } from '@/components/settings/glossary-editor';
import { useSaveNotice } from '@/components/settings/reprocess-banner';
import { errorMessage, query } from '@/lib/api';
import { countLabel, decimal, number } from '@/lib/format';
import { useData, useDebounced } from '@/lib/use-data';
import type {
  LocationAreasResponse, LocationCatalog, LocationCluster, LocationClusterInput, LocationClusterSaveResponse, LocationNeighbors,
  LocationPreview, LocationWriteResponse,
} from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

const PAGE = 30;
const MAX_NAME = 200;
type WriteResult = LocationWriteResponse & { unchanged?: boolean; reprocess?: { pending?: boolean } | null };
/** The headers the import accepts (the nearby column only needs to start with "Cluster Terdekat"). */
const IMPORT_COLUMNS = ['Cluster', 'Alias', 'Area/Development', 'Cluster Terdekat (dalam radius 4 km)'] as const;
const IMPORT_PLACEHOLDER = `${IMPORT_COLUMNS.join('\t')}\nTeluk Golf\tTG; Teluk Golf CitraLand\tCitraLand\tBukit Golf (2.4)\nBukit Golf\t\tCitraLand\tTeluk Golf (2.4)`;

/** "1.2" / "1,2" → 1.2; NaN when it is not a number. */
const toNumber = (text: string) => Number(text.trim().replace(',', '.'));
const splitNames = (text: string) => [...new Map(text.split(/[\n,;]+/).map((part) => part.trim().replace(/\s+/g, ' ')).filter(Boolean).map((name) => [name.toLowerCase(), name])).values()];

export function LocationIndexPanel({ canEdit, refreshKey = 0, onChanged }: { canEdit: boolean; refreshKey?: number; onChanged?: () => void }) {
  const api = useApi();
  const notice = useSaveNotice();
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [selectedId, setSelectedId] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<LocationCluster | null>(null);
  const [deleting, setDeleting] = useState(false);
  const form = useOverlayState();
  const detailRef = useRef<HTMLDivElement>(null);
  const term = useDebounced(search.trim(), 250);
  const deps = [version, refreshKey];

  const catalog = useData((signal) => api.get<LocationCatalog>(`/location-index?${query({ search: term, offset })}`, signal), [term, offset, ...deps]);
  const detail = useData((signal) => api.get<LocationNeighbors>(`/location-index/neighbors?${query({ cluster: selectedId })}`, signal), [selectedId, ...deps], !!selectedId);
  const areas = useData((signal) => api.get<LocationAreasResponse>('/location-index/areas', signal), deps, canEdit);
  const data = catalog.data;
  const selected = selectedId && detail.data?.cluster.id === selectedId ? detail.data : undefined;

  function select(id: string) {
    setSelectedId(id);
    if (typeof window !== 'undefined' && window.innerWidth < 1280) window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
  }

  /** Runs one change; reloads the list and the open cluster afterwards. */
  async function write<T extends WriteResult>(run: () => Promise<T>, title: string, fallback: string) {
    setBusy(true);
    try {
      const result = await run();
      setVersion((value) => value + 1);
      notice(result.unchanged ? 'Nothing changed' : title, { pending: result.unchanged ? false : result.reprocess?.pending !== false });
      onChanged?.();
      return result;
    } catch (reason) {
      toast.danger(errorMessage(reason, fallback));
      return null;
    } finally { setBusy(false); }
  }

  async function deleteCluster() {
    if (!selected) return;
    const name = selected.cluster.name;
    const result = await write(() => api.del<WriteResult>(`/location-index/clusters/${encodeURIComponent(selected.cluster.id)}`), `${name} deleted`, 'The cluster could not be deleted. Please try again.');
    setDeleting(false);
    if (result) setSelectedId('');
  }

  return (
    <Panel
      title="Locations"
      description="Housing clusters, their other names, and which clusters are within 4 km of each other. A buyer can be offered a listing in a nearby cluster when everything else fits."
      bodyClassName="space-y-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Chip variant="soft">{countLabel(data?.total ?? 0, 'cluster')}</Chip>
          <Chip variant="soft">{countLabel(data?.pairs ?? 0, 'nearby link')}</Chip>
        </div>
        {canEdit && <Button variant="secondary" isDisabled={busy} onPress={() => { setEditing(null); form.open(); }}><Plus className="size-4" aria-hidden="true" />Add cluster</Button>}
      </div>

      {canEdit && <ImportSection onImported={() => { setVersion((value) => value + 1); onChanged?.(); }} />}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <div className="min-w-0 space-y-3">
          <SearchField value={search} onChange={(value) => { setSearch(value); setOffset(0); }} aria-label="Search clusters" fullWidth>
            <Label className="text-sm font-semibold">Search clusters</Label>
            <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder="Cluster, other name or area" /><SearchField.ClearButton /></SearchField.Group>
          </SearchField>
          <ErrorNotice message={catalog.error} onRetry={catalog.reload} />
          {catalog.loading && !data ? <SkeletonList rows={3} /> : !data?.clusters.length ? (
            <div className="rounded-2xl border border-dashed border-border">
              <EmptyState compact icon={<MapPin />} title={term ? 'No cluster matches your search' : 'No clusters yet'}
                description={term ? 'Try another name or area.' : canEdit ? 'Add a cluster or import your cluster table.' : 'Your company admin has not added locations yet.'} />
            </div>
          ) : (
            <ul className="max-h-[32rem] divide-y divide-separator overflow-y-auto rounded-2xl border border-border" aria-label="Clusters">
              {data.clusters.map((cluster) => {
                const active = selectedId === cluster.id;
                return (
                  <li key={cluster.id}>
                    <button type="button" aria-pressed={active} onClick={() => select(cluster.id)}
                      className={cn('block min-h-14 w-full px-4 py-2.5 text-left transition-colors focus-visible:outline-none', active ? 'bg-accent-soft' : 'hover:bg-surface-secondary focus-visible:bg-surface-secondary')}>
                      <span className={cn('block text-sm font-semibold', active ? 'text-accent-soft-foreground' : 'text-foreground')}>{cluster.name}</span>
                      <span className="block truncate text-caption text-muted">
                        {[cluster.area, countLabel(cluster.neighbor_count ?? 0, 'nearby cluster'), cluster.aliases.length ? `Also: ${cluster.aliases.join(', ')}` : ''].filter(Boolean).join(' · ')}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {data && data.filtered_total > PAGE && (
            <PagerBar from={offset + 1} to={offset + data.clusters.length} total={data.filtered_total} hasPrev={offset > 0} hasNext={data.has_more} loading={catalog.loading}
              onPrev={() => setOffset((value) => Math.max(0, value - PAGE))} onNext={() => setOffset((value) => value + PAGE)} />
          )}
        </div>

        <div ref={detailRef} className="min-w-0 scroll-mt-4">
          {!selectedId ? (
            <div className="flex h-full min-h-40 items-center justify-center rounded-2xl border border-dashed border-border p-6 text-center text-sm text-muted">
              Pick a cluster to see its other names and nearby clusters.
            </div>
          ) : detail.error ? <ErrorNotice message={detail.error} onRetry={detail.reload} /> : !selected ? <SkeletonList rows={1} /> : (
            <ClusterDetail key={selected.cluster.id} data={selected} canEdit={canEdit} busy={busy}
              onEdit={() => { setEditing(selected.cluster); form.open(); }}
              onDelete={() => setDeleting(true)}
              onAddAlias={(alias) => write(() => api.post<WriteResult>('/location-index/aliases', { cluster_id: selected.cluster.id, alias }), `“${alias}” added`, 'The name could not be added. Please try again.')}
              onRemoveAlias={(alias) => write(() => api.del<WriteResult>(`/location-index/aliases?${query({ cluster_id: selected.cluster.id, alias })}`), `“${alias}” removed`, 'The name could not be removed. Please try again.')}
              onAddNeighbor={(other, distance) => write(() => api.post<WriteResult>('/location-index/edges', { a: selected.cluster.id, b: other.id, distance_km: distance }), `${other.name} added as nearby`, 'The nearby cluster could not be saved. Please try again.')}
              onRemoveNeighbor={(other) => write(() => api.del<WriteResult>(`/location-index/edges?${query({ a: selected.cluster.id, b: other.id })}`), `${other.name} removed from nearby`, 'The nearby cluster could not be removed. Please try again.')} />
          )}
        </div>
      </div>

      <ClusterDialog state={form} cluster={editing} areas={(areas.data?.areas ?? []).map((item) => item.area)}
        onSave={async (input) => {
          const result = await write(() => api.post<LocationClusterSaveResponse & WriteResult>('/location-index/clusters', input), input.original_id ? 'Cluster saved' : `${input.name} added`, 'The cluster could not be saved. Please try again.');
          if (result?.cluster) { select(result.cluster.id); form.close(); }
        }} busy={busy} />

      <ConfirmDialog open={deleting} onOpenChange={setDeleting} title={`Delete ${selected?.cluster.name ?? 'this cluster'}?`} confirmLabel="Delete cluster" busy={busy} onConfirm={() => void deleteCluster()}>
        Its other names{selected?.neighbors.length ? ` and ${countLabel(selected.neighbors.length, 'nearby link')}` : ''} are removed too. Messages that mention it are no longer recognised as this cluster after you reprocess.
      </ConfirmDialog>
    </Panel>
  );
}

/* ------------------------------------------------------------------ one cluster */

function ClusterDetail({ data, canEdit, busy, onEdit, onDelete, onAddAlias, onRemoveAlias, onAddNeighbor, onRemoveNeighbor }: {
  data: LocationNeighbors; canEdit: boolean; busy: boolean;
  onEdit: () => void; onDelete: () => void;
  onAddAlias: (alias: string) => Promise<unknown>; onRemoveAlias: (alias: string) => Promise<unknown>;
  onAddNeighbor: (cluster: LocationCluster, distance: number) => Promise<unknown>; onRemoveNeighbor: (cluster: LocationCluster) => Promise<unknown>;
}) {
  const { cluster, neighbors } = data;
  const [alias, setAlias] = useState('');
  const [other, setOther] = useState<LocationCluster | null>(null);
  const [distance, setDistance] = useState('');
  const [tried, setTried] = useState(false);
  const km = toNumber(distance);
  const distanceProblem = !distance.trim() ? 'Enter the distance in km.' : !Number.isFinite(km) || km <= 0 || km > 4 ? 'Use a distance above 0 and up to 4 km.' : '';

  async function addAlias() {
    const value = alias.trim().replace(/\s+/g, ' ');
    if (!value) return;
    if (value.length > MAX_NAME) { toast.danger(`Use at most ${MAX_NAME} characters.`); return; }
    const result = await onAddAlias(value);
    if (result) setAlias('');
  }
  async function addNeighbor() {
    setTried(true);
    if (!other || distanceProblem) return;
    const result = await onAddNeighbor(other, Math.round(km * 1000) / 1000);
    if (result) { setOther(null); setDistance(''); setTried(false); }
  }

  return (
    <section className="space-y-5 rounded-2xl border border-border p-4 sm:p-5" aria-label={`Cluster ${cluster.name}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-section font-semibold break-words text-foreground">{cluster.name}</h3>
          <p className="text-sm text-muted">{cluster.area ? `Part of ${cluster.area}` : 'No area or development set'}</p>
        </div>
        {canEdit && (
          <div className="flex gap-1">
            <Button size="sm" variant="secondary" isDisabled={busy} onPress={onEdit}><Pencil className="size-4" aria-hidden="true" />Edit</Button>
            <Button size="sm" variant="danger-soft" isDisabled={busy} onPress={onDelete}><Trash2 className="size-4" aria-hidden="true" />Delete</Button>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h4 className="text-sm font-semibold text-foreground">Other names</h4>
        {cluster.aliases.length ? (
          <ul className="flex flex-wrap gap-2" aria-label="Other names">
            {cluster.aliases.map((name) => (
              <li key={name} className="inline-flex h-9 items-center gap-1 rounded-full bg-default pr-1 pl-3 text-sm font-medium text-default-foreground">
                {name}
                {canEdit && (
                  <button type="button" disabled={busy} aria-label={`Remove other name ${name}`} onClick={() => void onRemoveAlias(name)}
                    className="flex size-7 items-center justify-center rounded-full text-muted hover:bg-surface hover:text-danger disabled:opacity-50">
                    <X className="size-3.5" aria-hidden="true" />
                  </button>
                )}
                {!canEdit && <span className="w-2" />}
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted">No other names yet.</p>}
        {canEdit && (
          <form className="flex flex-col gap-2 sm:flex-row sm:items-end" onSubmit={(event) => { event.preventDefault(); void addAlias(); }}>
            <TextField value={alias} onChange={setAlias} fullWidth isDisabled={busy} aria-label={`Add another name for ${cluster.name}`}>
              <Input placeholder="Add another name, e.g. TG" maxLength={MAX_NAME} autoComplete="off" />
            </TextField>
            <Button type="submit" variant="secondary" className="shrink-0" isDisabled={busy || !alias.trim()}><Plus className="size-4" aria-hidden="true" />Add name</Button>
          </form>
        )}
      </div>

      <div className="space-y-2">
        <h4 className="text-sm font-semibold text-foreground">Nearby clusters <span className="font-normal text-muted">(within 4 km)</span></h4>
        {neighbors.length ? (
          <ul className="max-h-72 divide-y divide-separator overflow-y-auto rounded-xl border border-border" aria-label="Nearby clusters">
            {neighbors.map((item) => (
              <li key={item.id} className="flex min-h-12 items-center gap-3 px-3 py-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-foreground">{item.name}</span>
                  {item.area && <span className="block truncate text-caption text-muted">{item.area}</span>}
                </span>
                <span className="shrink-0 text-sm font-semibold text-foreground tabular-nums">{decimal(item.distance_km ?? 0, 2)} km</span>
                {canEdit && (
                  <Button isIconOnly size="sm" variant="ghost" isDisabled={busy} aria-label={`Remove ${item.name} from nearby clusters`} onPress={() => void onRemoveNeighbor(item)}>
                    <X className="size-4" />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted">No nearby clusters yet.</p>}
        {canEdit && (
          <form className="space-y-3 rounded-xl bg-surface-secondary/60 p-3" onSubmit={(event) => { event.preventDefault(); void addNeighbor(); }} aria-label="Add a nearby cluster">
            <ClusterPicker label="Add a nearby cluster" value={other} onChange={setOther} exclude={[cluster.id, ...neighbors.map((item) => item.id)]} isDisabled={busy} />
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <TextField value={distance} onChange={setDistance} isDisabled={busy} isInvalid={tried && !!distanceProblem} className="sm:w-40">
                <Label className="text-sm font-semibold">Distance (km)</Label>
                <Input inputMode="decimal" placeholder="e.g. 1.2" autoComplete="off" />
              </TextField>
              <Button type="submit" className="shrink-0" isDisabled={busy || !other}>Save nearby cluster</Button>
            </div>
            {tried && distanceProblem && <p className="text-sm font-medium text-danger" role="alert">{distanceProblem}</p>}
          </form>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ add / edit */

function ClusterDialog({ state, cluster, areas, busy, onSave }: {
  state: ReturnType<typeof useOverlayState>; cluster: LocationCluster | null; areas: readonly string[]; busy: boolean;
  onSave: (input: LocationClusterInput) => Promise<void>;
}) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="lg" scroll="inside">
        {/* Mounted per opening, so the form always starts from the chosen cluster (or empty). */}
        <Modal.Dialog>{state.isOpen && <ClusterForm cluster={cluster} areas={areas} busy={busy} onSave={onSave} />}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function ClusterForm({ cluster, areas, busy, onSave }: { cluster: LocationCluster | null; areas: readonly string[]; busy: boolean; onSave: (input: LocationClusterInput) => Promise<void> }) {
  const listId = useId();
  const [name, setName] = useState(cluster?.name ?? '');
  const [area, setArea] = useState(cluster?.area ?? '');
  const [aliases, setAliases] = useState((cluster?.aliases ?? []).join('\n'));
  const [tried, setTried] = useState(false);
  const names = splitNames(aliases).filter((alias) => alias.toLowerCase() !== name.trim().toLowerCase());
  const problem = !name.trim() ? 'Enter the cluster name.' : name.trim().length > MAX_NAME ? `Use at most ${MAX_NAME} characters for the name.`
    : area.trim().length > MAX_NAME ? `Use at most ${MAX_NAME} characters for the area.` : names.some((alias) => alias.length > MAX_NAME) ? `Each other name can have at most ${MAX_NAME} characters.`
      : names.length > 100 ? 'A cluster can have at most 100 other names.' : '';

  function submit() {
    setTried(true);
    if (problem) return;
    void onSave({ name: name.trim().replace(/\s+/g, ' '), area: area.trim().replace(/\s+/g, ' '), aliases: names, ...(cluster ? { original_id: cluster.id } : {}) });
  }

  return (
    <form className="contents" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <Modal.CloseTrigger />
      <Modal.Header><Modal.Heading>{cluster ? `Edit ${cluster.name}` : 'Add a cluster'}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">
        <TextField value={name} onChange={setName} fullWidth isRequired isDisabled={busy}>
          <Label className="text-sm font-semibold">Cluster name</Label>
          <Input maxLength={MAX_NAME} placeholder="e.g. Teluk Golf" autoComplete="off" />
        </TextField>
        <TextField value={area} onChange={setArea} fullWidth isDisabled={busy}>
          <Label className="text-sm font-semibold">Area or development <span className="font-normal text-muted">(optional)</span></Label>
          <Input maxLength={MAX_NAME} placeholder="e.g. CitraLand" autoComplete="off" list={listId} />
        </TextField>
        <PlaceOptions id={listId} names={areas} />
        <TextField value={aliases} onChange={setAliases} fullWidth isDisabled={busy}>
          <Label className="text-sm font-semibold">Other names <span className="font-normal text-muted">(optional)</span></Label>
          <TextArea rows={3} placeholder={'One per line or separated by commas, e.g.\nTG, Teluk Golf CitraLand'} />
        </TextField>
        {names.length > 0 && <div className="flex flex-wrap gap-2">{names.map((alias) => <Chip key={alias} size="sm" variant="soft">{alias}</Chip>)}</div>}
        {cluster && cluster.name.trim().toLowerCase() !== name.trim().toLowerCase() && name.trim() && (
          <Notice status="accent">Renaming keeps its nearby clusters.</Notice>
        )}
        {tried && problem && <p className="text-sm font-medium text-danger" role="alert">{problem}</p>}
      </Modal.Body>
      <Modal.Footer>
        <Button slot="close" variant="tertiary" isDisabled={busy}>Cancel</Button>
        <Button type="submit" isPending={busy}>{cluster ? 'Save changes' : 'Add cluster'}</Button>
      </Modal.Footer>
    </form>
  );
}

/* ------------------------------------------------------------------ import a table */

function ImportSection({ onImported }: { onImported: () => void }) {
  const api = useApi();
  const notice = useSaveNotice();
  const [text, setText] = useState('');
  const [sourceName, setSourceName] = useState('Pasted table');
  const [preview, setPreview] = useState<LocationPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const file = useRef<HTMLInputElement>(null);

  async function chooseFile(input?: File) {
    if (file.current) file.current.value = '';
    if (!input) return;
    if (input.size > 3_000_000) { setError('The file is too large. Use a file of at most 3 MB.'); return; }
    setText(await input.text()); setSourceName(input.name); setPreview(null); setError('');
  }
  async function check() {
    setBusy(true); setError('');
    try { setPreview(await api.post<LocationPreview>('/location-index/preview', { text, source_name: sourceName })); }
    catch (reason) { setPreview(null); setError(errorMessage(reason, 'The table could not be checked. Please try again.')); } finally { setBusy(false); }
  }
  async function save() {
    if (!preview) return;
    setBusy(true); setError('');
    try {
      const result = await api.post<WriteResult>('/location-index/import', { text, source_name: sourceName, revision: preview.revision });
      notice(result.unchanged ? 'Everything in this table is already in your locations' : 'Locations added', { pending: result.unchanged ? false : result.reprocess?.pending !== false });
      setPreview(null); setText(''); setSourceName('Pasted table'); onImported();
    } catch (reason) { setPreview(null); setError(errorMessage(reason, 'The table could not be added. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <Collapsible title="Import a cluster table" description="Add many clusters at once from a CSV file or a table copied from Excel." icon={<FileSpreadsheet />} bodyClassName="space-y-3">
      <div className="space-y-2 text-sm leading-relaxed text-muted">
        <p>
          The first row must have these column headers, written exactly like this:{' '}
          {IMPORT_COLUMNS.map((column, index) => (
            <span key={column}>{index > 0 && ', '}<code className="rounded bg-default px-1 py-0.5 text-xs font-semibold text-foreground">{column}</code></span>
          ))}. Other columns (such as No) are ignored.
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li><strong className="font-semibold text-foreground">Alias</strong>: other names of the cluster, separated by semicolons, e.g. <em>TG; Teluk Golf CitraLand</em>.</li>
          <li><strong className="font-semibold text-foreground">Cluster Terdekat</strong>: the clusters within 4 km, separated by semicolons, each with its distance in km in brackets, e.g. <em>Bukit Golf (2.4); Taman Golf (1.1)</em>. Each of those clusters needs its own row or must already be in your locations.</li>
        </ul>
        <p>Nothing is added twice.</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <input ref={file} type="file" accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values" className="sr-only" aria-label="Choose a cluster table file" onChange={(event) => void chooseFile(event.target.files?.[0])} />
        <Button variant="secondary" isDisabled={busy} onPress={() => file.current?.click()}><FileSpreadsheet className="size-4" aria-hidden="true" />Choose file</Button>
        {text && <span className="min-w-0 text-sm break-all text-muted">{sourceName}</span>}
      </div>
      <TextField value={text} onChange={(value) => { setText(value); setSourceName('Pasted table'); setPreview(null); }} fullWidth isDisabled={busy}>
        <Label className="text-sm font-semibold">Or paste the table</Label>
        <TextArea rows={6} className="font-mono text-sm" placeholder={IMPORT_PLACEHOLDER} />
      </TextField>
      <Button variant="secondary" isDisabled={!text.trim() || busy} isPending={busy && !preview} onPress={() => void check()}>Check table</Button>
      {error && <Notice status="danger">{error}</Notice>}
      {preview && (
        <div className="space-y-3 rounded-2xl border border-accent/30 bg-accent-soft/30 p-4">
          <p className="text-body font-semibold text-foreground">Ready to add</p>
          <p className="text-sm text-foreground">{countLabel(preview.new_clusters, 'new cluster')} · {countLabel(preview.new_pairs, 'new nearby link')} · {countLabel(preview.new_aliases, 'new other name')}</p>
          <p className="text-sm text-muted">Afterwards you have {countLabel(preview.clusters, 'cluster')} and {number(preview.pairs)} nearby links. Conflicting distances are rejected.</p>
          <Button isDisabled={busy} isPending={busy} onPress={() => void save()}>Add to locations</Button>
        </div>
      )}
    </Collapsible>
  );
}
