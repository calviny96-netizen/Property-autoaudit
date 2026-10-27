'use client';

import { useState } from 'react';
import { Button, Chip, Input, Label, Switch, TextField, toast } from '@heroui/react';
import { Bot, KeyRound, Plus, RefreshCw, ShieldCheck, Star, Trash2 } from 'lucide-react';
import { ConfirmDialog, EmptyState, ErrorNotice, LoadingRows, Notice, Panel } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { dateTime } from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { AiKey } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';
import { setUsdIdrRate, usdIdrRate } from '@/lib/jev';

/** Settings → AI keys (super admins): OpenRouter keys for AI Review. Keys are stored encrypted and never shown again. */
export function AiKeysTab() {
  const { company } = useCompany();
  if (!company?.permissions.jev_review) return <Notice>Only company admins can manage AI keys.</Notice>;
  return <AiKeys />;
}

function AiKeys() {
  const api = useApi();
  const keys = useData((signal) => api.get<{ keys: AiKey[] }>('/ai/keys', signal), []);
  const [label, setLabel] = useState('');
  const [secret, setSecret] = useState('');
  const [makeDefault, setMakeDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [removing, setRemoving] = useState<AiKey | null>(null);
  const list = keys.data?.keys ?? [];

  async function add() {
    if (saving || !label.trim() || secret.trim().length < 8) return;
    setSaving(true); setError('');
    try {
      const created = await api.post<AiKey>('/ai/keys', { label: label.trim(), api_key: secret.trim(), make_default: makeDefault });
      setLabel(''); setSecret(''); setMakeDefault(false);
      keys.reload();
      toast.success(`Key ${created.label} added`, { description: created.is_default ? 'It is the default key for AI Review.' : undefined });
    } catch (reason) { setError(errorMessage(reason, 'The key could not be saved. Please try again.')); } finally { setSaving(false); }
  }

  async function setDefault(key: AiKey) {
    setBusyId(key.id);
    try {
      const next = await api.post<{ keys: AiKey[] }>(`/ai/keys/${key.id}/default`);
      keys.setData(next);
      toast.success(`${key.label} is now the default key`);
    } catch (reason) { toast.danger(errorMessage(reason, 'The default key could not be changed.')); } finally { setBusyId(''); }
  }

  async function remove() {
    if (!removing) return;
    setBusyId(removing.id);
    try {
      const next = await api.del<{ keys: AiKey[] }>(`/ai/keys/${removing.id}`);
      keys.setData(next);
      toast.success(`Key ${removing.label} deleted`);
      setRemoving(null);
    } catch (reason) { toast.danger(errorMessage(reason, 'The key could not be deleted.')); } finally { setBusyId(''); }
  }

  return (
    <div className="space-y-6">
      <UsdRatePanel />
      <Panel title={<span className="flex items-center gap-2"><Bot className="size-5 text-accent" aria-hidden="true" />Add an OpenRouter key</span>}
        description="AI Review sends the pairs the matching rules found to an AI model on OpenRouter. Usage is billed to the key’s OpenRouter account.">
        <form onSubmit={(event) => { event.preventDefault(); void add(); }} className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField value={label} onChange={setLabel} isRequired fullWidth>
              <Label className="text-sm font-semibold">Label</Label>
              <Input maxLength={100} placeholder="e.g. Office account" autoComplete="off" />
            </TextField>
            <TextField type="password" value={secret} onChange={setSecret} isRequired fullWidth>
              <Label className="text-sm font-semibold">API key</Label>
              <Input maxLength={500} placeholder="sk-or-v1-…" autoComplete="off" spellCheck={false} className="font-mono" />
            </TextField>
          </div>
          <Switch isSelected={makeDefault || list.length === 0} isDisabled={list.length === 0} onChange={setMakeDefault}>
            <Switch.Content>
              <Switch.Control><Switch.Thumb /></Switch.Control>
              <span className="text-sm font-semibold">Use as the default key
                <span className="mt-0.5 block font-normal text-muted">{list.length === 0 ? 'Your first key is always the default.' : 'AI Review picks the default key unless you choose another one.'}</span></span>
            </Switch.Content>
          </Switch>
          <p className="flex items-start gap-2 text-sm text-muted"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />The key is encrypted on the server. After saving, only its label and last four characters are shown.</p>
          <ErrorNotice message={error} />
          <Button type="submit" isPending={saving} isDisabled={!label.trim() || secret.trim().length < 8}><Plus className="size-4" aria-hidden="true" />Save key</Button>
        </form>
      </Panel>

      <Panel title="Keys" description="The default key is chosen first when you start a AI Review."
        actions={<Button isIconOnly size="sm" variant="ghost" isDisabled={keys.loading} onPress={keys.reload} aria-label="Refresh the key list"><RefreshCw className="size-4" /></Button>}>
        <ErrorNotice message={keys.error} onRetry={keys.reload} />
        {keys.loading && !keys.data && <LoadingRows rows={2} />}
        {keys.data && list.length === 0 && <EmptyState compact icon={<KeyRound />} title="No AI keys yet" description="Add an OpenRouter key above to use AI Review." />}
        {list.length > 0 && (
          <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border">
            {list.map((key) => (
              <li key={key.id} className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-body font-semibold break-words">{key.label}</p>
                    <span className="font-mono text-sm text-muted">{key.masked}</span>
                    <Chip size="sm" variant="soft">OpenRouter</Chip>
                    {key.is_default && <Chip size="sm" variant="soft" color="success">Default</Chip>}
                  </div>
                  <p className="text-caption text-muted">
                    Added {dateTime(key.created_at)}{key.created_by_name ? ` by ${key.created_by_name}` : ''} · {key.last_used_at ? `last used ${dateTime(key.last_used_at)}` : 'not used yet'}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  {!key.is_default && <Button size="sm" variant="secondary" isPending={busyId === key.id && !removing} isDisabled={!!busyId} onPress={() => void setDefault(key)}><Star className="size-4" aria-hidden="true" />Make default</Button>}
                  <Button size="sm" variant="danger-soft" isDisabled={!!busyId} onPress={() => setRemoving(key)} aria-label={`Delete key ${key.label}`}><Trash2 className="size-4" aria-hidden="true" />Delete</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <ConfirmDialog open={!!removing} onOpenChange={(open) => { if (!open && !busyId) setRemoving(null); }} title="Delete this key?"
        confirmLabel="Delete key" busy={!!busyId} onConfirm={() => void remove()}>
        <p><strong>{removing?.label}</strong> ({removing?.masked}) is removed from the server. AI Reviews that are still running with it stop. Finished reviews keep their results.</p>
      </ConfirmDialog>
    </div>
  );
}


/** USD → IDR rate for showing Jev costs in rupiah. Stored in this browser only, never in the database. */
function UsdRatePanel() {
  const [value, setValue] = useState(() => String(usdIdrRate()));
  const rate = Number(value.replace(/[^\d]/g, ''));
  return (
    <Panel title="Exchange rate for costs" description="OpenRouter bills in US dollars. Type today’s rate to also see every Jev cost in rupiah. Saved in this browser only.">
      <div className="flex flex-wrap items-end gap-3">
        <TextField className="w-56" value={value} onChange={setValue} inputMode="numeric">
          <Label>1 USD = Rp</Label>
          <Input placeholder="30000" />
        </TextField>
        <Button variant="secondary" onPress={() => { setUsdIdrRate(rate || null); toast.success(`Rate saved: 1 USD = Rp ${(rate || 30000).toLocaleString('id-ID')}`); if (!rate) setValue('30000'); }}>Save rate</Button>
      </div>
    </Panel>
  );
}
