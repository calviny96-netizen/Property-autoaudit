"""v5.1: live progress + time estimates of background jobs, and the analysis window (last 4 months by default)."""
from datetime import date, timedelta
from unittest.mock import patch

from harness import ServerTestCase

BUYER = 'Dicari rumah Citraland LT 100 Budget 2 M\nHub Rina 081311110000'
LISTING = 'Dijual rumah Citraland LT 100 Harga 1,9 M\nHub Andi 081322220000'


def months_ago(months, day=15):
    today = date.today()
    index = today.year * 12 + (today.month - 1) - months
    return date(index // 12, index % 12 + 1, day)


class ProgressTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.owner, self.boss = self.make_company('Progress')
        self.member, self.staff = self.make_member(self.boss)
        self.scope = self.company['company_id']

    def rows(self, sql, params=()):
        from db import connect
        with connect() as conn:
            return conn.execute(sql, params).fetchall()

    def run_maintenance(self):
        from worker import process_maintenance
        with patch('reindex.upsert', lambda points: len(points)), patch('reindex._request', lambda *a, **kw: None), \
                patch('matcher.qdrant_query', lambda *a, **kw: []):
            self.assertTrue(process_maintenance())

    def test_upload_and_reprocess_carry_progress_and_learned_durations(self):
        queued = self.upload(self.owner, messages=[(BUYER, '~ Rina'), (LISTING, '~ Andi')])
        self.assertEqual(queued['messages'], 2)
        self.assertGreater(queued['estimate_s'], 0)
        listed = self.call(self.boss, '/imports')[0]
        self.assertEqual((listed['progress']['percent'], listed['progress']['stage']), (0, 'queued'))
        self.assertGreater(listed['progress']['eta_s'], 0)
        self.process(queued)
        done = self.call(self.boss, '/imports')[0]
        self.assertEqual(done['status'], 'completed')
        self.assertIsNotNone(done['started_at'])
        self.assertEqual(done['progress']['percent'], 100)
        self.assertIn('parse', done['progress']['durations'])
        self.assertEqual(done['progress']['items']['parse'], 2)
        # An up-front estimate now comes from the learned rates.
        estimate = self.call(self.boss, '/imports/estimate?messages=5000')
        self.assertGreater(estimate['estimate_s'], 0)

        job = self.call(self.boss, '/reprocess', 'POST', status=202)['job']
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['progress']['percent'], 0)
        self.assertGreater(job['progress']['eta_s'], 0)
        self.assertEqual(job['progress']['items']['parse'], 2)
        self.run_maintenance()
        latest = self.call(self.staff, '/index/status')
        self.assertEqual((latest['status'], latest['progress']['percent'], latest['progress']['stage']), ('completed', 100, 'done'))
        self.assertIsNotNone(latest['started_at'])
        self.assertIn('parse', latest['progress']['durations'])
        self.assertIn('match', latest['progress']['durations'])
        self.assertEqual(latest['progress']['items']['parse'], 2)
        self.assertEqual(latest['result'], {'documents': 2, 'matches': 1})
        status = self.call(self.staff, '/reprocess/status')
        self.assertEqual(status['job']['id'], latest['id'])

    def test_estimate_seconds_uses_learned_rates(self):
        from db import connect
        from progress import DEFAULT_RATES, estimate_seconds
        from tenant import workspace_scope
        with workspace_scope(self.scope), connect() as conn:
            cold = estimate_seconds(conn, self.scope, 'reprocess', {'parse': 1000, 'match': 150})
            self.assertAlmostEqual(cold, 1000 / DEFAULT_RATES['parse'] + 150 / DEFAULT_RATES['match'] + 5 + 1, delta=1)
        self.process(self.upload(self.owner, messages=[(BUYER, '~ Rina'), (LISTING, '~ Andi')]))
        self.call(self.boss, '/reprocess', 'POST', status=202)
        self.run_maintenance()
        with workspace_scope(self.scope), connect() as conn:
            warm = estimate_seconds(conn, self.scope, 'reprocess', {'parse': 1000, 'match': 150})
            self.assertGreater(warm, 0)
            self.assertGreater(estimate_seconds(conn, self.scope, 'import', {'parse': 10}), 0)

    def test_analysis_window_skips_old_messages_and_can_be_extended(self):
        old = months_ago(6).isoformat()
        new = date.today().replace(day=1).isoformat()
        self.process(self.upload(self.owner, messages=[
            (BUYER, '~ Rina', f'{old}T10:00:00'), (LISTING, '~ Andi', f'{old}T11:00:00'),
            (BUYER.replace('LT 100', 'LT 150'), '~ Rina', f'{new}T10:00:00'), (LISTING.replace('LT 100', 'LT 150'), '~ Andi', f'{new}T11:00:00')]))
        status = self.call(self.staff, '/reprocess/status')
        self.assertEqual(status['analysis_window_months'], 4)
        self.assertEqual(status['analysis_from'], months_ago(4, 1).isoformat())
        self.assertTrue(status['has_older_data'])
        dates = self.call(self.staff, f'/workspace/dates?direction=buyer&date_from={new}&date_to={date.today() + timedelta(days=1)}')
        self.assertEqual(dates['analysis_from'], status['analysis_from'])
        # Every message is stored and classified; only the recent pair is matched.
        self.assertEqual(len(self.rows("SELECT 1 FROM xm.documents WHERE company_id=%s AND active", (self.scope,))), 4)
        matched = self.rows('''SELECT DISTINCT r.sent_at::date AS day FROM xm.matches m JOIN xm.documents d ON d.id=m.buyer_request_id
          JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE m.company_id=%s''', (self.scope,))
        self.assertEqual([str(row['day']) for row in matched], [new])

        # A member cannot extend the window; the admin can, and the start persists for later reprocesses.
        self.call(self.staff, '/reprocess', 'POST', {'from': old}, status=403)
        self.call(self.boss, '/reprocess', 'POST', {'from': (date.today() + timedelta(days=1)).isoformat()}, status=400)
        extended = self.call(self.boss, '/reprocess', 'POST', {'from': old}, status=202)
        self.assertEqual(extended['analysis_from'], old)
        self.assertTrue(extended['analysis_pinned'])
        self.assertFalse(extended['has_older_data'])
        self.assertEqual(extended['job']['status'], 'queued')
        self.assertEqual(extended['job']['progress']['items']['parse'], 4)
        self.run_maintenance()
        finished = self.call(self.staff, '/index/status')
        self.assertEqual((finished['status'], finished['error']), ('completed', None))
        matched = self.rows('''SELECT DISTINCT r.sent_at::date AS day FROM xm.matches m JOIN xm.documents d ON d.id=m.buyer_request_id
          JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE m.company_id=%s ORDER BY 1''', (self.scope,))
        self.assertEqual([str(row['day']) for row in matched], [old, new])
        self.assertEqual(self.call(self.staff, '/reprocess/status')['analysis_from'], old)
        # A later date never shrinks a pinned window.
        self.call(self.boss, '/reprocess', 'POST', {'from': new}, status=202)
        self.assertEqual(self.call(self.staff, '/reprocess/status')['analysis_from'], old)

    def test_analysis_window_months_round_trips_through_settings(self):
        self.process(self.upload(self.owner, messages=[(BUYER, '~ Rina'), (LISTING, '~ Andi')]))
        self.assertEqual(int(self.call(self.boss, '/settings')['analysis_window_months']), 4)
        saved = self.call(self.boss, '/settings', 'PUT', {'analysis_window_months': 7})
        self.assertEqual(int(saved['analysis_window_months']), 7)
        self.assertEqual(int(self.call(self.staff, '/settings')['analysis_window_months']), 7)
        status = self.call(self.staff, '/reprocess/status')
        self.assertIn('Analysis window changed', status['reasons'])
        self.assertEqual(status['analysis_from'], months_ago(7, 1).isoformat())
        self.call(self.boss, '/settings', 'PUT', {'analysis_window_months': 0}, status=422)
        self.call(self.boss, '/settings', 'PUT', {'analysis_window_months': 25}, status=422)
        self.call(self.staff, '/settings', 'PUT', {'analysis_window_months': 3}, status=403)
