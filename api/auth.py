import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

import activity
from db import connect
from tenant import provision_company, provision_workspace


router = APIRouter(prefix="/auth")
COOKIE_NAME = "xm_session"
SESSION_DAYS = 7
# Passwords only need a minimum length (no complexity rules): people read them out over WhatsApp.
PASSWORD_MIN = 6
PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'

USER_SQL = """SELECT u.id, u.email, u.display_name, u.role, u.is_locked, u.workspace_id, u.must_change_password, u.features,
                     ap.company_name, coalesce(ap.status, 'active') AS company_status
              FROM xm.users u LEFT JOIN xm.app_preferences ap ON ap.company_id=u.workspace_id"""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=100)


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def temporary_password(length: int = 10) -> str:
    """A password people can read out: no look-alike characters."""
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def seed_admin() -> None:
    email = os.getenv("XM_ADMIN_EMAIL", "admin@autoaudit.id").strip().lower()
    password = os.getenv("XM_ADMIN_PASSWORD", "secret123")
    with connect() as conn:
        migrated = conn.execute(
            """
            INSERT INTO xm.users(id, email, display_name, password_hash, role)
            VALUES (%s, %s, 'Administrator', %s, 'admin')
            ON CONFLICT (email) DO UPDATE SET role='admin', password_hash=excluded.password_hash,
                is_locked=false WHERE xm.users.role != 'admin'
            RETURNING id
            """,
            (uuid.uuid4(), email, _password_hash(password)),
        ).fetchone()
        if migrated:
            conn.execute('DELETE FROM xm.sessions WHERE user_id=%s', (migrated['id'],))
        # The original archive ('xm') stays with the platform admin only while no company owns it
        # (the admin workspace was handed to a company account in v5.0).
        legacy_owner = conn.execute("SELECT 1 FROM xm.users WHERE workspace_id='xm' AND role<>'admin'").fetchone()
        for user in conn.execute('SELECT id,email,role FROM xm.users').fetchall():
            provision_workspace(conn, user['id'], legacy=user['email'] == email and user['role'] == 'admin' and not legacy_owner)
        conn.commit()


def current_user(request: Request):
    # The access middleware already resolved the account for this request.
    cached = getattr(request.state, 'xm_user', None)
    if cached is not None:
        return cached
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connect() as conn:
        return conn.execute(
            USER_SQL + " JOIN xm.sessions s ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at > now()",
            (token_hash,),
        ).fetchone()


def _client_ip(request: Request):
    forwarded = request.headers.get('X-Real-IP') or request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
    return forwarded or (request.client.host if request.client else None)


def _start_session(conn, user_id, response: Response):
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    conn.execute("DELETE FROM xm.sessions WHERE expires_at <= now()")
    conn.execute(
        "INSERT INTO xm.sessions(id,user_id,token_hash,expires_at) VALUES(%s,%s,%s,%s)",
        (uuid.uuid4(), user_id, hashlib.sha256(token.encode()).hexdigest(), expires_at),
    )
    conn.execute('UPDATE xm.users SET last_login_at=now() WHERE id=%s', (user_id,))
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="strict",
        secure=os.getenv("XM_COOKIE_SECURE", "0") == "1",
        path="/",
    )


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    email = payload.email.strip().lower()
    with connect() as conn:
        user = conn.execute(
            """SELECT u.*, ap.company_name, coalesce(ap.status,'active') AS company_status FROM xm.users u
               LEFT JOIN xm.app_preferences ap ON ap.company_id=u.workspace_id WHERE u.email=%s""", (email,)).fetchone()
        if not user or not _password_matches(payload.password, user["password_hash"]):
            activity.record(conn, 'login_failed', company_id=user['workspace_id'] if user else None,
                            user={'id': user['id'], 'email': email} if user else {'email': email}, ip=_client_ip(request))
            conn.commit()
            raise HTTPException(401, "Incorrect email or password.")
        _start_session(conn, user['id'], response)
        activity.record(conn, 'login', company_id=user['workspace_id'], user=user, ip=_client_ip(request))
        conn.commit()
    return public_user(user)


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, request: Request, response: Response):
    """Self-service: a company and its first admin account. The company waits for the platform admin's approval."""
    if payload.password != payload.confirm_password:
        raise HTTPException(400, 'The passwords do not match.')
    name = payload.company_name.strip()
    if not name:
        raise HTTPException(400, 'Company or brand name is required.')
    email = payload.email.strip().lower()
    company_id = 'xm-co-' + str(uuid.uuid4())
    with connect() as conn:
        if conn.execute('SELECT 1 FROM xm.users WHERE email=%s', (email,)).fetchone():
            raise HTTPException(409, 'This email is already registered. Sign in instead.')
        user = conn.execute(
            """INSERT INTO xm.users(id, email, display_name, password_hash, role, workspace_id)
               VALUES (%s, %s, %s, %s, 'company_admin', %s) RETURNING *""",
            (uuid.uuid4(), email, (payload.display_name or '').strip() or name, _password_hash(payload.password), company_id)).fetchone()
        provision_company(conn, company_id, name)
        conn.execute("""UPDATE xm.app_preferences SET status='pending', registered_at=now(), contact_email=%s
                        WHERE company_id=%s""", (email, company_id))
        _start_session(conn, user['id'], response)
        activity.record(conn, 'register', company_id=company_id, user=user, detail={'company_name': name}, ip=_client_ip(request))
        conn.commit()
    return public_user({**user, 'company_name': name, 'company_status': 'pending'})


@router.get("/me")
def me(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Please sign in.")
    return public_user(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    user = current_user(request)
    if token:
        with connect() as conn:
            conn.execute(
                "DELETE FROM xm.sessions WHERE token_hash=%s",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )
            if user:
                activity.record(conn, 'logout', company_id=user.get('workspace_id'), user=user, ip=_client_ip(request))
            conn.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


def public_user(user):
    data = {key: user.get(key) for key in ('id', 'email', 'display_name', 'role', 'is_locked', 'company_name', 'must_change_password',
                                           'company_status', 'workspace_id')}
    data['company_status'] = data['company_status'] or 'active'
    data['must_change_password'] = bool(data['must_change_password'])
    return data


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=200)


class FirstPassword(BaseModel):
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)


def _replace_password(conn, request: Request, user_id, new_password: str):
    conn.execute('UPDATE xm.users SET password_hash=%s, must_change_password=false WHERE id=%s', (_password_hash(new_password), user_id))
    token = request.cookies.get(COOKIE_NAME) or ''
    # Other devices must sign in again with the new password; this session continues.
    conn.execute('DELETE FROM xm.sessions WHERE user_id=%s AND token_hash<>%s',
                 (user_id, hashlib.sha256(token.encode()).hexdigest()))


@router.put('/password', status_code=204)
def change_password(payload: PasswordChange, request: Request):
    user = current_user(request)
    with connect() as conn:
        row = conn.execute('SELECT password_hash FROM xm.users WHERE id=%s FOR UPDATE', (user['id'],)).fetchone()
        if not row or not _password_matches(payload.current_password, row['password_hash']):
            raise HTTPException(400, 'Your current password is incorrect.')
        _replace_password(conn, request, user['id'], payload.new_password)
        conn.commit()


@router.post('/password/first', status_code=204)
def first_password(payload: FirstPassword, request: Request):
    """After an admin reset: the account signed in with the temporary password and now chooses its own."""
    user = current_user(request)
    if not user:
        raise HTTPException(401, 'Please sign in.')
    if payload.new_password != payload.confirm_password:
        raise HTTPException(400, 'The passwords do not match.')
    with connect() as conn:
        row = conn.execute('SELECT must_change_password FROM xm.users WHERE id=%s FOR UPDATE', (user['id'],)).fetchone()
        if not row or not row['must_change_password']:
            raise HTTPException(400, 'Your password does not need to be changed. Use Settings → My account instead.')
        _replace_password(conn, request, user['id'], payload.new_password)
        activity.record(conn, 'password_changed', company_id=user.get('workspace_id'), user=user, ip=_client_ip(request))
        conn.commit()
