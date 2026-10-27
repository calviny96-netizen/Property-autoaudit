"""Public IDs, the entity registry and listing/buyer status.

An *entity* is one unique message content per company and kind - exactly what a
card represents in the UI. For a bubble that holds several requests/listings the
content is that part's own text (``segment_text``), so every part has its own ID.
Entities keep their public ID (``L-AB908``) and status (ready / on_hold / sold /
deleted) while the matching tables are rebuilt, because they are keyed by a
content hash rather than by row IDs that change on every recompute.

v4.10: the key is ``documents.content_key`` (``parser.content_hash``: the part text
without the sales signature, links, phone numbers, emoji, punctuation and case), so
the same request reposted with another signature or as "KONIG" / "Konig" is one ID.
A document without a key yet groups by md5 of its exact part text (the pre-v4.10
key, ``GROUP_KEY``). When copies that already had separate IDs turn out to share a
key, ``merge_entities`` folds them into the oldest ID (see there).
"""
import json
import re
import uuid

from fastapi import HTTPException

from db import connect

DOCUMENT_TYPES = ('buyer_request', 'property_listing')
PREFIX = {'buyer_request': 'B', 'property_listing': 'L'}
TYPE_BY_PREFIX = {value: key for key, value in PREFIX.items()}
STATUSES = ('ready', 'on_hold', 'sold', 'deleted')
STATUS_LABELS = {'ready': 'Ready', 'on_hold': 'On hold', 'sold': 'Sold', 'deleted': 'Deleted'}
# A merged entity keeps the most restrictive status of the IDs it absorbs: a disabled (deleted) buyer stays disabled.
STATUS_SEVERITY = {'ready': 0, 'on_hold': 1, 'sold': 2, 'deleted': 3}


def group_key_sql(doc='d', raw='r'):
    """SQL: the entity/group key of a document row: its content key, else md5 of its exact part text."""
    return f"coalesce({doc}.content_key, md5(coalesce({doc}.segment_text, {raw}.raw_text)))"


GROUP_KEY = group_key_sql()


def group_key(row):
    """Python twin of ``group_key_sql`` for a row with ``content_key``, ``segment_text`` and ``raw_text``."""
    import hashlib
    return row.get('content_key') or hashlib.md5((row.get('segment_text') or row.get('raw_text') or '').encode('utf-8')).hexdigest()

# No I or O: they are too easy to confuse with 1 and 0 when read aloud or typed.
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
BASE = len(ALPHABET)
DIGITS = 1000


def _letters(block: int) -> str:
    width, span = 2, BASE ** 2
    while block >= span:
        block -= span
        width += 1
        span *= BASE
    out = []
    for _ in range(width):
        out.append(ALPHABET[block % BASE])
        block //= BASE
    return ''.join(reversed(out))


def encode_public_id(document_type: str, seq: int) -> str:
    """``seq`` 0 -> L-AA000, 999 -> L-AA999, 1000 -> L-AB000, 575999 -> L-ZZ999, 576000 -> L-AAA000."""
    if seq < 0:
        raise ValueError('seq must not be negative')
    return f'{PREFIX[document_type]}-{_letters(seq // DIGITS)}{seq % DIGITS:03d}'


def decode_public_id(value: str):
    """Return ``(document_type, seq)`` for a canonical ID such as ``L-AB908``."""
    match = re.fullmatch(r'([BL])-([A-HJ-NP-Z]{2,})(\d{3})', (value or '').strip().upper())
    if not match:
        return None
    letters = match.group(2)
    offset = sum(BASE ** width for width in range(2, len(letters)))
    number = 0
    for char in letters:
        number = number * BASE + ALPHABET.index(char)
    return TYPE_BY_PREFIX[match.group(1)], (offset + number) * DIGITS + int(match.group(3))


def normalize_id_query(value: str) -> str:
    """Typed IDs are matched without hyphen, spaces or case: ``l ab-908`` -> ``LAB908``."""
    return re.sub(r'[^A-Za-z0-9]', '', value or '').upper()


def exact_public_ids(needle: str) -> list:
    """Every canonical ID a *complete* typed ID can mean, or ``[]`` for partial text.

    The L-/B- prefix is optional (``AB908`` may be a listing or a buyer), and
    ``LAB908`` may be ``L-AB908`` or a three-letter ID. Complete IDs are looked
    up through an index; only partial text needs a slow contains-search.
    """
    found = []
    pattern = r'[A-HJ-NP-Z]{2,}\d{3}'
    if re.fullmatch(pattern, needle):
        found += [f'{prefix}-{needle}' for prefix in PREFIX.values()]
    if needle[:1] in TYPE_BY_PREFIX and re.fullmatch(pattern, needle[1:]):
        found.append(f'{needle[0]}-{needle[1:]}')
    return found


def cache_lock(conn):
    """Serialises writers of the denormalised group tables (refresh, status changes)."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042028))")


def with_aliases(conn, company_id: str, public_ids):
    """``public_ids`` plus the IDs that retired (merged) ones now resolve to (``xm.entity_aliases``)."""
    ids = list(dict.fromkeys(public_ids or ()))
    if not ids:
        return ids
    rows = conn.execute('SELECT public_id FROM xm.entity_aliases WHERE company_id = %s AND old_public_id = ANY(%s)',
                        (company_id, ids)).fetchall()
    return list(dict.fromkeys(ids + [row['public_id'] for row in rows]))


def backfill_content_keys(conn, company_id: str, batch: int = 5000) -> int:
    """Fill ``content_key`` of active documents stored before v4.10 (once; later uploads and Reprocess set it).

    Runs in the worker (recompute / reindex), never at API start-up: it parses every text once."""
    from parser import content_hash
    total, cache = 0, {}
    while True:
        rows = conn.execute(
            '''SELECT d.id, coalesce(d.segment_text, r.raw_text) AS text FROM xm.documents d
               JOIN xm.raw_messages r ON r.id = d.raw_message_id
               WHERE d.company_id = %s AND d.content_key IS NULL AND d.active LIMIT %s''', (company_id, batch)).fetchall()
        if not rows:
            break
        keys = []
        for row in rows:
            text = row['text'] or ''
            if text not in cache:
                cache[text] = content_hash(text)
            keys.append(cache[text])
        conn.execute('''UPDATE xm.documents d SET content_key = k.key FROM unnest(%s::uuid[], %s::text[]) AS k(id, key)
                        WHERE d.id = k.id''', ([str(row['id']) for row in rows], keys))
        total += len(rows)
        if len(cache) > 200000:
            cache.clear()
        if total % 50000 < batch:
            print(f'Content keys: {total} documents', flush=True)
    return total


def merge_entities(conn, company_id: str) -> int:
    """Give every content key exactly one entity; returns the number of retired IDs.

    Entities used to be keyed by the exact part text, so copies of one content may hold several IDs
    (B-AA417 / B-AA418: the same request with two signatures). For each key whose active copies are
    linked to several entities - or to one entity still keyed by its old text hash - this:

    * keeps the OLDEST public ID: the lowest sequence number, i.e. the first posting (IDs are numbered
      in order of first posting); an orphan entity that already holds the key (no active copies) counts
      too, exactly as a repost would have revived it;
    * gives the kept entity the most restrictive status of the merged ones (deleted > sold > on_hold >
      ready), so a disabled buyer stays disabled, and the earliest first_seen_at;
    * moves documents, match history (``match_events``, earliest find wins), delivery receipts and Jev
      reviews to the kept entity, deletes the others and records ``old_public_id -> public_id`` in
      ``xm.entity_aliases`` so a retired ID typed in a search still finds the card;
    * re-keys the kept entity to the content key (``text_hash``), so new copies link to it.

    IDs are never reused: the retired sequence numbers stay consumed. An entity whose active copies no
    longer share one key (not expected: the key is a function of the text) keeps the key of most copies;
    the other copies are unlinked and join the entity of their own key.
    """
    conn.execute('DROP TABLE IF EXISTS pg_temp.xm_entity_keys')
    conn.execute(f'''CREATE TEMP TABLE xm_entity_keys ON COMMIT DROP AS
        SELECT d.document_type, d.entity_id, {GROUP_KEY} AS h, count(*) AS n
        FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id
        WHERE d.company_id = %s AND d.active AND d.entity_id IS NOT NULL GROUP BY 1, 2, 3''', (company_id,))
    conn.execute('ANALYZE pg_temp.xm_entity_keys')
    split = conn.execute('''SELECT entity_id, (array_agg(h ORDER BY n DESC, h))[1] AS h FROM xm_entity_keys
                            GROUP BY entity_id HAVING count(*) > 1''').fetchall()
    if split:
        values = ([str(row['entity_id']) for row in split], [row['h'] for row in split])
        conn.execute(f'''UPDATE xm.documents d SET entity_id = NULL
            FROM xm.raw_messages r, unnest(%s::uuid[], %s::text[]) AS s(entity_id, h)
            WHERE r.id = d.raw_message_id AND d.entity_id = s.entity_id AND d.active AND {GROUP_KEY} <> s.h''', values)
        conn.execute('''DELETE FROM xm_entity_keys k USING unnest(%s::uuid[], %s::text[]) AS s(entity_id, h)
                        WHERE k.entity_id = s.entity_id AND k.h <> s.h''', values)
    groups = conn.execute(
        '''WITH members AS (
             SELECT document_type, h, entity_id FROM xm_entity_keys
             UNION
             SELECT e.document_type, e.text_hash, e.entity_id FROM xm.entities e
             WHERE e.company_id = %(c)s
               AND EXISTS (SELECT 1 FROM xm_entity_keys k WHERE k.document_type = e.document_type AND k.h = e.text_hash)
               AND NOT EXISTS (SELECT 1 FROM xm_entity_keys k WHERE k.entity_id = e.entity_id))
           SELECT m.h, array_agg(e.entity_id ORDER BY e.seq) AS ids
           FROM members m JOIN xm.entities e ON e.entity_id = m.entity_id
           GROUP BY m.document_type, m.h
           HAVING count(*) > 1 OR bool_or(e.text_hash <> m.h)''', {'c': company_id}).fetchall()
    if not groups:
        return 0
    involved = [str(item) for group in groups for item in group['ids']]
    entities = {str(row['entity_id']): row for row in conn.execute(
        '''SELECT entity_id, document_type, public_id, status, status_note, status_changed_at, status_changed_by,
                  first_seen_at FROM xm.entities WHERE entity_id = ANY(%s::uuid[])''', (involved,)).fetchall()}
    keepers, merges = [], []
    for group in groups:
        members = [entities[str(item)] for item in group['ids']]
        kept = members[0]
        # Most restrictive status; among equals the keeper's own, else the latest change.
        strictest = max(members, key=lambda row: (STATUS_SEVERITY.get(row['status'], 0), row is kept,
                                                  row['status_changed_at'] is not None,
                                                  row['status_changed_at'] or kept['status_changed_at'] or 0))
        seen = [row['first_seen_at'] for row in members if row['first_seen_at']]
        keepers.append((str(kept['entity_id']), group['h'], strictest['status'], strictest['status_note'],
                        strictest['status_changed_at'], strictest['status_changed_by'], min(seen) if seen else None))
        merges += [(str(row['entity_id']), str(kept['entity_id']), row['public_id'], kept['public_id'], row['status'],
                    kept['document_type']) for row in members[1:]]
    if merges:
        _retire(conn, company_id, merges, {row[0]: row[2] for row in keepers})
    ids = [row[0] for row in keepers]
    # Two steps: a key may still be held by another entity that moves to its own key in this same pass.
    conn.execute("UPDATE xm.entities SET text_hash = 'rekey:' || entity_id::text WHERE entity_id = ANY(%s::uuid[])", (ids,))
    conn.execute(
        '''UPDATE xm.entities e SET text_hash = k.h, status = k.status, status_note = k.note,
                  status_changed_at = k.changed_at, status_changed_by = k.changed_by,
                  first_seen_at = coalesce(k.first_seen, e.first_seen_at)
           FROM unnest(%s::uuid[], %s::text[], %s::text[], %s::text[], %s::timestamptz[], %s::uuid[], %s::timestamp[])
                AS k(entity_id, h, status, note, changed_at, changed_by, first_seen)
           WHERE e.entity_id = k.entity_id''',
        [[str(row[index]) if index in (0, 5) and row[index] is not None else row[index] for row in keepers]
         for index in range(7)])
    conn.execute('''UPDATE xm.document_groups g SET status = e.status, public_id = e.public_id
                    FROM xm.entities e WHERE g.entity_id = e.entity_id AND e.entity_id = ANY(%s::uuid[])''', (ids,))
    return len(merges)


def _retire(conn, company_id, merges, final_status):
    """Point everything that names a retired entity at the entity it merged into, then delete it."""
    conn.execute('DROP TABLE IF EXISTS pg_temp.xm_entity_merge')
    conn.execute('''CREATE TEMP TABLE xm_entity_merge ON COMMIT DROP AS
        SELECT * FROM unnest(%s::uuid[], %s::uuid[], %s::text[], %s::text[]) AS m(old, keeper, old_public, keeper_public)''',
                 [[row[index] for row in merges] for index in range(4)])
    conn.execute('UPDATE xm.documents d SET entity_id = m.keeper FROM xm_entity_merge m WHERE d.entity_id = m.old')
    conn.execute('''UPDATE xm.document_groups g SET entity_id = m.keeper, public_id = m.keeper_public
                    FROM xm_entity_merge m WHERE g.entity_id = m.old''')
    # Match history: one row per (buyer, listing) pair after the merge; the earliest find is kept (so a pair
    # announced before is not announced again) and takes the latest score and status of the merged rows.
    conn.execute('DROP TABLE IF EXISTS pg_temp.xm_event_merge')
    conn.execute(
        '''CREATE TEMP TABLE xm_event_merge ON COMMIT DROP AS
           WITH moved AS (SELECT old AS id FROM xm_entity_merge UNION SELECT keeper FROM xm_entity_merge),
           touched AS (
             SELECT me.*, coalesce(mb.keeper, me.buyer_entity) AS b, coalesce(ml.keeper, me.listing_entity) AS l
             FROM xm.match_events me
             LEFT JOIN xm_entity_merge mb ON mb.old = me.buyer_entity
             LEFT JOIN xm_entity_merge ml ON ml.old = me.listing_entity
             WHERE me.company_id = %s AND (me.buyer_entity IN (SELECT id FROM moved) OR me.listing_entity IN (SELECT id FROM moved))
           )
           SELECT b, l, array_agg(id) AS ids, (array_agg(id ORDER BY found_at, id))[1] AS keep, bool_or(active) AS active,
                  max(last_seen_at) AS last_seen_at, min(hot_at) AS hot_at,
                  (array_agg(last_score ORDER BY last_seen_at DESC, id))[1] AS last_score,
                  (array_agg(last_status ORDER BY last_seen_at DESC, id))[1] AS last_status
           FROM touched GROUP BY b, l HAVING count(*) > 1 OR bool_or(b <> buyer_entity OR l <> listing_entity)''',
        (company_id,))
    conn.execute('''DELETE FROM xm.match_events me USING xm_event_merge p
                    WHERE me.id = ANY(p.ids) AND me.id <> p.keep''')
    conn.execute('''UPDATE xm.match_events me SET buyer_entity = p.b, listing_entity = p.l, active = p.active,
                      last_seen_at = p.last_seen_at, hot_at = p.hot_at, last_score = p.last_score, last_status = p.last_status
                    FROM xm_event_merge p WHERE me.id = p.keep''')
    conn.execute(
        '''INSERT INTO xm.match_deliveries(company_id, delivery_scope, buyer_entity_id, listing_entity_id, delivered_at, delivery_id)
           SELECT md.company_id, md.delivery_scope, coalesce(mb.keeper, md.buyer_entity_id), coalesce(ml.keeper, md.listing_entity_id),
                  md.delivered_at, md.delivery_id
           FROM xm.match_deliveries md
           LEFT JOIN xm_entity_merge mb ON mb.old = md.buyer_entity_id
           LEFT JOIN xm_entity_merge ml ON ml.old = md.listing_entity_id
           WHERE md.company_id = %s AND (mb.old IS NOT NULL OR ml.old IS NOT NULL)
           ON CONFLICT DO NOTHING''', (company_id,))
    for column, public in (('source_entity_id', 'source_public_id'), ('target_entity_id', 'target_public_id'),
                           ('buyer_entity_id', None), ('listing_entity_id', None)):
        extra = f', {public} = m.keeper_public' if public else ''
        conn.execute(f'''UPDATE xm.ai_reviews a SET {column} = m.keeper{extra} FROM xm_entity_merge m
                         WHERE a.company_id = %s AND a.{column} = m.old''', (company_id,))
    # Aliases: the retired ID, and IDs retired into it earlier, now resolve to the kept ID.
    conn.execute('''UPDATE xm.entity_aliases a SET public_id = m.keeper_public, entity_id = m.keeper
                    FROM xm_entity_merge m WHERE a.company_id = %s AND a.public_id = m.old_public''', (company_id,))
    conn.execute('''INSERT INTO xm.entity_aliases(company_id, old_public_id, public_id, entity_id)
                    SELECT %s, old_public, keeper_public, keeper FROM xm_entity_merge
                    ON CONFLICT (company_id, old_public_id) DO UPDATE SET public_id = EXCLUDED.public_id,
                      entity_id = EXCLUDED.entity_id, merged_at = now()''', (company_id,))
    conn.execute('DELETE FROM xm.entities e USING xm_entity_merge m WHERE e.entity_id = m.old')
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO xm.audit_events(company_id, event_type, entity_type, entity_id, details) VALUES (%s,'entity_merged',%s,%s,%s::jsonb)",
            [(company_id, document_type, old_public,
              json.dumps({'into': keeper_public, 'status': old_status, 'kept_status': final_status.get(keeper)}))
             for old, keeper, old_public, keeper_public, old_status, document_type in merges])


def assign_entities(conn, company_id: str, rekey: bool = True) -> int:
    """Create missing entities (allocating public IDs in order of first posting) and link documents.

    ``rekey`` (the worker: recompute, reindex) first fills missing content keys and merges entities that
    share one (``merge_entities``). API start-up passes False: it must stay fast, and without keys every
    document keeps its pre-v4.10 key, so nothing changes until the worker runs."""
    created = 0
    if rekey:
        backfill_content_keys(conn, company_id)
        merge_entities(conn, company_id)
    for document_type in DOCUMENT_TYPES:
        fresh_sql = f'''
          WITH todo AS (
            SELECT {GROUP_KEY} AS text_hash, min(r.sent_at) AS first_sent, min(d.id::text) AS tiebreak
            FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id
            WHERE d.company_id = %(c)s AND d.document_type = %(t)s AND d.entity_id IS NULL AND d.active
            GROUP BY 1
          ), fresh AS (
            SELECT t.* FROM todo t WHERE NOT EXISTS (
              SELECT 1 FROM xm.entities e
              WHERE e.company_id = %(c)s AND e.document_type = %(t)s AND e.text_hash = t.text_hash)
          )'''
        params = {'c': company_id, 't': document_type}
        count = conn.execute(fresh_sql + ' SELECT count(*) AS n FROM fresh', params).fetchone()['n']
        if count:
            base = conn.execute(
                '''INSERT INTO xm.entity_counters(company_id, document_type, next_seq) VALUES (%s, %s, %s)
                   ON CONFLICT (company_id, document_type)
                   DO UPDATE SET next_seq = xm.entity_counters.next_seq + EXCLUDED.next_seq
                   RETURNING next_seq - %s AS base''',
                (company_id, document_type, count, count)).fetchone()['base']
            inserted = conn.execute(
                fresh_sql + ''', numbered AS (
                  SELECT text_hash, first_sent, row_number() OVER (ORDER BY first_sent NULLS LAST, tiebreak) AS rn FROM fresh
                ) INSERT INTO xm.entities(company_id, document_type, text_hash, seq, public_id, first_seen_at)
                  SELECT %(c)s, %(t)s, n.text_hash, %(base)s + n.rn - 1,
                         xm.public_id(%(prefix)s, %(base)s + n.rn - 1), n.first_sent
                  FROM numbered n
                  ON CONFLICT (company_id, document_type, text_hash) DO NOTHING''',
                {**params, 'base': base, 'prefix': PREFIX[document_type]}).rowcount
            created += inserted
    if created > 1000:
        conn.execute('ANALYZE xm.entities')
    # The key is computed once per document in a CTE so the unique index on
    # (company, kind, text_hash) drives the join; filtering on the expression directly
    # degenerates into a nested loop over every pair.
    conn.execute(
        f'''WITH h AS MATERIALIZED (
             SELECT d.id AS document_id, d.document_type, {GROUP_KEY} AS text_hash
             FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id
             WHERE d.company_id = %(c)s AND d.entity_id IS NULL AND d.active)
           UPDATE xm.documents d SET entity_id = e.entity_id
           FROM h JOIN xm.entities e ON e.company_id = %(c)s AND e.document_type = h.document_type AND e.text_hash = h.text_hash
           WHERE d.id = h.document_id''', {'c': company_id})
    return created


def recount_groups(conn, company_id: str, entity_ids):
    """Refresh hot/warm/custom counters of every group matched with the given entities.

    Counters only include counterparts whose status is ``ready``; this runs
    when a status changes so the cards update without a full cache rebuild.
    """
    conn.execute(
        '''WITH changed AS (
             SELECT group_id FROM xm.document_groups WHERE company_id = %(c)s AND entity_id = ANY(%(ids)s::uuid[])
           ), affected AS (
             SELECT gm.property_group_id AS group_id FROM xm.group_matches gm JOIN changed c ON c.group_id = gm.buyer_group_id
             WHERE gm.company_id = %(c)s
             UNION
             SELECT gm.buyer_group_id FROM xm.group_matches gm JOIN changed c ON c.group_id = gm.property_group_id
             WHERE gm.company_id = %(c)s
           ), counts AS (
             SELECT a.group_id,
                    count(x.status) FILTER (WHERE x.status = 'hot') AS hot,
                    count(x.status) FILTER (WHERE x.status = 'warm') AS warm,
                    count(x.status) FILTER (WHERE x.status = 'custom') AS custom
             FROM affected a LEFT JOIN LATERAL (
               SELECT gm.status FROM xm.group_matches gm
                 JOIN xm.document_groups o ON o.group_id = gm.property_group_id AND o.status = 'ready'
               WHERE gm.buyer_group_id = a.group_id
               UNION ALL
               SELECT gm.status FROM xm.group_matches gm
                 JOIN xm.document_groups o ON o.group_id = gm.buyer_group_id AND o.status = 'ready'
               WHERE gm.property_group_id = a.group_id
             ) x ON true
             GROUP BY a.group_id
           )
           UPDATE xm.document_groups g SET hot_count = counts.hot, warm_count = counts.warm, custom_count = counts.custom
           FROM counts WHERE g.group_id = counts.group_id''',
        {'c': company_id, 'ids': [str(item) for item in entity_ids]})


def resolve_refs(conn, company_id: str, refs):
    """Entity rows (locked) for a mix of entity UUIDs and public IDs of this company."""
    uuids, publics = [], []
    for ref in refs:
        text = str(ref).strip()
        if not text:
            continue
        try:
            uuids.append(str(uuid.UUID(text)))
        except ValueError:
            publics.append(text.upper())
    if not uuids and not publics:
        return []
    publics = with_aliases(conn, company_id, publics)  # a retired (merged) ID acts on the ID it merged into
    return conn.execute(
        '''SELECT entity_id, document_type, public_id, status FROM xm.entities
           WHERE company_id = %s AND (entity_id = ANY(%s::uuid[]) OR public_id = ANY(%s))
           ORDER BY seq FOR UPDATE''', (company_id, uuids, publics)).fetchall()


def set_entity_status(conn, company_id: str, user_id, refs, status: str, note: str | None = None):
    """Mark buyers/listings ready, on_hold, sold or deleted. Soft state: always reversible."""
    if status not in STATUSES:
        raise HTTPException(400, 'Unknown status.')
    refs = list(dict.fromkeys(str(item) for item in refs))
    if not refs:
        raise HTTPException(400, 'Select at least one ID.')
    if len(refs) > 200:
        raise HTTPException(400, 'At most 200 IDs per status change.')
    cache_lock(conn)
    rows = resolve_refs(conn, company_id, refs)
    if not rows:
        raise HTTPException(404, 'ID not found.')
    changing = [row for row in rows if row['status'] != status]
    if changing:
        ids = [str(row['entity_id']) for row in changing]
        conn.execute(
            '''UPDATE xm.entities SET status = %s, status_note = %s, status_changed_at = now(), status_changed_by = %s
               WHERE company_id = %s AND entity_id = ANY(%s::uuid[])''',
            (status, (note or '').strip()[:300] or None, user_id, company_id, ids))
        conn.execute('UPDATE xm.document_groups SET status = %s WHERE company_id = %s AND entity_id = ANY(%s::uuid[])',
                     (status, company_id, ids))
        recount_groups(conn, company_id, ids)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO xm.audit_events(company_id, event_type, entity_type, entity_id, details) VALUES (%s,'entity_status',%s,%s,%s::jsonb)",
                [(company_id, row['document_type'], row['public_id'],
                  json.dumps({'from': row['status'], 'to': status, 'by': str(user_id), 'note': note or None}))
                 for row in changing])
        listing_ids = [str(row['entity_id']) for row in changing if row['document_type'] == 'property_listing']
        if listing_ids:
            import stock
            stock.snapshot_for_entities(conn, company_id, listing_ids)
    return {
        'status': status,
        'updated': [{'entity_id': row['entity_id'], 'public_id': row['public_id'], 'document_type': row['document_type'],
                     'previous': row['status']} for row in changing],
        'unchanged': [row['public_id'] for row in rows if row['status'] == status],
        'missing': max(0, len(refs) - len(rows)),
    }


def lookup(conn, company_id: str, query: str, limit: int = 8):
    """Resolve a typed ID (partial allowed) to entities with a short preview."""
    needle = normalize_id_query(query)
    if len(needle) < 2:
        return []
    exact = exact_public_ids(needle)
    if exact:
        exact = with_aliases(conn, company_id, exact)  # a retired (merged) ID finds the ID it merged into
    match_sql, match_arg = ('e.public_id = ANY(%s)', exact) if exact else ("replace(e.public_id, '-', '') LIKE %s", '%' + needle + '%')
    from matching_scope import document_scope_filter
    scope_sql, scope_params = document_scope_filter(conn)
    return conn.execute(
        f'''SELECT e.entity_id, e.public_id, e.document_type, e.status, d.contact_name, d.categories, d.locations
           FROM xm.entities e
           LEFT JOIN xm.document_groups g ON g.entity_id = e.entity_id
           LEFT JOIN xm.documents d ON d.id = g.group_id
           LEFT JOIN xm.raw_messages r ON r.id=d.raw_message_id
           WHERE e.company_id = %s AND {match_sql}''' + scope_sql + ' ORDER BY e.seq DESC LIMIT %s',
        [company_id,match_arg,*scope_params,min(max(limit, 1), 20)]).fetchall()


def sync_groups_from_entities(conn, company_id: str) -> int:
    """Fill entity columns of already-built group rows (used once, when upgrading)."""
    return conn.execute(
        '''UPDATE xm.document_groups g SET entity_id = d.entity_id, public_id = e.public_id, status = e.status
           FROM xm.documents d JOIN xm.entities e ON e.entity_id = d.entity_id
           WHERE g.company_id = %s AND g.entity_id IS NULL AND d.id = g.group_id''', (company_id,)).rowcount


def _migrate_company(conn, company_id: str):
    conn.execute(
        '''INSERT INTO xm.app_preferences(company_id, company_name) VALUES (%s, NULL) ON CONFLICT (company_id) DO NOTHING''',
        (company_id,))
    conn.execute(
        '''UPDATE xm.app_preferences ap SET company_name = coalesce(
              (SELECT CASE WHEN ap.company_id = 'xm' THEN 'Property' ELSE u.display_name END
               FROM xm.users u WHERE u.workspace_id = ap.company_id ORDER BY u.created_at LIMIT 1),
              ap.company_id)
           WHERE ap.company_id = %s AND ap.company_name IS NULL''', (company_id,))
    assign_entities(conn, company_id, rekey=False)
    sync_groups_from_entities(conn, company_id)
    # Pairs that exist at upgrade time are history, not news.
    conn.execute(
        '''INSERT INTO xm.match_events(company_id, buyer_entity, listing_entity, first_score, last_score, temperature,
                                       last_status, source, found_at, last_seen_at)
           SELECT %(c)s, bg.entity_id, pg.entity_id, max(gm.score), max(gm.score),
                  (array_agg(gm.status ORDER BY gm.score DESC, gm.status = 'custom'))[1],
                  (array_agg(gm.status ORDER BY gm.score DESC, gm.status = 'custom'))[1], 'baseline', now(), now()
           FROM xm.group_matches gm
           JOIN xm.document_groups bg ON bg.group_id = gm.buyer_group_id
           JOIN xm.document_groups pg ON pg.group_id = gm.property_group_id
           WHERE gm.company_id = %(c)s AND bg.entity_id IS NOT NULL AND pg.entity_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM xm.match_events WHERE company_id = %(c)s)
           GROUP BY bg.entity_id, pg.entity_id
           ON CONFLICT (company_id, buyer_entity, listing_entity) DO NOTHING''', {'c': company_id})


def ensure_custom_count(conn):
    """v4.1 per-card counter of pairs whose strict buyer location is unconfirmed (status 'custom').

    Checked first so a restart never queues an exclusive table lock behind a running rebuild.
    """
    if not conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='xm'
                           AND table_name='document_groups' AND column_name='custom_count'""").fetchone():
        conn.execute('ALTER TABLE xm.document_groups ADD COLUMN IF NOT EXISTS custom_count bigint NOT NULL DEFAULT 0')


def migrate_v4():
    """One-time, restartable backfill after upgrading from v3: IDs, group links, match baseline."""
    with connect() as conn:
        # Serialises API and worker when both start together after an upgrade.
        conn.execute('SELECT pg_advisory_xact_lock(9042099)')
        ensure_custom_count(conn)
        companies = conn.execute(
            '''SELECT company_id FROM (SELECT DISTINCT company_id FROM xm.documents
               UNION SELECT workspace_id FROM xm.users WHERE workspace_id IS NOT NULL) c
               WHERE company_id IS NOT NULL ORDER BY 1''').fetchall()
        for row in companies:
            _migrate_company(conn, row['company_id'])
        conn.commit()
    return len(companies)
