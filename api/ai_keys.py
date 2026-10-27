"""Jev review: OpenRouter API keys per company, encrypted at rest.

Keys are encrypted with Fernet (``cryptography``). The server secret is ``XM_SECRET_KEY`` or, when that is not set,
a random secret created once in ``/data/xm-secret.key`` (mode 0600) on the data volume that the API and the worker
share. A key is decrypted only by the worker right before it calls OpenRouter; the browser only ever receives the
label and the last four characters, and nothing logs the full key.
"""
import base64
import hashlib
import os
import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from auth import current_user
from db import connect
from tenant import workspace_id

def require_platform_admin(request: Request):
    if (current_user(request) or {}).get('role') != 'admin':
        raise HTTPException(403, 'Only the platform administrator can manage API keys.')


router = APIRouter(prefix='/ai/keys', dependencies=[Depends(require_platform_admin)])
PROVIDER = 'openrouter'
SECRET_FILE = Path(os.getenv('XM_SECRET_KEY_FILE', '/data/xm-secret.key'))
MAX_KEYS = 50


class SecretUnavailable(RuntimeError):
    pass


def _server_secret():
    configured = os.getenv('XM_SECRET_KEY', '').strip()
    if configured:
        return configured
    path = SECRET_FILE
    if not path.exists():
        # Create atomically: the API and the worker may start at the same time.
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f'.{path.name}.{os.getpid()}.{secrets.token_hex(4)}')
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'w') as handle:
                handle.write(secrets.token_urlsafe(48))
            os.chmod(temp, 0o600)
            try:
                os.link(temp, path)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
    value = path.read_text().strip()
    if not value:
        raise SecretUnavailable('The server secret file is empty.')
    return value


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # the image predates the dependency
        raise SecretUnavailable('Encryption is not available on this server yet (the cryptography package is missing).') from exc
    digest = hashlib.sha256(b'xm-ai-keys:' + _server_secret().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    box = _fernet()
    from cryptography.fernet import InvalidToken
    try:
        return box.decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnavailable('The API key could not be decrypted (the server secret changed). Add the key again.') from exc


def mask(last4: str) -> str:
    return '••••' + (last4 or '')


def public_key(row):
    return {'id': row['id'], 'label': row['label'], 'provider': row['provider'], 'last4': row['last4'],
            'masked': mask(row['last4']), 'is_default': row['is_default'], 'created_at': row['created_at'],
            'last_used_at': row.get('last_used_at'), 'created_by_name': row.get('created_by_name')}


KEY_FIELDS = '''k.id,k.label,k.provider,k.last4,k.is_default,k.created_at,k.last_used_at,
  coalesce(nullif(u.display_name,''),u.email) created_by_name'''


def list_rows(conn):
    return conn.execute(f'''SELECT {KEY_FIELDS} FROM xm.ai_keys k LEFT JOIN xm.users u ON u.id=k.created_by
      WHERE k.company_id=%s ORDER BY k.is_default DESC,k.created_at DESC''', (workspace_id(),)).fetchall()


def key_secret(conn, key_id, company_id):
    """(row, decrypted key) of a key of ``company_id``; None when it was deleted."""
    row = conn.execute('SELECT id,secret_encrypted FROM xm.ai_keys WHERE id=%s AND company_id=%s', (key_id, company_id)).fetchone()
    if not row:
        return None
    return row, decrypt(row['secret_encrypted'])


def resolve_key_id(conn, key_id):
    """The chosen key of this company, or its default key. 400 when there is none."""
    if key_id:
        row = conn.execute("SELECT id FROM xm.ai_keys WHERE id=%s AND company_id=current_setting('xm.workspace_id')", (key_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'API key not found.')
        return row['id']
    row = conn.execute("""SELECT id FROM xm.ai_keys WHERE company_id=current_setting('xm.workspace_id')
      ORDER BY is_default DESC,created_at DESC LIMIT 1""").fetchone()
    if not row:
        raise HTTPException(400, 'AI Review is not configured yet. Contact the platform administrator.')
    return row['id']


class NewKey(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=8, max_length=500)
    make_default: bool = False

    @field_validator('label')
    @classmethod
    def clean_label(cls, value):
        if not value.strip():
            raise ValueError('A label is required.')
        return value.strip()

    @field_validator('api_key')
    @classmethod
    def clean_key(cls, value):
        value = value.strip()
        if len(value) < 8 or any(ch.isspace() for ch in value):
            raise ValueError('This does not look like an API key.')
        return value


@router.get('')
def list_keys():
    with connect() as conn:
        return {'keys': [public_key(row) for row in list_rows(conn)]}


@router.post('', status_code=201)
def create_key(payload: NewKey, request: Request):
    try:
        encrypted = encrypt(payload.api_key)
    except SecretUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042031))")
        count = conn.execute('SELECT count(*) n FROM xm.ai_keys WHERE company_id=%s', (workspace_id(),)).fetchone()['n']
        if count >= MAX_KEYS:
            raise HTTPException(400, f'A company can keep at most {MAX_KEYS} API keys. Delete one first.')
        default = payload.make_default or count == 0
        if default:
            conn.execute('UPDATE xm.ai_keys SET is_default=false WHERE company_id=%s AND is_default', (workspace_id(),))
        key_id = uuid.uuid4()
        conn.execute('''INSERT INTO xm.ai_keys(id,company_id,provider,label,secret_encrypted,last4,is_default,created_by)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
          (key_id, workspace_id(), PROVIDER, payload.label, encrypted, payload.api_key[-4:], default, current_user(request)['id']))
        row = next(item for item in list_rows(conn) if item['id'] == key_id)
        conn.commit()
    return public_key(row)


@router.post('/{key_id}/default')
def make_default(key_id: uuid.UUID):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042031))")
        if not conn.execute('SELECT 1 FROM xm.ai_keys WHERE id=%s AND company_id=%s', (key_id, workspace_id())).fetchone():
            raise HTTPException(404, 'API key not found.')
        conn.execute('UPDATE xm.ai_keys SET is_default=false WHERE company_id=%s AND is_default AND id<>%s', (workspace_id(), key_id))
        conn.execute('UPDATE xm.ai_keys SET is_default=true WHERE id=%s AND company_id=%s', (key_id, workspace_id()))
        rows = list_rows(conn)
        conn.commit()
    return {'keys': [public_key(row) for row in rows]}


@router.delete('/{key_id}')
def delete_key(key_id: uuid.UUID):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042031))")
        row = conn.execute('DELETE FROM xm.ai_keys WHERE id=%s AND company_id=%s RETURNING is_default', (key_id, workspace_id())).fetchone()
        if not row:
            raise HTTPException(404, 'API key not found.')
        # Runs still waiting for this key cannot continue.
        conn.execute("""UPDATE xm.ai_review_runs SET status='failed',finished_at=now(),
          error='The API key of this run was deleted.' WHERE company_id=%s AND key_id IS NULL AND status IN ('queued','running')""",
                     (workspace_id(),))
        if row['is_default']:
            conn.execute('''UPDATE xm.ai_keys SET is_default=true WHERE id=(SELECT id FROM xm.ai_keys WHERE company_id=%s
              ORDER BY created_at DESC LIMIT 1)''', (workspace_id(),))
        rows = list_rows(conn)
        conn.commit()
    return {'keys': [public_key(row) for row in rows]}
