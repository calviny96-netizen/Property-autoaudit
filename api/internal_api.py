"""Explicit internal API access for server-to-server reporting across companies."""
import hashlib
import hmac
import os
import uuid

from fastapi import HTTPException
from db import connect

# Keep account administration, deployment and credential management session-only.
READ_PATHS = {
    '/admin/companies', '/openapi.json', '/stats', '/dashboard/overview',
    '/stock/overview', '/stock/log', '/stock/export', '/company/settings',
    '/workspace', '/workspace/groups', '/workspace/dates', '/entities/lookup',
    '/entities/summary', '/matches/recent', '/matches/recent/days',
    '/imports', '/documents', '/index/status', '/reprocess/status',
    '/settings', '/glossary', '/search-default', '/buyers/list', '/ai/models', '/ai/review-runs',
    '/matches/recent/summary', '/integration/exports',
}
WRITE_PATHS = {
    ('PUT', '/stock/tracked'), ('POST', '/stock/snapshot'),
    ('POST', '/workspace/recommendations'), ('POST', '/export/pdf'),
    ('POST', '/export/all/plan'), ('POST', '/export/all/part'),
    ('POST', '/stock/tracked/item'), ('PATCH', '/stock/tracked/item'), ('DELETE', '/stock/tracked/item'),
    ('POST', '/stock/tracked/parse'), ('POST', '/entities/status'),
    ('PUT', '/settings'), ('PUT', '/search-default'), ('PUT', '/company/settings'),
    ('POST', '/ai/review-runs/estimate'), ('POST', '/ai/review-runs'), ('POST', '/ai/reviews/lookup'),
    ('POST', '/integration/exports/plan'),
    ('POST', '/export/all/cancel'), ('POST', '/export/selection'), ('POST', '/imports'),
}


def allowed(method, path):
    if method == 'GET' and path in READ_PATHS:
        return True
    if (method, path) in WRITE_PATHS:
        return True
    # Validate route shapes rather than opening an entire URL prefix.
    pieces = path.strip('/').split('/')
    if pieces == ['integration', 'imports']:
        return method == 'POST'
    if pieces == ['integration', 'exports']:
        return method == 'POST'
    try:
        if len(pieces) in (3, 4) and pieces[:2] == ['ai', 'review-runs']:
            uuid.UUID(pieces[2])
            return (len(pieces) == 3 and method == 'GET') or (len(pieces) == 4 and (
                (method == 'GET' and pieces[3] in ('results', 'export')) or
                (method == 'POST' and pieces[3] == 'cancel')))
        if len(pieces) == 4 and pieces[:2] == ['ai', 'reviews'] and pieces[3] == 'override':
            uuid.UUID(pieces[2])
            return method == 'PUT'
        if len(pieces) == 4 and pieces[:2] == ['integration', 'exports'] and pieces[3] == 'download':
            uuid.UUID(pieces[2])
            return method == 'GET'
        if len(pieces) == 3 and pieces[:2] in (['integration', 'imports'], ['integration', 'exports']):
            uuid.UUID(pieces[2])
            return method == 'GET' or (method == 'DELETE' and pieces[1] == 'exports')
        if len(pieces) in (5, 6) and pieces[:2] == ['integration', 'exports'] and pieces[3] == 'files':
            uuid.UUID(pieces[2]); uuid.UUID(pieces[4])
            return (len(pieces) == 5 and method == 'GET') or (len(pieces) == 6 and pieces[5] == 'delivered' and method == 'POST')
        if len(pieces) == 4 and pieces[:2] == ['export', 'all'] and pieces[3] == 'download':
            uuid.UUID(pieces[2])
            return method == 'GET'
    except ValueError:
        return False
    return False


def authenticate(request):
    configured = os.getenv('XM_INTERNAL_API_KEY', '')
    token = request.headers.get('Authorization', '').partition(' ')[2]
    if not configured or not hmac.compare_digest(
        hashlib.sha256(token.encode()).digest(), hashlib.sha256(configured.encode()).digest()
    ):
        raise HTTPException(401, 'The internal API key is invalid or not configured.')
    path = request.url.path.rstrip('/') or '/'
    if not allowed(request.method, path):
        raise HTTPException(403, 'This endpoint is not available to the internal API key.')
    if request.headers.get('X-XM-User-Id'):
        raise HTTPException(403, 'Use X-XM-Company-Id with the internal API key.')
    company = request.headers.get('X-XM-Company-Id', '').strip()
    if not company and path not in ('/admin/companies', '/openapi.json'):
        raise HTTPException(400, 'The X-XM-Company-Id header is required.')
    with connect() as conn:
        # A real platform administrator supplies the actor ID for stock audit records.
        actor = conn.execute("""SELECT id,email,display_name,role,is_locked,workspace_id
          FROM xm.users WHERE role='admin' AND NOT is_locked ORDER BY created_at,id LIMIT 1""").fetchone()
        if not actor:
            raise HTTPException(503, 'An active platform administrator is required for the internal API.')
        if company and not conn.execute('SELECT 1 FROM xm.app_preferences WHERE company_id=%s', (company,)).fetchone():
            raise HTTPException(404, 'Company not found.')
    return {'company_id': company or actor['workspace_id'], 'user': actor}
