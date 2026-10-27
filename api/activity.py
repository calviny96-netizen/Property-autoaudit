"""Activity log: who did what, in which company, when. Read by the platform administrator (Usage & logs).

Every successful write to the API is recorded by the access middleware with a short action name;
sign-ins, registrations and administrative actions are recorded explicitly. Rows are plain facts
(no request bodies, never passwords or keys)."""
import re

from db import connect

# (method, path pattern) -> action name. First match wins; anything else is logged as "write".
_RULES = [
    ('POST', r'/imports$', 'upload'),
    ('POST', r'/integration/imports$', 'upload'),
    ('POST', r'/reprocess$', 'reprocess'),
    ('POST', r'/index/recompute$', 'reprocess'),
    ('POST', r'/matches/recompute$', 'reprocess'),
    ('POST', r'/export/pdf$', 'export_pdf'),
    ('POST', r'/export/selection$', 'export_pdf'),
    ('POST', r'/export/all/plan$', 'export_all'),
    ('POST', r'/integration/exports$', 'export_all'),
    ('PUT', r'/settings$', 'settings_changed'),
    ('PUT', r'/company/settings$', 'company_settings_changed'),
    ('PUT', r'/search-default$', 'keywords_changed'),
    ('PUT', r'/glossary(/entries)?$', 'glossary_changed'),
    ('*', r'/location-index/', 'locations_changed'),
    ('PUT', r'/stock/tracked$', 'watchlist_changed'),
    ('*', r'/stock/tracked/item$', 'watchlist_changed'),
    ('POST', r'/stock/snapshot$', 'stock_snapshot'),
    ('POST', r'/entities/status$', 'status_marked'),
    ('POST', r'/team/users$', 'member_added'),
    ('PUT', r'/team/users/', 'member_edited'),
    ('POST', r'/ai/review-runs$', 'ai_review_started'),
    ('POST', r'/ai/review-runs/[^/]+/cancel$', 'ai_review_cancelled'),
    ('PUT', r'/ai/reviews/', 'ai_review_overridden'),
    ('POST', r'/ai/keys$', 'ai_key_added'),
    ('DELETE', r'/ai/keys/', 'ai_key_removed'),
    ('POST', r'/ai/keys/[^/]+/default$', 'ai_key_default'),
    ('POST', r'/integration/keys$', 'integration_key_added'),
    ('DELETE', r'/integration/keys/', 'integration_key_removed'),
    ('PUT', r'/auth/password$', 'password_changed'),
    ('POST', r'/auth/password/first$', 'password_changed'),
]
_COMPILED = [(method, re.compile(pattern), action) for method, pattern, action in _RULES]
# Writes that are reads in disguise or far too frequent to keep.
SKIPPED = re.compile(r'^/(?:workspace/recommendations|buyers/recommendations/batch|export/all/(?:part|cancel)|matches/recent/seen'
                     r'|preferences|search-default/personal|ai/reviews/lookup|ai/review-runs/estimate|location-index/preview'
                     r'|stock/tracked/parse|auth/(?:login|logout|me)|admin/deploy)$')

ACTION_LABELS = {
    'login': 'Signed in', 'login_failed': 'Sign-in failed', 'logout': 'Signed out', 'register': 'Company registered',
    'upload': 'Uploaded a chat export', 'reprocess': 'Reprocessed results', 'export_pdf': 'Exported a PDF',
    'export_all': 'Exported all matches', 'settings_changed': 'Changed matching settings',
    'company_settings_changed': 'Changed company settings', 'keywords_changed': 'Changed company keywords',
    'glossary_changed': 'Changed words & places', 'locations_changed': 'Changed locations',
    'watchlist_changed': 'Changed the sales watchlist', 'stock_snapshot': 'Saved a stock snapshot',
    'status_marked': 'Marked a status', 'member_added': 'Added a team member', 'member_edited': 'Edited a team member',
    'ai_review_started': 'Started an AI review', 'ai_review_cancelled': 'Cancelled an AI review',
    'ai_review_overridden': 'Overrode an AI decision', 'ai_key_added': 'Added an AI key', 'ai_key_removed': 'Removed an AI key',
    'ai_key_default': 'Changed the default AI key', 'integration_key_added': 'Added an integration key',
    'integration_key_removed': 'Removed an integration key', 'password_changed': 'Changed password',
    'company_created': 'Company created', 'company_approved': 'Company approved', 'company_rejected': 'Registration rejected',
    'company_activated': 'Company activated', 'company_deactivated': 'Company deactivated', 'company_renamed': 'Company renamed',
    'password_reset': 'Password reset by admin', 'knowledge_copied': 'Words & places copied from another company',
    'write': 'Other change',
}


def action_for(method: str, path: str):
    """Action name of a write, or None when it is not worth logging."""
    if SKIPPED.match(path):
        return None
    for rule_method, pattern, action in _COMPILED:
        if (rule_method == '*' or rule_method == method) and pattern.search(path):
            return action
    return 'write'


def record(conn, action, *, company_id=None, user=None, method=None, path=None, status=None, detail=None, duration_ms=None, ip=None):
    """Append one row inside the caller's transaction."""
    conn.execute(
        """INSERT INTO xm.activity_log(company_id, user_id, user_email, action, method, path, status, detail, duration_ms, ip)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
        (company_id, user.get('id') if user else None, user.get('email') if user else None, action, method, path, status,
         _json(detail), duration_ms, ip))


def log(action, **kwargs):
    """Append one row on its own connection; never lets a logging problem fail the request."""
    try:
        with connect() as conn:
            record(conn, action, **kwargs)
            conn.commit()
    except Exception as exc:  # pragma: no cover - logging must not break requests
        print(f'activity log skipped: {exc}', flush=True)


def _json(detail):
    import json
    return json.dumps(detail or {}, default=str)
