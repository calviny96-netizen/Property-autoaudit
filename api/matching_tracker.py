"""Company-scoped, durable pair-discovery history for the sales watchlist.

A period filters FIRST discovery, never the latest recomputation. Retired pairs
remain visible. Rates use unique entities, so repeated messages/pairs cannot
inflate coverage above 100%. Inventory means current non-deleted stock.
"""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException
from db import connect
from matchlog import wib_bounds
from parser import normalize_phone

router = APIRouter(prefix='/matching-tracker')


def population(direction, phone='', include_archived=False):
    kind = 'property_listing' if direction == 'property' else 'buyer_request'
    return '''WITH inventory AS MATERIALIZED (
      SELECT t.phone,t.label,e.entity_id,e.public_id,e.status AS entity_status,
        mode() WITHIN GROUP (ORDER BY nullif(d.contact_name,'')) AS contact_name
      FROM xm.tracked_sales t JOIN xm.documents d
        ON d.company_id=t.company_id AND (%s OR d.active) AND d.document_type=%s
        AND (d.contact_phones @> ARRAY[t.phone] OR d.contact_phone=t.phone)
      JOIN xm.entities e ON e.entity_id=d.entity_id AND e.company_id=t.company_id
      WHERE t.company_id=current_setting('xm.workspace_id') AND (%s OR e.status<>'deleted')
        AND (%s='' OR t.phone=%s)
      GROUP BY t.phone,t.label,e.entity_id,e.public_id,e.status)''', [include_archived, kind, include_archived, phone, phone]


def event_filter(date_from, date_to):
    if not date_from and not date_to:
        return '', []
    start, end = wib_bounds(date_from or date_to, date_to or date_from)
    return ' AND me.found_at >= %s AND me.found_at < %s', [start, end]


def rate(matched, total):
    return round(100 * matched / total, 1) if total else 0


@router.get('/sales')
def sales():
    """Names for the picker without repeating the matching aggregates."""
    with connect() as conn:
        rows = conn.execute('''SELECT t.phone,
          coalesce(t.label,mode() WITHIN GROUP (ORDER BY nullif(d.contact_name,''))) AS label
          FROM xm.tracked_sales t LEFT JOIN xm.documents d
            ON d.company_id=t.company_id AND d.active
            AND (d.contact_phones @> ARRAY[t.phone] OR d.contact_phone=t.phone)
          WHERE t.company_id=current_setting('xm.workspace_id')
          GROUP BY t.phone,t.label ORDER BY label NULLS LAST,t.phone''').fetchall()
    return {'sales': rows}


@router.get('')
def overview(direction: Literal['property', 'buyer'] = 'property', date_from: str = '', date_to: str = '', phone: str = '', include_archived: bool = False):
    phone = normalize_phone(phone) if phone else ''
    cte, args = population(direction, phone, include_archived)
    column = 'listing_entity' if direction == 'property' else 'buyer_entity'
    period, dates = event_filter(date_from, date_to)
    cte += f''', events AS MATERIALIZED (
      SELECT me.* FROM xm.match_events me
      WHERE me.company_id=current_setting('xm.workspace_id'){period}
        AND me.{column} IN (SELECT entity_id FROM inventory))'''
    with connect() as conn:
        sales = conn.execute(cte + f'''
          SELECT t.phone,coalesce(t.label,mode() WITHIN GROUP (ORDER BY i.contact_name)) AS label,
            count(DISTINCT i.entity_id) AS total,
            count(DISTINCT i.entity_id) FILTER (WHERE ev.id IS NOT NULL) AS matched,
            count(DISTINCT ev.id) AS pairs,max(ev.found_at) AS last_match_at
          FROM xm.tracked_sales t LEFT JOIN inventory i ON i.phone=t.phone
          LEFT JOIN events ev ON ev.{column}=i.entity_id
          WHERE t.company_id=current_setting('xm.workspace_id') AND (%s='' OR t.phone=%s)
          GROUP BY t.phone,t.label ORDER BY count(DISTINCT ev.id) DESC,label NULLS LAST,t.phone''', args + dates + [phone, phone]).fetchall()
        totals = conn.execute(cte + f'''
          SELECT count(DISTINCT i.entity_id) AS total,
            count(DISTINCT i.entity_id) FILTER (WHERE ev.id IS NOT NULL) AS matched,
            count(DISTINCT ev.id) AS pairs
          FROM inventory i LEFT JOIN events ev ON ev.{column}=i.entity_id''', args + dates).fetchone()
        days = conn.execute(cte + f'''
          SELECT (found_at AT TIME ZONE 'Asia/Jakarta')::date AS day,count(*) AS pairs,
            count(DISTINCT {column}) AS matched FROM events GROUP BY 1 ORDER BY 1''', args + dates).fetchall()
    for row in [totals, *sales]:
        row['unmatched'] = row['total'] - row['matched']
        row['matching_rate'] = rate(row['matched'], row['total'])
    return {'direction': direction, 'totals': totals, 'sales': sales, 'days': days,
            'period_basis': 'first_discovery', 'inventory_basis': 'including_archived' if include_archived else 'current_non_deleted'}


@router.get('/entities')
def entities(direction: Literal['property', 'buyer'] = 'property', phone: str = '', date_from: str = '', date_to: str = '',
             limit: int = 50, offset: int = 0, include_archived: bool = False):
    cte, args = population(direction, normalize_phone(phone) if phone else '', include_archived)
    column = 'listing_entity' if direction == 'property' else 'buyer_entity'
    period, dates = event_filter(date_from, date_to)
    limit = min(100, max(1, limit))
    with connect() as conn:
        rows = conn.execute(cte + f'''
          SELECT i.entity_id,i.public_id,i.entity_status,count(DISTINCT me.id) AS pairs,
            min(me.found_at) AS first_match_at,max(me.found_at) AS last_match_at,
            (SELECT coalesce(d.segment_text,r.raw_text) FROM xm.documents d
             JOIN xm.raw_messages r ON r.id=d.raw_message_id
             WHERE d.entity_id=i.entity_id AND d.company_id=current_setting('xm.workspace_id')
             ORDER BY d.active DESC,r.sent_at DESC NULLS LAST,d.id LIMIT 1) AS message
          FROM inventory i LEFT JOIN xm.match_events me ON me.{column}=i.entity_id
            AND me.company_id=current_setting('xm.workspace_id'){period}
          GROUP BY i.entity_id,i.public_id,i.entity_status
          ORDER BY count(DISTINCT me.id) DESC,i.public_id LIMIT %s OFFSET %s''', args + dates + [limit + 1, max(0, offset)]).fetchall()
    return {'rows': rows[:limit], 'has_more': len(rows) > limit}


@router.get('/history')
def history(entity_id: UUID, direction: Literal['property', 'buyer'] = 'property', date_from: str = '', date_to: str = '',
            limit: int = 50, offset: int = 0):
    column, other = ('listing_entity', 'buyer_entity') if direction == 'property' else ('buyer_entity', 'listing_entity')
    period, dates = event_filter(date_from, date_to)
    limit = min(100, max(1, limit))
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM xm.entities WHERE entity_id=%s AND company_id=current_setting('xm.workspace_id')", (entity_id,)).fetchone():
            raise HTTPException(404, 'Buyer or listing not found.')
        rows = conn.execute(f'''
          SELECT me.id,me.found_at,me.last_seen_at,me.first_score,me.last_score,me.temperature AS first_status,
            me.last_status,me.active,me.source,me.import_id,me.agent_name,e.entity_id,e.public_id,e.status AS entity_status,
            (SELECT coalesce(d.segment_text,r.raw_text) FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
             WHERE d.company_id=me.company_id AND d.entity_id=e.entity_id
             ORDER BY d.active DESC,r.sent_at DESC NULLS LAST,d.id LIMIT 1) AS message
          FROM xm.match_events me JOIN xm.entities e ON e.entity_id=me.{other} AND e.company_id=me.company_id
          WHERE me.company_id=current_setting('xm.workspace_id') AND me.{column}=%s{period}
          ORDER BY me.found_at DESC,me.id DESC LIMIT %s OFFSET %s''', [entity_id, *dates, limit + 1, max(0, offset)]).fetchall()
    return {'rows': rows[:limit], 'has_more': len(rows) > limit}
