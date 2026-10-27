"""Location-index editing API: clusters, aliases, neighbour links, areas; validation and pending reprocess."""
import unittest
from urllib.parse import quote

from harness import ServerTestCase
from test_location_index import table

CSV = table([[1, 'Teluk Golf', 'tg', 'CitraLand', 'Woodland (1.2)'],
             [2, 'Woodland', '', 'CitraLand', 'Teluk Golf (1.2)'],
             [3, 'Pakuwon City Cluster', 'pcc', 'Pakuwon City', '']])


class LocationRoutesTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.owner, self.boss = self.make_company('Locations')
        self.member, self.staff = self.make_member(self.boss)

    def write(self, path, payload=None, method='POST', client=None, status=200):
        return self.call(client or self.boss, '/location-index' + path, method, payload, status=status)

    def catalog(self, search=''):
        return self.call(self.staff, '/location-index?search=' + quote(search))

    def imported(self):
        preview = self.write('/preview', {'text': CSV, 'source_name': 'clusters.csv'})
        return self.write('/import', {'text': CSV, 'source_name': 'clusters.csv', 'revision': preview['revision']})

    def jobs(self):
        from db import connect
        with connect() as conn:
            return conn.execute('SELECT * FROM xm.maintenance_jobs WHERE company_id=%s', (self.company['company_id'],)).fetchall()

    def test_members_read_but_only_company_admins_edit(self):
        self.imported()
        for path, method, payload in [('/clusters', 'POST', {'name': 'X Cluster', 'aliases': [], 'area': ''}),
                                      ('/clusters/woodland', 'DELETE', None),
                                      ('/aliases', 'POST', {'cluster_id': 'woodland', 'alias': 'wl'}),
                                      ('/aliases?cluster_id=teluk%20golf&alias=tg', 'DELETE', None),
                                      ('/edges', 'POST', {'a': 'teluk golf', 'b': 'pakuwon city cluster', 'distance_km': 2}),
                                      ('/edges?a=teluk%20golf&b=woodland', 'DELETE', None)]:
            with self.subTest(path=path, method=method):
                self.write(path, payload, method, client=self.staff, status=403)
        self.assertEqual(self.catalog()['total'], 3)
        self.assertEqual(self.call(self.staff, '/location-index/areas')['areas'],
                         [{'area': 'CitraLand', 'clusters': 2}, {'area': 'Pakuwon City', 'clusters': 1}])

    def test_import_and_messages_are_english_and_only_mark_results_out_of_date(self):
        self.process(self.upload(self.owner))
        stale = self.write('/import', {'text': CSV, 'revision': 'old'}, status=409)['detail']
        self.assertIn('Your locations changed since the check', stale)
        self.assertIn('Use the columns', self.write('/preview', {'text': 'a,b\n1,2'}, status=400)['detail'])
        result = self.imported()
        self.assertEqual((result['clusters'], result['pairs'], result['job']), (3, 1, None))
        self.assertEqual(self.jobs(), [])
        status = self.call(self.staff, '/reprocess/status')
        self.assertEqual((status['pending'], status['reasons']), (True, ['Location index updated']))
        self.assertEqual(self.catalog()['sources'], ['clusters.csv'])
        self.assertEqual(self.call(self.staff, '/location-index/neighbors?cluster=nowhere', status=404)['detail'], 'Cluster not found.')
        # Importing the same table again changes nothing.
        preview = self.write('/preview', {'text': CSV})
        self.assertTrue(self.write('/import', {'text': CSV, 'revision': preview['revision']})['unchanged'])

    def test_create_update_rename_and_delete_clusters(self):
        self.imported()
        created = self.write('/clusters', {'name': ' Bukit  Palma ', 'aliases': ['BP', 'bp', 'Bukit Palma', ''], 'area': 'CitraLand'})
        self.assertEqual(created['cluster'], {'id': 'bukit palma', 'name': 'Bukit Palma', 'aliases': ['BP'], 'area': 'CitraLand'})
        self.assertIsNone(created['job'])
        self.assertEqual(created['revision'], self.catalog()['revision'])
        self.assertEqual(self.call(self.staff, '/location-index/areas')['areas'][0], {'area': 'CitraLand', 'clusters': 3})
        self.assertIn('already exists', self.write('/clusters', {'name': 'bukit palma', 'aliases': [], 'area': ''}, status=409)['detail'])
        self.assertIn('already used by the cluster Teluk Golf',
                      self.write('/clusters', {'name': 'New One', 'aliases': ['TG'], 'area': ''}, status=409)['detail'])
        self.assertIn('A cluster named Woodland already exists',
                      self.write('/clusters', {'name': 'Woodland', 'aliases': [], 'area': '', 'original_id': 'bukit palma'}, status=409)['detail'])
        self.assertIn('Cluster name is required', self.write('/clusters', {'name': ' -- ', 'aliases': [], 'area': ''}, status=400)['detail'])
        self.assertIn('max. 200', self.write('/clusters', {'name': 'x' * 201, 'aliases': [], 'area': ''}, status=400)['detail'])
        self.assertIn('Cluster not found', self.write('/clusters', {'name': 'Ghost', 'aliases': [], 'area': '', 'original_id': 'ghost'}, status=404)['detail'])
        # Rename keeps the neighbour links under the new id.
        renamed = self.write('/clusters', {'name': 'Teluk Golf CitraLand', 'aliases': ['tg', 'teluk golf'], 'area': 'CitraLand',
                                           'original_id': 'teluk golf'})
        self.assertEqual((renamed['cluster']['id'], renamed['previous_id']), ('teluk golf citraland', 'teluk golf'))
        neighbors = self.call(self.staff, '/location-index/neighbors?cluster=' + quote('teluk golf citraland'))
        self.assertEqual([(n['id'], n['distance_km']) for n in neighbors['neighbors']], [('woodland', 1.2)])
        # The old name is now an alias of the renamed cluster.
        self.assertEqual(self.call(self.staff, '/location-index/neighbors?cluster=' + quote('Teluk Golf'))['cluster']['id'],
                         'teluk golf citraland')
        # Saving the same values again is reported as unchanged.
        same = self.write('/clusters', {'name': 'Teluk Golf CitraLand', 'aliases': ['tg', 'teluk golf'], 'area': 'CitraLand',
                                        'original_id': 'teluk golf citraland'})
        self.assertTrue(same['unchanged'])
        deleted = self.write('/clusters/' + quote('teluk golf citraland'), method='DELETE')
        self.assertEqual((deleted['deleted'], deleted['removed_pairs']), ('teluk golf citraland', 1))
        self.assertEqual(self.catalog()['pairs'], 0)
        self.write('/clusters/' + quote('teluk golf citraland'), method='DELETE', status=404)

    def test_aliases_are_unique_across_clusters(self):
        self.imported()
        added = self.write('/aliases', {'cluster_id': 'woodland', 'alias': 'WL Citra'})
        self.assertEqual(added['cluster']['aliases'], ['WL Citra'])
        self.assertTrue(self.write('/aliases', {'cluster_id': 'woodland', 'alias': 'wl  citra'})['unchanged'])
        self.assertIn('already used by the cluster Teluk Golf',
                      self.write('/aliases', {'cluster_id': 'woodland', 'alias': 'TG'}, status=409)['detail'])
        self.assertIn('Alias is required', self.write('/aliases', {'cluster_id': 'woodland', 'alias': ' '}, status=400)['detail'])
        self.write('/aliases', {'cluster_id': 'nowhere', 'alias': 'x'}, status=404)
        removed = self.write('/aliases?cluster_id=woodland&alias=' + quote('wl citra'), method='DELETE')
        self.assertEqual(removed['cluster']['aliases'], [])
        self.assertIn('Alias not found', self.write('/aliases?cluster_id=woodland&alias=wl', method='DELETE', status=404)['detail'])
        # Resolving by alias works for writes too ("tg" names Teluk Golf).
        self.assertEqual(self.write('/aliases', {'cluster_id': 'tg', 'alias': 'Teluk'})['cluster']['id'], 'teluk golf')

    def test_neighbor_links_are_validated_and_upserted(self):
        self.imported()
        saved = self.write('/edges', {'a': 'Woodland', 'b': 'pcc', 'distance_km': 3.5})
        self.assertEqual(saved['edge'], {'a': 'pakuwon city cluster', 'b': 'woodland', 'distance_km': 3.5})
        updated = self.write('/edges', {'a': 'pakuwon city cluster', 'b': 'woodland', 'distance_km': 2})
        self.assertEqual(updated['edge']['distance_km'], 2)
        self.assertEqual(self.catalog()['pairs'], 2)
        for distance in (0, -1, 4.01, 100):
            with self.subTest(distance=distance):
                self.assertIn('more than 0 and at most 4 km',
                              self.write('/edges', {'a': 'woodland', 'b': 'teluk golf', 'distance_km': distance}, status=400)['detail'])
        self.assertIn('cannot be its own neighbor',
                      self.write('/edges', {'a': 'woodland', 'b': 'Woodland', 'distance_km': 1}, status=400)['detail'])
        self.assertIn('Cluster not found: Atlantis',
                      self.write('/edges', {'a': 'woodland', 'b': 'Atlantis', 'distance_km': 1}, status=404)['detail'])
        neighbors = self.call(self.staff, '/location-index/neighbors?cluster=woodland')['neighbors']
        self.assertEqual([(n['id'], n['distance_km']) for n in neighbors], [('teluk golf', 1.2), ('pakuwon city cluster', 2)])
        self.write('/edges?a=woodland&b=pcc', method='DELETE')
        self.assertIn('not linked', self.write('/edges?a=woodland&b=pcc', method='DELETE', status=404)['detail'])
        self.assertEqual(self.catalog()['pairs'], 1)

    def test_revision_guard_and_pending_reprocess_for_edits(self):
        self.imported()
        revision = self.catalog()['revision']
        self.assertTrue(self.call(self.staff, '/reprocess/status')['pending'] is False)  # no data: nothing out of date
        self.process(self.upload(self.owner))
        edited = self.write('/aliases', {'cluster_id': 'woodland', 'alias': 'wood', 'revision': revision})
        self.assertNotEqual(edited['revision'], revision)
        self.assertEqual(edited['reprocess']['reasons'], ['Location index updated'])
        self.assertIn('Your locations were changed in the meantime',
                      self.write('/edges', {'a': 'woodland', 'b': 'pcc', 'distance_km': 1, 'revision': revision}, status=409)['detail'])
        self.assertIn('changed in the meantime',
                      self.write('/clusters/woodland?revision=' + revision, method='DELETE', status=409)['detail'])
        self.write('/edges', {'a': 'woodland', 'b': 'pcc', 'distance_km': 1, 'revision': edited['revision']})
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.call(self.staff, '/reprocess/status')['reasons'], ['Location index updated'])
        # The edit feeds matching through the resolver at the next reprocess.
        from db import connect
        from location_index import load_index
        with connect() as conn:
            index = load_index(conn, self.company['company_id'])
        self.assertEqual(index.resolve('rumah wood'), ['woodland'])
        self.assertEqual(index.neighbors['woodland']['pakuwon city cluster'], 1)


if __name__ == '__main__':
    unittest.main()
