"""Company-level settings and, for the platform administrator, company management."""
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from psycopg.errors import UniqueViolation

import activity
from access import permissions
from auth import PASSWORD_MIN, _password_hash, current_user, temporary_password
from db import connect
from stock import tracked_phones
from tenant import owner_id, provision_company, workspace_id
from runtime_cache import invalidate_stats

router = APIRouter()


def company_row(conn):
    return conn.execute(
        """SELECT company_name, search_terms, search_locked, listing_group_by, matching_mode
           FROM xm.app_preferences WHERE company_id = current_setting('xm.workspace_id')""").fetchone() or {
        'company_name': None, 'search_terms': [], 'search_locked': False, 'listing_group_by': 'sender', 'matching_mode': 'company'}


def personal_terms(conn, user_key):
    row = conn.execute('SELECT preferences FROM xm.user_preferences WHERE user_id = %s', (user_key,)).fetchone()
    terms = (row['preferences'] if row else {}).get('search_terms')
    return [item for item in terms if isinstance(item, str) and item.strip()] if isinstance(terms, list) else None


def effective_terms(role, company, personal):
    """Company keywords win when locked or when a member has no personal list."""
    if role == 'user' and not company['search_locked'] and personal:
        return personal
    return list(company['search_terms'])


@router.get('/company/settings')
def get_company_settings(request: Request):
    user = current_user(request)
    with connect() as conn:
        company = company_row(conn)
        mine = personal_terms(conn, owner_id() or user['id'])
        phones = tracked_phones(conn, workspace_id())
    return {
        'company_id': workspace_id(), 'company_name': company['company_name'], 'listing_group_by': company['listing_group_by'],
        'matching_mode': company['matching_mode'],
        'search_locked': company['search_locked'], 'search_terms': company['search_terms'],
        'personal_terms': mine, 'effective_terms': effective_terms(user['role'], company, mine),
        'tracked_phones': phones, 'role': user['role'],
        'permissions': permissions(user['role'], company['search_locked'], user.get('features')),
    }


class CompanyUpdate(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=120)
    listing_group_by: Literal['sender', 'phone'] | None = None
    search_locked: bool | None = None
    matching_mode: Literal['company', 'sales'] | None = None


@router.put('/company/settings')
def put_company_settings(payload: CompanyUpdate, request: Request):
    changes = payload.model_dump(exclude_none=True)
    if 'company_name' in changes:
        changes['company_name'] = changes['company_name'].strip()
        if not changes['company_name']:
            raise HTTPException(400, 'Company name is required.')
    if changes:
        # Column names come from the fixed model above, never from the client.
        sets = ', '.join(f'{column} = %s' for column in changes)
        with connect() as conn:
            if 'matching_mode' in changes:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042026))")
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")
            previous = company_row(conn)
            if changes.get('matching_mode') == 'sales':
                from matching_scope import load_matching_scope
                load_matching_scope(conn, workspace_id(), 'sales')
            conn.execute(f"UPDATE xm.app_preferences SET {sets}, updated_at = now() WHERE company_id = current_setting('xm.workspace_id')",
                         tuple(changes.values()))
            if 'matching_mode' in changes and changes['matching_mode'] != previous['matching_mode']:
                from matching_scope import mark_reprocess_pending
                mark_reprocess_pending(conn, 'Matching mode changed')
            conn.commit()
        invalidate_stats(workspace_id())
    return get_company_settings(request)


# --- platform administrator --------------------------------------------------

class NewCompany(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    admin_name: str = Field(min_length=1, max_length=100)
    admin_email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN, max_length=200)


class RenameCompany(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CompanyStatus(BaseModel):
    status: Literal['active', 'inactive', 'rejected']


class ResetPassword(BaseModel):
    user_id: uuid.UUID | None = None


USER_FIELDS = ('id', 'email', 'display_name', 'role', 'is_locked', 'created_at', 'last_login_at', 'must_change_password')


def _companies(conn, only=None):
    rows = conn.execute(
        """WITH ids AS (SELECT DISTINCT workspace_id AS company_id FROM xm.users WHERE workspace_id IS NOT NULL AND role <> 'admin'
                        UNION SELECT company_id FROM xm.app_preferences WHERE company_id LIKE 'xm-co-%%')
           SELECT i.company_id, ap.company_name, ap.search_locked, ap.listing_group_by, coalesce(ap.status, 'active') AS status,
                  ap.registered_at, ap.reviewed_at, ap.contact_email,
                  (SELECT count(*) FROM xm.document_groups g WHERE g.company_id = i.company_id AND g.document_type = 'buyer_request' AND g.status <> 'deleted') AS buyers,
                  (SELECT count(*) FROM xm.document_groups g WHERE g.company_id = i.company_id AND g.document_type = 'property_listing' AND g.status <> 'deleted') AS listings,
                  (SELECT max(created_at) FROM xm.activity_log a WHERE a.company_id = i.company_id) AS last_activity_at,
                  (SELECT count(*) FROM xm.glossary gl WHERE gl.company_id = i.company_id) AS glossary_entries,
                  (SELECT coalesce(jsonb_array_length(li.data->'clusters'), 0) FROM xm.location_indexes li WHERE li.company_id = i.company_id) AS clusters
           FROM ids i LEFT JOIN xm.app_preferences ap ON ap.company_id = i.company_id
           WHERE (%s::text IS NULL OR i.company_id = %s)
           ORDER BY (coalesce(ap.status, 'active') = 'pending') DESC, ap.company_name NULLS LAST, i.company_id""", (only, only)).fetchall()
    users = conn.execute(
        """SELECT id, email, display_name, role, is_locked, created_at, last_login_at, must_change_password, workspace_id FROM xm.users
           WHERE workspace_id IS NOT NULL AND role <> 'admin' ORDER BY (role = 'user'), created_at""").fetchall()
    by_company = {}
    for user in users:
        by_company.setdefault(user['workspace_id'], []).append({key: user[key] for key in USER_FIELDS})
    result = []
    for row in rows:
        members = by_company.get(row['company_id'], [])
        # The account the platform admin acts as when entering this company's workspace.
        owner = next((u for u in members if u['role'] == 'company_admin'), None) or (members[0] if members else None)
        result.append({**row, 'name': row['company_name'] or row['company_id'], 'users': members,
                       'owner_id': owner['id'] if owner else None})
    return result


def _company(conn, company_id):
    rows = _companies(conn, company_id)
    if not rows:
        raise HTTPException(404, 'Company not found.')
    return rows[0]


@router.get('/admin/companies')
def list_companies():
    with connect() as conn:
        return _companies(conn)


@router.post('/admin/companies', status_code=201)
def create_company(payload: NewCompany, request: Request):
    me = current_user(request)
    company_id = 'xm-co-' + str(uuid.uuid4())
    try:
        with connect() as conn:
            conn.execute(
                """INSERT INTO xm.users(id, email, display_name, password_hash, role, workspace_id)
                   VALUES (%s, %s, %s, %s, 'company_admin', %s)""",
                (uuid.uuid4(), payload.admin_email.strip().lower(), payload.admin_name.strip(),
                 _password_hash(payload.password), company_id))
            provision_company(conn, company_id, payload.name.strip())
            conn.execute("UPDATE xm.app_preferences SET status='active', reviewed_at=now(), reviewed_by=%s, contact_email=%s WHERE company_id=%s",
                         (me['id'], payload.admin_email.strip().lower(), company_id))
            activity.record(conn, 'company_created', company_id=company_id, user=me, detail={'company_name': payload.name.strip()})
            conn.commit()
            return _company(conn, company_id)
    except UniqueViolation:
        raise HTTPException(409, 'This email is already registered.')


@router.put('/admin/companies/{company_id}')
def rename_company(company_id: str, payload: RenameCompany, request: Request):
    me = current_user(request)
    with connect() as conn:
        updated = conn.execute('UPDATE xm.app_preferences SET company_name = %s, updated_at = now() WHERE company_id = %s RETURNING company_id',
                               (payload.name.strip(), company_id)).fetchone()
        if not updated:
            raise HTTPException(404, 'Company not found.')
        activity.record(conn, 'company_renamed', company_id=company_id, user=me, detail={'company_name': payload.name.strip()})
        conn.commit()
        return _company(conn, company_id)


@router.post('/admin/companies/{company_id}/status')
def set_company_status(company_id: str, payload: CompanyStatus, request: Request):
    """Approve a registration (active), reject it, or pause / resume a company. Paused companies lose their sessions."""
    me = current_user(request)
    with connect() as conn:
        before = conn.execute("SELECT coalesce(status,'active') AS status FROM xm.app_preferences WHERE company_id=%s FOR UPDATE", (company_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Company not found.')
        if payload.status == 'rejected' and before['status'] != 'pending':
            raise HTTPException(400, 'Only a pending registration can be rejected.')
        conn.execute("UPDATE xm.app_preferences SET status=%s, reviewed_at=now(), reviewed_by=%s, updated_at=now() WHERE company_id=%s",
                     (payload.status, me['id'], company_id))
        if payload.status != 'active':
            conn.execute("DELETE FROM xm.sessions WHERE user_id IN (SELECT id FROM xm.users WHERE workspace_id=%s AND role<>'admin')", (company_id,))
        action = {'active': 'company_approved' if before['status'] == 'pending' else 'company_activated',
                  'inactive': 'company_deactivated', 'rejected': 'company_rejected'}[payload.status]
        activity.record(conn, action, company_id=company_id, user=me, detail={'from': before['status'], 'to': payload.status})
        conn.commit()
        return _company(conn, company_id)


@router.post('/admin/companies/{company_id}/reset-password')
def reset_company_password(company_id: str, payload: ResetPassword, request: Request):
    """A temporary password for a company account (its admin by default). Shown once; the account must choose
    its own password at the next sign-in."""
    me = current_user(request)
    with connect() as conn:
        if payload.user_id:
            target = conn.execute("SELECT * FROM xm.users WHERE id=%s AND workspace_id=%s AND role<>'admin' FOR UPDATE",
                                  (payload.user_id, company_id)).fetchone()
        else:
            target = conn.execute("""SELECT * FROM xm.users WHERE workspace_id=%s AND role<>'admin'
                                     ORDER BY (role='company_admin') DESC, created_at LIMIT 1 FOR UPDATE""", (company_id,)).fetchone()
        if not target:
            raise HTTPException(404, 'Account not found in this company.')
        password = temporary_password()
        conn.execute('UPDATE xm.users SET password_hash=%s, must_change_password=true WHERE id=%s', (_password_hash(password), target['id']))
        conn.execute('DELETE FROM xm.sessions WHERE user_id=%s', (target['id'],))
        activity.record(conn, 'password_reset', company_id=company_id, user=me, detail={'target_email': target['email'], 'target_id': str(target['id'])})
        conn.commit()
    return {'user_id': target['id'], 'email': target['email'], 'display_name': target['display_name'], 'password': password}
