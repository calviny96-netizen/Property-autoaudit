"""v4.1 matching strategy: transaction intent, strict locations, glossary, contacts, prices, status."""
import unittest
from unittest.mock import patch

from harness import ServerTestCase
from location_index import LocationIndex, parse_table
from locations import DEFAULT_LOCATION_PARENTS
from matching_rules import assess_pair, prepare_document
from parser import (DEFAULT_LOCATION_PARENTS as PARSER_PARENTS, approximate_range, extract_contact,
                    location_ancestors, parse_message)
from test_location_index import table

SETTINGS = dict(land_tolerance_pct=10, building_tolerance_pct=20, price_tolerance_pct=10, location_weight_pct=35,
                land_weight_pct=20, building_weight_pct=15, price_weight_pct=20, semantic_weight_pct=5,
                data_quality_weight_pct=5)
M = 1_000_000_000
JT = 1_000_000

LA_RIZ = 'JUAL/SEWA *Apartment La Riz*\nTipe 3BR\nLuas 150 m2\n*130jt/th nego*\n*3,6M nego*'
JUAL_SEWA = '✅ *JUAL 2,1M - NEGO!*\n✅ *SEWA 50 JUTA /THN!*\nRumah Manyar LT 200 LB 250'
HARGA_JUAL_SEWA = 'Dijual Gedung Raya Darmo\nLT 1000\nHarga Jual 75 Milyar nego\nHarga Sewa 2,2 Milyar / Tahun nego'
SALE_RENT = ('SALE &RENT\n\nC I T R A L A N D\n\nRAYA BUKIT GOLF MEDITERANIA\n\nLT; 800 m² / 20 x 40 m\n'
             '2 View Long Golf\nLegalitas SHGB\n\nHarga : 30 juta / m²')
RENTER_B = ('‼️RENTER REQUEST ( B ) ‼️\n1. Rumah Pondok Tjandra only !!\n2. Kamar Tidur : 3 ( tidak mau terlalu kecil )\n'
            '3. Listrik 2200\n4. Budget : 35 jt / thn')
BUYER_F = '‼️BUYER REQUEST ( F ) ‼️\n1. Rumah Surabaya Barat Only !! ( Diutamakan Rumah baru )\n2. Semi/Kosongan Boleh ditawar\n3. Budget 3 M'
BUYER_F_LT = '‼️BUYER REQUEST ( F ) ‼️\n1. Rumah Surabaya Barat Only !!\n2. LT 100\n3. Budget 3 M'
CSV = table([
    [1, 'Woodland (CitraLand)', '', 'CitraLand', 'Teluk Golf (CitraLand) (1.2); Graha Famili (2.5)'],
    [2, 'Teluk Golf (CitraLand)', '', 'CitraLand', ''],
    [3, 'Bukit Palma (CitraLand Utara)', '', 'CitraLand Utara', ''],
    [4, 'Graha Famili', '', '', 'Dian Istana (2)'],
    [5, 'Dian Istana', '', '', ''],
    [6, 'Perumahan Green Royal Residence', '', 'Royal Residence', ''],
    [7, 'Cluster Imam Bonjol', '', '', ''],
    [8, 'Kokoh City', '', 'Kokoh City', ''],
    [9, 'Kokoh City Cluster Bali', '', 'Kokoh City', ''],
])


def row(text, glossary=None):
    value = parse_message(text, glossary=glossary).dict()
    value['document_type'] = value.pop('classification')
    value['extraction_confidence'] = value.pop('confidence')
    value['raw_text'] = text
    return value


def match(buyer, listing, index=None, semantic=50.0):
    request, offer = row(buyer), row(listing)
    if index:
        prepare_document(request, index)
        prepare_document(offer, index)
    return assess_pair(request, offer, SETTINGS, semantic, index)


class TransactionIntent(unittest.TestCase):
    def test_listings_offered_for_sale_and_rent(self):
        p = parse_message(LA_RIZ)
        self.assertEqual(p.transaction_type, 'both')
        self.assertEqual((p.price_max, p.price_basis), (3_600_000_000, 'total'))
        self.assertEqual((p.rent_price_max, p.rent_price_basis), (130 * JT, 'per_year'))
        p = parse_message(JUAL_SEWA)
        self.assertEqual((p.transaction_type, p.price_max, p.rent_price_max, p.rent_price_basis), ('both', 2_100_000_000, 50 * JT, 'per_year'))
        p = parse_message(HARGA_JUAL_SEWA)
        self.assertEqual((p.transaction_type, p.price_max, p.rent_price_max, p.rent_price_basis), ('both', 75 * M, 2_200_000_000, 'per_year'))
        p = parse_message(SALE_RENT)
        self.assertEqual((p.classification, p.transaction_type), ('property_listing', 'both'))
        self.assertEqual((p.price_min, p.price_basis, p.land_area_min), (30 * JT, 'per_m2', 800))
        self.assertIn('citraland', p.locations)
        for text in ('Dijual / Disewakan Rumah Manyar LT 100 Harga 2 M', 'For Sale/Rent Ruko Manyar LT 100 Harga 2 M',
                     'FS/FR : Rumah Raya Darmo LT 516 Harga 30 M'):
            self.assertEqual(parse_message(text).transaction_type, 'both', text)

    def test_existing_tenant_does_not_make_a_rent_offer(self):
        for note in ('Masih disewa sampai 2027', 'Kondisi tersewa', '(posisi tersewa sampai juli 2028, uang sewa untuk pembeli)',
                     'Langsung ada penyewa 400 juta per tahun'):
            p = parse_message('Dijual Ruko Raya Merr\nLT 100 LB 200\nHarga 5 M\n' + note)
            self.assertEqual((p.transaction_type, p.price_max, p.price_basis), ('sale', 5 * M, 'total'), note)

    def test_request_intent(self):
        self.assertEqual(parse_message(RENTER_B).transaction_type, 'rent')
        self.assertEqual(parse_message(BUYER_F).transaction_type, 'sale')
        self.assertEqual(parse_message('*BUYER REQUEST*\n- Beli/Sewa Rumah Daerah Wiyung\n- Budget 2M').transaction_type, 'both')
        self.assertEqual(parse_message('Dicari unit apartemen Voila\nBisa sewa, bisa beli').transaction_type, 'both')
        self.assertEqual(parse_message('*HOT BUYER*\nDicari rumah untuk Sewa 2th\nBudget 100jt/th').transaction_type, 'rent')
        self.assertEqual(parse_message('Dicari beli ruko untuk disewakan lagi, budget 3M').transaction_type, 'sale')

    def test_transaction_gate_and_price_per_transaction(self):
        renter = 'Renter request rumah Manyar LT 200\nBudget 60 jt/th'
        self.assertIsNone(match(renter, 'Dijual rumah Manyar LT 200 Harga 2 M'))
        both = match(renter, JUAL_SEWA)
        self.assertIsNotNone(both)
        self.assertIn('Rent price within budget', both['explanation'])
        self.assertIsNone(match('Renter request rumah Manyar LT 200\nBudget 40 jt/th', JUAL_SEWA))
        buyer = 'Buyer request rumah Manyar LT 200\nBudget 2,5 M'
        self.assertIsNone(match(buyer, 'Disewakan rumah Manyar LT 200 Harga 50 jt/th'))
        self.assertIn('Price within budget', match(buyer, JUAL_SEWA)['explanation'])
        self.assertIsNone(match('Buyer request rumah Manyar LT 200\nBudget 1,5 M', JUAL_SEWA))
        wants_both = 'Cari sewa / beli rumah Manyar LT 200'
        self.assertIsNotNone(match(wants_both, 'Dijual rumah Manyar LT 200 Harga 2 M'))
        self.assertIsNotNone(match(wants_both, 'Disewakan rumah Manyar LT 200 Harga 50 jt/th'))


class StrictLocations(unittest.TestCase):
    def setUp(self):
        self.index = LocationIndex(parse_table(CSV))

    def test_strict_detection(self):
        p = parse_message(RENTER_B)
        self.assertTrue(p.location_strict)
        self.assertIn('pondok tjandra', p.strict_locations)
        self.assertEqual(parse_message(BUYER_F).strict_locations, ['surabaya barat'])
        for text, expected in [('Buyer request rumah Citraland only', ['citraland']),
                               ('Buyer request ruko only di Manyar', ['manyar']),
                               ('Buyer request rumah hanya di Pakuwon Indah / Graha Famili', ['pakuwon indah', 'graha famili']),
                               ('Buyer request gudang khusus area Margomulyo', ['margomulyo']),
                               ('Buyer request rumah wajib di Wiyung', ['wiyung']),
                               ('Buyer request ruko harus di Kertajaya', ['kertajaya']),
                               ('Buyer request rumah Citraland\nselain itu tidak mau', ['citraland']),
                               ('Buyer request rumah Wiyung, lokasi lain tidak mau', ['wiyung']),
                               ('Buyer request rumah Mulyosari, tidak mau lokasi lain', ['mulyosari']),
                               ('Buyer request rumah Sutorejo\ndi luar itu tidak mau', ['sutorejo'])]:
            p = parse_message(text)
            self.assertTrue(p.location_strict, text)
            self.assertEqual(p.strict_locations, expected, text)

    def test_non_location_only_and_exclusions_are_not_strict(self):
        for text in ('*HOT BUYER REQUEST!*\n•Cari Kav/Rumah Citraland\n•Harga BU Only', 'Renter request gudang\nStorage only\nBudget 100jt/th',
                     'Buyer request rumah Citraland\nWajib SHM\nHarus 2 lantai', 'Buyer request rumah dekat UPN only',
                     'Buyer request rumah Citraland\nBudget di bawah 2 M (di atas itu tawarkan saja)'):
            self.assertFalse(parse_message(text).location_strict, text)
        p = parse_message('‼️RENTER REQUEST ( A )‼️\n1. Apartemen Daerah Barat Kecuali Grup Puncak ( LUAS WAJIB DIATAS 100 M² )\n2. Budget 150 jt/thn')
        self.assertFalse(p.location_strict)
        self.assertIn('puncak', p.exclusions)
        self.assertIn('surabaya barat', p.locations)
        self.assertIsNone(match('Renter request apartemen Daerah Barat Kecuali Grup Puncak\nBudget 150 jt/thn',
                                'Disewakan Apartemen Puncak Bukit Golf Surabaya Barat\nHarga sewa 100 jt/th'))

    def test_strict_buyer_inside_outside_and_unknown(self):
        inside = match(BUYER_F_LT, 'Dijual rumah Citraland LT 100 Harga 2,5 M')
        self.assertEqual(inside['status'], 'hot')
        self.assertIn('Inside requested area: Surabaya Barat', inside['explanation'])
        self.assertIsNone(match(BUYER_F_LT, 'Dijual rumah Manyar LT 100 Harga 2,5 M'))
        self.assertIsNone(match(BUYER_F_LT, 'Dijual rumah Surabaya Timur LT 100 Harga 2,5 M'))
        unknown = match(BUYER_F_LT, 'Dijual rumah siap huni LT 100 Harga 2,5 M')
        self.assertEqual(unknown['status'], 'custom')
        self.assertLessEqual(unknown['score'], 79)
        self.assertGreaterEqual(unknown['score'], 60)
        line = 'Location not confirmed — buyer only accepts Surabaya Barat'
        checks = [i for i, text in enumerate(unknown['explanation']) if text.startswith('Needs check:')]
        self.assertLess(unknown['explanation'].index(line), min(checks + [99]))
        self.assertTrue(all(not text.startswith('Needs check:') for text in unknown['explanation'][:unknown['explanation'].index(line)]))
        general = match(BUYER_F_LT, 'Dijual rumah Surabaya LT 100 Harga 2,5 M')
        self.assertEqual(general['status'], 'custom')

    def test_strict_renter_pondok_tjandra(self):
        listing = 'Disewakan rumah Pondok Tjandra Indah\nKT 3\nHarga sewa 30 jt/th'
        self.assertIn(match(RENTER_B, listing)['status'], ('hot', 'warm'))
        self.assertIsNone(match(RENTER_B, 'Disewakan rumah Rungkut Asri\nKT 3\nHarga sewa 30 jt/th'))
        self.assertIsNone(match(RENTER_B, 'Dijual rumah Pondok Tjandra\nKT 3\nHarga 1 M'))

    def test_strict_index_clusters_hierarchy_and_nearby(self):
        buyer = 'Buyer request rumah Citraland only\nLT 100\nBudget 3 M'
        self.assertIn(match(buyer, 'Dijual rumah Woodland LT 100 Harga 2,5 M', self.index)['status'], ('hot', 'warm'))
        # Graha Famili is 2.5 km from Woodland: a nearby alternative is never offered to a strict buyer.
        self.assertIsNone(match(buyer, 'Dijual rumah Graha Famili LT 100 Harga 2,5 M', self.index))
        self.assertIsNone(match('Buyer request rumah Woodland Citraland LT 100 Budget 3 M\nselain itu tidak mau',
                                'Dijual rumah Teluk Golf LT 100 Harga 2,5 M', self.index))
        non_strict = match('Buyer request rumah Woodland LT 100 Budget 3 M', 'Dijual rumah Teluk Golf LT 100 Harga 2,5 M', self.index)
        self.assertIn('Nearby alternative: Woodland (CitraLand) → Teluk Golf (CitraLand) · 1.2 km', non_strict['explanation'])
        # CitraLand Utara is registered as part of CitraLand in the built-in hierarchy.
        self.assertIn(match(buyer, 'Dijual rumah Bukit Palma LT 100 Harga 2,5 M', self.index)['status'], ('hot', 'warm'))


class StrictRefusalIsNotAnExclusion(unittest.TestCase):
    def test_strict_refusal_phrase_does_not_hold_a_hot_match(self):
        index = LocationIndex(parse_table(CSV))
        listing = 'JUAL/SEWA Rumah Teluk Golf\nLT 200\nJual 5M\nSewa 95jt/th'
        for phrase in ('selain itu tidak mau', 'lokasi lain tidak mau', 'tidak mau lokasi lain'):
            result = match(f'RENTER REQUEST\nRumah Citraland only !! {phrase}\nLT 150-250\nBudget 100jt/thn', listing, index)
            self.assertEqual(result['status'], 'hot', phrase)
            self.assertFalse(any('exclusions' in line for line in result['explanation']), phrase)

    def test_real_exclusions_still_need_checking(self):
        result = match('BUYER REQUEST\nRumah Citraland only\nTidak mau hadap barat\nBudget 5M',
                       'Dijual Rumah Teluk Golf Citraland LT 200 Harga 5M', LocationIndex(parse_table(CSV)))
        # v4.8: each refusal is checked on its own ("Tidak mau hadap barat" by the facing rule), no blanket item.
        self.assertIn('Needs check: facing not stated (buyer refuses Barat)', result['explanation'])
        self.assertEqual(result['status'], 'custom')


class LocationKnowledge(unittest.TestCase):
    def test_hierarchy_is_exported(self):
        self.assertIs(PARSER_PARENTS, DEFAULT_LOCATION_PARENTS)
        for child, parent in [('citraland', 'surabaya barat'), ('pakuwon indah', 'surabaya barat'), ('manyar', 'surabaya timur'),
                              ('jemursari', 'surabaya selatan'), ('tunjungan', 'surabaya pusat'), ('kenjeran', 'surabaya utara'),
                              ('surabaya barat', 'surabaya')]:
            self.assertEqual(DEFAULT_LOCATION_PARENTS[child], parent)
        self.assertEqual(location_ancestors('citraland'), ['surabaya barat', 'surabaya'])

    def test_glossary_entries_and_legacy_dict(self):
        glossary = [{'term': 'griya mapan', 'canonical': 'griya mapan', 'kind': 'location', 'parent': 'wiyung'},
                    {'term': 'pcm', 'canonical': 'pakuwon city mall', 'kind': 'location', 'parent': 'pakuwon city'},
                    {'term': 'kost', 'canonical': 'house', 'kind': 'category'},
                    {'alias': 'rmh', 'canonical': 'rumah'}]
        p = parse_message('Dijual rmh Griya Mapan LT 100 Harga 2 M', glossary=glossary)
        self.assertIn('griya mapan', p.locations)
        self.assertIn('rumah', p.normalized_text)
        self.assertEqual(location_ancestors('griya mapan', glossary), ['wiyung', 'surabaya barat', 'surabaya'])
        self.assertIn('pakuwon city mall', parse_message('Dijual apartemen PCM Harga 1 M', glossary=glossary).locations)
        self.assertIn('house', parse_message('Dijual kost 10 kamar LT 200 Harga 3 M', glossary=glossary).categories)
        self.assertIn('custom district', parse_message('Dicari rumah districtx', glossary={'districtx': 'custom district'}).locations)
        # A strict buyer for Surabaya Barat accepts a glossary place inside it.
        buyer, listing = row(BUYER_F_LT, glossary), row('Dijual rumah Griya Mapan LT 100 Harga 2,5 M', glossary)
        index = LocationIndex(glossary=glossary)
        self.assertEqual(assess_pair(buyer, listing, SETTINGS, 50, index)['status'], 'hot')
        self.assertEqual(assess_pair(row(BUYER_F_LT), row('Dijual rumah Griya Mapan LT 100 Harga 2,5 M'), SETTINGS, 50)['status'], 'custom')

    def test_glossary_terms_resolve_in_location_index(self):
        glossary = [{'term': 'cl', 'canonical': 'citraland', 'kind': 'location', 'parent': None},
                    {'term': 'wood', 'canonical': 'woodland', 'kind': 'location', 'parent': None}]
        index = LocationIndex(parse_table(CSV), glossary=glossary)
        self.assertEqual(sorted(index.resolve('rumah cl')), ['bukit palma citraland utara', 'teluk golf citraland', 'woodland citraland'])
        self.assertEqual(index.resolve('Dijual rumah wood', listing=True), ['woodland citraland'])


class ClusterResolution(unittest.TestCase):
    def setUp(self):
        self.index = LocationIndex(parse_table(CSV))

    def test_area_names_expand_and_area_level_listings(self):
        self.assertEqual(sorted(self.index.resolve('Buyer request rumah Citraland')),
                         ['bukit palma citraland utara', 'teluk golf citraland', 'woodland citraland'])
        self.assertEqual(sorted(self.index.resolve('rumah citra land')), sorted(self.index.resolve('rumah citraland')))
        ids = self.index.resolve('SALE & RENT\nC I T R A L A N D\nKavling 800 m2', listing=True)
        self.assertEqual(self.index.scope(ids), ('area', 'citraland'))
        same = self.index.compare('rumah citraland', '', listing_ids=ids)
        self.assertEqual((same['kind'], same['reason']), ('exact', 'Same area: CitraLand'))
        unsure = self.index.compare('rumah woodland', '', listing_ids=ids)
        self.assertEqual(unsure['kind'], 'unknown')
        # Several clusters of one development resolve at area level instead of becoming unknown.
        both = self.index.resolve('Dijual kavling Woodland dan Teluk Golf', listing=True)
        self.assertEqual(self.index.scope(both), ('area', 'citraland'))

    def test_specific_cluster_beats_its_development_and_title_wins(self):
        self.assertEqual(self.index.resolve('Dijual rumah Woodland Citraland', listing=True), ['woodland citraland'])
        text = 'Dijual Rumah Woodland\nCitraland\nLT 200\nHarga 5 M\nDekat Teluk Golf\nAkses Graha Famili 5 menit'
        self.assertEqual(self.index.resolve(text, listing=True), ['woodland citraland'])
        self.assertEqual(self.index.resolve('Dijual rumah dekat Graha Famili', listing=True), [])

    def test_base_names_without_generic_prefix(self):
        self.assertEqual(self.index.resolve('Dijual rumah Green Royal Residence', listing=True), ['perumahan green royal residence'])
        self.assertEqual(self.index.resolve('Dijual ruko Jl. Imam Bonjol Surabaya', listing=True), [])
        self.assertEqual(self.index.resolve('Dijual rumah Cluster Imam Bonjol', listing=True), ['cluster imam bonjol'])
        # A development that shares its name with one of its clusters means the whole development.
        self.assertEqual(sorted(self.index.resolve('rumah Kokoh City')), ['kokoh city', 'kokoh city cluster bali'])


class Contacts(unittest.TestCase):
    def test_topmost_signature_name_and_phone(self):
        cases = [
            ('Harga 2M\n\nMORE INFO:\nJASON || 081703122359\nNATHANIA || 089639872372', 'JASON', '6281703122359', 2),
            ('Harga 9 M\n\n*📲CONTACT:*\n*ROBERT || 081233779070*\n*AMELIA || 085755776067*', 'ROBERT', '6281233779070', 2),
            ('Harga 3 M\n\nHubungi:\n*Tjen Tjen*\n*Galaxy Mayjend*\n*0818309322/ 082233432588*', 'Tjen Tjen', '62818309322', 2),
            ('*Harga: 3.35m*\n\nInfo:\n*Erick Prasetyo*\n*PropNex Dharmahusada*\n*WA 081232785700*', 'Erick Prasetyo', '6281232785700', 1),
            ('*Harga : Rp9.500.000/m²*\n\n_Get This Property Now!_\n*Sonny A&A Property*\n📞 087851953208\n📞 08113080893',
             'Sonny A&A Property', '6287851953208', 2),
            ('Dijual rumah Manyar Harga 2 M\nMore info albert 08123437894390, Maria 01231203120', 'Albert', '628123437894390', 1),
            ('Harga 2M\n\n*Henry WinMax West*\n081237890768\n\n*Natalia WinMax West*\n081231201481', 'Henry WinMax West', '6281237890768', 2),
            ('Harga 1.5M\n\nMore Info : wa.me/6281270906677\n🙅‍♀️Lienny 081270906677\n🙅‍♂️Adi 082228229949\nXavier Marks Vertu',
             'Lienny', '6281270906677', 2),
        ]
        for text, name, phone, phones in cases:
            p = parse_message(text)
            self.assertEqual((p.contact_name, p.contact_phone), (name, phone), text)
            self.assertEqual(len(p.contact_phones), phones, text)
            self.assertEqual(p.contact_phones[0], phone)
        self.assertEqual(extract_contact('Budget 150jt\n\nVera Ang || PropNex Citraland  📲WA: 0812-1697-1412'), ('Vera Ang', ['6281216971412']))

    def test_signature_agency_is_not_a_location(self):
        p = parse_message('Dijual rumah Manyar\nLT 100\nHarga 2 M\n*Erick Prasetyo*\n*PropNex Dharmahusada*\n*WA 081232785700*')
        self.assertNotIn('dharmahusada', p.locations)
        self.assertEqual(p.contact_name, 'Erick Prasetyo')


class Prices(unittest.TestCase):
    def test_approximate_budgets(self):
        expected = {'1M-an': (900 * JT, 1490 * JT), '1 M an': (900 * JT, 1490 * JT), '1man': (900 * JT, 1490 * JT),
                    '1Man': (900 * JT, 1490 * JT), '500jt-an': (490 * JT, 549 * JT), '500 jutaan': (490 * JT, 549 * JT),
                    '15M-an': (14_900 * JT, 15_490 * JT), '1,5M-an': (1490 * JT, 1549 * JT)}
        for written, bounds in expected.items():
            p = parse_message('Buyer request rumah Citraland budget ' + written)
            self.assertEqual((p.price_min, p.price_max), bounds, written)
        self.assertEqual(approximate_range('500', 'jt'), (490 * JT, 549 * JT))
        result = match('Buyer request rumah Citraland LT 100 budget 1M-an', 'Dijual rumah Citraland LT 100 Harga 1,1 M')
        self.assertIn('Price within budget', result['explanation'])
        self.assertEqual(parse_message('Buyer request rumah Surabaya Budget 600-700 jutaaan').price_max, 700 * JT)

    def test_price_per_square_metre_both_ways(self):
        listing = 'Dijual kavling Citraland LT 100\nHarga 1 jt/m2'
        result = match('Buyer request kavling Citraland LT 100 Budget 120 jt', listing)
        self.assertIn('Rp 1 jt/m² × 100 m² ≈ Rp 100 jt — within budget', result['explanation'])
        self.assertIsNone(match('Buyer request kavling Citraland LT 100 Budget 50 jt', listing))
        self.assertIsNotNone(match('Buyer request kavling Citraland LT 100 Budget max 1,2 jt/m2', listing))
        self.assertIsNone(match('Buyer request kavling Citraland LT 100 Budget max 800 rb/m2', listing))
        per_m2 = match('Buyer request kavling Citraland LT 300 Budget max 10 jt/m2', 'Dijual kavling Citraland LT 300 Harga 2,7 M')
        self.assertIn('Rp 2,7 M ÷ 300 m² ≈ Rp 9 jt/m² — within budget', per_m2['explanation'])
        self.assertIsNone(match('Buyer request kavling Citraland LT 300 Budget max 5 jt/m2', 'Dijual kavling Citraland LT 300 Harga 2,7 M'))
        # Without any area the conversion is impossible: never full credit, always capped.
        unknown = match('Buyer request kavling Citraland Budget 1 M', 'Dijual kavling Citraland\nHarga 5 jt/m2')
        self.assertLess(unknown['score'], 80)
        self.assertIn('Needs check: price is per m² but the area is not stated', unknown['explanation'])
        # Apartments use the building area when there is no land area.
        apartment = match('Buyer request apartemen Citraland Budget 500 jt', 'Dijual apartemen Citraland LB 40 m2\nHarga 10 jt/m2')
        self.assertIn('Rp 10 jt/m² × 40 m² ≈ Rp 400 jt — within budget', apartment['explanation'])

    def test_rent_periods(self):
        self.assertEqual(parse_message('Disewakan rumah Manyar\nHarga 5 jt/bln').price_basis, 'per_month')
        monthly = match('Renter request rumah Manyar Budget 70 jt/th', 'Disewakan rumah Manyar\nHarga 5 jt/bln')
        self.assertIn('Rent price within budget', monthly['explanation'])
        self.assertIsNone(match('Renter request rumah Manyar Budget 50 jt/th', 'Disewakan rumah Manyar\nHarga 5 jt/bln'))
        unstated = match('Renter request rumah Manyar Budget 50 jt/th', 'Disewakan rumah Manyar\nHarga 45 jt')
        self.assertIn('Needs check: rent period not stated', unstated['explanation'])
        self.assertLess(unstated['score'], 80)


class StatusAndWording(unittest.TestCase):
    def test_status_values_and_english_explanations(self):
        hot = match('Buyer request rumah Surabaya Barat LT 100 Budget 2 M', 'Dijual rumah Surabaya Barat LT 100 Harga 1,8 M')
        self.assertEqual(hot['status'], 'hot')
        self.assertGreaterEqual(hot['score'], 80)
        warm = match('Buyer request rumah Surabaya Barat LT 100 Budget 2 M', 'Dijual rumah Surabaya Barat LT 105 Harga 1,8 M')
        self.assertEqual(warm['status'], 'warm')
        for result in (hot, warm):
            for line in result['explanation']:
                self.assertNotRegex(line, r'belum|perlu|sesuai|Lokasi|Harga|Kategori')

    def test_custom_requires_sixty_before_the_cap(self):
        # Price and land at the edge of their Warm allowances keep the pre-cap score below 60: Custom, but never
        # stored (matcher.stored needs pre_cap_score ≥ 60 for Custom).
        low = match(BUYER_F_LT, 'Dijual rumah LT 109\nHarga 3,29 M', semantic=0)
        if low:
            self.assertEqual(low['status'], 'custom')
            self.assertLess(low['pre_cap_score'], 60)
            self.assertLess(low['score'], 60)
        within = match(BUYER_F_LT, 'Dijual rumah LT 100\nHarga 3,2 M', semantic=0)
        self.assertEqual(within['status'], 'custom')
        self.assertGreaterEqual(within['pre_cap_score'], 60)
        self.assertIn('Needs check: price Rp 3,2 M is 7% above the budget Rp 3 M (within tolerance)', within['explanation'])


TAGLINE_LISTING = ('*DIJUAL GUDANG FULL BANGUNAN LOKASI TROPODO*\nLT 1000\nHARGA JUAL 4.3 M\n\nInfo\n'
                   'JUAL | BELI | SEWA |  KPR.PROPERTY\n\n      *Albert KS*\nhttps://wa.me/628175133388')
BUYER_16 = 'BUYER REQUEST\n1. Rumah Minimalis Modern Citraland Only !! ( Northwest Jangan Ditawarkan )\n2. LT 200\n3. Budget 4 M'
SATELLITES = table([
    [1, 'Woodland (CitraLand)', '', 'CitraLand', 'Teluk Golf (CitraLand) (1.2)'],
    [2, 'Teluk Golf (CitraLand)', '', 'CitraLand', ''],
    [3, 'Citra Land Puncak Tidar Malang', '', 'CitraLand', ''],
])


class OfferWording(unittest.TestCase):
    def test_agency_taglines_and_ppjb_are_not_offers(self):
        self.assertEqual(parse_message(TAGLINE_LISTING).transaction_type, 'sale')
        self.assertEqual(parse_message('Dijual rumah Manyar LT 100 Harga 2 M\nSell | Buy | Rent\nAlbert 08123456789').transaction_type, 'sale')
        p = parse_message('Disewakan Rumah Citraland\nLT 200\nHarga Rp. 30jt/tahun\nSHM / Perjanjian Pengikatan Jual Beli')
        self.assertEqual((p.transaction_type, p.price_max, p.price_basis), ('rent', 30 * JT, 'per_year'))
        renter = parse_message('RENTER REQUEST\nRumah Citraland\nBudget 30jt/thn\n\nInfo\nJUAL | BELI | SEWA | KPR.PROPERTY\nAlbert KS')
        self.assertEqual(renter.transaction_type, 'rent')
        # An offer for both is not a tagline.
        self.assertEqual(parse_message('*JUAL | SEWA* Rumah Citraland\nLT 200\nHarga 3 M / 100 jt per tahun').transaction_type, 'both')
        self.assertIsNone(match('Hot Renter\nCari gudang di daerah tropodo atau tambak sawah\nBudget 300jt/thn', TAGLINE_LISTING))

    def test_tenant_suitability_and_income_wording_is_not_a_rent_offer(self):
        for note in ('Cocok untuk dihuni maupun disewakan', 'Kondisi masih dikontrakkan sisa 3 tahun', 'Tidak Pernah Disewakan',
                     'Posisi Tersewa Bisa Dapat Cashback Sewa', 'Potensi sewa 80jt/thn', 'Cocok investasi sewa',
                     'Bonus passive income dari sewa', 'Harga sewa kamar 1,5jt/bulan', 'Ada calon bisa langsung sewa 10 tahun',
                     'Bisa disewakan untuk mes karyawan', 'Bangunan milik sendiri, tanah sewa 10 thn'):
            p = parse_message('Dijual Rumah Kost Manyar 20 kamar\nLT 300\nHarga 5 M\n' + note)
            self.assertEqual((p.transaction_type, p.price_max, p.price_basis), ('sale', 5 * M, 'total'), note)
        self.assertIsNone(match('Renter request rumah kost Manyar\nBudget 20 jt/thn',
                                'Dijual Rumah Kost Manyar 20 kamar\nLT 300\nHarga 5 M\nHarga sewa kamar 1,5jt/bulan'))

    def test_request_investment_rent_and_separate_sections(self):
        for line in ('Cari rumah kost aktif Siwalankerto untuk investasi sewa', 'Rumah kost, ROI sewa minimal 8%',
                     'Apartemen Tanglin untuk invest, bisa disewakan', 'Ruko yang sudah ada sewa nya (passive income)'):
            self.assertEqual(parse_message('BUYER REQUEST\n' + line + '\nBudget 3-5 M').transaction_type, 'sale', line)
        numbered = parse_message('*KLIEN REQUEST TODAY*\n1. Buyer cari Rumah Graha Famili luas 150-180m²\n\n'
                                 '2. Renter cari Rumah Minimalis Furnish Graha Famili luas 150-200m², bujet 125jt/th')
        self.assertEqual((numbered.transaction_type, numbered.rent_price_max, numbered.rent_price_basis), ('both', 125 * JT, 'per_year'))
        headed = parse_message('*BUYER*...\n\nCari rumah hitung tanah di *SURABAYA TIMUR*\n\n*Budget max 600 jt*\n\n'
                               '------------------------\n*RENTER*...\n\nCari sewa Rumah siap huni di *DARMO*\n\n*Budget max 60 jt/th*')
        self.assertEqual((headed.transaction_type, headed.price_max, headed.price_basis), ('both', 600 * JT, 'total'))
        self.assertEqual((headed.rent_price_max, headed.rent_price_basis), (60 * JT, 'per_year'))


class OfferPrices(unittest.TestCase):
    def test_sale_and_rent_prices_on_one_line(self):
        for text, sale, rent in [('JUAL/SEWA Apartemen Trillium\n2BR\n💰 Jual 1,65M / Sewa 85jt/thn', 1_650_000_000, 85 * JT),
                                 ('Dijual / disewakan rumah Manyar\nJual 8M sewa 85jt/thn', 8 * M, 85 * JT),
                                 ('Dijual / Disewakan Rumah Manyar\nHarga jual 625 jt / sewa 42 jt/thn', 625 * JT, 42 * JT),
                                 ('*JUAL | SEWA* Rumah Citraland\nHarga 3 M / 100 jt per tahun', 3 * M, 100 * JT)]:
            p = parse_message(text)
            self.assertEqual((p.transaction_type, p.price_max, p.price_basis, p.rent_price_max, p.rent_price_basis),
                             ('both', sale, 'total', rent, 'per_year'), text)
        self.assertIsNone(match('Cari beli apartemen Trillium 2BR\nBudget max 500 jt', 'JUAL/SEWA Apartemen Trillium\n2BR\n💰 Jual 1,65M / Sewa 85jt/thn'))

    def test_reduced_price_keeps_the_rent_offer_and_lease_terms_are_not_bounds(self):
        p = parse_message('Dijual Murah Turun Harga\n*Rumah Royal Residence Greenwich*\nLt : 160\nLb : 160\n*Harga 3.3 M nego*\n*Rent 60jt/thn nego*')
        self.assertEqual((p.transaction_type, p.price_max, p.rent_price_max, p.rent_price_basis), ('both', 3_300_000_000, 60 * JT, 'per_year'))
        p = parse_message('Disewakan gudang Margomulyo\nHarga sewa 125jt/th minimal 2th')
        self.assertEqual((p.price_min, p.price_max), (125 * JT, 125 * JT))
        p = parse_message('Dijual / disewakan rumah Citraland\nHarga jual 2 M nego\nsewa 40 jt/th minimal sewa 2 th')
        self.assertEqual((p.rent_price_min, p.rent_price_max), (40 * JT, 40 * JT))

    def test_malformed_amounts_never_fail_the_message(self):
        for amount in ('1,2,5 M', '2,5,0 M', 'Rp. 1,5,0 M', '1,5.0,0 M', '1,0,0 M'):
            parse_message('Dijual rumah Citraland\nLT 200\nHarga ' + amount)
            parse_message('BUYER REQUEST\nRumah Citraland\nBudget ' + amount)
        self.assertEqual(parse_message('Dijual rumah Citraland\nLT 200\nHarga 1,2,5 M').price_max, 1_250_000_000)

    def test_apartment_unit_size_is_the_building_area(self):
        listing = 'Dijual Apartemen Waterplace Tower A\nLuas 100 m2\n2 BR\nHarga 30jt/m2'
        self.assertEqual(parse_message(listing).building_area_min, 100)
        self.assertEqual(parse_message('Dijual Apartemen Tanglin\nLuas semi gross 120 m2 / nett 93 m2\nHarga 3 M').building_area_min, 93)
        self.assertEqual(parse_message('Dijual rumah Manyar\nLuas 100 m2\nHarga 2 M').building_area_min, None)
        result = match('Buyer request apartemen Waterplace\nBudget max 3,5 M', listing)
        self.assertIn('Rp 30 jt/m² × 100 m² ≈ Rp 3 M — within budget', result['explanation'])
        request = parse_message('‼️RENTER REQUEST ( A )‼️\n1. Apartemen Daerah Barat ( LUAS WAJIB DIATAS 100 M² )\n2. Budget 150 jt/thn')
        self.assertEqual((request.building_area_min, request.building_area_max), (100, None))


class StrictPhrasing(unittest.TestCase):
    def setUp(self):
        self.index = LocationIndex(parse_table(CSV))

    def test_place_refused_after_the_noun(self):
        for text in (BUYER_16, 'BUYER REQUEST\n1. Rumah Citraland Only (Northwest jangan)\n2. Budget 4 M'):
            p = parse_message(text)
            self.assertEqual(p.strict_locations, ['citraland'], text)
            self.assertIn('northwest', p.exclusions)
            self.assertNotIn('northwest', p.locations)
        self.assertIsNotNone(match(BUYER_16, 'Dijual rumah Woodland Citraland LT 200 Harga 3,5 M', self.index))
        self.assertIsNotNone(match(BUYER_16, 'Dijual rumah Citraland Bukit Palma LT 200 Harga 3,8 M'))
        self.assertIsNone(match(BUYER_16, 'Dijual rumah Northwest Central Citraland LT 200 Harga 3,5 M', self.index))
        self.assertIsNone(match(BUYER_16, 'Dijual rumah Citraland Northwest LT 200 Harga 3,8 M'))

    def test_ordinary_words_before_only_are_not_places(self):
        listing = 'Dijual rumah Woodland Citraland\nLT 150\nHarga 2,8 M'
        for line in ('Serious buyer only', 'Co-broke only', 'Japri only', 'Survey only weekend'):
            text = 'BUYER REQUEST\nRumah Citraland\nLT 150\n' + line + '\nBudget 3 M'
            self.assertFalse(parse_message(text).location_strict, line)
            self.assertEqual(match(text, listing, self.index)['status'], 'hot', line)
        self.assertFalse(parse_message('BUYER REQUEST\nRumah di Surabaya Barat, LT min 200, serious buyer only\nBudget 3 M').location_strict)
        self.assertEqual(parse_message('BUYER REQUEST\nRumah Palm Spring only\nBudget 3 M').strict_locations, ['palm spring'])

    def test_generic_words_are_never_strict_targets(self):
        renter = 'RENTER REQUEST\nCari sewa rumah northwest (only central or hill)\nBudget 100jt/thn'
        self.assertEqual(parse_message(renter).strict_locations, ['northwest'])
        self.assertIsNone(match(renter, 'Disewakan rumah Pelican Hill Citraland\nHarga 90jt/thn'))
        self.assertIsNone(match(renter, 'Disewakan rumah Diamond Hill\nHarga 90jt/thn'))
        self.assertIsNotNone(match(renter, 'Disewakan rumah Northwest Hill\nHarga 90jt/thn'))
        self.assertEqual(self.index.strict_verdict(['hill'], [], [], 'disewakan rumah darmo hill'), 'unknown')

    def test_more_strict_phrasings(self):
        for text, expected in [('BUYER REQUEST\nRumah Citraland\nSelain Citraland tidak mau\nBudget 3 M', ['citraland']),
                               ('BUYER REQUEST\nRumah Pakuwon Indah, Graha Famili\nselain yang diatas gak mau\nBudget 3 M',
                                ['pakuwon indah', 'graha famili']),
                               ('BUYER REQUEST\nCari Tanah Citraland Utama Saja\nBudget 3 M', ['citraland utama']),
                               ('BUYER REQUEST\nDicari rumah di pelican hill / dempsey only!\nBudget 3 M', ['pelican hill', 'dempsey']),
                               ('BUYER REQUEST\n- Cari beli rumah hitung tanah Manyar Kertoadi saja\nBudget 2 M', ['manyar'])]:
            p = parse_message(text)
            self.assertEqual(p.strict_locations, expected, text)
            self.assertEqual(p.exclusions, [], text)
        self.assertFalse(parse_message('BUYER REQUEST\nRumah Citraland, tanya aja\nBudget 3 M').location_strict)
        self.assertIsNotNone(match('BUYER REQUEST\nDicari rumah di pelican hill / dempsey only!\nLT min 160\nBudget 3,5 M',
                                   'Dijual Rumah Dempsey Hill Citraland Utara\nLT 180\nHarga 3 M'))

    def test_jgn_is_a_negation(self):
        renter = 'RENTER REQUEST\nCari sewa rumah\nDaerah Wiyung - PTC (jgn sampai citraland n Greenlake) kalau waterplace jg Bs\nBudget 100jt/thn'
        p = parse_message(renter)
        self.assertNotIn('citraland', p.locations)
        self.assertIn('waterplace', p.locations)
        self.assertEqual(p.exclusions, ['citraland', 'greenlake'])
        self.assertIsNone(match(renter, 'Disewakan Rumah Bukit Palma Citraland\nHarga sewa 90jt/thn', self.index))
        self.assertIsNotNone(match(renter, 'Disewakan Rumah Waterplace\nHarga sewa 90jt/thn', self.index))
        # A place the buyer also asks for is not excluded as a whole ("Jemursari Regency" is one estate).
        p = parse_message('BUYER REQUEST\nCari rumah Jemursari dan sekitarnya (nggak mau Prapen dan Jemursari Regency)\nBudget 2 M')
        self.assertNotIn('jemursari', p.exclusions)


class PlaceNames(unittest.TestCase):
    def test_places_named_after_another_place_lie_inside_it(self):
        from locations import PREFIX_EXCEPTIONS, default_places
        places = default_places()
        names = sorted(places.known)
        wrong = [(child, parent) for parent in names for child in names
                 if child.startswith(parent + ' ') and child not in PREFIX_EXCEPTIONS and places.relation(child, parent) != 'inside']
        self.assertEqual(wrong, [])

    def test_spelling_variants_are_one_place(self):
        from locations import default_places
        places = default_places()
        for variant, canonical in [('north west', 'northwest'), ('gwalk', 'g walk'), ('the greenlake', 'greenlake'),
                                   ('ptc', 'pakuwon trade center'), ('universitas surabaya', 'ubaya'), ('universitas petra', 'petra'),
                                   ('north west park', 'northwest park')]:
            self.assertEqual(places.canonical(variant), canonical)
            self.assertEqual(places.relation(variant, canonical), 'inside')
        for child, parent in [('northwest park', 'northwest'), ('dempsey hill', 'dempsey'), ('lebak indah', 'lebak'),
                              ('bukit darmo golf', 'bukit darmo'), ('sukolilo park', 'sukolilo'), ('greenlake', 'citraland')]:
            self.assertEqual(places.relation(child, parent), 'inside', child)

    def test_strict_northwest_buyer_accepts_its_sub_areas(self):
        buyer = 'BUYER REQUEST\nRumah Northwest only !!\nLT min 120\nBudget 3 M'
        for place in ('Northwest Park', 'Northwest Lake', 'Northwest Hill', 'Northwest Boulevard', 'North West Park Citraland'):
            self.assertIsNotNone(match(buyer, f'Dijual Rumah {place}\nLT 150\nHarga 2,5 M'), place)
        self.assertIsNone(match(buyer, 'Dijual Rumah Woodland Citraland\nLT 150\nHarga 2,5 M'))


class SatelliteDevelopments(unittest.TestCase):
    def setUp(self):
        self.index = LocationIndex(parse_table(SATELLITES))

    def test_satellites_elsewhere_are_not_the_surabaya_development(self):
        self.assertEqual(sorted(self.index.area_members('citraland')), ['teluk golf citraland', 'woodland citraland'])
        self.assertEqual(self.index.resolve('Dijual Rumah Citraland Puncak Tidar Malang', listing=True), ['citra land puncak tidar malang'])
        for text in ('Dijual Rumah Citraland Driyorejo Gresik', 'Dijual Rumah Citraland Kedamean', 'RUKO CITRALAND DRIYOREJO - Gresik'):
            self.assertEqual(self.index.resolve(text, listing=True), [], text)
        for buyer in ('BUYER REQUEST\nRumah Citraland only !!\nLT min 150\nBudget 3 M', 'BUYER REQUEST\nRumah Citraland\nLT min 150\nBudget 3 M'):
            for place in ('Citraland Driyorejo Gresik', 'Citraland Kedamean', 'Citraland Puncak Tidar Malang'):
                self.assertIsNone(match(buyer, f'Dijual Rumah {place}\nLT 160\nHarga 2,5 M', self.index), (buyer, place))
            self.assertEqual(match(buyer, 'Dijual Rumah Woodland Citraland\nLT 160\nHarga 2,5 M', self.index)['status'], 'hot')

    def test_parsed_places_and_index_must_agree(self):
        ids = ['woodland citraland', 'teluk golf citraland']
        self.assertEqual(self.index.strict_verdict(['citraland'], ['citraland driyorejo', 'gresik'], ids), 'unknown')
        self.assertEqual(self.index.strict_verdict(['citraland'], [], ids), 'inside')
        conflict = self.index.compare('rumah citraland', '', listing_ids=['woodland citraland'], listing_regions=frozenset({'gresik'}))
        self.assertEqual(conflict['kind'], 'unknown')


class LocationEvidence(unittest.TestCase):
    def test_distance_phrases_are_landmarks(self):
        for line in ('* ±6 KM ke PTC', 'Selangkah ke Pakuwon City Mall', '5 menit dari Galaxy Mall', '3 km dari Tol Waru'):
            p = parse_message('*JUAL RUMAH KENCANA SARI TIMUR @DUKUH PAKIS*\nLT 200\nHarga 3 M\n' + line)
            self.assertEqual(p.locations, ['dukuh pakis'], line)

    def test_city_named_alone_is_never_hot(self):
        buyer = 'BUYER REQUEST\nRumah kost Surabaya (Bebas)\nBudget 1 M'
        city = match(buyer, 'Dijual Rumah kost Surabaya\nLT 100\nHarga 900 jt')
        self.assertEqual(city['status'], 'custom')  # v4.8: a city-only request is unclear (Custom, never Warm)
        self.assertIn('Needs check: buyer named only the city; exact area not confirmed', city['explanation'])
        elsewhere = match(buyer, 'Rumah kost 2 lantai siap huni di Sidoarjo\nLT 100\nHarga 900 jt\nDekat tol\nPusat Kota Surabaya (25 menit)')
        self.assertTrue(elsewhere is None or elsewhere['status'] != 'hot')

    def test_place_named_later_in_the_message_is_not_full_credit(self):
        listing = ('RUMAH LEBAK TIMUR SURABAYA TIMUR\nLT 100\nHarga 1,8 M\n\n----\n'
                   'Rumah Ketintang Surabaya Selatan\nLT 100\nHarga 1,7 M')
        result = match('BUYER REQUEST\nCari rumah Ketintang\nBudget 1,5-2 M', listing)
        self.assertNotEqual(result and result['status'], 'hot')
        if result:
            self.assertIn('Needs check: requested location is only mentioned later in the message', result['explanation'])


class EnglishWording(unittest.TestCase):
    def test_requirements_and_index_reasons_read_as_english(self):
        index = LocationIndex(parse_table(CSV))
        result = match('Buyer request rumah siap huni Citraland LT 100 Budget 3 M', 'Dijual rumah Woodland LT 100 Harga 2,5 M', index)
        self.assertIn('Needs check: move-in ready not confirmed', result['explanation'])
        unknown = match('Buyer request rumah Citraland LT 100 Budget 3 M', 'Dijual rumah Surabaya Barat LT 100 Harga 2,5 M', index)
        for line in unknown['explanation']:
            self.assertNotRegex(line, r'location index|not confirmed: ')
        self.assertIn('Needs check: exact location not recognised', unknown['explanation'])
        self.assertEqual(index.area_label('surabaya barat'), 'Surabaya Barat')
        self.assertEqual(index.area_label('citraland'), 'CitraLand')


class SignatureNames(unittest.TestCase):
    def test_signature_variants(self):
        author = '~ Some Author, +62 811-0000-000'
        body = 'Dijual Rumah Citraland\nLT 200 LB 250\nHarga 3 M\n\n'
        for signature, name, phone in [
                ('Hub Yudy Tjondro\nHP/WA\n089505277862', 'Yudy Tjondro', '6289505277862'),
                ('Segera hub: Albert- Five Stars Property\nwa.me/6283812345678', 'Albert', '6283812345678'),
                ('Hubungi Kami Sekarang Juga:\n*Aileen Soe BR Titanium Sby*\n*0813-5785-1888*', 'Aileen Soe BR Titanium Sby', '6281357851888'),
                ('KIEM (LPWE) - 082245954598', 'KIEM', '6282245954598'),
                ('*PropNex | Platinum*\n*Yulius Halim - 081803067885*', 'Yulius Halim', '6281803067885'),
                ('*SUGIANTO LIE*\n_RayWhite Northwest Citraland_\nJUAL | BELI | SEWA | KPR Bank\nwa.me/6281234567890', 'SUGIANTO LIE', '6281234567890'),
                ('More Info :\n*Albert Bebex\'s - KONIG Property*\n+62 821 8899 8990 (WA)', 'Albert Bebex\'s', '6282188998990'),
                ('Info lebih lanjut\nJimmy\n082143120050', 'Jimmy', '6282143120050')]:
            p = parse_message(body + signature, author)
            self.assertEqual((p.contact_name, p.contact_phone), (name, phone), signature)
        p = parse_message(body + 'Belum termasuk pajak\nInfo lebih lanjut\nJimmy\n082143120050', author)
        self.assertEqual(p.contact_name, 'Jimmy')
        # No name in the message: the WhatsApp author as a display name, never "~ Name, +62 …".
        self.assertEqual(parse_message(body + 'Siap huni', author).contact_name, 'Some Author')
        self.assertEqual(parse_message(body + 'Siap huni', '+62 811-0000-000').contact_name, '+62 811-0000-000')


class PoliteContactLabels(unittest.TestCase):
    def test_polite_labels_are_not_names(self):
        body = 'Dijual rumah LT 100 harga 1M\n'
        for signature, name in [('\nPlease contact me\nJohn Tan 081234567890', 'John Tan'), ('\nPls Call\n081234567890', None),
                                ('HUBI : PETER 081234567890', 'PETER'), ('Minat hub Frendy 081234567890', 'Frendy')]:
            parsed = parse_message(body + signature)
            self.assertEqual(parsed.contact_name, name, signature)
            self.assertEqual(parsed.contact_phone, '6281234567890', signature)


class StrictMatchesStoredWithStatus(ServerTestCase):
    def test_matcher_stores_status_and_glossary_feeds_location_index(self):
        from db import connect
        from location_index import load_index
        from matcher import recompute_matches
        from tenant import workspace_scope
        company, owner, boss = self.make_company('Engine')
        texts = [BUYER_F_LT, 'Dijual rumah Citraland LT 100 Harga 2,5 M', 'Dijual rumah Manyar LT 100 Harga 2,5 M',
                 'Dijual rumah siap huni LT 100 Harga 2,5 M']
        self.process(self.upload(owner, texts=texts, agent='Engine Agent'))
        scope = company['company_id']
        with workspace_scope(scope):
            with connect() as conn:
                for doc in conn.execute('SELECT d.id, r.raw_text FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id '
                                        'WHERE d.company_id=%s', (scope,)).fetchall():
                    p = parse_message(doc['raw_text'])
                    conn.execute('UPDATE xm.documents SET transaction_type=%s, location_strict=%s, strict_locations=%s, locations=%s WHERE id=%s',
                                 (p.transaction_type, p.location_strict, p.strict_locations, p.locations, doc['id']))
                conn.commit()
            with patch('matcher.qdrant_query', lambda *a, **kw: []):
                recompute_matches(scope)
            with connect() as conn:
                stored = {r['raw_text']: r for r in conn.execute(
                    'SELECT m.status, m.score, r.raw_text FROM xm.matches m JOIN xm.documents d ON d.id=m.property_listing_id '
                    'JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE m.company_id=%s', (scope,)).fetchall()}
                self.assertEqual(stored[texts[1]]['status'], 'hot')
                self.assertNotIn(texts[2], stored)
                self.assertEqual(stored[texts[3]]['status'], 'custom')
                self.assertLessEqual(float(stored[texts[3]]['score']), 79)
                conn.execute("INSERT INTO xm.glossary(company_id, alias, canonical, kind, parent) VALUES (%s,'cl','citraland','location',NULL),"
                             "(%s,'dian istana','dian istana','location','wiyung')", (scope, scope))
                conn.execute('INSERT INTO xm.location_indexes(company_id, data) VALUES (%s, %s::jsonb)',
                             (scope, __import__('json').dumps(parse_table(CSV))))
                conn.commit()
                index = load_index(conn, scope)
        self.assertEqual(sorted(index.resolve('rumah cl')), ['bukit palma citraland utara', 'teluk golf citraland', 'woodland citraland'])
        self.assertEqual(index.places.ancestors('dian istana'), ('wiyung', 'surabaya barat', 'surabaya'))

    def test_retrieval_reaches_every_pair_the_rules_accept(self):
        """A listing placed only by the location index, or naming only the city, still reaches region buyers."""
        import json
        from db import connect
        from matcher import recompute_matches
        from tenant import workspace_scope
        company, owner, boss = self.make_company('Retrieval')
        loose = 'Buyer request rumah Surabaya Barat LT 100 budget 3 M'
        zebra = 'Dijual rumah Griya Zebra LT 100 Harga 2,5 M\nHub Rina 081311112222'
        city = 'Dijual rumah Surabaya LT 100 Harga 2,5 M\nHub Tono 081322223333'
        self.process(self.upload(owner, messages=[(BUYER_F_LT, 'Ana'), (loose, 'Budi'), (zebra, 'Rina'), (city, 'Tono')], agent='Retrieval'))
        scope = company['company_id']
        data = parse_table(table([[1, 'Zebra Park', 'griya zebra', 'CitraLand', ''], [2, 'Woodland (CitraLand)', '', 'CitraLand', '']]))
        with workspace_scope(scope):
            with connect() as conn:
                conn.execute('INSERT INTO xm.location_indexes(company_id, data) VALUES (%s, %s::jsonb)', (scope, json.dumps(data)))
                conn.commit()
            with patch('matcher.qdrant_query', lambda *a, **kw: []):
                recompute_matches(scope)
            with connect() as conn:
                stored = {(r['b'], r['p']): r['status'] for r in conn.execute(
                    '''SELECT m.status, rb.raw_text b, rp.raw_text p FROM xm.matches m
                       JOIN xm.documents db ON db.id=m.buyer_request_id JOIN xm.raw_messages rb ON rb.id=db.raw_message_id
                       JOIN xm.documents dp ON dp.id=m.property_listing_id JOIN xm.raw_messages rp ON rp.id=dp.raw_message_id
                       WHERE m.company_id=%s''', (scope,)).fetchall()}
        self.assertEqual(stored.get((BUYER_F_LT, zebra)), 'hot')
        self.assertEqual(stored.get((loose, zebra)), 'hot')
        self.assertEqual(stored.get((BUYER_F_LT, city)), 'custom')
        self.assertEqual(stored.get((loose, city)), 'custom')  # v4.3: "Surabaya" alone does not confirm Surabaya Barat


if __name__ == '__main__':
    unittest.main()
