'use client';

import { useMemo, useState } from 'react';
import { Button, Label, SearchField } from '@heroui/react';
import { Check, RefreshCw } from 'lucide-react';
import { ErrorNotice, Notice } from '@/components/app/primitives';
import { number } from '@/lib/format';
import { priceText } from '@/lib/jev';
import type { AiModel, AiModels } from '@/lib/types';
import { cn } from '@/lib/utils';

const SHOWN = 40;

/** Preset chips (Jev 1.13 first) plus a searchable list of every OpenRouter text model with its price. */
export function ModelPicker({ value, onChange, models, error, loading, onRefresh }: {
  value: string; onChange: (id: string) => void; models?: AiModels; error?: string; loading?: boolean; onRefresh?: () => void;
}) {
  const [search, setSearch] = useState('');
  const presets = models?.presets ?? [];
  const all = useMemo(() => models?.models ?? [], [models]);
  const selected = [...presets, ...all].find((model) => model.id === value);
  const query = search.trim().toLowerCase();
  const matches = useMemo(() => (query ? all.filter((model) => `${model.name} ${model.id}`.toLowerCase().includes(query)) : []), [all, query]);

  return (
    <div className="space-y-3">
      <fieldset aria-label="Preset models" className="mx-0 grid min-w-0 gap-2 border-0 p-0 sm:grid-cols-2">
        {presets.map((model) => {
          const on = model.id === value;
          return (
            <button key={model.id} type="button" aria-pressed={on} onClick={() => onChange(model.id)}
              className={cn('flex min-h-14 items-start gap-2.5 rounded-xl border p-3 text-left transition-colors',
                on ? 'border-accent bg-accent-soft/60 ring-2 ring-accent/20' : 'border-border bg-surface hover:border-accent/50')}>
              <span aria-hidden="true" className={cn('mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border', on ? 'border-accent bg-accent text-accent-foreground' : 'border-border-secondary')}>
                {on && <Check className="size-3.5" strokeWidth={3} />}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-foreground">{model.name}{model.default ? <span className="ml-1.5 text-caption font-medium text-accent">Default</span> : null}</span>
                <span className="block truncate font-mono text-caption text-muted" title={model.id}>{model.id}</span>
                <span className="block text-caption text-muted"><KindTag model={model} /> {priceText(model.pricing)}</span>
              </span>
            </button>
          );
        })}
      </fieldset>

      <div className="space-y-2">
        <div className="flex items-end gap-2">
          <SearchField value={search} onChange={setSearch} aria-label="Search all OpenRouter models" className="min-w-0 flex-1">
            <Label className="text-sm font-semibold">Or choose any other model</Label>
            <SearchField.Group><SearchField.SearchIcon /><SearchField.Input placeholder={all.length ? `Search ${number(all.length)} models by name or id` : 'Search models'} /><SearchField.ClearButton /></SearchField.Group>
          </SearchField>
          {onRefresh && <Button isIconOnly variant="ghost" aria-label="Reload the model list" isDisabled={loading} onPress={onRefresh}><RefreshCw className="size-4" /></Button>}
        </div>
        {error && <ErrorNotice message={error} title="Model list unavailable" />}
        {models?.error && <Notice status="warning">{models.error} The presets still work.</Notice>}
        {query && (
          <ul aria-label="Matching models" className="max-h-72 divide-y divide-separator overflow-y-auto overscroll-contain rounded-2xl border border-border bg-surface">
            {matches.slice(0, SHOWN).map((model) => <ModelRow key={model.id} model={model} on={model.id === value} onPick={() => { onChange(model.id); setSearch(''); }} />)}
            {matches.length === 0 && <li className="px-4 py-5 text-center text-sm text-muted">No model matches “{search.trim()}”.</li>}
            {matches.length > SHOWN && <li className="px-4 py-2.5 text-caption text-muted">{number(matches.length - SHOWN)} more. Type more of the name to narrow the list.</li>}
          </ul>
        )}
      </div>

      <p className="text-sm text-muted">
        Selected: <strong className="font-semibold text-foreground">{selected?.name ?? value}</strong> <span className="font-mono text-caption">{value}</span>
        {' · '}{priceText(selected?.pricing)}{selected?.context_length ? ` · ${number(selected.context_length)} tokens context` : ''}
      </p>
      {selected?.kind === 'decision' && <p className="text-caption text-muted">{DECISION_NOTE}</p>}
    </div>
  );
}

const DECISION_NOTE = 'Returns yes/no probabilities; reasons are generated from those.';

function KindTag({ model }: { model: AiModel }) {
  const decision = model.kind === 'decision';
  return (
    <span title={decision ? DECISION_NOTE : 'Writes its answer as text (chat completions).'}
      className={cn('inline-block rounded-md px-1.5 py-px text-caption font-medium', decision ? 'bg-accent-soft text-accent' : 'bg-default text-muted')}>
      {decision ? 'Decision model' : 'Text model'}
    </span>
  );
}

function ModelRow({ model, on, onPick }: { model: AiModel; on: boolean; onPick: () => void }) {
  return (
    <li>
      <button type="button" onClick={onPick} aria-pressed={on}
        className={cn('flex w-full items-start gap-3 px-3.5 py-2.5 text-left hover:bg-surface-secondary/70', on && 'bg-accent-soft/40')}>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold text-foreground">{model.name}</span>
          <span className="block truncate font-mono text-caption text-muted">{model.id}</span>
          <KindTag model={model} />
        </span>
        <span className="shrink-0 text-right text-caption text-muted">
          <span className="block">{priceText(model.pricing)}</span>
          {model.context_length ? <span className="block">{number(model.context_length)} ctx</span> : null}
        </span>
      </button>
    </li>
  );
}
