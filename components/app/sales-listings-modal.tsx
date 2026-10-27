'use client';

import { useState } from 'react';
import { Accordion, Button, Modal, toast, type useOverlayState } from '@heroui/react';
import { Building2 } from 'lucide-react';
import { EmptyState, ErrorNotice, SkeletonList, StatusChip } from '@/components/app/primitives';
import { OriginalMessage, PartText, PurposeTags } from '@/components/match/cards';
import { errorMessage, query } from '@/lib/api';
import { formatPhone, isMessagePart, messageText, relativeDateTime, structuredSummary } from '@/lib/format';
import type { Row } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';

type Page = { rows: Row[]; has_more: boolean };

/** First non-empty line of the message: what an agent would call the headline of the listing. */
const headline = (text: string) => (text.split('\n').map((line) => line.replace(/[*_~]+/g, '').trim()).find(Boolean) ?? 'Untitled listing').slice(0, 140);

/** Listings of one tracked sales number, shown in place so the stock page is never left. */
export function SalesListingsModal({ state, phone, name }: { state: ReturnType<typeof useOverlayState>; phone: string; name: string }) {
  return (
    <Modal.Backdrop isOpen={state.isOpen} onOpenChange={(open) => state.setOpen(open)}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog>{state.isOpen && phone ? <Body phone={phone} name={name} onClose={() => state.close()} /> : null}</Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}

function Body({ phone, name, onClose }: { phone: string; name: string; onClose: () => void }) {
  const api = useApi();
  const [extra, setExtra] = useState<Row[]>([]);
  const [more, setMore] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  // Every listing of this number: any match status, ready / on hold / sold, no keyword filter.
  const params = { direction: 'property', phones: phone, statuses: 'hot,warm,custom,unmatched', stock_status: 'ready,on_hold,sold', search: '' };
  const first = useData((signal) => api.get<Page>(`/workspace?${query({ ...params, offset: 0 })}`, signal), [phone]);
  const rows = [...(first.data?.rows ?? []), ...extra];
  const hasMore = more ?? !!first.data?.has_more;

  async function loadMore() {
    setBusy(true);
    try {
      const page = await api.get<Page>(`/workspace?${query({ ...params, offset: rows.length })}`);
      setExtra((current) => [...current, ...page.rows]); setMore(page.has_more);
    } catch (reason) {
      toast.danger(errorMessage(reason, 'More listings could not be loaded. Please try again.'));
    } finally { setBusy(false); }
  }

  return (
    <>
      <Modal.CloseTrigger />
      <Modal.Header>
        <Modal.Heading>{name ? `Listings · ${name}` : 'Listings'}</Modal.Heading>
        <p className="mt-1 text-sm text-muted tabular-nums">{formatPhone(phone)}{first.data ? ` · ${rows.length}${hasMore ? '+' : ''} shown` : ''}</p>
      </Modal.Header>
      <Modal.Body className="space-y-3">
        <ErrorNotice message={first.error} onRetry={first.reload} />
        {first.loading && !first.data && <SkeletonList rows={4} />}
        {!first.loading && !first.error && rows.length === 0 && (
          <EmptyState compact icon={<Building2 />} title="No listings yet" description="There are no Ready, On hold or Sold listings with this number." />
        )}
        {rows.length > 0 && (
          <Accordion allowsMultipleExpanded className="w-full">
            {rows.map((row) => (
              <Accordion.Item key={row.id} id={row.id}>
                <Accordion.Heading>
                  <Accordion.Trigger className="items-start gap-3 py-3 text-left">
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        {row.public_id && <span className="font-mono text-[0.8125rem] font-semibold text-cyan-800">{row.public_id}</span>}
                        <StatusChip status={row.entity_status ?? 'ready'} className="h-6 px-2 text-xs" />
                        <PurposeTags row={row} kind="property" compact />
                        {row.sent_at && <span className="text-caption text-muted">{relativeDateTime(row.sent_at)}</span>}
                      </span>
                      <span className="mt-1 block text-sm leading-snug font-semibold break-words text-foreground">{headline(messageText(row))}</span>
                      {structuredSummary(row) && <span className="mt-0.5 block text-caption break-words text-muted">{structuredSummary(row)}</span>}
                    </span>
                    <Accordion.Indicator />
                  </Accordion.Trigger>
                </Accordion.Heading>
                <Accordion.Panel>
                  <Accordion.Body>
                    {isMessagePart(row) ? (
                      <><PartText row={row} lines="all" className="text-sm" /><div className="mt-1"><OriginalMessage row={row} /></div></>
                    ) : <p className="rounded-xl bg-surface-secondary p-3 text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground/85">{row.raw_text}</p>}
                  </Accordion.Body>
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>
        )}
        {hasMore && <Button variant="secondary" fullWidth isPending={busy} onPress={loadMore}>Show more</Button>}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="tertiary" onPress={onClose}>Close</Button>
      </Modal.Footer>
    </>
  );
}
