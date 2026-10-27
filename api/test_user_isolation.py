"""Real HTTP + PostgreSQL isolation regressions (never runs against production)."""
import concurrent.futures
import json
import unittest
import uuid
from unittest.mock import patch

from harness import ServerTestCase


class UserIsolationHTTPTests(ServerTestCase):
    """Two companies must never see, change or infer each other's data."""

    def setUp(self):
        super().setUp()
        self.company_a, self.a, self.ca = self.make_company('A')
        self.company_b, self.b, self.cb = self.make_company('B')
        self.accounts = [self.a, self.b]
        self.member_a, self.ma = self.make_member(self.ca, 'Member A')
        self.member_b, self.mb = self.make_member(self.cb, 'Member B')

    def test_settings_glossary_preferences_and_forged_scope(self):
        for user, client, search, tolerance in [(self.a, self.ca, 'Alpha\nBravo', 3), (self.b, self.cb, 'Delta', 17)]:
            self.assertEqual(self.call(client, '/documents'), [])
            self.assertEqual(self.call(client, '/glossary'), {})
            self.call(self.admin, '/search-default', 'PUT', {'search': search}, owner=user)
            settings = self.call(self.admin, '/settings', owner=user)
            settings['land_tolerance_pct'] = tolerance
            self.call(self.admin, '/settings', 'PUT', settings, owner=user)
            self.call(self.admin, '/glossary', 'PUT', {'entries': {'samealias': search.split('\n')[0]}}, owner=user)
        self.assertEqual(self.call(self.ca, '/search-default')['terms'], ['Alpha', 'Bravo'])
        self.assertEqual(self.call(self.cb, '/search-default')['terms'], ['Delta'])
        self.assertEqual(float(self.call(self.ca, '/settings')['land_tolerance_pct']), 3)
        self.assertEqual(float(self.call(self.cb, '/settings')['land_tolerance_pct']), 17)
        self.assertEqual(self.call(self.ca, '/glossary'), {'samealias': 'alpha'})
        self.assertEqual(self.call(self.cb, '/glossary'), {'samealias': 'delta'})
        self.call(self.admin, '/preferences', 'PUT', {'direction': 'property', 'statuses': ['hot']}, owner=self.a)
        self.assertEqual(self.call(self.ca, '/preferences')['statuses'], ['hot'])
        self.assertEqual(self.call(self.cb, '/preferences')['direction'], 'buyer')
        for path in ['/settings', '/glossary', '/documents', '/imports', '/workspace', '/agent/matches']:
            self.call(self.ca, path, owner=self.b, status=403)
        self.call(self.ma, '/settings', 'PUT', {}, status=403)
        self.call(self.mb, '/team/users', status=403)
        self.call(self.admin, '/settings', owner='invalid', status=400)
        self.call(self.admin, '/settings', owner=str(uuid.uuid4()), status=404)
        self.assertEqual(self.call(self.ca, '/settings', owner=self.a)['company_id'], self.a['workspace_id'])
        # Requests in parallel must not reuse another user's ContextVar or cached settings.
        def read_one(index):
            client, expected = (self.ca, 'Alpha') if index % 2 else (self.cb, 'Delta')
            self.assertEqual(self.call(client, '/search-default')['terms'][0], expected)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(read_one, range(18)))

    def test_identical_uploads_matching_exports_and_cached_reads_are_private(self):
        from db import connect
        a, b = self.upload(self.a), self.upload(self.b)
        self.assertNotEqual(a['id'], b['id'])
        self.assertFalse(a['duplicate']); self.assertFalse(b['duplicate'])
        self.assertTrue(self.upload(self.a)['duplicate'])
        for user, job in [(self.a, a), (self.b, b)]:
            points = self.process(job)
            self.assertEqual(len(points), 2)
            self.assertTrue(all(p['payload']['company_id'] == user['workspace_id'] for p in points))
        docs_a = self.call(self.ca, '/documents'); docs_b = self.call(self.cb, '/documents')
        self.assertEqual(len(docs_a), 2); self.assertEqual(len(docs_b), 2)
        self.assertFalse({d['id'] for d in docs_a} & {d['id'] for d in docs_b})
        buyer_a = next(d for d in docs_a if d['document_type'] == 'buyer_request')
        buyer_b = next(d for d in docs_b if d['document_type'] == 'buyer_request')
        property_b = next(d for d in docs_b if d['document_type'] == 'property_listing')
        for client, user, job in [(self.ca, self.a, a), (self.cb, self.b, b)]:
            self.assertEqual([r['id'] for r in self.call(client, '/imports')], [job['id']])
            self.assertTrue(user['workspace_id'] in self.call(client, '/imports')[0]['file_path'])
            for path in ['/workspace', '/buyers', '/matches', '/documents']:
                data = self.call(client, path)
                rows = data['rows'] if path == '/workspace' else data
                self.assertTrue(rows)
                self.assertTrue(all(r.get('company_id', user['workspace_id']) == user['workspace_id'] for r in rows))
            self.assertEqual(self.call(client, '/stats')['raw_messages'], 2)
            self.assertEqual(len(self.call(client, '/agent/matches')['results']), 1)
            search = self.call(client, '/agent/search?q=rumah')['results']
            self.assertTrue(search); self.assertTrue(all(r['company_id'] == user['workspace_id'] for r in search))
            self.assertEqual(len(self.call(client, '/workspace/dates?date_from=2026-09-01&date_to=2026-10-01')['counts']), 1)
        own = self.call(self.ca, '/workspace/recommendations', 'POST', {'ids': [buyer_a['id']]})['groups'][0]
        self.assertTrue(own['recommendations'])
        self.assertTrue(all(r['company_id'] == self.a['workspace_id'] for r in own['recommendations']))
        pdf = self.call(self.ca, '/export/pdf', 'POST', {'pairs': [{'source_id': buyer_a['id'], 'target_id': own['recommendations'][0]['id']}]})
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(self.call(self.ca, '/workspace/recommendations', 'POST', {'ids': [buyer_b['id']]})['groups'], [])
        self.call(self.ca, '/buyers/' + buyer_b['id'] + '/recommendations', status=404)
        self.assertEqual(self.call(self.ca, '/buyers/recommendations/batch', 'POST', {'buyer_ids': [buyer_b['id']]})['groups'], [])
        self.call(self.ca, '/export/pdf', 'POST', {'pairs': [{'source_id': buyer_b['id'], 'target_id': property_b['id']}]}, status=404)
        self.call(self.ca, '/export/pdf', 'POST', {'pairs': [{'source_id': buyer_a['id'], 'target_id': property_b['id']}]}, status=409)
        with connect() as conn:
            conn.execute('DELETE FROM xm.workspace_cache_state WHERE company_id=%s', (self.a['workspace_id'],)); conn.commit()
        uncached = self.call(self.ca, '/workspace')['rows']
        self.assertEqual(len(uncached), 1)
        self.assertEqual(uncached[0]['id'], buyer_a['id'])
        self.assertEqual(self.call(self.ca, '/workspace/recommendations', 'POST', {'ids': [buyer_b['id']]})['groups'], [])

    def test_reindex_and_stats_cache_stay_in_selected_workspace(self):
        from tenant import workspace_scope
        import reindex
        self.process(self.upload(self.a))
        self.process(self.upload(self.b, ['Dijual rumah Surabaya Barat LT 100 Harga 1,8 M']))
        before_b = self.call(self.cb, '/documents')
        self.call(self.admin, '/glossary', 'PUT', {'entries': {'surabaya barat': 'citraland'}}, owner=self.a)
        points = []
        def record(batch): points.extend(batch); return len(batch)
        with workspace_scope(self.a['workspace_id']), patch.object(reindex, 'upsert', record):
            self.assertEqual(reindex.reindex_documents(), 2)
        self.assertTrue(all(p['payload']['company_id'] == self.a['workspace_id'] for p in points))
        self.assertEqual(self.call(self.cb, '/documents'), before_b)
        self.assertTrue(all('citraland' in r['normalized_text'].lower() for r in self.call(self.ca, '/documents')))
        # Cache is keyed by workspace, even when requests alternate rapidly.
        for client, expected in [(self.ca, 2), (self.cb, 1), (self.ca, 2), (self.cb, 1)]:
            self.assertEqual(self.call(client, '/stats')['raw_messages'], expected)

    def test_locations_jobs_and_worker_scopes_are_independent(self):
        from test_location_index import table
        from tenant import workspace_id
        import reindex, matcher, worker
        for user in (self.a, self.b):
            self.process(self.upload(user))
        for user, name in [(self.a, 'Alpha Cluster'), (self.b, 'Beta Cluster')]:
            payload = {'text': table([[1, name, 'samealias', 'Private Area', '']]), 'source_name': name + '.csv'}
            preview = self.call(self.admin, '/location-index/preview', 'POST', payload, owner=user)
            self.call(self.admin, '/location-index/import', 'POST', {**payload, 'revision': preview['revision']}, owner=user)
            # Saving knowledge may only mark results out of date; reprocessing reuses any queued job.
            self.call(self.admin, '/reprocess', 'POST', owner=user, status=202)
        self.assertEqual(self.call(self.ca, '/location-index')['clusters'][0]['name'], 'Alpha Cluster')
        self.assertEqual(self.call(self.cb, '/location-index')['clusters'][0]['name'], 'Beta Cluster')
        self.call(self.ca, '/location-index/neighbors?cluster=Beta%20Cluster', status=404)
        ja = self.call(self.ca, '/index/status'); jb = self.call(self.cb, '/index/status')
        self.assertNotEqual(ja['id'], jb['id'])
        self.assertEqual(ja['company_id'], self.a['workspace_id'])
        self.assertEqual(jb['company_id'], self.b['workspace_id'])
        calls = []
        def record(): calls.append(workspace_id()); return 0
        with patch.object(reindex, 'reindex_documents', record), patch.object(matcher, 'recompute_matches', record):
            self.assertTrue(worker.process_maintenance()); self.assertTrue(worker.process_maintenance())
        self.assertEqual(calls, [self.a['workspace_id']]*2 + [self.b['workspace_id']]*2)
        self.assertEqual(self.call(self.ca, '/index/status')['status'], 'completed')
        self.assertEqual(self.call(self.cb, '/index/status')['status'], 'completed')

    def test_qdrant_filters_use_request_workspace(self):
        import qdrant
        from tenant import workspace_scope
        with workspace_scope(self.a['workspace_id']), patch.object(qdrant, '_request', return_value={'result': {'points': []}}) as request:
            qdrant.query([0.0]*384)
            payload = request.call_args.args[2]
            self.assertEqual(payload['filter']['must'][0]['match']['value'], self.a['workspace_id'])
        with workspace_scope(self.b['workspace_id']), patch.object(qdrant, '_request', return_value={'result': {'count': 7}}) as request:
            self.assertEqual(qdrant.status()['points'], 7)
            self.assertEqual(request.call_args.args[2]['filter']['must'][0]['match']['value'], self.b['workspace_id'])


if __name__ == '__main__': unittest.main()
