import unittest
from parser import parse_message
from matching_rules import assess_pair

SETTINGS = dict(land_tolerance_pct=10, building_tolerance_pct=20, price_tolerance_pct=20,
                location_weight_pct=30, land_weight_pct=10, building_weight_pct=10,
                price_weight_pct=40, semantic_weight_pct=7, data_quality_weight_pct=3)
LAND_REQUEST = '''*BUYER REQUEST*
- Tanah Spesifikasi buat bangunan office (5 lantai)
- Lokasi Surabaya Barat (lebih diutamakan daerah Mayjend)
- ukuran ± 18 x 30 /±500 m²
- Akses jalan row besar
- Budget menyesuaikan'''
SHOP = '''*Dijual Ruko Dian Istana, Surabaya Barat*
Luas Tanah 112m² (4,5×25)
Luas Bangunan 250m² (3 Lantai)
Hadap Utara
- Cocok untuk kantor atau usaha lain
*Harga 3,1 Milyar Nego*'''


def row(text):
    value = parse_message(text).dict()
    value['extraction_confidence'] = value.pop('confidence')
    return value


def match(buyer, listing, **kwargs):
    return assess_pair(row(buyer), row(listing), SETTINGS, **kwargs)


class MatchingRegression(unittest.TestCase):
    def test_land_for_office_is_not_office_stock(self):
        req = row(LAND_REQUEST)
        self.assertEqual(req['categories'], ['land'])
        self.assertEqual(req['land_area_min'], 500)
        self.assertEqual(row(SHOP)['categories'], ['shophouse'])
        self.assertIsNone(match(LAND_REQUEST, SHOP, semantic=100))

    def test_buyer_request_is_never_recommended_as_stock(self):
        self.assertIsNone(match('Buyer request Apartemen Voila Ciputra World', 'Buyer request Apartemen Voila Ciputra World'))

    def test_small_land_fails_even_perfect_semantic(self):
        self.assertIsNone(match(LAND_REQUEST, 'Dijual tanah Surabaya Barat LT 112 Harga 3 M', semantic=100))

    def test_development_plan_needs_verification_not_existing_floors(self):
        result = match(LAND_REQUEST, 'Dijual tanah Surabaya Barat LT 500 Harga 3 M')
        self.assertIsNotNone(result)
        self.assertLess(result['score'], 80)
        self.assertTrue(any('building 5 floors' in s for s in result['explanation']))

    def test_floor_count_conflict(self):
        self.assertIsNone(match('Buyer request ruko Surabaya Barat 5 lantai', SHOP))

    def test_bedroom_and_facing_conflicts(self):
        buyer = 'Buyer request Apartemen Voila Ciputra World\n3/4 Bedroom\nHadap Selatan\nBudget mengikuti'
        for specs in ['KT 2\nHadap Selatan', 'KT 3\nHadap Utara']:
            self.assertIsNone(match(buyer, 'Dijual Apartemen Voila Ciputra World\n' + specs))
        self.assertLess(match(buyer, 'Dijual Apartemen Voila Ciputra World\nKT 3')['score'], 80)
        # v4.7: "Budget mengikuti" is no budget, so the facing match stays Warm.
        self.assertEqual(match(buyer, 'Dijual Apartemen Voila Ciputra World\nKT 3\nHadap Selatan')['score'], 79)

    def test_unknown_location_cannot_be_hot(self):
        # Citraland (West) and Rungkut (East) are different regions of Surabaya.
        self.assertIsNone(match('Buyer request rumah Citraland Surabaya', 'Dijual rumah Rungkut Surabaya'))
        unconfirmed = match('Buyer request rumah Citraland Surabaya', 'Dijual rumah Surabaya')
        self.assertLess(unconfirmed['score'], 80)
        self.assertIn('Needs check: exact location not confirmed', unconfirmed['explanation'])
        self.assertIsNone(match('Buyer request rumah Surabaya Barat', 'Dijual rumah Jakarta Utara'))
        self.assertIsNone(match('Buyer request rumah Surabaya Barat', 'Dijual rumah Surabaya Timur'))

    def test_named_tower_and_studio_conflicts(self):
        buyer = 'Buyer request Apartemen Voila Ciputra World 3/4 Bedroom'
        self.assertIsNone(match(buyer, 'Dijual Apartemen 3 Bedroom Tower Vue Ciputra World'))
        self.assertIsNone(match(buyer, 'Dijual Apartemen Voila Ciputra World Tipe studio'))

    def test_required_view_needs_evidence(self):
        result = match('Buyer request Apartemen Voila Ciputra World (View Yani Golf)', 'Dijual Apartemen Voila Ciputra World')
        self.assertLess(result['score'],80)
        self.assertIn('Needs check: view yani golf not confirmed',result['explanation'])

    def test_dimensions_cannot_be_replaced_by_equal_area(self):
        self.assertIsNone(match('Buyer request tanah untuk kantor Surabaya LT 500 (18x30)', 'Dijual tanah Surabaya LT 500 (10x50)'))

    def test_unrequested_price_has_no_score_credit(self):
        buyer = row('Buyer request rumah Surabaya Barat')
        listing = row('Dijual rumah Surabaya Barat')
        result = assess_pair(buyer, listing, SETTINGS, 0)
        self.assertEqual(result['price'], 0)
        self.assertAlmostEqual(result['pre_cap_score'], (30*100+3*result['data_quality'])/40, places=2)
        self.assertEqual(result['score'], 79)  # v4.7: no budget, never Hot

    def test_missing_price_or_area_capped(self):
        self.assertLess(match('Buyer request rumah Surabaya Barat LT 100 Budget 2 M', 'Dijual rumah Surabaya Barat')['score'], 80)

    def test_area_not_floor_count_and_approximate(self):
        self.assertIsNone(parse_message('Dijual rumah Surabaya bangunan 3 lantai').building_area_min)
        self.assertEqual(parse_message('Dijual rumah Surabaya LB : ± 129 m²').building_area_min, 129)

    def test_tolerance_and_unknown_transaction_capped(self):
        self.assertLess(match('Buyer request rumah Surabaya LT 100', 'Dijual rumah Surabaya LT 105')['score'], 80)
        req, listing = row('Buyer request rumah Surabaya'), row('Dijual rumah Surabaya')
        listing['transaction_type']='unknown'
        self.assertLess(assess_pair(req,listing,SETTINGS)['score'],80)


if __name__ == '__main__': unittest.main()
