"""Platform administrator: usage per company (AI tokens and cost, uploads, exports, sign-ins) and the activity log."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from activity import ACTION_LABELS
from db import connect

router = APIRouter(prefix='/admin')

COMPANY_IDS_SQL = """WITH ids AS (SELECT DISTINCT workspace_id AS company_id FROM xm.users WHERE workspace_id IS NOT NULL AND role <> 'admin'
                                  UNION SELECT company_id FROM xm.app_preferences WHERE company_id LIKE 'xm-co-%%')"""


def _since(days: int):
    return datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 3650)))


@router.get('/usage')
def usage(days: int = 30):
    """Per company: accounts, data, uploads, exports, AI reviews with tokens and cost (all time and the last ``days``)."""
    since = _since(days)
    with connect() as conn:
        rows = conn.execute(COMPANY_IDS_SQL + """
            SELECT i.company_id, coalesce(ap.company_name, i.company_id) AS name, coalesce(ap.status,'active') AS status,
                   ap.registered_at, ap.contact_email,
                   (SELECT count(*) FROM xm.users u WHERE u.workspace_id=i.company_id AND u.role<>'admin') AS members,
                   (SELECT max(last_login_at) FROM xm.users u WHERE u.workspace_id=i.company_id AND u.role<>'admin') AS last_login_at,
                   (SELECT count(*) FROM xm.raw_messages r WHERE r.company_id=i.company_id) AS messages,
                   (SELECT count(*) FROM xm.document_groups g WHERE g.company_id=i.company_id AND g.document_type='buyer_request' AND g.status<>'deleted') AS buyers,
                   (SELECT count(*) FROM xm.document_groups g WHERE g.company_id=i.company_id AND g.document_type='property_listing' AND g.status<>'deleted') AS listings,
                   (SELECT count(*) FROM xm.imports im WHERE im.company_id=i.company_id) AS imports_total,
                   (SELECT count(*) FROM xm.imports im WHERE im.company_id=i.company_id AND im.created_at>=%(since)s) AS imports_period,
                   (SELECT count(*) FROM xm.activity_log a WHERE a.company_id=i.company_id AND a.action IN ('export_pdf','export_all') AND a.created_at>=%(since)s) AS exports_period,
                   (SELECT count(*) FROM xm.activity_log a WHERE a.company_id=i.company_id AND a.action='login' AND a.created_at>=%(since)s) AS logins_period,
                   (SELECT max(created_at) FROM xm.activity_log a WHERE a.company_id=i.company_id) AS last_activity_at,
                   (SELECT count(*) FROM xm.ai_review_runs r WHERE r.company_id=i.company_id) AS ai_runs_total,
                   (SELECT count(*) FROM xm.ai_review_runs r WHERE r.company_id=i.company_id AND r.created_at>=%(since)s) AS ai_runs_period,
                   (SELECT count(*) FROM xm.ai_keys k WHERE k.company_id=i.company_id) AS ai_keys,
                   t.*
            FROM ids i LEFT JOIN xm.app_preferences ap ON ap.company_id=i.company_id
            CROSS JOIN LATERAL (
              SELECT coalesce(sum(a.tokens_in),0) AS tokens_in_total, coalesce(sum(a.tokens_out),0) AS tokens_out_total,
                     coalesce(sum(a.cost),0) AS cost_total, count(*) FILTER (WHERE a.status='done') AS reviews_total,
                     coalesce(sum(a.tokens_in) FILTER (WHERE a.created_at>=%(since)s),0) AS tokens_in_period,
                     coalesce(sum(a.tokens_out) FILTER (WHERE a.created_at>=%(since)s),0) AS tokens_out_period,
                     coalesce(sum(a.cost) FILTER (WHERE a.created_at>=%(since)s),0) AS cost_period,
                     count(*) FILTER (WHERE a.status='done' AND a.created_at>=%(since)s) AS reviews_period,
                     bool_or(a.cost_estimated) AS cost_estimated
              FROM xm.ai_reviews a WHERE a.company_id=i.company_id) t
            ORDER BY cost_total DESC, name""", {'since': since}).fetchall()
    companies = []
    for row in rows:
        row = dict(row)
        for key in ('cost_total', 'cost_period'):
            row[key] = float(row[key] or 0)
        for key in ('tokens_in_total', 'tokens_out_total', 'reviews_total', 'tokens_in_period', 'tokens_out_period', 'reviews_period',
                    'imports_total', 'imports_period', 'exports_period', 'logins_period', 'ai_runs_total', 'ai_runs_period', 'ai_keys',
                    'members', 'messages', 'buyers', 'listings'):
            row[key] = int(row.get(key) or 0)
        row['cost_estimated'] = bool(row.get('cost_estimated'))
        companies.append(row)
    totals = {key: sum(c[key] for c in companies) for key in ('tokens_in_total', 'tokens_out_total', 'cost_total', 'reviews_total',
                                                              'tokens_in_period', 'tokens_out_period', 'cost_period', 'reviews_period',
                                                              'imports_period', 'exports_period', 'logins_period')}
    return {'days': days, 'since': since, 'companies': companies, 'totals': totals}


@router.get('/usage/monthly')
def usage_monthly(months: int = 6, company_id: str | None = None):
    """AI tokens and cost per company per month (newest last), for the usage chart."""
    months = max(1, min(months, 36))
    first = date.today().replace(day=1)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    with connect() as conn:
        rows = conn.execute("""
            SELECT a.company_id, coalesce(ap.company_name, a.company_id) AS name, to_char(date_trunc('month', a.created_at), 'YYYY-MM') AS month,
                   coalesce(sum(a.tokens_in),0) AS tokens_in, coalesce(sum(a.tokens_out),0) AS tokens_out, coalesce(sum(a.cost),0) AS cost,
                   count(*) FILTER (WHERE a.status='done') AS reviews
            FROM xm.ai_reviews a LEFT JOIN xm.app_preferences ap ON ap.company_id=a.company_id
            WHERE a.created_at >= %s AND (%s::text IS NULL OR a.company_id=%s)
            GROUP BY a.company_id, ap.company_name, 3 ORDER BY 3, 2""", (first, company_id, company_id)).fetchall()
    return {'from': first, 'rows': [{**row, 'tokens_in': int(row['tokens_in']), 'tokens_out': int(row['tokens_out']),
                                     'cost': float(row['cost']), 'reviews': int(row['reviews'])} for row in rows]}


@router.get('/logs')
def logs(company_id: str | None = None, action: str | None = None, user: str | None = None, days: int | None = None,
         limit: int = 50, offset: int = 0, from_: str | None = Query(default=None, alias='from'), to: str | None = None):
    """The activity log, newest first. Filters: company, action, user email (substring), last ``days`` or a
    ``from``/``to`` date range (YYYY-MM-DD, Jakarta days, both inclusive)."""
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    where, params = [], []
    for value, op in ((from_, '>='), (to, '<')):
        if value:
            try:
                day = date.fromisoformat(value)
            except ValueError:
                raise HTTPException(400, 'Dates must be written as YYYY-MM-DD.')
            if op == '<':
                day = day + timedelta(days=1)
            where.append(f"a.created_at {op} (%s::date::timestamp AT TIME ZONE 'Asia/Jakarta')"); params.append(day)
    if company_id:
        where.append('a.company_id = %s'); params.append(company_id)
    if action:
        where.append('a.action = %s'); params.append(action)
    if user:
        where.append('a.user_email ILIKE %s'); params.append(f'%{user}%')
    if days:
        where.append('a.created_at >= %s'); params.append(_since(days))
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    with connect() as conn:
        total = conn.execute(f'SELECT count(*) AS n FROM xm.activity_log a{clause}', params).fetchone()['n']
        rows = conn.execute(f"""
            SELECT a.id, a.company_id, coalesce(ap.company_name, a.company_id) AS company_name, a.user_id, a.user_email,
                   coalesce(nullif(u.display_name,''), a.user_email) AS user_name, a.action, a.method, a.path, a.status, a.detail,
                   a.duration_ms, a.ip, a.created_at
            FROM xm.activity_log a LEFT JOIN xm.app_preferences ap ON ap.company_id=a.company_id LEFT JOIN xm.users u ON u.id=a.user_id
            {clause} ORDER BY a.created_at DESC, a.id DESC LIMIT %s OFFSET %s""", [*params, limit, offset]).fetchall()
    for row in rows:
        row['label'] = ACTION_LABELS.get(row['action'], row['action'])
    return {'rows': rows, 'total': total, 'limit': limit, 'offset': offset,
            'actions': [{'id': key, 'label': label} for key, label in ACTION_LABELS.items()]}


@router.get('/companies/{company_id}/activity')
def company_activity(company_id: str, days: int = 30):
    """Daily sign-ins and actions of one company (for the company card)."""
    since = _since(days)
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM xm.app_preferences WHERE company_id=%s', (company_id,)).fetchone():
            raise HTTPException(404, 'Company not found.')
        rows = conn.execute("""SELECT date_trunc('day', created_at)::date AS day, count(*) FILTER (WHERE action='login') AS logins,
                                      count(*) FILTER (WHERE action NOT IN ('login','login_failed','logout')) AS actions
                               FROM xm.activity_log WHERE company_id=%s AND created_at>=%s GROUP BY 1 ORDER BY 1""", (company_id, since)).fetchall()
    return {'days': days, 'rows': rows}
