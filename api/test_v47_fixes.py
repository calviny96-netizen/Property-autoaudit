"""v4.7: a buyer who wants an old house to rebuild ("rumah hancur", "hitung tanah", "mau di bangun ulang") never matches a
new / renovated / furnished house, and a buyer without a readable budget is never Hot. Contact numbers are placeholders."""
import unittest

from matching_rules import HOT_MIN_BUYER_DETAILS, _furnishing, _wants_teardown
from test_v43_terms import checks, match

NEEDS_CONDITION = 'Needs check: building condition (buyer wants an old house to rebuild)'


class Teardown(unittest.TestCase):
    BUYER = ('BUYER REQ *RUMAH HANCUR* Surabaya Timur - klampis | Manyar\nLT 200-300\nBudget 2,5 - 3,5 M\n'
             'Budget investor mau di bangun ulang lalu di huni (dimensi proporsional)\nAndi 081200000301')
    RENOVATED = ('Dijual Rumah Manyar Kertoarjo\nLT 250 LB 300\nBaru renovasi, siap huni, full furnish\nSHM\n'
                 'Harga 3,2 M nego\nBudi 081200000302')
    PLAIN = 'Dijual Rumah Manyar Kertoarjo\nLT 250 LB 200\nSHM\nHarga 3,2 M nego\nBudi 081200000302'
    OLD = 'Dijual Rumah lama hitung tanah Manyar Kertoarjo\nLT 250\nSHM\nHarga 3,2 M nego\nBudi 081200000302'

    def test_wording(self):
        for text in ('rumah hancur', 'rumah rusak', 'rumah tua', 'rumah lama', 'bangunan lama', 'hitung tanah',
                     'mau dibangun ulang', 'bangun ulang', 'mau dibongkar', 'bisa di bongkar', 'investor mau di bangun ulang'):
            self.assertTrue(_wants_teardown(text), text)
        for text in ('tidak mau rumah lama', 'rumah baru siap huni', 'jangan rumah tua'):
            self.assertFalse(_wants_teardown(text), text)

    def test_renovated_furnished_house_is_rejected(self):
        self.assertIsNone(match(self.BUYER, self.RENOVATED))
        for word in ('minimalis modern', 'bonus furniture', 'rumah baru gress'):
            self.assertIsNone(match(self.BUYER, self.PLAIN.replace('SHM', 'SHM\n' + word)), word)

    def test_unknown_condition_is_never_hot(self):
        result = match(self.BUYER, self.PLAIN)
        self.assertIsNotNone(result)
        self.assertNotEqual(result['status'], 'hot')
        self.assertIn(NEEDS_CONDITION, checks(result))

    def test_old_house_is_a_positive_reason(self):
        result = match(self.BUYER, self.OLD)
        self.assertIsNotNone(result)
        self.assertNotIn(NEEDS_CONDITION, checks(result))
        self.assertIn('Building condition: old house / land value ✓', result['explanation'])


class NoBudget(unittest.TestCase):
    BUYER = 'Cari rumah Manyar Kertoarjo\nLT 200-300\nSHM\nAndi 081200000303'
    LISTING = 'Dijual Rumah Manyar Kertoarjo\nLT 250 LB 200\nSHM\nHarga 3,2 M nego\nBudi 081200000304'

    def test_buyer_without_budget_is_capped_warm(self):
        result = match(self.BUYER, self.LISTING)
        self.assertIsNotNone(result)
        self.assertNotEqual(result['status'], 'hot')
        self.assertLess(result['score'], 80)
        self.assertIn('Needs check: buyer budget not stated', checks(result))


class Furnishing(unittest.TestCase):
    LISTING = 'Disewakan Apartemen Educity 2BR\n{}\nHarga 40 jt/tahun\nBudi 081200000305'
    BUYER = 'Cari sewa apartemen Educity 2BR {}\nBudget 45 jt/tahun\nAndi 081200000306'

    def pair(self, wants, has):
        return match(self.BUYER.format(wants), self.LISTING.format(has))

    def test_levels(self):
        for text, level in (('unfurnish', 'empty'), ('kosongan', 'empty'), ('tanpa furniture', 'empty'),
                            ('full furnish', 'full'), ('furnished', 'full'), ('bonus furniture', 'full'),
                            ('semi furnish', 'semi'), ('semi kosongan', 'semi'), ('', None)):
            self.assertEqual(_furnishing(text), level, text)
        self.assertEqual(_furnishing('boleh furnish/semi/kosong', True), 'any')
        self.assertEqual(_furnishing('AC, kitchen set'), 'semi')

    def test_unfurnished_buyer(self):
        for has in ('Full furnish', 'Fully furnished', 'bonus furniture', 'AC, kitchen set'):
            self.assertIsNone(self.pair('unfurnished', has), has)
        self.assertIn('Furnishing: unfurnished (kosongan) ✓', self.pair('kosongan', 'Kosongan')['explanation'])

    def test_furnished_buyer(self):
        for has in ('Kosongan', 'unfurnished', 'tanpa perabot'):
            self.assertIsNone(self.pair('full furnish', has), has)
        semi = self.pair('full furnish', 'Semi furnish')
        self.assertNotEqual(semi['status'], 'hot')
        self.assertIn('Needs check: furnishing: buyer wants it fully furnished; listing is semi-furnished', checks(semi))

    def test_semi_buyer(self):
        self.assertIn('Furnishing: semi-furnished ✓', self.pair('semi furnish', 'Semi furnished')['explanation'])
        for has in ('Full furnish', 'Kosongan'):
            result = self.pair('semi kosongan', has)
            self.assertIsNotNone(result, has)
            self.assertNotEqual(result['status'], 'hot')

    def test_any_and_unknown(self):
        self.assertFalse([c for c in checks(self.pair('boleh furnish/semi/kosong', 'Kosongan')) if 'urnish' in c])
        unknown = self.pair('furnished', 'Lantai 10')
        self.assertNotEqual(unknown['status'], 'hot')
        self.assertIn('Needs check: furnishing not stated (buyer wants it fully furnished)', checks(unknown))


class NewHouse(unittest.TestCase):
    BUYER = 'Cari rumah {} Manyar Kertoarjo\nLT 200-300\nBudget 3-3,5 M\nAndi 081200000307'
    LISTING = 'Dijual Rumah Manyar Kertoarjo\nLT 250\n{}\nSHM\nHarga 3,2 M nego\nBudi 081200000308'

    def test_new_buyer_rejects_old_house(self):
        for wants in ('baru', 'gress', 'siap huni', 'tidak perlu renovasi'):
            for has in ('hitung tanah', 'rumah tua', 'perlu renovasi', 'butuh renovasi', 'bangunan lama'):
                self.assertIsNone(match(self.BUYER.format(wants), self.LISTING.format(has)), (wants, has))

    def test_new_buyer_accepts_new_house(self):
        result = match(self.BUYER.format('baru'), self.LISTING.format('Rumah baru gress'))
        self.assertIn('Building condition: new / move-in ready ✓', result['explanation'])
        self.assertIn('Needs check: building condition (buyer wants a new house)',
                      checks(match(self.BUYER.format('baru'), self.LISTING.format('Hadap timur'))))

    def test_teardown_buyer_rejects_new_house(self):
        for has in ('Rumah baru gress', 'Baru renovasi', 'siap huni', 'minimalis modern'):
            self.assertIsNone(match(self.BUYER.format('hitung tanah'), self.LISTING.format(has)), has)


class BuyerDetails(unittest.TestCase):
    LISTING = 'Dijual Rumah Manyar Kertoarjo\nLT 250 LB 200\nKT 4\nSHM\nHadap timur\nHarga 3,2 M nego\nBudi 081200000309'

    def test_vague_buyer_is_capped_warm(self):
        result = match('Cari rumah Manyar budget 3,5 M\nAndi 081200000310', self.LISTING)
        self.assertNotEqual(result['status'], 'hot')
        self.assertTrue(any(c.startswith('Needs check: buyer request has only 4 of 5 required details (missing size)')
                            for c in checks(result)), checks(result))

    def test_detailed_buyer_can_be_hot(self):
        self.assertEqual(HOT_MIN_BUYER_DETAILS, 5)
        result = match('Cari beli rumah Manyar Kertoarjo\nLT 200-300\nKT 4\nSHM\nbudget 3-3,5 M\nAndi 081200000311', self.LISTING)
        self.assertEqual(result['status'], 'hot', result['explanation'])




class RefusedArea(unittest.TestCase):
    """B-AD530 vs L-AE599: "Citraland Utara jangan ditawarkan" refuses Bukit Palma, a CitraLand Utara cluster."""
    BUYER = ('*Dicari Beli Rumah Citraland Cluster Depan & Tengah, tidak mau terlalu dalam*\n{}\nLT 150-250\n'
             'Budget 3-4 M\nAndi 081200000400')
    LISTING = 'Dijual rumah Bukit Palma / Citraland\nLT 200\nSHM\nHarga 3,5 M\nBudi 081200000401'

    def index(self):
        from location_index import LocationIndex, parse_table
        from test_location_index import table
        return LocationIndex(parse_table(table([[1, 'Bukit Palma (CitraLand Utara)', '', 'CitraLand', ''],
                                                [2, 'Taman Puspa Raya', '', 'CitraLand', '']])))

    def test_refused_area_rejects_its_clusters(self):
        index = self.index()
        for phrase in ('Citraland Utara jangan ditawarkan', 'jangan tawarkan Citraland Utara', 'Citraland Utara skip',
                       'kecuali Citraland Utara', 'Citraland Utara no'):
            self.assertIsNone(match(self.BUYER.format(phrase), self.LISTING, index=index), phrase)
        self.assertIsNotNone(match(self.BUYER.format('Citraland Utara jangan ditawarkan'),
                                   self.LISTING.replace('Bukit Palma', 'Taman Puspa Raya'), index=index))


from test_v4_features import BUYER_HOUSE, BUYER_RUKO, V4Case  # noqa: E402


class BuyerListTests(V4Case):
    """GET /buyers/list and the Disabled (= soft 'deleted') buyer status."""

    def buyers(self, **query):
        from urllib.parse import urlencode
        return self.call(self.staff, '/buyers/list?' + urlencode(query))

    def test_list_search_and_disable(self):
        self.seed()
        listed = self.buyers()
        self.assertEqual(sorted(r['public_id'] for r in listed['rows']), ['B-AA000', 'B-AA001'])
        self.assertEqual(listed['counts']['ready'], 2)
        house = next(r for r in listed['rows'] if r['raw_text'] == BUYER_HOUSE)
        self.assertGreaterEqual(int(house['match_count']), 1)
        # Search by public ID, sender name and message text.
        self.assertEqual([r['public_id'] for r in self.buyers(search='b-aa001')['rows']], ['B-AA001'])
        self.assertEqual([r['raw_text'] for r in self.buyers(search='Dodi')['rows']], [BUYER_RUKO])
        self.assertEqual([r['raw_text'] for r in self.buyers(search='Rungkut')['rows']], [BUYER_RUKO])
        self.call(self.staff, '/buyers/list?status=bogus', status=400)
        # A member disables the buyer: it leaves recommendations in both directions and the counters at once.
        listing = self.card(self.rows(direction='property'), 'Budi')
        self.assertTrue(any(r['public_id'] == house['public_id'] for r in self.recs(listing['id'], direction='property')))
        self.call(self.staff, '/entities/status', 'POST', {'ids': [house['public_id']], 'status': 'deleted'})
        self.assertEqual(self.recs(listing['id'], direction='property'), [])
        self.assertNotIn(house['public_id'], [r['public_id'] for r in self.rows(direction='buyer')])
        after = self.card(self.rows(direction='property'), 'Budi')
        self.assertEqual(int(after['hot_count']) + int(after['warm_count']), 0)
        self.assertEqual(self.call(self.staff, '/stats')['buyer_requests'], 1)
        listed = self.buyers()
        self.assertEqual((listed['counts']['ready'], listed['counts']['deleted']), (1, 1))
        self.assertEqual([r['public_id'] for r in self.buyers(status='deleted')['rows']], [house['public_id']])
        self.assertEqual(self.buyers(status='deleted')['rows'][0]['entity_status'], 'deleted')
        # On hold and back to ready.
        self.call(self.staff, '/entities/status', 'POST', {'ids': [house['public_id']], 'status': 'on_hold'})
        self.assertEqual([r['public_id'] for r in self.buyers(status='on_hold')['rows']], [house['public_id']])
        self.call(self.staff, '/entities/status', 'POST', {'ids': [house['public_id']], 'status': 'ready'})
        self.assertTrue(any(r['public_id'] == house['public_id'] for r in self.recs(listing['id'], direction='property')))


if __name__ == '__main__':
    unittest.main()
