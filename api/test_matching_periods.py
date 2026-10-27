"""Independent posting windows across lists, recommendations, PDFs and delivery receipts."""
import io
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from pypdf import PdfReader

from db import connect
from periods import RelativePeriod, matching_windows, shift_month
from test_integration import WorkflowIntegrationCase
from test_v4_features import V4Case, BUYER_HOUSE, LISTING_HOUSE_A, LISTING_HOUSE_B

# v4.10: one ID per content, so the new buyer needs its own content (not only another signature).
NEW_BUYER_HOUSE = BUYER_HOUSE.replace('LT 100', 'LT 102')


class RelativePeriodTests(unittest.TestCase):
    def test_today_rolling_month_and_previous_calendar_month_differ(self):
        today = date(2026, 10, 9)
        self.assertEqual(RelativePeriod(mode='today').resolve(today), ('2026-10-09', '2026-10-09'))
        self.assertEqual(RelativePeriod(mode='last_months').resolve(today), ('2026-09-09', '2026-10-09'))
        self.assertEqual(RelativePeriod(mode='last_months', amount=3).resolve(today), ('2026-07-09', '2026-10-09'))
        self.assertEqual(RelativePeriod(mode='previous_month').resolve(today), ('2026-09-01', '2026-09-30'))
        self.assertEqual(RelativePeriod(mode='last_days', amount=7).resolve(today), ('2026-10-03', '2026-10-09'))
        self.assertEqual(shift_month(date(2024, 3, 31), -1), date(2024, 2, 29))
        self.assertEqual(shift_month(date(2026, 3, 31), -1), date(2026, 2, 28))

    def test_legacy_window_only_limits_source_and_named_windows_follow_entity_kind(self):
        source, target = matching_windows('property', date_from='2026-10-09', date_to='2026-10-09')
        self.assertTrue(source[0])
        self.assertFalse(target[0])
        for direction in ('buyer', 'property'):
            source, target = matching_windows(direction, buyer_date_from='2026-09-01', buyer_date_to='2026-09-30',
                                             listing_date_from='2026-10-09', listing_date_to='2026-10-09')
            self.assertEqual(source[1][0].date(), date(2026, 9, 1) if direction == 'buyer' else date(2026, 10, 9))
            self.assertEqual(target[1][0].date(), date(2026, 10, 9) if direction == 'buyer' else date(2026, 9, 1))
        with self.assertRaises(HTTPException):
            matching_windows('property', buyer_date_from='2026-10-10', buyer_date_to='2026-10-09')


class MatchingWindowTests(V4Case):
    def seed_windows(self):
        self.old_buyer = BUYER_HOUSE + '\nContact: Buyer Lama 081300000001'
        self.new_buyer = NEW_BUYER_HOUSE + '\nContact: Buyer Baru 081300000002'
        self.run_import([
            (self.old_buyer, '~ Buyer Lama', '2026-09-15T10:00:00'),
            (self.new_buyer, '~ Buyer Baru', '2026-10-09T08:00:00'),
            (LISTING_HOUSE_A, '~ Andi', '2026-07-09T00:00:00'),
            (LISTING_HOUSE_B, '~ Sari', '2026-10-09T09:36:59'),
            # Later reposts must not hide an eligible earlier posting.
            (LISTING_HOUSE_A, '~ Andi', '2026-10-10T10:00:00'),
            (self.old_buyer, '~ Buyer Lama', '2026-10-10T11:00:00'),
        ])

    def test_new_buyers_against_three_month_stock_in_both_directions(self):
        self.seed_windows()
        windows = dict(buyer_date_from='2026-10-09', buyer_date_to='2026-10-09',
                       listing_date_from='2026-07-09', listing_date_to='2026-10-09')
        sources = self.rows(direction='property', **windows)
        self.assertEqual(len(sources), 2)
        for source in sources:
            targets = self.recs(source['id'], direction='property', **windows)
            self.assertEqual([r['raw_text'] for r in targets], [self.new_buyer])
            self.assertEqual(source['match_count'], len(targets))
        buyers = self.rows(direction='buyer', **windows)
        self.assertEqual([b['raw_text'] for b in buyers], [self.new_buyer])
        targets = self.recs(buyers[0]['id'], direction='buyer', **windows)
        self.assertEqual({r['raw_text'] for r in targets}, {LISTING_HOUSE_A, LISTING_HOUSE_B})
        self.assertTrue(all(r['sent_at'][:10] <= '2026-10-09' for r in targets))

    def test_new_listing_against_old_buyer_and_pdf_excludes_new_buyer(self):
        self.seed_windows()
        windows = dict(buyer_date_from='2026-09-01', buyer_date_to='2026-09-30',
                       listing_date_from='2026-10-09', listing_date_to='2026-10-09')
        source = self.rows(direction='property', **windows)[0]
        self.assertEqual(source['raw_text'], LISTING_HOUSE_B)
        targets = self.recs(source['id'], direction='property', **windows)
        self.assertEqual([r['raw_text'] for r in targets], [self.old_buyer])
        self.assertEqual(targets[0]['sent_at'][:10], '2026-09-15')
        pdf = self.call(self.staff, '/export/pdf', 'POST', {'direction': 'property', **windows,
            'pairs': [{'source_id': source['id'], 'target_id': targets[0]['id']}]})
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn('Buyer Lama', text)
        self.assertNotIn('Buyer Baru', text)
        outside = self.card(self.rows(direction='buyer'), 'Buyer Baru')
        self.call(self.staff, '/export/pdf', 'POST', {'direction': 'property', **windows,
            'pairs': [{'source_id': source['id'], 'target_id': outside['id']}]}, status=409)
        old_listing = self.card(self.rows(direction='property'), 'Budi')
        self.call(self.staff, '/export/pdf', 'POST', {'direction': 'property', **windows,
            'pairs': [{'source_id': old_listing['id'], 'target_id': targets[0]['id']}]}, status=404)

    def test_filtered_counts_groups_and_unmatched_follow_target_period(self):
        self.seed_windows()
        from urllib.parse import urlencode
        windows = dict(buyer_date_from='2026-09-01', buyer_date_to='2026-09-30',
                       listing_date_from='2026-10-09', listing_date_to='2026-10-09')
        result = self.call(self.staff, '/workspace/groups?' + urlencode({'group_by': 'sender', **windows}))
        self.assertEqual([(g['key'], g['count']) for g in result['groups']], [('~ Sari', 1)])
        plan = self.call(self.staff, '/export/all/plan', 'POST', {'direction': 'property', **windows})
        self.assertEqual(plan['totals']['pages'], 1)
        self.assertEqual(plan['totals']['sources'], 1)
        self.assertEqual(plan['totals']['hot'] + plan['totals']['warm'], 1)
        empty = {**windows, 'buyer_date_from': '2026-08-01', 'buyer_date_to': '2026-08-31'}
        self.assertEqual(self.rows(direction='property', statuses='hot,warm', **empty), [])
        unmatched = self.rows(direction='property', statuses='unmatched', **empty)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]['match_count'], 0)
        self.assertEqual(self.recs(unmatched[0]['id'], direction='property', **empty), [])
        self.call(self.staff, '/workspace?direction=property&buyer_date_from=bad', status=400)

    def test_cache_and_fallback_agree_on_independent_windows(self):
        self.seed_windows()
        from workspace_cache import refresh_workspace_cache
        windows = dict(buyer_date_from='2026-09-01', buyer_date_to='2026-09-30',
                       listing_date_from='2026-07-09', listing_date_to='2026-10-09')
        cached = self.rows(direction='property', **windows)
        with connect() as conn:
            conn.execute('DELETE FROM xm.workspace_cache_state WHERE company_id=%s', (self.company['company_id'],))
            conn.commit()
        try:
            fallback = self.rows(direction='property', **windows)
            fields = ['raw_text', 'hot_count', 'warm_count', 'match_count']
            self.assertEqual([[r[k] for k in fields] for r in cached], [[r[k] for k in fields] for r in fallback])
            for source in fallback:
                self.assertEqual([r['raw_text'] for r in self.recs(source['id'], direction='property', **windows)], [self.old_buyer])
            all_temps = self.rows(direction='property', statuses='hot,warm,unmatched', **windows)
            self.assertEqual(len(all_temps), len(fallback))
        finally:
            with connect() as conn:
                refresh_workspace_cache(conn, self.company['company_id'])
                conn.commit()


class DeliveryWindowTests(WorkflowIntegrationCase):
    def test_custom_windows_and_recipient_names_reach_pdf_manifest(self):
        from integration import process_next_export
        old_buyer = BUYER_HOUSE + '\nContact: Buyer Lama 081300000001'
        new_buyer = NEW_BUYER_HOUSE + '\nContact: Buyer Baru 081300000002'
        self.run_import([(old_buyer, '~ Old', '2026-09-15T10:00:00'),
                         (new_buyer, '~ New', '2026-10-09T08:00:00'),
                         (LISTING_HOUSE_A, '~ Andi', '2026-10-09T09:00:00')])
        job = self.queue(group_by='phone', buyer_period={'mode': 'custom', 'date_from': '2026-09-01', 'date_to': '2026-09-30'},
                         listing_period={'mode': 'custom', 'date_from': '2026-10-09', 'date_to': '2026-10-09'},
                         recipient_names={'6281234567890': 'Budi - Konig'})
        self.assertTrue(process_next_export())
        item = self.status(job)['files'][0]
        self.assertEqual(item['recipient_name'], 'Budi - Konig')
        pdf = self.call(self.workflow, item['download_url'].removeprefix('/api'))
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn('Buyer Lama', text)
        self.assertNotIn('Buyer Baru', text)
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'conflicting-window',
            'buyer_period': {'mode': 'today'}, 'buyer_date_from': '2026-10-09'}, status=400)
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'invalid-custom',
            'buyer_period': {'mode': 'custom', 'date_from': '2026-10-09'}}, status=422)

    def test_relative_windows_freeze_in_wib_and_retries_do_not_shift(self):
        payload = {'buyer_period': {'mode': 'today'}, 'listing_period': {'mode': 'last_months', 'amount': 3}}
        # UTC evening is already the following day in WIB.
        with patch('integration.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)
            job = self.queue(**payload)
        self.assertEqual(job['periods'], {'buyer_date_from': '2026-10-09', 'buyer_date_to': '2026-10-09',
                                        'listing_date_from': '2026-07-09', 'listing_date_to': '2026-10-09'})
        self.assertEqual(self.queue(**payload)['job_id'], job['job_id'])
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': job['request_id'],
            'buyer_period': {'mode': 'previous_month'}}, status=409)

    def test_delivery_receipt_suppresses_pairs_only_after_success_and_survives_rebuild(self):
        self.seed()
        from integration import process_next_export
        params = {'delivery_scope': 'konig-daily', 'group_by': 'phone'}
        job = self.queue(**params)
        self.assertTrue(process_next_export())
        files = self.status(job)['files']
        self.assertEqual(len(files), 3)
        self.assertTrue(all('entity_pairs' not in f for f in files))
        # A retry/new run before delivery remains eligible, not silently discarded.
        retry = self.queue(request_id='retry-before-delivery', **params)
        self.assertTrue(process_next_export())
        self.assertEqual(len(self.status(retry)['files']), 3)
        for index, item in enumerate(files):
            path = f"/integration/exports/{job['job_id']}/files/{item['file_id']}/delivered"
            receipt = self.call(self.workflow, path, 'POST', {'delivery_id': 'gowa-' + item['file_id']})
            self.assertEqual(receipt, self.call(self.workflow, path, 'POST', {'delivery_id': 'retry-receipt'}))
            if index == 0:
                partial = self.queue(request_id='after-one-file', **params)
                self.assertTrue(process_next_export())
                remaining = self.status(partial)['files']
                self.assertEqual(len(remaining), 2)
                self.assertNotIn(item['group_key'], [f['group_key'] for f in remaining])
        from matcher import recompute_matches
        with patch('matcher.qdrant_query', return_value=[]):
            recompute_matches(self.company['company_id'])
        self.run_import([(LISTING_HOUSE_A, '~ Repost')])
        next_job = self.queue(request_id='after-success', **params)
        self.assertTrue(process_next_export())
        self.assertEqual(self.status(next_job)['files'], [])
        reverse = self.queue(request_id='reverse-after-success', direction='buyer', **params)
        self.assertTrue(process_next_export())
        self.assertEqual(self.status(reverse)['files'], [])
        independent = self.queue(request_id='another-audience', delivery_scope='different-audience', group_by='phone')
        self.assertTrue(process_next_export())
        self.assertEqual(len(self.status(independent)['files']), 3)
        # The same stock can produce a new, previously undelivered pair.
        self.run_import([(NEW_BUYER_HOUSE + '\nContact: Buyer Baru 081300000002', '~ New Buyer')])
        fresh = self.queue(request_id='new-buyer', **params)
        self.assertTrue(process_next_export())
        self.assertEqual(len(self.status(fresh)['files']), 2)

    def test_receipts_are_company_scoped_and_require_ready_recipient(self):
        self.seed()
        from integration import process_next_export
        job = self.queue(delivery_scope='konig-daily')
        self.assertTrue(process_next_export())
        item = self.status(job)['files'][0]
        path = f"/integration/exports/{job['job_id']}/files/{item['file_id']}/delivered"
        self.call(self.workflow, path, 'POST', {'delivery_id': 'gowa-success'}, status=409)
        _, _, other = self.make_company('Other Delivery')
        key = self.call(other, '/integration/keys', 'POST', {'name': 'Other'}, status=201)
        self.call(self.bearer(key['token']), path, 'POST', {'delivery_id': 'foreign'}, status=404)
        self.call(self.workflow, '/integration/exports', 'POST', {'request_id': 'bad-unmatched',
            'delivery_scope': 'konig-daily', 'statuses': 'unmatched'}, status=400)


if __name__ == '__main__':
    unittest.main()
