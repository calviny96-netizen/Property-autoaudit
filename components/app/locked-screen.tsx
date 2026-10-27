'use client';

import { Button } from '@heroui/react';
import { LockKeyhole, LogOut, MessageCircle } from 'lucide-react';

/** Shown when a company admin or the platform administrator has locked this account. */
export function LockedScreen({ onLogout }: { onLogout: () => void }) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-10">
      <section className="xm-card w-full max-w-md p-6 text-center sm:p-8">
        <span aria-hidden="true" className="mx-auto flex size-14 items-center justify-center rounded-2xl bg-warning-soft text-warning-soft-foreground">
          <LockKeyhole className="size-7" />
        </span>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">Your account is locked</h1>
        <p className="mt-2 text-body text-muted">Access to this account has been paused. Contact your company admin to unlock it.</p>
        <a className="mt-7 flex min-h-11 items-center justify-center gap-2 rounded-xl bg-success px-4 text-sm font-semibold text-success-foreground transition-colors hover:bg-success-hover"
          href="https://wa.me/6282233744657" target="_blank" rel="noopener noreferrer">
          <MessageCircle className="size-4" aria-hidden="true" />Contact admin on WhatsApp
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
        <Button variant="tertiary" fullWidth className="mt-2" onPress={onLogout}><LogOut className="size-4" aria-hidden="true" />Sign out</Button>
      </section>
    </main>
  );
}
