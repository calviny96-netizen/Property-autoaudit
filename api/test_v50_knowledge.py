"""v5.0: glossary / cluster CSV exports and the platform admin copying them between companies."""
import csv
import io
import unittest
import urllib.request

from harness import ServerTestCase
from test_location_index import table

CLUSTERS = table([[1, 'Zeta Park', 'ZP; Zeta', 'Zeta Town', 'Omega Hills (2)'], [2, 'Omega Hills', '', 'Omega Town', '']])


def rows(text):
    return list(csv.DictReader(io.StringIO(text.lstrip('﻿'))))


class KnowledgeTests(ServerTestCase):
    def import_clusters(self, client, text, name):
        preview = self.call(client, '/location-index/preview', 'POST', {'text': text, 'source_name': name})
        return self.call(client, '/location-index/import', 'POST', {'text': text, 'source_name': name, 'revision': preview['revision']})

    def download(self, client, path):
        req = urllib.request.Request(self.base + path)
        with client.open(req, timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('text/csv', response.headers['Content-Type'])
            self.assertIn('attachment', response.headers['Content-Disposition'])
            return response.read().decode('utf-8')

    def test_exports_use_the_import_columns(self):
        company, user, boss = self.make_company('Export')
        self.call(boss, '/glossary/entries', 'PUT', {'entries': [
            {'term': 'ctl', 'canonical': 'citraland', 'kind': 'synonym', 'parent': None, 'note': 'short form'},
            {'term': 'bukit palma', 'canonical': 'bukit palma', 'kind': 'location', 'parent': 'citraland', 'note': None}]})
        self.import_clusters(boss, CLUSTERS, 'test.csv')
        glossary = rows(self.download(boss, '/glossary/export.csv'))
        self.assertEqual([(r['term'], r['canonical'], r['kind'], r['parent'], r['note']) for r in glossary],
                         [('bukit palma', 'bukit palma', 'location', 'citraland', ''), ('ctl', 'citraland', 'synonym', '', 'short form')])
        clusters = rows(self.download(boss, '/location-index/export.csv'))
        self.assertEqual([r['Cluster'] for r in clusters], ['Omega Hills', 'Zeta Park'])
        zeta = next(r for r in clusters if r['Cluster'] == 'Zeta Park')
        self.assertEqual(zeta['Alias'], 'ZP; Zeta')
        self.assertEqual(zeta['Area/Development'], 'Zeta Town')
        self.assertEqual(zeta['Cluster Terdekat (dalam radius 4 km)'], 'Omega Hills (2)')
        # The export is importable again, unchanged.
        preview = self.call(boss, '/location-index/preview', 'POST', {'text': self.download(boss, '/location-index/export.csv'), 'source_name': 'again.csv'})
        self.assertEqual((preview['clusters'], preview['pairs'], preview['new_clusters'], preview['new_aliases']), (2, 1, 0, 0))
        # Members may download, a stranger may not.
        member, staff = self.make_member(boss)
        self.download(staff, '/glossary/export.csv')

    def test_platform_admin_copies_knowledge_between_companies(self):
        source, s_user, s_boss = self.make_company('Source')
        target, t_user, t_boss = self.make_company('Target')
        self.call(s_boss, '/glossary/entries', 'PUT', {'entries': [
            {'term': 'ctl', 'canonical': 'citraland', 'kind': 'synonym', 'parent': None, 'note': None},
            {'term': 'pkw', 'canonical': 'pakuwon', 'kind': 'synonym', 'parent': None, 'note': None}]})
        self.import_clusters(s_boss, CLUSTERS, 'source.csv')
        self.call(t_boss, '/glossary/entries', 'PUT', {'entries': [{'term': 'ctl', 'canonical': 'citra land', 'kind': 'synonym', 'parent': None, 'note': None}]})
        self.import_clusters(t_boss, table([[1, 'Kappa Cluster', '', 'Kappa Town', '']]), 'target.csv')
        path = f"/admin/companies/{target['company_id']}/copy-knowledge"
        self.call(t_boss, path, 'POST', {'source_company_id': source['company_id']}, status=403)
        self.call(self.admin, path, 'POST', {'source_company_id': target['company_id']}, status=400)
        self.call(self.admin, path, 'POST', {'source_company_id': 'xm-co-missing'}, status=404)
        merged = self.call(self.admin, path, 'POST', {'source_company_id': source['company_id'], 'glossary': True, 'locations': True, 'mode': 'merge'})
        self.assertEqual((merged['glossary_added'], merged['glossary_total']), (1, 2))      # ctl kept as the target wrote it
        self.assertEqual((merged['clusters_added'], merged['links_added'], merged['clusters_total']), (2, 1, 3))
        entries = {e['term']: e['canonical'] for e in self.call(t_boss, '/glossary/entries')['entries']}
        self.assertEqual(entries, {'ctl': 'citra land', 'pkw': 'pakuwon'})
        names = {c['name'] for c in self.call(t_boss, '/location-index?search=')['clusters']}
        self.assertEqual(names, {'Kappa Cluster', 'Zeta Park', 'Omega Hills'})
        # The source is untouched; the target is marked out of date; the copy is in the log.
        self.assertEqual(len(self.call(s_boss, '/glossary/entries')['entries']), 2)
        log = self.call(self.admin, f"/admin/logs?company_id={target['company_id']}&action=knowledge_copied")
        self.assertEqual(log['rows'][0]['detail']['source_company_id'], source['company_id'])
        replaced = self.call(self.admin, path, 'POST', {'source_company_id': source['company_id'], 'glossary': True, 'locations': True, 'mode': 'replace'})
        self.assertEqual((replaced['glossary_added'], replaced['glossary_total'], replaced['clusters_total']), (2, 2, 2))
        entries = {e['term']: e['canonical'] for e in self.call(t_boss, '/glossary/entries')['entries']}
        self.assertEqual(entries, {'ctl': 'citraland', 'pkw': 'pakuwon'})
        companies = {c['company_id']: c for c in self.call(self.admin, '/admin/companies')}
        self.assertEqual((companies[target['company_id']]['glossary_entries'], companies[target['company_id']]['clusters']), (2, 2))


if __name__ == '__main__':
    unittest.main()
