'use client';

import { useState } from 'react';
import { Button, Chip, Description, Input, Label, ListBox, Modal, Select, Switch, TextField, toast, useOverlayState } from '@heroui/react';
import {
  BookCopy, Building2, Check, Clock, Copy, KeyRound, LockKeyhole, LogIn, Pencil, Plus, Power, ScrollText, Wand2, X, XCircle,
} from 'lucide-react';
import { DeployPanel } from '@/components/app/deploy-panel';
import { ConfirmDialog, EmptyState, ErrorNotice, Notice, PageHeader, Panel, Segmented, SkeletonList } from '@/components/app/primitives';
import { copySignIn, generatePassword } from '@/components/pages/team-page';
import { errorMessage } from '@/lib/api';
import { COMPANY_STATUS_LABELS, companyStatusColor, countLabel, relativeDateTime, ROLE_LABELS } from '@/lib/format';
import { ROUTE_PARAMS, ROUTES, useRoute, type Navigate } from '@/lib/router';
import { useManage } from '@/lib/session';
import { useData } from '@/lib/use-data';
import type { CompanyStatus, CompanySummary, CompanyUser, CopyKnowledgeRequest, CopyKnowledgeResult, PasswordReset } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

type CreatedCompany = { company: string; admin: string; email: string; password: string };
type ResetInfo = PasswordReset & { company: string };
type CopiedInfo = CopyKnowledgeResult & { target: string; source: string };
type ListId = 'pending' | 'active' | 'inactive';
type OverlayState = ReturnType<typeof useOverlayState>;
type Confirmation = { kind: 'reject' | 'deactivate'; company: CompanySummary };

const LIST_IDS: readonly ListId[] = ['pending', 'active', 'inactive'];
const listOf = (status: CompanyStatus): ListId => (status === 'pending' ? 'pending' : status === 'active' ? 'active' : 'inactive');
/** The company admin (or the first account) of a company. */
const adminOf = (company: CompanySummary) => company.users.find((user) => user.role === 'company_admin') ?? company.users[0];

/** Platform administrator only: every company, its review state and accounts, and a way to step inside. */
export default function CompaniesPage({ params, navigate }: { params?: URLSearchParams; navigate?: Navigate } = {}) {
  const route = useRoute();
  const search = params ?? route.params;
  const go = navigate ?? route.navigate;
  const api = useApi();
  const { enter } = useManage();
  const [tick, setTick] = useState(0);
  const create = useOverlayState();
  const rename = useOverlayState();
  const reset = useOverlayState();
  const [renaming, setRenaming] = useState<CompanySummary | null>(null);
  const [resetting, setResetting] = useState<CompanySummary | null>(null);
  const [created, setCreated] = useState<CreatedCompany | null>(null);
  const [resetInfo, setResetInfo] = useState<ResetInfo | null>(null);
  const copyKnowledge = useOverlayState();
  const [copying, setCopying] = useState<CompanySummary | null>(null);
  const [copied, setCopied] = useState<CopiedInfo | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const companies = useData((signal) => api.get<CompanySummary[]>('/admin/companies', signal), [tick]);
  const reload = () => setTick((value) => value + 1);

  const all = companies.data ?? [];
  const groups: Record<ListId, CompanySummary[]> = { pending: [], active: [], inactive: [] };
  for (const company of all) groups[listOf(company.status)].push(company);
  const requested = search.get(ROUTE_PARAMS.status);
  const list: ListId = LIST_IDS.includes(requested as ListId) ? (requested as ListId) : groups.pending.length ? 'pending' : 'active';
  const shown = groups[list];

  async function changeStatus(company: CompanySummary, status: 'active' | 'inactive' | 'rejected', message: string) {
    setBusyId(company.company_id);
    try {
      await api.post(`/admin/companies/${company.company_id}/status`, { status });
      toast.success(message);
      setConfirmation(null);
      reload();
    } catch (reason) {
      toast.danger(errorMessage(reason, 'The company status could not be changed. Please try again.'));
    } finally {
      setBusyId(null);
    }
  }

  const openCompany = (company: CompanySummary) => company.owner_id && enter({ userId: company.owner_id, name: company.name });
  const showLogs = (company: CompanySummary) => go(ROUTES.usage, { [ROUTE_PARAMS.company]: company.company_id });

  return (
    <div className="space-y-6">
      <PageHeader title="Companies" description="Each company has its own separate data and accounts. Review new sign-ups, open a company to manage its data, team and settings."
        actions={<Button onPress={() => create.open()}><Plus className="size-4" aria-hidden="true" />Add company</Button>} />

      {created && (
        <Notice status="success" title={`${created.company} created`}>
          Share the sign-in details with {created.admin}: <strong className="break-all">{created.email}</strong> and the password <strong className="font-mono">{created.password}</strong>. The password is not shown again after you close this message.
          <span className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onPress={() => void copySignIn(created.email, created.password)}><Copy className="size-4" aria-hidden="true" />Copy sign-in details</Button>
            <Button size="sm" variant="tertiary" onPress={() => setCreated(null)}><X className="size-4" aria-hidden="true" />Close</Button>
          </span>
        </Notice>
      )}

      {copied && (
        <Notice status="success" title={`Words and places copied into ${copied.target}`}
          action={<Button size="sm" variant="tertiary" onPress={() => setCopied(null)}><X className="size-4" aria-hidden="true" />Close</Button>}>
          {countLabel(copied.glossary_added, 'word')} and {countLabel(copied.clusters_added, 'cluster')} ({countLabel(copied.links_added, 'link')}) copied from {copied.source} into {copied.target}{copied.mode === 'replace' ? ', replacing what it had' : ''}. Results update after the company’s next Reprocess.
        </Notice>
      )}

      {resetInfo && (
        <Notice status="success" title={`Temporary password for ${resetInfo.display_name} (${resetInfo.company})`}>
          Share these sign-in details with them: <strong className="break-all">{resetInfo.email}</strong> and the temporary password <strong className="font-mono">{resetInfo.password}</strong>. They must choose a new password when they sign in. The password is not shown again after you close this message.
          <span className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onPress={() => void copySignIn(resetInfo.email, resetInfo.password)}><Copy className="size-4" aria-hidden="true" />Copy sign-in details</Button>
            <Button size="sm" variant="tertiary" onPress={() => setResetInfo(null)}><X className="size-4" aria-hidden="true" />Close</Button>
          </span>
        </Notice>
      )}

      <ErrorNotice message={companies.error} onRetry={companies.reload} />

      <Segmented<ListId> label="Company list" value={list} onChange={(next) => go(ROUTES.companies, { [ROUTE_PARAMS.status]: next })}
        options={[
          { id: 'pending', label: <span className="inline-flex items-center gap-1.5">Pending{groups.pending.length > 0 && <Chip size="sm" variant="soft" color="warning" className="tabular-nums">{groups.pending.length}</Chip>}</span> },
          { id: 'active', label: 'Active' },
          { id: 'inactive', label: 'Inactive' },
        ]} />

      {companies.loading && !companies.data && <SkeletonList rows={3} />}
      {companies.data && shown.length === 0 && <Panel><ListEmptyState list={list} onAdd={() => create.open()} /></Panel>}

      {shown.length > 0 && (
        <ul className="grid gap-4 lg:grid-cols-2">
          {shown.map((company) => (
            <li key={company.company_id} className="min-w-0">
              {company.status === 'pending' ? (
                <PendingCard company={company} busy={busyId === company.company_id}
                  onApprove={() => void changeStatus(company, 'active', `${company.name} approved`)}
                  onReject={() => setConfirmation({ kind: 'reject', company })} />
              ) : company.status === 'active' ? (
                <ActiveCard company={company} busy={busyId === company.company_id} onOpen={() => openCompany(company)} onLogs={() => showLogs(company)}
                  onRename={() => { setRenaming(company); rename.open(); }}
                  onReset={() => { setResetting(company); reset.open(); }}
                  onCopy={() => { setCopying(company); copyKnowledge.open(); }}
                  onDeactivate={() => setConfirmation({ kind: 'deactivate', company })} />
              ) : (
                <InactiveCard company={company} busy={busyId === company.company_id}
                  onActivate={() => void changeStatus(company, 'active', `${company.name} is active again`)} />
              )}
            </li>
          ))}
        </ul>
      )}

      {list === 'active' && <DeployPanel />}

      <CreateCompany state={create} onDone={(info) => { setCreated(info); reload(); }} />
      <RenameCompany state={rename} company={renaming} onDone={reload} />
      <ResetPassword state={reset} company={resetting} onDone={(info) => { setResetInfo(info); reload(); }} />
      <CopyKnowledge state={copyKnowledge} company={copying} companies={all} onDone={(info) => { setCopied(info); reload(); }} />

      <ConfirmDialog open={confirmation?.kind === 'reject'} onOpenChange={(open) => { if (!open) setConfirmation(null); }}
        title={`Reject ${confirmation?.company.name ?? 'this company'}?`} confirmLabel="Reject" busy={!!confirmation && busyId === confirmation.company.company_id}
        onConfirm={() => confirmation && void changeStatus(confirmation.company, 'rejected', `${confirmation.company.name} rejected`)}>
        The people who registered will not be able to sign in. You can still activate the company later from the Inactive list.
      </ConfirmDialog>
      <ConfirmDialog open={confirmation?.kind === 'deactivate'} onOpenChange={(open) => { if (!open) setConfirmation(null); }}
        title={`Deactivate ${confirmation?.company.name ?? 'this company'}?`} confirmLabel="Deactivate" busy={!!confirmation && busyId === confirmation.company.company_id}
        onConfirm={() => confirmation && void changeStatus(confirmation.company, 'inactive', `${confirmation.company.name} deactivated`)}>
        Everyone in this company is signed out immediately and cannot sign in until you activate it again. Its data is kept.
      </ConfirmDialog>
    </div>
  );
}

/* ------------------------------------------------------------------ cards */

function ListEmptyState({ list, onAdd }: { list: ListId; onAdd: () => void }) {
  if (list === 'pending') return <EmptyState icon={<Clock />} title="Nothing waiting for review" description="New sign-ups appear here until you approve or reject them." />;
  if (list === 'inactive') return <EmptyState icon={<Power />} title="No inactive companies" description="Deactivated and rejected companies appear here. You can activate them again at any time." />;
  return <EmptyState icon={<Building2 />} title="No active companies yet" description="Add the first company, or approve one that is waiting for review."
    action={<Button onPress={onAdd}><Plus className="size-4" aria-hidden="true" />Add company</Button>} />;
}

function CompanyTitle({ company }: { company: CompanySummary }) {
  return (
    <span className="flex items-center gap-2">
      <Building2 className="size-5 shrink-0 text-accent" aria-hidden="true" />
      <span className="min-w-0 break-words">{company.name}</span>
    </span>
  );
}

function AccountList({ users, limit = 4 }: { users: CompanyUser[]; limit?: number }) {
  return (
    <ul className="space-y-2">
      {users.slice(0, limit).map((user) => (
        <li key={user.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="font-semibold text-foreground">{user.display_name}</span>
          <span className="min-w-0 break-all text-muted">{user.email}</span>
          {user.role !== 'user' && <Chip size="sm" variant="soft" color="accent">{ROLE_LABELS[user.role]}</Chip>}
          {user.is_locked && <Chip size="sm" variant="soft" color="danger">Locked</Chip>}
        </li>
      ))}
      {users.length > limit && <li className="text-caption text-muted">+{countLabel(users.length - limit, 'more account')}</li>}
      {users.length === 0 && <li className="text-sm text-muted">No accounts yet.</li>}
    </ul>
  );
}

const counts = (company: CompanySummary) => `${countLabel(company.buyers, 'buyer')} · ${countLabel(company.listings, 'listing')} · ${countLabel(company.users.length, 'account')}`;
/** "120 words · 900 clusters": the glossary and location clusters a company has. */
const knowledgeCounts = (company: Pick<CompanySummary, 'glossary_entries' | 'clusters'>) => `${countLabel(company.glossary_entries ?? 0, 'word')} · ${countLabel(company.clusters ?? 0, 'cluster')}`;
function KnowledgeLine({ company }: { company: CompanySummary }) {
  return <p className="mb-3 flex items-center gap-1.5 text-caption text-muted"><BookCopy className="size-3.5 shrink-0" aria-hidden="true" />{knowledgeCounts(company)}</p>;
}

function PendingCard({ company, busy, onApprove, onReject }: { company: CompanySummary; busy: boolean; onApprove: () => void; onReject: () => void }) {
  const admin = adminOf(company);
  return (
    <Panel className="flex h-full flex-col" bodyClassName="flex flex-1 flex-col gap-4" title={<CompanyTitle company={company} />}
      description={<>Registered {relativeDateTime(company.registered_at)}</>}
      actions={<Chip size="sm" variant="soft" color={companyStatusColor(company.status)}>{COMPANY_STATUS_LABELS[company.status]}</Chip>}>
      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-caption text-muted">Contact email</dt>
          <dd className="mt-0.5 font-medium break-all text-foreground">{company.contact_email || admin?.email || 'Not available'}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-caption text-muted">Company admin</dt>
          <dd className="mt-0.5 font-medium break-words text-foreground">{admin ? admin.display_name : 'No account yet'}{admin && admin.email !== company.contact_email && <span className="block font-normal break-all text-muted">{admin.email}</span>}</dd>
        </div>
      </dl>
      <p className="flex items-center gap-1.5 text-caption text-muted"><BookCopy className="size-3.5 shrink-0" aria-hidden="true" />{knowledgeCounts(company)}</p>
      <div className="mt-auto flex flex-col gap-2 sm:flex-row">
        <Button className="sm:flex-1" isPending={busy} onPress={onApprove}><Check className="size-4" aria-hidden="true" />Approve</Button>
        <Button className="sm:flex-1" variant="danger-soft" isDisabled={busy} onPress={onReject}><XCircle className="size-4" aria-hidden="true" />Reject</Button>
      </div>
    </Panel>
  );
}

function ActiveCard({ company, busy, onOpen, onLogs, onRename, onReset, onCopy, onDeactivate }: {
  company: CompanySummary; busy: boolean; onOpen: () => void; onLogs: () => void; onRename: () => void; onReset: () => void; onCopy: () => void; onDeactivate: () => void;
}) {
  const admin = adminOf(company);
  return (
    <Panel className="flex h-full flex-col" bodyClassName="flex flex-1 flex-col" title={<CompanyTitle company={company} />} description={counts(company)}
      actions={<Button isIconOnly size="sm" variant="tertiary" aria-label={`Rename ${company.name}`} onPress={onRename}><Pencil className="size-4" /></Button>}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-caption text-muted">
        <span>Last activity {relativeDateTime(company.last_activity_at)}</span>
        {company.search_locked && <Chip size="sm" variant="soft" color="warning"><LockKeyhole className="mr-1 size-3" aria-hidden="true" />Keywords locked</Chip>}
      </div>
      <KnowledgeLine company={company} />
      <AccountList users={company.users} />
      <div className="mt-auto space-y-2 pt-4">
        <Button fullWidth variant="secondary" isDisabled={!company.owner_id || busy} onPress={onOpen}>
          <LogIn className="size-4" aria-hidden="true" />Open company{admin ? ` as ${admin.display_name}` : ''}
        </Button>
        <div className="flex flex-wrap gap-1">
          <Button size="sm" variant="tertiary" isDisabled={busy || company.users.length === 0} onPress={onReset}><KeyRound className="size-4" aria-hidden="true" />Reset password</Button>
          <Button size="sm" variant="tertiary" isDisabled={busy} onPress={onCopy}><BookCopy className="size-4" aria-hidden="true" />Copy words &amp; places</Button>
          <Button size="sm" variant="tertiary" isDisabled={busy} onPress={onLogs}><ScrollText className="size-4" aria-hidden="true" />Logs</Button>
          <Button size="sm" variant="tertiary" className="text-danger" isDisabled={busy} onPress={onDeactivate}><Power className="size-4" aria-hidden="true" />Deactivate</Button>
        </div>
      </div>
    </Panel>
  );
}

function InactiveCard({ company, busy, onActivate }: { company: CompanySummary; busy: boolean; onActivate: () => void }) {
  const rejected = company.status === 'rejected';
  return (
    <Panel className="flex h-full flex-col" bodyClassName="flex flex-1 flex-col" title={<CompanyTitle company={company} />} description={counts(company)}
      actions={<Chip size="sm" variant="soft" color={companyStatusColor(company.status)}>{COMPANY_STATUS_LABELS[company.status]}</Chip>}>
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-muted">
        <span>{rejected ? 'Rejected' : 'Deactivated'} {relativeDateTime(company.reviewed_at)}</span>
        <span>Registered {relativeDateTime(company.registered_at)}</span>
        {company.contact_email && <span className="break-all">{company.contact_email}</span>}
        {company.search_locked && <Chip size="sm" variant="soft" color="warning"><LockKeyhole className="mr-1 size-3" aria-hidden="true" />Keywords locked</Chip>}
      </div>
      <KnowledgeLine company={company} />
      <AccountList users={company.users} />
      <div className="mt-auto pt-4">
        <Button fullWidth isPending={busy} onPress={onActivate}><Check className="size-4" aria-hidden="true" />{rejected ? 'Activate (approve)' : 'Activate'}</Button>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ dialogs */

function CreateCompany({ state, onDone }: { state: OverlayState; onDone: (created: CreatedCompany) => void }) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="md" scroll="inside">
        <Modal.Dialog>{state.isOpen && <CreateForm onClose={() => state.close()} onDone={onDone} />}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function CreateForm({ onClose, onDone }: { onClose: () => void; onDone: (created: CreatedCompany) => void }) {
  const api = useApi();
  const [name, setName] = useState('');
  const [adminName, setAdminName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState(() => generatePassword());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const valid = !!name.trim() && !!adminName.trim() && /.+@.+\..+/.test(email) && password.length >= 6;

  async function save() {
    if (!valid) return;
    setBusy(true); setError('');
    try {
      await api.post('/admin/companies', { name: name.trim(), admin_name: adminName.trim(), admin_email: email.trim(), password });
      toast.success(`${name.trim()} created`);
      onDone({ company: name.trim(), admin: adminName.trim(), email: email.trim(), password });
      onClose();
    } catch (reason) { setError(errorMessage(reason, 'The company could not be created. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <form className="contents" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <Modal.CloseTrigger />
      <Modal.Header><Modal.Heading>Add a company</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">
        <p className="text-sm text-muted">A new company starts empty, active and separate from the others. Its first account becomes the company admin.</p>
        <TextField value={name} onChange={setName} isRequired fullWidth><Label className="text-sm font-semibold">Company name</Label><Input placeholder="e.g. Property Citraland" maxLength={120} autoComplete="off" /></TextField>
        <TextField value={adminName} onChange={setAdminName} isRequired fullWidth><Label className="text-sm font-semibold">Company admin’s name</Label><Input maxLength={100} autoComplete="off" /></TextField>
        <TextField value={email} onChange={setEmail} isRequired type="email" fullWidth><Label className="text-sm font-semibold">Company admin’s email</Label><Input autoComplete="off" /></TextField>
        <TextField value={password} onChange={setPassword} isRequired fullWidth>
          <Label className="text-sm font-semibold">First password</Label>
          <div className="relative">
            <Input className="pr-12 font-mono" autoComplete="new-password" />
            <button type="button" aria-label="Create a password" onClick={() => setPassword(generatePassword())} className="absolute top-1/2 right-1 flex size-10 -translate-y-1/2 items-center justify-center rounded-xl text-accent hover:bg-default"><Wand2 className="size-5" aria-hidden="true" /></button>
          </div>
          <Description className="text-caption">At least 6 characters. You can copy the sign-in details after creating the company.</Description>
        </TextField>
        {error && <Notice status="danger">{error}</Notice>}
      </Modal.Body>
      <Modal.Footer>
        <Button slot="close" variant="tertiary">Cancel</Button>
        <Button type="submit" isPending={busy} isDisabled={!valid}>Create company</Button>
      </Modal.Footer>
    </form>
  );
}

function RenameCompany({ state, company, onDone }: { state: OverlayState; company: CompanySummary | null; onDone: () => void }) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="sm">
        <Modal.Dialog>{state.isOpen && company && <RenameForm company={company} onClose={() => state.close()} onDone={onDone} />}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function RenameForm({ company, onClose, onDone }: { company: CompanySummary; onClose: () => void; onDone: () => void }) {
  const api = useApi();
  const [name, setName] = useState(company.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function save() {
    if (!name.trim()) return;
    setBusy(true); setError('');
    try { await api.put(`/admin/companies/${company.company_id}`, { name: name.trim() }); toast.success('Company renamed'); onClose(); onDone(); }
    catch (reason) { setError(errorMessage(reason, 'The company could not be renamed. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <form className="contents" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <Modal.CloseTrigger />
      <Modal.Header><Modal.Heading>Rename company</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-3">
        <TextField value={name} onChange={setName} isRequired fullWidth><Label className="text-sm font-semibold">Company name</Label><Input maxLength={120} autoComplete="off" /></TextField>
        {error && <Notice status="danger">{error}</Notice>}
      </Modal.Body>
      <Modal.Footer>
        <Button slot="close" variant="tertiary">Cancel</Button>
        <Button type="submit" isPending={busy} isDisabled={!name.trim() || name.trim() === company.name}>Save name</Button>
      </Modal.Footer>
    </form>
  );
}

function ResetPassword({ state, company, onDone }: { state: OverlayState; company: CompanySummary | null; onDone: (info: ResetInfo) => void }) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="sm">
        <Modal.Dialog>{state.isOpen && company && <ResetForm company={company} onClose={() => state.close()} onDone={onDone} />}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function ResetForm({ company, onClose, onDone }: { company: CompanySummary; onClose: () => void; onDone: (info: ResetInfo) => void }) {
  const api = useApi();
  const [userId, setUserId] = useState(() => adminOf(company)?.id ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const target = company.users.find((user) => user.id === userId);

  async function save() {
    if (!target) return;
    setBusy(true); setError('');
    try {
      const result = await api.post<PasswordReset>(`/admin/companies/${company.company_id}/reset-password`, { user_id: target.id });
      toast.success(`Temporary password set for ${result.display_name}`);
      onDone({ ...result, company: company.name });
      onClose();
    } catch (reason) { setError(errorMessage(reason, 'The password could not be reset. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <form className="contents" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <Modal.CloseTrigger />
      <Modal.Header><Modal.Heading>Reset a password in {company.name}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">
        <p className="text-sm text-muted">A temporary password is created for the account you choose. It is shown once; the person must choose a new password when they sign in.</p>
        <Select value={userId} onChange={(value) => { if (value !== null) setUserId(String(value)); }} fullWidth isDisabled={busy}>
          <Label className="text-sm font-semibold">Account</Label>
          <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover>
            <ListBox>
              {company.users.map((user) => (
                <ListBox.Item key={user.id} id={user.id} textValue={`${user.display_name} ${user.email}`}>
                  {user.display_name} · {user.email}{user.role === 'company_admin' ? ` (${ROLE_LABELS.company_admin})` : ''}<ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>
        {error && <Notice status="danger">{error}</Notice>}
      </Modal.Body>
      <Modal.Footer>
        <Button slot="close" variant="tertiary">Cancel</Button>
        <Button type="submit" isPending={busy} isDisabled={!target}><KeyRound className="size-4" aria-hidden="true" />Reset password</Button>
      </Modal.Footer>
    </form>
  );
}

function CopyKnowledge({ state, company, companies, onDone }: { state: OverlayState; company: CompanySummary | null; companies: CompanySummary[]; onDone: (info: CopiedInfo) => void }) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="md" scroll="inside">
        <Modal.Dialog>{state.isOpen && company && <CopyKnowledgeForm company={company} companies={companies} onClose={() => state.close()} onDone={onDone} />}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

type CopyMode = CopyKnowledgeRequest['mode'];

function CopyKnowledgeForm({ company, companies, onClose, onDone }: { company: CompanySummary; companies: CompanySummary[]; onClose: () => void; onDone: (info: CopiedInfo) => void }) {
  const api = useApi();
  const sources = companies.filter((item) => item.company_id !== company.company_id);
  const [sourceId, setSourceId] = useState(sources[0]?.company_id ?? '');
  const [glossary, setGlossary] = useState(true);
  const [locations, setLocations] = useState(true);
  const [mode, setMode] = useState<CopyMode>('merge');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const source = sources.find((item) => item.company_id === sourceId);
  const valid = !!source && (glossary || locations);

  async function save() {
    if (!source || !valid) return;
    setBusy(true); setError('');
    try {
      const body: CopyKnowledgeRequest = { source_company_id: source.company_id, glossary, locations, mode };
      const result = await api.post<CopyKnowledgeResult>(`/admin/companies/${company.company_id}/copy-knowledge`, body);
      toast.success(`${countLabel(result.glossary_added, 'word')} and ${countLabel(result.clusters_added, 'cluster')} copied into ${company.name}`);
      onDone({ ...result, target: company.name, source: source.name });
      onClose();
    } catch (reason) { setError(errorMessage(reason, 'The words and places could not be copied. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <form className="contents" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <Modal.CloseTrigger />
      <Modal.Header><Modal.Heading>Copy words &amp; places into {company.name}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">
        <p className="text-sm text-muted">{company.name} has {knowledgeCounts(company)} now. Choose which company to copy from and what to copy.</p>
        {sources.length === 0 ? <Notice status="warning">There is no other company to copy from yet.</Notice> : (
          <Select value={sourceId} onChange={(value) => { if (value !== null) setSourceId(String(value)); }} fullWidth isDisabled={busy}>
            <Label className="text-sm font-semibold">Copy from</Label>
            <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
            <Select.Popover>
              <ListBox>
                {sources.map((item) => (
                  <ListBox.Item key={item.company_id} id={item.company_id} textValue={`${item.name} ${knowledgeCounts(item)}`}>
                    {item.name} · {knowledgeCounts(item)}{item.status !== 'active' ? ` (${COMPANY_STATUS_LABELS[item.status]})` : ''}<ListBox.ItemIndicator />
                  </ListBox.Item>
                ))}
              </ListBox>
            </Select.Popover>
          </Select>
        )}
        <div className="space-y-3 rounded-2xl border border-border p-4">
          <Switch isSelected={glossary} onChange={setGlossary} isDisabled={busy}>
            <Switch.Content>
              <Switch.Control><Switch.Thumb /></Switch.Control>
              <span className="text-sm font-semibold">Glossary (words)<span className="mt-0.5 block font-normal text-muted">Abbreviations, slang and spellings and what they mean.</span></span>
            </Switch.Content>
          </Switch>
          <Switch isSelected={locations} onChange={setLocations} isDisabled={busy}>
            <Switch.Content>
              <Switch.Control><Switch.Thumb /></Switch.Control>
              <span className="text-sm font-semibold">Clusters (places)<span className="mt-0.5 block font-normal text-muted">Areas, developments and the links between them.</span></span>
            </Switch.Content>
          </Switch>
          {!glossary && !locations && <p className="text-caption text-danger">Choose at least one thing to copy.</p>}
        </div>
        <div className="space-y-2">
          <Segmented<CopyMode> label="How" value={mode} onChange={setMode} fullWidth isDisabled={busy}
            options={[{ id: 'merge', label: 'Merge' }, { id: 'replace', label: 'Replace' }]} />
          <p className="text-caption text-muted">
            {mode === 'merge'
              ? `Merge adds what ${company.name} does not have yet and keeps its own entries.`
              : `Replace makes ${company.name} an exact copy; its own words and clusters are removed.`}
          </p>
          {mode === 'replace' && <Notice status="warning">Every word and cluster {company.name} has now will be deleted and replaced by the copy. This cannot be undone.</Notice>}
        </div>
        {error && <Notice status="danger">{error}</Notice>}
      </Modal.Body>
      <Modal.Footer>
        <Button slot="close" variant="tertiary">Cancel</Button>
        <Button type="submit" variant={mode === 'replace' ? 'danger' : 'primary'} isPending={busy} isDisabled={!valid}><BookCopy className="size-4" aria-hidden="true" />{mode === 'replace' ? 'Replace and copy' : 'Copy'}</Button>
      </Modal.Footer>
    </form>
  );
}
