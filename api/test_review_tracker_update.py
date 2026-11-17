"""Credits, stock drill-down, tracker isolation and timestamp regressions.

Pure tests run without PostgreSQL. HTTP tests require XM_TEST_DATABASE_URL,
pointing at an isolated database, as with the rest of the integration suite.
"""
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pypdf import PdfReader
from access import allowed, permissions
import ai_review
from ai_review import ReviewScope
from report import build_report, last_updated_at
from matching_tracker import event_filter, rate
from db import connect
from tenant import workspace_scope
from test_v4_features import V4Case, SALES_A, LISTING_HOUSE_B, BUYER_HOUSE
from test_jev_review import JevCase, SECRET, DEEPSEEK


class ReviewPolicyTests(unittest.TestCase):
    def test_defaults_and_upload_visibility(self):
        self.assertEqual(ReviewScope().model, 'z-ai/glm-5.3-flash')
        self.assertEqual(ReviewScope().max_pairs, 100)
        for role in ('user', 'company_admin', 'admin'):
            self.assertEqual(permissions(role, features=['upload'])['upload_data'], role == 'admin')
        self.assertFalse(allowed({'role': 'user', 'features': ['stock']}, 'GET', '/matching-tracker'))
        self.assertTrue(allowed({'role': 'user', 'features': ['match']}, 'GET', '/matching-tracker/history'))
        self.assertTrue(allowed({'role': 'user', 'features': ['stock']}, 'GET', '/stock/listings'))

    def test_exhausted_credit_blocks_before_planning(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {'used': 40}
        with patch('ai_review.connect') as factory, patch('ai_review.current_user', return_value={'role': 'company_admin'}), patch('ai_review.plan_run') as plan:
            factory.return_value.__enter__.return_value = conn
            with self.assertRaises(HTTPException) as failure:
                ai_review.create_run(ReviewScope(), None)
            self.assertEqual(failure.exception.status_code, 409)
            plan.assert_not_called()
            conn.commit.assert_not_called()

    def test_same_request_returns_existing_run_even_when_balance_is_empty(self):
        conn = MagicMock()
        existing = uuid.uuid4()
        conn.execute.return_value.fetchone.return_value = {'id': existing}
        with patch('ai_review.connect') as factory, patch('ai_review.run_summary', return_value={'id': existing}) as summary, patch('ai_review.plan_run') as plan:
            factory.return_value.__enter__.return_value = conn
            result = ai_review.create_run(ReviewScope(request_id=uuid.uuid4()), None)
            self.assertEqual(result['id'], existing)
            summary.assert_called_once_with(conn, existing)
            plan.assert_not_called()
            conn.commit.assert_not_called()

    def test_period_boundaries_are_wib_and_empty_rate_is_zero(self):
        _, values = event_filter('2026-11-17', '2026-11-17')
        self.assertEqual(values[0].astimezone(timezone.utc).isoformat(), '2026-11-16T17:00:00+00:00')
        self.assertEqual(values[1].astimezone(timezone.utc).isoformat(), '2026-11-17T17:00:00+00:00')
        self.assertEqual(rate(1, 3), 33.3)
        self.assertEqual(rate(0, 0), 0)
        with self.assertRaises(HTTPException):
            event_filter('2026-11-18', '2026-11-17')

    def test_pdf_timestamps_are_under_both_ids_and_do_not_replace_message(self):
        buyer = {'public_id': 'B-AA000', 'raw_text': 'BUYER COMPLETE TEXT', 'sent_at': '2026-11-16T08:00:00', 'last_seen_at': '2026-11-17T18:34:00'}
        listing = {'public_id': 'L-AA000', 'raw_text': 'LISTING COMPLETE TEXT', 'sent_at': '2026-11-17T11:34:00Z'}
        self.assertEqual(last_updated_at(buyer), '17 Nov 2026 - 18:34 WIB')
        self.assertEqual(last_updated_at(listing), '17 Nov 2026 - 18:34 WIB')
        self.assertIsNone(last_updated_at({'sent_at': 'broken'}))
        page = PdfReader(BytesIO(build_report([(buyer, listing)], 'buyer'))).pages[0]
        text = page.extract_text()
        self.assertEqual(text.count('Last updated at : 17 Nov 2026 - 18:34 WIB'), 2)
        for value in ['B-AA000', 'L-AA000', buyer['raw_text'], listing['raw_text']]:
            self.assertIn(value, text)
        positions = []
        page.extract_text(visitor_text=lambda text, cm, tm, font, size: positions.append((text, tm[5])))
        id_y = next(y for text, y in positions if 'B-AA000' in text)
        stamp_y = next(y for text, y in positions if 'Last updated at' in text)
        self.assertLess(stamp_y, id_y)


class StockTrackerHTTPTests(V4Case):
    def test_stock_is_independent_of_matching_keyword_and_cache(self):
        self.seed()
        self.run_import([(LISTING_HOUSE_B, '~ Repost')], agent='Second')
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': [SALES_A]})
        self.call(self.boss, '/search-default', 'PUT', {'terms': ['no-such-company-keyword'], 'locked': True})
        with workspace_scope(self.company['company_id']), connect() as conn:
            conn.execute("DELETE FROM xm.document_groups WHERE company_id=current_setting('xm.workspace_id')")
            conn.execute("UPDATE xm.users SET features='[\"stock\"]'::jsonb WHERE id=%s", (self.member['id'],))
            conn.commit()
        overview = self.call(self.staff, '/stock/overview')
        listings = self.call(self.staff, '/stock/listings?phone=' + SALES_A)
        self.assertEqual(listings['total'], overview['tracked'][0]['counts']['total'])
        self.assertEqual(len(listings['rows']), 1)
        _, _, other = self.make_company('Other tracker')
        self.call(other, '/stock/listings?phone=' + SALES_A, status=404)

    def test_tracker_unique_counts_retired_history_and_buyer_direction(self):
        self.run_import([(BUYER_HOUSE + '\nContact: Sari ' + SALES_A, '~ Buyer'), (LISTING_HOUSE_B, '~ Listing')])
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': [SALES_A]})
        for direction in ('property', 'buyer'):
            overview = self.call(self.staff, '/matching-tracker?direction=' + direction)
            self.assertEqual(overview['totals']['total'], 1)
            self.assertEqual(overview['totals']['matched'], 1)
            self.assertEqual(overview['totals']['matching_rate'], 100)
            entity = self.call(self.staff, '/matching-tracker/entities?direction=' + direction)['rows'][0]
            path = '/matching-tracker/history?direction=' + direction + '&entity_id=' + entity['entity_id']
            before = self.call(self.staff, path)
            self.assertEqual(len(before['rows']), 1)
            with workspace_scope(self.company['company_id']), connect() as conn:
                conn.execute("UPDATE xm.match_events SET active=false WHERE company_id=current_setting('xm.workspace_id')")
                conn.commit()
            after = self.call(self.staff, path)
            self.assertEqual(after['rows'][0]['id'], before['rows'][0]['id'])
            self.assertFalse(after['rows'][0]['active'])
            _, _, other = self.make_company('Foreign history')
            self.call(other, path, status=404)
        empty = self.call(self.staff, '/matching-tracker?date_from=2020-01-01&date_to=2020-01-02')
        self.assertEqual(empty['totals']['matched'], 0)
        self.assertEqual(empty['totals']['total'], 1)


class CreditHTTPTests(JevCase):
    def add_key(self):
        return self.call(self.admin, '/ai/keys', 'POST', {'label': 'Credit test', 'api_key': SECRET}, owner=self.user, status=201)

    def test_retry_and_parallel_requests_share_last_credit(self):
        self.seed(); self.add_key()
        payload = {'model': DEEPSEEK, 'request_id': str(uuid.uuid4())}
        first = self.call(self.boss, '/ai/review-runs', 'POST', payload, status=202)
        again = self.call(self.boss, '/ai/review-runs', 'POST', payload, status=202)
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(self.call(self.boss, '/ai/credits')['used'], 1)
        with workspace_scope(self.company['company_id']), connect() as conn:
            conn.execute("UPDATE xm.app_preferences SET ai_review_credits_used=39 WHERE company_id=current_setting('xm.workspace_id')")
            conn.commit()
        # Separate client cookie jars exercise two simultaneous requests for the same company.
        clients = [self.client(self.user['email'], 'isolation-user-pass') for _ in range(2)]
        def start(client):
            import json, urllib.request, urllib.error
            request = urllib.request.Request(self.base + '/ai/review-runs', data=json.dumps({'model': DEEPSEEK, 'request_id': str(uuid.uuid4())}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with client.open(request, timeout=30) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(start, clients)), [202, 409])
        self.assertEqual(self.call(self.boss, '/ai/credits')['remaining'], 0)
        self.call(self.admin, '/ai/review-runs', 'POST', {'model': DEEPSEEK}, owner=self.user, status=202)
        self.assertEqual(self.call(self.boss, '/ai/credits')['used'], 40)
