"""History of buyer/listing pairs: which matches appeared, and when.

``log_matches`` runs inside the recompute transaction, right after the group
tables are rebuilt. Pairs are identified by their *entities* (unique texts), so
the log survives the full match rebuild that every recompute performs.
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from auth import current_user
from db import connect
from tenant import owner_id
from matching_scope import history_scope_filter
import ownership

router = APIRouter(prefix='/matches/recent')
WIB = ZoneInfo('Asia/Jakarta')
PAIR_FIELDS = '''g.entity_id, g.public_id, g.status AS entity_status, g.duplicate_count, g.last_seen_at,
  d.id, d.document_type, d.transaction_type, d.categories, d.locations, d.contact_name, d.contact_phone, d.contact_phones,
  d.land_area_min, d.land_area_max, d.building_area_min, d.building_area_max, d.price_min, d.price_max, d.price_basis,
  d.rent_price_min, d.rent_price_max, d.rent_price_basis, d.location_strict, d.strict_locations,
  d.segment_index, d.segment_count, d.segment_text,
  d.normalized_text, r.raw_text, r.sent_at, r.author, r.chat_name'''
# Stored pair statuses (v4.8): 'hot' = everything clearly matches (score ≥ 90, no item to check), price 80–100 % of the
# budget; 'warm' = everything proven, only price or land/building size slightly outside the Hot range; 'custom' = something
# is unclear (a requirement not stated or not proven in the listing, a vague request) or a nearby alternative.
TEMPERATURES = ('hot', 'warm', 'custom')
STATUS_COUNTS = '''count(*) FILTER (WHERE me.last_status = 'hot') AS hot,
  count(*) FILTER (WHERE me.last_status = 'warm') AS warm, count(*) FILTER (WHERE me.last_status = 'custom') AS custom '''


def status_clause(wanted, column='me.last_status'):
    """SQL filter for a set of pair statuses (values are whitelisted, never client text)."""
    chosen = [item for item in TEMPERATURES if item in wanted]
    if len(chosen) == len(TEMPERATURES):
        return ''
    if not chosen:
        return ' AND false'
    return f" AND {column} IN ({','.join(repr(item) for item in chosen)})"


def log_matches(conn, company_id, source, import_id=None, agent_name=None):
    """Record pairs that were never seen before; refresh the rest; retire pairs that vanished."""
    run = conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    rows = conn.execute(
        '''WITH pairs AS (
             SELECT bg.entity_id AS buyer_entity, pg.entity_id AS listing_entity, max(gm.score) AS score,
                    (array_agg(gm.status ORDER BY gm.score DESC, gm.status = 'custom'))[1] AS status
             FROM xm.group_matches gm
             JOIN xm.document_groups bg ON bg.group_id = gm.buyer_group_id
             JOIN xm.document_groups pg ON pg.group_id = gm.property_group_id
             WHERE gm.company_id = %(c)s AND bg.entity_id IS NOT NULL AND pg.entity_id IS NOT NULL
             GROUP BY 1, 2)
           INSERT INTO xm.match_events AS me(company_id, buyer_entity, listing_entity, first_score, last_score,
                  temperature, last_status, source, import_id, agent_name, found_at, hot_at, last_seen_at, active)
           SELECT %(c)s, p.buyer_entity, p.listing_entity, p.score, p.score,
                  p.status, p.status, %(source)s, %(import)s, %(agent)s,
                  %(run)s, CASE WHEN p.status = 'hot' THEN %(run)s END, %(run)s, true
           FROM pairs p
           ON CONFLICT (company_id, buyer_entity, listing_entity) DO UPDATE SET
                  last_score = EXCLUDED.last_score, last_status = EXCLUDED.last_status,
                  last_seen_at = EXCLUDED.last_seen_at, active = true,
                  hot_at = COALESCE(me.hot_at, EXCLUDED.hot_at)
           RETURNING (xmax = 0) AS inserted, me.temperature''',
        {'c': company_id, 'source': source, 'import': import_id, 'agent': agent_name, 'run': run}).fetchall()
    retired = conn.execute(
        'UPDATE xm.match_events SET active = false WHERE company_id = %s AND active AND last_seen_at < %s',
        (company_id, run)).rowcount
    created = [row for row in rows if row['inserted']]
    result = {'new': len(created), 'hot': sum(1 for row in created if row['temperature'] == 'hot'),
              'warm': sum(1 for row in created if row['temperature'] == 'warm'),
              'custom': sum(1 for row in created if row['temperature'] == 'custom'),
              'pairs': len(rows), 'retired': retired, 'source': source}
    conn.execute(
        "INSERT INTO xm.audit_events(company_id, event_type, entity_type, entity_id, details) VALUES (%s,'match_log','import',%s,%s::jsonb)",
        (company_id, str(import_id) if import_id else None, json.dumps(result)))
    return result


def pair_explanations(conn, pairs):
    """Current reasons for (buyer_entity, listing_entity) pairs, from the same group match log_matches keeps."""
    pairs = list(dict.fromkeys((str(buyer), str(listing)) for buyer, listing in pairs))
    if not pairs:
        return {}
    rows = conn.execute(
        '''SELECT DISTINCT ON (p.buyer_entity, p.listing_entity) p.buyer_entity, p.listing_entity, m.explanation
           FROM unnest(%s::uuid[], %s::uuid[]) AS p(buyer_entity, listing_entity)
           JOIN xm.document_groups bg ON bg.entity_id = p.buyer_entity AND bg.company_id = current_setting('xm.workspace_id')
           JOIN xm.group_matches gm ON gm.buyer_group_id = bg.group_id AND gm.company_id = bg.company_id
           JOIN xm.document_groups pg ON pg.group_id = gm.property_group_id AND pg.entity_id = p.listing_entity
           JOIN xm.matches m ON m.id = gm.match_id
           ORDER BY p.buyer_entity, p.listing_entity, gm.score DESC, gm.status = 'custom' ''',
        ([buyer for buyer, _ in pairs], [listing for _, listing in pairs])).fetchall()
    return {(str(row['buyer_entity']), str(row['listing_entity'])): row['explanation'] or [] for row in rows}


def tier_sql(conn):
    """``(ctes, joins, rank, params)`` for logged pairs in a CTE ``ev`` (``source_entity``, ``target_entity``):
    ``ctes`` adds ``src`` / ``tgt``, each entity's representative posting (the row ``people`` and the export show),
    and ``cs``, the senders of every copy of a target posted more than once; ``joins`` follows
    ``FROM ev LEFT JOIN src ... LEFT JOIN tgt ...``; ``rank`` is the ownership rank of the pair. Any copy of the
    target sent by the source's sender makes it own, as on the Match page. ``params`` belong to ``rank``."""
    rank, params, sender_join = ownership.rank_sql(ownership.load_rules(conn), 'src', 'tgt', copies='cs')
    copies = ownership.copy_senders_sql('tgt.entity_id', f'''FROM tgt CROSS JOIN LATERAL (
            SELECT CASE WHEN EXISTS (SELECT 1 FROM src WHERE NOT src.own_has_phone)
                          OR NOT {ownership.target_has_phone_sql('cd')} THEN copy_raw.author END AS author,
                   cd.contact_phones || cd.contact_phone AS phones
            FROM xm.documents cd JOIN xm.raw_messages copy_raw ON copy_raw.id = cd.raw_message_id
            WHERE cd.entity_id = tgt.entity_id AND cd.active AND cd.company_id = current_setting('xm.workspace_id')
            OFFSET 0) copy_raw
          WHERE tgt.copies > 1''', phones='copy_raw.phones')
    ctes = f''', src AS MATERIALIZED (
          SELECT DISTINCT ON (g.entity_id) g.entity_id, {ownership.source_sql('d', 'r')}
          FROM xm.document_groups g JOIN xm.documents d ON d.id = g.group_id JOIN xm.raw_messages r ON r.id = d.raw_message_id
          WHERE g.company_id = current_setting('xm.workspace_id') AND g.entity_id IN (SELECT source_entity FROM ev)
          ORDER BY g.entity_id, g.group_id
        ), tgt AS MATERIALIZED (
          SELECT DISTINCT ON (g.entity_id) g.entity_id, d.id, d.contact_phone, d.contact_phones, d.normalized_text,
                 r.raw_text, r.author, sum(g.duplicate_count) OVER (PARTITION BY g.entity_id) AS copies
          FROM xm.document_groups g JOIN xm.documents d ON d.id = g.group_id JOIN xm.raw_messages r ON r.id = d.raw_message_id
          WHERE g.company_id = current_setting('xm.workspace_id') AND g.entity_id IN (SELECT target_entity FROM ev)
          ORDER BY g.entity_id, g.group_id
        ), cs AS MATERIALIZED ({copies})'''
    return ctes, f'LEFT JOIN cs ON cs.key = tgt.entity_id {sender_join}', rank, params


def rank_pairs(conn, pairs):
    """Logged ``(source, target)`` rows (with ``entity_id``; target ``status`` and ``score``) in the Recent order:
    each source stays where it first appears, its targets go own -> team -> company -> other, then hot, warm,
    custom and score; ties keep the given order (latest find first). Sets ``ownership_tier`` on every target."""
    if not pairs:
        return []
    ctes, joins, rank, params = tier_sql(conn)
    keys = list(dict.fromkeys((str(source['entity_id']), str(target['entity_id'])) for source, target in pairs))
    rows = conn.execute(
        '''WITH ev AS (SELECT * FROM unnest(%s::uuid[], %s::uuid[]) AS ev(source_entity, target_entity))''' + ctes +
        f''' SELECT ev.source_entity, ev.target_entity, {rank} AS {ownership.RANK_COLUMN}
            FROM ev LEFT JOIN src ON src.entity_id = ev.source_entity LEFT JOIN tgt ON tgt.entity_id = ev.target_entity {joins}''',
        [[source for source, _ in keys], [target for _, target in keys], *params]).fetchall()
    tiers = {(str(row['source_entity']), str(row['target_entity'])): row for row in ownership.label(rows)}
    order = {}
    for source, target in pairs:
        order.setdefault(str(source['entity_id']), len(order))
        row = tiers.get((str(source['entity_id']), str(target['entity_id'])))
        target[ownership.TIER_FIELD] = row[ownership.TIER_FIELD] if row else 'other'
    return sorted(pairs, key=lambda pair: (order[str(pair[0]['entity_id'])], ownership.tier_rank(pair[1][ownership.TIER_FIELD]),
                                           ownership.status_rank(pair[1].get('status')), -float(pair[1].get('score') or 0)))


def wib_bounds(date_from: str, date_to: str):
    """Inclusive Jakarta calendar days -> [start, end) timestamps."""
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to or date_from)
    except ValueError:
        raise HTTPException(400, 'Invalid date.')
    if start > end:
        raise HTTPException(400, 'The start date cannot be after the end date.')
    if (end - start).days > 366:
        raise HTTPException(400, 'The date range can be at most 366 days.')
    return (datetime.combine(start, time.min, WIB), datetime.combine(end + timedelta(days=1), time.min, WIB))


def _filters(include_inactive: bool):
    allowed = ['ready', 'on_hold', 'sold'] if include_inactive else ['ready']
    return allowed


@router.get('')
def recent_matches(direction: Literal['buyer', 'property'] = 'buyer', date_from: str = '', date_to: str = '',
                   temps: str = 'hot,warm,custom', include_inactive: bool = False, limit: int = 30, offset: int = 0,
                   per_source: int = 20, source_entity: UUID | None = None, match_offset: int = 0):
    """New matches grouped by the buyer (``buyer``) or the listing (``property``) they belong to."""
    today = datetime.now(WIB).date().isoformat()
    start, end = wib_bounds(date_from or today, date_to or date_from or today)
    wanted = {item for item in temps.split(',') if item in TEMPERATURES}
    source_col, target_col = ('buyer_entity', 'listing_entity') if direction == 'buyer' else ('listing_entity', 'buyer_entity')
    allowed = _filters(include_inactive)
    limit = min(max(limit, 1), 100)
    per_source = min(max(per_source, 0), 50)
    base = f'''FROM xm.match_events me
       JOIN xm.entities se ON se.entity_id = me.{source_col} JOIN xm.entities te ON te.entity_id = me.{target_col}
       WHERE me.company_id = current_setting('xm.workspace_id') AND me.source = 'import' AND me.active
         AND me.found_at >= %s AND me.found_at < %s AND se.status = ANY(%s) AND te.status = ANY(%s)'''
    base_params = [start, end, allowed, allowed]
    temp_sql = status_clause(wanted)
    with connect() as conn:
        scope_sql, scope_params = history_scope_filter(conn, direction)
        base += scope_sql
        base_params += scope_params
        if source_entity:
            base += f' AND me.{source_col}=%s'
            base_params.append(source_entity)
        totals = conn.execute(
            'SELECT count(*) AS pairs, ' + STATUS_COUNTS + base, base_params).fetchone()
        page = conn.execute(
            f'''SELECT me.{source_col} AS entity_id, max(me.found_at) AS latest, count(*) AS pairs,
               count(*) FILTER (WHERE me.last_status='hot') AS hot_count,
               count(*) FILTER (WHERE me.last_status='warm') AS warm_count,
               count(*) FILTER (WHERE me.last_status='custom') AS custom_count ''' + base + temp_sql +
            f' GROUP BY me.{source_col} ORDER BY max(me.found_at) DESC, me.{source_col} LIMIT %s OFFSET %s',
            base_params + [limit + 1, max(0, offset)]).fetchall()
        has_more = len(page) > limit
        page = page[:limit]
        source_ids = [str(row['entity_id']) for row in page]
        events = []
        if source_ids and per_source:
            # Per source: the owner's own stock first, then the sales watchlist, company-keyword messages and
            # everyone else (ownership.py), each hot, warm, custom, by score and then the latest find. Both sides
            # are read from their group's representative posting, the same row ``people`` shows.
            ctes, joins, rank, rank_params = tier_sql(conn)
            events = ownership.label(conn.execute(
                f'''WITH ev AS (
                      SELECT me.id AS event_id, me.{source_col} AS source_entity, me.{target_col} AS target_entity, me.found_at,
                             me.hot_at, me.import_id, me.agent_name, me.first_score, me.last_score, me.last_status ''' + base + temp_sql +
                f''' AND me.{source_col} = ANY(%s::uuid[])
                    )''' + ctes + f'''
                    SELECT ev.*, {rank} AS {ownership.RANK_COLUMN}
                    FROM ev LEFT JOIN src ON src.entity_id = ev.source_entity
                    LEFT JOIN tgt ON tgt.entity_id = ev.target_entity {joins}
                    ORDER BY {ownership.RANK_COLUMN}, {ownership.status_rank_sql('ev.last_status')}, ev.last_score DESC,
                             ev.found_at DESC, ev.event_id''',
                base_params + [source_ids] + rank_params).fetchall())
        by_source = {}
        seen = {}
        for event in events:
            key = str(event['source_entity'])
            seen[key] = seen.get(key, 0) + 1
            bucket = by_source.setdefault(key, [])
            if seen[key] > max(0, match_offset) and len(bucket) < per_source:
                bucket.append(event)
        entity_ids = list(source_ids) + [str(event['target_entity']) for bucket in by_source.values() for event in bucket]
        people = {}
        if entity_ids:
            for row in conn.execute(
                f'''SELECT {PAIR_FIELDS} FROM xm.document_groups g
                    JOIN xm.documents d ON d.id = g.group_id JOIN xm.raw_messages r ON r.id = d.raw_message_id
                    WHERE g.company_id = current_setting('xm.workspace_id') AND g.entity_id = ANY(%s::uuid[])''',
                (list(dict.fromkeys(entity_ids)),)).fetchall():
                people[str(row['entity_id'])] = row
        # A pair's reasons ("Same cluster: …", "Location not confirmed — …") belong to the pair, not to either side.
        reasons = pair_explanations(conn, [
            (event['source_entity'], event['target_entity']) if direction == 'buyer' else (event['target_entity'], event['source_entity'])
            for bucket in by_source.values() for event in bucket])
    groups = []
    for row in page:
        key = str(row['entity_id'])
        if key not in people:
            continue
        matches = []
        for event in by_source.get(key, []):
            target = people.get(str(event['target_entity']))
            if not target:
                continue
            score = float(event['last_score'])
            status = event['last_status']
            pair = (key, str(event['target_entity'])) if direction == 'buyer' else (str(event['target_entity']), key)
            tier = event[ownership.TIER_FIELD]
            matches.append({'event_id': event['event_id'], 'found_at': event['found_at'], 'score': score,
                            'temperature': status, 'import_id': event['import_id'],
                            'agent_name': event['agent_name'],
                            # Hot since a later run than the one that found it (a Warm pair may score 80+).
                            'upgraded_to_hot': bool(event['hot_at'] and event['hot_at'] > event['found_at'] and status == 'hot'),
                            ownership.TIER_FIELD: tier,
                            # Like a recommendation row, the target carries the pair's reasons and ownership tier.
                            'target': {**target, 'explanation': reasons.get(pair, []), ownership.TIER_FIELD: tier}})
        groups.append({'source': people[key], 'latest_found_at': row['latest'], 'pair_count': row['pairs'],
                       'hot_count': row['hot_count'], 'warm_count': row['warm_count'], 'custom_count': row['custom_count'],
                       'matches': matches})
    return {'direction': direction, 'date_from': start.date().isoformat(), 'date_to': (end - timedelta(days=1)).date().isoformat(),
            'totals': totals, 'groups': groups, 'has_more': has_more}


@router.get('/days')
def recent_days(date_from: str, date_to: str, include_inactive: bool = False,
                direction: Literal['buyer', 'property'] | None = None):
    """Per-day counts for the calendar: how many new matches were found on each Jakarta day."""
    start, end = wib_bounds(date_from, date_to)
    allowed = _filters(include_inactive)
    with connect() as conn:
        scope_sql, scope_params = history_scope_filter(conn, direction)
        rows = conn.execute(
            '''SELECT (me.found_at AT TIME ZONE 'Asia/Jakarta')::date AS day, count(*) AS total, ''' + STATUS_COUNTS + '''
               FROM xm.match_events me JOIN xm.entities se ON se.entity_id = me.buyer_entity
               JOIN xm.entities te ON te.entity_id = me.listing_entity
               WHERE me.company_id = current_setting('xm.workspace_id') AND me.source = 'import' AND me.active
                 AND me.found_at >= %s AND me.found_at < %s AND se.status = ANY(%s) AND te.status = ANY(%s)
               ''' + scope_sql + ' GROUP BY 1 ORDER BY 1', [start, end, allowed, allowed, *scope_params]).fetchall()
        latest = conn.execute(
            '''SELECT max(me.found_at AT TIME ZONE 'Asia/Jakarta')::date AS day FROM xm.match_events me
               JOIN xm.entities se ON se.entity_id=me.buyer_entity JOIN xm.entities te ON te.entity_id=me.listing_entity
               WHERE me.company_id = current_setting('xm.workspace_id') AND me.source = 'import' AND me.active
                 AND se.status=ANY(%s) AND te.status=ANY(%s)''' +
            scope_sql, [allowed, allowed, *scope_params]).fetchone()
    return {'days': {str(row['day']): {'total': row['total'], 'hot': row['hot'], 'warm': row['warm'], 'custom': row['custom']}
                     for row in rows},
            'latest_date': str(latest['day']) if latest and latest['day'] else None}


def _seen_at(conn, user_key):
    row = conn.execute('SELECT preferences FROM xm.user_preferences WHERE user_id = %s', (user_key,)).fetchone()
    value = (row['preferences'] if row else {}).get('recent_seen_at')
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get('/summary')
def recent_summary(request: Request, direction: Literal['buyer', 'property'] | None = None):
    """Badge for the navigation, plus what the latest upload produced."""
    user = current_user(request)
    user_key = owner_id() or user['id']
    with connect() as conn:
        scope_sql, scope_params = history_scope_filter(conn, direction)
        since = _seen_at(conn, user_key) or (datetime.now(WIB) - timedelta(days=7))
        unseen = conn.execute(
            '''SELECT count(*) AS total, count(*) FILTER (WHERE me.last_status = 'hot') AS hot,
                      count(*) FILTER (WHERE me.last_status = 'custom') AS custom
               FROM xm.match_events me JOIN xm.entities se ON se.entity_id = me.buyer_entity
               JOIN xm.entities te ON te.entity_id = me.listing_entity
               WHERE me.company_id = current_setting('xm.workspace_id') AND me.source = 'import' AND me.active
                 AND me.found_at > %s AND se.status = 'ready' AND te.status = 'ready' ''' + scope_sql,
            [since, *scope_params]).fetchone()
        last = conn.execute(
            '''SELECT me.import_id, max(me.found_at) AS found_at, count(*) AS total, ''' + STATUS_COUNTS + ''',
                      i.agent_name, i.file_name
               FROM xm.match_events me JOIN xm.imports i ON i.id = me.import_id
               WHERE me.company_id = current_setting('xm.workspace_id') AND me.source = 'import'
               ''' + scope_sql +
            ' GROUP BY me.import_id, i.agent_name, i.file_name ORDER BY max(me.found_at) DESC LIMIT 1', scope_params).fetchone()
    return {'unseen': unseen['total'], 'unseen_hot': unseen['hot'], 'unseen_custom': unseen['custom'], 'since': since,
            'last_import': last}


@router.post('/seen', status_code=204)
def mark_seen(request: Request):
    user = current_user(request)
    stamp = json.dumps({'recent_seen_at': datetime.now(timezone.utc).isoformat()})
    with connect() as conn:
        conn.execute(
            '''INSERT INTO xm.user_preferences(user_id, preferences) VALUES (%s, %s::jsonb)
               ON CONFLICT (user_id) DO UPDATE SET preferences = xm.user_preferences.preferences || excluded.preferences,
                 updated_at = now()''', (owner_id() or user['id'], stamp))
        conn.commit()
