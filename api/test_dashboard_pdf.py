"""Dashboard -> Export PDF: the endpoint over real HTTP + PostgreSQL, and the pure posting-date helpers."""
import unittest
from datetime import date, datetime
from urllib.parse import urlencode

from harness import ServerTestCase

BUYER_HOUSE = 'Buyer request rumah Surabaya Barat LT 100 Budget 2 M'
BUYER_RUKO = 'Buyer request ruko Rungkut LT 60 budget max 4 M'
LISTING_HOUSE_A = 'Dijual rumah Surabaya Barat LT 100 Harga 1,8 M\nContact: Budi 081234567890'
LISTING_HOUSE_B = 'Dijual rumah Surabaya Barat LT 105 Harga 1,9 M\nHubungi: Sari 6282233744657'
LISTING_RUKO = 'Dijual ruko Rungkut LT 60 LB 120 Harga 3,5 M\nInfo lanjut: Andi 081202310022'


class HelperTests(unittest.TestCase):
    def test_newest_posting_is_the_latest_of_all_postings(self):
        from dashboard_pdf import newest_posting
        self.assertEqual(newest_posting([datetime(2026, 10, 1, 9), datetime(2026, 10, 5, 8), datetime(2026, 10, 3, 12)]), date(2026, 10, 5))
        self.assertEqual(newest_posting([None, date(2026, 10, 2)]), date(2026, 10, 2))
        self.assertIsNone(newest_posting([None]))
        self.assertIsNone(newest_posting([]))

    def test_stock_age_buckets_with_percentages(self):
        from dashboard_pdf import stock_age
        today = date(2026, 10, 21)
        dates = [date(2026, 10, 21), date(2026, 10, 14), date(2026, 10, 13), date(2026, 9, 21), date(2026, 9, 20), None]
        buckets = stock_age(dates, today)
        self.assertEqual([(b['label'], b['count'], b['pct']) for b in buckets],
                         [('<= 7 hari', 2, 40), ('8-30 hari', 2, 40), ('> 30 hari', 1, 20)])
        self.assertEqual([b['count'] for b in stock_age([], today)], [0, 0, 0])

    def test_posting_histogram_per_day_for_short_periods_and_per_week_for_long_ones(self):
        from dashboard_pdf import posting_histogram
        dates = [datetime(2026, 10, 2, 10), date(2026, 10, 2), date(2026, 10, 5), date(2026, 9, 1), None]
        kind, labels, values, outside = posting_histogram(dates, date(2026, 10, 1), date(2026, 10, 7))
        self.assertEqual(kind, 'day')
        self.assertEqual(len(labels), 7)
        self.assertEqual(values, [0, 2, 0, 0, 1, 0, 0])
        self.assertEqual(outside, 1)
        kind, labels, values, outside = posting_histogram(dates, date(2026, 9, 1), date(2026, 10, 7))
        self.assertEqual(kind, 'week')
        self.assertTrue(all(label.weekday() == 0 for label in labels))
        self.assertEqual(sum(values), 4)
        self.assertEqual(outside, 0)

    def test_indonesian_formatting(self):
        from dashboard_pdf import listing_summary, pretty_range
        self.assertEqual(pretty_range(date(2026, 10, 13), date(2026, 10, 19)), '13–19 Okt 2026')
        self.assertEqual(pretty_range(date(2026, 9, 28), date(2026, 10, 4)), '28 Sep – 4 Okt 2026')
        row = {'categories': ['house'], 'locations': ['surabaya barat'], 'land_area_min': 100, 'land_area_max': None,
               'building_area_min': None, 'building_area_max': None, 'price_min': 1_800_000_000, 'price_max': None,
               'price_basis': 'total', 'transaction_type': 'sale'}
        self.assertEqual(listing_summary(row), 'Rumah · Surabaya Barat · LT 100 m² · Jual Rp 1,8 M')
        self.assertEqual(listing_summary({'raw_text': 'x' * 100}), 'x' * 80 + '…')


class ExportTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.user, self.boss = self.make_company('Dashboard PDF')
        self.member, self.staff = self.make_member(self.boss, 'Staff')

    def export(self, client, **params):
        return self.call(client, '/dashboard/export.pdf?' + urlencode(params))

    def test_members_download_an_indonesian_pdf_of_the_period(self):
        job = self.upload(self.user, messages=[
            (BUYER_HOUSE, '~ Rina', '2026-10-02T09:00:00'), (BUYER_RUKO, '~ Dodi', '2026-10-03T09:00:00'),
            (LISTING_HOUSE_A, '~ Andi', '2026-10-01T10:00:00'), (LISTING_HOUSE_A, '~ Andi', '2026-10-05T10:00:00'),  # reposted
            (LISTING_HOUSE_B, '~ Andi', '2026-10-03T10:00:00'), (LISTING_RUKO, '~ Budi', '2026-10-04T10:00:00'),
        ])
        self.process(job)
        with self.subTest('newest posting date per listing'):
            from db import connect
            from dashboard_pdf import ready_listings_by_sales
            from tenant import workspace_scope
            with workspace_scope(self.company['company_id']), connect() as conn:
                sales = ready_listings_by_sales(conn, 'sender')
            self.assertEqual([(s['label'].split(' (')[0], len(s['listings'])) for s in sales], [('Andi', 2), ('Budi', 1)])
            newest = {row['public_id']: row['newest'] for row in sales[0]['listings']}
            self.assertEqual(sorted(newest.values(), reverse=True), [date(2026, 10, 5), date(2026, 10, 3)])
            self.assertEqual(sales[0]['listings'][0]['newest'], date(2026, 10, 5))   # newest first
            self.assertTrue(all(key.startswith('L-') for key in newest))
        custom = self.export(self.staff, period='custom', date_from='2026-10-01', date_to='2026-10-07')
        self.assertTrue(custom.startswith(b'%PDF'))
        self.assertGreater(len(custom), 20_000)
        last30 = self.export(self.staff, period='last30')
        self.assertTrue(last30.startswith(b'%PDF'))
        self.assertGreater(len(last30), 20_000)
        from io import BytesIO
        from pypdf import PdfReader
        pages = len(PdfReader(BytesIO(custom)).pages)
        self.assertGreaterEqual(pages, 2)
        self.assertIn(f'Hal. 1 dari {pages}', PdfReader(BytesIO(custom)).pages[0].extract_text())
        self.assertIn('Laporan Dashboard', PdfReader(BytesIO(custom)).pages[0].extract_text())

    def test_content_type_filename_and_bad_period(self):
        import urllib.request
        from urllib.parse import quote
        req = urllib.request.Request(self.base + '/dashboard/export.pdf?period=last_week')
        response = self.staff.open(req, timeout=30)
        with response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get('Content-Type'), 'application/pdf')
            disposition = response.headers.get('Content-Disposition')
            self.assertIn('attachment; filename="Laporan-Dashboard-Isolation-Dashboard-PDF-', disposition)
            self.assertTrue(response.read().startswith(b'%PDF'))
        self.call(self.staff, '/dashboard/export.pdf?period=custom&date_from=' + quote('2026-10-10') + '&date_to=2026-10-01', status=400)


if __name__ == '__main__':
    unittest.main()
