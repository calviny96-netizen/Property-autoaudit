"""Daily API contracts; database tests use only the disposable harness database."""
import io
import unittest
import uuid
from datetime import datetime
from unittest.mock import patch
from urllib.parse import urlencode
from zipfile import ZipFile

from pypdf import PdfReader
from ai_review import ReviewScope, decision_from_answers, DEFAULT_MODEL
from integration import process_next_export
from internal_api import allowed
from test_integration import WorkflowIntegrationCase


class ReviewDefaultsTests(unittest.TestCase):
    def test_windows_are_resolved_in_wib_and_explicit_all_is_preserved(self):
        with patch('ai_review.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 27)
            scope = ReviewScope()
        self.assertEqual((scope.buyer_date_from, scope.buyer_date_to), ('2026-10-26', '2026-10-27'))
        self.assertEqual((scope.listing_date_from, scope.listing_date_to), ('2026-07-27', '2026-10-27'))
        self.assertEqual((scope.max_pairs, scope.model), (150, DEFAULT_MODEL))
        all_dates = ReviewScope(buyer_date_from='', buyer_date_to='', listing_date_from='', listing_date_to='')
        self.assertEqual(all_dates.buyer_date_from, '')
        self.assertEqual(all_dates.listing_date_from, '')
        legacy = ReviewScope(date_from='2026-09-01', date_to='2026-09-30')
        self.assertEqual(legacy.buyer_date_from, '')

    def test_reject_is_strictly_below_forty_percent(self):
        for probability, expected in ((0, 'reject'), (.3999, 'reject'), (.4, 'unsure'), (.6999, 'unsure'), (.7, 'recommend')):
            self.assertEqual(decision_from_answers({'recommend': {'type': 'noul', 'noul': probability}})['decision'], expected)

    def test_internal_allowlist_does_not_open_secrets_or_arbitrary_routes(self):
        run = str(uuid.uuid4())
        for method, path in [('POST', '/stock/tracked/item'), ('PATCH', '/stock/tracked/item'),
                             ('POST', '/entities/status'), ('GET', f'/ai/review-runs/{run}/results'),
                             ('GET', f'/integration/exports/{run}/download')]:
            self.assertTrue(allowed(method, path), path)
        for method, path in [('GET', '/ai/keys'), ('POST', '/integration/keys'), ('PUT', '/auth/password'),
                             ('GET', '/ai/review-runs/not-a-uuid/results'), ('POST', '/admin/companies')]:
            self.assertFalse(allowed(method, path), path)


class DailyApiTests(WorkflowIntegrationCase):
    def test_export_preview_combined_zip_pdf_and_selection(self):
        self.seed()
        preview = self.call(self.workflow, '/integration/exports/plan', 'POST', {'group_by': 'sender', 'combined': True})
        self.assertTrue(preview['groups'])
        job = self.queue(combined=True)
        self.call(self.workflow, f"/integration/exports/{job['job_id']}/download", status=409)
        self.assertTrue(process_next_export())
        ready = self.status(job)
        self.assertEqual(ready['status'], 'completed', ready.get('error'))
        self.assertTrue(ready['combined'])
        self.assertTrue(all(item['source_type'] == 'both' for item in ready['files']))
        url = ready['download_url'].removeprefix('/api')
        archive = self.call(self.workflow, url)
        with ZipFile(io.BytesIO(archive)) as bundle:
            self.assertEqual(len(bundle.namelist()), len(ready['files']))
            for name in bundle.namelist():
                self.assertTrue(bundle.read(name).startswith(b'%PDF'))
        selected = ready['files'][0]
        pdf = self.call(self.workflow, url + '?' + urlencode({'output': 'pdf', 'group_keys': selected['group_key']}))
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), selected['page_count'])
        self.assertTrue(self.call(self.workflow, url).startswith(b'PK'))
        self.call(self.workflow, url + '?group_keys=missing', status=404)
        jobs = self.call(self.workflow, '/integration/exports?limit=1')['jobs']
        self.assertEqual(jobs[0]['job_id'], job['job_id'])
        self.call(self.workflow, '/integration/exports?limit=0', status=422)
        _, _, other = self.make_company('Foreign exports')
        key = self.call(other, '/integration/keys', 'POST', {'name': 'Other'}, status=201)
        foreign = self.bearer(key['token'])
        self.call(foreign, url, status=404)

    def test_company_header_session_and_watchlist(self):
        self.admin.addheaders = [('X-XM-Company-Id', self.company['company_id'])]
        company = self.call(self.admin, '/company/settings')
        self.assertEqual(company['company_id'], self.company['company_id'])
        added = self.call(self.admin, '/stock/tracked/item', 'POST', {'phone': '081234567890', 'label': 'Sales API'})
        self.assertEqual(added['tracked'][0]['label'], 'Sales API')
        self.staff.addheaders = [('X-XM-Company-Id', 'foreign-company')]
        self.call(self.staff, '/stock/overview', status=403)
        self.admin.addheaders = [('X-XM-Company-Id', 'missing-company')]
        self.call(self.admin, '/company/settings', status=404)
