"""Request/job-local workspace ownership. Never accept a workspace ID from a client."""
from contextlib import contextmanager
from contextvars import ContextVar

# Legacy command-line audit scripts operate on the original admin workspace.
_workspace = ContextVar('xm_workspace', default='xm')
_owner = ContextVar('xm_workspace_owner', default=None)


def workspace_id():
    return _workspace.get()


def owner_id():
    return _owner.get()


@contextmanager
def workspace_scope(workspace, owner=None):
    token = _workspace.set(workspace)
    owner_token = _owner.set(owner)
    try:
        yield
    finally:
        _owner.reset(owner_token)
        _workspace.reset(token)


def provision_workspace(conn, user_id, legacy=False):
    """One private namespace per account; new accounts never copy existing data."""
    workspace = 'xm' if legacy else 'xm-user-' + str(user_id)
    row = conn.execute('UPDATE xm.users SET workspace_id=coalesce(workspace_id,%s) WHERE id=%s RETURNING workspace_id',
                       (workspace, user_id)).fetchone()
    workspace = row['workspace_id']
    conn.execute('INSERT INTO xm.match_settings(company_id) VALUES(%s) ON CONFLICT DO NOTHING', (workspace,))
    conn.execute('INSERT INTO xm.app_preferences(company_id) VALUES(%s) ON CONFLICT DO NOTHING', (workspace,))
    return workspace


def provision_company(conn, company_id, name=None):
    """Create the shared settings rows of a company workspace (idempotent)."""
    conn.execute('INSERT INTO xm.match_settings(company_id) VALUES(%s) ON CONFLICT DO NOTHING', (company_id,))
    # A new company starts without a keyword filter (every message counts) until its super admin sets one.
    conn.execute("INSERT INTO xm.app_preferences(company_id, company_name, search_terms) VALUES(%s,%s,'{}') ON CONFLICT DO NOTHING", (company_id, name))
    if name:
        conn.execute('UPDATE xm.app_preferences SET company_name=%s WHERE company_id=%s AND company_name IS NULL', (name, company_id))
    return company_id
