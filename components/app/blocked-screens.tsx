'use client';

import { useState, type ReactNode, type SyntheticEvent } from 'react';
import { Button, Input, Label, TextField } from '@heroui/react';
import { Ban, Eye, EyeOff, Hourglass, KeyRound, LogOut, MessageCircle } from 'lucide-react';
import { Notice } from '@/components/app/primitives';
import type { CompanyStatus, User } from '@/lib/types';
import { cn } from '@/lib/utils';

const WHATSAPP = 'https://wa.me/6282233744657';
const MIN_PASSWORD = 6;

/** The centered card every blocked state uses (same look as LockedScreen). */
function BlockedCard({ icon, tone, title, children, center = true }: {
  icon: ReactNode; tone: 'warning' | 'danger' | 'accent'; title: string; children: ReactNode; center?: boolean;
}) {
  const tones = {
    warning: 'bg-warning-soft text-warning-soft-foreground',
    danger: 'bg-danger-soft text-danger-soft-foreground',
    accent: 'bg-accent-soft text-accent-soft-foreground',
  };
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-10">
      <section className={cn('xm-card w-full max-w-md p-6 sm:p-8', center && 'text-center')}>
        <span aria-hidden="true" className={cn('flex size-14 items-center justify-center rounded-2xl [&_svg]:size-7', center && 'mx-auto', tones[tone])}>{icon}</span>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">{title}</h1>
        {children}
      </section>
    </main>
  );
}

function WhatsAppLink() {
  return (
    <a className="mt-7 flex min-h-11 items-center justify-center gap-2 rounded-xl bg-success px-4 text-sm font-semibold text-success-foreground transition-colors hover:bg-success-hover"
      href={WHATSAPP} target="_blank" rel="noopener noreferrer">
      <MessageCircle className="size-4" aria-hidden="true" />Contact admin on WhatsApp
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

function SignOutButton({ onLogout, className }: { onLogout: () => void; className?: string }) {
  return <Button variant="tertiary" fullWidth className={className} onPress={onLogout}><LogOut className="size-4" aria-hidden="true" />Sign out</Button>;
}

/** The company registered itself and the platform admin has not approved it yet. */
export function PendingScreen({ user, onLogout }: { user: User; onLogout: () => void }) {
  return (
    <BlockedCard icon={<Hourglass />} tone="warning" title="Registration received">
      <p className="mt-2 text-body text-muted">
        Your company <strong className="font-semibold text-foreground">{user.company_name || 'account'}</strong> is being reviewed by the administrator.
        You will get access as soon as it is approved.
      </p>
      <p className="mt-3 text-caption text-muted">This page updates automatically. You can leave it open or come back later.</p>
      <WhatsAppLink />
      <SignOutButton onLogout={onLogout} className="mt-2" />
    </BlockedCard>
  );
}

/** The company was deactivated by the platform admin, or its registration was turned down. */
export function InactiveScreen({ status, user, onLogout }: { status: Extract<CompanyStatus, 'inactive' | 'rejected'>; user: User; onLogout: () => void }) {
  const rejected = status === 'rejected';
  return (
    <BlockedCard icon={<Ban />} tone="danger" title={rejected ? 'Registration not approved' : 'Company deactivated'}>
      <p className="mt-2 text-body text-muted">
        {rejected
          ? <>Your registration{user.company_name ? <> for <strong className="font-semibold text-foreground">{user.company_name}</strong></> : null} was not approved.</>
          : 'Your company has been deactivated. Contact the administrator to reactivate it.'}
      </p>
      {rejected && <p className="mt-2 text-body text-muted">If you think this is a mistake, contact the administrator.</p>}
      <WhatsAppLink />
      <SignOutButton onLogout={onLogout} className="mt-2" />
    </BlockedCard>
  );
}

function PasswordField({ label, value, onChange, show, onToggle, invalid, hint, placeholder }: {
  label: string; value: string; onChange: (value: string) => void; show: boolean; onToggle: () => void; invalid?: boolean; hint?: ReactNode; placeholder?: string;
}) {
  return (
    <TextField isRequired type={show ? 'text' : 'password'} value={value} onChange={onChange} autoComplete="new-password" isInvalid={invalid} fullWidth>
      <Label className="text-sm font-semibold">{label}</Label>
      <div className="relative">
        <Input className="h-11 pr-12" placeholder={placeholder} />
        <button type="button" onClick={onToggle} aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}
          className="absolute top-1/2 right-1 flex size-9 -translate-y-1/2 items-center justify-center rounded-lg text-muted hover:bg-default hover:text-foreground">
          {show ? <EyeOff className="size-[1.125rem]" aria-hidden="true" /> : <Eye className="size-[1.125rem]" aria-hidden="true" />}
        </button>
      </div>
      {hint && <p className="mt-1 text-sm text-danger">{hint}</p>}
    </TextField>
  );
}

/** Signed in with a password an admin set: a personal one must be chosen before anything else. */
export function SetPasswordScreen({ user, onDone, onLogout }: { user: User; onDone: () => void; onLogout: () => void }) {
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const short = !!next && next.length < MIN_PASSWORD;
  const mismatch = !!again && next !== again;
  const ready = next.length >= MIN_PASSWORD && next === again;

  async function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !ready) return;
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/auth/password/first', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ new_password: next, confirm_password: again }),
      });
      if (response.status === 204 || response.ok) { onDone(); return; }
      const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
      if (response.status >= 500) throw new Error('Something went wrong on our side. Please try again in a moment.');
      throw new Error(typeof body.detail === 'string' ? body.detail : `Use at least ${MIN_PASSWORD} characters and type the same password twice.`);
    } catch (reason) {
      setError(reason instanceof TypeError ? "We can't reach the app right now. Check your connection and try again." : reason instanceof Error ? reason.message : 'The password could not be saved. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <BlockedCard icon={<KeyRound />} tone="accent" title="Choose a new password" center={false}>
      <p className="mt-2 text-body text-muted">
        The password you signed in with was set by an admin and is temporary. Pick your own password to continue{user.email ? <> as <strong className="font-semibold text-foreground">{user.email}</strong></> : null}.
      </p>
      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <PasswordField label="New password" value={next} onChange={setNext} show={show} onToggle={() => setShow((value) => !value)}
          placeholder={`At least ${MIN_PASSWORD} characters`} invalid={short} hint={short ? `Use at least ${MIN_PASSWORD} characters.` : undefined} />
        <PasswordField label="Repeat the new password" value={again} onChange={setAgain} show={show} onToggle={() => setShow((value) => !value)}
          placeholder="Type the password again" invalid={mismatch} hint={mismatch ? 'The passwords do not match yet.' : undefined} />
        {error && <Notice status="danger">{error}</Notice>}
        <Button type="submit" size="lg" fullWidth isPending={busy} isDisabled={!ready}>{busy ? 'Saving…' : 'Save password'}</Button>
      </form>
      <SignOutButton onLogout={onLogout} className="mt-2" />
    </BlockedCard>
  );
}
