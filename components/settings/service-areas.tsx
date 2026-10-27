'use client';

import { useState } from 'react';
import { Button, toast } from '@heroui/react';
import { Check, Copy, MapPinned, RotateCcw, Save } from 'lucide-react';
import { ErrorNotice, Panel, SkeletonCard } from '@/components/app/primitives';
import { useSaveNotice } from '@/components/settings/reprocess-banner';
import { errorMessage } from '@/lib/api';
import { titleCase } from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { CategoryAreas, SettingsExtras } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';
import { cn } from '@/lib/utils';

/** Unit types that "Copy to all housing types" fills from the first housing row. */
const HOUSING = ['house', 'apartment', 'shophouse', 'villa'];
const SURABAYA = 'surabaya';

const cityName = (id: string) => titleCase(id.replace(/_/g, ' '));

/** GET /settings also returns the matching numbers; keep only what this panel edits. */
function pickExtras(row: Record<string, unknown>): SettingsExtras {
  const areas = (row.category_areas && typeof row.category_areas === 'object' ? row.category_areas : {}) as Record<string, unknown>;
  const category_areas: CategoryAreas = Object.fromEntries(
    Object.entries(areas).map(([key, value]) => [key, Array.isArray(value) ? value.map(String) : []]),
  );
  const service_cities = Array.isArray(row.service_cities) ? row.service_cities.map(String) : [];
  const categories = Array.isArray(row.categories)
    ? (row.categories as { id?: unknown; label?: unknown }[]).filter((item) => typeof item?.id === 'string').map((item) => ({ id: String(item.id), label: String(item.label ?? item.id) }))
    : [];
  return { category_areas, service_cities, categories };
}

/** Same cities, in the server's order, without empty rows: makes "unchanged" comparisons reliable. */
function normalize(areas: CategoryAreas, cities: readonly string[]): CategoryAreas {
  const result: CategoryAreas = {};
  for (const [key, value] of Object.entries(areas)) {
    const kept = cities.filter((city) => value.includes(city));
    if (kept.length) result[key] = kept;
  }
  return result;
}

const byKey = ([a]: [string, string[]], [b]: [string, string[]]) => a.localeCompare(b);
const sameAreas = (a: CategoryAreas, b: CategoryAreas) => JSON.stringify(Object.entries(a).sort(byKey)) === JSON.stringify(Object.entries(b).sort(byKey));

/** Settings → Matching: the cities each unit type is matched in. */
export function ServiceAreasPanel() {
  const api = useApi();
  const { company } = useCompany();
  const editable = !!company?.permissions.manage_settings;
  const loaded = useData((signal) => api.get<Record<string, unknown>>('/settings', signal).then(pickExtras), []);
  const extras = loaded.data;
  return (
    <Panel
      title={<span className="flex items-center gap-2"><MapPinned className="size-5 text-accent" aria-hidden="true" />Service areas by unit type</span>}
      description="Limit each unit type to the cities your brand serves. A buyer or listing that is clearly in another city is never matched for that unit type. Leave a row empty to allow every city."
    >
      <ErrorNotice message={loaded.error} onRetry={loaded.reload} />
      {extras
        ? <AreasForm key={JSON.stringify(extras.category_areas)} extras={extras} editable={editable} onSaved={(areas) => loaded.setData({ ...extras, category_areas: areas })} />
        : !loaded.error && <SkeletonCard avatar={false} lines={4} className="shadow-none" />}
    </Panel>
  );
}

function AreasForm({ extras, editable, onSaved }: { extras: SettingsExtras; editable: boolean; onSaved: (areas: CategoryAreas) => void }) {
  const api = useApi();
  const notice = useSaveNotice();
  const cities = extras.service_cities;
  const initial = normalize(extras.category_areas, cities);
  const [areas, setAreas] = useState<CategoryAreas>(initial);
  const [busy, setBusy] = useState(false);
  const dirty = !sameAreas(normalize(areas, cities), initial);
  const housingRows = extras.categories.filter((category) => HOUSING.includes(category.id));
  const firstHousing = housingRows[0]?.id;
  const firstHousingCities = firstHousing ? areas[firstHousing] ?? [] : [];

  const setRow = (category: string, next: string[]) => setAreas((current) => {
    const copy = { ...current };
    if (next.length) copy[category] = cities.filter((city) => next.includes(city));
    else delete copy[category];
    return copy;
  });
  const toggle = (category: string, city: string) => {
    const current = areas[category] ?? [];
    setRow(category, current.includes(city) ? current.filter((item) => item !== city) : [...current, city]);
  };
  const copyHousing = () => {
    if (!firstHousing) return;
    setAreas((current) => {
      const copy = { ...current };
      for (const row of housingRows) {
        if (firstHousingCities.length) copy[row.id] = [...firstHousingCities];
        else delete copy[row.id];
      }
      return copy;
    });
  };

  async function save() {
    setBusy(true);
    try {
      const saved = await api.put<Record<string, unknown>>('/settings', { category_areas: normalize(areas, cities) });
      const next = normalize(pickExtras({ category_areas: areas, ...saved }).category_areas, cities);
      setAreas(next);
      onSaved(next);
      // The notice is the success toast ("Service areas saved" + "Results will update when you reprocess.") and refreshes the banner.
      notice('Service areas saved');
    } catch (reason) { toast.danger(errorMessage(reason, 'The service areas could not be saved. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <p className="rounded-lg bg-surface-secondary px-3 py-2 text-caption text-muted">
        <span className="font-semibold text-foreground">Example: </span>Houses, apartments and shophouses: Surabaya only. Warehouses and land: Surabaya, Gresik and Sidoarjo.
      </p>

      <ul className="divide-y divide-separator rounded-2xl border border-border">
        {extras.categories.map((category) => {
          const selected = areas[category.id] ?? [];
          const surabayaOnly = selected.length === 1 && selected[0] === SURABAYA;
          return (
            <li key={category.id} className="space-y-2.5 px-3 py-3 sm:px-4">
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                <p className="text-sm font-semibold text-foreground">
                  {category.label}
                  <span className={cn('ml-2 font-normal', selected.length ? 'text-muted' : 'text-muted/80 italic')}>
                    {selected.length ? `${selected.length === 1 ? '1 city' : `${selected.length} cities`}` : 'Any city'}
                  </span>
                </p>
                {editable && cities.includes(SURABAYA) && !surabayaOnly && (
                  <button type="button" disabled={busy} onClick={() => setRow(category.id, [SURABAYA])}
                    className="min-h-8 rounded-lg px-2 text-caption font-semibold text-accent hover:bg-accent-soft disabled:opacity-50">
                    Surabaya only
                  </button>
                )}
              </div>
              <fieldset aria-label={`${category.label}: cities`} className="flex flex-wrap gap-2 border-0 p-0">
                {cities.map((city) => {
                  const on = selected.includes(city);
                  return (
                    <button key={city} type="button" aria-pressed={on} disabled={!editable || busy} onClick={() => toggle(category.id, city)}
                      className={cn(
                        'inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed',
                        on ? 'border-accent/40 bg-accent-soft text-accent-soft-foreground' : 'border-border bg-surface text-muted',
                        editable && !busy && (on ? 'hover:border-accent/60' : 'hover:border-border-secondary hover:text-foreground'),
                        !editable && !on && 'opacity-60',
                      )}>
                      {on && <Check className="size-3.5" strokeWidth={2.5} aria-hidden="true" />}
                      {cityName(city)}
                    </button>
                  );
                })}
              </fieldset>
            </li>
          );
        })}
      </ul>

      {editable ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button isPending={busy} isDisabled={!dirty} onPress={() => void save()}><Save className="size-4" aria-hidden="true" />Save service areas</Button>
          {dirty && <Button variant="tertiary" isDisabled={busy} onPress={() => setAreas(initial)}><RotateCcw className="size-4" aria-hidden="true" />Reset</Button>}
          {housingRows.length > 1 && (
            <Button variant="tertiary" isDisabled={busy} onPress={copyHousing}>
              <Copy className="size-4" aria-hidden="true" />Copy {housingRows[0].label} to all housing types
            </Button>
          )}
        </div>
      ) : (
        <p className="text-caption text-muted">Only a company admin can change the service areas.</p>
      )}
      <p className="text-caption text-muted">Saved changes apply after the next Reprocess.</p>
    </div>
  );
}
