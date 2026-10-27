"""Buyers page: every buyer request card (one per public ID, grouped like the Match page) with its status.

"Disabled" in the UI is the existing soft ``deleted`` entity status: it already removes a buyer from matching in both
directions, from counters, recent matches and exports, and it is reversible with POST /entities/status."""
from fastapi import APIRouter, HTTPException

from db import connect
from entities import STATUSES, exact_public_ids, normalize_id_query, with_aliases
from tenant import workspace_id
from matching_scope import document_scope_filter

router = APIRouter(prefix='/buyers')
PAGE = 50


def _search_clause(search, conn=None):
    """Public ID, sender, contact name, phone number or message text. A retired public ID (merged into an older
    one, ``entities.merge_entities``) finds the card of the ID it was merged into."""
    text = search.strip()
    if not text:
        return '', []
    like = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    parts = ["r.raw_text ILIKE %s", "coalesce(r.author,'') ILIKE %s", "coalesce(d.contact_name,'') ILIKE %s"]
    params = [like, like, like]
    digits = ''.join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:
        tail = digits[1:] if digits.startswith('0') else digits[2:] if digits.startswith('62') else digits
        parts.append("(coalesce(d.contact_phone,'') LIKE %s OR array_to_string(d.contact_phones,',') LIKE %s)")
        params += ['%' + tail + '%'] * 2
    needle = normalize_id_query(text)
    if needle and len(needle) >= 2:
        exact = exact_public_ids(needle)
        if exact:
            parts.append('g.public_id = ANY(%s)')
            params.append(with_aliases(conn, workspace_id(), exact) if conn is not None else exact)
        else:
            parts.append("replace(g.public_id,'-','') LIKE %s")
            params.append('%' + needle + '%')
    return ' AND (' + ' OR '.join(parts) + ')', params


@router.get('/list')
def buyer_list(search: str = '', status: str = '', offset: int = 0, scope: str = 'all'):
    chosen = [s for s in dict.fromkeys(v.strip() for v in status.split(',')) if s]
    if any(s not in STATUSES for s in chosen):
        raise HTTPException(400, 'Unknown buyer status.')
    with connect() as conn:
        search_sql, search_params = _search_clause(search[:200], conn)
        # Every buyer in the workspace by default: buyers from outside agents can spoil matches too and
        # must be findable to disable them. scope=company limits the list to the company's matching scope.
        scope_sql, scope_params = document_scope_filter(conn) if scope == 'company' else ('', [])
        # One representative (latest) posting per buyer card.
        base = '''WITH cards AS (
          SELECT DISTINCT ON (g.group_id) g.group_id, d.id, g.status, r.sent_at
          FROM xm.document_groups g JOIN xm.document_group_members gm ON gm.group_id = g.group_id
          JOIN xm.documents d ON d.id = gm.document_id JOIN xm.raw_messages r ON r.id = d.raw_message_id
          WHERE g.company_id = current_setting('xm.workspace_id') AND g.document_type = 'buyer_request' AND d.active''' + \
            scope_sql + search_sql + ''' ORDER BY g.group_id, r.sent_at DESC NULLS LAST, d.id)'''
        params = [*scope_params, *search_params]
        counts = {s: 0 for s in STATUSES}
        for row in conn.execute(base + ' SELECT status, count(*) n FROM cards GROUP BY 1', params).fetchall():
            counts[row['status']] = row['n']
        filter_sql = ' WHERE c.status = ANY(%s)' if chosen else ''
        rows = conn.execute(base + '''
          SELECT d.*, r.raw_text, r.chat_name, r.sent_at, r.author, g.public_id, g.status AS entity_status, g.entity_id,
                 g.duplicate_count, g.last_seen_at, g.hot_count, g.warm_count, g.custom_count,
                 g.hot_count + g.warm_count + g.custom_count AS match_count
          FROM cards c JOIN xm.documents d ON d.id = c.id JOIN xm.raw_messages r ON r.id = d.raw_message_id
          JOIN xm.document_groups g ON g.group_id = c.group_id''' + filter_sql + '''
          ORDER BY c.sent_at DESC NULLS LAST, c.id LIMIT %s OFFSET %s''',
            [*params, *([chosen] if chosen else []), PAGE + 1, max(0, offset)]).fetchall()
    return {'rows': rows[:PAGE], 'has_more': len(rows) > PAGE, 'counts': counts, 'page_size': PAGE}
