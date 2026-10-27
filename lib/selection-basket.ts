'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { cleanName, displaySender, formatPhone, matchStatusOf } from '@/lib/format';
import type { Direction, GroupBy, MatchStatus, Row } from '@/lib/types';

/** The most pairs one PDF export takes (api/export_all.py Selection). */
export const BASKET_MAX = 200;
/** Raw group key of messages without a WhatsApp author (see api/workspace_cache.py). */
const NO_SENDER = 'Tanpa nama pengirim';
const VERSION = 1;

/**
 * One ticked pair. Besides the IDs the API needs, it keeps what the tray shows and what decides the person it
 * belongs to (the source's sender and sales contact), so the tray never has to load anything.
 */
export type BasketItem = {
  direction: Direction;
  source_id: string;
  target_id: string | null;
  source_public_id?: string;
  source_author?: string | null;
  source_contact_name?: string | null;
  source_phone?: string | null;
  target_public_id?: string;
  target_name?: string | null;
  status: MatchStatus;
  added_at: number;
};

export const basketKey = (direction: Direction, sourceId: string, targetId?: string | null) => `${direction}:${sourceId}:${targetId ?? ''}`;
const keyOf = (item: BasketItem) => basketKey(item.direction, item.source_id, item.target_id);

export function basketItem(direction: Direction, source: Row, target: Row | null): BasketItem {
  return {
    direction, source_id: source.id, target_id: target?.id ?? null, source_public_id: source.public_id,
    source_author: source.author, source_contact_name: source.contact_name, source_phone: source.contact_phone,
    target_public_id: target?.public_id, target_name: target ? cleanName(target.contact_name) || null : null,
    status: target ? matchStatusOf(target) : 'unmatched', added_at: Date.now(),
  };
}

/** The person a pair is grouped under in the export: the same key as the server's company grouping. */
export function personOf(item: BasketItem, groupBy: GroupBy) {
  if (groupBy === 'phone') {
    const phone = item.source_phone ?? '';
    const name = cleanName(item.source_contact_name);
    return { key: phone, title: phone ? `${name ? `${name} · ` : ''}${formatPhone(phone)}` : 'No phone number', phone };
  }
  const key = (item.source_author ?? '').trim() || NO_SENDER;
  return { key, title: key === NO_SENDER ? 'Unknown sender' : displaySender(key) || 'Unknown sender', phone: item.source_phone ?? '' };
}

function read(storageKey: string): BasketItem[] {
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) ?? 'null') as { v?: number; items?: BasketItem[] } | null;
    if (saved?.v !== VERSION || !Array.isArray(saved.items)) return [];
    return saved.items.filter((item) => item && (item.direction === 'buyer' || item.direction === 'property') && typeof item.source_id === 'string').slice(0, BASKET_MAX);
  } catch { return []; }
}

export type AddResult = { added: number; skipped: number };

/**
 * Pairs ticked for the PDF, kept across sources, directions, filters and reloads (per person and company, in this browser).
 * Other tabs of the same company follow along through the storage event.
 */
export function useSelectionBasket(storageKey: string) {
  const [items, setItems] = useState<BasketItem[]>(() => (typeof window === 'undefined' ? [] : read(storageKey)));
  // The key only changes with the person or company, and the workspace remounts then (lib/session.tsx).
  useEffect(() => {
    const onStorage = (event: StorageEvent) => { if (event.key === storageKey) setItems(read(storageKey)); };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [storageKey]);

  const save = useCallback((next: BasketItem[]) => {
    try {
      if (next.length) window.localStorage.setItem(storageKey, JSON.stringify({ v: VERSION, items: next }));
      else window.localStorage.removeItem(storageKey);
    } catch { /* storage blocked or full: the selection still works until the page closes */ }
  }, [storageKey]);

  const update = useCallback((change: (current: BasketItem[]) => BasketItem[]) => {
    setItems((current) => { const next = change(current); if (next !== current) save(next); return next; });
  }, [save]);

  const keys = useMemo(() => new Set(items.map(keyOf)), [items]);
  const has = useCallback((key: string) => keys.has(key), [keys]);

  /** Adds what fits under BASKET_MAX; `skipped` counts the pairs that did not fit. */
  const add = useCallback((additions: BasketItem[]): AddResult => {
    const fresh = additions.filter((item, index) => !keys.has(keyOf(item)) && additions.findIndex((other) => keyOf(other) === keyOf(item)) === index);
    const room = Math.max(0, BASKET_MAX - items.length);
    const accepted = fresh.slice(0, room);
    if (accepted.length) update((current) => [...current, ...accepted.filter((item) => !current.some((other) => keyOf(other) === keyOf(item)))].slice(0, BASKET_MAX));
    return { added: accepted.length, skipped: fresh.length - accepted.length };
  }, [items.length, keys, update]);

  const remove = useCallback((removeKeys: string[]) => {
    const drop = new Set(removeKeys);
    update((current) => (current.some((item) => drop.has(keyOf(item))) ? current.filter((item) => !drop.has(keyOf(item))) : current));
  }, [update]);

  const clear = useCallback(() => update((current) => (current.length ? [] : current)), [update]);

  return useMemo(() => ({ items, has, add, remove, clear, keyOf }), [items, has, add, remove, clear]);
}

export type SelectionBasket = ReturnType<typeof useSelectionBasket>;
