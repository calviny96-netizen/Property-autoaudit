'use client';

import { useState } from 'react';
import { Button, Modal } from '@heroui/react';
import { ArrowRight, Download, SearchX } from 'lucide-react';
import { DetailList, EmptyState, ErrorNotice, IdChip, LoadingIndicator, SkeletonList, StatusBadge, StatusChip } from '@/components/app/primitives';
import { StatusMenu } from '@/components/app/status-menu';
import { DuplicateNote, OriginalMessage, PartText, PurposeTags, RecommendationCard, StrictNote, TierGroups, WhatsAppButton } from '@/components/match/cards';
import { query } from '@/lib/api';
import { cleanName, countLabel, displaySender, formatPhone, isMessagePart, messageText, number, relativeDateTime, structuredSummary } from '@/lib/format';
import type { Direction, EntityStatus, RecentGroup, RecentMatch, RecentResponse, Row, Temperature } from '@/lib/types';
import { useData } from '@/lib/use-data';
import { useApi } from '@/lib/workspace-context';

const MAX_PICK = 200;
/** The API may send the ownership tier on the match or on its target. */
const tierOf = (match: RecentMatch) => match.ownership_tier ?? match.target.ownership_tier;

export function RecentDetailModal({ group, direction, filters, onClose, onStatus, onExport, onManual }: {
  group: RecentGroup; direction: Direction; filters: Record<string, string | boolean>;
  onClose: () => void; onStatus: (row: Row, status: EntityStatus) => void;
  onExport: (events?: number[]) => void; onManual?: () => void;
}) {
  const api = useApi();
  const [limit, setLimit] = useState(50);
  const [picked, setPicked] = useState<number[]>([]);
  // Only this source's counterparts are loaded after opening the gallery card.
  const data = useData(async (signal) => {
    const matches = [] as RecentGroup['matches'];
    let current: RecentGroup | undefined;
    for (let offset = 0; offset < limit; offset += 50) {
      const response = await api.get<RecentResponse>(`/matches/recent?${query({ ...filters, direction, source_entity: group.source.entity_id, per_source: 50, match_offset: offset, limit: 1 })}`, signal);
      current = response.groups[0];
      matches.push(...(current?.matches ?? []));
      if (!current || matches.length >= current.pair_count) break;
    }
    return current ? { ...current, matches } : null;
  }, [limit]);
  const source = data.data?.source ?? group.source;
  const matches = data.data?.matches ?? [];
  const count = data.data?.pair_count ?? group.pair_count;
  const counts: [Temperature, number][] = [
    ['hot', data.data?.hot_count ?? group.hot_count], ['warm', data.data?.warm_count ?? group.warm_count], ['custom', data.data?.custom_count ?? group.custom_count ?? 0],
  ];
  const sourceLabel = direction === 'buyer' ? 'Buyer' : 'Listing';
  const targetLabel = direction === 'buyer' ? 'listing' : 'buyer';
  const targetKind = direction === 'buyer' ? 'property' : 'buyer';
  const toggle = (id: number) => setPicked((values) => values.includes(id) ? values.filter((value) => value !== id) : values.length < MAX_PICK ? [...values, id] : values);
  const sender = displaySender(source.author);
  const summary = structuredSummary(source);
  const part = isMessagePart(source);

  return (
    <Modal.Backdrop isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog style={{ width: 'calc(100vw - 2rem)', maxWidth: 1200 }}>
          <Modal.CloseTrigger />
          <Modal.Header>
            <Modal.Heading>{sourceLabel} {source.public_id ?? ''}</Modal.Heading>
            <p className="mt-1 text-sm text-muted">{countLabel(count, `new ${targetLabel}`)} matched in the chosen period</p>
          </Modal.Header>
          <Modal.Body>
            <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
              <section aria-label={`About this ${sourceLabel.toLowerCase()}`} className="min-w-0 rounded-2xl border border-border bg-surface-secondary/50 p-4 lg:sticky lg:top-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-overline font-semibold text-muted uppercase">{sourceLabel}</p>
                    <p className="mt-1 text-section font-semibold break-words text-foreground">{cleanName(source.contact_name) || `Unnamed ${sourceLabel.toLowerCase()}`}</p>
                  </div>
                  <StatusMenu status={source.entity_status} name={`${sourceLabel} ${source.public_id ?? ''}`.trim()} onChoose={(status) => onStatus(source, status)} />
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <IdChip id={source.public_id} kind={direction} />
                  <PurposeTags row={source} kind={direction} />
                  {(source.entity_status ?? 'ready') !== 'ready' && <StatusChip status={source.entity_status} />}
                  <StrictNote row={source} />
                  <DuplicateNote row={source} />
                </div>
                {(summary || !part) && <p className="mt-3 text-sm leading-relaxed text-foreground/85">{summary || messageText(source).slice(0, 280)}</p>}
                <PartText row={source} lines="all" className={summary ? 'mt-2' : 'mt-3'} />
                <DetailList className="mt-4 grid-cols-2" items={[
                  { label: 'Contact number', value: formatPhone(source.contact_phone) || 'Not available' },
                  { label: 'Posted', value: source.sent_at ? relativeDateTime(source.sent_at) : '' },
                  { label: 'Match found', value: relativeDateTime(group.latest_found_at) },
                  { label: 'Sender', value: sender },
                  { label: 'Chat group', value: source.chat_name ?? '' },
                ]} />
                <div className="mt-3"><OriginalMessage row={source} /></div>
                <div className="mt-3 border-t border-separator pt-3"><WhatsAppButton row={source} kind={direction} label={`WhatsApp ${direction === 'buyer' ? 'buyer' : 'listing agent'}`} /></div>
              </section>

              <section aria-label="Matches" className="min-w-0 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="text-section font-semibold text-foreground">Matches</h3>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {counts.map(([status, value]) => (value > 0 ? <StatusBadge key={status} status={status} count={value} size="sm" explain={false} /> : null))}
                    </div>
                  </div>
                  <Button variant="secondary" size="sm" onPress={() => onExport()}><Download className="size-4" aria-hidden="true" />Export all ({number(count)})</Button>
                </div>
                <ErrorNotice message={data.error} onRetry={data.reload} />
                {data.loading && !data.data && <SkeletonList rows={2} />}
                {data.loading && data.data && <LoadingIndicator />}
                {!data.loading && data.data === null && (
                  <EmptyState compact icon={<SearchX />} title="These matches have changed" description="They no longer fit the filters or your company rules. Close this window to refresh the list." />
                )}
                {matches.length > 0 && (
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-surface-secondary/60 px-3 py-2">
                    <span className="text-caption text-muted">
                      {picked.length ? <><strong className="font-semibold text-foreground tabular-nums">{number(picked.length)}</strong> selected</> : `Tick matches to export only those. ${direction === 'buyer' ? "The sender's own stock is" : "The sender's own buyers are"} listed first.`}
                      {matches.length > MAX_PICK ? ` · up to ${MAX_PICK}` : ''}
                    </span>
                    <span className="flex gap-1">
                      <Button size="sm" variant="tertiary" isDisabled={data.loading} onPress={() => setPicked(matches.slice(0, MAX_PICK).map((match) => match.event_id))}>Select shown</Button>
                      <Button size="sm" variant="tertiary" isDisabled={!picked.length} onPress={() => setPicked([])}>Clear</Button>
                    </span>
                  </div>
                )}
                <div aria-busy={data.loading} inert={data.loading} className="space-y-3">
                  <TierGroups items={matches} tierOf={tierOf} kind={targetKind} hasMore={!!data.data && matches.length < count}>
                    {(match) => (
                      <RecommendationCard key={match.event_id} target={{ ...match.target, score: match.score, status: match.temperature, ownership_tier: tierOf(match) }}
                        source={source} direction={direction}
                        checked={picked.includes(match.event_id)} onCheck={() => toggle(match.event_id)} onStatus={(status) => onStatus(match.target, status)}
                        extra={<p className="mt-2 text-caption text-muted">Found {relativeDateTime(match.found_at)}{match.upgraded_to_hot ? ' · Upgraded to Hot' : ''}{match.agent_name ? ` · Upload by ${match.agent_name}` : ''}</p>} />
                    )}
                  </TierGroups>
                </div>
                {data.data && matches.length < count && !data.error && (
                  <Button variant="secondary" fullWidth isDisabled={data.loading} onPress={() => setLimit((value) => value + 50)}>Show more ({number(matches.length)} of {number(count)})</Button>
                )}
              </section>
            </div>
          </Modal.Body>
          <Modal.Footer className="flex flex-wrap items-center justify-between gap-2 border-t border-separator">
            {onManual ? <Button variant="secondary" onPress={onManual}>Open in Match<ArrowRight className="size-4" aria-hidden="true" /></Button> : <span />}
            <div className="flex flex-wrap gap-2">
              <Button variant="tertiary" onPress={onClose}>Close</Button>
              <Button isDisabled={!picked.length || data.loading} onPress={() => onExport(picked)}><Download className="size-4" aria-hidden="true" />Export selected{picked.length ? ` (${picked.length})` : ''}</Button>
            </div>
          </Modal.Footer>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
