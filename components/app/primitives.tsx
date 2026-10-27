'use client';

/*
 * Shared building blocks for every page. Read DESIGN.md (scratchpad) before adding a new
 * pattern: layout (PageHeader, Section, Panel, FilterGroup, DetailList, PagerBar), numbers
 * (StatCard), match status (StatusBadge, StatusFilter, StatusLegend), feedback (EmptyState,
 * Notice, ErrorNotice, LoadingIndicator, skeletons), disclosure (Collapsible, AdvancedSection,
 * InfoHint), reasons (ReasonList) and small chips/controls.
 */

import { useState, type ReactNode } from 'react';
import { AlertDialog, Alert, Button, Chip, Popover, Skeleton, Spinner, ToggleButton, ToggleButtonGroup, toast } from '@heroui/react';
import { Button as AriaButton } from 'react-aria-components';
import {
  Check, ChevronDown, ChevronLeft, ChevronRight, CircleDashed, Copy, Flame, Inbox, Info, MapPinSearch, SlidersHorizontal,
  ThermometerSun, TriangleAlert, type LucideIcon,
} from 'lucide-react';
import { LottiePlayer, type LottieName } from '@/components/lottie/lottie-player';
import { NumberTicker } from '@/components/magicui/number-ticker';
import { MATCH_STATUSES, MATCH_STATUS_HINTS, MATCH_STATUS_LABELS, STATUS_LABELS, number } from '@/lib/format';
import { splitReasons } from '@/lib/reasons';
import type { EntityStatus, MatchStatus, Temperature } from '@/lib/types';
import { cn } from '@/lib/utils';

/* --------------------------------------------------------------- page layout */

/** Top of every page: one title, one sentence of help, and the page's main actions on the right. */
export function PageHeader({ title, description, actions, eyebrow, back, className }: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode;
  /** Optional "← Back" link above the title (detail pages, wizards). */
  back?: { label: string; onPress: () => void }; className?: string;
}) {
  return (
    <header className={cn('mb-6 flex flex-col gap-4 sm:mb-8 sm:flex-row sm:items-end sm:justify-between', className)}>
      <div className="min-w-0">
        {back && (
          <button type="button" onClick={back.onPress} className="-ml-1 mb-2 inline-flex min-h-9 items-center gap-1 rounded-lg px-1 text-sm font-medium text-muted hover:text-foreground">
            <ChevronLeft className="size-4" aria-hidden="true" />{back.label}
          </button>
        )}
        {eyebrow && <p className="mb-1 text-overline font-semibold text-accent uppercase">{eyebrow}</p>}
        <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-page">{title}</h1>
        {description && <p className="mt-1.5 max-w-3xl text-body text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2 sm:shrink-0 sm:justify-end">{actions}</div>}
    </header>
  );
}

/** A titled group of content without a card around it (for stacking several Panels or a grid of cards). */
export function Section({ title, description, actions, children, className }: {
  title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cn('min-w-0 space-y-3', className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            {title && <h2 className="text-section font-semibold text-foreground">{title}</h2>}
            {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

/** The standard white card. `action`/`actions` sit top-right, `footer` gets a quiet bar at the bottom. */
export function Panel({ title, description, action, actions, footer, children, className, bodyClassName, flush }: {
  title?: ReactNode; description?: ReactNode; action?: ReactNode; actions?: ReactNode; footer?: ReactNode;
  children: ReactNode; className?: string; bodyClassName?: string;
  /** No body padding (tables, lists that run edge to edge). */
  flush?: boolean;
}) {
  const side = actions ?? action;
  const heading = !!(title || description);
  const hasHeader = heading || !!side;
  return (
    <section className={cn('xm-card min-w-0', className)}>
      {hasHeader && (
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 sm:px-6 sm:pt-5">
          {/* The title keeps at least 12rem: wide actions (text buttons on a phone) wrap below instead of squeezing it. */}
          {heading && (
            <div className="min-w-[min(100%,12rem)] flex-1">
              {title && <h2 className="text-section font-semibold text-foreground">{title}</h2>}
              {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
            </div>
          )}
          {side && <div className={cn('flex flex-wrap items-center gap-2', !heading && 'ml-auto')}>{side}</div>}
        </div>
      )}
      <div className={cn(flush ? '' : 'p-4 sm:p-6', !flush && hasHeader && 'pt-4 sm:pt-4', flush && hasHeader && 'mt-4', bodyClassName)}>{children}</div>
      {footer && <div className="flex flex-wrap items-center justify-between gap-3 rounded-b-[inherit] border-t border-separator bg-surface-secondary/60 px-4 py-3 sm:px-6">{footer}</div>}
    </section>
  );
}

/** A label above a filter control (period picker, status chips, a search box). */
export function FilterGroup({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn('min-w-0 space-y-2', className)}>
      <p className="text-sm font-semibold text-foreground">{label}</p>
      {children}
      {hint && <p className="text-caption text-muted">{hint}</p>}
    </div>
  );
}

/** Label / value pairs for detail panels and modals. Empty values are left out. */
export function DetailList({ items, columns = 1, className }: { items: { label: ReactNode; value: ReactNode }[]; columns?: 1 | 2; className?: string }) {
  const shown = items.filter((item) => item.value !== null && item.value !== undefined && item.value !== '' && item.value !== false);
  return (
    <dl className={cn('grid gap-x-6 gap-y-3', columns === 2 && 'sm:grid-cols-2', className)}>
      {shown.map((item, index) => (
        <div key={index} className="min-w-0">
          <dt className="text-caption text-muted">{item.label}</dt>
          <dd className="mt-0.5 text-body font-medium break-words text-foreground">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** "Showing 1–200 of 1,240" with Previous / Next. */
export function PagerBar({ from, to, total, hasPrev, hasNext, onPrev, onNext, loading, className }: {
  from: number; to: number; total?: number; hasPrev: boolean; hasNext: boolean; onPrev: () => void; onNext: () => void; loading?: boolean; className?: string;
}) {
  return (
    <nav aria-label="Pages" className={cn('flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-surface px-4 py-2.5', className)}>
      <span className="text-sm text-muted">Showing <strong className="font-semibold text-foreground tabular-nums">{number(from)}–{number(to)}</strong>{total !== undefined ? <> of <span className="tabular-nums">{number(total)}</span></> : null}</span>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" isDisabled={!hasPrev || loading} onPress={onPrev}><ChevronLeft className="size-4" aria-hidden="true" />Previous</Button>
        <Button variant="secondary" size="sm" isDisabled={!hasNext || loading} onPress={onNext}>Next<ChevronRight className="size-4" aria-hidden="true" /></Button>
      </div>
    </nav>
  );
}

/* --------------------------------------------------------------- numbers */

const tones = {
  blue: 'bg-accent-soft text-accent-soft-foreground',
  cyan: 'bg-cyan-50 text-cyan-800',
  indigo: 'bg-indigo-50 text-indigo-800',
  violet: 'bg-custom-soft text-custom-foreground',
  green: 'bg-success-soft text-success-soft-foreground',
  amber: 'bg-warning-soft text-warning-soft-foreground',
  red: 'bg-danger-soft text-danger-soft-foreground',
  slate: 'bg-default text-default-foreground',
  hot: 'bg-hot-soft text-hot-foreground',
  warm: 'bg-warm-soft text-warm-foreground',
  custom: 'bg-custom-soft text-custom-foreground',
  unmatched: 'bg-unmatched-soft text-unmatched-foreground',
} as const;
export type Tone = keyof typeof tones;

export function IconBadge({ icon, tone = 'blue', size = 'md', className }: { icon: ReactNode; tone?: Tone; size?: 'sm' | 'md'; className?: string }) {
  return (
    <span aria-hidden="true" className={cn('flex shrink-0 items-center justify-center', size === 'sm' ? 'size-9 rounded-xl [&_svg]:size-[1.125rem]' : 'size-11 rounded-2xl [&_svg]:size-5', tones[tone], className)}>
      {icon}
    </span>
  );
}

/** "▲ 12%" against the previous period. Null = nothing to compare with (renders nothing). */
export function ChangeBadge({ change }: { change: number | null }) {
  if (change === null) return null;
  const up = change >= 0;
  return (
    <span className={cn('inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-semibold tabular-nums', up ? 'bg-success-soft text-success-soft-foreground' : 'bg-danger-soft text-danger-soft-foreground')}>
      <span aria-hidden="true">{up ? '▲' : '▼'}</span>
      {Math.abs(change) > 999 ? '>999' : Math.abs(change).toLocaleString('en-US', { maximumFractionDigits: 1 })}%
      <span className="sr-only">{up ? ' up' : ' down'} from the previous period</span>
    </span>
  );
}

/** A headline number (KPI). Numbers count up once when first shown; strings are shown as they are. */
export function StatCard({ label, value, icon, tone = 'blue', change, hint, loading, onPress, className }: {
  label: ReactNode; value: number | string; icon?: ReactNode; tone?: Tone;
  change?: number | null; hint?: ReactNode; loading?: boolean;
  /** Makes the whole card a button (e.g. jump to the filtered list). */
  onPress?: () => void; className?: string;
}) {
  const body = (
    <>
      <span className="flex items-start justify-between gap-3">
        <span className="text-sm leading-5 font-medium text-muted">{label}</span>
        {icon && <IconBadge icon={icon} tone={tone} size="sm" />}
      </span>
      {loading ? <Skeleton className="mt-2 h-8 w-24 rounded-lg" /> : (
        <span className="mt-1 block text-[1.75rem] leading-9 font-semibold tracking-tight text-foreground tabular-nums">
          {typeof value === 'number' ? <NumberTicker value={value} key={value} /> : value}
        </span>
      )}
      {!loading && (change !== undefined || hint) && (
        <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
          {change !== undefined && <ChangeBadge change={change} />}
          {hint && <span className="text-caption text-muted">{hint}</span>}
        </span>
      )}
    </>
  );
  const base = 'xm-card flex h-full w-full flex-col p-4 text-left sm:p-5';
  return onPress
    ? <button type="button" onClick={onPress} className={cn(base, 'xm-card-hover cursor-pointer', className)}>{body}</button>
    : <div className={cn(base, className)}>{body}</div>;
}

/** Older name for StatCard (numeric value, icon required). New code: use StatCard. */
export function StatTile({ label, value, icon, tone = 'blue', change, hint, loading }: {
  label: string; value: number; icon: ReactNode; tone?: Tone; change?: number | null; hint?: ReactNode; loading?: boolean;
}) {
  return <StatCard label={label} value={value} icon={icon} tone={tone} change={change} hint={hint} loading={loading} />;
}

/* --------------------------------------------------------------- match status */

const STATUS_ICON: Record<MatchStatus, LucideIcon> = { hot: Flame, warm: ThermometerSun, custom: MapPinSearch, unmatched: CircleDashed };
const STATUS_STYLE: Record<MatchStatus, { badge: string; dot: string; on: string }> = {
  hot: { badge: 'bg-hot-soft text-hot-foreground', dot: 'bg-hot', on: 'border-hot/45 bg-hot-soft text-hot-foreground' },
  warm: { badge: 'bg-warm-soft text-warm-foreground', dot: 'bg-warm', on: 'border-warm/60 bg-warm-soft text-warm-foreground' },
  custom: { badge: 'bg-custom-soft text-custom-foreground', dot: 'bg-custom', on: 'border-custom/45 bg-custom-soft text-custom-foreground' },
  unmatched: { badge: 'bg-unmatched-soft text-unmatched-foreground', dot: 'bg-unmatched', on: 'border-unmatched/50 bg-unmatched-soft text-unmatched-foreground' },
};
const STATUS_TITLE: Record<MatchStatus, string> = { hot: 'Hot match', warm: 'Warm match', custom: 'Custom match', unmatched: 'No match' };

/**
 * Hot / Warm / Custom / No match pill. A Custom badge is a small button that explains itself on tap
 * (phones have no hover). Inside another button or link, pass `explain={false}`.
 */
export function StatusBadge({ status, count, size = 'md', explain, showIcon = true, className }: {
  status: MatchStatus; count?: number; size?: 'sm' | 'md';
  /** Tap/click shows what the status means. Default: on for 'custom', off for the others. */
  explain?: boolean; showIcon?: boolean; className?: string;
}) {
  const Icon = STATUS_ICON[status];
  const label = MATCH_STATUS_LABELS[status];
  const hoverHint = status === 'custom' ? MATCH_STATUS_HINTS.custom : undefined;
  const classes = cn(
    'inline-flex shrink-0 items-center gap-1 rounded-full font-semibold whitespace-nowrap',
    size === 'sm' ? 'h-6 px-2 text-xs' : 'h-7 px-2.5 text-[0.8125rem]',
    STATUS_STYLE[status].badge, className,
  );
  const content = (
    <>
      {showIcon && <Icon className={size === 'sm' ? 'size-3' : 'size-3.5'} aria-hidden="true" />}
      <span>{label}</span>
      {count !== undefined && <><span aria-hidden="true" className="opacity-50">·</span><span className="tabular-nums">{number(count)}</span></>}
    </>
  );
  const interactive = explain ?? status === 'custom';
  if (!interactive) return <span className={classes} title={hoverHint}>{content}</span>;
  return (
    <Popover>
      <AriaButton aria-label={`${label}${count !== undefined ? `, ${count}` : ''}. What does this mean?`}
        className={cn(classes, 'cursor-pointer outline-none hover:brightness-95 data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent/40')}>
        {content}<Info className={size === 'sm' ? 'size-3 opacity-70' : 'size-3.5 opacity-70'} aria-hidden="true" />
      </AriaButton>
      <Popover.Content placement="top" className="max-w-[min(20rem,calc(100vw-2rem))]">
        <Popover.Dialog className="p-3.5">
          <Popover.Heading className="text-sm font-semibold">{STATUS_TITLE[status]}</Popover.Heading>
          <p className="mt-1 text-sm text-muted">{MATCH_STATUS_HINTS[status]}</p>
        </Popover.Dialog>
      </Popover.Content>
    </Popover>
  );
}

/** Older name: one pair status chip (never interactive, so it is safe inside clickable cards). */
export function TemperatureChip({ temperature, count, className }: { temperature: Temperature; count?: number; className?: string }) {
  return <StatusBadge status={temperature} count={count} explain={false} className={className} />;
}

/** Multi-select status chips (Hot, Warm, Custom, No match). Keeps at least one selected unless `allowEmpty`. */
export function StatusFilter({ value, onChange, options = MATCH_STATUSES, counts, label = 'Match status', allowEmpty = false, isDisabled, className }: {
  value: readonly MatchStatus[]; onChange: (next: MatchStatus[]) => void;
  options?: readonly MatchStatus[]; counts?: Partial<Record<MatchStatus, number>>;
  label?: string; allowEmpty?: boolean; isDisabled?: boolean; className?: string;
}) {
  function toggle(status: MatchStatus) {
    const on = value.includes(status);
    if (on && value.length === 1 && !allowEmpty) return;
    const next = on ? value.filter((item) => item !== status) : [...value, status];
    onChange(options.filter((item) => next.includes(item)));
  }
  // No m-0: it would override a parent's space-y-* gap. Preflight already zeroes fieldset margins.
  return (
    <fieldset aria-label={label} className={cn('flex min-w-0 flex-wrap gap-2 border-0 p-0', className)}>
      {options.map((status) => {
        const on = value.includes(status);
        const count = counts?.[status];
        return (
          <button key={status} type="button" aria-pressed={on} disabled={isDisabled} onClick={() => toggle(status)} title={MATCH_STATUS_HINTS[status]}
            className={cn(
              'inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-10',
              on ? STATUS_STYLE[status].on : 'border-border bg-surface text-muted hover:border-border-secondary hover:text-foreground',
            )}>
            {on ? <Check className="size-4" strokeWidth={2.5} aria-hidden="true" /> : <span aria-hidden="true" className={cn('size-2 rounded-full', STATUS_STYLE[status].dot)} />}
            {MATCH_STATUS_LABELS[status]}
            {count !== undefined && <span className="tabular-nums opacity-75">{number(count)}</span>}
          </button>
        );
      })}
    </fieldset>
  );
}

/** What each status means, for filter panels and help text (works on phones, unlike hover hints). */
export function StatusLegend({ statuses = MATCH_STATUSES, columns = 1, className }: { statuses?: readonly MatchStatus[]; columns?: 1 | 2; className?: string }) {
  return (
    <dl className={cn('grid gap-x-6 gap-y-2.5', columns === 2 && 'md:grid-cols-2', className)}>
      {statuses.map((status) => (
        <div key={status} className="flex items-start gap-2.5">
          <dt className="w-24 shrink-0"><StatusBadge status={status} size="sm" explain={false} /></dt>
          <dd className="text-caption text-muted">{MATCH_STATUS_HINTS[status]}</dd>
        </div>
      ))}
    </dl>
  );
}

/* --------------------------------------------------------------- reasons */

/**
 * Why a pair matches: positive reasons with a check, then "Needs check" items with an info icon.
 * Internal summary lines ("Location 100%", "Asset category …", percentages) are never shown.
 */
export function ReasonList({ reasons, limit = 4, className }: { reasons?: readonly string[] | null; limit?: number; className?: string }) {
  const [all, setAll] = useState(false);
  const { positive, checks } = splitReasons(reasons);
  if (!positive.length && !checks.length) return null;
  const shownPositive = all ? positive : positive.slice(0, limit);
  const shownChecks = all ? checks : checks.slice(0, 3);
  const hidden = positive.length - shownPositive.length + checks.length - shownChecks.length;
  return (
    <div className={cn('space-y-1.5', className)}>
      <ul className="space-y-1.5" aria-label="Why this matches">
        {shownPositive.map((text) => (
          <li key={`p:${text}`} className="flex items-start gap-2 text-sm leading-5 text-foreground/90">
            <Check className="mt-0.5 size-4 shrink-0 text-success" strokeWidth={2.5} aria-hidden="true" />
            <span className="min-w-0 break-words">{text}</span>
          </li>
        ))}
        {shownChecks.map((text) => (
          <li key={`c:${text}`} className="flex items-start gap-2 text-sm leading-5 text-foreground/90">
            <Info className="mt-0.5 size-4 shrink-0 text-warm-foreground" aria-hidden="true" />
            <span className="min-w-0 break-words"><span className="font-semibold text-warm-foreground">Needs check: </span>{text}</span>
          </li>
        ))}
      </ul>
      {(positive.length > limit || checks.length > 3) && (
        <button type="button" onClick={() => setAll((value) => !value)} className="min-h-8 text-sm font-semibold text-accent hover:underline">
          {all ? 'Show less' : `Show ${hidden} more`}
        </button>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- empty / error / loading */

export function EmptyState({ animation, icon, title, description, action, compact, className }: {
  /** A Lottie animation instead of the default calm icon (use sparingly: first-run and celebration moments). */
  animation?: LottieName; icon?: ReactNode; title: ReactNode; description?: ReactNode; action?: ReactNode; compact?: boolean; className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center px-4 text-center', compact ? 'py-8' : 'py-12 sm:py-16', className)}>
      {animation ? <LottiePlayer name={animation} className={compact ? 'w-28' : 'w-36'} /> : (
        <span aria-hidden="true" className="flex size-12 items-center justify-center rounded-2xl bg-default text-muted [&_svg]:size-6">{icon ?? <Inbox />}</span>
      )}
      <h3 className="mt-4 text-section font-semibold text-foreground">{title}</h3>
      {description && <p className="mt-1.5 max-w-md text-body text-muted">{description}</p>}
      {action && <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

export function Notice({ status = 'default', title, children, action, className }: {
  status?: 'default' | 'accent' | 'success' | 'warning' | 'danger'; title?: ReactNode; children?: ReactNode; action?: ReactNode; className?: string;
}) {
  // With an action, phones put it under the text (aligned with it) instead of squeezing the text beside it.
  return (
    <Alert status={status} className={cn('items-start', !!action && 'grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2.5 sm:flex', className)}>
      <Alert.Indicator />
      <Alert.Content className="min-w-0">
        {title && <Alert.Title className="text-body font-semibold">{title}</Alert.Title>}
        {children && <Alert.Description className="text-sm leading-relaxed">{children}</Alert.Description>}
      </Alert.Content>
      {!!action && <div className="col-start-2 flex flex-wrap items-center gap-2 sm:shrink-0">{action}</div>}
    </Alert>
  );
}

export function ErrorNotice({ message, onRetry, title = 'Something went wrong' }: { message: string; onRetry?: () => void; title?: ReactNode }) {
  if (!message) return null;
  return (
    <Notice status="danger" title={title} action={onRetry && <Button size="sm" variant="danger-soft" onPress={onRetry}>Try again</Button>}>{message}</Notice>
  );
}

/** A quiet inline "working on it" line. Say what is happening in plain words ("Updating results…"). */
export function LoadingIndicator({ message = 'Loading…', className }: { message?: string; className?: string }) {
  return (
    <output aria-live="polite" className={cn('flex items-center gap-2.5 rounded-xl border border-accent/15 bg-accent-soft/60 px-3.5 py-2.5', className)}>
      <Spinner size="sm" color="accent" />
      <span className="text-sm font-medium text-accent-soft-foreground">{message}</span>
    </output>
  );
}

/** Placeholder for one card while it loads. */
export function SkeletonCard({ lines = 3, avatar = true, className }: { lines?: number; avatar?: boolean; className?: string }) {
  const widths = ['w-5/6', 'w-2/3', 'w-1/2', 'w-3/4'];
  return (
    <div className={cn('xm-card flex gap-3 p-4', className)}>
      {avatar && <Skeleton className="size-10 shrink-0 rounded-full" />}
      <div className="flex-1 space-y-2.5">
        <Skeleton className="h-4 w-1/3 rounded-md" />
        {Array.from({ length: lines }, (_, index) => <Skeleton key={index} className={cn('h-3.5 rounded-md', widths[index % widths.length])} />)}
      </div>
    </div>
  );
}

/** A list of card placeholders. */
export function SkeletonList({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div aria-label="Loading" aria-busy="true" className={cn('space-y-3', className)}>
      {Array.from({ length: rows }, (_, index) => <SkeletonCard key={index} />)}
    </div>
  );
}

/** A row of KPI placeholders (matches the StatCard grid). */
export function SkeletonStats({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div aria-label="Loading" aria-busy="true" className={cn('grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4', className)}>
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="xm-card space-y-3 p-4 sm:p-5"><Skeleton className="h-3.5 w-1/2 rounded-md" /><Skeleton className="h-8 w-20 rounded-lg" /><Skeleton className="h-3 w-2/3 rounded-md" /></div>
      ))}
    </div>
  );
}

/** A chart panel placeholder. */
export function SkeletonChart({ height = 260, className }: { height?: number; className?: string }) {
  return (
    <div aria-label="Loading" aria-busy="true" className={cn('xm-card space-y-4 p-4 sm:p-6', className)}>
      <div className="space-y-2"><Skeleton className="h-4 w-1/3 rounded-md" /><Skeleton className="h-3 w-1/2 rounded-md" /></div>
      <Skeleton className="w-full rounded-xl" style={{ height }} />
    </div>
  );
}

/** Older loading block: an inline message plus card placeholders. */
export function LoadingRows({ rows = 4, className, message }: { rows?: number; className?: string; message?: string }) {
  return (
    <div aria-label="Loading" aria-busy="true" className={cn('block space-y-3', className)}>
      <LoadingIndicator message={message} />
      {Array.from({ length: rows }, (_, index) => <SkeletonCard key={index} />)}
    </div>
  );
}

/* -------------------------------------------------------------- disclosure */

/** A section that opens and closes (native <details>, so it works without JavaScript and with the keyboard). */
export function Collapsible({ title, description, icon, badge, defaultOpen = false, open, onOpenChange, children, className, bodyClassName }: {
  title: ReactNode; description?: ReactNode; icon?: ReactNode; badge?: ReactNode;
  defaultOpen?: boolean; open?: boolean; onOpenChange?: (open: boolean) => void;
  children: ReactNode; className?: string; bodyClassName?: string;
}) {
  const [inner, setInner] = useState(defaultOpen);
  const isOpen = open ?? inner;
  return (
    <details open={isOpen} className={cn('group/collapsible rounded-2xl border border-border bg-surface', className)}
      onToggle={(event) => { const next = event.currentTarget.open; if (next !== isOpen) { setInner(next); onOpenChange?.(next); } }}>
      <summary className="xm-summary flex min-h-12 cursor-pointer items-center gap-3 rounded-2xl px-4 py-3 select-none hover:bg-surface-secondary/60">
        {icon && <span aria-hidden="true" className="flex text-muted [&_svg]:size-[1.125rem]">{icon}</span>}
        <span className="min-w-0 flex-1">
          <span className="block text-body font-semibold text-foreground">{title}</span>
          {description && <span className="block text-caption text-muted">{description}</span>}
        </span>
        {badge}
        <ChevronDown className="size-4 shrink-0 text-muted transition-transform group-open/collapsible:rotate-180" aria-hidden="true" />
      </summary>
      <div className={cn('border-t border-separator p-4 sm:p-5', bodyClassName)}>{children}</div>
    </details>
  );
}

/** Admin-only technical settings (weights, tolerances, raw data) live in here, closed by default. */
export function AdvancedSection({ children, title = 'Advanced', description = 'Technical settings for admins. Most teams never need to change these.', defaultOpen = false, className }: {
  children: ReactNode; title?: ReactNode; description?: ReactNode; defaultOpen?: boolean; className?: string;
}) {
  return <Collapsible title={title} description={description} icon={<SlidersHorizontal />} defaultOpen={defaultOpen} className={className}>{children}</Collapsible>;
}

/** A small ⓘ button that explains a term in a popover (works on touch). Never place inside another button. */
export function InfoHint({ label, title, children, className }: { label: string; title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Popover>
      <AriaButton aria-label={label} className={cn('inline-flex size-7 shrink-0 items-center justify-center rounded-full text-muted outline-none hover:bg-default hover:text-foreground data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent/40', className)}>
        <Info className="size-4" aria-hidden="true" />
      </AriaButton>
      <Popover.Content placement="top" className="max-w-[min(22rem,calc(100vw-2rem))]">
        <Popover.Dialog aria-label={typeof title === 'string' ? title : label} className="p-3.5">
          {title && <p className="text-sm font-semibold">{title}</p>}
          <div className={cn('text-sm text-muted', title && 'mt-1')}>{children}</div>
        </Popover.Dialog>
      </Popover.Content>
    </Popover>
  );
}

/* ------------------------------------------------------------------ chips */

const entityStyle: Record<EntityStatus, string> = {
  ready: 'bg-success-soft text-success-soft-foreground',
  on_hold: 'bg-warning-soft text-warning-soft-foreground',
  sold: 'bg-accent-soft text-accent-soft-foreground',
  deleted: 'bg-danger-soft text-danger-soft-foreground',
};

/** Stock status of a buyer or listing (Ready / On hold / Sold / Deleted). */
export function StatusChip({ status, className }: { status?: EntityStatus; className?: string }) {
  if (!status) return null;
  return <span className={cn('inline-flex h-7 items-center rounded-full px-2.5 text-[0.8125rem] font-semibold whitespace-nowrap', entityStyle[status], className)}>{STATUS_LABELS[status]}</span>;
}

/** The public ID (L-AB908 / B-AB908). Tap to copy: it is what people say to each other on WhatsApp. */
export function IdChip({ id, kind, className }: { id?: string; kind: 'buyer' | 'property'; className?: string }) {
  const [copied, setCopied] = useState(false);
  if (!id) return null;
  async function copy() {
    try {
      await navigator.clipboard.writeText(id!);
      setCopied(true);
      toast.success(`ID ${id} copied`);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.danger(`Couldn't copy the ID. Copy it manually: ${id}`);
    }
  }
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={`${kind === 'buyer' ? 'Buyer' : 'Listing'} ID ${id}. Tap to copy`}
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-lg px-2 font-mono text-[0.8125rem] font-semibold tracking-wide transition-colors',
        kind === 'buyer' ? 'bg-indigo-50 text-indigo-800 hover:bg-indigo-100' : 'bg-cyan-50 text-cyan-800 hover:bg-cyan-100', className)}
    >
      {id}{copied ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5 opacity-60" aria-hidden="true" />}
    </button>
  );
}

export function PlainChip({ children, color = 'default' }: { children: ReactNode; color?: 'default' | 'accent' | 'success' | 'warning' | 'danger' }) {
  return <Chip color={color} variant="soft" size="md">{children}</Chip>;
}

/* -------------------------------------------------------------- controls */

/** One choice out of a few, shown as big buttons that fit a thumb. */
export function Segmented<T extends string>({ value, onChange, options, label, fullWidth, isDisabled = false }: {
  value: T; onChange: (value: T) => void; options: { id: T; label: ReactNode }[]; label: string; fullWidth?: boolean; isDisabled?: boolean;
}) {
  return (
    <ToggleButtonGroup
      aria-label={label}
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={new Set([value])}
      onSelectionChange={(keys) => { const next = [...keys][0]; if (next) onChange(next as T); }}
      fullWidth={fullWidth}
      isDisabled={isDisabled}
      className="max-w-full"
    >
      {options.map((option, index) => (
        <ToggleButton key={option.id} id={option.id} className="min-h-11 px-4 text-sm font-semibold sm:min-h-10">
          {index > 0 && <ToggleButtonGroup.Separator />}{option.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}

/** A confirmation that names what will happen, with a plain way back. */
export function ConfirmDialog({ open, onOpenChange, title, children, confirmLabel, cancelLabel = 'Cancel', status = 'danger', busy, onConfirm }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; children: ReactNode; confirmLabel: string; cancelLabel?: string;
  status?: 'danger' | 'warning' | 'accent'; busy?: boolean; onConfirm: () => void;
}) {
  return (
    <AlertDialog.Backdrop isOpen={open} onOpenChange={onOpenChange}>
      <AlertDialog.Container>
        <AlertDialog.Dialog className="sm:max-w-[440px]">
          <AlertDialog.Header>
            <AlertDialog.Icon status={status}>{status === 'danger' ? <TriangleAlert /> : undefined}</AlertDialog.Icon>
            <AlertDialog.Heading>{title}</AlertDialog.Heading>
          </AlertDialog.Header>
          <AlertDialog.Body className="text-body">{children}</AlertDialog.Body>
          <AlertDialog.Footer>
            <Button slot="close" variant="tertiary">{cancelLabel}</Button>
            <Button variant={status === 'danger' ? 'danger' : 'primary'} isPending={busy} onPress={onConfirm}>{confirmLabel}</Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog>
      </AlertDialog.Container>
    </AlertDialog.Backdrop>
  );
}
