'use client';

import { useId, useMemo, useState } from 'react';
import { Button, toast } from '@heroui/react';
import { ChevronDown, Download, FileArchive, FileText, ShoppingBasket, Trash2, X } from 'lucide-react';
import { StatusBadge } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { countLabel, formatPhone, number, todayWib } from '@/lib/format';
import { BASKET_MAX, personOf, type BasketItem, type SelectionBasket } from '@/lib/selection-basket';
import type { Direction, GroupBy } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

type Output = 'grouped' | 'single';
const OUTPUT_KEY = 'xm.match.basket-output';
// Same order as the export: Listing → Buyer first, then Buyer → Listing.
const DIRECTIONS: Direction[] = ['property', 'buyer'];
const SHORT: Record<Direction, string> = { property: 'Listing → Buyer', buyer: 'Buyer → Listing' };
const TAG: Record<Direction, string> = { property: 'L→B', buyer: 'B→L' };
const TAG_STYLE: Record<Direction, string> = {
  property: 'bg-[#0b2a6f] text-white',
  buyer: 'bg-[#7dd3fc] text-[#0b2a6f]',
};

function savedOutput(): Output {
  try { return window.localStorage.getItem(OUTPUT_KEY) === 'single' ? 'single' : 'grouped'; } catch { return 'grouped'; }
}

type Person = { key: string; title: string; phone: string; items: BasketItem[]; sources: number };

/**
 * The pairs ticked for the PDF across every buyer and listing, both directions. Collapsed it is one line with the
 * count and the download; expanded it lists the pairs per sender / sales contact, the way the export groups them.
 */
export function SelectionTray({ basket, groupBy }: { basket: SelectionBasket; groupBy: GroupBy }) {
  const api = useApi();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [output, setOutputState] = useState<Output>(savedOutput);
  const [confirmClear, setConfirmClear] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const { items } = basket;

  const setOutput = (value: Output) => {
    setOutputState(value);
    try { window.localStorage.setItem(OUTPUT_KEY, value); } catch { /* storage blocked */ }
  };

  const perDirection = useMemo(() => {
    const counts: Record<Direction, number> = { buyer: 0, property: 0 };
    for (const item of items) counts[item.direction] += 1;
    return counts;
  }, [items]);

  const people = useMemo(() => {
    const map = new Map<string, Person>();
    for (const item of items) {
      const person = personOf(item, groupBy);
      const entry = map.get(person.key) ?? { ...person, items: [], sources: 0 };
      if (!entry.phone && person.phone) entry.phone = person.phone;
      entry.items.push(item);
      map.set(person.key, entry);
    }
    for (const entry of map.values()) {
      entry.sources = new Set(entry.items.map((item) => `${item.direction}:${item.source_id}`)).size;
      entry.items.sort((a, b) => DIRECTIONS.indexOf(a.direction) - DIRECTIONS.indexOf(b.direction) || a.added_at - b.added_at);
    }
    return [...map.values()].sort((a, b) => b.sources - a.sources || a.key.localeCompare(b.key));
  }, [items, groupBy]);

  if (!items.length) return null;
  const unit = groupBy === 'phone' ? ['sales contact', 'sales contacts'] : ['sender', 'senders'];

  async function download() {
    setDownloading(true);
    try {
      const pairs = items.map((item) => ({ direction: item.direction, source_id: item.source_id, target_id: item.target_id }));
      const blob = await api.blob('/export/selection', { pairs, output });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `Selected-Matches-${todayWib()}.${output === 'grouped' ? 'zip' : 'pdf'}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast.success(output === 'grouped' ? `ZIP downloaded: one PDF for each of ${countLabel(people.length, unit[0], unit[1])}` : 'PDF downloaded');
    } catch (reason) {
      toast.danger(errorMessage(reason, 'The PDF could not be created. Please try again.'));
      setOpen(true);
    } finally { setDownloading(false); }
  }

  return (
    <>
      {/* Keeps the last cards reachable above the tray. */}
      <div aria-hidden="true" className="h-24" />
      <section aria-label="Selected matches for the PDF"
        className="pb-safe fixed inset-x-3 bottom-20 z-30 mx-auto max-w-3xl rounded-2xl border border-border bg-surface shadow-raised lg:inset-x-auto lg:right-10 lg:bottom-6 lg:left-[calc(var(--sidebar-width)+2.5rem)]">
        {open && (
          <div id={`${id}-list`} className="max-h-[min(60dvh,32rem)] space-y-4 overflow-y-auto border-b border-separator p-3 sm:p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-muted">Grouped by {unit[0]}, as in the download. Ticks stay when you switch {groupBy === 'phone' ? 'contacts' : 'senders'}, directions or filters.</p>
              {confirmClear ? (
                <span className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-foreground">Clear all {number(items.length)}?</span>
                  <Button size="sm" variant="danger-soft" onPress={() => { basket.clear(); setConfirmClear(false); setOpen(false); }}>Clear</Button>
                  <Button size="sm" variant="tertiary" onPress={() => setConfirmClear(false)}>Keep</Button>
                </span>
              ) : (
                <Button size="sm" variant="tertiary" onPress={() => setConfirmClear(true)}><Trash2 className="size-4" aria-hidden="true" />Clear all</Button>
              )}
            </div>

            {people.map((person) => (
              <section key={person.key || '__none'} aria-label={person.title} className="rounded-xl border border-border">
                <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-separator bg-surface-secondary/60 px-3 py-2">
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold break-words text-foreground">{person.title}</span>
                    {person.phone && groupBy === 'sender' && <span className="block text-caption text-muted">{formatPhone(person.phone)}</span>}
                  </span>
                  <span className="text-caption text-muted tabular-nums">{countLabel(person.items.length, 'pair')}</span>
                </header>
                <ul className="divide-y divide-separator">
                  {person.items.map((item) => (
                    <li key={basket.keyOf(item)} className="flex items-center gap-2 py-1 pr-1 pl-3">
                      <span className={cn('inline-flex h-5 shrink-0 items-center rounded-full px-1.5 text-[0.6875rem] font-bold', TAG_STYLE[item.direction])} title={SHORT[item.direction]}>
                        {TAG[item.direction]}<span className="sr-only"> ({SHORT[item.direction]})</span>
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                        <span className="font-semibold">{item.source_public_id ?? 'No ID'}</span>
                        <span aria-hidden="true" className="px-1 text-muted">→</span><span className="sr-only"> to </span>
                        {item.target_id ? <><span className="font-semibold">{item.target_public_id ?? 'No ID'}</span>{item.target_name ? <span className="text-muted"> · {item.target_name}</span> : null}</> : <span className="text-muted">no match yet</span>}
                      </span>
                      <StatusBadge status={item.status} size="sm" explain={false} className="hidden shrink-0 sm:inline-flex" />
                      <Button isIconOnly size="sm" variant="tertiary" aria-label={`Remove ${item.source_public_id ?? ''} → ${item.target_public_id ?? 'no match'} from the selection`}
                        onPress={() => basket.remove([basket.keyOf(item)])}>
                        <X className="size-4" aria-hidden="true" />
                      </Button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}

            <fieldset className="mx-0 min-w-0 space-y-2 border-0 p-0">
              <legend className="mb-2 text-sm font-semibold text-foreground">File type</legend>
              {([
                ['grouped', FileArchive, `One ZIP, a PDF per ${unit[0]}`, `Each PDF is named after the ${unit[0]} and starts at page 1.`],
                ['single', FileText, 'One PDF', `Every ${unit[0]} in one file, each with its own section covers and page numbers.`],
              ] as const).map(([value, Icon, title, hint]) => (
                <label key={value} className={cn('flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors', output === value ? 'border-accent bg-accent-soft/50' : 'border-border hover:border-border-secondary')}>
                  <input type="radio" name={`${id}-output`} value={value} checked={output === value} onChange={() => setOutput(value)} className="mt-1 size-4 shrink-0 accent-[var(--accent)]" />
                  <Icon className="mt-0.5 size-5 shrink-0 text-muted" aria-hidden="true" />
                  <span className="min-w-0"><span className="block text-sm font-semibold text-foreground">{title}</span><span className="block text-caption text-muted">{hint}</span></span>
                </label>
              ))}
            </fieldset>
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 sm:p-3">
          <button type="button" aria-expanded={open} aria-controls={`${id}-list`} onClick={() => { setOpen((value) => !value); setConfirmClear(false); }}
            className="flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded-xl px-2 text-left outline-none hover:bg-surface-secondary focus-visible:ring-2 focus-visible:ring-accent/40">
            <ShoppingBasket className="size-5 shrink-0 text-accent" aria-hidden="true" />
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-foreground tabular-nums">
                Selected ({number(items.length)}){items.length >= BASKET_MAX ? <span className="font-normal text-warning"> · full</span> : null}
              </span>
              <span className="block truncate text-caption text-muted">
                {DIRECTIONS.filter((direction) => perDirection[direction]).map((direction) => `${SHORT[direction]} ${number(perDirection[direction])}`).join(' · ')}
                {' · '}{countLabel(people.length, unit[0], unit[1])}
              </span>
            </span>
            <ChevronDown className={cn('ml-auto size-4 shrink-0 text-muted transition', open && 'rotate-180')} aria-hidden="true" />
          </button>
          <Button className="max-sm:w-full" isPending={downloading} onPress={download}>
            {output === 'grouped' ? <FileArchive className="size-4" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />}
            Download PDF ({number(items.length)})
          </Button>
        </div>
      </section>
    </>
  );
}
