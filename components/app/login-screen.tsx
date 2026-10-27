'use client';

import { useState, type ReactNode, type SyntheticEvent } from 'react';
import { Button, Input, Label, TextField } from '@heroui/react';
import { ArrowRight, Eye, EyeOff } from 'lucide-react';
import { Notice } from '@/components/app/primitives';
import type { User } from '@/lib/types';

const OFFLINE = "We can't reach the app right now. Make sure the service is running, then reload the page.";
const NETWORK = "We can't reach the app right now. Check your connection and try again.";
const SERVER = 'Something went wrong on our side. Please try again in a moment.';
const MIN_PASSWORD = 6;

type Mode = 'signin' | 'register';

/** A password field with a show / hide toggle (the only custom control on this screen). */
function PasswordField({ label, value, onChange, show, onToggle, autoComplete, placeholder, invalid, hint }: {
  label: string; value: string; onChange: (value: string) => void; show: boolean; onToggle: () => void;
  autoComplete: string; placeholder?: string; invalid?: boolean; hint?: ReactNode;
}) {
  return (
    <TextField isRequired type={show ? 'text' : 'password'} value={value} onChange={onChange} autoComplete={autoComplete} isInvalid={invalid} fullWidth>
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

function SignInForm({ onLogin, initialError }: { onLogin: (user: User) => void; initialError: string }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(initialError);

  async function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading || !email || !password) return;
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const body = (await response.json().catch(() => ({}))) as User & { detail?: unknown };
      if (!response.ok) {
        // 401/422 = wrong or malformed credentials; the API's own wording is not shown for those.
        if (response.status >= 500) throw new Error(SERVER);
        throw new Error(response.status === 401 || response.status === 400 || response.status === 422 || typeof body.detail !== 'string'
          ? 'Email or password is incorrect. Check them and try again.'
          : body.detail);
      }
      onLogin(body);
    } catch (reason) {
      setError(reason instanceof TypeError ? NETWORK : reason instanceof Error ? reason.message : 'Sign-in failed. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">Sign in</h1>
      <p className="mt-1.5 text-body text-muted">Use the email and password your company admin gave you.</p>
      <div className="mt-6 space-y-4">
        <TextField isRequired name="email" type="email" value={email} onChange={setEmail} autoComplete="username" fullWidth>
          <Label className="text-sm font-semibold">Email</Label>
          <Input className="h-11" placeholder="name@company.com" />
        </TextField>
        <PasswordField label="Password" value={password} onChange={setPassword} show={show} onToggle={() => setShow((value) => !value)}
          autoComplete="current-password" placeholder="Enter your password" />
      </div>
      {error && <div className="mt-5"><Notice status="danger">{error}</Notice></div>}
      <Button type="submit" size="lg" fullWidth className="mt-6" isPending={loading} isDisabled={!email || !password}>
        {loading ? 'Signing in…' : <>Sign in<ArrowRight className="size-4" aria-hidden="true" /></>}
      </Button>
      <p className="mt-5 text-center text-caption text-muted">Forgot your password? Ask your company admin (or the platform admin) to reset it.</p>
    </form>
  );
}

function RegisterForm({ onLogin }: { onLogin: (user: User) => void }) {
  const [company, setCompany] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [show, setShow] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const short = !!password && password.length < MIN_PASSWORD;
  const mismatch = !!again && password !== again;
  const ready = !!company.trim() && !!email.trim() && password.length >= MIN_PASSWORD && password === again;

  async function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading || !ready) return;
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ company_name: company.trim(), email: email.trim(), password, confirm_password: again }),
      });
      const body = (await response.json().catch(() => ({}))) as User & { detail?: unknown };
      if (!response.ok) {
        if (response.status === 409) throw new Error('This email is already registered. Sign in instead.');
        if (response.status === 400) throw new Error('The passwords do not match yet.');
        if (response.status === 422) throw new Error(`Use at least ${MIN_PASSWORD} characters for the password and a valid email address.`);
        if (response.status >= 500) throw new Error(SERVER);
        throw new Error(typeof body.detail === 'string' ? body.detail : 'Registration failed. Please try again.');
      }
      onLogin(body);
    } catch (reason) {
      setError(reason instanceof TypeError ? NETWORK : reason instanceof Error ? reason.message : 'Registration failed. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">Register your company</h1>
      <p className="mt-1.5 text-body text-muted">Create the first account for your company or brand. The administrator reviews every registration before it gets access.</p>
      <div className="mt-6 space-y-4">
        <TextField isRequired name="company_name" value={company} onChange={setCompany} autoComplete="organization" fullWidth>
          <Label className="text-sm font-semibold">Company or brand name</Label>
          <Input className="h-11" placeholder="e.g. Rumah Kita Property" />
        </TextField>
        <TextField isRequired name="email" type="email" value={email} onChange={setEmail} autoComplete="email" fullWidth>
          <Label className="text-sm font-semibold">Email</Label>
          <Input className="h-11" placeholder="name@company.com" />
        </TextField>
        <PasswordField label="Password" value={password} onChange={setPassword} show={show} onToggle={() => setShow((value) => !value)}
          autoComplete="new-password" placeholder={`At least ${MIN_PASSWORD} characters`} invalid={short}
          hint={short ? `Use at least ${MIN_PASSWORD} characters.` : undefined} />
        <PasswordField label="Repeat password" value={again} onChange={setAgain} show={show} onToggle={() => setShow((value) => !value)}
          autoComplete="new-password" placeholder="Type the password again" invalid={mismatch}
          hint={mismatch ? 'The passwords do not match yet.' : undefined} />
      </div>
      {error && <div className="mt-5"><Notice status="danger">{error}</Notice></div>}
      <Button type="submit" size="lg" fullWidth className="mt-6" isPending={loading} isDisabled={!ready}>
        {loading ? 'Registering…' : 'Register'}
      </Button>
    </form>
  );
}

/** Sign-in and company registration: one white card on a dark page. */
export function LoginScreen({ onLogin, offline }: { onLogin: (user: User) => void; offline?: boolean }) {
  const [mode, setMode] = useState<Mode>('signin');
  return (
    <main className="flex min-h-dvh items-center justify-center bg-[#111] px-4 py-8 sm:py-12">
      <section className="w-full max-w-[26rem] rounded-2xl bg-white p-6 text-foreground shadow-[0_24px_60px_rgb(0_0_0/45%)] sm:p-8">
        <div className="mb-6 flex justify-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand-auto-audit.png" alt="Property Auto Audit" width={1024} height={418} className="h-12 w-auto" />
        </div>
        {mode === 'signin'
          ? <SignInForm onLogin={onLogin} initialError={offline ? OFFLINE : ''} />
          : <RegisterForm onLogin={onLogin} />}
        <p className="mt-6 border-t border-separator pt-5 text-center text-sm text-muted">
          {mode === 'signin' ? (
            <>New here?{' '}
              <button type="button" onClick={() => setMode('register')} className="font-semibold text-accent hover:underline">Register your company or brand</button>
            </>
          ) : (
            <>Already have an account?{' '}
              <button type="button" onClick={() => setMode('signin')} className="font-semibold text-accent hover:underline">Sign in</button>
            </>
          )}
        </p>
      </section>
    </main>
  );
}
