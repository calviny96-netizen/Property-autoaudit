'use client';

import { useId, useState, type ReactNode } from 'react';
import { Button, Chip, Input, Label, TextField, toast } from '@heroui/react';
import { RotateCcw, Save } from 'lucide-react';
import { AdvancedSection, ErrorNotice, Panel, SkeletonCard, StatusBadge } from '@/components/app/primitives';
import { useSaveNotice } from '@/components/settings/reprocess-banner';
import { ServiceAreasPanel } from '@/components/settings/service-areas';
import { KeywordsPanel, MatchingModePanel } from '@/components/settings/source-rules';
import { errorMessage } from '@/lib/api';
import { MATCH_STATUS_HINTS, area, dateOnly, decimal, money, moneyRange, toleranceBand } from '@/lib/format';
import type { Navigate } from '@/lib/router';
import { useCompany } from '@/lib/session';
import type { MatchSettings, MatchStatus, ReprocessStatus } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';

type Field = keyof MatchSettings;

/** Server defaults (api/workspace.py SETTING_DEFAULTS); also used when an older server lacks a field. */
const DEFAULTS: MatchSettings = {
  hot_price_floor_pct: 80,
  price_down_pct: 15, price_up_pct: 15, land_down_pct: 15, land_up_pct: 15, building_down_pct: 15, building_up_pct: 15, location_radius_km: 4,
  location_weight_pct: 35, land_weight_pct: 20, building_weight_pct: 15, price_weight_pct: 20, semantic_weight_pct: 5, data_quality_weight_pct: 5,
};
const FIELDS = Object.keys(DEFAULTS) as Field[];
const TOLERANCE_FIELDS: Field[] = [
  'hot_price_floor_pct', 'price_down_pct', 'price_up_pct', 'land_down_pct', 'land_up_pct', 'building_down_pct', 'building_up_pct', 'location_radius_km',
];
const ALLOWANCE_FIELDS: Field[] = ['price_down_pct', 'price_up_pct', 'land_down_pct', 'land_up_pct', 'building_down_pct', 'building_up_pct'];
/**
 * The single tolerances an older server still returns instead of the below/above pair, with the conversion.
 * price_floor_pct is the echo 100 − price_down_pct (api/workspace.py mirrors it the same way).
 */
const same = (value: number) => value;
const LEGACY: Partial<Record<Field, [string, (value: number) => number]>> = {
  price_down_pct: ['price_floor_pct', (floor) => 100 - floor], price_up_pct: ['price_tolerance_pct', same],
  land_down_pct: ['land_tolerance_pct', same], land_up_pct: ['land_tolerance_pct', same],
  building_down_pct: ['building_tolerance_pct', same], building_up_pct: ['building_tolerance_pct', same],
};
const MAX_RADIUS_KM = 4;
/** Allowed range per field (the API validates the same: Hot floor 50–100 %, radius 0–4 km, the rest 0–100 %). */
const LIMITS: Partial<Record<Field, [number, number]>> = { hot_price_floor_pct: [50, 100], location_radius_km: [0, MAX_RADIUS_KM] };
const limitsOf = (key: Field) => LIMITS[key] ?? [0, 100];
const isValid = (key: Field, value: number) => {
  const [min, max] = limitsOf(key);
  return Number.isFinite(value) && value >= min && value <= max;
};

/** GET/PUT /settings return the whole settings row (company_id, updated_at, …); keep only the numbers this form edits. */
function pickSettings(values: Record<string, unknown>): MatchSettings {
  const read = (key?: string) => {
    const value = key && values[key] != null && values[key] !== '' ? Number(values[key]) : Number.NaN;
    return Number.isFinite(value) ? value : null;
  };
  const legacy = (key: Field) => {
    const [name, convert] = LEGACY[key] ?? [];
    const value = read(name);
    return value == null || !convert ? null : convert(value);
  };
  return Object.fromEntries(FIELDS.map((key) => [key, read(key) ?? legacy(key) ?? DEFAULTS[key]])) as MatchSettings;
}

const pct = (value: number) => `${decimal(value, 2)}%`;

/* ------------------------------------------------------------------ Hot and Warm ranges (same rule as the matcher) */

const EXAMPLE_BUDGET = 2e9;
const EXAMPLE_LAND = 300;
const EXAMPLE_BUILDING = 200;

type Band = { lower: number; upper: number | null };

/** Price, single budget B: Hot = [B × floor %, B]; Warm = [Hot floor × (1 − below %), B × (1 + above %)]. */
function priceBands(budget: number, floorPct: number, downPct: number, upPct: number) {
  const hot: Band = { lower: (budget * floorPct) / 100, upper: budget };
  return { hot, warm: toleranceBand(hot.lower, budget, downPct, upPct) };
}

/** Land / building, requested size S: Hot = S; Warm = [S × (1 − below %), S × (1 + above %)]. */
function sizeBands(size: number, downPct: number, upPct: number) {
  return { hot: { lower: size, upper: size } as Band, warm: toleranceBand(size, size, downPct, upPct) };
}

const moneyBand = (band: Band | null) => (!band ? '' : band.upper == null ? `from ${money(band.lower)}` : band.lower > 0 ? moneyRange(band.lower, band.upper) ?? '' : `up to ${money(band.upper)}`);
const areaBand = (band: Band | null) => (!band ? '' : band.upper == null ? `from ${area(band.lower)}` : band.lower > 0 ? area(band.lower, band.upper) ?? '' : `up to ${area(band.upper)}`);

/** "Budget Rp 2 M → Hot Rp 1,6–2 M · Warm Rp 1,36–2,3 M" */
function priceExample(settings: Pick<MatchSettings, 'hot_price_floor_pct' | 'price_down_pct' | 'price_up_pct'>, withWarm = true) {
  const { hot, warm } = priceBands(EXAMPLE_BUDGET, settings.hot_price_floor_pct, settings.price_down_pct, settings.price_up_pct);
  return `Budget ${money(EXAMPLE_BUDGET)} → Hot ${moneyBand(hot)}${withWarm && warm ? ` · Warm ${moneyBand(warm)}` : ''}`;
}

/** "LT 300 m² → Hot 300 m² · Warm 255–345 m²" */
function sizeExample(prefix: string, size: number, downPct: number, upPct: number) {
  const { hot, warm } = sizeBands(size, downPct, upPct);
  return `${prefix} ${area(size)} → Hot ${areaBand(hot)}${warm ? ` · Warm ${areaBand(warm)}` : ''}`;
}

/** "up to 15%" when every Warm allowance is the same, otherwise the largest one. */
function allowanceText(settings: MatchSettings) {
  const values = ALLOWANCE_FIELDS.map((key) => settings[key]);
  if (values.some((value) => !Number.isFinite(value))) return 'a little';
  const largest = Math.max(...values);
  return values.every((value) => value === values[0]) ? `up to ${pct(largest)}` : `up to ${pct(largest)} (set per factor)`;
}

type Factor = {
  label: string; down: Field; up: Field;
  summary: (down: number, up: number) => string;
  /** Live example from the current inputs, e.g. "Budget Rp 2 M → Hot Rp 1,6–2 M · Warm Rp 1,36–2,3 M". */
  example: (settings: MatchSettings) => string;
  hint: (settings: MatchSettings) => string;
};

const FACTORS: Factor[] = [
  {
    label: 'Price', down: 'price_down_pct', up: 'price_up_pct',
    summary: (down, up) => `Warm down to ${pct(down)} below the Hot range and up to ${pct(up)} above the budget.`,
    example: (settings) => priceExample(settings),
    hint: (settings) => {
      const floor = money((EXAMPLE_BUDGET * settings.hot_price_floor_pct) / 100);
      return `“Below” counts down from the Hot floor (${floor} in the example), not from the budget. A budget range is widened from both ends. Rent and per-m² prices use the same ranges.`;
    },
  },
  {
    label: 'Land area', down: 'land_down_pct', up: 'land_up_pct',
    summary: (down, up) => `Warm from ${pct(down)} below to ${pct(up)} above the requested size.`,
    example: (settings) => sizeExample('LT', EXAMPLE_LAND, settings.land_down_pct, settings.land_up_pct),
    hint: () => 'A minimum the buyer insists on (“paling min 300, ga mau di bawah itu”) is never loosened. “LT min 300” has no upper limit.',
  },
  {
    label: 'Building area', down: 'building_down_pct', up: 'building_up_pct',
    summary: (down, up) => `Warm from ${pct(down)} below to ${pct(up)} above the requested size.`,
    example: (settings) => sizeExample('LB', EXAMPLE_BUILDING, settings.building_down_pct, settings.building_up_pct),
    hint: () => 'Same rule as land area, for the building size.',
  },
];

const WEIGHTS: [Field, string, string][] = [
  ['location_weight_pct', 'Location', '#0a3be0'], ['land_weight_pct', 'Land area', '#14b8d4'], ['building_weight_pct', 'Building area', '#6366f1'],
  ['price_weight_pct', 'Price', '#f59e0b'], ['semantic_weight_pct', 'Similar wording', '#10b981'], ['data_quality_weight_pct', 'Complete details', '#64748b'],
];

type LoadedSettings = { data?: MatchSettings; error: string; reload: () => void; setData: (settings: MatchSettings) => void };

/** Settings → Matching: whose buyers and listings count, what the statuses mean, and (admins) the advanced scoring. */
export function MatchingTab({ navigate }: { navigate?: Navigate }) {
  const { company } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const api = useApi();
  // Everyone may read the settings: the legend quotes the company's own Hot and Warm ranges.
  const loaded = useData((signal) => api.get<Record<string, unknown>>('/settings', signal).then(pickSettings), []);
  return (
    <div className="space-y-6">
      <ServiceAreasPanel />
      <MatchingModePanel navigate={navigate} />
      <KeywordsPanel />
      <Panel title="What the statuses mean" description="Every pair of a buyer and a listing gets one of these statuses.">
        <StatusGuide settings={loaded.data} />
      </Panel>
      {admin && <AnalysisWindowPanel />}
      {admin && <AdvancedScoring loaded={loaded} />}
    </div>
  );
}

/** Settings → Matching → "Analysis window": how many months back messages are analysed and matched (admins). */
function AnalysisWindowPanel() {
  const api = useApi();
  const notify = useSaveNotice();
  const id = useId();
  const status = useData((signal) => api.get<ReprocessStatus>('/reprocess/status', signal), []);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const saved = status.data?.analysis_window_months ?? 4;
  const value = draft ?? String(saved);
  const months = Number(value);
  const valid = Number.isInteger(months) && months >= 1 && months <= 24;
  const changed = valid && months !== saved;

  async function save() {
    if (!changed) return;
    setSaving(true);
    try {
      await api.put('/settings', { analysis_window_months: months });
      notify('Analysis window saved');
      setDraft(null);
      status.reload();
    } catch (reason) {
      toast.danger(errorMessage(reason, 'The analysis window could not be saved.'));
    } finally { setSaving(false); }
  }

  return (
    <Panel title="Analysis window" description="How far back messages are analysed and matched. Older messages stay stored and can be analysed later from the Match page or Home.">
      <ErrorNotice message={status.error} onRetry={status.reload} />
      <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <TextField value={value} onChange={setDraft} isInvalid={!valid} className="w-36">
          <Label htmlFor={id} className="text-sm font-semibold">Months</Label>
          <Input id={id} type="number" inputMode="numeric" min={1} max={24} step={1} />
        </TextField>
        <Button type="submit" isDisabled={!changed} isPending={saving}><Save className="size-4" aria-hidden="true" />Save</Button>
        {draft !== null && draft !== String(saved) && (
          <Button variant="tertiary" onPress={() => setDraft(null)}><RotateCcw className="size-4" aria-hidden="true" />Reset</Button>
        )}
      </form>
      <p className="mt-3 text-caption text-muted">
        {status.data?.analysis_from
          ? <>Currently analysing messages from <strong className="font-semibold text-foreground">{dateOnly(status.data.analysis_from)}</strong>{status.data.analysis_pinned ? ' (extended with “Analyse older data”)' : ` (${saved} ${saved === 1 ? 'month' : 'months'})`}.</>
          : 'Between 1 and 24 months; 4 by default.'}
        {' '}Changing it takes effect on the next reprocess.
      </p>
    </Panel>
  );
}

/** The status legend with this company's ranges ("price 80–100% of the budget", "Warm Rp 1,36–2,3 M"). */
function StatusGuide({ settings }: { settings?: MatchSettings }) {
  const values = settings ?? DEFAULTS;
  const radius = values.location_radius_km;
  const price = priceBands(EXAMPLE_BUDGET, values.hot_price_floor_pct, values.price_down_pct, values.price_up_pct);
  const land = sizeBands(EXAMPLE_LAND, values.land_down_pct, values.land_up_pct);
  const items: { status: MatchStatus; text: string; detail?: string }[] = [
    {
      status: 'hot',
      text: values.hot_price_floor_pct === DEFAULTS.hot_price_floor_pct ? MATCH_STATUS_HINTS.hot : `Everything clearly matches (score 90+), every buyer requirement is proven, price ${decimal(values.hot_price_floor_pct, 2)}–100% of the budget.`,
      detail: settings && `Purpose, property type, area, legal status, tower and unit type are the same; sizes are inside the request. Example: budget ${money(EXAMPLE_BUDGET)} → ${moneyBand(price.hot)}.`,
    },
    {
      status: 'warm',
      text: MATCH_STATUS_HINTS.warm,
      detail: settings && `Price and sizes ${allowanceText(values)} outside the Hot range. Example: budget ${money(EXAMPLE_BUDGET)} → ${moneyBand(price.warm)}; LT ${area(EXAMPLE_LAND)} → ${areaBand(land.warm)}. A detail the listing does not mention, such as the floor, also makes a pair Warm.`,
    },
    {
      status: 'custom',
      text: MATCH_STATUS_HINTS.custom,
      detail: settings && (radius > 0 ? `Nearby alternatives are listings in other clusters up to ${decimal(radius, 2)} km away.` : 'Nearby alternatives are switched off.'),
    },
    {
      status: 'unmatched',
      text: MATCH_STATUS_HINTS.unmatched,
      detail: 'A different purpose, property type, area, legal status, tower or unit type — or a price or size outside the Warm range — never matches.',
    },
  ];
  return (
    <dl className="grid gap-y-3.5">
      {items.map(({ status, text, detail }) => (
        <div key={status} className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-start sm:gap-2.5">
          <dt className="shrink-0 sm:w-24"><StatusBadge status={status} size="sm" explain={false} /></dt>
          <dd className="min-w-0">
            <span className="block text-sm text-foreground">{text}</span>
            {detail && <span className="mt-0.5 block text-caption break-words text-muted">{detail}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function AdvancedScoring({ loaded }: { loaded: LoadedSettings }) {
  return (
    <AdvancedSection description="Hot and Warm ranges and score weights for company admins. Most teams never need to change these.">
      <ErrorNotice message={loaded.error} onRetry={loaded.reload} />
      {loaded.data ? <ScoringForm initial={loaded.data} onSaved={(next) => loaded.setData(next)} /> : !loaded.error && <SkeletonCard avatar={false} lines={4} className="shadow-none" />}
    </AdvancedSection>
  );
}

function ScoringForm({ initial, onSaved }: { initial: MatchSettings; onSaved: (settings: MatchSettings) => void }) {
  const api = useApi();
  const notice = useSaveNotice();
  const [settings, setSettings] = useState<MatchSettings>(initial);
  const [busy, setBusy] = useState(false);
  const total = WEIGHTS.reduce((sum, [key]) => sum + (Number(settings[key]) || 0), 0);
  const balanced = Math.abs(total - 100) < 0.001;
  const dirty = FIELDS.some((key) => !Object.is(Number(initial[key]), Number(settings[key])));
  const invalid = FIELDS.some((key) => !isValid(key, settings[key]));
  const recommended = TOLERANCE_FIELDS.every((key) => settings[key] === DEFAULTS[key]);
  const set = (key: Field, value: number) => setSettings((current) => ({ ...current, [key]: value }));
  const floorValid = isValid('hot_price_floor_pct', settings.hot_price_floor_pct);
  const allowancesValid = ALLOWANCE_FIELDS.every((key) => isValid(key, settings[key]));

  async function save() {
    setBusy(true);
    try {
      const saved = await api.put<Record<string, unknown>>('/settings', Object.fromEntries(FIELDS.map((key) => [key, settings[key]])));
      const next = pickSettings({ ...settings, ...saved });
      setSettings(next);
      onSaved(next);
      notice('Saved');
    } catch (reason) { toast.danger(errorMessage(reason, 'The settings could not be saved. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <section className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-body font-semibold text-foreground">Hot and Warm ranges</h3>
            <p className="text-sm text-muted">How close a listing must be to what the buyer asked for. Outside the Warm range it never matches.</p>
          </div>
          {!recommended && (
            <Button size="sm" variant="tertiary" isDisabled={busy} onPress={() => setSettings((current) => ({ ...current, ...Object.fromEntries(TOLERANCE_FIELDS.map((key) => [key, DEFAULTS[key]])) }))}>
              <RotateCcw className="size-4" aria-hidden="true" />Recommended values
            </Button>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2" aria-live="polite">
          <BandCard status="hot">
            Everything matches: purpose, property type, area, legal status, tower and unit type. {floorValid ? `Price ${decimal(settings.hot_price_floor_pct, 2)}–100% of the budget` : 'Price close to the budget'}, sizes inside the request.
          </BandCard>
          <BandCard status="warm">
            Category, area and legal status still match 100%. Price and sizes may be {allowancesValid ? allowanceText(settings) : 'a little'} outside the Hot range.
          </BandCard>
        </div>
        <p className="text-caption text-muted">
          A requirement the buyer insists on that the listing does not state (location, tower, legal status, unit type) makes a pair Custom instead.
        </p>

        <Group title="Hot range">
          <ToleranceRow
            label="Hot price floor"
            summary={floorValid ? `Hot when the price is ${decimal(settings.hot_price_floor_pct, 2)}–100% of the budget.` : 'Enter a percentage from 50 to 100.'}
            example={floorValid ? priceExample(settings, false) : ''}
            hint="Used when the buyer gives one budget or a maximum. A budget range (“1,5–2 M”, “2M-an”) is Hot inside the range as written. Land and building are Hot inside the requested size."
            inputs={(
              <div className="grid grid-cols-2 gap-3 sm:w-60">
                <NumberField label={<><span className="sr-only">Hot price floor: </span>From</>} value={settings.hot_price_floor_pct} min={50} isDisabled={busy}
                  onChange={(value) => set('hot_price_floor_pct', value)} />
              </div>
            )}
          />
        </Group>

        <Group title="Warm allowance" description="How far outside the Hot range a listing may be and still be Warm.">
          {FACTORS.map((factor) => <FactorRow key={factor.label} factor={factor} settings={settings} floorValid={floorValid} isDisabled={busy} onChange={set} />)}
        </Group>

        <Group title="Nearby locations">
          <LocationRow value={settings.location_radius_km} isDisabled={busy} onChange={(value) => set('location_radius_km', value)} />
        </Group>
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-body font-semibold text-foreground">Score weights</h3>
            <p className="text-sm text-muted">How much each part counts towards the score. Together they must be exactly 100%.</p>
          </div>
          <Chip color={balanced ? 'success' : 'danger'} variant="soft">Total {decimal(total, 2)}%</Chip>
        </div>
        <figure className="m-0 flex h-3 overflow-hidden rounded-full bg-default" aria-label={`Weights: ${WEIGHTS.map(([key, label]) => `${label} ${settings[key]}%`).join(', ')}`}>
          {WEIGHTS.map(([key, label, color]) => <div key={key} title={`${label} ${settings[key]}%`} style={{ width: `${Math.max(0, Math.min(100, Number(settings[key]) || 0))}%`, background: color }} />)}
        </figure>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {WEIGHTS.map(([key, label, color]) => (
            <NumberField key={key} label={<span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full" style={{ background: color }} aria-hidden="true" />{label}</span>}
              value={settings[key]} isDisabled={busy} onChange={(value) => set(key, value)} />
          ))}
        </div>
        {!balanced && <p className="text-sm font-medium text-danger" role="alert">The weights add up to {decimal(total, 2)}%. Change them until they add up to 100%.</p>}
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <Button isPending={busy} isDisabled={!dirty || !balanced || invalid} onPress={() => void save()}><Save className="size-4" aria-hidden="true" />Save advanced settings</Button>
        {dirty && <Button variant="tertiary" isDisabled={busy} onPress={() => setSettings(initial)}>Undo changes</Button>}
        {invalid && (
          <p className="text-sm font-medium text-danger" role="alert">
            Use 50–100% for the Hot price floor, 0–100% for the Warm allowances and weights, and 0–{MAX_RADIUS_KM} km for nearby locations.
          </p>
        )}
      </div>
    </div>
  );
}

/** "Hot" / "Warm" explanation tile at the top of the ranges. */
function BandCard({ status, children }: { status: 'hot' | 'warm'; children: ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl border border-border bg-surface-secondary/60 p-3 sm:p-4">
      <StatusBadge status={status} size="sm" explain={false} />
      <p className="mt-2 text-sm text-foreground/90">{children}</p>
    </div>
  );
}

/** A small captioned group of rows ("Hot range", "Warm allowance", "Nearby locations"). */
function Group({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-2.5">
      <div>
        <h4 id={id} className="text-caption font-semibold tracking-wide text-muted uppercase">{title}</h4>
        {description && <p className="text-caption text-muted">{description}</p>}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

/** One factor: "Warm down to 15% below the Hot range and up to 15% above the budget", two inputs and a live example. */
function FactorRow({ factor, settings, floorValid, isDisabled, onChange }: {
  factor: Factor; settings: MatchSettings; floorValid: boolean; isDisabled?: boolean; onChange: (key: Field, value: number) => void;
}) {
  const down = settings[factor.down];
  const up = settings[factor.up];
  const valid = isValid(factor.down, down) && isValid(factor.up, up);
  // The price example starts from the Hot floor, so it also needs a valid floor.
  const exampleReady = valid && (factor.down !== 'price_down_pct' || floorValid);
  return (
    <ToleranceRow
      label={factor.label}
      summary={valid ? factor.summary(down, up) : 'Enter a percentage from 0 to 100.'}
      example={exampleReady ? factor.example(settings) : ''}
      hint={floorValid ? factor.hint(settings) : factor.hint({ ...settings, hot_price_floor_pct: DEFAULTS.hot_price_floor_pct })}
      inputs={(
        <div className="grid grid-cols-2 gap-3 sm:w-60">
          <NumberField label={<><span className="sr-only">{factor.label} Warm allowance: </span>Below</>} value={down} isDisabled={isDisabled} onChange={(value) => onChange(factor.down, value)} />
          <NumberField label={<><span className="sr-only">{factor.label} Warm allowance: </span>Above</>} value={up} isDisabled={isDisabled} onChange={(value) => onChange(factor.up, value)} />
        </div>
      )}
    />
  );
}

/** "Location: nearby clusters within 4 km" (0 = the same cluster or area only); nearby alternatives are Custom. */
function LocationRow({ value, isDisabled, onChange }: { value: number; isDisabled?: boolean; onChange: (value: number) => void }) {
  const valid = isValid('location_radius_km', value);
  const km = `${decimal(value, 2)} km`;
  return (
    <ToleranceRow
      label="Location"
      summary={!valid ? `Enter a distance from 0 to ${MAX_RADIUS_KM} km.` : value > 0 ? `Listings in nearby clusters within ${km} become Custom alternatives the buyer has to approve.` : 'No nearby alternatives.'}
      example={!valid ? '' : value > 0 ? `Buyer in one cluster → other clusters up to ${km} away are Custom` : 'Only listings in the same cluster or area match'}
      hint={`0 = only the same cluster or area. Up to ${MAX_RADIUS_KM} km. Buyers who accept only one location never get nearby alternatives.`}
      inputs={(
        <div className="grid grid-cols-2 gap-3 sm:w-60">
          <NumberField label={<><span className="sr-only">Nearby clusters </span>Within</>} value={value} max={MAX_RADIUS_KM} suffix="km" isDisabled={isDisabled} onChange={onChange} />
        </div>
      )}
    />
  );
}

function ToleranceRow({ label, summary, example, hint, inputs }: { label: string; summary: string; example: string; hint: string; inputs: ReactNode }) {
  const id = useId();
  return (
    <fieldset aria-labelledby={id} className="min-w-0 rounded-xl border border-border p-3 sm:p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p id={id} className="text-sm font-semibold text-foreground">{label}</p>
          <p className="mt-0.5 text-sm text-muted">{summary}</p>
        </div>
        {inputs}
      </div>
      {example && (
        <p className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-lg bg-surface-secondary px-3 py-2 text-sm" aria-live="polite">
          <span className="text-caption font-semibold text-muted uppercase">Example</span>
          <span className="font-medium break-words text-foreground tabular-nums">{example}</span>
        </p>
      )}
      <p className="mt-2 text-caption text-muted">{hint}</p>
    </fieldset>
  );
}

function NumberField({ label, hint, value, onChange, isDisabled, min = 0, max = 100, suffix = '%' }: {
  label: ReactNode; hint?: string; value: number; onChange: (value: number) => void; isDisabled?: boolean; min?: number; max?: number; suffix?: string;
}) {
  // Keep what is typed ("1." on the way to "1.5"); show the outside value again after a reset.
  const [text, setText] = useState(String(value));
  const parsed = text.trim() === '' ? Number.NaN : Number(text.replace(',', '.'));
  const shown = Object.is(parsed, value) || parsed === value ? text : Number.isFinite(value) ? String(value) : '';
  return (
    <TextField value={shown} onChange={(next) => { setText(next); onChange(next.trim() === '' ? Number.NaN : Number(next.replace(',', '.'))); }} isDisabled={isDisabled} fullWidth
      isInvalid={!Number.isFinite(value) || value < min || value > max}>
      <Label className="text-sm font-semibold">{label}</Label>
      <div className="relative">
        <Input inputMode="decimal" className={suffix.length > 1 ? 'pr-11' : 'pr-9'} />
        <span aria-hidden="true" className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-sm text-muted">{suffix}</span>
      </div>
      {hint && <p className="mt-1 text-caption text-muted">{hint}</p>}
    </TextField>
  );
}
