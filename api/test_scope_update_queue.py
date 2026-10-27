"""Effective ownership changes are saved as "pending" and rebuilt by an explicit reprocess,
without inventing upload history."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from harness import ServerTestCase
from test_matching_scope import BUYER, OWNED_BUYER, KONIG, OTHER, PHONE


class ScopeUpdateQueueTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.owner, self.boss = self.make_company('ScopeQueue')
        self.member, self.staff = self.make_member(self.boss)

    def keywords(self, terms, locked=True):
        return self.call(self.boss, '/search-default', 'PUT', {'terms': terms, 'locked': locked})

    def seed(self):
        job = self.upload(self.owner, texts=[BUYER, OWNED_BUYER, KONIG, OTHER])
        self.process(job)
        return job

    def jobs(self, company=None):
        from db import connect
        with connect() as conn:
            return conn.execute('SELECT * FROM xm.maintenance_jobs WHERE company_id=%s ORDER BY created_at,id',
                                (company or self.company['company_id'],)).fetchall()

    def discard_jobs(self):
        from db import connect
        with connect() as conn:
            conn.execute('DELETE FROM xm.maintenance_jobs WHERE company_id=%s', (self.company['company_id'],))
            conn.commit()

    def reprocess(self, client=None, status=202):
        return self.call(client or self.boss, '/reprocess', 'POST', status=status)

    def pending(self):
        return self.call(self.staff, '/reprocess/status')

    def reset_pending(self):
        from db import connect
        with connect() as conn:
            conn.execute("UPDATE xm.app_preferences SET reprocess_pending_since=NULL, reprocess_reasons='[]' WHERE company_id=%s",
                         (self.company['company_id'],))
            conn.commit()

    def job_status(self, job, status):
        from db import connect
        with connect() as conn:
            conn.execute('UPDATE xm.maintenance_jobs SET status=%s WHERE id=%s', (status, job['id']))
            conn.commit()

    def test_changed_settings_without_active_documents_do_not_queue(self):
        self.keywords(['konig'])
        self.assertEqual(self.jobs(), [])
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': [PHONE]})
        self.call(self.boss, '/company/settings', 'PUT', {'matching_mode': 'sales'})
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': ['081234567891']})
        self.assertEqual(self.jobs(), [])
        # Nothing uploaded: nothing is out of date, and reprocessing has nothing to do.
        self.assertEqual((self.pending()['pending'], self.pending()['reasons']), (False, []))
        self.assertIsNone(self.reprocess()['job'])
        self.assertEqual(self.jobs(), [])

    def test_saving_only_marks_results_out_of_date_until_an_admin_reprocesses(self):
        from worker import process_maintenance
        self.seed()
        self.assertFalse(self.pending()['pending'])
        self.keywords(['konig'])
        self.call(self.boss, '/glossary', 'PUT', {'entries': {'cl': 'citraland'}})
        self.keywords(['sari'])
        settings = self.call(self.boss, '/settings')
        self.call(self.boss, '/settings', 'PUT', {**settings, 'land_tolerance_pct': float(settings['land_tolerance_pct']) + 1})
        self.assertEqual(self.jobs(), [])  # saving never starts a rebuild
        status = self.pending()
        self.assertTrue(status['pending'])
        self.assertIsNotNone(status['since'])
        self.assertEqual(status['reasons'], ['Search keywords changed', 'Glossary updated', 'Matching settings changed'])
        self.assertIsNone(status['job'])
        self.reprocess(self.staff, status=403)  # members see the banner but cannot reprocess
        queued = self.reprocess()
        self.assertEqual(queued['job']['status'], 'queued')
        self.assertTrue(queued['pending'])
        self.assertEqual(self.reprocess()['job']['id'], queued['job']['id'])  # one active job per company
        # The older endpoint is an alias that answers with the job.
        self.assertEqual(self.call(self.boss, '/index/recompute', 'POST', status=202)['id'], queued['job']['id'])
        with patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                patch('matcher.qdrant_query', lambda *a, **kw: []):
            self.assertTrue(process_maintenance())
        done = self.pending()
        self.assertEqual((done['pending'], done['reasons'], done['job']['status']), (False, [], 'completed'))
        # Saving the same values again changes nothing.
        self.call(self.boss, '/settings', 'PUT', {**settings, 'land_tolerance_pct': float(settings['land_tolerance_pct']) + 1})
        self.call(self.boss, '/glossary', 'PUT', {'entries': {'cl': 'citraland'}})
        self.assertFalse(self.pending()['pending'])

    def test_changes_saved_while_a_job_runs_stay_pending_after_it(self):
        from worker import process_maintenance
        self.seed()
        self.keywords(['konig'])
        current = self.reprocess()['job']

        def change_rules_during_processing():
            self.keywords(['sari'])
            return reindex_documents()

        from reindex import reindex_documents
        with patch('reindex.reindex_documents', change_rules_during_processing), \
                patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                patch('matcher.qdrant_query', lambda *a, **kw: []):
            self.assertTrue(process_maintenance())
        self.assertEqual([job['status'] for job in self.jobs()], ['completed'])
        after = self.pending()
        self.assertEqual((after['pending'], after['reasons'], after['job']['id']), (True, ['Search keywords changed'], current['id']))
        # A change saved while a job is still queued is covered by that job.
        self.reset_pending()
        queued = self.reprocess()['job']
        self.keywords(['konig'])
        with patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                patch('matcher.qdrant_query', lambda *a, **kw: []):
            self.assertTrue(process_maintenance())
        self.assertEqual(str(self.jobs()[-1]['id']), queued['id'])
        self.assertFalse(self.pending()['pending'])

    def test_reprocess_with_an_empty_sales_watchlist_explains_what_is_missing(self):
        self.seed()
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': [PHONE]})
        self.call(self.boss, '/company/settings', 'PUT', {'matching_mode': 'sales'})
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': []})
        self.assertEqual(self.pending()['reasons'], ['Matching mode changed', 'Sales watchlist changed'])
        self.reprocess(status=400)
        self.assertEqual(self.jobs(), [])

    def test_keyword_changes_deduplicate_queued_work_and_follow_processing_work(self):
        self.seed()
        self.keywords(['Konig', 'Sari'])
        self.assertEqual(self.jobs(), [])
        first = self.reprocess()['job']
        self.assertEqual(first['status'], 'queued')
        self.keywords(['Sari', 'KONIG'], locked=False)
        self.reprocess()
        self.assertEqual([str(job['id']) for job in self.jobs()], [first['id']])
        # With the earlier job removed, equivalent keywords/order/lock changes
        # must still mark nothing out of date.
        self.discard_jobs()
        self.reset_pending()
        self.keywords(['konig', 'sari'], locked=True)
        self.assertFalse(self.pending()['pending'])
        self.keywords(['konig'])
        self.assertTrue(self.pending()['pending'])
        current = self.reprocess()['job']
        self.keywords(['sari'])
        self.reprocess()
        self.assertEqual([str(job['id']) for job in self.jobs()], [current['id']])
        from worker import process_maintenance
        from reindex import reindex_documents

        def change_rules_during_processing():
            self.assertEqual([job['status'] for job in self.jobs()], ['processing'])
            self.keywords(['konig'])
            self.keywords(['sari'])
            self.reprocess()
            # Reprocessing during a running job requests one successor, while
            # preserving the database's single-running-job invariant.
            self.assertEqual([str(job['id']) for job in self.jobs()], [current['id']])
            self.assertTrue(self.jobs()[0]['result']['scope_changed'])
            return reindex_documents()

        with patch('reindex.reindex_documents', change_rules_during_processing), \
                patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                patch('matcher.qdrant_query', lambda *a, **kw: []):
            self.assertTrue(process_maintenance())
        jobs = self.jobs()
        self.assertEqual([job['status'] for job in jobs], ['completed', 'queued'])
        self.assertTrue(self.pending()['pending'])  # saved after the first job was queued
        successor = jobs[1]
        self.assertNotEqual(str(successor['id']), current['id'])
        self.keywords(['konig'])
        self.reprocess()
        self.assertEqual([str(job['id']) for job in self.jobs()], [current['id'], str(successor['id'])])
        with patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                patch('matcher.qdrant_query', lambda *a, **kw: []):
            self.assertTrue(process_maintenance())
        self.assertEqual([job['status'] for job in self.jobs()], ['completed', 'completed'])
        self.assertFalse(self.pending()['pending'])

    def test_scope_update_during_failed_maintenance_is_not_lost(self):
        from worker import process_maintenance

        self.seed()
        self.keywords(['konig'])
        self.reprocess()

        def change_rules_then_fail():
            self.keywords(['sari'])
            self.reprocess()
            raise RuntimeError('Simulated interrupted rebuild')

        with patch('reindex.reindex_documents', change_rules_then_fail):
            self.assertTrue(process_maintenance())
        jobs = self.jobs()
        self.assertEqual([job['status'] for job in jobs], ['failed', 'queued'])
        self.assertIn('Simulated interrupted rebuild', jobs[0]['error'])
        with patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                patch('matcher.qdrant_query', lambda *a, **kw: []):
            self.assertTrue(process_maintenance())
        self.assertEqual([job['status'] for job in self.jobs()], ['failed', 'completed'])
        self.assertEqual(self.jobs()[1]['result']['matches'], 2)

    def test_sales_changes_mark_only_effective_phone_or_mode_changes(self):
        self.seed()
        phones = [PHONE, '081234567892']
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': phones})
        self.assertFalse(self.pending()['pending'])  # Company mode does not use watchlist ownership.
        self.call(self.boss, '/company/settings', 'PUT', {'matching_mode': 'sales'})
        self.assertEqual(self.pending()['reasons'], ['Matching mode changed'])
        self.reset_pending()
        self.call(self.boss, '/company/settings', 'PUT', {'matching_mode': 'sales', 'company_name': 'New display name'})
        self.keywords(['not-used-in-sales'])
        self.call(self.boss, '/stock/tracked', 'PUT',
                  {'phones': list(reversed(phones)), 'labels': {PHONE: 'A new sales label'}})
        self.assertFalse(self.pending()['pending'])
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': ['081234567891']})
        self.assertEqual(self.pending()['reasons'], ['Sales watchlist changed'])
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': [PHONE]})
        self.assertEqual(self.pending()['reasons'], ['Sales watchlist changed'])  # each reason is listed once
        first = self.reprocess()['job']
        self.assertEqual([str(job['id']) for job in self.jobs()], [first['id']])
        self.discard_jobs()
        self.reset_pending()
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': []})
        self.assertEqual(self.jobs(), [])
        self.assertTrue(self.pending()['pending'])
        self.assertEqual(self.call(self.staff, '/workspace?direction=property')['rows'], [])
        self.call(self.boss, '/company/settings', 'PUT', {'matching_mode': 'company'})
        self.assertEqual(self.pending()['reasons'], ['Sales watchlist changed', 'Matching mode changed'])
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.reprocess()['job']['status'], 'queued')

    def test_queue_utility_is_tenant_scoped_and_requires_eligible_active_data(self):
        from db import connect
        from matching_scope import queue_scope_recompute
        from tenant import workspace_scope

        self.seed()
        other, other_user, _ = self.make_company('OtherQueue')
        self.process(self.upload(other_user))
        with workspace_scope(self.company['company_id']), connect() as conn:
            first = queue_scope_recompute(conn)
            self.assertEqual(queue_scope_recompute(conn)['id'], first['id'])
            conn.commit()
        with workspace_scope(other['company_id']), connect() as conn:
            other_job = queue_scope_recompute(conn)
            conn.commit()
        self.assertNotEqual(first['id'], other_job['id'])
        self.assertEqual(first['company_id'], self.company['company_id'])
        self.assertEqual(other_job['company_id'], other['company_id'])
        self.job_status(first, 'processing')
        with workspace_scope(self.company['company_id']), connect() as conn:
            requested = queue_scope_recompute(conn)
            self.assertEqual(requested['id'], first['id'])
            self.assertTrue(requested['result']['scope_changed'])
            conn.execute('UPDATE xm.documents SET active=false WHERE company_id=%s', (self.company['company_id'],))
            self.assertIsNone(queue_scope_recompute(conn))
            conn.commit()
        self.assertEqual(len(self.jobs(other['company_id'])), 1)

    def test_scope_maintenance_adds_missing_pairs_and_preserves_original_discovery_history(self):
        from db import connect
        from worker import process_maintenance

        self.keywords(['konig'])
        upload = self.seed()
        old_found = datetime.now(ZoneInfo('Asia/Jakarta')) - timedelta(days=5)
        with connect() as conn:
            conn.execute('UPDATE xm.match_events SET found_at=%s WHERE company_id=%s',
                         (old_found, self.company['company_id']))
            before = conn.execute('SELECT * FROM xm.match_events WHERE company_id=%s ORDER BY id',
                                  (self.company['company_id'],)).fetchall()
            conn.commit()
        self.assertEqual(len(before), 3)
        self.assertTrue(all(row['source'] == 'import' and str(row['import_id']) == upload['id'] for row in before))
        self.keywords(['sari'])
        self.assertEqual(self.jobs(), [])
        self.reprocess()
        self.assertEqual(len(self.jobs()), 1)
        import dashboard
        overview_url = '/dashboard/overview?period=custom&date_from=2026-01-01&date_to=2026-12-31'
        with patch('dashboard.compute_overview', wraps=dashboard.compute_overview) as compute:
            # Prime both API caches after the rule write but before the worker
            # catches up; completion must refresh that same request immediately.
            self.assertEqual(self.call(self.staff, '/stats')['matches'], 1)
            self.assertEqual(self.call(self.staff, overview_url)['kpi']['matches'], 1)
            with patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                    patch('matcher.qdrant_query', lambda *a, **kw: []):
                self.assertTrue(process_maintenance())
            self.assertEqual(self.call(self.staff, '/stats')['matches'], 2)
            # Recompute pairs are baseline, so upload-history KPI stays at one;
            # the dashboard still has to calculate against the refreshed data.
            self.assertEqual(self.call(self.staff, overview_url)['kpi']['matches'], 1)
            self.assertEqual(compute.call_count, 2)
        job = self.jobs()[0]
        self.assertEqual(job['status'], 'completed', job['error'])
        self.assertEqual(job['result'], {'documents': 4, 'matches': 2})
        with connect() as conn:
            after = conn.execute('SELECT * FROM xm.match_events WHERE company_id=%s ORDER BY id',
                                 (self.company['company_id'],)).fetchall()
        by_id = {row['id']: row for row in after}
        for row in before:
            preserved = by_id[row['id']]
            for key in ('found_at', 'source', 'import_id', 'buyer_entity', 'listing_entity'):
                self.assertEqual(preserved[key], row[key])
        fresh = [row for row in after if row['id'] not in {old['id'] for old in before}]
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]['source'], 'recompute')
        self.assertIsNone(fresh[0]['import_id'])
        self.assertEqual(sum(bool(row['active']) for row in after), 2)
        sources = self.call(self.staff, '/workspace?direction=property')['rows']
        self.assertEqual([row['raw_text'] for row in sources], [OTHER])
        matched = self.call(self.staff, '/workspace/recommendations', 'POST',
                            {'direction': 'property', 'ids': [sources[0]['id']]})['groups'][0]
        self.assertEqual({row['raw_text'] for row in matched['recommendations']}, {BUYER, OWNED_BUYER})
        today = datetime.now(ZoneInfo('Asia/Jakarta')).date().isoformat()
        recent = self.call(self.staff, f'/matches/recent?direction=property&date_from={today}&date_to={today}')
        self.assertEqual(recent['groups'], [])
        old_day = old_found.date().isoformat()
        historical = self.call(self.staff, f'/matches/recent?direction=property&date_from={old_day}&date_to={old_day}')
        self.assertEqual(historical['totals']['pairs'], 1)
        self.assertEqual(historical['groups'][0]['source']['raw_text'], OTHER)
        self.assertEqual(historical['groups'][0]['matches'][0]['import_id'], upload['id'])


if __name__ == '__main__':
    unittest.main()
