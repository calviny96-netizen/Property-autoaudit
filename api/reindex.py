"""Rebuild extracted documents from original bubbles, preserving document IDs.

One WhatsApp bubble may hold several requests/listings ("#Buyer 1 … #Renter 1",
numbered offers). Each part is its own document, keyed by (raw_message_id,
segment_index); ``segment_text`` is the part's own text (NULL = whole bubble).
"""
import json
import uuid
from db import connect
from tenant import workspace_id
import parser as bubble_parser
from parser import parse_message
from embedding import embed
from qdrant import upsert, _request, COLLECTION
from analysis_window import in_window_sql, window_start
import progress as job_progress

DOCUMENT_KINDS = ('buyer_request', 'property_listing')


def glossary_values(conn):
    """Legacy {alias: canonical} view of the company glossary (all kinds)."""
    return {r['alias']: r['canonical'] for r in conn.execute("SELECT * FROM xm.glossary WHERE company_id=current_setting('xm.workspace_id')").fetchall()}


def glossary_entries(conn):
    """Structured company glossary for parse_message: [{term, canonical, kind, parent, note}]."""
    return [{'term': r['alias'], 'canonical': r['canonical'], 'kind': r.get('kind') or 'synonym',
             'parent': r.get('parent'), 'note': r.get('note')}
            for r in conn.execute("SELECT * FROM xm.glossary WHERE company_id=current_setting('xm.workspace_id') ORDER BY alias").fetchall()]


# v4.1 parser fields; read defensively so older parser builds keep working.
EXTRA_FIELDS = (('rent_price_min', None), ('rent_price_max', None), ('rent_price_basis', None),
                ('location_strict', False), ('strict_locations', []))
# Parser fields stored as xm.documents columns of the same name. A fixed list, so
# fields the parser adds later never turn into unknown SQL columns.
PARSED_COLUMNS = (('transaction_type', 'unknown'), ('categories', []), ('locations', []),
                  ('land_area_min', None), ('land_area_max', None), ('building_area_min', None), ('building_area_max', None),
                  ('price_min', None), ('price_max', None), ('price_basis', None), ('negotiable', False),
                  ('facing', []), ('exclusions', []), ('requirements', []),
                  ('contact_name', None), ('contact_phone', None), ('contact_phones', []), ('normalized_text', ''),
                  ('content_key', None))


def extra_values(parsed):
    values = {}
    for name, default in EXTRA_FIELDS:
        value = getattr(parsed, name, default)
        values[name] = list(value or []) if isinstance(default, list) else (bool(value) if isinstance(default, bool) else value)
    return values


def parse_parts(text, author=None, glossary=None):
    """Every context of one bubble (``parser.parse_bubble``); a single parse for older parser builds."""
    bubble = getattr(bubble_parser, 'parse_bubble', None)
    parts = list(bubble(text, author, glossary) or []) if bubble else []
    return parts or [parse_message(text, author, glossary)]


def segment_values(part, position=0, count=1):
    """(segment_index, segment_count, segment_text) of a part; segment_text is NULL for a single-context bubble."""
    index = getattr(part, 'segment_index', None)
    total = getattr(part, 'segment_count', None)
    index = position if index is None else int(index)
    total = max(count if total is None else int(total), index + 1, 1)
    text = getattr(part, 'segment_text', None) if total > 1 else None
    return index, total, (text.strip() or None) if isinstance(text, str) else None


def primary_part(parts):
    """The part that classifies the raw message: the first request/listing, else the first part."""
    return next((p for p in parts if p.classification in DOCUMENT_KINDS), parts[0])


def review_status(part):
    # Merged specs (several property categories in one part) are kept searchable
    # but held back from automatic matching, as are weak extractions.
    return 'review' if part.confidence < .7 or not part.categories or len(part.categories) > 2 else 'auto'


def extracted_values(parts):
    """raw_messages.extracted: the primary part's fields, plus every part when the bubble was split."""
    values = primary_part(parts).dict()
    if len(parts) > 1:
        values['parts'] = [part.dict() for part in parts]
    return values


def document_values(part, position=0, count=1):
    """xm.documents column values extracted from one part."""
    values = {}
    for name, default in PARSED_COLUMNS:
        value = getattr(part, name, default)
        values[name] = list(value or []) if isinstance(default, list) else (bool(value) if isinstance(default, bool) else value)
    values['transaction_type'] = values['transaction_type'] or 'unknown'
    values['normalized_text'] = values['normalized_text'] or ''
    values.update(extra_values(part))
    values['segment_index'], values['segment_count'], values['segment_text'] = segment_values(part, position, count)
    values.update(document_type=part.classification, extraction_confidence=part.confidence,
                  primary_category=values['categories'][0] if values['categories'] else None,
                  review_status=review_status(part))
    return values


def document_parts(parts):
    """[(part, column values)] for the parts that become documents (requests and listings)."""
    rows = [(part, document_values(part, position, len(parts)))
            for position, part in enumerate(parts) if part.classification in DOCUMENT_KINDS]
    if len({values['segment_index'] for _, values in rows}) < len(rows):
        # Positions must be unique per bubble; fall back to the order of the parts.
        for position, (part, values) in enumerate(rows):
            values['segment_index'] = position
            values['segment_count'] = max(values['segment_count'], len(rows))
    return rows


def qdrant_payload(parsed, doc_id, agent_name):
    # transaction_type is stored verbatim ('sale'|'rent'|'both'|'unknown'); vector
    # queries filter by category only, and the matcher applies the transaction gate.
    return {'company_id': workspace_id(), 'postgres_id': str(doc_id), 'document_type': parsed.classification,
            'agent_name': agent_name, 'transaction_type': parsed.transaction_type or 'unknown',
            'categories': parsed.categories, 'locations': parsed.locations, 'active': True}


def point_id_for(doc_id):
    return uuid.uuid5(uuid.NAMESPACE_URL, f'xm:{doc_id}')


def plan_parts(old_docs, wanted):
    """Pair each new part with the existing document it continues.

    The same part text keeps its document (also when its position moved); then the
    same position. Returns ([(old_doc or None, values)], leftover old docs)."""
    claimed, plan = set(), [None] * len(wanted)
    for passes in ('text', 'index'):
        for slot, values in enumerate(wanted):
            if plan[slot] is not None:
                continue
            for doc in sorted(old_docs, key=lambda d: (d['segment_index'] != values['segment_index'], not d['active'], d['segment_index'])):
                if doc['id'] in claimed:
                    continue
                if (passes == 'text' and doc['segment_text'] == values['segment_text']) or \
                   (passes == 'index' and doc['segment_index'] == values['segment_index']):
                    plan[slot] = doc
                    claimed.add(doc['id'])
                    break
    return list(zip(plan, wanted)), [doc for doc in old_docs if doc['id'] not in claimed]


def relinked(doc, values):
    """A part whose text and content, or whose kind, changed belongs to another entity (public ID).

    v4.10: entities are keyed by the normalised content (``content_key``). The same part text keeps its entity even
    when a parser change gives it another key (``entities.merge_entities`` re-keys or merges it, keeping the
    oldest ID); another part text with the same content key keeps it too."""
    if doc['document_type'] != values['document_type']:
        return True
    if doc['segment_text'] == values['segment_text']:
        return False
    return not (doc.get('content_key') and doc['content_key'] == values.get('content_key'))


def status_sources(old_docs, plan):
    """{index in plan: entity whose on hold/sold/deleted status the part continues}.

    When reprocessing splits a bubble differently (a v4.1 whole bubble into parts, a merged part
    into several), the parts get new entities. A part continues the entity of the document it
    reuses; a new part continues the single entity whose text this message no longer has."""
    kept = {doc['id'] for doc, values in plan if doc and not relinked(doc, values)}
    lost = {doc['entity_id'] for doc in old_docs
            if doc['active'] and doc['id'] not in kept and doc['entity_status'] not in (None, 'ready')}
    sources = {}
    for slot, (doc, values) in enumerate(plan):
        if doc and not relinked(doc, values):
            continue
        if doc and doc['active'] and doc['entity_status'] not in (None, 'ready'):
            sources[slot] = doc['entity_id']
        elif len(lost) == 1:
            sources[slot] = next(iter(lost))
    return sources


def carry_statuses(conn, carried):
    """Give the new entities of re-split parts the status of the entity they continue.

    ``carried`` is [(document id, source entity id)]. Only entities created by this run take it
    over: a part text that already had its own public ID keeps its own status."""
    from entities import assign_entities, cache_lock
    cache_lock(conn)
    assign_entities(conn, workspace_id())
    rows = conn.execute(
        '''WITH carried AS (
             SELECT DISTINCT ON (d.entity_id) d.entity_id, s.public_id AS source_id, s.status, s.status_note,
                    s.status_changed_at, s.status_changed_by
             FROM unnest(%s::uuid[], %s::uuid[]) AS c(document_id, source_entity)
             JOIN xm.documents d ON d.id = c.document_id
             JOIN xm.entities s ON s.entity_id = c.source_entity
             WHERE d.entity_id IS NOT NULL AND d.entity_id <> s.entity_id AND s.status <> 'ready'
             ORDER BY d.entity_id, s.status_changed_at DESC NULLS LAST)
           UPDATE xm.entities e SET status = c.status, status_note = c.status_note,
                  status_changed_at = c.status_changed_at, status_changed_by = c.status_changed_by
           FROM carried c
           WHERE e.entity_id = c.entity_id AND e.status = 'ready' AND e.created_at = now()
           RETURNING e.document_type, e.public_id, e.status, c.source_id''',
        ([str(doc_id) for doc_id, _ in carried], [str(entity) for _, entity in carried])).fetchall()
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO xm.audit_events(company_id, event_type, entity_type, entity_id, details) VALUES (%s,'entity_status',%s,%s,%s::jsonb)",
                [(workspace_id(), row['document_type'], row['public_id'],
                  json.dumps({'from': 'ready', 'to': row['status'], 'by': None, 'carried_from': row['source_id']})) for row in rows])
    return len(rows)


def reindex_documents(progress=None):
    """Reparse and re-embed every message inside the analysis window (older documents keep their rows untouched)."""
    progress = progress or job_progress.current()
    with connect() as conn:
        # Serialize maintenance with imports and other recomputations.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042026))")
        glossary = glossary_entries(conn)
        start = window_start(conn)
        rows = conn.execute("""SELECT r.id, r.import_id, r.agent_name, r.raw_text, r.author FROM xm.raw_messages r
          WHERE r.company_id=current_setting('xm.workspace_id') AND r.duplicate_of IS NULL""" + in_window_sql('r') + ' ORDER BY r.id',
                            (start,)).fetchall()
        progress.plan(parse=len(rows))
        progress.report('parse', 0, len(rows))
        existing = {}
        for doc in conn.execute("""SELECT d.id, d.raw_message_id, d.document_type, d.segment_index, d.segment_text, d.content_key, d.qdrant_point_id,
            d.active, d.entity_id, e.status AS entity_status
          FROM xm.documents d LEFT JOIN xm.entities e ON e.entity_id = d.entity_id
          WHERE d.company_id=current_setting('xm.workspace_id')""").fetchall():
            existing.setdefault(doc['raw_message_id'], []).append(doc)
        batch, inactive_points, carried = [], [], []
        parsed_cache = {}
        with conn.pipeline() as pipeline:
            for row_number, row in enumerate(rows, 1):
                cache_key = (row['raw_text'], row['author'])
                if cache_key not in parsed_cache:
                    parts = parse_parts(*cache_key, glossary)
                    parsed_cache[cache_key] = (parts, document_parts(parts), extracted_values(parts))
                parts, wanted, extracted = parsed_cache[cache_key]
                primary = primary_part(parts)
                conn.execute('UPDATE xm.raw_messages SET classification=%s, confidence=%s, extracted=%s::jsonb WHERE id=%s',
                             (primary.classification, primary.confidence, json.dumps(extracted), row['id']))
                plan, leftovers = plan_parts(existing.get(row['id'], []), [values for _, values in wanted])
                # Parts that disappeared stay as inactive rows; their position moves out of the way.
                used = {values['segment_index'] for _, values in wanted}
                spare = max([*used, *(d['segment_index'] for d in existing.get(row['id'], []))], default=-1) + 1
                parked = {}
                for doc in leftovers:
                    if doc['segment_index'] in used:
                        parked[doc['id']], spare = spare, spare + 1
                moving = [doc['id'] for doc, values in plan if doc and doc['segment_index'] != values['segment_index']] + list(parked)
                if moving:
                    # Unique (raw_message_id, segment_index): step aside first, then take the final positions.
                    conn.execute('UPDATE xm.documents SET segment_index=-1-segment_index WHERE id=ANY(%s)', (moving,))
                for doc in leftovers:
                    if doc['active'] or doc['id'] in parked:
                        conn.execute('UPDATE xm.documents SET active=false, segment_index=%s, updated_at=now() WHERE id=%s',
                                     (parked.get(doc['id'], doc['segment_index']), doc['id']))
                    if doc['active'] and doc['qdrant_point_id']:
                        inactive_points.append(str(doc['qdrant_point_id']))
                sources = status_sources(existing.get(row['id'], []), plan)
                for slot, ((part, _), (doc, values)) in enumerate(zip(wanted, plan)):
                    doc_id = doc['id'] if doc else uuid.uuid4()
                    point_id = (doc['qdrant_point_id'] if doc else None) or point_id_for(doc_id)
                    values = {**values, 'active': True, 'qdrant_point_id': point_id}
                    if slot in sources:
                        carried.append((doc_id, sources[slot]))
                    # Column names are fixed document fields, never user input.
                    if doc:
                        conn.execute('UPDATE xm.documents SET ' + ','.join(f'{key}=%s' for key in values) +
                                     (',entity_id=NULL' if relinked(doc, values) else '') + ',updated_at=now() WHERE id=%s',
                                     (*values.values(), doc_id))
                    else:
                        values.update(id=doc_id, raw_message_id=row['id'], import_id=row['import_id'], agent_name=row['agent_name'])
                        conn.execute('INSERT INTO xm.documents (' + ','.join(values) + ') VALUES (' + ','.join(['%s'] * len(values)) + ')',
                                     tuple(values.values()))
                    batch.append({'id': str(point_id), 'vector': embed(part.normalized_text), 'payload': qdrant_payload(part, doc_id, row['agent_name'])})
                if row_number % 2000 == 0: print(f'Reindex {row_number}/{len(rows)} messages', flush=True)
                if row_number % 500 == 0: progress.report('parse', row_number, len(rows))
                if len(batch) >= 128:
                    pipeline.sync()
                    upsert(batch)
                    batch = []
        progress.report('parse', len(rows), len(rows), force=True)
        progress.report('index', 0, len(batch) + len(inactive_points), force=True)
        if batch:
            upsert(batch)
        if carried:
            carry_statuses(conn, carried)
        for offset in range(0, len(inactive_points), 256):
            try:  # advisory only: matching reads the active flag from PostgreSQL
                _request('POST', f'/collections/{COLLECTION}/points/payload?wait=true',
                         {'payload': {'active': False}, 'points': inactive_points[offset:offset + 256]})
            except Exception as exc:
                print(f'Could not mark {len(inactive_points[offset:offset + 256])} vector points inactive: {exc}', flush=True)
            progress.report('index', len(batch) + offset + 256, len(batch) + len(inactive_points))
        conn.commit()
        return len(rows)
