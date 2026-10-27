'use client';

import { useState } from 'react';
import { Avatar, Button, Chip, Description, Input, Label, ListBox, Modal, Select, Switch, TextField, toast, useOverlayState } from '@heroui/react';
import { Copy, Eye, EyeOff, KeyRound, LockKeyhole, Pencil, Plus, ShieldCheck, UserRound, Wand2, X } from 'lucide-react';
import { Collapsible, EmptyState, ErrorNotice, Notice, PageHeader, Panel, SkeletonList } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { dateOnly, FEATURE_LABELS, initials, relativeDateTime, ROLE_LABELS } from '@/lib/format';
import { useData } from '@/lib/use-data';
import { DEFAULT_MEMBER_FEATURES, FEATURE_KEYS, type Feature, type FeatureInfo, type TeamMember } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

/** The API accepts passwords of 6 characters or more. The generator still makes 12: they are easy to read out. */
const MIN_PASSWORD = 6;

type MemberRole = 'user' | 'company_admin';
type FeatureCatalog = { features: FeatureInfo[]; default: Feature[] };

/** A password people can read out and type: no look-alike characters. */
export function generatePassword(length = 12) {
  const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789';
  const bytes = crypto.getRandomValues(new Uint32Array(length));
  return Array.from(bytes, (value) => alphabet[value % alphabet.length]).join('');
}

/** "Email: … / Password: …" on the clipboard, with a toast either way. */
export async function copySignIn(email: string, password: string) {
  try {
    await navigator.clipboard.writeText(`Email: ${email}\nPassword: ${password}`);
    toast.success('Sign-in details copied');
  } catch {
    toast.danger('Copying did not work. Select the details and copy them yourself.');
  }
}

/** Fallback catalog while GET /team/features loads (or on an older server): the same labels, no descriptions. */
const FALLBACK_CATALOG: FeatureCatalog = {
  features: FEATURE_KEYS.map((id) => ({ id, label: FEATURE_LABELS[id], description: '' })),
  default: [...DEFAULT_MEMBER_FEATURES],
};

type CreatedInfo = { email: string; password: string; name: string };

export default function TeamPage() {
  const api = useApi();
  const [tick, setTick] = useState(0);
  const [editing, setEditing] = useState<TeamMember | null>(null);
  const [created, setCreated] = useState<CreatedInfo | null>(null);
  const form = useOverlayState();
  const members = useData((signal) => api.get<TeamMember[]>('/team/users', signal), [tick]);
  const catalogData = useData((signal) => api.get<FeatureCatalog>('/team/features', signal), []);
  const catalog = catalogData.data ?? FALLBACK_CATALOG;
  const labelOf = (id: Feature) => catalog.features.find((feature) => feature.id === id)?.label ?? FEATURE_LABELS[id] ?? id;
  const reload = () => setTick((value) => value + 1);

  return (
    <div className="space-y-6">
      <PageHeader title="Team" description="Add accounts for the people on your team and choose what each of them can open. Everyone in this company works on the same data."
        actions={<Button onPress={() => { setEditing(null); form.open(); }}><Plus className="size-4" aria-hidden="true" />Add member</Button>} />

      {created && (
        <Notice status="success" title={`Account for ${created.name} created`}>
          Share the email <strong className="break-all">{created.email}</strong> and the password <strong className="font-mono">{created.password}</strong> with them. The password is not shown again after you close this message.
          <span className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onPress={() => void copySignIn(created.email, created.password)}><Copy className="size-4" aria-hidden="true" />Copy sign-in details</Button>
            <Button size="sm" variant="tertiary" onPress={() => setCreated(null)}><X className="size-4" aria-hidden="true" />Close</Button>
          </span>
        </Notice>
      )}

      <ErrorNotice message={members.error} onRetry={members.reload} />
      {members.loading && !members.data && <SkeletonList rows={3} />}
      {members.data && (
        <Panel title="Accounts" description={`${members.data.length} ${members.data.length === 1 ? 'account' : 'accounts'} in this company.`}>
          {members.data.length === 0 ? <EmptyState compact icon={<UserRound />} title="No accounts yet" description="Add the first member of your team." /> : (
            <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border">
              {members.data.map((member) => (
                <li key={member.id} className="flex items-start gap-3 px-4 py-3.5 sm:px-5">
                  <Avatar color={member.is_locked ? 'danger' : 'accent'} className="shrink-0">
                    <Avatar.Fallback>{member.is_locked ? <LockKeyhole className="size-4" aria-label="Locked" /> : initials(member.display_name)}</Avatar.Fallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-body font-semibold break-words text-foreground">{member.display_name}</p>
                      {member.is_self && <Chip size="sm" variant="soft" color="accent">You</Chip>}
                    </div>
                    <p className="text-sm break-all text-muted">{member.email}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      <Chip size="sm" variant="soft" color={member.role === 'user' ? 'default' : 'accent'}>{ROLE_LABELS[member.role]}</Chip>
                      <Chip size="sm" variant="soft" color={member.is_locked ? 'danger' : 'success'}>{member.is_locked ? 'Locked' : 'Active'}</Chip>
                      {member.must_change_password && <Chip size="sm" variant="soft" color="warning">Must change password</Chip>}
                      <span className="text-caption text-muted">Since {dateOnly(member.created_at)}</span>
                      {member.last_login_at && <span className="text-caption text-muted">Last sign-in {relativeDateTime(member.last_login_at)}</span>}
                    </div>
                    <FeatureChips member={member} labelOf={labelOf} />
                  </div>
                  {member.editable && (
                    <Button size="sm" variant="secondary" className="shrink-0" aria-label={`Manage ${member.display_name}`} onPress={() => { setEditing(member); form.open(); }}>
                      <Pencil className="size-4" aria-hidden="true" /><span className="hidden sm:inline">Manage</span>
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      <Collapsible title="What can each role do?" icon={<ShieldCheck />}>
        <div className="grid gap-6 md:grid-cols-2">
          <div>
            <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground"><ShieldCheck className="size-4 text-accent" aria-hidden="true" />{ROLE_LABELS.company_admin}</p>
            <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-muted">
              <li>Opens everything, including Team and Settings</li>
              <li>Adds team members and other company admins, locks accounts and resets passwords</li>
              <li>Uploads chat exports and runs AI Review</li>
              <li>Sets the service areas, company keywords, sales watchlist, glossary, locations and advanced matching settings</li>
            </ul>
          </div>
          <div>
            <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground"><UserRound className="size-4 text-accent" aria-hidden="true" />{ROLE_LABELS.user}</p>
            <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-muted">
              <li>Opens only the features ticked for them when you add or manage the account</li>
              <li>Default: {catalog.default.map(labelOf).join(', ')}</li>
              <li>Can also be given Upload, Settings, Team or AI Review when you tick them</li>
              <li>Always has My account to change their own password</li>
            </ul>
          </div>
        </div>
      </Collapsible>

      <MemberDialog state={form} member={editing} catalog={catalog} onDone={(info) => { if (info) setCreated(info); reload(); }} />
    </div>
  );
}

/** The features a member can open as small chips; one "All features" chip for admins. */
function FeatureChips({ member, labelOf }: { member: TeamMember; labelOf: (id: Feature) => string }) {
  const everything = member.role !== 'user';
  const features = everything ? [] : FEATURE_KEYS.filter((id) => member.features?.includes(id));
  return (
    <div className="mt-2 flex flex-wrap gap-1.5" aria-label={`What ${member.display_name} can open`}>
      {everything
        ? <Chip size="sm" variant="soft" color="accent">All features</Chip>
        : features.length
          ? features.map((id) => <Chip key={id} size="sm" variant="soft" color="default">{labelOf(id)}</Chip>)
          : <span className="text-caption text-muted">Nothing granted yet</span>}
    </div>
  );
}

function MemberDialog({ state, member, catalog, onDone }: {
  state: ReturnType<typeof useOverlayState>; member: TeamMember | null; catalog: FeatureCatalog; onDone: (created?: CreatedInfo) => void;
}) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="md" scroll="inside">
        {/* Mounted per opening: the form starts from the chosen account (or empty) without any syncing effect. */}
        <Modal.Dialog>{state.isOpen && <MemberForm member={member} catalog={catalog} onClose={() => state.close()} onDone={onDone} />}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function MemberForm({ member, catalog, onClose, onDone }: {
  member: TeamMember | null; catalog: FeatureCatalog; onClose: () => void; onDone: (created?: CreatedInfo) => void;
}) {
  const api = useApi();
  const [name, setName] = useState(member?.display_name ?? '');
  const [email, setEmail] = useState(member?.email ?? '');
  const [password, setPassword] = useState(() => (member ? '' : generatePassword()));
  const [role, setRole] = useState<MemberRole>(member?.role === 'company_admin' ? 'company_admin' : 'user');
  // A member keeps the grants they have; a new member (or an admin turned into a member) starts from the default list.
  const [features, setFeatures] = useState<Feature[]>(() => (member?.role === 'user' ? FEATURE_KEYS.filter((id) => member.features?.includes(id)) : [...catalog.default]));
  const [locked, setLocked] = useState(member?.is_locked ?? false);
  const [show, setShow] = useState(!member);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const self = !!member?.is_self;
  // Nobody may lock or demote their own account (the API answers 400): those controls are off for yourself.
  const roleLocked = self;
  const temporary = !!member && !self && password.length > 0;

  const valid = !!name.trim() && /.+@.+\..+/.test(email) && (member ? !password || password.length >= MIN_PASSWORD : password.length >= MIN_PASSWORD);
  const toggleFeature = (id: Feature, on: boolean) => setFeatures((current) => FEATURE_KEYS.filter((key) => (key === id ? on : current.includes(key))));

  async function save() {
    if (!valid) return;
    setBusy(true); setError('');
    try {
      const grants = role === 'user' ? { features } : {};
      if (member) {
        await api.put(`/team/users/${member.id}`, {
          email: email.trim(), display_name: name.trim(), password: password || undefined, is_locked: locked,
          ...(roleLocked ? {} : { role }), ...grants,
        });
        toast.success(locked && !member.is_locked ? `${name.trim()} is locked` : 'Changes saved');
        onDone();
      } else {
        await api.post('/team/users', { email: email.trim(), display_name: name.trim(), password, role, ...grants });
        onDone({ email: email.trim(), password, name: name.trim() });
      }
      onClose();
    } catch (reason) { setError(errorMessage(reason, 'The account could not be saved. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <form className="contents" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <Modal.CloseTrigger />
      <Modal.Header><Modal.Heading>{member ? `Manage ${member.display_name}` : 'Add a team member'}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">
        <TextField value={name} onChange={setName} isRequired fullWidth>
          <Label className="text-sm font-semibold">Name</Label>
          <Input placeholder="e.g. Rina" maxLength={100} autoComplete="off" />
        </TextField>
        <TextField value={email} onChange={setEmail} isRequired type="email" fullWidth>
          <Label className="text-sm font-semibold">Email (to sign in)</Label>
          <Input placeholder="name@company.com" autoComplete="off" />
        </TextField>
        <TextField value={password} onChange={setPassword} isRequired={!member} type={show ? 'text' : 'password'} fullWidth>
          <Label className="text-sm font-semibold">{member ? 'New password' : 'Password'}</Label>
          <div className="relative">
            <Input className="pr-24 font-mono" autoComplete="new-password" />
            <div className="absolute top-1/2 right-1 flex -translate-y-1/2 gap-0.5">
              <button type="button" aria-label={show ? 'Hide password' : 'Show password'} onClick={() => setShow((value) => !value)} className="flex size-10 items-center justify-center rounded-xl text-muted hover:bg-default">{show ? <EyeOff className="size-5" aria-hidden="true" /> : <Eye className="size-5" aria-hidden="true" />}</button>
              <button type="button" aria-label="Create a password" onClick={() => { setPassword(generatePassword()); setShow(true); }} className="flex size-10 items-center justify-center rounded-xl text-accent hover:bg-default"><Wand2 className="size-5" aria-hidden="true" /></button>
            </div>
          </div>
          <Description className="text-caption">
            {member ? 'Leave empty to keep the current password. ' : ''}At least {MIN_PASSWORD} characters. The wand creates one that is easy to read out.
            {temporary && <span className="mt-1 block font-medium text-foreground">They will have to choose their own password at the next sign-in.</span>}
          </Description>
        </TextField>

        <Select value={role} onChange={(value) => setRole(value as MemberRole)} fullWidth aria-label="Role" isDisabled={roleLocked}>
          <Label className="text-sm font-semibold">Role</Label>
          <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
          <Select.Popover><ListBox>
            <ListBox.Item id="user" textValue={ROLE_LABELS.user}>{ROLE_LABELS.user}<ListBox.ItemIndicator /></ListBox.Item>
            <ListBox.Item id="company_admin" textValue={ROLE_LABELS.company_admin}>{ROLE_LABELS.company_admin}<ListBox.ItemIndicator /></ListBox.Item>
          </ListBox></Select.Popover>
          {roleLocked && <Description className="text-caption">You cannot change your own role. Ask another company admin.</Description>}
        </Select>

        {role === 'company_admin' ? (
          <p className="rounded-2xl border border-border bg-surface-secondary/60 px-4 py-3 text-sm text-muted">
            <ShieldCheck className="mr-1.5 inline size-4 align-[-3px] text-accent" aria-hidden="true" />Company admins can open everything.
          </p>
        ) : (
          <fieldset className="rounded-2xl border border-border p-4">
            <legend className="px-1 text-sm font-semibold text-foreground">What can they open?</legend>
            <p className="mb-3 text-caption text-muted">Tick the menus and actions this member may use. Untick everything they should not see.</p>
            <ul className="space-y-3">
              {catalog.features.map((feature) => (
                <li key={feature.id}>
                  <Switch isSelected={features.includes(feature.id)} onChange={(on) => toggleFeature(feature.id, on)} isDisabled={busy}>
                    <Switch.Content>
                      <Switch.Control><Switch.Thumb /></Switch.Control>
                      <span className="text-sm font-semibold">{feature.label}
                        {feature.description && <span className="mt-0.5 block font-normal text-muted">{feature.description}</span>}</span>
                    </Switch.Content>
                  </Switch>
                </li>
              ))}
            </ul>
            {features.length === 0 && <p className="mt-3 text-sm font-medium text-warning-soft-foreground">With nothing ticked this member can only open My account.</p>}
          </fieldset>
        )}

        {member && (
          <div className="rounded-2xl border border-border p-4">
            <Switch isSelected={locked} onChange={setLocked} isDisabled={self || busy}>
              <Switch.Content>
                <Switch.Control><Switch.Thumb /></Switch.Control>
                <span className="text-sm font-semibold"><KeyRound className="mr-1.5 inline size-4" aria-hidden="true" />Lock this account
                  <span className="mt-0.5 block font-normal text-muted">
                    {self ? 'You cannot lock your own account. Ask another company admin.' : 'A locked account cannot open any data until you unlock it.'}
                  </span></span>
              </Switch.Content>
            </Switch>
          </div>
        )}
        {error && <Notice status="danger">{error}</Notice>}
      </Modal.Body>
      <Modal.Footer>
        <Button slot="close" variant="tertiary">Cancel</Button>
        <Button type="submit" isPending={busy} isDisabled={!valid}>{member ? 'Save changes' : 'Create account'}</Button>
      </Modal.Footer>
    </form>
  );
}
