import csv
import io
import unittest
from location_index import parse_table, LocationIndex
from matching_rules import assess_pair
from test_matching_rules import row, SETTINGS

HEADER = ['No','Cluster','Alias','Area/Development','Cluster Terdekat (dalam radius 4 km)']

def table(rows, delimiter='\t'):
    out=io.StringIO();writer=csv.writer(out,delimiter=delimiter);writer.writerow(HEADER);writer.writerows(rows);return out.getvalue()

FIXTURE=table([[1,'Aang Home','aang','Jember','Royal City (1.1); Pajajaran Cluster (3.9); Batas Cluster (4)'],[2,'Royal City','','Jember','Aang Home (1.1)'],[3,'Pajajaran Cluster','','Jember',''],[4,'Batas Cluster','','Jember',''],[5,'Jauh Cluster','','Jember','']])

class LocationIndexRegression(unittest.TestCase):
    def setUp(self):
        self.data=parse_table(FIXTURE)
        self.index=LocationIndex(self.data)

    def test_alias_exact_and_distance_symmetry(self):
        self.assertEqual(self.index.compare('cari rumah aang','dijual rumah Aang Home')['kind'],'exact')
        self.assertEqual(self.index.compare('Aang Home','Royal City')['distance_km'],1.1)
        self.assertEqual(self.index.compare('Royal City','Aang Home')['distance_km'],1.1)

    def test_boundaries_and_missing_edge_no_transitive_inference(self):
        self.assertEqual(self.index.compare('Aang Home','Pajajaran Cluster')['distance_km'],3.9)
        self.assertEqual(self.index.compare('Aang Home','Batas Cluster')['distance_km'],4)
        self.assertEqual(self.index.compare('Aang Home','Jauh Cluster')['kind'],'excluded')
        self.assertEqual(self.index.compare('Royal City','Pajajaran Cluster')['kind'],'excluded')

    def test_only_location_and_unknown_listing(self):
        self.assertEqual(self.index.compare('hanya di Aang Home','Royal City')['kind'],'excluded')
        self.assertEqual(self.index.compare('Aang Home','alamat belum disebut')['kind'],'unknown')

    def test_landmarks_not_listing_address(self):
        self.assertEqual(self.index.resolve('Dijual rumah dekat Aang Home',listing=True),[])
        self.assertEqual(self.index.resolve('Dijual rumah Royal City dekat Aang Home',listing=True),['royal city'])

    def test_additive_csv_and_repeat_import(self):
        csv_text=table([[1,'Baru Cluster','baru','','Royal City (2.2)']],',')
        merged=parse_table(csv_text,self.data)
        self.assertEqual(len(merged['clusters']),6)
        self.assertEqual(len(merged['edges']),4)
        self.assertEqual(parse_table(csv_text,merged),merged)
        self.assertEqual(parse_table(FIXTURE,self.data),self.data)

    def test_conflicting_distance_alias_and_missing_target_rejected(self):
        for rows in [[[1,'Aang Home','','','Royal City (2.2)']],[[1,'Baru Cluster','aang','','']],[[1,'Baru Cluster','','','Missing (1.1)']],[[1,'Baru Cluster','','','Royal City (4.1)']]]:
            with self.assertRaises(ValueError):parse_table(table(rows),self.data)

    def test_parentheses_inside_cluster_name(self):
        value=parse_table(table([[1,'Alpha (Kota)','','','Beta (Area) (1.1)'],[2,'Beta (Area)','','','']]))
        self.assertEqual(value['edges'][0]['distance_km'],1.1)

    def test_ambiguous_short_name_not_resolved(self):
        value=parse_table(table([[1,'Spring (Kota A)','','',''],[2,'Spring (Kota B)','','','']]))
        self.assertEqual(LocationIndex(value).resolve('rumah Spring'),[])

    def test_close_matches_offered_but_size_still_blocks(self):
        req=row('Buyer request rumah Aang Home LT 100 Budget 2 M')
        near=row('Dijual rumah Royal City LT 100 Harga 2 M')
        far=row('Dijual rumah Pajajaran Cluster LT 100 Harga 2 M')
        a=assess_pair(req,near,SETTINGS,location_index=self.index)
        b=assess_pair(req,far,SETTINGS,location_index=self.index)
        self.assertGreaterEqual(a['score'],60);self.assertLess(a['score'],80)
        self.assertGreater(a['score'],b['score'])
        self.assertIn('Nearby alternative: Aang Home → Royal City · 1.1 km', a['explanation'])
        self.assertIsNone(assess_pair(req,row('Dijual rumah Royal City LT 40 Harga 2 M'),SETTINGS,location_index=self.index))
        self.assertIsNone(assess_pair(req,row('Dijual ruko Royal City LT 100 Harga 2 M'),SETTINGS,location_index=self.index))

if __name__=='__main__':unittest.main()
