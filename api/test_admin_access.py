"""Access policy and account lifecycle regressions; DB tests use an isolated DB."""
import asyncio
import os
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException, Response
from starlette.requests import Request

from access import MEMBER_WRITES, permissions, required_role, role_allows
from harness import ServerTestCase
from search_terms import normalize_terms, search_filter


def request(path='/', method='GET', cookie=''):
    return Request({'type': 'http', 'path': path, 'method': method,
                    'headers': [(b'cookie', cookie.encode())], 'query_string': b''})


def person(role, locked=False, workspace='xm-test'):
    return {'id': uuid.UUID(int=1), 'workspace_id': workspace, 'role': role, 'is_locked': locked}


class AccessPolicyTests(unittest.TestCase):
    def check(self, user, path, method='GET'):
        from app import require_login

        async def next_handler(_request):
            return Response(status_code=204)
        with patch('app.current_user', return_value=user):
            return asyncio.run(require_login(request(path, method), next_handler)).status_code

    COMPANY_ADMIN_ONLY = [('/settings', 'PUT'), ('/glossary', 'PUT'), ('/search-default', 'PUT'), ('/imports', 'POST'),
                          ('/index/recompute', 'POST'), ('/matches/recompute', 'POST'), ('/locations/import', 'POST'),
                          ('/location-index/import', 'POST'), ('/company/settings', 'PUT'), ('/stock/tracked', 'PUT'),
                          ('/stock/snapshot', 'POST'), ('/team/users', 'GET'), ('/team/users', 'POST'), ('/team/users/123', 'PUT'),
                          ('/reprocess', 'POST'), ('/stock/tracked/item', 'POST'), ('/stock/tracked/item', 'PATCH'),
                          ('/stock/tracked/item', 'DELETE'), ('/glossary/entries', 'PUT'), ('/location-index/clusters', 'POST'),
                          ('/location-index/clusters/x', 'DELETE'), ('/location-index/aliases', 'POST'),
                          ('/location-index/aliases', 'DELETE'), ('/location-index/edges', 'POST'), ('/location-index/edges', 'DELETE')]
    PLATFORM_ONLY = [('/admin/companies', 'GET'), ('/admin/companies', 'POST'), ('/admin/companies/x', 'PUT'),
                     ('/admin/deploy', 'GET'), ('/admin/deploy', 'POST')]
    MEMBER_OK = [('/settings', 'GET'), ('/search-default', 'GET'), ('/workspace', 'GET'), ('/workspace/groups', 'GET'),
                 ('/workspace/recommendations', 'POST'), ('/export/pdf', 'POST'), ('/preferences', 'PUT'),
                 ('/entities/status', 'POST'), ('/entities/lookup', 'GET'), ('/dashboard/overview', 'GET'),
                 ('/stock/overview', 'GET'), ('/stock/log', 'GET'), ('/matches/recent', 'GET'), ('/matches/recent/seen', 'POST'),
                 ('/company/settings', 'GET'), ('/search-default/personal', 'PUT'), ('/auth/password', 'PUT'),
                 ('/reprocess/status', 'GET'), ('/glossary/entries', 'GET'), ('/glossary/suggestions', 'GET'),
                 ('/location-index/areas', 'GET')]

    def test_member_cannot_write_company_data_or_manage_accounts(self):
        for path, method in self.COMPANY_ADMIN_ONLY:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.check(person('user'), path, method), 403)
                self.assertEqual(self.check(person('company_admin'), path, method), 204)
                self.assertEqual(self.check(person('admin', workspace='xm'), path, method), 204)

    def test_only_platform_admin_manages_companies(self):
        for path, method in self.PLATFORM_ONLY:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.check(person('user'), path, method), 403)
                self.assertEqual(self.check(person('company_admin'), path, method), 403)
                self.assertEqual(self.check(person('admin', workspace='xm'), path, method), 204)

    def test_active_member_can_read_match_and_mark_status(self):
        for path, method in self.MEMBER_OK:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.check(person('user'), path, method), 204)

    def test_locked_account_cannot_access_data_even_as_admin(self):
        for role in ('admin', 'company_admin', 'user'):
            user = person(role, locked=True)
            for path, method in [('/stats', 'GET'), ('/settings', 'GET'), ('/workspace', 'GET'), ('/entities/status', 'POST'),
                                 ('/workspace/recommendations', 'POST'), ('/export/pdf', 'POST'), ('/team/users', 'GET')]:
                self.assertEqual(self.check(user, path, method), 403)
            self.assertEqual(self.check(user, '/auth/me'), 204)
            self.assertEqual(self.check(user, '/auth/logout', 'POST'), 204)
        self.assertEqual(self.check(None, '/workspace'), 401)

    def test_company_admin_cannot_enter_another_account_workspace(self):
        from app import require_login

        async def next_handler(_request):
            return Response(status_code=204)
        scope = {'type': 'http', 'path': '/workspace', 'method': 'GET', 'query_string': b'',
                 'headers': [(b'x-xm-user-id', str(uuid.uuid4()).encode())]}
        for role in ('user', 'company_admin'):
            with patch('app.current_user', return_value=person(role)):
                self.assertEqual(asyncio.run(require_login(Request(scope), next_handler)).status_code, 403)

    def test_role_matrix_and_permissions(self):
        self.assertEqual(required_role('GET', '/anything'), 'user')
        self.assertEqual(required_role('POST', '/unknown-write'), 'company_admin')
        self.assertEqual(required_role('GET', '/admin/companies/'), 'admin')
        self.assertEqual(required_role('GET', '/team'), 'company_admin')
        for method, path in MEMBER_WRITES:
            self.assertEqual(required_role(method, path), 'user', (method, path))
        self.assertTrue(role_allows('admin', 'company_admin'))
        self.assertFalse(role_allows('user', 'company_admin'))
        self.assertFalse(role_allows('company_admin', 'admin'))
        self.assertTrue(permissions('company_admin')['manage_team'])
        self.assertFalse(permissions('user')['manage_team'])
        self.assertTrue(permissions('user', search_locked=False)['edit_personal_search'])
        self.assertFalse(permissions('user', search_locked=True)['edit_personal_search'])

    def test_multi_phrase_validation_and_literal_wildcards(self):
        self.assertEqual(normalize_terms(' XM Darmo, XM Citraland\nXM Darmo;;'), ['XM Darmo', 'XM Citraland'])
        self.assertEqual(search_filter('100%_sale')[1][0], [r'%100\%\_sale%'])
        with self.assertRaises(HTTPException) as raised:
            normalize_terms(['x' * 201])
        self.assertEqual(raised.exception.detail, 'Use at most 20 keywords, each up to 200 characters.')
        with self.assertRaises(HTTPException):
            normalize_terms([str(i) for i in range(21)])


class AccountLifecycleTests(ServerTestCase):
    def test_company_admin_manages_members_end_to_end(self):
        company, admin_user, admin = self.make_company('Lifecycle')
        self.assertEqual(admin_user['role'], 'company_admin')
        email = f'isol-{uuid.uuid4().hex}@example.com'
        member = self.call(admin, '/team/users', 'POST', {'email': email, 'display_name': 'Sales Satu', 'password': 'member-pass-123'}, status=201)
        self.assertEqual(member['role'], 'user')
        self.assertNotIn('password_hash', member)
        self.call(admin, '/team/users', 'POST', {'email': email, 'display_name': 'Dup', 'password': 'member-pass-123'}, status=409)
        session = self.client(email, 'member-pass-123')
        self.assertFalse(self.call(session, '/auth/me')['is_locked'])
        self.assertEqual(self.call(session, '/auth/me')['company_name'], 'Isolation Lifecycle')
        # Lock: takes effect on the member's very next request.
        self.call(admin, f"/team/users/{member['id']}", 'PUT',
                  {'email': email, 'display_name': 'Sales Satu', 'is_locked': True}, status=200)
        self.assertEqual(self.call(session, '/stats', status=403)['code'], 'account_locked')
        self.assertTrue(self.call(session, '/auth/me')['is_locked'])
        self.call(admin, f"/team/users/{member['id']}", 'PUT', {'email': email, 'display_name': 'Sales Satu', 'is_locked': False})
        self.call(session, '/stats')
        # Password reset signs the member out and the old password stops working.
        self.call(admin, f"/team/users/{member['id']}", 'PUT',
                  {'email': email, 'display_name': 'Sales Dua', 'password': 'new-pass-456', 'is_locked': False})
        self.call(session, '/auth/me', status=401)
        self.call(self.admin, '/auth/login', 'POST', {'email': email, 'password': 'member-pass-123'}, status=401)
        renewed = self.client(email, 'new-pass-456')
        self.assertEqual(self.call(renewed, '/auth/me')['display_name'], 'Sales Dua')
        listing = self.call(admin, '/team/users')
        self.assertEqual({row['email'] for row in listing}, {admin_user['email'], email})
        self.assertTrue(next(row for row in listing if row['email'] == email)['editable'])
        self.assertTrue(next(row for row in listing if row['email'] == admin_user['email'])['editable'])  # v5.0: admins edit their own name

    def test_role_boundaries_between_roles_and_companies(self):
        _, a_user, a_admin = self.make_company('A')
        _, b_user, b_admin = self.make_company('B')
        member, member_client = self.make_member(a_admin, 'Member')
        other, _ = self.make_member(b_admin, 'Other company member')
        # Members cannot manage the team or company settings.
        self.call(member_client, '/team/users', status=403)
        self.call(member_client, '/company/settings', 'PUT', {'listing_group_by': 'phone'}, status=403)
        # v5.0: a company admin may add and promote other company admins and edit its own name, but never
        # reach another company's accounts; members never change roles.
        self.call(a_admin, '/team/users', 'POST', {'email': f'isol-{uuid.uuid4().hex}@example.com', 'display_name': 'Boss',
                                                   'password': 'member-pass-123', 'role': 'company_admin'}, status=201)
        self.call(a_admin, f"/team/users/{member['id']}", 'PUT',
                  {'email': member['email'], 'display_name': 'M', 'role': 'company_admin'})
        self.call(a_admin, f"/team/users/{member['id']}", 'PUT', {'email': member['email'], 'display_name': 'M', 'role': 'user'})
        self.assertEqual(self.call(a_admin, f"/team/users/{a_user['id']}", 'PUT', {'email': a_user['email'], 'display_name': 'Me'})['display_name'], 'Me')
        self.call(a_admin, f"/team/users/{a_user['id']}", 'PUT', {'email': a_user['email'], 'display_name': 'Me', 'is_locked': True}, status=400)
        self.call(a_admin, f"/team/users/{other['id']}", 'PUT', {'email': other['email'], 'display_name': 'X'}, status=404)
        self.assertEqual([row['email'] for row in self.call(b_admin, '/team/users')][-1], other['email'])
        # Neither of them reaches platform routes.
        self.call(a_admin, '/admin/companies', status=403)
        self.call(member_client, '/admin/companies', status=403)
        # The platform administrator can add and promote inside a chosen company.
        promoted = self.call(self.admin, f"/team/users/{member['id']}", 'PUT',
                             {'email': member['email'], 'display_name': 'Member', 'role': 'company_admin'}, owner=a_user)
        self.assertEqual(promoted['role'], 'company_admin')
        self.call(member_client, '/team/users')

    def test_platform_admin_company_management(self):
        company, admin_user, _ = self.make_company('Platform')
        listing = self.call(self.admin, '/admin/companies')
        mine = next(item for item in listing if item['company_id'] == company['company_id'])
        self.assertEqual(mine['name'], 'Isolation Platform')
        self.assertEqual(mine['owner_id'], admin_user['id'])
        renamed = self.call(self.admin, f"/admin/companies/{company['company_id']}", 'PUT', {'name': 'Nama Baru'})
        self.assertEqual(renamed['name'], 'Nama Baru')
        self.call(self.admin, '/admin/companies/xm-co-missing', 'PUT', {'name': 'X'}, status=404)
        self.call(self.admin, '/admin/companies', 'POST', {'name': 'Dup', 'admin_name': 'Dup', 'admin_email': admin_user['email'],
                                                           'password': 'isolation-user-pass'}, status=409)

    def test_own_password_change(self):
        _, user, client = self.make_company('Password')
        self.call(client, '/auth/password', 'PUT', {'current_password': 'wrong-pass', 'new_password': 'brand-new-pass'}, status=400)
        self.call(client, '/auth/password', 'PUT', {'current_password': 'isolation-user-pass', 'new_password': 'brand-new-pass'}, status=204)
        self.call(client, '/auth/me')
        self.client(user['email'], 'brand-new-pass')


@unittest.skipUnless(os.getenv('XM_TEST_DATABASE_URL'), 'requires isolated XM_TEST_DATABASE_URL')
class AccountDatabaseTests(unittest.TestCase):
    def setUp(self):
        import auth
        import workspace
        from test_workspace import connect_test_db
        from pathlib import Path
        self.auth, self.workspace, self.connect = auth, workspace, connect_test_db
        self.patches = [patch.object(module, 'connect', connect_test_db) for module in (auth, workspace)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        with self.connect() as conn:
            conn.execute(Path(__file__).with_name('schema.sql').read_text())
        self.email = 'test-' + uuid.uuid4().hex + '@example.com'
        self.addCleanup(self.cleanup)

    def cleanup(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM xm.users WHERE email LIKE 'test-%@example.com'")
            conn.execute("DELETE FROM xm.match_settings WHERE company_id LIKE 'xm-user-%' AND company_id NOT IN (SELECT workspace_id FROM xm.users WHERE workspace_id IS NOT NULL)")
            conn.execute("DELETE FROM xm.app_preferences WHERE company_id LIKE 'xm-user-%' AND company_id NOT IN (SELECT workspace_id FROM xm.users WHERE workspace_id IS NOT NULL)")

    def login(self, email, password):
        response = Response()
        user = self.auth.login(self.auth.LoginRequest(email=email, password=password), request('/auth/login', 'POST'), response)
        cookie = response.headers['set-cookie'].split(';')[0]
        return user, request(cookie=cookie)

    def test_admin_migration_idempotent(self):
        from tenant import provision_workspace
        a = self.auth
        with self.connect() as conn:
            row = conn.execute("INSERT INTO xm.users(id,email,display_name,password_hash) VALUES (%s,%s,'Legacy',%s) RETURNING id",
                               (uuid.uuid4(), self.email, a._password_hash('old-pass-123'))).fetchone()
            provision_workspace(conn, row['id'])
            conn.commit()
        with patch.dict(os.environ, {'XM_ADMIN_EMAIL': self.email, 'XM_ADMIN_PASSWORD': 'secret123'}):
            a.seed_admin()
            user, session = self.login(self.email, 'secret123')
            self.assertEqual(user['role'], 'admin')
            a.seed_admin()
            self.assertIsNotNone(a.current_user(session))

    def test_search_defaults_shared_and_persisted(self):
        w = self.workspace
        original = w.get_search_default()
        try:
            saved = w.save_search_default(w.SearchDefault(search='XM Darmo\nXM Citraland, Pakuwon'))
            self.assertEqual(saved['terms'], ['XM Darmo', 'XM Citraland', 'Pakuwon'])
            self.assertEqual(w.get_search_default(), saved)
            with self.assertRaises(HTTPException):
                w.save_search_default(w.SearchDefault(search='  , '))
            self.assertEqual(w.get_search_default(), saved)
        finally:
            w.save_search_default(w.SearchDefault(terms=original['terms']))


if __name__ == '__main__':
    unittest.main()
