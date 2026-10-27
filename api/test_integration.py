"""Real HTTP/worker/PDF tests against an isolated PostgreSQL database."""
import hashlib
import io
import json
import tempfile
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from db import connect
from integration import cleanup_expired, process_next_export
from test_v4_features import V4Case, BUYER_HOUSE, BUYER_RUKO, LISTING_HOUSE_A, LISTING_HOUSE_B, LISTING_RUKO


class WorkflowIntegrationCase(V4Case):
    def setUp(self):
        super().setUp()
        self.disk = tempfile.TemporaryDirectory()
        self.addCleanup(self.disk.cleanup)
        self.files = Path(self.disk.name) / 'files'
        for item in [patch('integration.FILE_DIR', self.files), patch('export_all.EXPORT_DIR', Path(self.disk.name) / 'parts')]:
            item.start()
            self.addCleanup(item.stop)
        self.key = self.call(self.boss, '/integration/keys', 'POST', {'name': 'Workflow Builder'}, status=201)
        self.workflow = self.bearer(self.key['token'])

    def bearer(self, token):
        client = urllib.request.build_opener()
        client.addheaders = [('Authorization', 'Bearer ' + token)]
        return client

    def seed(self):
        return self.run_import([(BUYER_HOUSE, '~ Rina'), (BUYER_RUKO, '~ Tono'),
                                (LISTING_HOUSE_A, '~ Andi'), (LISTING_HOUSE_B, '~ Andi'), (LISTING_RUKO, '~ Citra')])

    def queue(self, **extra):
        return self.call(self.workflow, '/integration/exports', 'POST',
                         {'request_id': 'workflow-1:2026-10-09:08:00', 'group_by': 'sender', **extra}, status=202)

    def status(self, job):
        return self.call(self.workflow, f"/integration/exports/{job['job_id']}")


class WorkflowIntegrationTests(WorkflowIntegrationCase):
    def test_admin_generates_scoped_key_raw_secret_not_stored_and_revocation(self):
        self.assertEqual(self.key['scope'], 'property:export')
        self.call(self.staff, '/integration/keys', 'POST', {'name': 'no'}, status=403)
        self.call(self.staff, '/integration/keys', status=403)
        listed = self.call(self.boss, '/integration/keys')['keys']
        self.assertNotIn('token', listed[0])
        with connect() as conn:
            row = conn.execute('SELECT token_hash FROM xm.integration_keys WHERE id=%s', (self.key['id'],)).fetchone()
        self.assertEqual(row['token_hash'], hashlib.sha256(self.key['token'].encode()).hexdigest())
        self.call(self.workflow, '/workspace', status=401)
        self.call(self.boss, '/integration/exports', 'POST', {'request_id': 'browser'}, status=401)
        self.call(self.boss, f"/integration/keys/{self.key['id']}", 'DELETE')
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'revoked'}, status=401)

    def test_job_idempotency_and_conflicting_request(self):
        one = self.queue()
        two = self.queue()
        self.assertEqual(one['job_id'], two['job_id'])
        self.call(self.workflow, '/integration/exports', 'POST',
                  {'request_id': one['request_id'], 'group_by': 'phone'}, status=409)
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'invalid', 'recipients': {'x': 'bad'}}, status=422)
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'invalid', 'date_from': 'not-a-date'}, status=400)
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'invalid', 'group_by': 'arbitrary'}, status=422)

    def test_worker_produces_individual_pdfs_manifest_and_repeat_downloads(self):
        self.seed()
        job = self.queue(recipients={'~ Andi': '082226811158'})
        self.assertEqual(job['status'], 'queued')
        self.assertTrue(process_next_export())
        ready = self.status(job)
        self.assertEqual(ready['status'], 'completed')
        self.assertEqual(len(ready['files']), 2)
        first, second = ready['files']
        self.assertEqual((first['recipient_phone'], first['recipient_status']), ('6282226811158', 'ready'))
        self.assertEqual((second['recipient_phone'], second['recipient_status']), (None, 'needs_review'))
        self.assertEqual(first['source_count'], 2)
        # Every file is numbered on its own, from page 1.
        self.assertEqual([(f['first_page'], f['last_page']) for f in ready['files']], [(1, f['page_count']) for f in ready['files']])
        self.assertEqual(first['mime'], 'application/pdf')
        path = first['download_url'].removeprefix('/api')
        pdf = self.call(self.workflow, path)
        self.assertEqual(pdf, self.call(self.workflow, path))
        pages = PdfReader(io.BytesIO(pdf)).pages
        self.assertEqual(len(pages), first['page_count'])
        self.assertIn('Andi', pages[0].extract_text())
        self.assertNotIn('~ Andi', pages[0].extract_text())
        self.assertIn('2 listing', pages[0].extract_text())
        self.assertEqual((first['group_key'], first['group_name']), ('~ Andi', 'Andi'))
        self.assertTrue(first['filename'].startswith('01_Andi_'))
        self.assertEqual(ready['progress']['groups_done'], 2)
        self.assertEqual(ready['progress']['pages_done'], sum(f['page_count'] for f in ready['files']))

    def test_selected_groups_only_and_custom_status_is_accepted(self):
        self.seed()
        job = self.queue(group_keys=['~ Citra'], statuses='hot,warm,custom')
        self.assertTrue(process_next_export())
        files = self.status(job)['files']
        self.assertEqual([f['group_key'] for f in files], ['~ Citra'])
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'bad-status', 'statuses': 'hot,lukewarm'}, status=400)
        self.call(self.workflow, '/integration/exports', 'POST',
                  {'request_id': 'too-many', 'group_keys': [str(n) for n in range(501)]}, status=422)

    def test_company_isolation_and_forbidden_owner_override(self):
        _, _, other = self.make_company('Other Integration')
        other_key = self.call(other, '/integration/keys', 'POST', {'name': 'Other'}, status=201)
        other_workflow = self.bearer(other_key['token'])
        job = self.queue()
        self.call(other_workflow, f"/integration/exports/{job['job_id']}", status=404)
        self.call(other_workflow, f"/integration/exports/{job['job_id']}", 'DELETE', status=404)
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'override'}, owner=self.user, status=403)
        self.call(other, f"/integration/keys/{self.key['id']}", 'DELETE', status=404)
        self.seed()
        self.assertTrue(process_next_export())
        first = self.status(job)['files'][0]
        self.call(other_workflow, first['download_url'].removeprefix('/api'), status=404)

    def test_phone_group_routes_by_key_and_sender_mapping_never_uses_representative_phone(self):
        self.seed()
        job = self.queue(group_by='phone')
        self.assertTrue(process_next_export())
        files = self.status(job)['files']
        self.assertEqual(len(files), 3)
        self.assertTrue(all(f['recipient_phone'] == f['group_key'] and f['recipient_status'] == 'ready' for f in files))
        self.assertTrue(all(f['source_count'] == 1 for f in files))

    def test_interrupted_job_is_reclaimed_but_locked_job_is_not(self):
        self.seed()
        job = self.queue()
        with connect() as conn:
            conn.execute("UPDATE xm.export_jobs SET status='processing' WHERE id=%s", (job['job_id'],))
            conn.commit()
            conn.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))', (job['job_id'],))
            self.assertFalse(process_next_export())
            conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (job['job_id'],))
        self.assertTrue(process_next_export())
        self.assertEqual(self.status(job)['status'], 'completed')

    def test_cancel_and_expire(self):
        job = self.queue()
        cancelled = self.call(self.workflow, f"/integration/exports/{job['job_id']}", 'DELETE')
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertFalse(process_next_export())
        self.seed()
        fresh = self.queue(request_id='next-day')
        self.assertTrue(process_next_export())
        path = self.status(fresh)['files'][0]['download_url'].removeprefix('/api')
        with connect() as conn:
            conn.execute("UPDATE xm.export_jobs SET expires_at=now()-interval '1 second' WHERE id=%s", (fresh['job_id'],))
            conn.commit()
        self.call(self.workflow, path, status=410)
        self.assertEqual(self.status(fresh)['status'], 'expired')
        cleanup_expired()
        self.assertFalse((self.files / fresh['job_id']).exists())

    def test_empty_job_completes_and_grouping_is_frozen(self):
        job = self.queue(group_by=None)
        self.call(self.boss, '/company/settings', 'PUT', {'listing_group_by': 'phone'})
        self.assertEqual(self.queue(group_by=None)['job_id'], job['job_id'])
        self.assertTrue(process_next_export())
        self.assertEqual(self.status(job)['files'], [])
        self.assertEqual(self.status(job)['status'], 'completed')

    def test_source_stock_filter_keeps_ready_recommendations(self):
        self.seed()
        source = self.card(self.rows(direction='property'), 'Budi')
        self.call(self.staff, '/entities/status', 'POST', {'ids': [source['public_id']], 'status': 'on_hold'})
        job = self.queue(stock_status='on_hold')
        self.assertTrue(process_next_export())
        files = self.status(job)['files']
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['source_count'], 1)
        pdf = self.call(self.workflow, files[0]['download_url'].removeprefix('/api'))
        text = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn('Buyer request rumah', text)

    def test_locked_key_creator_cannot_access_integration(self):
        job = self.queue()
        with connect() as conn:
            conn.execute('UPDATE xm.users SET is_locked=true WHERE id=%s', (self.user['id'],))
            conn.commit()
        self.call(self.workflow, f"/integration/exports/{job['job_id']}", status=401)

    def test_failure_does_not_publish_partial_files(self):
        self.seed()
        job = self.queue()
        with patch('integration.merge_pdf_parts', side_effect=RuntimeError('simulated merge failure')):
            self.assertTrue(process_next_export())
        failed = self.status(job)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['files'], [])
        self.assertFalse((self.files / job['job_id']).exists())
        self.assertNotIn('simulated', failed['error'])

    def test_running_cancellation_is_not_overwritten_by_completed(self):
        self.seed()
        job = self.queue()
        from integration import update_progress

        def cancel_during_progress(job_id, progress):
            self.call(self.workflow, f'/integration/exports/{job_id}', 'DELETE')
            return update_progress(job_id, progress)

        with patch('integration.update_progress', side_effect=cancel_during_progress):
            self.assertTrue(process_next_export())
        self.assertEqual(self.status(job)['status'], 'cancelled')
        self.assertFalse((self.files / job['job_id']).exists())


class UploadIntegrationTests(WorkflowIntegrationCase):
    def test_upload_scope_status_duplicate_and_company_isolation(self):
        key = self.call(self.boss, '/integration/keys', 'POST',
                        {'name': 'Upload automation', 'scope': 'property:upload'}, status=201)
        client = self.bearer(key['token'])
        boundary = 'upload-integration-test'
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="cleaned.json"'
               '\r\nContent-Type: application/json\r\n\r\n'
               + json.dumps({'salesName': 'Automation', 'chats': {'room': {'name': 'Room',
                   'messages': [['2026-10-15T10:00:00', LISTING_HOUSE_A, 'Agent']]}}})
               + f'\r\n--{boundary}--\r\n').encode()
        def upload(who, status=202):
            return self.call(who, '/integration/imports', 'POST', raw=raw,
                             content_type='multipart/form-data; boundary=' + boundary, status=status)
        upload(self.workflow, 403)
        upload(self.boss, 401)
        job = upload(client)
        self.assertFalse(job['duplicate'])
        self.assertEqual(upload(client)['id'], job['id'])
        self.assertTrue(upload(client)['duplicate'])
        path = '/integration/imports/' + job['id']
        self.assertEqual(self.call(client, path)['company_id'], self.company['company_id'])
        self.process(job)
        self.assertEqual(self.call(client, path)['status'], 'completed')
        self.call(client, '/integration/exports', 'POST', {'request_id': 'denied'}, status=403)
        self.call(client, '/workspace', status=401)
        _, _, other = self.make_company('Other upload')
        other_key = self.call(other, '/integration/keys', 'POST',
                              {'name': 'Other upload', 'scope': 'property:upload'}, status=201)
        self.call(self.bearer(other_key['token']), path, status=404)
        self.call(client, path, owner=self.user, status=403)
        self.call(self.boss, '/integration/keys/' + key['id'], 'DELETE')
        self.call(client, path, status=401)
