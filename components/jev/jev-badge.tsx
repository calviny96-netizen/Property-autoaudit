'use client';

import { Popover } from '@heroui/react';
import { Button as AriaButton } from 'react-aria-components';
import { Bot, Hourglass, TriangleAlert } from 'lucide-react';
import { DECISION_LABELS, DECISION_STYLES, DECISION_SYMBOLS, percent } from '@/lib/jev';
import { relativeDateTime } from '@/lib/format';
import type { JevDecision, JevLookup } from '@/lib/types';
import { cn } from '@/lib/utils';

/** "✓ Recommended", "✗ Rejected", "? Unsure" (or Failed / Waiting for pairs Jev has not answered). */
export function DecisionBadge({ decision, status = 'done', size = 'md', pendingLabel = 'Waiting', className }: {
  decision: JevDecision | null; status?: 'done' | 'failed' | 'pending'; size?: 'sm' | 'md'; pendingLabel?: string; className?: string;
}) {
  const key = status === 'done' && decision ? decision : status === 'done' ? 'unsure' : status;
  const label = key === 'failed' ? 'Failed' : key === 'pending' ? pendingLabel : DECISION_LABELS[key];
  return (
    <span className={cn('inline-flex shrink-0 items-center gap-1 rounded-full font-semibold whitespace-nowrap',
      size === 'sm' ? 'h-6 px-2 text-xs' : 'h-7 px-2.5 text-[0.8125rem]', DECISION_STYLES[key], className)}>
      {key === 'failed' ? <TriangleAlert className="size-3.5" aria-hidden="true" />
        : key === 'pending' ? <Hourglass className="size-3.5" aria-hidden="true" />
        : <span aria-hidden="true" className="font-bold">{DECISION_SYMBOLS[key]}</span>}
      {label}
    </span>
  );
}

/** Small "Jev" pill on a Match card (super admins): the latest review of this pair; tap for Jev's reasons. */
export function JevMatchBadge({ review }: { review: JevLookup }) {
  const decision = review.effective_decision;
  return (
    <div className="mt-2.5">
      <Popover>
        <AriaButton aria-label={`AI Review: ${DECISION_LABELS[decision]}. Show Jev's reasons`}
          className={cn('inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-full px-2.5 text-[0.8125rem] font-semibold outline-none hover:brightness-95 data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent/40', DECISION_STYLES[decision])}>
          <Bot className="size-3.5" aria-hidden="true" />Jev
          <span aria-hidden="true" className="font-bold">{DECISION_SYMBOLS[decision]}</span>
          {DECISION_LABELS[decision]}
          {review.confidence !== null && <span className="tabular-nums opacity-75">· {percent(review.confidence)}</span>}
        </AriaButton>
        <Popover.Content placement="top" className="max-w-[min(22rem,calc(100vw-2rem))]">
          <Popover.Dialog className="space-y-2 p-3.5">
            <Popover.Heading className="text-sm font-semibold">Jev: {DECISION_LABELS[decision]}{review.override_decision ? ' (set by an admin)' : ''}</Popover.Heading>
            {!!review.reasons.length && (
              <ul className="list-disc space-y-1 pl-4 text-sm text-foreground/85" lang="id">
                {review.reasons.map((reason, index) => <li key={index}>{reason}</li>)}
              </ul>
            )}
            <p className="text-caption text-muted">{review.model_label} · {relativeDateTime(review.reviewed_at)}</p>
          </Popover.Dialog>
        </Popover.Content>
      </Popover>
    </div>
  );
}
