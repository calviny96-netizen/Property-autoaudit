'use client';

/*
 * The company's sales watchlist: phone numbers whose listing stock is logged, and the sources
 * in Sales mode. Rows are added, edited and removed one at a time (POST/PATCH/DELETE
 * /stock/tracked/item); "Import from CSV / Excel" adds or replaces numbers from a spreadsheet, and
 * "Paste many numbers" replaces the whole list at once (both save with PUT /stock/tracked).
 */

import { useMemo, useState } from 'react';
import { Button, Description, Input, Label, Modal, TextArea, TextField, toast, type useOverlayState } from '@heroui/react';
import { ClipboardPaste, FileSpreadsheet, Pencil, Plus, Trash2, UserRound } from 'lucide-react';
import { Collapsible, ConfirmDialog, EmptyState, Notice } from '@/components/app/primitives';
import { useSaveNotice } from '@/components/settings/reprocess-banner';
import { WatchlistImport } from '@/components/settings/watchlist-import';
import { errorMessage, query } from '@/lib/api';
import { cleanName, countLabel, formatPhone, normalizePhone, number } from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { StockOverview, StockTracked } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useApi } from '@/lib/workspace-context';

const PHONE = /^628\d{8,12}$/;
const DEFAULT_MAX = 150;
const salesName = (item: Pick<StockTracked, 'label' | 'contact_name'>) => item.label || cleanName(item.contact_name) || 'Sales';
const phoneProblem = (raw: string) => {
  if (!raw.trim()) return 'Enter a phone number.';
  return PHONE.test(normalizePhone(raw)) ? '' : 'Use an Indonesian mobile number, starting with 08… or 628….';
};

/** After a watchlist change: in Sales mode the matches are out of date until the next reprocess. */
function useWatchlistNotice() {
  const notice = useSaveNotice();
  const { company, refresh } = useCompany();
  return (title: string, phonesChanged: boolean) => {
    refresh();
    notice(title, { pending: phonesChanged && company?.matching_mode === 'sales' });
  };
}

/**
 * The watchlist as a list with per-row editing, an "Add sales" form, "Import from CSV / Excel" and "Paste many numbers".
 * `onChange` receives the fresh overview after every saved change.
 */
export function TrackedManager({ items, maxTracked = DEFAULT_MAX, canEdit, onChange }: {
  items: readonly StockTracked[]; maxTracked?: number; canEdit: boolean; onChange: (overview: StockOverview) => void;
}) {
  const api = useApi();
  const announce = useWatchlistNotice();
  const [editing, setEditing] = useState<string | null>(null);
  const [removing, setRemoving] = useState<StockTracked | null>(null);
  const [busy, setBusy] = useState('');
  const [importing, setImporting] = useState(false);
  const full = items.length >= maxTracked;

  async function remove() {
    if (!removing) return;
    setBusy('remove');
    try {
      onChange(await api.del<StockOverview>(`/stock/tracked/item?${query({ phone: removing.phone })}`));
      announce(`${salesName(removing)} removed from the watchlist`, true);
      setRemoving(null);
    } catch (reason) { toast.danger(errorMessage(reason, 'The number could not be removed. Please try again.')); } finally { setBusy(''); }
  }

  return (
    <div className="space-y-4">
      {items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border">
          <EmptyState compact icon={<UserRound />} title="No sales numbers yet"
            description={canEdit ? 'Add the phone numbers of the sales people your company follows.' : 'Your company admin can add sales numbers.'} />
        </div>
      ) : (
        <ul className="divide-y divide-separator overflow-hidden rounded-2xl border border-border" aria-label="Sales watchlist">
          {items.map((item) => editing === item.phone ? (
            <li key={item.phone} className="bg-surface-secondary/50 p-3 sm:p-4">
              <TrackedForm initial={{ label: item.label ?? '', phone: item.phone }} submitLabel="Save" busy={busy === item.phone}
                onCancel={() => setEditing(null)}
                onSubmit={async ({ label, phone }) => {
                  setBusy(item.phone);
                  try {
                    const next = normalizePhone(phone);
                    onChange(await api.patch<StockOverview>('/stock/tracked/item', { original_phone: item.phone, phone: next, label: label || null }));
                    announce(`${label || salesName(item)} saved`, next !== item.phone);
                    setEditing(null);
                    return true;
                  } catch (reason) { toast.danger(errorMessage(reason, 'The change could not be saved. Please try again.')); return false; } finally { setBusy(''); }
                }} />
            </li>
          ) : (
            <li key={item.phone} className="flex min-h-16 items-center gap-3 px-4 py-3 sm:px-5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-body font-semibold text-foreground">{salesName(item)}</p>
                <p className="flex flex-wrap gap-x-2 text-sm text-muted tabular-nums"><span className="whitespace-nowrap">{formatPhone(item.phone)}</span><span className="whitespace-nowrap">{number(item.counts.ready)} ready</span></p>
              </div>
              {canEdit && (
                <div className="-mr-2 flex shrink-0">
                  <Button isIconOnly variant="ghost" isDisabled={!!busy} aria-label={`Edit ${salesName(item)}`} onPress={() => setEditing(item.phone)}><Pencil className="size-4" /></Button>
                  <Button isIconOnly variant="ghost" isDisabled={!!busy} aria-label={`Remove ${salesName(item)}`} onPress={() => setRemoving(item)}><Trash2 className="size-4" /></Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {canEdit && (
        <>
          <div className="rounded-2xl border border-border p-3 sm:p-4">
            <p className="mb-3 text-body font-semibold text-foreground">Add sales</p>
            {full ? <p className="text-sm text-muted">The watchlist is full ({number(maxTracked)} numbers). Remove a number to add another.</p> : (
              <TrackedForm key={items.length} initial={{ label: '', phone: '' }} submitLabel="Add sales" busy={busy === 'add'}
                onSubmit={async ({ label, phone }) => {
                  setBusy('add');
                  try {
                    onChange(await api.post<StockOverview>('/stock/tracked/item', { phone: normalizePhone(phone), label: label || null }));
                    announce(`${label || formatPhone(phone)} added to the watchlist`, true);
                    return true;
                  } catch (reason) { toast.danger(errorMessage(reason, 'The number could not be added. Please try again.')); return false; } finally { setBusy(''); }
                }} />
            )}
          </div>
          <Collapsible title="Import from CSV / Excel" description="Add or replace numbers and names from a spreadsheet file." icon={<FileSpreadsheet />}
            open={importing} onOpenChange={setImporting}>
            <WatchlistImport current={items} maxTracked={maxTracked}
              onImported={(overview, result) => { onChange(overview); announce(result.title, result.phonesChanged); setImporting(false); }} />
          </Collapsible>
          <Collapsible title="Paste many numbers" description="Replace the whole watchlist with a list of numbers." icon={<ClipboardPaste />}>
            <BulkForm key={items.map((item) => item.phone).join(',')} current={items} maxTracked={maxTracked} onSaved={onChange} />
          </Collapsible>
        </>
      )}

      <ConfirmDialog open={!!removing} onOpenChange={(open) => { if (!open) setRemoving(null); }} title={`Remove ${removing ? salesName(removing) : 'this number'}?`}
        confirmLabel="Remove from watchlist" busy={busy === 'remove'} onConfirm={() => void remove()}>
        {removing ? formatPhone(removing.phone) : 'This number'} is no longer followed. Its earlier stock history stays in the log.
      </ConfirmDialog>
    </div>
  );
}

function TrackedForm({ initial, submitLabel, busy, onSubmit, onCancel }: {
  initial: { label: string; phone: string }; submitLabel: string; busy: boolean;
  onSubmit: (value: { label: string; phone: string }) => Promise<boolean>; onCancel?: () => void;
}) {
  const [label, setLabel] = useState(initial.label);
  const [phone, setPhone] = useState(initial.phone ? formatPhone(initial.phone) : '');
  const [tried, setTried] = useState(false);
  const problem = phoneProblem(phone);

  async function submit() {
    setTried(true);
    if (problem) return;
    if (await onSubmit({ label: label.trim(), phone })) { if (!onCancel) { setLabel(''); setPhone(''); setTried(false); } }
  }

  return (
    <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField value={label} onChange={setLabel} fullWidth isDisabled={busy}>
          <Label className="text-sm font-semibold">Name <span className="font-normal text-muted">(optional)</span></Label>
          <Input maxLength={80} placeholder="e.g. Albert" autoComplete="off" />
        </TextField>
        <TextField value={phone} onChange={setPhone} fullWidth isRequired isDisabled={busy} isInvalid={tried && !!problem}>
          <Label className="text-sm font-semibold">Phone number</Label>
          <Input inputMode="tel" placeholder="e.g. 0812 3456 7890" autoComplete="off" />
        </TextField>
      </div>
      {tried && problem && <p className="text-sm font-medium text-danger" role="alert">{problem}</p>}
      <div className={cn('flex flex-wrap gap-2', onCancel && 'justify-end')}>
        {onCancel && <Button variant="tertiary" isDisabled={busy} onPress={onCancel}>Cancel</Button>}
        <Button type="submit" variant={onCancel ? 'primary' : 'secondary'} isPending={busy}>{!onCancel && <Plus className="size-4" aria-hidden="true" />}{submitLabel}</Button>
      </div>
    </form>
  );
}

/** One box for many numbers, separated by commas; the list below shows what was understood. */
function BulkForm({ current, maxTracked, onSaved }: { current: readonly StockTracked[]; maxTracked: number; onSaved: (overview: StockOverview) => void }) {
  const api = useApi();
  const announce = useWatchlistNotice();
  const [text, setText] = useState(() => current.map((item) => item.phone).join(', '));
  const [labels, setLabels] = useState<Record<string, string>>(() => Object.fromEntries(current.filter((item) => item.label).map((item) => [item.phone, item.label as string])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const parsed = useMemo(() => {
    const parts = text.split(/[,;\n]+/).map((part) => part.trim()).filter(Boolean);
    const rows = parts.map((raw) => ({ raw, phone: normalizePhone(raw) }));
    const valid = [...new Map(rows.filter((row) => PHONE.test(row.phone)).map((row) => [row.phone, row])).values()];
    const invalid = rows.filter((row) => !PHONE.test(row.phone));
    return { valid, invalid };
  }, [text]);
  const tooMany = parsed.valid.length > maxTracked;
  const removed = current.filter((item) => !parsed.valid.some((row) => row.phone === item.phone)).length;

  async function save() {
    setBusy(true); setError('');
    try {
      const overview = await api.put<StockOverview>('/stock/tracked', { phones: parsed.valid.map((row) => row.phone), labels });
      const phonesChanged = parsed.valid.length !== current.length || parsed.valid.some((row) => !current.some((item) => item.phone === row.phone));
      onSaved(overview);
      announce(`Watchlist saved: ${countLabel(parsed.valid.length, 'number')}`, phonesChanged);
    } catch (reason) { setError(errorMessage(reason, 'The watchlist could not be saved. Please try again.')); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <TextField value={text} onChange={setText} fullWidth isDisabled={busy}>
        <Label className="text-sm font-semibold">Phone numbers</Label>
        <TextArea rows={4} placeholder="6282233744657, 6281202310022" />
        <Description className="text-caption">Separate numbers with commas or new lines. Start with 08… or 628…. Up to {number(maxTracked)} numbers; repeats count once.</Description>
      </TextField>
      {parsed.invalid.length > 0 && <Notice status="danger" title="These numbers are not recognised">{parsed.invalid.map((row) => row.raw).join(', ')}. Use Indonesian mobile numbers.</Notice>}
      {tooMany && <Notice status="danger">Use at most {number(maxTracked)} numbers.</Notice>}
      {parsed.valid.length > 0 && (
        <div>
          <p className="mb-2 text-sm font-semibold text-foreground">{countLabel(parsed.valid.length, 'number')} will be followed{removed ? ` · ${number(removed)} removed` : ''}</p>
          <ul className="space-y-2">
            {parsed.valid.map((row) => (
              <li key={row.phone} className="flex flex-col gap-2 rounded-xl bg-surface-secondary/60 p-2.5 sm:flex-row sm:items-center">
                <span className="text-sm font-semibold tabular-nums sm:w-44">{formatPhone(row.phone)}</span>
                <input aria-label={`Name for ${formatPhone(row.phone)}`} value={labels[row.phone] ?? ''} onChange={(event) => setLabels((currentLabels) => ({ ...currentLabels, [row.phone]: event.target.value }))}
                  maxLength={80} placeholder="Name (optional)" disabled={busy}
                  className="h-11 min-w-0 flex-1 rounded-xl border border-field-border bg-field px-3 text-base outline-none focus-visible:border-accent sm:h-10 sm:text-sm" />
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <Notice status="danger">{error}</Notice>}
      <Button isPending={busy} isDisabled={parsed.invalid.length > 0 || tooMany} onPress={() => void save()}>Replace watchlist</Button>
    </div>
  );
}

/** The watchlist in a dialog (used on the Sales stock page). Keeps the props of the earlier bulk editor. */
export function TrackedEditor({ state, current, onSaved }: { state: ReturnType<typeof useOverlayState>; current: StockTracked[]; onSaved: () => void }) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="lg" scroll="inside">
        {/* Mounted each time the dialog opens, so it always starts from what is saved. */}
        <Modal.Dialog>{state.isOpen && <TrackedDialogBody current={current} onSaved={onSaved} />}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function TrackedDialogBody({ current, onSaved }: { current: StockTracked[]; onSaved: () => void }) {
  const [items, setItems] = useState<readonly StockTracked[]>(current);
  const [max, setMax] = useState(DEFAULT_MAX);
  return (
    <>
      <Modal.CloseTrigger />
      <Modal.Header>
        <Modal.Heading>Sales watchlist</Modal.Heading>
      </Modal.Header>
      <Modal.Body className="space-y-4">
        <p className="text-sm leading-relaxed text-muted">For the whole company. Listings that show one of these numbers are counted on every upload. In Sales mode, only their buyers and listings are matched.</p>
        <TrackedManager items={items} maxTracked={max} canEdit onChange={(overview) => { setItems(overview.tracked); setMax(overview.max_tracked || DEFAULT_MAX); onSaved(); }} />
      </Modal.Body>
      <Modal.Footer>
        <Button slot="close" variant="secondary">Done</Button>
      </Modal.Footer>
    </>
  );
}
