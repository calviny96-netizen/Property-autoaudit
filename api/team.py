"""Accounts inside one company. A company admin manages members and other company admins and chooses
which menus / features each member may open (``features``). The platform admin may do the same."""
import hashlib
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from psycopg.errors import UniqueViolation

import activity
from access import DEFAULT_MEMBER_FEATURES, FEATURE_KEYS, FEATURES, member_features
from auth import COOKIE_NAME, PASSWORD_MIN, _password_hash, current_user, public_user
from db import connect

router = APIRouter(prefix='/team')


class NewMember(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=PASSWORD_MIN, max_length=200)
    role: Literal['user', 'company_admin'] = 'user'
    features: list[str] | None = None


class EditMember(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=PASSWORD_MIN, max_length=200)
    is_locked: bool = False
    role: Literal['user', 'company_admin'] | None = None
    features: list[str] | None = None


def can_edit(requester_role: str, target_role: str) -> bool:
    """Company admins manage members and other company admins; the platform admin too. Nobody edits a platform admin."""
    if target_role == 'admin':
        return False
    return requester_role in ('admin', 'company_admin')


def manages_team(user) -> bool:
    """A company admin, the platform admin, or a member granted the Team feature."""
    return 'team' in member_features(user)


def _clean_features(values, role):
    if role != 'user':
        return None  # company admins always have everything
    if values is None:
        return None
    unknown = [f for f in values if f not in FEATURE_KEYS]
    if unknown:
        raise HTTPException(400, f'Unknown feature: {unknown[0]}')
    return [key for key in FEATURE_KEYS if key in set(values)]


def _row(user, me):
    return {**public_user(user), 'created_at': user.get('created_at'), 'last_login_at': user.get('last_login_at'),
            'features': sorted(member_features(user), key=FEATURE_KEYS.index),
            'editable': can_edit(me['role'], user['role']) or (me['role'] == 'user' and user['role'] == 'user' and manages_team(me)),
            'is_self': user['id'] == me['id']}


@router.get('/features')
def list_features():
    return {'features': [{'id': key, 'label': label, 'description': text} for key, label, text in FEATURES],
            'default': list(DEFAULT_MEMBER_FEATURES)}


@router.get('/users')
def list_members(request: Request):
    me = current_user(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, email, display_name, role, is_locked, created_at, last_login_at, must_change_password, features FROM xm.users
               WHERE workspace_id = current_setting('xm.workspace_id')
               ORDER BY (role = 'user'), created_at""").fetchall()
    return [_row(row, me) for row in rows]


@router.post('/users', status_code=201)
def add_member(payload: NewMember, request: Request):
    me = current_user(request)
    if payload.role != 'user' and me['role'] == 'user':
        raise HTTPException(403, 'Only a company admin can add other company admins.')
    if not payload.display_name.strip():
        raise HTTPException(400, 'Name is required.')
    features = _clean_features(payload.features, payload.role)
    try:
        with connect() as conn:
            user = conn.execute(
                """INSERT INTO xm.users(id, email, display_name, password_hash, role, workspace_id, features)
                   VALUES (%s, %s, %s, %s, %s, current_setting('xm.workspace_id'), %s::jsonb) RETURNING *""",
                (uuid.uuid4(), payload.email.strip().lower(), payload.display_name.strip(),
                 _password_hash(payload.password), payload.role, _json(features))).fetchone()
            conn.commit()
            return _row(user, me)
    except UniqueViolation:
        raise HTTPException(409, 'This email is already registered.')


@router.put('/users/{user_id}')
def edit_member(user_id: uuid.UUID, payload: EditMember, request: Request):
    me = current_user(request)
    if not payload.display_name.strip():
        raise HTTPException(400, 'Name is required.')
    try:
        with connect() as conn:
            target = conn.execute(
                "SELECT * FROM xm.users WHERE id = %s AND workspace_id = current_setting('xm.workspace_id') FOR UPDATE",
                (user_id,)).fetchone()
            if not target:
                raise HTTPException(404, 'Account not found.')
            if not (can_edit(me['role'], target['role']) or (me['role'] == 'user' and target['role'] == 'user')):
                raise HTTPException(403, 'You cannot change this account.')
            role = payload.role or target['role']
            if role != target['role'] and me['role'] == 'user':
                raise HTTPException(403, 'Only a company admin can change account roles.')
            is_self = target['id'] == me['id']
            if is_self and (payload.is_locked or role != target['role']):
                raise HTTPException(400, 'You cannot lock or demote your own account.')
            email = payload.email.strip().lower()
            features = _clean_features(payload.features, role) if (payload.features is not None or role != target['role']) else target['features']
            # A password set by someone else is temporary: the member chooses their own at the next sign-in.
            must_change = bool(payload.password) and not is_self
            updated = conn.execute(
                """UPDATE xm.users SET email = %s, display_name = %s, is_locked = %s, password_hash = %s, role = %s,
                       features = %s::jsonb, must_change_password = CASE WHEN %s THEN true WHEN %s THEN false ELSE must_change_password END
                   WHERE id = %s RETURNING *""",
                (email, payload.display_name.strip(), payload.is_locked,
                 _password_hash(payload.password) if payload.password else target['password_hash'], role,
                 _json(features), must_change, bool(payload.password) and is_self, user_id)).fetchone()
            if payload.password or email != target['email']:
                # Everyone else signs in again; your own current session continues.
                token = request.cookies.get(COOKIE_NAME) or '' if is_self else ''
                conn.execute('DELETE FROM xm.sessions WHERE user_id = %s AND token_hash <> %s',
                             (user_id, hashlib.sha256(token.encode()).hexdigest()))
            if payload.password and not is_self:
                activity.record(conn, 'password_reset', company_id=target['workspace_id'], user=me,
                                detail={'target_email': email, 'target_id': str(user_id)})
            conn.commit()
            return _row(updated, me)
    except UniqueViolation:
        raise HTTPException(409, 'This email is already registered.')


def _json(value):
    import json
    return None if value is None else json.dumps(value)
