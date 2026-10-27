'use client';

import { useState } from 'react';
import { Avatar, Button, Input, Label, TextField, toast } from '@heroui/react';
import { Building2, KeyRound, LogOut, Mail } from 'lucide-react';
import { Notice, Panel } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { initials, ROLE_LABELS } from '@/lib/format';
import { useCompany, useSession } from '@/lib/session';
import { useApi } from '@/lib/workspace-context';

/** The API accepts passwords of 6 characters or more (POST /auth/password/first, PUT /auth/password). */
const MIN_PASSWORD = 6;

/** Settings → My account: who you are signed in as, a new password, and sign out. */
export function AccountTab() {
  const api = useApi();
  const { user, logout } = useSession();
  const { company } = useCompany();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mismatch = !!again && next !== again;
  const short = !!next && next.length < MIN_PASSWORD;

  async function change() {
    if (!current || next.length < MIN_PASSWORD || next !== again) return;
    setBusy(true); setError('');
    try {
      await api.put('/auth/password', { current_password: current, new_password: next });
      toast.success('Password changed', { description: 'Other devices need to sign in again.' });
      setCurrent(''); setNext(''); setAgain('');
    } catch (reason) { setError(errorMessage(reason, 'The password could not be changed. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <Panel>
        <div className="flex items-center gap-4">
          <Avatar color="accent" size="lg" className="size-14 shrink-0 text-lg"><Avatar.Fallback>{initials(user.display_name)}</Avatar.Fallback></Avatar>
          <div className="min-w-0">
            <p className="text-section font-semibold break-words text-foreground">{user.display_name}</p>
            <p className="mt-0.5 flex items-center gap-1.5 text-sm break-all text-muted"><Mail className="size-4 shrink-0" aria-hidden="true" />{user.email}</p>
            <p className="mt-0.5 flex items-center gap-1.5 text-sm text-muted"><Building2 className="size-4 shrink-0" aria-hidden="true" />{[company?.company_name, ROLE_LABELS[user.role]].filter(Boolean).join(' · ')}</p>
          </div>
        </div>
      </Panel>

      <Panel title={<span className="flex items-center gap-2"><KeyRound className="size-5 text-accent" aria-hidden="true" />Change password</span>} description={`Use at least ${MIN_PASSWORD} characters.`}>
        <form className="max-w-md space-y-4" onSubmit={(event) => { event.preventDefault(); void change(); }}>
          <TextField type="password" value={current} onChange={setCurrent} fullWidth isRequired>
            <Label className="text-sm font-semibold">Current password</Label>
            <Input autoComplete="current-password" />
          </TextField>
          <TextField type="password" value={next} onChange={setNext} fullWidth isRequired isInvalid={short}>
            <Label className="text-sm font-semibold">New password</Label>
            <Input autoComplete="new-password" />
            {short && <p className="mt-1 text-sm text-danger">Use at least {MIN_PASSWORD} characters.</p>}
          </TextField>
          <TextField type="password" value={again} onChange={setAgain} fullWidth isRequired isInvalid={mismatch}>
            <Label className="text-sm font-semibold">Repeat the new password</Label>
            <Input autoComplete="new-password" />
            {mismatch && <p className="mt-1 text-sm text-danger">The passwords do not match yet.</p>}
          </TextField>
          {error && <Notice status="danger">{error}</Notice>}
          <Button type="submit" isPending={busy} isDisabled={!current || next.length < MIN_PASSWORD || next !== again}>Change password</Button>
        </form>
      </Panel>

      <Button variant="danger-soft" onPress={logout}><LogOut className="size-4" aria-hidden="true" />Sign out</Button>
    </div>
  );
}
