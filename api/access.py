"""Who may call what. Kept free of FastAPI so the whole matrix is unit-testable.

Roles
  admin          platform administrator: companies, usage and logs. No matching features of its own;
                 it may step inside a company (X-XM-User-Id) and then acts as that company's admin.
  company_admin  super admin of one company (team, settings, uploads, locks): every feature
  user           company member: only the features its company admin granted (``features``)
"""

ROLE_RANK = {'user': 1, 'company_admin': 2, 'admin': 3}
PUBLIC_PATHS = {'/health', '/auth/login', '/auth/register'}
SESSION_PATHS = {'/auth/me', '/auth/logout', '/auth/password/first'}
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}

# Features a company admin can grant to a member (the menu / feature matrix in Team).
FEATURES = (
    ('home', 'Home', 'The dashboard with buyer demand and listing numbers.'),
    ('match', 'Match', 'Match buyers and listings, see recommendations and export PDFs.'),
    ('recent', 'Recent matches', 'New matches found by the latest uploads.'),
    ('stock', 'Sales stock', 'Listings per sales person and the stock log.'),
    ('buyers', 'Buyers', 'Search buyer IDs and disable buyers.'),
    ('mark_status', 'Mark status', 'Mark buyers and listings as Ready, On hold, Sold or Deleted.'),
    ('upload', 'Upload', 'Upload WhatsApp chat exports.'),
    ('settings', 'Settings', 'Matching settings, words & places, sales watchlist and service areas.'),
    ('team', 'Team', 'Add team members and choose what they can open.'),
    ('jev', 'AI Review', 'AI review of matches.'),
)
FEATURE_KEYS = tuple(key for key, _, _ in FEATURES)
DEFAULT_MEMBER_FEATURES = ('home', 'match', 'recent', 'stock', 'buyers', 'mark_status')
PAGE_FEATURES = ('home', 'match', 'recent', 'stock', 'buyers')

# Writes a regular member may perform without any feature grant.
MEMBER_WRITES = {
    ('PUT', '/preferences'), ('POST', '/auth/logout'), ('PUT', '/auth/password'),
    ('PUT', '/search-default/personal'), ('DELETE', '/search-default/personal'),
    ('POST', '/matches/recent/seen'),
}
# Reads every signed-in account needs (the shell, the account page, the "not updated yet" indicator).
SHARED_READS = ('/stats', '/company/settings', '/preferences', '/matches/recent/summary', '/reprocess/status',
                '/workspace/dates', '/search-default', '/glossary', '/settings', '/entities/lookup', '/entities/summary',
                '/location-index', '/index/status', '/documents', '/health')


def normalize(path: str) -> str:
    return path.rstrip('/') or '/'


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + '/')


def required_feature(method: str, path: str):
    """The feature a member needs for ``method path``; None when the call is open to every member."""
    path, method = normalize(path), method.upper()
    safe = method in SAFE_METHODS
    if _under(path, '/team'):
        return 'team'
    if _under(path, '/ai'):
        return 'jev'
    if _under(path, '/imports'):
        return 'upload'
    if _under(path, '/dashboard'):
        return 'home'
    if _under(path, '/stock'):
        return 'stock' if safe else 'settings'  # the watchlist and snapshots are settings
    if path == '/buyers/list':
        return 'buyers'
    if path == '/entities/status':
        return 'mark_status'
    if _under(path, '/matches/recent'):
        return None if path in ('/matches/recent/summary', '/matches/recent/seen') else 'recent'
    if _under(path, '/workspace') or _under(path, '/export') or _under(path, '/buyers') or path == '/matches':
        return None if path == '/workspace/dates' else 'match'
    if not safe and (path in ('/settings', '/glossary', '/search-default', '/reprocess', '/index/recompute', '/company/settings',
                              '/matches/recompute') or _under(path, '/glossary') or _under(path, '/location-index')):
        return 'settings'
    return None


def required_role(method: str, path: str) -> str:
    """Lowest role that may call ``method path`` without a feature grant (kept for the older matrix)."""
    path = normalize(path)
    if path == '/admin' or path.startswith('/admin/'):
        return 'admin'
    if path == '/integration/keys' or path.startswith('/integration/keys/'):
        return 'company_admin'
    feature = required_feature(method, path)
    if feature is not None:
        return 'user' if feature in DEFAULT_MEMBER_FEATURES else 'company_admin'
    if method.upper() in SAFE_METHODS:
        return 'user'
    if (method.upper(), path) in MEMBER_WRITES:
        return 'user'
    return 'company_admin'


def role_allows(role: str, needed: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[needed]


def member_features(user) -> frozenset:
    """Effective feature grants of an account: admins get everything, members their stored list (or the default)."""
    role = user.get('role') if isinstance(user, dict) else user
    if role in ('admin', 'company_admin'):
        return frozenset(FEATURE_KEYS)
    stored = user.get('features') if isinstance(user, dict) else None
    if isinstance(stored, (list, tuple)):
        return frozenset(f for f in stored if f in FEATURE_KEYS)
    return frozenset(DEFAULT_MEMBER_FEATURES)


def allowed(user, method: str, path: str) -> bool:
    """May this account call ``method path``? The platform admin may do everything; a company admin everything
    inside its company; a member what its grants cover plus the shared reads and its own writes."""
    path = normalize(path)
    role = user.get('role')
    if path == '/admin' or path.startswith('/admin/'):
        return role == 'admin'
    if _under(path, '/ai/keys'):
        return role == 'admin'
    if role in ('admin', 'company_admin'):
        return True
    if path == '/integration/keys' or path.startswith('/integration/keys/'):
        return False
    feature = required_feature(method, path)
    if feature is not None:
        return feature in member_features(user)
    if method.upper() in SAFE_METHODS:
        return True
    return (method.upper(), path) in MEMBER_WRITES


def denial_message(method: str, path: str) -> str:
    path = normalize(path)
    if path == '/admin' or path.startswith('/admin/'):
        return 'Only the platform administrator can do this.'
    feature = required_feature(method, path)
    label = next((name for key, name, _ in FEATURES if key == feature), None)
    if label:
        return f'Your account does not have access to {label}. Ask your company admin.'
    return 'Only a company admin can do this.'


def permissions(role: str, search_locked: bool = False, features=None) -> dict:
    """What the interface may offer. The server still enforces every call."""
    granted = member_features({'role': role, 'features': features})
    admin = role_allows(role, 'company_admin')
    return {
        'platform': role == 'admin',
        'manage_team': 'team' in granted,
        'manage_settings': 'settings' in granted,
        'upload_data': 'upload' in granted,
        'edit_company_search': 'settings' in granted,
        'edit_personal_search': (role == 'user') and not search_locked,
        'mark_status': 'mark_status' in granted,
        'jev_review': 'jev' in granted,
        'pages': {key: key in granted for key in PAGE_FEATURES},
        'features': [key for key in FEATURE_KEYS if key in granted],
        'all_features': admin,
    }
