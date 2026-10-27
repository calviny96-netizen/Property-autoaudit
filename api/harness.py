"""Shared HTTP + PostgreSQL harness for regression tests (never touches production data).

Starts the real FastAPI app on a free port, wired to ``XM_TEST_DATABASE_URL``.
Each test creates throw-away companies through the platform-admin API and
removes every row they own afterwards.
"""
import http.cookiejar
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

WORKSPACE_TABLES = ('ai_reviews', 'ai_review_runs', 'ai_keys', 'match_deliveries', 'export_jobs', 'integration_keys', 'match_events', 'stock_log', 'tracked_sales', 'group_matches', 'document_group_members', 'document_groups',
                    'workspace_cache_state', 'matches', 'documents', 'entity_aliases', 'entities', 'entity_counters', 'raw_messages', 'imports',
                    'maintenance_jobs', 'glossary', 'location_indexes', 'match_settings', 'app_preferences', 'audit_events', 'activity_log')


@unittest.skipUnless(os.getenv('XM_TEST_DATABASE_URL'), 'requires isolated XM_TEST_DATABASE_URL')
class ServerTestCase(unittest.TestCase):
    ADMIN_EMAIL = 'isolation-admin@example.com'
    ADMIN_PASSWORD = 'isolation-admin-pass'

    @classmethod
    def setUpClass(cls):
        import app
        import uvicorn
        cls.temp = tempfile.TemporaryDirectory()
        cls.patches = [
            patch.dict(os.environ, {'DATABASE_URL': os.environ['XM_TEST_DATABASE_URL'],
                       'XM_ADMIN_EMAIL': cls.ADMIN_EMAIL, 'XM_ADMIN_PASSWORD': cls.ADMIN_PASSWORD}),
            patch.object(app, 'UPLOAD_DIR', Path(cls.temp.name)),
            patch.object(app, 'ensure_collection', lambda: None),
            patch.object(app, 'qdrant_status', lambda **kwargs: {'ok': True}),
            patch.object(app, 'qdrant_query', lambda *args, **kwargs: []),
        ]
        for item in cls.patches:
            item.start()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.base = f'http://127.0.0.1:{port}'
        cls.server = uvicorn.Server(uvicorn.Config(app.app, host='127.0.0.1', port=port, log_level='error'))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 15
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        if not cls.server.started:
            raise RuntimeError('Test server did not start')

    @classmethod
    def tearDownClass(cls):
        from db import connect
        cls.server.should_exit = True
        cls.thread.join(10)
        with connect() as conn:
            conn.execute('DELETE FROM xm.users WHERE email=%s', (cls.ADMIN_EMAIL,))
            conn.commit()
        for item in reversed(cls.patches):
            item.stop()
        cls.temp.cleanup()

    # -- HTTP helpers -------------------------------------------------------
    def client(self, email, password):
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.call(client, '/auth/login', 'POST', {'email': email, 'password': password})
        return client

    def call(self, client, path, method='GET', payload=None, owner=None, status=200, raw=None, content_type=None):
        headers = {'Content-Type': content_type or 'application/json'}
        if owner:
            headers['X-XM-User-Id'] = str(owner['id'] if isinstance(owner, dict) else owner)
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            response = client.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            self.assertEqual(response.status, status, (path, data[:600]))
            if response.headers.get('Content-Type', '').startswith(('application/pdf', 'application/zip', 'application/vnd.openxmlformats')):
                return data
            return json.loads(data) if data else None

    # -- fixtures -----------------------------------------------------------
    def setUp(self):
        self.admin = self.client(self.ADMIN_EMAIL, self.ADMIN_PASSWORD)
        self.companies = []
        self.addCleanup(self.remove_companies)

    def make_company(self, label, password='isolation-user-pass'):
        """A company with one super admin. Returns (company, admin_user, admin_client)."""
        email = f'isol-{uuid.uuid4().hex}@example.com'
        company = self.call(self.admin, '/admin/companies', 'POST', {
            'name': 'Isolation ' + label, 'admin_name': 'Admin ' + label, 'admin_email': email, 'password': password}, status=201)
        self.companies.append(company['company_id'])
        user = {**company['users'][0], 'workspace_id': company['company_id'], 'email': email}
        return company, user, self.client(email, password)

    def make_member(self, admin_client, label='Member', password='member-pass-123'):
        email = f'isol-{uuid.uuid4().hex}@example.com'
        user = self.call(admin_client, '/team/users', 'POST', {'email': email, 'display_name': label, 'password': password}, status=201)
        return user, self.client(email, password)

    def remove_companies(self):
        from db import connect
        with connect() as conn:
            for scope in self.companies:
                for table in WORKSPACE_TABLES:
                    conn.execute(f'DELETE FROM xm.{table} WHERE company_id=%s', (scope,))
                conn.execute('DELETE FROM xm.users WHERE workspace_id=%s', (scope,))
            conn.commit()

    def upload(self, owner, texts=None, agent='Private Agent', day=1, messages=None, matching_mode=None, status=202):
        """Queue a chat export. ``messages`` is a list of (text, author) or (text, author, iso_timestamp)."""
        if messages is None:
            texts = texts or ['Buyer request rumah Surabaya Barat LT 100 Budget 2 M',
                              'Dijual rumah Surabaya Barat LT 100 Harga 1,8 M']
            messages = [(text, agent) for text in texts]
        rows = []
        for index, item in enumerate(messages):
            text, author = item[0], item[1]
            stamp = item[2] if len(item) > 2 else f'2026-09-{day + index:02}T10:00:00'
            rows.append([stamp, text, author])
        data = json.dumps({'chats': {'room': {'name': 'Private chat', 'messages': rows}}})
        boundary = 'xm-isolation-test-boundary'
        mode_field = (f'--{boundary}\r\nContent-Disposition: form-data; name="matching_mode"\r\n\r\n{matching_mode}\r\n'
                      if matching_mode is not None else '')
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="agent_name"\r\n\r\n{agent}\r\n'
               f'{mode_field}'
               f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="cleaned.json"\r\nContent-Type: application/json\r\n\r\n'
               f'{data}\r\n--{boundary}--\r\n').encode()
        return self.call(self.admin, '/imports', 'POST', owner=owner, raw=raw,
                         content_type='multipart/form-data; boundary=' + boundary, status=status)

    def process(self, job):
        import ingest
        import matcher
        points = []

        def record(batch):
            points.extend(batch)
            return len(batch)
        with patch.object(ingest, 'upsert', record), patch.object(matcher, 'qdrant_query', lambda *args, **kwargs: []):
            ingest.process_import(job['id'])
        return points
