'use client';

import { useCallback, useState, type ReactNode } from 'react';
import { Description, Dropdown, Label, buttonVariants, toast } from '@heroui/react';
import { Ban, CircleCheck, Clock3, EllipsisVertical, PackageCheck, Trash2 } from 'lucide-react';
import { ConfirmDialog } from '@/components/app/primitives';
import { errorMessage } from '@/lib/api';
import { STATUS_LABELS } from '@/lib/format';
import { useCompany } from '@/lib/session';
import type { EntityStatus } from '@/lib/types';
import { useApi } from '@/lib/workspace-context';

const OPTIONS: { id: EntityStatus; label: string; hint: string; icon: typeof CircleCheck }[] = [
  { id: 'ready', label: STATUS_LABELS.ready, hint: 'Still available / still looking', icon: CircleCheck },
  { id: 'on_hold', label: STATUS_LABELS.on_hold, hint: 'Paused for now', icon: Clock3 },
  { id: 'sold', label: STATUS_LABELS.sold, hint: 'Sold, rented or deal closed', icon: PackageCheck },
  { id: 'deleted', label: 'Delete', hint: 'Hide from every list', icon: Trash2 },
];

/** For buyers the soft "deleted" status is shown as "Disabled": out of matching, counts, recent matches and exports. */
export const DISABLED_LABEL = 'Disabled';
const DISABLE_OPTION = { id: 'deleted' as const, label: DISABLED_LABEL, hint: 'Remove from matching, counts and exports', icon: Ban };

/** How a status reads for a buyer (deleted = Disabled) or a listing. */
export const statusLabel = (status: EntityStatus, kind?: 'buyer' | 'property') => (kind === 'buyer' && status === 'deleted' ? DISABLED_LABEL : STATUS_LABELS[status]);

/** The "⋮" on a card: mark it Ready, On hold, Sold or deleted (a buyer is disabled instead). */
export function StatusMenu({ status = 'ready', name, onChoose, kind }: { status?: EntityStatus; name: string; onChoose: (status: EntityStatus) => void; kind?: 'buyer' | 'property' }) {
  const options = kind === 'buyer' ? OPTIONS.map((option) => (option.id === 'deleted' ? DISABLE_OPTION : option)) : OPTIONS;
  return (
    <Dropdown>
      <Dropdown.Trigger aria-label={`Change status of ${name}`} className={buttonVariants({ variant: 'tertiary', isIconOnly: true, size: 'md' })}>
        <EllipsisVertical className="size-5" aria-hidden="true" />
      </Dropdown.Trigger>
      <Dropdown.Popover placement="bottom end" className="min-w-64">
        <Dropdown.Menu aria-label={`Status of ${name}`} onAction={(key) => onChoose(key as EntityStatus)}>
          <Dropdown.Section>
            {options.map((option) => (
              <Dropdown.Item key={option.id} id={option.id} textValue={option.label} isDisabled={option.id === status} variant={option.id === 'deleted' ? 'danger' : 'default'}>
                <option.icon className="size-5 shrink-0" aria-hidden="true" />
                <div className="flex flex-col">
                  <Label className="text-body">{option.id === status ? `${option.label} (current)` : option.id === 'deleted' ? (kind === 'buyer' ? 'Disable buyer' : 'Mark for deletion') : `Mark as ${option.label}`}</Label>
                  <Description className="text-sm">{option.hint}</Description>
                </div>
              </Dropdown.Item>
            ))}
          </Dropdown.Section>
        </Dropdown.Menu>
      </Dropdown.Popover>
    </Dropdown>
  );
}

export type StatusRequest = {
  /** Entity IDs or public IDs. */
  refs: string[];
  status: EntityStatus;
  /** What to call it in messages: "Listing L-AB908" or "3 listings". */
  name: string;
  /** If every item had the same status before, "Undo" can restore it. */
  previous?: EntityStatus;
  /** Buyers are "disabled" rather than deleted. */
  kind?: 'buyer' | 'property';
};

/**
 * Applies a status change, asks first when it hides something, and always offers
 * a way back. Render `dialog` somewhere in the page.
 */
export function useStatusActions(onChanged: () => void): { request: (request: StatusRequest) => void; dialog: ReactNode } {
  const api = useApi();
  const { refresh } = useCompany();
  const [pending, setPending] = useState<StatusRequest | null>(null);
  const [busy, setBusy] = useState(false);

  const apply = useCallback(async (request: StatusRequest) => {
    setBusy(true);
    // The API takes 200 at a time; "Select all" can hand over far more.
    const send = async (status: EntityStatus) => {
      for (let start = 0; start < request.refs.length; start += 200) await api.post('/entities/status', { ids: request.refs.slice(start, start + 200), status });
    };
    try {
      await send(request.status);
      const undo = request.previous && request.previous !== request.status
        ? { children: 'Undo', variant: 'tertiary' as const, onPress: () => { const previous = request.previous; if (previous) void send(previous).then(() => { onChanged(); refresh(); }).catch((reason) => toast.danger(errorMessage(reason))); } }
        : undefined;
      toast.success(`${request.name} marked as ${statusLabel(request.status, request.kind)}`, { timeout: 8000, ...(undo ? { actionProps: undo } : {}) });
      onChanged();
      refresh();
    } catch (reason) {
      toast.danger(errorMessage(reason, 'The status could not be changed. Please try again.'));
    } finally {
      setBusy(false);
      setPending(null);
    }
  }, [api, onChanged, refresh]);

  const request = useCallback((next: StatusRequest) => {
    if (next.status === 'deleted') setPending(next);
    else void apply(next);
  }, [apply]);

  const dialog = (
    <ConfirmDialog
      open={!!pending}
      onOpenChange={(open) => { if (!open && !busy) setPending(null); }}
      title={pending?.kind === 'buyer' ? 'Disable this buyer?' : 'Mark for deletion?'}
      confirmLabel={pending?.kind === 'buyer' ? 'Yes, disable' : 'Yes, remove from lists'}
      busy={busy}
      onConfirm={() => pending && void apply(pending)}
    >
      <p><strong>{pending?.name}</strong> will be hidden from every list and from matching{pending?.kind === 'buyer' ? ', counts, recent matches and exports' : ''}.</p>
      <p className="mt-2 text-muted">Nothing is deleted permanently. You can restore it any time with the <strong>{pending?.kind === 'buyer' ? 'Disabled filter on the Buyers page' : 'Deleted filter'}</strong>.</p>
    </ConfirmDialog>
  );
  return { request, dialog };
}
