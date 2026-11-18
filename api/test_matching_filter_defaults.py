"""Shared page defaults: validated presets, platform-only writes and company isolation."""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from company import CompanyUpdate, MatchingFilterDefaults, put_company_settings
from ai_review import ReviewScope, apply_company_periods
from harness import ServerTestCase


DEFAULTS = {'listing_period': 'last_14_days', 'buyer_period': 'last_3_days'}


class FilterDefaultsPolicyTests(unittest.TestCase):
    def test_ai_review_applies_rolling_defaults_and_respects_explicit_windows(self):
        defaults = {'buyer_period': 'last_7_days', 'listing_period': 'last_3_months'}
        with patch('ai_review.datetime', wraps=datetime) as clock:
            # 31 May in WIB, still 30 May in UTC.
            clock.now.side_effect = lambda tz: datetime.fromisoformat('2026-05-30T18:00:00+00:00').astimezone(tz)
            resolved = apply_company_periods(ReviewScope(), defaults)
            self.assertEqual(resolved.source, 'match')
            self.assertEqual((resolved.buyer_date_from, resolved.buyer_date_to), ('2026-05-25', '2026-05-31'))
            self.assertEqual((resolved.listing_date_from, resolved.listing_date_to), ('2026-02-28', '2026-05-31'))
            manual = ReviewScope(source='match', buyer_date_from='', buyer_date_to='')
            self.assertEqual(apply_company_periods(manual, defaults).buyer_date_from, '')
            self.assertEqual(apply_company_periods(manual, defaults).listing_date_from, '2026-02-28')
            for manual in (ReviewScope(source='recent'), ReviewScope(found_from='2026-05-20'),
                           ReviewScope(date_from='2026-05-20')):
                self.assertIs(apply_company_periods(manual, defaults), manual)

    def test_presets_are_validated_and_both_periods_are_required(self):
        for period in ('all', 'today', 'today_yesterday', 'last_3_days', 'last_7_days', 'last_14_days', 'last_1_month', 'last_3_months'):
            self.assertEqual(MatchingFilterDefaults(listing_period=period, buyer_period=period).buyer_period, period)
        for value in ({'listing_period': 'today'}, {**DEFAULTS, 'buyer_period': 'yesterday; DROP TABLE'},
                      {**DEFAULTS, 'company_id': 'another-company'}):
            with self.assertRaises(ValidationError):
                MatchingFilterDefaults(**value)
        self.assertIsNone(CompanyUpdate(company_name='Example').matching_filter_defaults)

    def test_non_platform_admin_cannot_change_defaults_even_in_mixed_updates(self):
        for role in ('company_admin', 'user'):
            with self.subTest(role=role), patch('company.current_user', return_value={'role': role}), patch('company.connect') as connect:
                with self.assertRaises(HTTPException) as error:
                    put_company_settings(CompanyUpdate(company_name='Changed', matching_filter_defaults=DEFAULTS), None)
                self.assertEqual(error.exception.status_code, 403)
                connect.assert_not_called()

    def test_platform_admin_saves_json_only_in_current_workspace_without_reprocessing(self):
        conn = MagicMock()
        with patch('company.current_user', return_value={'role': 'admin'}), patch('company.connect') as connect, \
             patch('company.company_row', return_value={'matching_mode': 'company'}), \
             patch('company.get_company_settings', return_value={'matching_filter_defaults': DEFAULTS}), \
             patch('company.invalidate_stats'), patch('company.workspace_id', return_value='company-a'):
            connect.return_value.__enter__.return_value = conn
            result = put_company_settings(CompanyUpdate(matching_filter_defaults=DEFAULTS), None)
        self.assertEqual(result['matching_filter_defaults'], DEFAULTS)
        conn.execute.assert_called_once()
        sql, values = conn.execute.call_args.args
        self.assertIn("WHERE company_id = current_setting('xm.workspace_id')", sql)
        self.assertIn('matching_filter_defaults = %s', sql)
        self.assertEqual(values[0].obj, DEFAULTS)
        conn.commit.assert_called_once()


class FilterDefaultsHTTPTests(ServerTestCase):
    def test_company_members_read_shared_defaults_but_only_platform_admin_writes(self):
        _, owner_a, client_a = self.make_company('Default periods A')
        _, owner_b, client_b = self.make_company('Default periods B')
        self.accounts = [owner_a, owner_b]
        _, member = self.make_member(client_a)
        self.assertIsNone(self.call(client_a, '/company/settings')['matching_filter_defaults'])
        self.call(client_a, '/company/settings', 'PUT', {'matching_filter_defaults': DEFAULTS}, status=403)
        self.call(member, '/company/settings', 'PUT', {'matching_filter_defaults': DEFAULTS}, status=403)
        saved = self.call(self.admin, '/company/settings', 'PUT', {'matching_filter_defaults': DEFAULTS}, owner=owner_a)
        self.assertEqual(saved['matching_filter_defaults'], DEFAULTS)
        self.assertEqual(self.call(member, '/company/settings')['matching_filter_defaults'], DEFAULTS)
        self.assertIsNone(self.call(client_b, '/company/settings')['matching_filter_defaults'])
        self.call(self.admin, '/company/settings', 'PUT', {'company_name': 'Other edit'}, owner=owner_a)
        self.assertEqual(self.call(client_a, '/company/settings')['matching_filter_defaults'], DEFAULTS)
        self.call(self.admin, '/company/settings', 'PUT', {'matching_filter_defaults': {**DEFAULTS, 'buyer_period': 'invalid'}}, owner=owner_a, status=422)
        self.call(client_a, '/company/settings', owner=owner_b, status=403)


if __name__ == '__main__':
    unittest.main()
