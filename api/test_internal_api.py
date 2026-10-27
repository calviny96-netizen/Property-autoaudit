import os
import urllib.request
from unittest.mock import patch

from test_integration import WorkflowIntegrationCase
from test_v4_features import BUYER_HOUSE, LISTING_HOUSE_A


class InternalApiTests(WorkflowIntegrationCase):
    def setUp(self):
        super().setUp()
        self.token = 'xm_internal_test-only-not-a-deployment-key'
        env = patch.dict(os.environ, {'XM_INTERNAL_API_KEY': self.token})
        env.start()
        self.addCleanup(env.stop)
        self.internal = self.client_for(self.company['company_id'])

    def client_for(self, company=None, token=None):
        client = urllib.request.build_opener()
        client.addheaders = [('Authorization', 'Bearer ' + (token or self.token))]
        if company:
            client.addheaders.append(('X-XM-Company-Id', company))
        return client

    def test_authentication_target_required_and_restricted_routes(self):
        self.call(self.client_for(token='xm_internal_wrong'), '/stats', status=401)
        self.call(self.client_for(), '/stats', status=400)
        self.call(self.client_for('missing-company'), '/stats', status=404)
        companies = self.call(self.client_for(), '/admin/companies')
        self.assertIn(self.company['company_id'], [c['company_id'] for c in companies])
        self.call(self.internal, '/stats', owner=self.user, status=403)
        self.call(self.internal, '/integration/keys', status=403)
        self.call(self.internal, '/admin/companies', 'POST', {}, status=403)
        self.call(self.internal, '/auth/password', 'PUT', {}, status=403)
        with patch.dict(os.environ, {'XM_INTERNAL_API_KEY': ''}):
            self.call(self.internal, '/stats', status=401)
        # Existing company-specific keys still cannot read dashboard or choose companies.
        self.call(self.workflow, '/dashboard/overview', status=401)

    def test_company_stock_dashboard_and_report_in_both_directions(self):
        job = self.upload(self.user, texts=[BUYER_HOUSE, LISTING_HOUSE_A])
        self.process(job)
        self.assertEqual(self.call(self.internal, '/integration/imports/' + job['id'])['status'], 'completed')
        self.call(self.internal, '/dashboard/overview?period=custom&date_from=2026-09-01&date_to=2026-09-30')
        tracked = self.call(self.internal, '/stock/tracked', 'PUT', {'phones': ['081234567890']})
        self.assertEqual(tracked['tracked'][0]['phone'], '6281234567890')
        self.call(self.internal, '/stock/snapshot', 'POST')
        for direction in ('buyer', 'property'):
            rows = self.call(self.internal, '/workspace?direction=' + direction)['rows']
            source = rows[0]
            groups = self.call(self.internal, '/workspace/recommendations', 'POST',
                               {'direction': direction, 'ids': [source['id']]})['groups']
            target = groups[0]['recommendations'][0]
            pdf = self.call(self.internal, '/export/pdf', 'POST', {'direction': direction,
                'pairs': [{'source_id': source['id'], 'target_id': target['id']}]})
            self.assertTrue(pdf.startswith(b'%PDF'))
        export = self.call(self.internal, '/integration/exports', 'POST',
                           {'request_id': 'internal-test', 'direction': 'property', 'phones': '6281234567890'}, status=202)
        self.call(self.internal, '/integration/exports/' + export['job_id'])
        from integration import process_next_export
        self.assertTrue(process_next_export())
        completed = self.call(self.internal, '/integration/exports/' + export['job_id'])
        self.assertEqual(completed['status'], 'completed')
        self.assertTrue(completed['files'])
        pdf = self.call(self.internal, completed['files'][0]['download_url'].removeprefix('/api'))
        self.assertTrue(pdf.startswith(b'%PDF'))
        filters = {'direction': 'property', 'group_by': 'phone', 'phones': '6281234567890'}
        plan = self.call(self.internal, '/export/all/plan', 'POST', filters)
        self.assertTrue(plan['groups'])
        part = self.call(self.internal, '/export/all/part', 'POST', {
            **filters, 'token': plan['token'], 'index': 0,
            'group_key': plan['groups'][0]['key'], 'offset': 0, 'first_page': 1})
        self.assertGreater(part['pages'], 0)
        pdf = self.call(self.internal, '/export/all/' + plan['token'] + '/download')
        self.assertTrue(pdf.startswith(b'%PDF'))
        other_company, _, _ = self.make_company('Internal alternate')
        other = self.client_for(other_company['company_id'])
        self.assertEqual(self.call(other, '/stock/overview')['tracked'], [])
        self.call(other, '/integration/imports/' + job['id'], status=404)
        self.call(other, '/integration/exports/' + export['job_id'], status=404)
        self.call(self.internal, '/integration/exports/' + export['job_id'], 'DELETE')

    def test_upload_accepts_single_internal_key(self):
        raw = (b'--test\r\nContent-Disposition: form-data; name="file"; filename="cleaned.json"\r\n'
               b'Content-Type: application/json\r\n\r\n{"chats": {}}\r\n--test--\r\n')
        job = self.call(self.internal, '/integration/imports', 'POST', raw=raw,
                        content_type='multipart/form-data; boundary=test', status=202)
        result = self.call(self.internal, '/integration/imports/' + job['id'])
        self.assertEqual(result['company_id'], self.company['company_id'])
        self.assertEqual(result['status'], 'queued')
