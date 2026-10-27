'use client';

import { useState } from 'react';
import { Button, Label, SearchField, Spinner } from '@heroui/react';
import { MapPin } from 'lucide-react';
import { query } from '@/lib/api';
import { number } from '@/lib/format';
import { useData, useDebounced } from '@/lib/use-data';
import type { LocationCatalog, LocationCluster } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

const SHOWN = 8;

/** Find one cluster by name, other name or area (GET /location-index?search=). */
export function ClusterPicker({ label, value, onChange, exclude = [], placeholder = 'e.g. Teluk Golf', isDisabled }: {
  label: string; value: LocationCluster | null; onChange: (cluster: LocationCluster | null) => void;
  /** Cluster ids that cannot be picked (the cluster itself, existing neighbours). */
  exclude?: readonly string[]; placeholder?: string; isDisabled?: boolean;
}) {
  const api = useApi();
  const [search, setSearch] = useState('');
  const term = useDebounced(search.trim(), 250);
  const enabled = !value && term.length >= 2;
  const results = useData((signal) => api.get<LocationCatalog>(`/location-index?${query({ search: term })}`, signal), [term], enabled);
  const clusters = (results.data?.clusters ?? []).filter((cluster) => !exclude.includes(cluster.id)).slice(0, SHOWN);

  if (value) {
    return (
      <div className="min-w-0 space-y-2">
        <p className="text-sm font-semibold text-foreground">{label}</p>
        <div className="flex min-h-12 items-center gap-3 rounded-xl border border-accent/30 bg-accent-soft/50 px-3 py-2">
          <MapPin className="size-4 shrink-0 text-accent" aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-foreground">{value.name}</span>
            {value.area && <span className="block truncate text-caption text-muted">{value.area}</span>}
          </span>
          <Button size="sm" variant="tertiary" isDisabled={isDisabled} onPress={() => onChange(null)}>Change</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-2">
      <SearchField value={search} onChange={setSearch} isDisabled={isDisabled} aria-label={label} fullWidth>
        <Label className="text-sm font-semibold">{label}</Label>
        <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder={placeholder} /><SearchField.ClearButton /></SearchField.Group>
      </SearchField>
      {enabled && (
        <div className="overflow-hidden rounded-xl border border-border">
          {results.loading && !results.data && <p className="flex items-center gap-2 px-3 py-3 text-sm text-muted"><Spinner size="sm" />Searching…</p>}
          {results.error && <p className="px-3 py-3 text-sm text-danger">{results.error}</p>}
          {results.data && clusters.length === 0 && <p className="px-3 py-3 text-sm text-muted">No cluster matches “{term}”.</p>}
          <ul className="divide-y divide-separator" aria-label="Matching clusters">
            {clusters.map((cluster) => (
              <li key={cluster.id}>
                <button type="button" onClick={() => { onChange(cluster); setSearch(''); }}
                  className="flex min-h-12 w-full items-center gap-3 px-3 py-2 text-left hover:bg-surface-secondary focus-visible:bg-surface-secondary focus-visible:outline-none">
                  <MapPin className="size-4 shrink-0 text-muted" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-foreground">{cluster.name}</span>
                    <span className="block truncate text-caption text-muted">
                      {[cluster.area, cluster.aliases.length ? `Also: ${cluster.aliases.slice(0, 3).join(', ')}` : ''].filter(Boolean).join(' · ') || 'No area set'}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {results.data && (results.data.filtered_total > SHOWN) && <p className="border-t border-separator px-3 py-2 text-caption text-muted">Keep typing to narrow down {number(results.data.filtered_total)} results.</p>}
        </div>
      )}
      {!enabled && search.trim().length === 1 && <p className="text-caption text-muted">Type at least 2 letters.</p>}
    </div>
  );
}
