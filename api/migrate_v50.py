"""v5.0 one-off: hand the original archive ('xm' workspace) to a company account and give the platform admin an
empty workspace of its own (the platform admin has no matching features any more).

Run inside the API container, credentials on stdin as JSON so they never appear in a command line or log:
    echo '{"email": "...", "password": "...", "company_name": "Konig", "display_name": "..."}' | docker exec -i xm-api python migrate_v50.py
Idempotent: re-running updates the password / name of the company admin and leaves everything else alone.
"""
import json
import sys
import uuid

from auth import _password_hash
from db import connect, ensure_schema
from tenant import provision_company


def main():
    payload = json.load(sys.stdin)
    email = payload['email'].strip().lower()
    password = payload['password']
    if len(password) < 6:
        raise SystemExit('password too short')
    company_name = (payload.get('company_name') or 'Konig').strip()
    display_name = (payload.get('display_name') or company_name).strip()
    ensure_schema()
    with connect() as conn:
        admin = conn.execute("SELECT id, email, workspace_id FROM xm.users WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()
        existing = conn.execute('SELECT id, role, workspace_id FROM xm.users WHERE email=%s', (email,)).fetchone()
        if existing and existing['role'] == 'admin':
            raise SystemExit('that email is the platform admin')
        if existing:
            conn.execute("""UPDATE xm.users SET password_hash=%s, display_name=%s, role='company_admin', workspace_id='xm',
                            is_locked=false, must_change_password=false, features=NULL WHERE id=%s""",
                         (_password_hash(password), display_name, existing['id']))
            owner_id = existing['id']
        else:
            owner_id = uuid.uuid4()
            conn.execute("""INSERT INTO xm.users(id, email, display_name, password_hash, role, workspace_id)
                            VALUES (%s, %s, %s, %s, 'company_admin', 'xm')""", (owner_id, email, display_name, _password_hash(password)))
        provision_company(conn, 'xm', company_name)
        conn.execute("""UPDATE xm.app_preferences SET company_name=coalesce(nullif(company_name,''), %s), status='active',
                        contact_email=coalesce(contact_email, %s), reviewed_at=coalesce(reviewed_at, now()) WHERE company_id='xm'""",
                     (company_name, email))
        moved = False
        if admin and admin['workspace_id'] == 'xm':
            own = 'xm-user-' + str(admin['id'])
            conn.execute('UPDATE xm.users SET workspace_id=%s WHERE id=%s', (own, admin['id']))
            conn.execute('INSERT INTO xm.match_settings(company_id) VALUES(%s) ON CONFLICT DO NOTHING', (own,))
            conn.execute("INSERT INTO xm.app_preferences(company_id, company_name, status) VALUES(%s, 'Platform admin', 'active') ON CONFLICT DO NOTHING", (own,))
            # Personal keyword lists of the admin belonged to the archive's matching; the company keeps the company list.
            moved = True
        conn.execute("""INSERT INTO xm.activity_log(company_id, user_id, user_email, action, detail)
                        VALUES ('xm', %s, %s, 'company_created', %s::jsonb)""",
                     (admin['id'] if admin else None, admin['email'] if admin else None,
                      json.dumps({'company_name': company_name, 'migrated': True, 'owner_email': email})))
        conn.commit()
    print(json.dumps({'company_id': 'xm', 'company_name': company_name, 'owner_id': str(owner_id), 'owner_email': email,
                      'admin_moved_to_own_workspace': moved}))


if __name__ == '__main__':
    main()
