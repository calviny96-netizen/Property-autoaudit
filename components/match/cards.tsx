'use client';

import { useId, useState, type ReactNode } from 'react';
import { Avatar, Popover, buttonVariants } from '@heroui/react';
import { Button as AriaButton } from 'react-aria-components';
import { Building2, Check, ChevronDown, Globe, MapPin, MessageCircle, UserRoundCheck, UsersRound, type LucideIcon } from 'lucide-react';
import { IdChip, ReasonList, StatusBadge, StatusChip } from '@/components/app/primitives';
import { StatusMenu } from '@/components/app/status-menu';
import {
  OWNERSHIP_TIERS, cleanName, dateTime, displaySender, followUpMessage, formatPhone, initials, isMessagePart, matchStatusOf, messageText, number,
  ownershipGroupLabel, ownershipHint, ownershipLabel, ownershipOrderHint, partLabel, plural, purposeLabel, relativeDate, structuredSummary, titleCase, waLink,
} from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { Direction, EntityStatus, OwnershipTier, Row, Temperature } from '@/lib/types';
import { cn } from '@/lib/utils';

/** Native checkbox with a big, always-clickable box (44 px touch area). */
export function PickBox({ label, checked, onChange, className }: { label: string; checked: boolean; onChange: () => void; className?: string }) {
  return (
    <label className={cn('relative z-10 flex size-11 shrink-0 cursor-pointer items-center justify-center', className)}>
      <input type="checkbox" aria-label={label} checked={checked} onChange={onChange} className="peer absolute inset-0 m-0 size-full cursor-pointer opacity-0" />
      <span aria-hidden="true" className="flex size-6 items-center justify-center rounded-md border-2 border-border bg-surface text-accent-foreground transition peer-checked:border-accent peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-accent/50 [&>svg]:opacity-0 peer-checked:[&>svg]:opacity-100">
        <Check className="size-4" strokeWidth={3} />
      </span>
    </label>
  );
}

const kindOf = (direction: Direction): 'buyer' | 'property' => direction;
const unnamed = (kind: 'buyer' | 'property') => (kind === 'buyer' ? 'Unnamed buyer' : 'Unnamed listing');

/**
 * Full original message, hidden until asked for so lists stay short. For one part of a split message
 * (`part`), the toggle reads "Show full message": the card already shows its part, this reveals all of it.
 */
export function RawChat({ text, part = false }: { text: string; part?: boolean }) {
  const [open, setOpen] = useState(false);
  const noun = part ? 'full message' : 'original message';
  return (
    <details className="group/chat" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1 text-sm font-semibold text-accent sm:min-h-9 [&::-webkit-details-marker]:hidden">
        {open ? `Hide ${noun}` : `Show ${noun}`}
        <ChevronDown className="size-4 transition group-open/chat:rotate-180" aria-hidden="true" />
      </summary>
      {open && <p className="mt-1 mb-2 rounded-xl bg-surface-secondary p-3 text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground/85">{text}</p>}
    </details>
  );
}

/** "Show original message" for any row; "Show full message" when the card is one part of the message. */
export function OriginalMessage({ row }: { row: Row }) {
  return <RawChat text={row.raw_text || row.normalized_text} part={isMessagePart(row)} />;
}

/** The part's own text, quoted, on a card for one part of a message with several requests or offers. */
export function PartText({ row, lines = 3, className }: { row: Row; lines?: 2 | 3 | 4 | 'all'; className?: string }) {
  if (!isMessagePart(row)) return null;
  const clamp = { 2: 'line-clamp-2', 3: 'line-clamp-3', 4: 'line-clamp-4', all: '' }[lines];
  // Padding sits on the outer box so clamped lines never peek out underneath.
  return (
    <div className={cn('rounded-r-xl border-l-2 border-accent/40 bg-surface-secondary/70 px-3 py-2 text-[0.8125rem] leading-relaxed text-foreground/80', className)}>
      <p className={cn('break-words whitespace-pre-wrap', clamp)}>{messageText(row)}</p>
    </div>
  );
}

/** "Buy" / "For rent" … plus a small "Part 1 of 2" badge when the card is one part of a split message. */
export function PurposeTags({ row, kind, compact = false }: { row: Row; kind: 'buyer' | 'property'; compact?: boolean }) {
  const purpose = purposeLabel(row.transaction_type, kind);
  const part = partLabel(row);
  if (!purpose && !part) return null;
  return (
    <span className="inline-flex items-center gap-1">
      {purpose && (
        <span className={cn('inline-flex items-center rounded-full border border-border bg-surface font-semibold whitespace-nowrap text-foreground/85', compact ? 'h-6 px-2 text-xs' : 'h-7 px-2.5 text-[0.8125rem]')}>
          {purpose}
        </span>
      )}
      {part && (
        <span title="This WhatsApp message holds several requests or offers. Each part is matched on its own."
          className="inline-flex h-6 items-center rounded-md bg-accent-soft px-1.5 text-xs font-semibold whitespace-nowrap text-accent-soft-foreground">
          {part}
        </span>
      )}
    </span>
  );
}

/** The same text was posted several times (in one or more groups). */
export function DuplicateNote({ row }: { row: Row }) {
  const count = Number(row.duplicate_count);
  return count > 1 ? <span className="inline-flex h-7 items-center rounded-full bg-default px-2.5 text-caption font-medium text-default-foreground">Shared {count} times</span> : null;
}

/** "Places only" for a buyer who accepts nothing else ("Citraland only"). */
export function StrictNote({ row }: { row: Row }) {
  const places = row.location_strict ? (row.strict_locations ?? []).filter(Boolean) : [];
  if (!places.length) return null;
  return (
    <span title="This buyer only accepts this location" className="inline-flex h-7 max-w-full items-center gap-1 rounded-full bg-default px-2.5 text-[0.8125rem] font-semibold text-default-foreground">
      <MapPin className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{titleCase(places.join(' / '))} only</span>
    </span>
  );
}

export function WhatsAppButton({ row, kind, label, className }: { row: Row; kind: 'buyer' | 'property'; label: string; className?: string }) {
  const href = waLink(row.contact_phone, followUpMessage(row, kind));
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={cn(buttonVariants({ variant: 'secondary', size: 'md' }), 'text-success-soft-foreground', className)}>
      <MessageCircle className="size-4" aria-hidden="true" />{label}
      <span className="sr-only"> ({formatPhone(row.contact_phone)}, opens WhatsApp in a new tab)</span>
    </a>
  );
}

function LastSeen({ row }: { row: Row }) {
  const value = row.last_seen_at || row.sent_at;
  if (!value) return <span>Post date not available</span>;
  return <time dateTime={value} title={dateTime(value)}>Posted {relativeDate(value).toLowerCase()}</time>;
}

/** The status counts of a source: Hot · 2, Warm · 1, Custom · 1, or No match. */
function SourceCounts({ row }: { row: Row }) {
  const counts: [Temperature, number][] = [['hot', Number(row.hot_count) || 0], ['warm', Number(row.warm_count) || 0], ['custom', Number(row.custom_count) || 0]];
  return (
    <>
      {counts.filter(([, count]) => count > 0).map(([status, count]) => <StatusBadge key={status} status={status} count={count} size="sm" explain={false} />)}
      {Number(row.match_count) === 0 && <StatusBadge status="unmatched" size="sm" explain={false} />}
    </>
  );
}

type SourceCardProps = {
  row: Row;
  direction: Direction;
  selected: boolean;
  onSelect: () => void;
  onStatus: (status: EntityStatus) => void;
  bulk?: { checked: boolean; onToggle: () => void };
  /** In grouped views the sender is already in the header. */
  showSender?: boolean;
  extra?: ReactNode;
};

/** One buyer or listing in the list. Tapping it opens its matches. */
export function SourceCard({ row, direction, selected, onSelect, onStatus, bulk, showSender, extra }: SourceCardProps) {
  const kind = kindOf(direction);
  const hot = Number(row.hot_count) > 0;
  const onlyCustom = !hot && !Number(row.warm_count) && Number(row.custom_count) > 0;
  const name = cleanName(row.contact_name) || unnamed(kind);
  const status = row.entity_status ?? 'ready';
  const sender = showSender ? displaySender(row.author) : '';
  const summary = structuredSummary(row);
  const part = isMessagePart(row);
  return (
    // Keyboard users select through the card's title button; the card-wide click is a mouse/touch shortcut.
    // oxlint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions
    <article
      style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 180px' }}
      // The whole card selects the source; clicks on its own controls (menu, copy, links, toggles) keep their action.
      onClick={(event) => {
        const target = event.target as HTMLElement;
        if (target.closest('button, a, input, label, [role="menu"], [role="menuitem"], [role="dialog"]')) return;
        if (window.getSelection()?.toString()) return;
        onSelect();
      }}
      className={cn(
        'relative cursor-pointer rounded-2xl border bg-surface p-4 shadow-card transition',
        hot && 'hot-match', onlyCustom && 'custom-match',
        selected ? 'border-accent bg-accent-soft/40 ring-2 ring-accent/20' : 'hover:border-accent/50 hover:shadow-raised',
        !selected && !hot && !onlyCustom && 'border-border',
      )}
    >
      <div className="flex items-start gap-3">
        {bulk && <PickBox label={`Select ${name}${row.public_id ? ` (${row.public_id})` : ''}`} checked={bulk.checked} onChange={bulk.onToggle} className="-mt-1.5 -ml-1.5" />}
        <Avatar color={hot ? 'danger' : 'accent'} variant="soft" size="md" className="hidden shrink-0 sm:flex"><Avatar.Fallback>{initials(name)}</Avatar.Fallback></Avatar>
        <div className="min-w-0 flex-1">
          <button type="button" onClick={onSelect} aria-pressed={selected} aria-label={`Show matches for ${name}${row.public_id ? `, ID ${row.public_id}` : ''}`}
            className="block w-full rounded-lg text-left outline-none after:absolute after:inset-0 after:rounded-2xl focus-visible:after:ring-2 focus-visible:after:ring-accent/40">
            <span className="block min-w-0 pr-10 text-body leading-snug font-semibold break-words text-foreground">{name}</span>
            <span className="mt-0.5 block text-caption text-muted"><LastSeen row={row} />{sender ? <> · {sender}</> : null}</span>
          </button>
          <div className="absolute top-2 right-2 z-10"><StatusMenu status={status} kind={kind} name={`${kind === 'buyer' ? 'Buyer' : 'Listing'} ${row.public_id ?? name}`} onChoose={onStatus} /></div>
          <div className="relative z-10 mt-2.5 flex flex-wrap items-center gap-1.5">
            <IdChip id={row.public_id} kind={kind} />
            <PurposeTags row={row} kind={kind} />
            {status !== 'ready' && <StatusChip status={status} />}
            <SourceCounts row={row} />
            <StrictNote row={row} />
          </div>
          {(summary || !part) && <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-foreground/85">{summary || messageText(row)}</p>}
          <PartText row={row} className={summary ? 'mt-2' : 'mt-2.5'} />
          <div className="relative z-10 mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1"><DuplicateNote row={row} /><OriginalMessage row={row} /></div>
          {extra}
        </div>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------ ownership tiers */

const TIER_STYLE: Record<OwnershipTier, string> = {
  own: 'border-accent/25 bg-accent-soft text-accent-soft-foreground',
  team: 'border-teal-200 bg-teal-50 text-teal-800',
  company: 'border-slate-200 bg-slate-100 text-slate-700',
  other: 'border-border bg-surface text-muted',
};
const TIER_ICON: Record<OwnershipTier, LucideIcon> = { own: UserRoundCheck, team: UsersRound, company: Building2, other: Globe };
const isTier = (value: unknown): value is OwnershipTier => (OWNERSHIP_TIERS as readonly unknown[]).includes(value);
const pillClass = (tier: OwnershipTier) => cn('inline-flex h-6 max-w-full min-w-0 shrink items-center gap-1 rounded-full border px-2 text-xs font-semibold whitespace-nowrap', TIER_STYLE[tier]);

/** Icon, label and an optional count ("Own stock · 3"); shared by the card badge and the group heading. */
function TierContent({ tier, label, count, more = false }: { tier: OwnershipTier; label: string; count?: number; more?: boolean }) {
  const Icon = TIER_ICON[tier];
  return (
    <>
      <Icon className="size-3 shrink-0" aria-hidden="true" />
      <span className="min-w-0 truncate">{label}</span>
      {count !== undefined && (
        <>
          <span aria-hidden="true" className="opacity-50">·</span>
          <span className="tabular-nums">{number(count)}{more && <span aria-hidden="true">+</span>}</span>
          <span className="sr-only">{more ? ' or more' : ''} {plural(more ? 2 : count, 'match', 'matches')}</span>
        </>
      )}
    </>
  );
}

/**
 * Whose buyer or listing a recommendation is: "Own stock" / "Own buyer", "Team", the company name, or "Other agent".
 * `kind` is the recommended side. Tap (phones) or click to see what it means and why the list is in this order.
 */
export function OwnershipBadge({ tier, kind, className }: { tier?: OwnershipTier | null; kind: 'buyer' | 'property'; className?: string }) {
  const { company } = useCompany();
  if (!isTier(tier)) return null;
  const label = ownershipLabel(tier, kind, company?.company_name);
  const hint = ownershipHint(tier, kind, company?.search_terms);
  const order = ownershipOrderHint(kind);
  return (
    <Popover>
      <AriaButton aria-label={`${label}. ${hint} ${order}`}
        className={cn(pillClass(tier), 'max-w-40 cursor-pointer outline-none hover:brightness-95 data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent/40', className)}>
        <TierContent tier={tier} label={label} />
      </AriaButton>
      <Popover.Content placement="top" className="max-w-[min(20rem,calc(100vw-2rem))]">
        <Popover.Dialog className="p-3.5">
          <Popover.Heading className="text-sm font-semibold">{label}</Popover.Heading>
          <p className="mt-1 text-sm text-muted">{hint}</p>
          <p className="mt-2 border-t border-separator pt-2 text-caption text-muted">{order}</p>
        </Popover.Dialog>
      </Popover.Content>
    </Popover>
  );
}

/**
 * A recommendation list under one heading per ownership tier ("Own stock · 3", "Team · 5", "Konig · 2", "Other agents · 9"),
 * own → team → company → other. The API already sorts by tier first, so this only adds the headings; rows in a tier keep
 * the API order. `hasMore`: more rows can still be loaded, so the last group's count gets a "+". Without tiers (an older
 * API) or for a single card it renders the plain list: the badge on the card says enough.
 */
export function TierGroups<T>({ items, tierOf, kind, hasMore = false, children }: {
  items: readonly T[]; tierOf: (item: T) => OwnershipTier | null | undefined; kind: 'buyer' | 'property'; hasMore?: boolean;
  children: (item: T) => ReactNode;
}) {
  const { company } = useCompany();
  const id = useId();
  const tiers = items.map((item) => { const tier = tierOf(item); return isTier(tier) ? tier : null; });
  if (items.length < 2 || !tiers.some(Boolean)) return <>{items.map((item) => children(item))}</>;
  const groups = OWNERSHIP_TIERS
    .map((tier) => ({ tier, items: items.filter((_, index) => (tiers[index] ?? 'other') === tier) }))
    .filter((group) => group.items.length > 0);
  const order = ownershipOrderHint(kind);
  return (
    <>
      {groups.map((group, index) => (
        <section key={group.tier} aria-labelledby={`${id}-${group.tier}`} className={cn('space-y-3', index > 0 && 'pt-3')}>
          <h4 id={`${id}-${group.tier}`} title={order} className="flex min-w-0 items-center gap-2 px-1">
            <span className={pillClass(group.tier)}>
              <TierContent tier={group.tier} label={ownershipGroupLabel(group.tier, kind, company?.company_name)} count={group.items.length}
                more={hasMore && index === groups.length - 1} />
            </span>
            <span aria-hidden="true" className="h-px min-w-4 flex-1 bg-separator" />
          </h4>
          {group.items.map((item) => children(item))}
        </section>
      ))}
    </>
  );
}

const PAIR_STYLE: Record<Temperature, string> = {
  hot: 'hot-match bg-hot-soft/30',
  warm: 'border-border bg-surface',
  custom: 'custom-match bg-custom-soft/30',
};

/** A suggested counterpart for the selected buyer/listing: status, the human reasons, and quick actions. */
export function RecommendationCard({ target, source, direction, checked, onCheck, onStatus, extra }: {
  target: Row; source: Row; direction: Direction; checked: boolean; onCheck: () => void; onStatus: (status: EntityStatus) => void; extra?: ReactNode;
}) {
  const targetKind = direction === 'buyer' ? 'property' : 'buyer';
  const pair = matchStatusOf(target);
  const status = target.entity_status ?? 'ready';
  const title = cleanName(target.contact_name) || unnamed(targetKind);
  const summary = structuredSummary(target);
  const part = isMessagePart(target);
  const name = cleanName(target.contact_name) || target.public_id || summary;
  return (
    <article style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 280px' }} className={cn('rounded-2xl border p-4 shadow-card', PAIR_STYLE[pair])}>
      <div className="flex items-start gap-2">
        <PickBox label={`Include ${name}${target.public_id && target.public_id !== name ? ` (${target.public_id})` : ''} in the PDF`} checked={checked} onChange={onCheck} className="-mt-2 -ml-2" />
        <div className="min-w-0 flex-1">
          <p className="text-body leading-snug font-semibold break-words text-foreground">{title}</p>
          <p className="mt-0.5 text-caption text-muted"><LastSeen row={target} /></p>
        </div>
        {/* Phones: status on top, owner below it, so the title keeps its width. Wider: side by side. */}
        <div className="flex shrink-0 flex-col-reverse items-end gap-1 sm:flex-row sm:items-center sm:gap-1.5">
          <OwnershipBadge tier={target.ownership_tier} kind={targetKind} />
          <StatusBadge status={pair} />
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <IdChip id={target.public_id} kind={targetKind} />
        <PurposeTags row={target} kind={targetKind} />
        {status !== 'ready' && <StatusChip status={status} />}
        <StrictNote row={target} />
      </div>
      {(summary || !part) && <p className="mt-2.5 text-sm leading-relaxed text-foreground/85">{summary || messageText(target).slice(0, 280)}</p>}
      <PartText row={target} lines={4} className={summary ? 'mt-2' : 'mt-2.5'} />
      {/* Purpose and unit always lead; six lines leave room for location, legal status, unit type or tower. */}
      <ReasonList reasons={target.explanation} limit={6} className="mt-3" />
      {extra}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1"><DuplicateNote row={target} /><OriginalMessage row={target} /></div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-separator pt-3">
        <div className="flex flex-wrap gap-2">
          <WhatsAppButton row={target} kind={targetKind} label={`WhatsApp ${targetKind === 'buyer' ? 'buyer' : 'listing agent'}`} />
          <WhatsAppButton row={source} kind={kindOf(direction)} label={`WhatsApp ${direction === 'buyer' ? 'buyer' : 'listing agent'}`} />
        </div>
        <StatusMenu status={status} kind={targetKind} name={`${targetKind === 'buyer' ? 'Buyer' : 'Listing'} ${target.public_id ?? name}`} onChoose={onStatus} />
      </div>
    </article>
  );
}
