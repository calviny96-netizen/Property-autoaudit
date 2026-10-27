'use client';

import { useState } from 'react';
import { Button, Chip, Input, Label, Modal, TextField, toast, useOverlayState } from '@heroui/react';
import { Check, Copy, Eye, EyeOff, KeyRound, Link2, Plus, RefreshCw, ShieldCheck, Unplug } from 'lucide-react';
import { EmptyState, ErrorNotice, LoadingRows, Notice, Panel, Segmented } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { dateTime } from '@/lib/format';
import { useCompany } from '@/lib/session';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';

type Scope = 'property:export' | 'property:upload';
type IntegrationKey = {
  id: string;
  name: string;
  scope?: Scope;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};
type NewKey = Pick<IntegrationKey, 'id' | 'name' | 'created_at'> & { token: string; company_id: string; scope: Scope };

const SCOPE_LABELS: Record<Scope, string> = { 'property:export': 'PDF exports', 'property:upload': 'Chat uploads' };
const SCOPE_HINTS: Record<Scope, string> = {
  'property:export': 'This token can only create and download PDF exports of this company.',
  'property:upload': 'This token can only upload chat exports into this company and check their progress.',
};

/** Settings → Integration (company admins): tokens for workflow tools. */
export function IntegrationTab() {
  const { company } = useCompany();
  if (!company?.permissions.manage_settings) return <Notice>Only company admins can manage integration tokens.</Notice>;
  return <IntegrationSettings companyName={company.company_name ?? 'this company'} />;
}

function IntegrationSettings({ companyName }: { companyName: string }) {
  const api = useApi();
  const keys = useData((signal) => api.get<{ keys: IntegrationKey[] }>('/integration/keys', signal), []);
  const [name, setName] = useState('Workflow Builder');
  const [scope, setScope] = useState<Scope>('property:export');
  const [fresh, setFresh] = useState<NewKey | null>(null);
  const [visible, setVisible] = useState(false);
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState('');
  const [revokeError, setRevokeError] = useState('');
  const [selected, setSelected] = useState<IntegrationKey | null>(null);
  const confirm = useOverlayState();
  const baseUrl = typeof window === 'undefined' ? '/api' : `${window.location.origin}/api`;

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied`);
    } catch {
      toast.danger('Copying did not work. Select the text and copy it yourself.');
    }
  }

  async function create() {
    if (creating || fresh || !name.trim()) return;
    setCreating(true); setError('');
    try {
      const created = await api.post<NewKey>('/integration/keys', { name: name.trim(), scope });
      setFresh(created); setVisible(false);
      // Show the new key right away; the full token exists only in this response.
      keys.setData((current) => ({ keys: [{ id: created.id, name: created.name, scope: created.scope, created_at: created.created_at, last_used_at: null, revoked_at: null }, ...(current?.keys ?? [])] }));
      keys.reload();
      toast.success('Integration token created');
    } catch (reason) { setError(errorMessage(reason, 'The token could not be created. Please try again.')); } finally { setCreating(false); }
  }

  function askRevoke(key: IntegrationKey) {
    setSelected(key); setRevokeError(''); confirm.open();
  }

  async function revoke() {
    if (!selected || revoking) return;
    setRevoking(true); setRevokeError('');
    try {
      await api.del(`/integration/keys/${selected.id}`);
      keys.setData((current) => current ? { keys: current.keys.map((key) => key.id === selected.id ? { ...key, revoked_at: new Date().toISOString() } : key) } : current);
      if (fresh?.id === selected.id) { setFresh(null); setVisible(false); }
      keys.reload(); confirm.close(); setSelected(null);
      toast.success('Token revoked', { description: 'Workflows that use it can no longer reach your data.' });
    } catch (reason) { setRevokeError(errorMessage(reason, 'The token could not be revoked. Please try again.')); } finally { setRevoking(false); }
  }

  return (
    <div className="space-y-6">
      <Panel title={<span className="flex items-center gap-2"><KeyRound className="size-5 text-accent" aria-hidden="true" />Integration tokens</span>}
        description={<>Connect a workflow tool to the data of <strong className="font-semibold text-foreground">{companyName}</strong>, for example for scheduled PDF exports.</>}>
        <form onSubmit={(event) => { event.preventDefault(); void create(); }} className="space-y-4">
          <Segmented<Scope> label="What the token may do" value={scope} onChange={setScope} isDisabled={creating || !!fresh}
            options={[{ id: 'property:export', label: SCOPE_LABELS['property:export'] }, { id: 'property:upload', label: SCOPE_LABELS['property:upload'] }]} />
          <p className="flex items-start gap-2 text-sm text-muted"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />{SCOPE_HINTS[scope]}</p>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <TextField value={name} onChange={setName} isRequired isDisabled={creating || !!fresh} fullWidth className="sm:max-w-md">
              <Label className="text-sm font-semibold">Connection name</Label>
              <Input maxLength={100} placeholder="e.g. Daily PDF workflow" autoComplete="off" />
            </TextField>
            <Button type="submit" className="shrink-0" isPending={creating} isDisabled={!name.trim() || !!fresh}><Plus className="size-4" aria-hidden="true" />Create token</Button>
          </div>
        </form>
        <div className="mt-4 space-y-4">
          <ErrorNotice message={error} />
          {fresh && (
            <div className="space-y-3 rounded-2xl border border-success/40 bg-success-soft p-4" aria-live="polite">
              <p className="flex items-center gap-2 text-body font-semibold"><Check className="size-5" aria-hidden="true" />The token for {fresh.name} is ready</p>
              <p className="text-sm">Copy it into your workflow credentials now. The full token is only shown here, until you close this box or leave the page.</p>
              <TextField type={visible ? 'text' : 'password'} value={fresh.token} isReadOnly fullWidth>
                <Label className="text-sm font-semibold">New token</Label>
                <Input className="font-mono" autoComplete="off" spellCheck={false} />
              </TextField>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onPress={() => void copy(fresh.token, 'Token')}><Copy className="size-4" aria-hidden="true" />Copy token</Button>
                <Button variant="tertiary" onPress={() => setVisible((value) => !value)}>{visible ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}{visible ? 'Hide' : 'Show token'}</Button>
                <Button variant="tertiary" onPress={() => { setFresh(null); setVisible(false); }}><Check className="size-4" aria-hidden="true" />I have saved it</Button>
              </div>
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Tokens" description="When each token was created and last used. A full token cannot be shown again."
        actions={<Button isIconOnly size="sm" variant="ghost" isDisabled={keys.loading} onPress={keys.reload} aria-label="Refresh the token list"><RefreshCw className="size-4" /></Button>}>
        <ErrorNotice message={keys.error} onRetry={keys.reload} />
        {keys.loading && !keys.data && <LoadingRows rows={2} />}
        {keys.data?.keys.length === 0 && <EmptyState compact icon={<KeyRound />} title="No integration tokens yet" description="Create a token above to connect a workflow." />}
        {!!keys.data?.keys.length && (
          <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border">
            {keys.data.keys.map((key) => (
              <li key={key.id} className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-body font-semibold break-words">{key.name}</p>
                    <Chip size="sm" variant="soft" color={key.revoked_at ? 'default' : 'success'}>{key.revoked_at ? 'Revoked' : 'Active'}</Chip>
                    {key.scope && <Chip size="sm" variant="soft">{SCOPE_LABELS[key.scope] ?? key.scope}</Chip>}
                  </div>
                  <p className="text-caption text-muted">
                    Created {dateTime(key.created_at)} · {key.last_used_at ? `last used ${dateTime(key.last_used_at)}` : 'never used'}
                    {key.revoked_at ? ` · revoked ${dateTime(key.revoked_at)}` : ''}
                  </p>
                </div>
                {!key.revoked_at && <Button variant="danger-soft" size="sm" className="shrink-0 self-start sm:self-auto" isDisabled={creating || revoking} onPress={() => askRevoke(key)} aria-label={`Revoke token ${key.name}`}><Unplug className="size-4" aria-hidden="true" />Revoke</Button>}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title={<span className="flex items-center gap-2"><Link2 className="size-5 text-accent" aria-hidden="true" />Connect a workflow</span>}
        description="Use this API address and a token in the Property connector of your workflow. Schedules and WhatsApp delivery are set up in the workflow tool.">
        <div className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <TextField value={baseUrl} isReadOnly fullWidth><Label className="text-sm font-semibold">API address</Label><Input className="font-mono text-sm" /></TextField>
            <Button variant="secondary" className="shrink-0 self-start sm:self-auto" onPress={() => void copy(baseUrl, 'API address')}><Copy className="size-4" aria-hidden="true" />Copy address</Button>
          </div>
          <details className="rounded-xl border border-border">
            <summary className="xm-summary flex min-h-11 cursor-pointer items-center px-3 text-sm font-semibold">Endpoints and how to connect</summary>
            <div className="space-y-3 border-t border-separator p-3 text-sm leading-relaxed">
              <p>Send the token as a <strong>Bearer token</strong> in the Authorization header.</p>
              <p className="font-semibold">PDF exports</p>
              <ol className="list-decimal space-y-1.5 pl-5">
                <li>Start an export with <code className="break-all">POST /integration/exports</code>.</li>
                <li>Check until it is done with <code className="break-all">GET /integration/exports/&#123;job_id&#125;</code>.</li>
                <li>Download each PDF from its <code>download_url</code> with the same token, then send it to the file’s <code>recipient_phone</code>.</li>
              </ol>
              <p>Grouping by sender needs a mapping to recipient phone numbers. Files with the status <code>needs_review</code> should be checked before sending.</p>
              <p className="font-semibold">Chat uploads</p>
              <ol className="list-decimal space-y-1.5 pl-5">
                <li>Upload a chat export with <code className="break-all">POST /integration/imports</code>.</li>
                <li>Check its progress with <code className="break-all">GET /integration/imports/&#123;import_id&#125;</code>.</li>
              </ol>
              <p>Creating a token does not turn on any schedule or delivery. Set those up in the workflow.</p>
            </div>
          </details>
        </div>
      </Panel>

      <Modal.Backdrop isOpen={confirm.isOpen} isDismissable={!revoking} isKeyboardDismissDisabled={revoking} onOpenChange={(open) => { if (!revoking) confirm.setOpen(open); }}>
        <Modal.Container size="sm">
          <Modal.Dialog>
            {!revoking && <Modal.CloseTrigger />}
            <Modal.Header><Modal.Heading>Revoke this token?</Modal.Heading></Modal.Header>
            <Modal.Body className="space-y-3">
              <p className="text-body">Workflows that use <strong>{selected?.name}</strong> can no longer reach your data. A revoked token cannot be turned back on.</p>
              <ErrorNotice message={revokeError} />
            </Modal.Body>
            <Modal.Footer>
              <Button variant="tertiary" isDisabled={revoking} onPress={() => confirm.close()}>Cancel</Button>
              <Button variant="danger" isPending={revoking} onPress={revoke}><Unplug className="size-4" aria-hidden="true" />Revoke token</Button>
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </div>
  );
}
