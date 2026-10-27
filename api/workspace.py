import json
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from db import connect
from parser import CATEGORY_LABELS, CATEGORY_PATTERNS, normalize_phone
from locations import ROOT_PLACES
from reindex import glossary_values
from auth import current_user
from company import company_row, effective_terms, personal_terms
from entities import STATUSES, group_key, group_key_sql
from tenant import owner_id, workspace_id
from search_terms import normalize_terms, search_filter
from periods import MatchingPeriods, date_filter, matching_windows
from matching_scope import document_scope_filter, mark_reprocess_pending, queue_reprocess, reprocess_status
from runtime_cache import invalidate_stats
import ownership

router = APIRouter()

Pct = Field(default=None, ge=0, le=100)
WEIGHT_FIELDS = ('location_weight_pct', 'land_weight_pct', 'building_weight_pct', 'price_weight_pct',
                 'semantic_weight_pct', 'data_quality_weight_pct')
# v4.3 bands: Hot = price hot_price_floor_pct–100 % of a single budget (a range [a, b] as written) and sizes
# inside the request; Warm = the Hot range widened by the "down" / "up" allowance per factor (price, land,
# building); nearby clusters within location_radius_km (0-4) are Custom alternatives.
SETTING_DEFAULTS = {'hot_price_floor_pct': 80, 'price_down_pct': 15, 'price_up_pct': 15, 'land_down_pct': 15,
                    'land_up_pct': 15, 'building_down_pct': 15, 'building_up_pct': 15, 'location_radius_km': 4,
                    'location_weight_pct': 35, 'land_weight_pct': 20, 'building_weight_pct': 15, 'price_weight_pct': 20,
                    'semantic_weight_pct': 5, 'data_quality_weight_pct': 5}


class Settings(BaseModel):
    """Matching settings. Every field is optional: what is left out keeps its saved value.

    ``hot_price_floor_pct`` (50–100) and the Warm allowances (``*_down_pct`` / ``*_up_pct``) are
    authoritative; the older single tolerances are still accepted from older clients and are mirrored on save."""
    hot_price_floor_pct: float | None = Field(default=None, ge=50, le=100)
    price_down_pct: float | None = Pct
    price_up_pct: float | None = Pct
    land_down_pct: float | None = Pct
    land_up_pct: float | None = Pct
    building_down_pct: float | None = Pct
    building_up_pct: float | None = Pct
    location_radius_km: float | None = Field(default=None, ge=0, le=4)
    land_tolerance_pct: float | None = Pct
    building_tolerance_pct: float | None = Pct
    price_tolerance_pct: float | None = Pct
    price_floor_pct: float | None = Pct
    location_weight_pct: float | None = Pct
    land_weight_pct: float | None = Pct
    building_weight_pct: float | None = Pct
    price_weight_pct: float | None = Pct
    semantic_weight_pct: float | None = Pct
    data_quality_weight_pct: float | None = Pct
    # Service areas per unit category (v5.0): {"house": ["surabaya"], "warehouse": ["surabaya", "gresik", "sidoarjo"]}.
    # A category left out or empty has no restriction. None = keep the saved value.
    category_areas: dict[str, list[str]] | None = None
    # Analysis window (v5.1): how many calendar months back messages are analysed and matched (1-24, default 4).
    analysis_window_months: int | None = Field(default=None, ge=1, le=24)


ANALYSIS_WINDOW_DEFAULT = 4


# Cities a company can limit a category to (the root places of the built-in hierarchy around Surabaya first).
SERVICE_CITIES = ('surabaya', 'sidoarjo', 'gresik', 'mojokerto', 'pasuruan', 'malang', 'lamongan', 'bangkalan', 'jombang', 'tuban', 'batu')


def clean_category_areas(value):
    """Validated copy: known categories only, known cities only, no duplicates, empty lists dropped."""
    if value is None:
        return None
    cleaned = {}
    for category, cities in value.items():
        if category not in CATEGORY_LABELS:
            raise HTTPException(400, f'Unknown unit category: {category}')
        kept = []
        for city in cities or []:
            city = (city or '').strip().lower()
            if city not in ROOT_PLACES:
                raise HTTPException(400, f'Unknown city: {city}')
            if city not in kept:
                kept.append(city)
        if kept:
            cleaned[category] = kept
    return cleaned


def _number(value, default):
    return float(default if value is None else value)


# The v4.1 single tolerances, still accepted from older clients and mirrored on save:
# legacy field -> (new fields it sets, new value from the legacy value, legacy value from the new ones).
# price_floor_pct stays the echo 100 − price_down_pct (matching_rules.tolerances reads it the same way when
# price_down_pct is missing); hot_price_floor_pct has no legacy twin.
LEGACY_TOLERANCES = {
    'price_floor_pct': (('price_down_pct',), lambda old: 100 - old, lambda v: 100 - v['price_down_pct']),
    'price_tolerance_pct': (('price_up_pct',), lambda old: old, lambda v: v['price_up_pct']),
    'land_tolerance_pct': (('land_down_pct', 'land_up_pct'), lambda old: old, lambda v: max(v['land_down_pct'], v['land_up_pct'])),
    'building_tolerance_pct': (('building_down_pct', 'building_up_pct'), lambda old: old,
                               lambda v: max(v['building_down_pct'], v['building_up_pct'])),
}


def merge_settings(before, payload: Settings):
    """Saved row + submitted fields -> every stored value (new tolerances, mirrored old columns, weights).

    Fields the payload carries decide on their own (the saved row only fills fields left out), so
    sending the same payload twice stores the same values."""
    sent = payload.model_dump(exclude_none=True)
    saved = {key: _number(before.get(key), default) for key, default in SETTING_DEFAULTS.items()}
    values = {key: float(sent[key]) if key in sent else saved[key] for key in SETTING_DEFAULTS}
    kept = {}
    for old, (fields, to_new, mirror) in LEGACY_TOLERANCES.items():
        if old not in sent:
            continue
        # The v4.3 migration (and new rows) set the Warm allowances without their old mirrors, so a row can
        # hold a stale single tolerance. Sending that saved value back is an echo, not an edit; it stays
        # as saved so the same payload sent again is still an echo.
        stored = before.get(old)
        if stored is not None and abs(float(stored) - mirror(saved)) > 1e-9 and abs(sent[old] - float(stored)) <= 1e-9:
            kept[old] = float(stored)
            continue
        # A single tolerance equal to the mirror of the (sent or saved) new values is an echo, not an edit.
        if abs(sent[old] - mirror(values)) <= 1e-9:
            continue
        # Edited by an older client: it sets the new fields the payload left out, or all of them when
        # the payload carries them all (a whole GET /settings body sent back with one tolerance changed).
        for key in [key for key in fields if key not in sent] or fields:
            values[key] = float(to_new(sent[old]))
    values.update({old: kept.get(old, float(mirror(values))) for old, (_, _, mirror) in LEGACY_TOLERANCES.items()})
    if abs(sum(values[key] for key in WEIGHT_FIELDS) - 100) > 0.001:
        raise HTTPException(400, "Matching weights must add up to 100%.")
    return values


# Same ceiling as PUT /glossary/entries: the legacy GET can return every structured entry.
MAX_GLOSSARY = 2000


class Glossary(BaseModel):
    entries: dict[str, str]

@router.put('/settings')
def save_settings(payload: Settings):
    with connect() as conn:
        before = conn.execute("SELECT * FROM xm.match_settings WHERE company_id=current_setting('xm.workspace_id') FOR UPDATE").fetchone() or {}
        values = merge_settings(before, payload)
        # Column names come from the fixed SETTING_DEFAULTS / legacy lists, never from the client.
        conn.execute('UPDATE xm.match_settings SET ' + ', '.join(f'{key}=%s' for key in values) +
                     ", updated_at=now() WHERE company_id=current_setting('xm.workspace_id')", tuple(values.values()))
        # The mirrored single tolerances follow the new fields and never change results on their own.
        if any(before.get(key) is None or abs(float(before[key]) - values[key]) > 1e-9 for key in SETTING_DEFAULTS):
            mark_reprocess_pending(conn, 'Matching settings changed')
        areas = clean_category_areas(payload.category_areas)
        if areas is not None and areas != (before.get('category_areas') or {}):
            conn.execute("UPDATE xm.match_settings SET category_areas=%s::jsonb, updated_at=now() WHERE company_id=current_setting('xm.workspace_id')",
                         (json.dumps(areas),))
            mark_reprocess_pending(conn, 'Service areas changed')
        months = payload.analysis_window_months
        if months is not None and int(before.get('analysis_window_months') or ANALYSIS_WINDOW_DEFAULT) != int(months):
            conn.execute("UPDATE xm.match_settings SET analysis_window_months=%s, updated_at=now() WHERE company_id=current_setting('xm.workspace_id')",
                         (int(months),))
            mark_reprocess_pending(conn, 'Analysis window changed')
        conn.commit()
        return conn.execute("SELECT * FROM xm.match_settings WHERE company_id=current_setting('xm.workspace_id')").fetchone() or values

@router.get('/glossary')
def get_glossary():
    with connect() as conn:
        return glossary_values(conn)


class Preferences(BaseModel):
    direction: Literal['buyer', 'property'] = 'buyer'
    statuses: list[Literal['hot', 'warm', 'custom', 'unmatched']] = ['hot', 'warm', 'custom']


@router.get('/preferences')
def get_preferences(request: Request):
    user = current_user(request)
    with connect() as conn:
        row = conn.execute(
            'SELECT preferences FROM xm.user_preferences WHERE user_id=%s',
            (owner_id() or user['id'],),
        ).fetchone()
    return {**Preferences().model_dump(), **(row['preferences'] if row else {})}


@router.put('/preferences')
def save_preferences(payload: Preferences, request: Request):
    user = current_user(request)
    with connect() as conn:
        conn.execute(
            '''INSERT INTO xm.user_preferences(user_id, preferences) VALUES(%s,%s::jsonb)
               ON CONFLICT(user_id) DO UPDATE SET preferences=xm.user_preferences.preferences || excluded.preferences, updated_at=now()''',
            (owner_id() or user['id'], json.dumps(payload.model_dump())),
        )
        conn.commit()
    return payload

class SearchDefault(BaseModel):
    search: str = Field(default='', max_length=4020)
    terms: list[str] | None = Field(default=None, max_length=20)
    locked: bool | None = None
    # Explicitly use every message (no keyword filter). Never inferred from an empty list.
    clear: bool = False


def search_state(conn, role='admin', user_key=None):
    """Keywords as one person experiences them, plus what they may change."""
    company = company_row(conn)
    mine = personal_terms(conn, user_key) if user_key else None
    terms = effective_terms(role, company, mine)
    return {'search': '\n'.join(terms), 'terms': terms, 'company_terms': list(company['search_terms']),
            'personal_terms': mine, 'locked': company['search_locked'],
            'can_edit_company': role in ('admin', 'company_admin'),
            'can_edit_personal': role == 'user' and not company['search_locked']}


def resolve_search(conn, request, search):
    """A locked company makes every member search with the company keywords, whatever the client sends."""
    if request is None:
        return search
    user = current_user(request)
    if user and user['role'] == 'user':
        company = company_row(conn)
        if company['search_locked'] and company['matching_mode'] == 'company':
            return '\n'.join(company['search_terms'])
    return search


@router.get('/search-default')
def get_search_default(request: Request = None):
    user = current_user(request) if request is not None else None
    with connect() as conn:
        return search_state(conn, user['role'] if user else 'admin', (owner_id() or user['id']) if user else None)


@router.put('/search-default')
def save_search_default(payload: SearchDefault, request: Request = None):
    terms = [] if payload.clear else normalize_terms(payload.terms if payload.terms is not None else payload.search)
    if not terms and not payload.clear:
        raise HTTPException(400, 'Enter at least one search keyword.')
    user = current_user(request) if request is not None else None
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")
        previous = company_row(conn)
        if payload.locked is None:
            conn.execute("UPDATE xm.app_preferences SET search_terms=%s,updated_at=now() WHERE company_id=current_setting('xm.workspace_id')", (terms,))
        else:
            conn.execute("UPDATE xm.app_preferences SET search_terms=%s,search_locked=%s,updated_at=now() WHERE company_id=current_setting('xm.workspace_id')",
                         (terms, payload.locked))
        if previous['matching_mode'] == 'company' and {term.casefold() for term in previous['search_terms']} != {term.casefold() for term in terms}:
            mark_reprocess_pending(conn, 'Search keywords changed')
        conn.commit()
        state = search_state(conn, user['role'] if user else 'admin', (owner_id() or user['id']) if user else None)
    invalidate_stats(workspace_id())
    return state


class PersonalSearch(BaseModel):
    search: str = Field(default='', max_length=4020)
    terms: list[str] | None = Field(default=None, max_length=20)


@router.put('/search-default/personal')
def save_personal_search(payload: PersonalSearch, request: Request):
    user = current_user(request)
    if user['role'] != 'user':
        raise HTTPException(400, 'Company admins set the company keywords in the company settings.')
    terms = normalize_terms(payload.terms if payload.terms is not None else payload.search)
    if not terms:
        raise HTTPException(400, 'Enter at least one keyword.')
    with connect() as conn:
        if company_row(conn)['search_locked']:
            raise HTTPException(403, 'Keywords are locked by your company admin.')
        key = owner_id() or user['id']
        conn.execute(
            '''INSERT INTO xm.user_preferences(user_id, preferences) VALUES(%s,%s::jsonb)
               ON CONFLICT(user_id) DO UPDATE SET preferences=xm.user_preferences.preferences || excluded.preferences, updated_at=now()''',
            (key, json.dumps({'search_terms': terms})))
        conn.commit()
        return search_state(conn, user['role'], key)


@router.delete('/search-default/personal')
def reset_personal_search(request: Request):
    user = current_user(request)
    key = owner_id() or user['id']
    with connect() as conn:
        conn.execute("UPDATE xm.user_preferences SET preferences=preferences - 'search_terms', updated_at=now() WHERE user_id=%s", (key,))
        conn.commit()
        return search_state(conn, user['role'], key)


@router.put('/glossary')
def save_glossary(payload: Glossary):
    """Legacy {term: standard name} save. Terms that stay keep their kind, parent area and note
    (set through PUT /glossary/entries); only removed terms are deleted."""
    entries = {k.strip().lower():v.strip().lower() for k,v in payload.entries.items()}
    if len(entries) > MAX_GLOSSARY or any(not k or not v or len(k)>100 or len(v)>100 for k,v in entries.items()):
        raise HTTPException(400, f'Each term and its standard name are required (max. 100 characters, {MAX_GLOSSARY:,} terms).')
    with connect() as conn:
        # Same per-company settings lock as PUT /glossary/entries, so concurrent saves never interleave.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")
        before = glossary_values(conn)
        removed = [alias for alias in before if alias not in entries]
        if removed:
            conn.execute("DELETE FROM xm.glossary WHERE company_id=current_setting('xm.workspace_id') AND alias=ANY(%s)", (removed,))
        for k,v in entries.items():
            if before.get(k) == v:
                continue
            # A category term must name an asset type; renamed to anything else it becomes a plain rewrite again.
            conn.execute('''INSERT INTO xm.glossary AS g(alias,canonical) VALUES(%s,%s)
              ON CONFLICT (company_id,alias) DO UPDATE SET canonical=excluded.canonical,
                kind=CASE WHEN g.kind='category' AND NOT excluded.canonical=ANY(%s) THEN 'synonym' ELSE g.kind END''',
              (k, v, list(CATEGORY_PATTERNS)))
        if before != entries:
            mark_reprocess_pending(conn, 'Glossary updated')
        conn.commit()
    return entries

@router.get('/reprocess/status')
def get_reprocess_status():
    with connect() as conn:
        status = reprocess_status(conn)
        conn.commit()
    return status


class ReprocessRequest(BaseModel):
    """Optional: ``from`` extends the analysis window back to that day (YYYY-MM-DD) before the run."""
    from_date: str | None = Field(default=None, alias='from', max_length=10)
    model_config = {'populate_by_name': True}


@router.post('/reprocess', status_code=202)
def reprocess(payload: ReprocessRequest | None = None):
    """Bring matches up to date with every saved setting (glossary, locations, keywords, watchlist, weights)."""
    from datetime import date
    start = None
    if payload and payload.from_date:
        try:
            start = date.fromisoformat(payload.from_date)
        except ValueError:
            raise HTTPException(400, 'Invalid date.')
    with connect() as conn:
        queue_reprocess(conn, analysis_from=start)
        conn.commit()
        status = reprocess_status(conn)
        conn.commit()
    invalidate_stats(workspace_id())
    return status


@router.post('/index/recompute', status_code=202)
def rebuild_index():
    """Older alias of POST /reprocess that answers with the job itself."""
    with connect() as conn:
        job = queue_reprocess(conn)
        conn.commit()
        return job or reprocess_status(conn)['job']

@router.get('/index/status')
def index_status():
    with connect() as conn:
        return conn.execute("SELECT * FROM xm.maintenance_jobs WHERE company_id=current_setting('xm.workspace_id') ORDER BY created_at DESC LIMIT 1").fetchone()

@router.get('/workspace/dates')
def workspace_dates(direction: Literal['buyer','property']='buyer', date_from: str='', date_to: str=''):
    from datetime import date
    try:
        start,end=date.fromisoformat(date_from),date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(400,'Invalid date.')
    if not 0 < (end-start).days <= 93:
        raise HTTPException(400,'The calendar range can be at most 93 days.')
    kind='buyer_request' if direction=='buyer' else 'property_listing'
    with connect() as conn:
        import workspace_cache
        from matching_scope import analysis_window_status
        window = analysis_window_status(conn)
        scope_sql, scope_params = document_scope_filter(conn)
        if workspace_cache.ready(conn):
            latest=conn.execute('''SELECT max(r.sent_at)::date latest_date
              FROM xm.document_group_members gm JOIN xm.document_groups g ON g.group_id=gm.group_id
              JOIN xm.documents d ON d.id=gm.document_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
              WHERE g.company_id=current_setting('xm.workspace_id') AND d.active
                AND g.document_type=%s AND g.status='ready' ''' + scope_sql, [kind, *scope_params]).fetchone()
            rows=conn.execute('''SELECT r.sent_at::date posted_day,count(DISTINCT gm.group_id) count
              FROM xm.document_group_members gm JOIN xm.document_groups g ON g.group_id=gm.group_id
              JOIN xm.documents d ON d.id=gm.document_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
              WHERE g.company_id=current_setting('xm.workspace_id') AND d.active AND g.document_type=%s AND g.status='ready' AND r.sent_at >= %s AND r.sent_at < %s
              ''' + scope_sql + ' GROUP BY r.sent_at::date',[kind,start,end,*scope_params]).fetchall()
            return {'counts':{str(row['posted_day'])[:10]:int(row['count']) for row in rows},
                    'latest_date':str(latest['latest_date'])[:10] if latest['latest_date'] else None, **window}
        latest=conn.execute("""SELECT max(r.sent_at)::date AS latest_date
          FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
          WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND d.document_type=%s""" + scope_sql,[kind,*scope_params]).fetchone()
        rows=conn.execute(f"""SELECT r.sent_at::date AS posted_day,count(DISTINCT {group_key_sql()}) AS count
          FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
          WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND d.document_type=%s
            AND r.sent_at >= %s AND r.sent_at < %s""" + scope_sql + ' GROUP BY r.sent_at::date',[kind,start,end,*scope_params]).fetchall()
    return {'counts': {str(row['posted_day'])[:10]:int(row['count']) for row in rows},
            'latest_date': str(latest['latest_date'])[:10] if latest['latest_date'] else None, **window}


def stock_statuses(value):
    """``ready,on_hold`` -> validated tuple; the default view only shows ready items."""
    chosen = [item for item in dict.fromkeys(v.strip() for v in value.split(',')) if item]
    if not chosen or any(item not in STATUSES for item in chosen):
        raise HTTPException(400, 'Unknown listing/buyer status.')
    return tuple(chosen)


def attach_entities(conn, rows):
    """Rows built without the group cache still need their public ID and status."""
    ids = list({str(r['entity_id']) for r in rows if r.get('entity_id')})
    if ids:
        found = {str(e['entity_id']): e for e in conn.execute('SELECT entity_id, public_id, status FROM xm.entities WHERE entity_id=ANY(%s::uuid[])', (ids,)).fetchall()}
        for r in rows:
            entity = found.get(str(r.get('entity_id')))
            if entity:
                r['public_id'], r['entity_status'] = entity['public_id'], entity['status']
    return rows


@router.get('/workspace')
def workspace(direction: Literal['buyer','property']='buyer', search: str='', phones: str='', statuses: str='hot,warm,custom,unmatched', date_from: str='', date_to: str='', offset: int=0, time_from: str='00:00', time_to: str='23:59', stock_status: str='ready', public_id: str='', group_by: str='', group_key: str | None=None, request: Request = None,
              buyer_date_from: str='', buyer_date_to: str='', listing_date_from: str='', listing_date_to: str=''):
    import workspace_cache
    (clause, date_params), (target_clause, target_params) = matching_windows(direction, date_from, date_to, time_from, time_to,
        buyer_date_from, buyer_date_to, listing_date_from, listing_date_to)
    chosen = stock_statuses(stock_status)
    if group_by and (direction != 'property' or group_by not in ('sender','phone')):
        raise HTTPException(400, 'Grouping is only available for property listings: sender or phone.')
    with connect() as conn:
        search = resolve_search(conn, request, search)
        if workspace_cache.ready(conn):
            return workspace_cache.sources(conn,direction,search,phones,statuses,clause,date_params,offset,chosen,public_id,group_by or None,group_key,
                                           target_clause, target_params)
        scope_sql, scope_params = document_scope_filter(conn)
    kind, relation = ('buyer_request','buyer_request_id') if direction=='buyer' else ('property_listing','property_listing_id')
    other = 'property_listing_id' if direction == 'buyer' else 'buyer_request_id'
    selected=set(statuses.split(','))
    all_statuses={'hot','warm','custom','unmatched'}.issubset(selected)
    # With every status included, paginate groups before counting their matches.
    # This avoids aggregating the entire match archive just to show 200 cards.
    page_cte = ''
    if all_statuses:
        page_cte = '''), page AS (
          SELECT * FROM ranked WHERE group_rank=1
          ORDER BY sent_at DESC NULLS LAST,id LIMIT 201 OFFSET %s'''
    # Copies are grouped by their content key (a part of a multi-context bubble is its own text).
    query = f"""WITH eligible AS (
       SELECT d.*,r.raw_text,r.chat_name,r.sent_at,{group_key_sql()} group_text
       FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
       WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND d.document_type=%s"""
    params=[kind]
    search_clause, search_params = search_filter(search)
    query += search_clause
    params += search_params
    query += scope_sql
    params += scope_params
    if phones.strip() and direction=='property':
        import re
        numbers=[normalize_phone(v) for v in re.split(r'[,;\n]+',phones) if v.strip()]
        if any(not n.startswith('628') or not 10<=len(n)<=15 for n in numbers):
            raise HTTPException(400,'Invalid phone number. Separate several numbers with a comma or a new line.')
        # Read all contact numbers in a signature, including second/alternate numbers.
        query += " AND (d.contact_phones && %s::text[] OR d.contact_phone = ANY(%s))"
        params.extend([numbers,numbers])
    query += clause
    params.extend(date_params)
    query += f"""), ranked AS (
       SELECT eligible.*, row_number() OVER(PARTITION BY group_text ORDER BY sent_at DESC NULLS LAST,id) group_rank,
              count(*) OVER(PARTITION BY group_text) duplicate_count
       FROM eligible
    {page_cte}
    ), pairs AS (
       SELECT e.group_text, {group_key_sql('t', 'tr')} target_text, max(m.score) score,
              (array_agg(m.status ORDER BY m.score DESC, m.status='custom'))[1] status
       FROM (SELECT DISTINCT group_text FROM {'page' if all_statuses else 'eligible'}) e
       JOIN xm.documents source ON source.company_id=current_setting('xm.workspace_id') AND source.active
           AND source.document_type='{kind}'
       JOIN xm.raw_messages sr ON sr.id=source.raw_message_id AND {group_key_sql('source', 'sr')}=e.group_text
       JOIN xm.matches m ON m.{relation}=source.id AND m.company_id=current_setting('xm.workspace_id')
       JOIN xm.documents mt ON mt.id=m.{other} AND mt.active AND mt.company_id=current_setting('xm.workspace_id')
       JOIN xm.raw_messages mtr ON mtr.id=mt.raw_message_id
       JOIN xm.documents t ON t.company_id=mt.company_id AND t.active AND t.document_type=mt.document_type
       JOIN xm.raw_messages tr ON tr.id=t.raw_message_id AND {group_key_sql('t', 'tr')}={group_key_sql('mt', 'mtr')}
       LEFT JOIN xm.entities te ON te.entity_id=t.entity_id
       WHERE m.score>=60 AND coalesce(te.status,'ready')='ready'
       {target_clause.replace('r.sent_at', 'tr.sent_at')} GROUP BY e.group_text,{group_key_sql('t', 'tr')}
    ), counts AS (
       SELECT group_text,count(*) match_count,count(*) FILTER(WHERE status='hot') hot_count,
              count(*) FILTER(WHERE status='warm') warm_count,count(*) FILTER(WHERE status='custom') custom_count
       FROM pairs GROUP BY group_text
    ) SELECT d.*,coalesce(c.match_count,0) match_count,coalesce(c.hot_count,0) hot_count,
             coalesce(c.warm_count,0) warm_count,coalesce(c.custom_count,0) custom_count
      FROM {'page' if all_statuses else 'ranked'} d LEFT JOIN counts c USING(group_text) WHERE group_rank=1 AND ("""
    filters=[]
    if 'hot' in selected: filters.append('coalesce(c.hot_count,0)>0')
    if 'warm' in selected: filters.append('coalesce(c.warm_count,0)>0')
    if 'custom' in selected: filters.append('coalesce(c.custom_count,0)>0')
    if 'unmatched' in selected: filters.append('coalesce(c.match_count,0)=0')
    query+=' OR '.join(filters or ['false'])+') ORDER BY d.sent_at DESC NULLS LAST,d.id'
    if not all_statuses:
        query+=' LIMIT 201 OFFSET %s'
    # The page CTE precedes pair aggregation, so its offset parameter comes first.
    if all_statuses:
        params.append(max(0, offset))
        params.extend(target_params)
    else:
        params.extend(target_params)
        params.append(max(0, offset))
    with connect() as conn:
        rows=attach_entities(conn,conn.execute(query,params).fetchall())
    for row in rows:
        row.pop('group_text', None)
    return {'rows':rows[:200],'has_more':len(rows)>200}


@router.get('/workspace/groups')
def workspace_groups(group_by: str='', search: str='', phones: str='', statuses: str='hot,warm,custom,unmatched', date_from: str='', date_to: str='', time_from: str='00:00', time_to: str='23:59', stock_status: str='ready', public_id: str='', group_search: str='', offset: int=0, request: Request = None,
                     buyer_date_from: str='', buyer_date_to: str='', listing_date_from: str='', listing_date_to: str=''):
    """Listings grouped by message sender or by the phone number written in the bubble."""
    import workspace_cache
    (clause, date_params), (target_clause, target_params) = matching_windows('property', date_from, date_to, time_from, time_to,
        buyer_date_from, buyer_date_to, listing_date_from, listing_date_to)
    chosen = stock_statuses(stock_status)
    with connect() as conn:
        mode = group_by or company_row(conn)['listing_group_by']
        if mode not in ('sender','phone'):
            raise HTTPException(400, 'Unknown grouping.')
        search = resolve_search(conn, request, search)
        if not workspace_cache.ready(conn):
            return {'group_by': mode, 'groups': [], 'has_more': False}
        return workspace_cache.group_summary(conn,'property',search,phones,statuses,clause,date_params,chosen,public_id,mode,group_search,offset=offset,
                                             target_clause=target_clause, target_params=target_params)

class Batch(MatchingPeriods):
    direction: Literal['buyer','property']='buyer'
    ids: list[uuid.UUID] = Field(max_length=50)
    target_status: list[Literal['ready','on_hold','sold']] = ['ready']

@router.post('/workspace/recommendations')
def recommendations(payload: Batch):
    relation, other, kind = ('buyer_request_id','property_listing_id','buyer_request') if payload.direction=='buyer' else ('property_listing_id','buyer_request_id','property_listing')
    ids=list(dict.fromkeys(payload.ids))
    (source_clause, source_params), (target_clause, target_params) = payload.windows(payload.direction)
    with connect() as conn:
        import workspace_cache
        if workspace_cache.ready(conn):
            return workspace_cache.recommendations(conn,payload.direction,ids,tuple(payload.target_status) or ('ready',), target_clause, target_params,
                                                   source_clause=source_clause, source_params=source_params)
        scope_clause, scope_params = document_scope_filter(conn)
        source_clause += scope_clause
        source_params = [*source_params, *scope_params]
        sources=conn.execute('''SELECT d.*,r.raw_text,r.chat_name,r.sent_at,r.author FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND d.id=ANY(%s) AND d.document_type=%s''' + source_clause,
                             [ids,kind,*source_params]).fetchall()
        ids = [source['id'] for source in sources]
        # Same order as the cached path: ownership tier (own, sales watchlist, company keyword, other), then
        # hot, warm, custom, score and the latest posting. Every copy of a text is ranked and group_identical
        # keeps each text's first (best) row, so a copy sent by the source's sender makes it own (cached: copies).
        tier_sql, tier_params, sender_join = ownership.rank_sql(ownership.load_rules(conn), 'src', 'd', 'r')
        # Resolve all copies (same part text) of a selected source; aggregate before rendering so
        # copies with different historic candidate edges do not lose matches.
        rows=conn.execute(f'''WITH src AS MATERIALIZED (
          SELECT sd.id,{ownership.source_sql('sd', 'sr')}
          FROM xm.documents sd JOIN xm.raw_messages sr ON sr.id=sd.raw_message_id WHERE sd.id=ANY(%s)
        ), source_copies AS (
          SELECT chosen.id source_id, copy.id copy_id
          FROM xm.documents chosen JOIN xm.raw_messages cr ON cr.id=chosen.raw_message_id
          JOIN xm.documents copy ON copy.active AND copy.company_id=chosen.company_id AND copy.document_type=chosen.document_type
          JOIN xm.raw_messages rr ON rr.id=copy.raw_message_id
             AND {group_key_sql('copy', 'rr')}={group_key_sql('chosen', 'cr')}
          WHERE chosen.company_id=current_setting('xm.workspace_id') AND chosen.active AND chosen.id=ANY(%s) AND chosen.document_type=%s
        ) SELECT m.id match_id,s.source_id,m.score,m.status,m.explanation,d.*,r.raw_text,r.chat_name,r.sent_at,r.author,
            {tier_sql} AS {ownership.RANK_COLUMN}
          FROM source_copies s JOIN xm.matches m ON m.{relation}=s.copy_id JOIN src ON src.id=s.source_id
          JOIN xm.documents matched ON matched.id=m.{other} AND matched.active AND matched.company_id=current_setting('xm.workspace_id')
          JOIN xm.raw_messages mr ON mr.id=matched.raw_message_id
          JOIN xm.documents d ON d.company_id=matched.company_id AND d.document_type=matched.document_type
          JOIN xm.raw_messages r ON r.id=d.raw_message_id
             AND {group_key_sql()}={group_key_sql('matched', 'mr')}
          LEFT JOIN xm.entities e ON e.entity_id=d.entity_id {sender_join}
          WHERE m.company_id=current_setting('xm.workspace_id') AND d.company_id=current_setting('xm.workspace_id') AND d.active AND m.score>=60
            AND coalesce(e.status,'ready')=ANY(%s){target_clause}
          ORDER BY {ownership.RANK_COLUMN},{ownership.status_rank_sql('m.status')},m.score DESC,r.sent_at DESC NULLS LAST,d.id''',
          [ids,ids,kind,*tier_params,list(payload.target_status) or ['ready'],*target_params]).fetchall()
        ownership.label(rows)
        attach_entities(conn,sources); attach_entities(conn,rows)
    return {'groups':[{'source':s,'recommendations':group_identical([r for r in rows if r['source_id']==s['id']])} for s in sources]}


def group_identical(rows):
    """One card per content key (``entities.group_key``; the part of a multi-context bubble is its own text)."""
    groups = {}
    for row in rows:
        key = group_key(row)
        if key not in groups:
            groups[key] = {**row, 'duplicate_count': 0, '_ids': set(), 'last_seen_at': row.get('sent_at')}
        group = groups[key]
        if row.get('sent_at') and (not group['last_seen_at'] or row['sent_at'] > group['last_seen_at']):
            group['last_seen_at'] = row['sent_at']
        group['_ids'].add(row['id'])
        group['duplicate_count'] = len(group['_ids'])
    return [{k: v for k, v in row.items() if k != '_ids'} for row in groups.values()]


class ExportPair(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID | None=None

class Export(MatchingPeriods):
    direction: Literal['buyer','property']='buyer'
    pairs: list[ExportPair] = Field(min_length=1,max_length=200)
    target_status: list[Literal['ready','on_hold','sold']] = ['ready']

@router.post('/export/pdf')
def export_pdf(payload: Export):
    from report import build_report
    source_order={}  # sources in the order they were first picked
    for p in payload.pairs: source_order.setdefault(p.source_id,len(source_order))
    groups=recommendations(Batch(**payload.model_dump(exclude={'pairs'}),ids=list(source_order)))['groups']
    selected=[]
    for p in payload.pairs:
        group=next((g for g in groups if g['source']['id']==p.source_id),None)
        if group is None: raise HTTPException(404,'The selected item was not found.')
        position=next((i for i,r in enumerate(group['recommendations']) if r['id']==p.target_id),None)
        if p.target_id and position is None: raise HTTPException(409,'Matches have changed. Reload and select again.')
        if not p.target_id and group['recommendations']: raise HTTPException(409,'This item now has matches. Reload the results.')
        target=group['recommendations'][position] if position is not None else None
        selected.append(((source_order[p.source_id],-1 if position is None else position),(group['source'],target)))
    # Pages follow the lists, not the click order: each source's pairs together, own stock first (tier, status,
    # score, latest posting, the order recommendations() returns).
    selected=[pair for _,pair in sorted(selected,key=lambda item:item[0])]
    with connect() as conn: company_name=company_row(conn)['company_name']
    return Response(build_report(selected,payload.direction,company_name=company_name),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="Property-Matching-Report.pdf"'})
