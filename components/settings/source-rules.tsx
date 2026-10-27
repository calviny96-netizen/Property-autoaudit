'use client';

/*
 * Which buyers and listings belong to the company (the sources of every match):
 * company keywords, or the sales watchlist. Saving only saves; results follow on reprocess.
 */

import { useState } from 'react';
import { Button, Chip, Label, Switch, TextArea, TextField, toast } from '@heroui/react';
import { KeyRound, LockKeyhole, RotateCcw, Save } from 'lucide-react';
import { ErrorNotice, Notice, Panel, Segmented, SkeletonCard } from '@/components/app/primitives';
import { useSaveNotice } from '@/components/settings/reprocess-banner';
import { errorMessage } from '@/lib/api';
import { formatPhone } from '@/lib/format';
import { ROUTES, type Navigate } from '@/lib/router';
import { useCompany } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { MatchingMode } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

type SearchState = { terms: string[]; company_terms: string[]; personal_terms: string[] | null; locked: boolean; can_edit_company: boolean; can_edit_personal: boolean };

const MAX_TERMS = 20;
const toLines = (terms: readonly string[]) => terms.join('\n');
const fromText = (text: string) => [...new Map(text.split(/[,;\n]+/).map((term) => term.trim()).filter(Boolean).map((term) => [term.toLowerCase(), term])).values()];
const sameTerms = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((term) => b.some((other) => other.toLowerCase() === term.toLowerCase()));

/** Company keywords or sales watchlist. */
export function MatchingModePanel({ navigate }: { navigate?: Navigate }) {
  const api = useApi();
  const notice = useSaveNotice();
  const { company, refresh } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const mode = company?.matching_mode ?? 'company';
  const [busy, setBusy] = useState(false);
  const noWatchlist = !company?.tracked_phones.length;

  async function change(next: MatchingMode) {
    if (next === mode) return;
    setBusy(true);
    try {
      await api.put('/company/settings', { matching_mode: next });
      refresh();
      notice('Matching mode saved');
    } catch (reason) { toast.danger(errorMessage(reason, 'The matching mode could not be saved. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <Panel title="Your buyers and listings" description="Every match starts from one of your company's own buyers or listings. Choose how they are recognised.">
      <div className="space-y-4">
        <Segmented<MatchingMode> label="Recognise our buyers and listings by" value={mode} fullWidth isDisabled={!admin || busy} onChange={(next) => void change(next)}
          options={[{ id: 'company', label: 'Company keywords' }, { id: 'sales', label: 'Sales watchlist' }]} />
        {mode === 'company' ? (
          <p className="text-sm leading-relaxed text-muted">A buyer or listing is yours when it mentions one of your company keywords. The signature at the bottom of the message counts too.</p>
        ) : (
          <div className="space-y-2">
            <p className="text-sm leading-relaxed text-muted">A buyer or listing is yours when its contact number is on your sales watchlist, including other numbers in the signature. Who posted the message in the group does not matter.</p>
            {noWatchlist
              ? <Notice status="warning">Your sales watchlist is empty, so nothing is matched yet. <button type="button" className="font-semibold underline" onClick={() => navigate?.(ROUTES.settings, { tab: 'sales' })}>Add sales numbers</button></Notice>
              : <p className="text-sm break-words text-muted">Watching: {company?.tracked_phones.map(formatPhone).join(' · ')}</p>}
          </div>
        )}
        {!admin && <p className="text-caption text-muted">Only company admins can change this.</p>}
      </div>
    </Panel>
  );
}

/** Company keywords (admins) or a member's own extra keywords. */
export function KeywordsPanel() {
  const api = useApi();
  const { company } = useCompany();
  const admin = !!company?.permissions.manage_settings;
  const loaded = useData((signal) => api.get<SearchState>('/search-default', signal), [admin]);
  const [saved, setSaved] = useState<SearchState | null>(null);
  const state = saved ?? loaded.data ?? null;

  return (
    <Panel title={<span className="flex items-center gap-2"><KeyRound className="size-5 text-accent" aria-hidden="true" />Company keywords</span>}
      description={company?.matching_mode === 'sales'
        ? 'Used when matching by company keywords. You are matching by the sales watchlist right now.'
        : 'A buyer or listing is yours when it mentions one of these words or phrases.'}>
      <ErrorNotice message={loaded.error} onRetry={loaded.reload} />
      {!state ? (loaded.error ? null : <SkeletonCard avatar={false} lines={3} className="shadow-none" />)
        : admin ? <CompanyKeywords key={toLines(state.company_terms) + state.locked} state={state} onSaved={setSaved} />
          : <PersonalKeywords key={toLines(state.personal_terms ?? state.company_terms)} state={state} onSaved={setSaved} />}
    </Panel>
  );
}

function CompanyKeywords({ state, onSaved }: { state: SearchState; onSaved: (state: SearchState) => void }) {
  const api = useApi();
  const notice = useSaveNotice();
  const { company, refresh } = useCompany();
  const [text, setText] = useState(toLines(state.company_terms));
  const [filterOn, setFilterOn] = useState(state.company_terms.length > 0);
  const [locked, setLocked] = useState(state.locked);
  const [busy, setBusy] = useState(false);
  const terms = fromText(text);
  const tooMany = terms.length > MAX_TERMS;
  const wanted = filterOn ? terms : [];
  const termsChanged = !sameTerms(wanted, state.company_terms);
  const changed = termsChanged || locked !== state.locked;

  async function save() {
    setBusy(true);
    try {
      const next = await api.put<SearchState>('/search-default', filterOn ? { terms, locked } : { terms: [], clear: true, locked });
      onSaved(next); refresh();
      notice('Keywords saved', { pending: termsChanged && company?.matching_mode === 'company' });
    } catch (reason) { toast.danger(errorMessage(reason, 'The keywords could not be saved. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-5">
      <Switch isSelected={filterOn} onChange={setFilterOn}>
        <Switch.Content>
          <Switch.Control><Switch.Thumb /></Switch.Control>
          <span className="text-sm font-semibold">Only use messages with these keywords
            <span className="mt-0.5 block font-normal text-muted">{filterOn ? 'Buyers and listings must mention one of the keywords below.' : 'Every message in your company data can be a buyer or listing.'}</span></span>
        </Switch.Content>
      </Switch>
      {filterOn && (
        <div className="space-y-3">
          <TextField value={text} onChange={setText} fullWidth isInvalid={tooMany}>
            <Label className="text-sm font-semibold">Keywords</Label>
            <TextArea rows={4} placeholder={'konig\nProperty Citraland'} />
          </TextField>
          <p className="text-caption text-muted">One word or phrase per line, or separate them with commas. Up to {MAX_TERMS}.</p>
          {terms.length > 0 && <div className="flex flex-wrap gap-2">{terms.map((term) => <Chip key={term} variant="soft" color="accent">{term}</Chip>)}</div>}
          {tooMany && <p className="text-sm font-medium text-danger" role="alert">Use at most {MAX_TERMS} keywords.</p>}
        </div>
      )}
      <div className="rounded-2xl border border-border bg-surface-secondary/50 p-4">
        <Switch isSelected={locked} onChange={setLocked}>
          <Switch.Content>
            <Switch.Control><Switch.Thumb /></Switch.Control>
            <span className="text-sm font-semibold"><LockKeyhole className="mr-1.5 inline size-4" aria-hidden="true" />Lock keywords for team members
              <span className="mt-0.5 block font-normal text-muted">{locked ? 'Team members cannot add keywords of their own.' : 'Team members may add their own keywords to narrow down what they see.'}</span></span>
          </Switch.Content>
        </Switch>
      </div>
      <Button isPending={busy} isDisabled={!changed || tooMany || (filterOn && !terms.length)} onPress={() => void save()}><Save className="size-4" aria-hidden="true" />Save keywords</Button>
    </div>
  );
}

function PersonalKeywords({ state, onSaved }: { state: SearchState; onSaved: (state: SearchState) => void }) {
  const api = useApi();
  const [text, setText] = useState(toLines(state.personal_terms ?? state.company_terms));
  const [busy, setBusy] = useState('');
  const terms = fromText(text);
  const tooMany = terms.length > MAX_TERMS;

  async function run(label: string, job: () => Promise<SearchState>, done: string) {
    setBusy(label);
    try { onSaved(await job()); toast.success(done); } catch (reason) { toast.danger(errorMessage(reason, 'Your keywords could not be saved. Please try again.')); } finally { setBusy(''); }
  }

  if (state.locked) {
    return (
      <div className="space-y-3">
        <Notice status="warning" title="Locked by your company admin">Everyone in the company uses these keywords.</Notice>
        <div className="flex flex-wrap gap-2">{state.company_terms.length ? state.company_terms.map((term) => <Chip key={term} variant="soft">{term}</Chip>) : <span className="text-sm text-muted">No keywords: every message is used.</span>}</div>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-sm font-semibold text-foreground">Company keywords</p>
        {state.company_terms.length
          ? <div className="flex flex-wrap gap-2">{state.company_terms.map((term) => <Chip key={term} variant="soft">{term}</Chip>)}</div>
          : <p className="text-sm text-muted">Your company uses no keywords, so every message is used.</p>}
      </div>
      <TextField value={text} onChange={setText} fullWidth isInvalid={tooMany}>
        <Label className="text-sm font-semibold">My keywords</Label>
        <TextArea rows={3} />
      </TextField>
      <p className="text-caption text-muted">Only for your account. They narrow down the buyers and listings that already follow the company rules.</p>
      {tooMany && <p className="text-sm font-medium text-danger" role="alert">Use at most {MAX_TERMS} keywords.</p>}
      <div className="flex flex-wrap gap-2">
        <Button isPending={busy === 'save'} isDisabled={!terms.length || tooMany || (!!state.personal_terms && sameTerms(terms, state.personal_terms))} onPress={() => void run('save', () => api.put<SearchState>('/search-default/personal', { terms }), 'Your keywords are saved')}>
          <Save className="size-4" aria-hidden="true" />Save my keywords
        </Button>
        <Button variant="secondary" isPending={busy === 'reset'} isDisabled={!state.personal_terms} onPress={() => void run('reset', () => api.del<SearchState>('/search-default/personal'), 'Back to the company keywords')}>
          <RotateCcw className="size-4" aria-hidden="true" />Use company keywords
        </Button>
      </div>
    </div>
  );
}
