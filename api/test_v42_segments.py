"""v4.2: multi-context bubbles, transaction knowledge, tolerance band, exclusions, location radius."""
import unittest

from location_index import LocationIndex, parse_table
from matching_rules import _rule_text, assess_pair, band, hot_price_floor, location_radius, prepare_document, tolerances
from parser import parse_bubble, parse_message
from test_location_index import table

M = 1_000_000_000
JT = 1_000_000
# v4.3: Hot price band 80–100 % of a single budget; the *_down/*_up values are the Warm allowance beyond it.
SETTINGS = dict(hot_price_floor_pct=80, price_down_pct=15, price_up_pct=15, land_down_pct=10, land_up_pct=10, building_down_pct=20,
                building_up_pct=20, location_radius_km=4, location_weight_pct=35, land_weight_pct=20,
                building_weight_pct=15, price_weight_pct=20, semantic_weight_pct=5, data_quality_weight_pct=5)

WIWIK = '''#Buyer 1

Dicari gudang (untuk beli)
wilayah surabaya ke arah waru, tidak sampe margomulyo atau dkp
IMB gudang
Luas 300-500m2
Penyimpanan tinner

Budget max 5M


#Renter 1

Rumah
LT min 300m2

Area lebak, rungkut, darmahusada, ngagel, kenjeran , mulyosari

Peruntukan: kantor & gudang mesin (genst, bor)
Wajib akses yang bisa masuk truk
Prefer hadap selatan

Sewa min 3 tahun
Budget 60 - 120 jt / th

Wiwik Konig property
087851480789'''
MEGA = '''Cari rumah surabaya timur area
- klampis
- ⁠nirwana
- ⁠baruk
Budget 2 Milyar
Bukan gang buntu, akses nyaman

Mega
Konig Property
082141172888'''
BUYER_F = ('‼️BUYER REQUEST ( F ) ‼️\n1. Rumah Surabaya Barat Only !! ( Diutamakan Rumah baru )\n2. Semi/Kosongan Boleh ditawar\n'
           '3. Kamar Minimal 4\n4. Budget 3 M')
EDITION = '''*EDISI PAKUWON CITY 4M > UP.*

*1. IMPERIAL BEACH 2 LANTAI PAKUWON CITY(NEW 100%)*
LT.8x20(160M²) || LB.279M²
KT.4+1 || KM.3+1 || PPJB || Utara || Carpot 2 || PLN. 4400W
*_Harga 5 M_*
•bmb

*2. LONG BEACH 2 LANTAI (FURNISH)*
_(Dari Awal Beli Belum Pernah ditempati)_‼️
LT.10x20(200M²) || LB.211M² || KT.3+1 || KM.4+1 || SHM || Selatan || PLN.4400W
*_Harga 5.1 M, Nego_*
•adri

*3. MOSSEL BAY 2 LANTAI SIAP HUNI*
LT.10x16(160M2) || LB.203M2 || KT.4+1 || KM.3+1 || SHM || Selatan || Furnish
*_Harga 5.3 M, Nego_*
•sea

*More Info :*
*Ferry Jack*
*wa.me/62818579818*'''
KLAMPIS_RENT = 'DISEWAKAN RUMAH DI KLAMPIS\nLT 200 LB 250\nKT 4 KM 3\nHarga 80 jt/thn, min 2 thn\n\nHub Andi 081311112222'


def klampis_sale(price):
    return f'Dijual rumah Klampis Surabaya Timur\nLT 150 LB 200\nKT 3\nHarga {price}\n\nHub Rina 081322223333'


def to_row(doc, raw_text):
    value = doc.dict()
    value['document_type'] = value.pop('classification')
    value['extraction_confidence'] = value.pop('confidence')
    value['raw_text'] = raw_text
    return value


def rows(text):
    return [to_row(doc, text) for doc in parse_bubble(text)]


def row(text):
    found = rows(text)
    assert len(found) == 1, text
    return found[0]


def match(request, listing, settings=SETTINGS, index=None):
    if index:
        prepare_document(request, index)
        prepare_document(listing, index)
    return assess_pair(request, listing, settings, 50.0, index)


class MultiContextBubbles(unittest.TestCase):
    def test_wiwik_buyer_and_renter_become_two_documents(self):
        parts = parse_bubble(WIWIK)
        self.assertEqual(len(parts), 2)
        buyer, renter = parts
        self.assertEqual([(p.segment_index, p.segment_count) for p in parts], [(0, 2), (1, 2)])
        self.assertEqual((buyer.classification, buyer.transaction_type, buyer.categories), ('buyer_request', 'sale', ['warehouse']))
        self.assertEqual((buyer.price_min, buyer.price_max, buyer.price_basis), (None, 5 * M, 'total'))
        self.assertEqual((buyer.land_area_min, buyer.land_area_max), (300, 500))
        self.assertIn('waru', buyer.locations)
        for refused in ('margomulyo', 'dkp'):
            self.assertIn(refused, buyer.exclusions)
            self.assertNotIn(refused, buyer.locations)
        self.assertEqual((renter.classification, renter.transaction_type, renter.categories), ('buyer_request', 'rent', ['house']))
        self.assertEqual((renter.land_area_min, renter.land_area_max), (300, None))
        self.assertEqual((renter.price_min, renter.price_max, renter.price_basis), (60 * JT, 120 * JT, 'per_year'))
        for place in ('lebak', 'rungkut', 'ngagel', 'kenjeran', 'mulyosari'):
            self.assertIn(place, renter.locations)
        self.assertEqual(renter.facing, [])  # "Prefer hadap selatan" is a wish, not a requirement
        for part in parts:
            self.assertEqual((part.contact_name, part.contact_phone, part.contact_phones),
                             ('Wiwik Konig property', '6287851480789', ['6287851480789']))
            self.assertNotIn('087851480789', part.segment_text)
        self.assertTrue(buyer.segment_text.startswith('#Buyer 1') and 'Renter' not in buyer.segment_text)
        self.assertTrue(renter.segment_text.startswith('#Renter 1') and 'gudang (untuk beli)' not in renter.segment_text)
        # Legacy callers still get the whole bubble.
        self.assertEqual(parse_message(WIWIK).segment_count, 1)

    def test_wiwik_parts_only_match_their_own_transaction(self):
        buyer, renter = rows(WIWIK)
        self.assertIn('renter', _rule_text(renter))
        self.assertNotIn('renter', _rule_text(buyer))
        self.assertIsNone(match(buyer, row('Disewakan gudang Waru Sidoarjo\nLuas tanah 400 m2\nHarga 150 jt/th')))
        self.assertIsNone(match(buyer, row('Disewakan gudang Waru Sidoarjo\nLT 400\nHarga 4,5 M')))
        self.assertIsNotNone(match(buyer, row('Dijual gudang Waru Sidoarjo\nLT 400\nHarga 4,5 M')))
        self.assertIsNone(match(buyer, row('Dijual gudang Margomulyo\nLT 400\nHarga 4,5 M')))
        self.assertIsNone(match(buyer, row('Dijual gudang daerah DKP\nLT 400\nHarga 4,5 M')))
        bukit_mas = row('Dijual rumah Wisata Bukit Mas Surabaya Barat\nLT 300 LB 350\nKT 4\nHarga 4,1 M')
        self.assertIsNone(match(renter, bukit_mas))
        self.assertIsNone(match(buyer, bukit_mas))
        self.assertIsNotNone(match(renter, row('Disewakan rumah Rungkut\nLT 320 LB 200\nHarga 100 jt/th')))
        # "Prefer hadap selatan" does not rule out other directions.
        self.assertIsNotNone(match(renter, row('Disewakan rumah Rungkut\nLT 320 LB 200\nHadap utara\nHarga 100 jt/th')))
        self.assertIsNone(match(renter, row('Disewakan rumah Rungkut\nLT 320 LB 200\nHarga 150 jt/th')))  # above 120 jt + 15 %

    def test_mega_cari_rumah_two_milyar_buys(self):
        request = row(MEGA)
        self.assertEqual((request['document_type'], request['transaction_type']), ('buyer_request', 'sale'))
        self.assertEqual((request['price_min'], request['price_max']), (None, 2 * M))
        self.assertEqual((request['contact_name'], request['contact_phone']), ('Mega', '6282141172888'))
        self.assertEqual(request['exclusions'], [])  # "Bukan gang buntu" is no place
        self.assertIsNone(match(request, row(KLAMPIS_RENT)))
        accepted = match(request, row(klampis_sale('1,5 M')))
        self.assertIsNotNone(accepted)
        # v4.3: below the Hot floor (80 % of 2 M = 1,6 M) but inside the Warm band (1,36 M). v4.8: "Bukan gang buntu" is not
        # shown by the listing and the request has no size: Custom.
        self.assertEqual(accepted['status'], 'custom')
        self.assertIn('Needs check: requirement not verified: “bukan gang buntu”', accepted['explanation'])
        self.assertIn('Needs check: price Rp 1,5 M is 25% below the budget Rp 2 M', accepted['explanation'])
        self.assertIsNone(match(request, row(klampis_sale('1,3 M'))))  # below the Warm floor 1,36 M
        self.assertIsNone(match(request, row(klampis_sale('0,9 M'))))
        upper = match(request, row(klampis_sale('2,25 M')))
        self.assertEqual(upper['status'], 'custom')
        self.assertIn('Needs check: price Rp 2,25 M is 12% above the budget Rp 2 M (within tolerance)', upper['explanation'])
        self.assertIsNone(match(request, row(klampis_sale('2,4 M'))))  # above 15 %
        self.assertIn('Price within budget', match(request, row(klampis_sale('1,8 M')))['explanation'])

    def test_numbered_requirements_stay_one_request(self):
        for text in (BUYER_F, '‼️RENTER REQUEST ( B ) ‼️\n1. Rumah Pondok Tjandra only !!\n2. Kamar Tidur : 3\n3. Listrik 2200\n4. Budget : 35 jt / thn',
                     '*Dicari Beli Rumah Graha Family*\nKriteria:\n1. Luas Tanah +-350m²\n2. Bangunan mewah\n3. Tidak mau rumah lama\n4. Budget 10-15 Milyar',
                     'Dijual Rumah Citraland\n1. LT 200 LB 300\n2. KT 4\n3. Harga jual 5 M\n4. Harga sewa 200 jt/th'):
            parts = parse_bubble(text)
            self.assertEqual(len(parts), 1, text)
            self.assertEqual((parts[0].segment_count, parts[0].segment_text), (1, None), text)
        self.assertEqual(parse_bubble(BUYER_F)[0].price_max, 3 * M)

    def test_numbered_offers_split_with_the_shared_header(self):
        parts = parse_bubble(EDITION)
        self.assertEqual(len(parts), 3)
        self.assertEqual([p.classification for p in parts], ['property_listing'] * 3)
        self.assertEqual([p.price_max for p in parts], [5 * M, 5_100_000_000, 5_300_000_000])
        self.assertEqual([p.land_area_min for p in parts], [160, 200, 160])
        for title, part in zip(('IMPERIAL BEACH', 'LONG BEACH', 'MOSSEL BAY'), parts):
            self.assertTrue(part.segment_text.startswith('*EDISI PAKUWON CITY 4M > UP.*'))
            self.assertIn(title, part.segment_text)
            self.assertIn('pakuwon city', part.locations)
            self.assertEqual((part.transaction_type, part.contact_name), ('sale', 'Ferry Jack'))
        self.assertNotIn('LONG BEACH', parts[0].segment_text)
        self.assertNotIn('More Info', parts[2].segment_text)
        # Shared header prices never override the item's own price.
        kavling = parse_bubble('Dijual kavling di Citraland mulai 2 M\n\n1. Blok A LT 200 Harga 2,5 M\n2. Blok B LT 250 Harga 3 M')
        self.assertEqual([(p.categories, p.price_max, p.land_area_min) for p in kavling], [(['land'], 2_500_000_000, 200), (['land'], 3 * M, 250)])

    def test_section_headings_and_offer_blocks(self):
        headed = parse_bubble('*BUYER*...\n\nCari rumah hitung tanah di *SURABAYA TIMUR*\n\n*Budget max 600 jt*\n\n'
                              '------------------------\n*RENTER*...\n\nCari sewa Rumah siap huni di *DARMO*\n\n*Budget max 60 jt/th*')
        self.assertEqual([(p.transaction_type, p.price_max, p.price_basis) for p in headed], [('sale', 600 * JT, 'total'), ('rent', 60 * JT, 'per_year')])
        self.assertNotIn('----', headed[0].segment_text)
        numbered = parse_bubble('*KLIEN REQUEST TODAY*\n1. Buyer cari Rumah Graha Famili luas 150-180m²\n\n'
                                '2. Renter cari Rumah Minimalis Furnish Graha Famili luas 150-200m², bujet 125jt/th')
        self.assertEqual([p.transaction_type for p in numbered], ['sale', 'rent'])
        self.assertTrue(all(p.segment_text.startswith('*KLIEN REQUEST TODAY*') for p in numbered))
        blocks = parse_bubble('DIJUAL RUMAH LEBAK TIMUR\nLT 100 LB 120\nHarga 1,8 M\n\nDIJUAL Rumah Ketintang\nLT 120 LB 150\nHarga 1,7 M')
        self.assertEqual([(p.price_max, p.land_area_min) for p in blocks], [(1_800_000_000, 100), (1_700_000_000, 120)])
        for text in ('DIJUAL RUMAH CITRALAND\nLT 200 LB 300\nHarga 5 M\n\nDijual cepat, harga bisa nego sampai 4,8 M',
                     'Dijual rumah Manyar\nLT 100\nHarga 2 M\n\nDijual rumah Manyar\nLT 100\nHarga 2 M',
                     'HOT BUYER REQUEST\n\nBuyer: cari rumah Citraland\nLT 200\nBudget 3 M'):
            self.assertEqual(len(parse_bubble(text)), 1, text)

    def test_forwarded_compilation_keeps_each_parts_own_contact_and_nested_items(self):
        text = ('Selamat siang\nMohon dibantu hubungi yg bersangkutan langsung\n\n'
                '1.\n*Hot Buyer Request*\ncari gudang pergudangan\nLuas minimal 400-500 m²\nBudget 4-5 M\nLokasi Surabaya atau waru\n\n'
                'Hubungi:\n*ko Lung-Lung RWDP*\n081234951669\n\n\n'
                '2.\n*Hot Renter Request*\nRuko tengah Kota Surabaya\nRuko 1-2 lantai\nBudget 150 jt/th\n\n'
                'Hub :\n*KENFAT*\n081216968787\n\n\n'
                '3.\n*RENTER REQUEST*\n1. Sewa Apartemen Surabaya Timur 2 BR max budget 50jt / th\n'
                '2. Sewa Rumah Surabaya Timur full furnish max budget 100 jt / th\n\n'
                '*ELIA RAY WHITE*\n082333014783')
        parts = parse_bubble(text)
        self.assertEqual([(p.classification, p.transaction_type) for p in parts],
                         [('buyer_request', 'sale'), ('buyer_request', 'rent'), ('buyer_request', 'rent'), ('buyer_request', 'rent')])
        self.assertEqual([p.contact_phone for p in parts], ['6281234951669', '6281216968787', '6282333014783', '6282333014783'])
        self.assertEqual(parts[0].contact_name, 'ko Lung-Lung RWDP')
        self.assertEqual([p.price_max for p in parts], [5 * M, 150 * JT, 50 * JT, 100 * JT])
        self.assertEqual([p.categories for p in parts], [['warehouse'], ['shophouse'], ['apartment'], ['house']])
        self.assertTrue(all('RENTER REQUEST' in p.segment_text for p in parts[2:]))
        self.assertNotIn('koh', parts[0].locations + parts[1].locations)

    def test_too_many_parts_fall_back_to_the_whole_bubble(self):
        items = '\n\n'.join(f'{n}. CLUSTER NOMOR {n}\nLT {100 + n} LB 120 KT 3\nHarga {n + 1} M' for n in range(1, 14))
        parts = parse_bubble('Dijual rumah Citraland\n\n' + items)
        self.assertEqual((len(parts), parts[0].segment_count, parts[0].segment_text), (1, 1, None))


class TransactionKnowledge(unittest.TestCase):
    def test_inferred_transactions(self):
        for text, expected in [('Cari rumah Manyar\nBudget 80 jt', 'rent'), ('Dicari rumah Citraland\nBudget 3 M', 'sale'),
                               ('Cari kavling Citraland\nBudget 150 jt', 'sale'), ('Butuh ruko Manyar\nBudget 120 jt/th', 'rent'),
                               ('Cari rumah Manyar dekat kampus', 'sale'), ('REQUEST rumah Citraland\nBudget 3 M', 'sale'),
                               ('Rumah Manyar LT 100\nHarga 85 jt', 'rent'), ('Kavling Citraland LT 100\nHarga 150 jt', 'sale'),
                               ('Rumah Manyar LT 100\nHarga 2 M', 'sale'), ('Rumah Manyar LT 100 SHM bisa KPR\nHarga 150 jt', 'sale'),
                               ('Apartemen Gunawangsa Merr\nStudio\nStrata Title\nHarga 185 jt', 'sale')]:
            self.assertEqual(parse_message(text).transaction_type, expected, text)

    def test_inferred_conflicts_and_price_bases_reject(self):
        buyer = row('Dicari rumah Manyar\nLT 100\nBudget 2 M')
        self.assertIsNone(match(buyer, row('Rumah Manyar LT 100\nHarga 85 jt')))
        self.assertIsNone(match(buyer, row('Dijual rumah Manyar\nLT 100\nHarga 100 jt/th')))
        self.assertIsNotNone(match(buyer, row('Dijual rumah Manyar\nLT 100\nHarga 1,9 M')))
        renter = row('Cari rumah Manyar\nLT 100\nBudget 80 jt')
        rent = match(renter, row('Disewakan rumah Manyar\nLT 100\nHarga 75 jt/th'))
        self.assertIn('Needs check: rent period not stated', rent['explanation'])
        self.assertIsNone(match(renter, row('Dijual rumah Manyar\nLT 100\nHarga 1,9 M')))


class ToleranceBand(unittest.TestCase):
    def test_land_area_single_value_and_minimum(self):
        request = row('Buyer request rumah Manyar LT 300 Budget 5 M')
        self.assertEqual((request['land_area_min'], request['land_area_max']), (300, 300))
        results = {lt: match(request, row(f'Dijual rumah Manyar LT {lt}\nHarga 4,5 M')) for lt in (260, 270, 300, 330, 340)}
        self.assertIsNone(results[260])
        self.assertIsNone(results[340])
        self.assertEqual(results[300]['status'], 'hot')
        for lt, side in ((270, 'below'), (330, 'above')):
            self.assertEqual(results[lt]['status'], 'warm', lt)
            self.assertIn(f'Needs check: land area {lt} m² is {side} the requested 300 m² (within tolerance)', results[lt]['explanation'])
        self.assertEqual((results[270]['land'], results[330]['land']), (60.0, 60.0))
        minimum = row('Buyer request rumah Manyar LT min 300 Budget 5 M')
        self.assertEqual(match(minimum, row('Dijual rumah Manyar LT 1000\nHarga 4,5 M'))['land'], 100.0)
        self.assertIsNone(match(minimum, row('Dijual rumah Manyar LT 260\nHarga 4,5 M')))

    def test_price_down_setting_and_legacy_keys(self):
        # v4.3: price_down_pct is the Warm allowance below the Hot floor (budget × hot_price_floor_pct).
        request = row('Buyer request rumah Manyar LT 100 Budget 2 M')
        listing = row('Dijual rumah Manyar LT 100\nHarga 1,4 M')
        self.assertEqual(match(request, listing)['status'], 'warm')                      # Warm floor 1,6 M × 0,85 = 1,36 M
        self.assertIsNone(match(request, listing, {**SETTINGS, 'price_down_pct': 10}))  # 1,44 M
        self.assertEqual(match(request, listing, {**SETTINGS, 'hot_price_floor_pct': 70})['status'], 'hot')  # Hot from 1,4 M
        self.assertIsNone(match(request, row('Dijual rumah Manyar LT 100\nHarga 2,2 M'), {**SETTINGS, 'price_up_pct': 5}))
        legacy = dict(land_tolerance_pct=10, building_tolerance_pct=20, price_tolerance_pct=20)
        self.assertEqual((tolerances(legacy, 'price'), tolerances(legacy, 'land'), tolerances(legacy, 'building')),
                         ((15.0, 20.0), (10.0, 10.0), (20.0, 20.0)))
        # The v4.2 floor (price_floor_pct) no longer means anything; the Hot floor has its own setting.
        self.assertEqual(tolerances({'price_floor_pct': 60, 'price_tolerance_pct': 15}, 'price'), (15.0, 15.0))
        self.assertEqual((hot_price_floor({}), hot_price_floor({'hot_price_floor_pct': 90})), (80.0, 90.0))
        self.assertEqual(tolerances({}, 'land'), (15.0, 15.0))
        self.assertEqual(tolerances(SETTINGS, 'land'), (10.0, 10.0))
        self.assertEqual((location_radius({}), location_radius({'location_radius_km': 9}), location_radius({'location_radius_km': 0})), (4.0, 4.0, 0.0))

    def test_band_curves(self):
        # v4.3: Hot = [budget × 80 %, budget]; Warm = 15 % beyond it (2 M → Hot 1,6–2 M, Warm 1,36–2,3 M).
        self.assertEqual(band((None, 2 * M), (1.6 * M, 1.6 * M), 15, 15, price=True), (100.0, 'inside'))
        self.assertEqual(band((None, 2 * M), (1.36 * M, 1.36 * M), 15, 15, price=True), (70.0, 'below'))
        self.assertEqual(band((None, 2 * M), (2.3 * M, 2.3 * M), 15, 15, price=True), (60.0, 'above'))
        self.assertEqual(band((None, 2 * M), (1.35 * M, 1.35 * M), 15, 15, price=True)[0], 0.0)
        self.assertEqual(band((1.5 * M, 2 * M), (1.275 * M, 1.275 * M), 15, 15, price=True), (70.0, 'below'))  # range: [a, b]
        self.assertEqual(band((1.5 * M, 2 * M), (1.5 * M, 1.5 * M), 15, 15, price=True), (100.0, 'inside'))
        self.assertEqual(band((1 * M, None), (9 * M, 9 * M), 15, 15, price=True), (100.0, 'inside'))
        self.assertEqual(band((None, 2 * M), (1.7 * M, 1.7 * M), 15, 15, price=True, hot_floor=90)[1], 'below')
        self.assertEqual(band((None, 300), (100, 100), 10, 10), (100.0, 'inside'))  # area maximum only
        self.assertEqual(band((300, 300), (None, None), 10, 10), (35.0, 'missing'))
        # A hard minimum ("paling min 2300 ga mau dibawah itu") is never loosened.
        self.assertEqual(band((2500, 3500), (2300, 2300), 15, 15, hard_min=2300), (60.0, 'below'))
        self.assertEqual(band((2500, 3500), (2299, 2299), 15, 15, hard_min=2300)[0], 0.0)
        # A listing range that reaches into the band counts with its closest value.
        self.assertEqual(band((None, 2 * M), (1.5 * M, 2.5 * M), 15, 15, price=True), (100.0, 'inside'))

    def test_location_radius_limits_nearby_alternatives(self):
        index = LocationIndex(parse_table(table([[1, 'Alpha Garden', '', '', 'Beta Garden (1.5)'], [2, 'Beta Garden', '', '', '']])))
        request = row('Buyer request rumah Alpha Garden LT 100 Budget 3 M')
        listing = row('Dijual rumah Beta Garden LT 100\nHarga 2,5 M')
        nearby = match(dict(request), dict(listing), SETTINGS, index)
        self.assertIn('Nearby alternative: Alpha Garden → Beta Garden · 1.5 km', nearby['explanation'])
        self.assertIn('Needs check: buyer approval for a nearby location (within 4 km)', nearby['explanation'])
        self.assertIsNone(match(dict(request), dict(listing), {**SETTINGS, 'location_radius_km': 1}, index))
        self.assertIsNone(match(dict(request), dict(listing), {**SETTINGS, 'location_radius_km': 0}, index))
        self.assertIsNotNone(match(dict(request), dict(listing), {**SETTINGS, 'location_radius_km': 1.5}, index))


class Exclusions(unittest.TestCase):
    def test_location_refusals(self):
        for text, refused in [('Cari rumah daerah Wiyung, jangan sampai Citraland\nBudget 3 M', 'citraland'),
                              ('Cari rumah area Surabaya Barat, bukan Citraland\nBudget 3 M', 'citraland'),
                              ('Cari gudang wilayah Surabaya Utara, tdk sampai Margomulyo\nBudget 3 M', 'margomulyo'),
                              ('Cari gudang lokasi Sidoarjo, gak sampe Waru\nBudget 3 M', 'waru')]:
            p = parse_message(text)
            self.assertIn(refused, p.exclusions, text)
            self.assertNotIn(refused, p.locations, text)
        p = parse_message('Cari rumah Manyar\nBudget tidak sampai 2 M\nBukan rumah lama\nBudget 2 M')
        self.assertEqual(p.exclusions, [])
        request = row('Cari rumah daerah Surabaya Barat, jangan sampai Citraland\nBudget 3 M')
        self.assertIsNone(match(request, row('Dijual rumah Citraland LT 100\nHarga 2,5 M')))
        self.assertIsNotNone(match(request, row('Dijual rumah Wiyung LT 100\nHarga 2,5 M')))


GRAHA = '''*HOT LISTING GRAHA PAKUWON CITRALAND*

*Rumah VBR III, Pakuwon Indah*
LT 240 m²
LB 177 m²
KT 3+1 KM 2+1
Jual harga 5,5M turun 5M nett

*Rumah Kost Aktif Citraland Waterfront*
Luas Tanah 240mtr (12x20)
Bangunan 350mtr
Harga 5,8M nego tipis
Okupansi full penghuni. Harga sewa bulanan 3jt/bln.

*Kavling Bukit Golf Internasional BGI Citraland*
1. Blok GC. LT 480. 19x25. PPJB. Harga 15,5jt/m nego
2. Blok GB, dekat komersial. LT 650. 15x43. *SHM*. Harga 16jt/m nego.
3. Blok GG, mainroad row 20 meter. LT 880. 27,5x32. *SHM*. Harga 11,5jt/m nego.

*Kavling Citraland cluster Esplanade Park*
LT 482 m²
Harga 18,5jt/m² nego

*Yoga Versus*
*https://wa.me/+6287878785978*'''


class ReviewFixes(unittest.TestCase):
    """Fixes from the v4.2 review on real data."""

    def test_shared_header_gives_places_but_never_another_parts_asset_or_purpose(self):
        condos = parse_bubble('*HOT LISTING APARTMENT & OFFICE SPACE*\n\n*Dijual Condominium De Residence Waterplace*\nLuas 120 m2\n'
                              'Harga Jual 2M\n\n*Di Jual Rugi Perkantoran SATORIA*\nLuas 35 m2\nHarga Jual Rp. 300Juta Nego\n\nHub Milkhy 081222950303')
        self.assertEqual([p.categories for p in condos], [['apartment'], ['office']])
        stray = parse_bubble('Renter request\n\n1.\n*Hot Buyer Request*\ncari gudang pergudangan\nLuas minimal 400-500 m²\nBudget 4-5 M\n'
                             'Lokasi Surabaya atau waru\n\n2.\n*Hot Renter Request*\nRuko tengah Kota Surabaya\nBudget 150 jt/th\n\nHub Daniel 081244387775')
        self.assertEqual([(p.transaction_type, p.categories) for p in stray], [('sale', ['warehouse']), ('rent', ['shophouse'])])
        self.assertFalse(stray[0].segment_text.startswith('Renter request'))
        priced = parse_bubble('*3 orang Renter Request budget 100jt akses container. Area Margomulyo dsk*\n- Buat simpan bahan rokok\n\n'
                              '*Buyer Request*\n- Gudang Beli Area Manukan / Tanjungsari\n- Luas 1.000-1.200\n- Budget 10M\n\n'
                              '*Buyer Request*\n- Tanah / Gudang untuk bearing\n- Luas 3.200 minimal\n- area Tandes / Demak\n- Budget 50M\n\nLili 0811349979')
        self.assertEqual([p.transaction_type for p in priced], ['rent', 'sale', 'sale'])
        self.assertTrue(all('margomulyo' not in p.locations for p in priced[1:]))
        # A header's words still give the purpose when an item states none.
        rent = parse_bubble('Disewakan gudang Margomulyo:\n1. Gudang A LT 500 Harga 300 jt\n2. Gudang B LT 600 Harga 400 jt')
        self.assertEqual([(p.transaction_type, p.categories) for p in rent], [('rent', ['warehouse'])] * 2)
        self.assertTrue(all('margomulyo' in p.locations for p in rent))

    def test_a_header_with_its_own_offers_is_split_by_blocks(self):
        parts = parse_bubble(GRAHA)
        self.assertEqual([(p.categories, p.transaction_type) for p in parts],
                         [(['house'], 'sale'), (['house'], 'sale')] + [(['land'], 'sale')] * 4)
        self.assertEqual([p.land_area_min for p in parts], [240, 240, 480, 650, 880, 482])
        self.assertEqual([p.segment_index for p in parts], list(range(6)))
        self.assertTrue(all('BGI Citraland' in p.segment_text for p in parts[2:5]))
        self.assertTrue(all(p.segment_text.startswith('*HOT LISTING GRAHA PAKUWON CITRALAND*') for p in parts))

    def test_block_boundaries_and_item_numbers(self):
        dashed = parse_bubble('Disewakan Tempat usaha Jln Kartini\nLT 620m2\n*Harga 325jt per thn*\n------------\n'
                              'Disewakan Rumah Pajajaran\nLt 400m2\n*Harga 225jt/ th Nego*\n\nRagil 085755294259')
        self.assertEqual([p.price_max for p in dashed], [325 * JT, 225 * JT])
        disewa = parse_bubble('Dijual Rumah Siap Huni Palm Beach\nLT 160\nLB 240\n*Harga 3.7M*\n\n'
                              'Disewa Rumah Palm Beach Boulevard\nLT 372\nLB 500\n*Harga 200 jt/th*\n\nJenny 081356565090')
        self.assertEqual([(p.transaction_type, p.price_max) for p in disewa], [('sale', 3_700_000_000), ('rent', 200 * JT)])
        for text in ('*RUMAH KOST AKTIF*\n\n1 *DUKUH KUPANG 17*\nLuas Tanah : 258 m²\n*Harga : 3,7M nego*\n\n'
                     '2. *DUKUH KUPANG 19*\nLuas Tanah : 240 m²\n*Harga Jual : 2,7 M*',
                     '*1.DIJUAL RUKO DARMO*\nLT 75m2\n*Harga 1.5M*\n\n*2*DIJUAL / DISEWA RUKO PURI SURYA*\nLT 80m2\n*HARGA 1.9M NEGO*',
                     '*Edisi Sewa Apartemen Surabaya Barat*\n1. \n*PBG*\nLuas : 156 m\n*Harga sewa: 125jt/thn Nego*\n.\n2.\n'
                     '*Waterplace*\nLuas :60M2\nHARGA : 35 JT / Thn Nego'):
            self.assertEqual(len(parse_bubble(text)), 2, text)
        renters = parse_bubble('*RENTER REQUEST*\n\n*CARI rumah di Surabaya timur :*\n1. 1-2 kamar\n2. Budget di bawah 20 juta/tahun\n\n'
                               '*CARI rumah di Surabaya :*\n1. Peruntukan kantor kecil\n2. Budget di bawah 30 juta/tahun')
        self.assertEqual([(p.transaction_type, p.price_max) for p in renters], [('rent', 20 * JT), ('rent', 30 * JT)])
        # A heading with a price above the property's own specs is no property of its own; the same unit posted
        # twice is one context.
        for text in ('*Dijual 2 Rumah Baru Gress Surabaya barat 500 juta an*\n\n*Area tandes :*\nLuas tanah 65\nKT 2\nHarga 550jt',
                     'JualSewa Apartment East Coast\nLT 35 LB 35\nRp 350.000.000\nRp 32.500.000\n\n'
                     'Jual / Sewa Apartement East Coast Pakuwon City\nLT 35 m2, LB 35 m2\n32.500.000/th\nJual 350jt'):
            self.assertEqual(len(parse_bubble(text)), 1, text)

    def test_split_parts_are_never_dropped_and_keep_one_contact(self):
        parts = parse_bubble('JUAL/SEWA KOS Manyar\nLT 300\nJUAL 9M, SEWA 300jt/thn\n\nDijual rumah Klampis\nLT 100\nKT 3\nHarga 2 M\n\n'
                             'Hub Andi 081311112222')
        self.assertEqual([(p.segment_index, p.segment_count, p.classification) for p in parts],
                         [(0, 2, 'property_listing'), (1, 2, 'property_listing')])
        self.assertEqual((parts[0].transaction_type, parts[0].price_max, parts[0].rent_price_max), ('both', 9 * M, 300 * JT))
        signed = parse_bubble('1.Di sewakan ruko Icon 21\n- Luas : 72 m²\n- rp 100jt /th nego\n- minim 2th\n- hub .erna konig\n- 085100777612\n'
                              '2.Dijual KAVLING puri galaxy\n*Luas 22X27,5\nHarga rp 17.5 jt/m nego\nHub erna konig 085100777612')
        self.assertEqual([(p.contact_name, p.contact_phone) for p in signed], [('Erna Konig', '6285100777612')] * 2)

    def test_region_root_dkp_alias_and_inverted_ranges(self):
        buyer = rows(WIWIK)[0]
        rungkut = match(dict(buyer), row('Dijual gudang di Rungkut Industri Surabaya\nLT 400 m2\nHarga 4 M'))
        self.assertEqual(rungkut['status'], 'custom')  # v4.3: an unconfirmed area is Custom, never Warm
        self.assertIn('Needs check: exact location not confirmed', rungkut['explanation'])
        self.assertIsNone(match(dict(buyer), row('Dijual gudang di Dukuh Kupang Surabaya\nLT 400 m2\nHarga 4 M')))
        self.assertIsNone(match(row('Buyer request rumah Citraland Surabaya'), row('Dijual rumah Rungkut Surabaya')))
        self.assertEqual(parse_message('Dijual rumah Manyar LT 136 LB 220 - 2 lantai Harga 2 M').building_area_max, 220)
        self.assertEqual(band((80, 100), (220, 2), 20, 20)[0], 0.0)
        self.assertIsNone(match(row('Cari rumah Manyar\nLB 80-100\nBudget 2 M'), row('Dijual rumah Manyar LT 136 LB 220 - 2 lantai Harga 2 M')))

    def test_transaction_vocabulary(self):
        for text, expected in [('INVESTASI KOST Surabaya\nRumah kost LT 300\nPotensi hasil Rp 20 Juta/bulan\nHarga Rp 2,5 Miliar', ('sale', 2_500_000_000, None)),
                               ('Ruko Manyar LT 100\nHOT INVESTASI\nHarga : 8M (nego) / 250jt/tahun, minimal sewa 2 tahun', ('both', 8 * M, 250 * JT)),
                               ('Ruko Raya Darmo LT 200\nHarga :Rp 10 milyar / Rp 200 juta / th', ('both', 10 * M, 200 * JT)),
                               ('Disewa gudang Margomulyo LT 500\nHarga 250 jt', ('rent', 250 * JT, None)),
                               ('Dijual rumah Manyar LT 100 Harga 2 M\nSaat ini disewa sampai 2027', ('sale', 2 * M, None)),
                               ('Cari rumah take over Manyar\nBudget 150 jt', ('sale', 150 * JT, None)),
                               ('Cari rumah lelang Manyar\nBudget 150 jt', ('sale', 150 * JT, None)),
                               ('APARTMENT Gunawangsa 2BR\nHarga: 195 juta\nbelum pernah ditempati', ('sale', 195 * JT, None))]:
            p = parse_message(text)
            self.assertEqual((p.transaction_type, p.price_max, p.rent_price_max), expected, text)
        for text, budget in [('Cari rumah\n- Luas minim 200m\n- Budget max 3M', 3 * M), ('Dijual apartemen\nLuas Nett 65m\nJual Rp 3.1m nego', 3_100_000_000)]:
            self.assertEqual(parse_message(text).price_max, budget, text)

    def test_purpose_unit_lines_budgets_and_negated_exclusions(self):
        both = row('Cari rumah beli/sewa Manyar\nLT 100\nBudget 2 M')
        rent = match(both, row('Disewakan rumah Manyar LT 100 Harga 900 jt/th'))
        self.assertEqual(rent['status'], 'custom')  # v4.8: no rent budget is unclear
        self.assertEqual(rent['explanation'][:2], ['Purpose: rent ✓', 'Unit: house ✓'])
        self.assertIn('Needs check: rent budget not stated', rent['explanation'])
        self.assertEqual(match(both, row('Dijual rumah Manyar LT 100 Harga 1,9 M'))['explanation'][:2], ['Purpose: buy ✓', 'Unit: house ✓'])
        self.assertEqual(row('Cari rumah di Manyar, bukan gang buntu\nBudget 2 M')['exclusions'], [])
        flood = row('Cari rumah Manyar\nNo banjir\nBudget 2 M')
        self.assertIsNotNone(match(dict(flood), row('Dijual rumah Manyar LT 100 Harga 1,9 M\nBebas banjir')))
        self.assertIsNone(match(dict(flood), row('Dijual rumah Manyar LT 100 Harga 1,9 M\nRawan banjir')))

    def test_a_part_with_two_properties_keeps_the_several_offers_hold(self):
        request = row('Buyer request rumah Manyar LT 100 Budget 2 M')
        merged = match(request, row('Dijual rumah Manyar\nLT 100\nHarga 1,9 M\nRumah Manyar Indah\nLT 120\nHarga 2,1 M'))
        # v4.3 R7: the specs of two properties are mixed, so nothing is proven for one of them: held back.
        self.assertIn('Needs check: message contains several offers/prices; held back until each property is separated',
                      merged['explanation'])
        self.assertEqual(merged['status'], 'custom')  # v4.8: unclear; held back below 60, so never stored
        self.assertLessEqual(merged['score'], 59)



def parts_of(text):
    return parse_bubble(text)


class SplitAccuracyRound1(unittest.TestCase):
    """Fixes for the split-accuracy review (patterns A–I); short synthetic texts modelled on real bubbles."""

    def test_e_teasers_discounts_reference_prices_and_income_never_split_one_property(self):
        for text, price in [
                ('*DIJUAL MURAH turun harga 2M gudang Margomulyo*\n\nGUDANG LUAS 3.000 m²\n\nLT 3.000 m2\nHadap Selatan\nSHM\n\n'
                 '*HARGA ~14M~ turun 12 M nego*', 12 * M),
                ('*TURUN HARGA 3M*\nDIJUAL GUDANG RUNGKUT\n\n*GUDANG*\nLT : 5.250 m²\nLB : 4.451 m²\n\nListrik 345 KVa\n\n'
                 '*HARGA ~20M~ 17M NEGO*', 17 * M),
                ('*DIJUAL*\n*RUMAH GRAND PAKUWON*\n*HARGA 1,05M*\n\n*Paling murah (Harga developer sebelum soldout 1,6M*\n\n'
                 'LT 72 LB 70\nKT 2 KM 2\nHadap utara', 1_050_000_000),
                ('*HARGA SEWA TURUN*\nSemula\n~Rp 60 Juta/tahun~\nMenjadi\nRp 55 Juta/tahun\n\n*SEWA RUMAH MANYAR*\nLT 148 LB 130\n'
                 'KT 3\nHarga sewa Rp 55 juta/tahun', 55 * JT),
                ('Rumah kost Manyar\nLT 238 LB 550\nKamar 23\nHarga jual 5,9M nego\n\nHITUNGAN BISNIS KOST:\n'
                 '- 20 kamar @1,6 juta (Total Rp 425 juta / tahun)\n- Expense 5 juta / bulan\n- Net income Rp 365 juta / tahun', 5_900_000_000),
                ('Dijual pabrik Gresik\nLuas tanah 63.745 m2\n\nHanggar 1\n3960m2\n\nHanggar 3\n6000m\n20x300\n\nKantor 2lt\n1012 m2\n\n'
                 'Harga 290M', 290 * M)]:
            parts = parts_of(text)
            self.assertEqual((len(parts), parts[0].price_max), (1, price), text)

    def test_amounts_that_are_no_price(self):
        for text in ('Hanggar 3 6000m', 'Unit: 6B', 'Harga jual Rp 1.250.000 m'):
            self.assertEqual(parse_message('Dijual gudang\n' + text).price_max, None, text)
        self.assertEqual(parse_message('Dijual rumah\nLT 100\nHarga : 2.600.000.000 M').price_max, 2_600_000_000)
        self.assertEqual(parse_message('Dijual rumah\nLT 100\nHarga ~Rp 7,5 M nego~ 6,9 M nego').price_max, 6_900_000_000)
        self.assertEqual(parse_message('*TURUN 50JT!!!*\nApartemen LT 65\n*Harga : ~450jt~ jadi 400 Nego*').price_max, 400 * JT)

    def test_a_own_prices_without_the_usual_wording_still_separate_parts(self):
        kenjeran, sidoyoso = parts_of('Dijual rumah Kenjeran\nLT 45 LB 30\nKT 2\nHarga 525 nett\n\nDijual tanah Sidoyoso\nLT 12x30\nHarga 1.750M nego')
        self.assertEqual((kenjeran.price_max, sidoyoso.price_max), (525 * JT, 1_750_000_000))
        rent = parts_of('*DISEWAKAN rumah Sukomanunggal*\nLuas Tanah 9 x 18\nKT 3\n*Harga Sewa : 55jt/th*\n\n'
                        'DISEWAKAN\n*RUKO Dian Istana*\nLT 4,5 x 17\nHg per th 110 jt')
        self.assertEqual([(p.price_max, p.price_basis) for p in rent], [(55 * JT, 'per_year'), (110 * JT, 'per_year')])
        self.assertEqual(rent[1].land_area_min, 76.5)  # "4,5 x 17" is one dimension
        plots = parts_of('*Jual Tanah Pekanbaru*\n\n1. Tanah\nSHM 7,5 ha\nRp350 ribu /M2 nego\n\n'
                         '2. Tanah SHM MURAH\nTerdiri 25 SHM @20,000m2\nHarga 9.000/m2 NEGO')
        self.assertEqual([(p.price_max, p.price_basis) for p in plots], [(350_000, 'per_m2'), (9_000, 'per_m2')])
        araya = parts_of('*DIJUAL RUMAH ARAYA*\nLT 525 LB 1000\n💰 13,5Mnego\n\n*DIJUAL RUMAH ARAYA Siap Huni*\nLT 375 LB 600\n💰 7,3M nego')
        self.assertEqual([p.price_max for p in araya], [13_500_000_000, 7_300_000_000])
        condos = parts_of('*HOT LISTING APARTEMEN*\n\n*PENTHOUSE VUE*\n186 sq.m\n3+1 Bedroom\nSell : IDR 7.75M nego\n\n'
                          'For Rent\nLa Riz\n3 br + 1\nFully furnish\n200jt / th nego\n\n*Disewakan VUE 2909*\nLuas 131 m2\nSewa 150 jt / th')
        self.assertEqual([(p.transaction_type, p.price_max) for p in condos], [('sale', 7_750_000_000), ('rent', 200 * JT), ('rent', 150 * JT)])
        self.assertNotIn('PENTHOUSE', condos[1].segment_text)

    def test_b_title_starts(self):
        fs = parts_of('*HOT LISTING BARATAJAYA*\n\nFS/FR : Rumah usaha\n*>> Raya Baratajaya*\nLT 240/LB 500\nHarga jual 9M\n'
                      'Harga sewa 300jt/th\n\nFS : Toko\n*>> Raya Bratang Gede*\nLT 209/LB 162\nHarga 3.4M NEGO')
        self.assertEqual([p.price_max for p in fs], [9 * M, 3_400_000_000])
        rungkut = parts_of('*Edisi Ruko Rungkut*\n\n>>>Ruko Gunung Anyar\nKondisi Siap Pakai.\n\n#1\nLT 4.5x14 | 2 lantai\n975jt nego\n\n'
                           '#2\nLT 5x14 | 2 lantai\n1.1M nego\n\n>>>Ruko Kalirungkut\nLT 4.5x12=53 | LB 158\nSewa 50jt/th')
        self.assertEqual([(p.transaction_type, p.price_max) for p in rungkut], [('sale', 975 * JT), ('sale', 1_100_000_000), ('rent', 50 * JT)])
        self.assertTrue(all('Gunung Anyar' in p.segment_text for p in rungkut[:2]))
        self.assertNotIn('Gunung Anyar', rungkut[2].segment_text)
        for text in ('*HOT LISTING SIDOARJO*\n\n*AJB NORMAL guys*\nDIJUAL Rumah Usaha Tulangan\nLuas tanah: 271 m2\n*Harga : 2,85M nego*\n\n'
                     '*AJB NORMAL*\nJual Rmh Delta Sari Waru\nLT: 98 (7 x 14)\nHarga JUAL 1,5M nego',
                     '*Kavling Victoria*\n9x16\nPPJB\nPrice : 9.5 jt nego\n\nLangka! Jarang ada\n*Kavling Margomulyo Permai*\nLT: 7560\n'
                     'Harga: 90 M nego'):
            self.assertEqual(len(parts_of(text)), 2, text)

    def test_b_units_of_one_development_split_and_keep_its_heading(self):
        types = parts_of('*DARMO PERMAI UTARA*\n\n*Type Alpha*\nLT 40 m2\nLB 78 m2\nKT 2\nHarga 999.999.000\n\n'
                         '*Type Milenial II*\nLT 55 m2\nLB 105 m2\nKT 3\nHarga 1.450.000.000')
        self.assertEqual([(p.price_max, p.land_area_min) for p in types], [(999_999_000, 40), (1_450_000_000, 55)])
        self.assertTrue(all(p.segment_text.startswith('*DARMO PERMAI UTARA*') for p in types))
        tipe = parts_of('*Dijual 2 rumah baru Dian Istana*\n\n* Tipe DS 3.0\n LT : 7x15 = 105 m2\n KT : 3 +1\n **Harga : 2.600.000.000 M*\n\n'
                        '* Tipe HD 3.5\n LT : 7x18 = 126 m2\n KT : 3+1\n **Harga : 3.100.000.000 M *')
        self.assertEqual([p.price_max for p in tipe], [2_600_000_000, 3_100_000_000])
        soho = parts_of('DIJUAL SOHO DENVER Citraland\nada 3 unit bisa beli satuan\n\nLuas Nett : 50.2m2\nLuas Gross : 60.1m2\n\n'
                        'Jual 1,1M nego\n\nLuas Nett : 44.1m2\nLuas Gross : 52.8m2\nJual 900 JT nego\n\n'
                        'Luas Nett : 47.8m2\nLuas Gross : 57.2m2\nJual 1 M nego')
        self.assertEqual([p.price_max for p in soho], [1_100_000_000, 900 * JT, 1 * M])

    def test_c_a_title_of_the_first_property_is_not_shared(self):
        land = parts_of('*YANG CARI TANAH ADA PILIHAN DI SINI*\n\n**JL. RAYA PETEMON ~ UNTUK 8 RUKO**\n\n*Tanah Pusat Kota bisa dibangun ruko*\n\n'
                        '*Luas Tanah 766m2*\nSHM\n\n*Harga Rp.8,5M nego*\n\n**Hitung Tanah di Jl. Raya Kendangsari**\n\n*Luas Tanah 2000m²*\n\n'
                        '*Harga Rp.20Jt/m2 Nego*')
        self.assertEqual([p.classification for p in land], ['property_listing'] * 2)  # "yang cari tanah …" pitches, it asks nothing
        self.assertIn('PETEMON', land[0].segment_text)
        self.assertNotIn('PETEMON', land[1].segment_text)
        self.assertNotIn('petemon', land[1].locations)
        ruko = parts_of('*JUAL/SEWA RUKO SEMOLOWARU*\n\n(Hanya 1M an, depan Indomaret)\n\nLT56 (4x9) ┃ LB72\n\n💰 Jual 1,2M nego / Sewa 40jt/thn\n\n'
                        '▬▬▬▬▬▬\n\n*JUAL RUKO RUNGKUT MEGAH*\nLT75 (5x15) ┃ LB225\n\n💰 1,5M nego keras')
        self.assertEqual([p.transaction_type for p in ruko], ['both', 'sale'])
        self.assertNotIn('SEMOLOWARU', ruko[1].segment_text)
        house, land = parts_of('*Buyer Request*\n\n*_Rumah Lokasi : Sutorejo, Mulyosari_*\n\n- Cari 2 lantai\n- Lt. 100-120 m2\n\n*Budget : 1.5 M*\n\n'
                               'Request...\n\nLahan industri untuk gudang\nArea Pandaan\nLuas total max 1.2ha\nBuget 1jt an /m²')
        self.assertEqual((house.categories, house.price_max), (['house'], 1_500_000_000))
        self.assertNotIn('sutorejo', land.locations)
        self.assertNotIn('house', land.categories)

    def test_d_shared_project_headings_reach_every_unit(self):
        units = parts_of('*#NEW GRESS TAMAN GADING PANTAI#*\n\nSHM, 4UNIT\nROW 3mobil\n\n#Unit A\nLt 7x12\nLb 150m2\n*Harga 1.750M (nego)*\n\n'
                         '#Unit B\nLt 8x12\nLb 150m2\n*Harga 1.950M (nego)*\n\n#Unit C\nLt 5,5 x 15\nLb 150m2\n*Harga 1.7M (nego)*')
        self.assertEqual([p.price_max for p in units], [1_750_000_000, 1_950_000_000, 1_700_000_000])
        self.assertTrue(all('TAMAN GADING PANTAI' in p.segment_text for p in units))
        nested = parts_of('*For Sale Edisi Apartemen*\n\n*_Apartemen Rainbow Spring, Tangerang_*\n\n• *Unit B03*\nLuas semi gross 126 m²\n'
                          '*Harga 2,2 M*\n\n• *Unit B05*\nLuas semi gross 84 m²\n*Harga 1,7 M*\n\n*_Apartemen Fifty Seven Promenade, Jakarta Pusat_*\n\n'
                          '• *Unit 21-C*\nLuas semi gross 55 m²\n*Harga 3,2 M*\n\n• *Unit 21-S*\nLuas semi gross 81 m²\n*Harga 4,7 M*')
        self.assertEqual(len(nested), 4)
        self.assertTrue(all('Rainbow' in p.segment_text and 'Fifty' not in p.segment_text for p in nested[:2]))
        self.assertTrue(all('Fifty Seven' in p.segment_text and 'Rainbow' not in p.segment_text for p in nested[2:]))

    def test_g_boundaries_and_whole_lot_prices(self):
        beverly = parts_of('Disewakan\nApartemen Taman Beverly\n2Br Full Furnish\n\nLuas 94\nUnit: 6B\n*40 juta/tahun*\n\n'
                           'Ready 15 Oktober 2026\nDisewakan\nHanya *20jutaan* dapat luas 50an m²\n\nBeverly Apartemen\nUnit: 10i\nLuas: 51m2\n'
                           'Sewa: Rp. 22jt/thn')
        self.assertEqual([(p.transaction_type, p.price_max) for p in beverly], [('rent', 40 * JT), ('rent', 22 * JT)])
        self.assertNotIn('Ready 15', beverly[0].segment_text)
        self.assertIn('Ready 15', beverly[1].segment_text)
        # "*Dijual Rp 9.5 Milyar*" under two rent plots prices the whole lot: splitting is unreliable.
        lot = parts_of('Disewakan atau Dijual lahan Dukuh Kupang\n- bagus untuk gudang\n\n*[Kuning] Dukuh Kupang Utara*\nTanah : 688 m2\n'
                       'Disewakan Rp 150.000.000/tahun (nego)\n\n*[Biru] Dukuh Kupang Utara*\nTanah : 300 m2 (15x20)\n'
                       'Disewakan : Rp 80.000.000/tahun (nego)\n\n*Dijual Rp 9.5 Milyar (nego)*')
        self.assertEqual((len(lot), lot[0].segment_text), (1, None))

    def test_f_a_numbered_catalogue_over_the_cap_stays_whole_even_with_a_missing_period(self):
        items = '\n\n'.join((f'*{n} RUMAH CLUSTER {n}*' if n == 6 else f'*{n}. RUMAH CLUSTER {n}*') +
                            f'\nLT.{100 + n} || LB.120 || KT.3\n*_Harga {n + 1} M_*' for n in range(1, 15))
        parts = parts_of('*EDISI RUMAH SURABAYA BARAT*\n\n' + items)
        self.assertEqual((len(parts), parts[0].segment_text), (1, None))
        few = parts_of('*EDISI RUMAH*\n\n*1. RUMAH A*\nLT.100\n*_Harga 2 M_*\n\n*2 RUMAH B*\nLT.120\n*_Harga 3 M_*\n\n*3. RUMAH C*\nLT.130\n*_Harga 4 M_*')
        self.assertEqual([p.price_max for p in few], [2 * M, 3 * M, 4 * M])

    def test_h_forwarded_listings_signed_on_the_next_title_line(self):
        parts = parts_of('*TURUN 50JT!!!*\n🏢 *FOR SALE*\n*Apartemen Icon Mall Gresik*\nLuas 65m²\n*Harga : ~450jt~ jadi 400 Nego*\n\n'
                         'More info:\n*BERNAD*\n📱 : 0812-3550-9156 *DISEWAKAN/ DIJUAL*\n*APARTEMEN PBG*\nLuas : 104m2\n'
                         '*Harga sewa : 65jt/thn nego*\n*Harga Jual ~950jt~ >> 800jt nego*\n\nMore info:\n*BERNAD*\n'
                         '📱 : 0812-3550-9156 🏷 *For Rent*\n🏢 *Kondominium Graha*\nLuas 158m2\n*Harga 150jt/thn Nego*\n\n'
                         'More info:\n*BERNAD*\n📱 : 0812-3550-9156')
        self.assertEqual([(p.transaction_type, p.price_max, p.rent_price_max) for p in parts],
                         [('sale', 400 * JT, None), ('both', 800 * JT, 65 * JT), ('rent', 150 * JT, None)])
        self.assertEqual({p.contact_phone for p in parts}, {'6281235509156'})

    def test_i_keycap_sections_split_and_never_mix_buyers_with_renters(self):
        parts = parts_of('1️⃣GUDANG!! request buyer\n\n-Surabaya ke arah waru, (tidak sampe Margomulyo/ dkp)\n-Luas 300-500m2\nBudget max 5M\n\n'
                         '2️⃣Request buyer\n\nGudang Sirie Sidoarjo\nYg jual rugi saja\n\n3️⃣Renter request\n\nRumah usaha\nLuasan 400-500m2\n'
                         'Area Manyar Kartika\n\n4️⃣Buyer cari gudang\n\n1) daerah Wonorejo, Medokan\n2) lebar + panjang : 10x25\n'
                         '3) budget Rp 1,5M max\n\n5️⃣Request buyer\n\n• Area karang asem\n• Rumah siap huni\n• Budget dibawah 900juta\n\n'
                         'Chrestina 081255773888')
        self.assertEqual([(p.transaction_type, p.categories) for p in parts],
                         [('sale', ['warehouse']), ('sale', ['warehouse']), ('rent', ['shophouse']), ('sale', ['warehouse']), ('sale', ['house'])])
        self.assertEqual((parts[0].price_max, parts[0].exclusions[:2]), (5 * M, ['margomulyo', 'dkp']))
        self.assertEqual([p.price_max for p in parts[3:]], [1_500_000_000, 900 * JT])

    def test_request_headings_cari_sewa_cari_beli_client_and_lettered_renters(self):
        house, warehouse = parts_of('🌟 _*CARI SEWA RUMAH*_\n*Surabaya area bebas sampai Waru\n*Luas bangunan 500 ke atas\n'
                                    '💰 _*BUDGET 200JT/TH*_\n\n☀️ _*CARI BELI GUDANG*_\n*Pasuruan, Mojokerto dan sekitarnya\n'
                                    '*Luas sekitar 1-10ha\n💰 _*BUDGET SESUAI PASARAN*_\n\nKirim spec foto:\n*SAMUEL*\n085158332710')
        self.assertEqual([(p.transaction_type, p.categories) for p in (house, warehouse)], [('rent', ['house']), ('sale', ['warehouse'])])
        self.assertEqual((warehouse.land_area_min, warehouse.land_area_max), (10_000, 100_000))
        clients = parts_of('Cobroke teman2\n\n*Client J*\nCari sewa gudang untuk produksi\nLuas bangunan 2000M² an\nLokasi surabaya, Gresik\n'
                           'Budget mengikuti\n\n*Client E*\n\nCari sewa tanah\n\n*Budget 200 juta sampai 500 juta setahun*\n\n'
                           '*Lokasi Gresik Kota saja*\n\n*Client S*\nCari sewa tanah atau gudang luas 300M² sampai 500m²\n\n'
                           '*Lokasi hanya Mulyosari Surabaya Timur*\n\n*Budget mengikuti*\n\nDON 082331119235')
        self.assertEqual([p.price_max for p in clients], [None, 500 * JT, None])
        self.assertTrue(all(p.transaction_type == 'rent' for p in clients))
        self.assertFalse(any(p.segment_text.startswith('Cobroke') for p in clients))
        renters = parts_of('*Renter A*\nCari ruko sekitaran Manyar\n2 lantai\n*Budget dibawah 90 JT / tahun*\n\n*Renter B*\n'
                           'Cari sewa gudang daerah Gempol\nLuas min 300 M2\n*Budget max 100jt / tahun.*\n\n*Chandra* 085646456797')
        self.assertEqual([(p.categories, p.price_max) for p in renters], [(['shophouse'], 90 * JT), (['warehouse'], 100 * JT)])
        lettered = parts_of('5.\n*HOT Buyer Request*\n*Apartemen* Waterplace 2bedroom\nBudget under 400-500jt\n\n6.\n'
                            '*RENTER A REQUEST APARTMENT BELLA YANG CONNECT PAKUWON CITY MALL*\nSEWA 6 BULAN\nBUDGET YANG MURAH\n\n'
                            '*RENTER B REQUEST APARTMENT SURABAYA BARAT* *2BR*\nSEWA 3 BULAN\nBUDGET IKUT\n\n*ELIA RAY WHITE*\n082333014783')
        self.assertEqual([p.transaction_type for p in lettered], ['sale', 'rent', 'rent'])

    def test_a_greeting_header_is_shared_by_no_part_and_cuts_no_body(self):
        parts = parts_of('Selamat malam\nMohon dibantu hubungi yg bersangkutan langsung yah...\n\n1.\n*Hot Buyer Request*\ncari gudang pergudangan\n'
                         'Luas minimal 400-500 m²\nBudget 4-5 M\n\nHubungi:\n*Lung*\n081234951669\n\n2.\n*RENTER REQUEST* Cari rumah second:\n'
                         '1. Daerah surabaya timur : kertajaya\n2. Hadap timur atau selatan\n3. Luas minim 300\n4. Budget max 10m\n'
                         '5. Boleh sewa dengan spek yang sama budget max 200jt / tahun\n\n*ELIA RAY WHITE*\n082333014783')
        self.assertFalse(any(p.segment_text.startswith('Selamat') for p in parts))
        renter = parts[1]
        self.assertIn('kertajaya', renter.locations)
        self.assertEqual((renter.land_area_min, renter.facing, renter.transaction_type), (300, ['timur', 'selatan'], 'both'))
        self.assertEqual(renter.contact_phone, '6282333014783')

    def test_emoji_titled_items_with_h_prices(self):
        parts = parts_of('*Rumah dan Kondominium Sby Barat*\n\n🏠 *Sewa Rmh Permata Safira, siap huni*\nLT. 220 m2 - LB. 150 m2\nKT 3+1\n'
                         '*Sewa 60 jt /th nego*\n\n🏘️ *Rmh DARMO PERMAI SELATAN*\nLT. 210, LB. 315\nKT 4+1\n*H: 4 M nego*\n\n'
                         '🏠 *Jual Rmh Darmo Permai Timur*\nLT. 252 m2, LB. 175\n3 KT\n*H: 3 M*\n\n*FeLie* 08113252888')
        self.assertEqual([(p.transaction_type, p.price_max) for p in parts], [('rent', 60 * JT), ('sale', 4 * M), ('sale', 3 * M)])

    def test_closing_lines_and_windows_line_breaks(self):
        parts = parts_of('Dijual rumah Manyar\r\nLT 100 LB 120\r\nHarga 1 M\r\n\r\nDijual rumah Klampis\r\nLT 200 LB 220\r\nHarga 2 M\r\n\r\n'
                         'Best Regards,\r\n\r\n\r\nAlfon\r\nBrighton Priority')
        self.assertEqual([p.price_max for p in parts], [1 * M, 2 * M])
        self.assertNotIn('Regards', parts[1].segment_text)


class SplitAccuracyRound2(unittest.TestCase):
    """Fixes for the second split-accuracy review (patterns A, D, F, G, I, K, L, M); short synthetic texts."""

    def test_f_numbered_sub_lists_restart_under_their_own_region_headings(self):
        parts = parts_of('🏢 *EDISI GEDUNG KANTOR JAKARTA*\nBARU GRESS, SEWA dan JUAL\n\n*`JAKARTA PUSAT`*\n\n'
                         '1. 🏢 *GEDUNG KANTOR CIKINI*\n-Luas Tanah: 800 m2\n-Harga: *Rp. 125 Milyar*\n\n'
                         '2 🏢 *OFFICE BUILDING MENTENG*\nLuas tanah 1075 M2\n3. 1/2Lantai (include Basement)\n*Harga 120 M Nego*\n\n'
                         '*`JAKARTA SELATAN`*\n\n1 GEDUNG KANTOR:\nJl. Buncit Raya\n-Luas Tanah: 1400 m2\nHArga Jual *Rp 225 M nego*\n\n'
                         '2. 🏢 *OFFICE BUILDING CIPUTAT*\nLuas tanah 932 M2\n5,5 Lantai\n*Harga 85 M Nego*\n\nHub Rafles 081703063118')
        self.assertEqual([p.price_max for p in parts], [125 * M, 120 * M, 225 * M, 85 * M])
        self.assertTrue(all(p.segment_text.startswith('🏢 *EDISI GEDUNG KANTOR JAKARTA*') for p in parts))
        self.assertEqual([('jakarta pusat' in p.locations, 'jakarta selatan' in p.locations) for p in parts],
                         [(True, False), (True, False), (False, True), (False, True)])

    def test_f_ruled_sections_share_their_heading_and_slipped_numbers_still_split(self):
        parts = parts_of('🤗 DISEWAKAN lokasi Sidoarjo\n\n1. Rumah Citra Garden\nLT 162m2\nHarga sewa 45jt/th\n\n'
                         '2. Ruko Pondok Mutiara\nLB 75m2\nHarga sewa 80jt/th\n~~~~~~~~~~\n🤗 JUAL\n\n'
                         '1. Tanah Lingkar Timur\nLuas 3,7 Ha\nHarga Rp500ribu/m2 nego\n\n1. Tanah Arteri Porong\nLuas 3,600 m2\n'
                         'Harga 4M NETT\n~~~~~~~~~~\n*🤗 Jual Tanah di LUAR PULAU*\n\nLokasi *PEKANBARU*\n\n1. Tanah Bangkinang\n'
                         'SHM 7,5 ha\nRp350 ribu /M2 nego\n\n2. Tanah SHM MURAH\nTerdiri 25 SHM @20,000m2\nHarga 9.000/m2 NEGO')
        self.assertEqual([(p.transaction_type, p.price_max) for p in parts],
                         [('rent', 45 * JT), ('rent', 80 * JT), ('sale', 500_000), ('sale', 4 * M), ('sale', 350_000), ('sale', 9_000)])
        self.assertTrue(all('JUAL' in p.segment_text for p in parts[2:4]))
        self.assertTrue(all('DISEWAKAN' not in p.segment_text for p in parts[2:]))
        self.assertEqual(['pekanbaru' in p.locations for p in parts], [False] * 4 + [True] * 2)

    def test_d_a_heading_above_items_with_one_lead_marker_heads_every_item(self):
        parts = parts_of('UPDATE!!!\nDIJUAL TANAH\nSIDOARJO\n\n➡️SARIROGO\nLT:15.653 m2\nHarga: 4 juta/m2\n\n'
                         '➡️WONOAYU\nLT:2.453m2\nHarga: 5 juta/m2\n\n➡️GUDANG BY PASS KRIAN\nLT:49.819 m2\nLB: 1,7 hektar\n'
                         'Harga; 3,5 juta/m2\n\n➡️RAYA GILANG\nLT:2.002 m2\nHarga : 7 juta/m2')
        self.assertEqual([p.categories for p in parts], [['land'], ['land'], ['warehouse'], ['land']])
        self.assertTrue(all('sidoarjo' in p.locations for p in parts))

    def test_d_one_unit_siblings_keep_the_title_and_the_shared_specs(self):
        parts = parts_of('*For Sale / Dijual SOHO Denver di Citraland Surabaya*\n\nREADY 4 UNIT:\n\n1 UNIT\nLuas nett 44.1m2\n'
                         '*Harga 800 jt nego*\n\n1 UNIT\nLuas nett 47.8m2\n*Harga 900 jt nego*\n\nSemua Unit :\ndi Lantai 9\nPPJB')
        self.assertEqual([p.price_max for p in parts], [800 * JT, 900 * JT])
        self.assertTrue(all('citraland' in p.locations and 'Lantai 9' in p.segment_text for p in parts))

    def test_i_a_request_under_its_own_heading_is_a_part_even_with_the_same_purpose(self):
        parts = parts_of('*Renter Request*\nRumah area satelit indah\n1 Lantai min 4 KT\nBugdet 60jt/th\nSewa 2th\n\n'
                         '*Renter Request*\nKlaska 2BR Full Furniture\nSewa 1th bugdet mengikuti\n\n'
                         '*Renter Request*\nAprt One Icon\n1BR sewa 1th\nBugdet under 100jt\n\nDavid 081233779905')
        self.assertEqual(len(parts), 3)
        self.assertNotIn('Klaska', parts[0].segment_text)
        self.assertEqual([p.transaction_type for p in parts], ['rent'] * 3)

    def test_a_rent_written_150t_per_year_closes_its_property(self):
        rent, sale = parts_of('*HOT LISTING AREA SURABAYA SELATAN*\n\n*DISEWAKAN*\nRumah Layak Pakai Hoek\n*Taman Kendangsari*\n'
                              'Lt. 800m2 (40x20)\n*Harga Sewa 150t/tahun*\n\nMurah BCL\n*Rumah Siap Huni BONUS kost2an*\n'
                              '*Kutisari Selatan*\nLT: 10 x 20\n*Harga : 1,8M nego tipis*')
        self.assertEqual((rent.transaction_type, rent.price_max, rent.price_basis), ('rent', 150 * JT, 'per_year'))
        self.assertEqual((sale.transaction_type, sale.price_max), ('sale', 1_800_000_000))

    def test_g_taglines_stay_with_their_property_and_peer_titles_start_the_next(self):
        parts = parts_of('*Luxurios Jakarta Selatan!*\n\n🏡 *GAHARU CILANDAK JAKSEL*\n\nHouse for Sale\nLt 786m2, Lb 500m2\n4 Kamar Tidur\n\n'
                         '*Open Price 19 M nego*\n\n*#Dekat Stasiun MRT Cipete Raya!*\n\n🏡 *JAYAMANDALA TEBET JAKSEL*\n\n'
                         'Rumah Jayamandala for SALE\n✅LT 603 m2 / LB 603m2\n*For sale 19.5M nego*\n\n*#5 Menit Pintu Tol Gatot Subroto!*\n\n'
                         '🏡 *PEJATEN BARAT*\nJAKSEL\n\nFor Sale\nLT 634m2 lB 600m2 shm\n*Open 16.5M nego*')
        self.assertEqual([p.price_max for p in parts], [19 * M, 19_500_000_000, 16_500_000_000])
        self.assertIn('Cipete', parts[0].segment_text)
        self.assertIn('5 Menit', parts[1].segment_text)
        self.assertEqual(['PEJATEN' in p.segment_text for p in parts], [False, False, True])
        items = parts_of('*EDISI TANAH JAKSEL!*\n\n1⛳ *Tanah Kebayoran Baru*\nLT 934 m2\n*Harga 120 jt/m2 nego*\n\n'
                         '*#Lokasi Dekat Blok M*\n\n2⛳ *RAYA CIRENDEU JAKSEL*\n\n⭐️Di Jual Tanah Kosong⭐️\nLuas Tanah 1200 mtr\n'
                         '*Harga Rp 16 Juta / meter*\n\n*10 menit ke Stasiun Kebayoran Lama*\n*Selangkah ke RS Eka!*\n\n'
                         '3. ⛳ *TANAH KEBAYORAN LAMA!*\nLuas 560 M2\n*Harga 25 jt/M2*')
        self.assertEqual([p.price_max for p in items], [120 * JT, 16 * JT, 25 * JT])
        self.assertIn('Blok M', items[0].segment_text)
        self.assertTrue('CIRENDEU' in items[1].segment_text and 'Selangkah' in items[1].segment_text)

    def test_l_a_listing_without_places_is_no_repeat_of_another_but_a_teaser_is(self):
        parts = parts_of('Disewakan Apartemen \nAmor \nLuas 25m2\nHadap kolam renang\nHarga 35jt/thn (nego tipis)\n\n\n'
                         'Disewakan Apartemen Orchard \nstudio \nLt 25 unit 02\n35jt / th Nego\n\n\n'
                         'Disewakan Apartemen Puncak Kertajaya\nLuas 36m \nHarga 29jt per thn')
        self.assertEqual(len(parts), 3)
        self.assertIn('Orchard', parts[1].segment_text)
        teaser = parts_of('For Sale\n*TURUN HARGA!!!!!*\n~*HARGA 2,25 M*~ *TURUN JADI 2M*\nRumah 2 Lantai Baru Gress\n\n'
                          '*Bukit Palma Citraland*\nLuas tanah 120m2 (8x15)\nKamar Tidur 4 + 1\n*Harga 2M nego*')
        self.assertEqual((len(teaser), teaser[0].price_max, teaser[0].segment_text), (1, 2 * M, None))

    def test_k_struck_out_items_are_sold_and_belong_to_no_part(self):
        parts = parts_of('*JUAL / SEWA APARTEMEN SEKITAR PAKUWON MALL*\n\n*1. ~Benson view pool :* 2BR - lantai 32 - 54m2 - nego~\n\n'
                         '*2. Rosebay tower B :* 2BR - lantai 9 - 79m2 - sewa : 130jt/thn Nego - jual : 2,4M nego\n\n'
                         '*3. ~Rosebay Tower G:* 2BR - lantai 3 - 79m2 - Nego~\n\n*4. La Viz :* 2BR - 83m2 - lantai 21 - sewa : 190jt/thn nego')
        self.assertEqual([(p.transaction_type, p.price_max) for p in parts], [('both', 2_400_000_000), ('rent', 190 * JT)])
        self.assertFalse(any('Benson' in p.segment_text or 'Tower G' in p.segment_text for p in parts))

    def test_m_a_price_list_of_something_else_is_not_split(self):
        parts = parts_of('Monggo yang mau sewa Genset Senyap kedap suara, buat hajatan, orkes, karnaval\n'
                         '1. 10 kva harga sewa 500 ribu/8 jam\n2. 20 kva harga sewa 800 ribu/8 jam\n3. 40 kva harga sewa 1,2 juta/8 jam\n'
                         '4. Jika sewa lebih dari 2 hari ada diskon')
        self.assertEqual((len(parts), parts[0].segment_text), (1, None))

    def test_italic_titles_and_units_inside_a_numbered_item(self):
        parts = parts_of('*HOT LISTING CITRALAND*\n\n*For sale*\nKavling Greenlake Citraland\nLt 201.29 m ( 9x22.8 )\nHarga 10 jt/m nego\n\n'
                         '_For Sale_\n_Rumah American Modern di Villa Sentra Raya , Citraland_\nLt 128 ( 8x16 )\nLb 180 ( 2 lantai )\n'
                         'Harga 3.3 M nett')
        self.assertEqual([(p.price_max, p.price_basis) for p in parts], [(10 * JT, 'per_m2'), (3_300_000_000, 'total')])
        units = parts_of('1.*Disewakan Apartemen La riz*\nLt 3603\nKT 2\n120jt per thn\n\n2.5M\n\n\n2. *Sutorejo*\nRumah kotak\n'
                         'LT 77m2 || LB 110 m2\n* KT 3 KM 3\n*Harga 1,8M nego*\n\n\nRumah hook\nLT 88m2 || LB 110 m2\n* KT 3+1 KM 3+1\n'
                         '*Harga 1,95M nego*\n\n\n3. *Dijual rumah cantik di GRIYA ASRI, PAKUWON CITY*\n• LT 78 m2 (6x13)\n*Harga Rp 1.5M*')
        self.assertEqual([p.price_max for p in units], [2_500_000_000, 1_800_000_000, 1_950_000_000, 1_500_000_000])
        self.assertEqual(['sutorejo' in p.locations for p in units], [False, True, True, False])

    def test_sale_and_rent_wording_deposits_and_included_costs(self):
        for text, expected in [('JualSewa Rumah Babatan Pantai\nLT 144 LB 144\nRp 2.500.000.000\nRp 70.000.000\nMinim Sewa 2 Th , Deposit 10 Jt',
                                ('both', 2_500_000_000, 70 * JT)),
                               ('Gedung kantor Jakarta\nLuas Tanah: 800 m2\n-Harga: *Rp. 125 Milyar*\n-HArga Sewa: *Rp 5 M*', ('both', 125 * M, 5 * M)),
                               ('Disewakan gudang Lingkar Timur\nLT 512m2\nHarga sewa 310juta nego ( min 2 tahun )Deposit 30juta',
                                ('rent', 310 * JT, None)),
                               ('Dijual/Disewakan Apartemen Tamansari\nLuas : 36 m2\n*Harga Jual :*\nRp 570 juta nego\n\n*Harga sewa:*\n'
                                'Rp 5,5 juta/bulan *( include Service Charge, listrik, air )*\n\n*Deposit sewa:* Rp 3,5 juta\n\n'
                                '*Service Charge :*\nRp 2.640.000 per 3 bulan\n(Rp 880rb/bulan)', ('both', 570 * JT, 5_500_000))]:
            parsed = parse_message(text)
            self.assertEqual((parsed.transaction_type, parsed.price_max, parsed.rent_price_max), expected, text)
            self.assertNotEqual(parsed.price_min, 30 * JT, text)


class BehaviourRound2(unittest.TestCase):
    """Fixes for the second behaviour review: price ranges, development-wide clusters, landmarks, investors,
    rent-priced sale listings, request wording, refused regions and listing parse errors."""

    def test_listing_amounts_that_are_no_range(self):
        for text, expected in [('Hrg 10,380 M (opsi lift + 380 jt)', (10_380_000_000, 10_380_000_000, 'total')),
                               ('harga 1.350m netto (permeter 8.5jt)', (1_350_000_000, 1_350_000_000, 'total')),
                               ('harga 16m nego (26 jt / sqm)', (16 * M, 16 * M, 'total')),
                               ('*Harga 6M nego tipis (10,5jt/m2)*', (6 * M, 6 * M, 'total')),
                               ('harga rp 1,850 m nego --- turun jd rp 1795m nego', (1_795_000_000, 1_795_000_000, 'total')),
                               ('Harga jual : 480jt, 380jt negotiable', (380 * JT, 380 * JT, 'total')),
                               ('Harga 15jt/m² >> 13,9jt/m² >> 13,5jt/m² nego', (13_500_000, 13_500_000, 'per_m2')),
                               ('*Harga 5,5M 👉4,8M  nego*', (4_800_000_000, 4_800_000_000, 'total')),
                               ('Harga 5 M - 7 M', (5 * M, 7 * M, 'total'))]:
            parsed = parse_message('Dijual rumah Citraland\nLT 300\n' + text)
            self.assertEqual((parsed.price_min, parsed.price_max, parsed.price_basis), expected, text)
        options = parse_message('Disewakan apartemen Grand Sungkono Lagoon\nLuas 36 m2\nSewa 40jt/th atau 4,5jt/bln')
        self.assertEqual((options.price_max, options.price_basis), (40 * JT, 'per_year'))
        self.assertEqual(parse_message('Cari rumah Manyar\nBudget 1,5 - 2 M').price_min, 1_500_000_000)
        under = row('Cari rumah Citraland\nBujet under 1M')
        self.assertIsNone(match(dict(under), row('Dijual Rumah Super Lux Citraland\nLT 300\nHrg 10,380 M (opsi lift + 380 jt)')))
        wide = match(row('Cari rumah Citraland\nBudget 2 M'), row('Dijual rumah Citraland\nLT 150\nHarga 1 - 3 M'))
        self.assertIn('Needs check: listing price unclear (wide range)', wide['explanation'])
        self.assertEqual(wide['status'], 'custom')  # v4.8: an unclear price is Custom, never Warm

    def test_a_named_place_inside_a_development_is_not_the_whole_development(self):
        index = LocationIndex(parse_table(table([[1, 'Bukit Palma', '', 'CitraLand', 'Taman Puspa Raya (1.2)'],
                                                 [2, 'Taman Puspa Raya', '', 'CitraLand', 'Bukit Palma (1.2)']])))
        buyer = row('Buyer request\nRumah Greenlake Citraland Wiyung\nLuas 120\nBudget maksimal 2M')
        palma = match(dict(buyer), row('Dijual Rumah 2 lantai di Bukit Palma Citraland\nLT 120 LB 150\nHarga 2 M'), index=index)
        self.assertEqual(palma['status'], 'custom')  # v4.3: the cluster the buyer named is not confirmed
        self.assertIn('Needs check: exact cluster not confirmed (buyer named Greenlake)', palma['explanation'])
        greenlake = match(dict(buyer), row('Dijual rumah Greenlake Citraland\nLT 120\nHarga 1,9 M'), index=index)
        self.assertEqual(greenlake['status'], 'hot')
        anywhere = row('Buyer request\nRumah Citraland\nLuas 120\nBudget maksimal 2M')
        self.assertEqual(match(anywhere, row('Dijual Rumah 2 lantai di Bukit Palma Citraland\nLT 120 LB 150\nHarga 2 M'),
                               index=index)['status'], 'hot')

    def test_a_landmark_never_places_a_listing(self):
        index = LocationIndex(parse_table(table([[1, 'Florence', '', 'Pakuwon City', ''], [2, 'Sorrento', '', 'Pakuwon City', '']])))
        listing = row('Dijual rumah Mulyosari\nLT 120\nLokasi dekat ITS, UNAIR, dan Pakuwon City\nHarga 2 M')
        prepare_document(listing, index)
        self.assertEqual(listing['_geo_ids'], [])
        self.assertIsNone(match(row('Cari rumah Pakuwon City only\nBudget 2,5 M'), listing, index=index))

    def test_an_investor_buys_even_when_rental_income_is_mentioned(self):
        investor = row('Dicari ruko untuk investasi, bisa disewakan lagi potensi sewa 100 jt/th\nArea Darmo\nBudget 3 M')
        self.assertEqual((investor['transaction_type'], investor['price_max'], investor['rent_price_max']), ('sale', 3 * M, None))
        self.assertIsNone(match(investor, row('Disewakan ruko Darmo\nLT 100\nHarga 100 jt/th')))
        self.assertEqual(row('Investor cari sewa/beli gudang Margomulyo\nBudget 3 M atau 200 jt/th')['transaction_type'], 'both')

    def test_a_sale_listing_priced_per_year_never_matches_buyers_without_budget(self):
        listing = row('Dijual\nRumah di Citraland Utama\nLT 150\nHarga Rp. 250Jt/tahun')
        self.assertIsNone(match(row('Cari rumah Citraland\nLT 150'), dict(listing)))
        unconfirmed = match(row('Request rumah Citraland LT min 150'), dict(listing))
        self.assertIn('Needs check: price looks like a rent price', unconfirmed['explanation'])
        self.assertNotEqual(unconfirmed['status'], 'hot')

    def test_request_wording_for_offices_and_apartments(self):
        for text, txn in [('Cari kantor area Darmo\nBudget 3 M', 'sale'), ('Cari office space Darmo for rent\nBudget 200 jt/th', 'rent'),
                          ('Cari kantor for lease area Darmo', 'rent'), ('Cari apartment Waterplace 2BR\nBudget 1 M', 'sale')]:
            parsed = parse_message(text)
            self.assertEqual((parsed.classification, parsed.transaction_type), ('buyer_request', txn), text)
        self.assertNotEqual(parse_message('*BUTUH LAHAN BESAR UNTUK PABRIK?!*\nLuas total 2.3 Hektar\n*2M/tahun*').classification,
                            'buyer_request')

    def test_refused_places_rule_out_their_region_of_a_wanted_city(self):
        buyer = rows(WIWIK)[0]
        for place in ('Romokalisari Surabaya', 'Benowo', 'Tambak Langon'):
            self.assertIsNone(match(dict(buyer), row(f'Dijual gudang {place}\nLT 400 m2\nHarga 4 M')), place)
        self.assertEqual(match(dict(buyer), row('Dijual gudang di Waru\nLT 400 m2\nHarga 4,5 M'))['status'], 'hot')

    def test_listing_parse_errors(self):
        gudang = parse_message('Disewakan Gudang Driyorejo\nUkuran 23m x 100m\nSewa 35.000/m2/bulan')
        self.assertEqual((gudang.transaction_type, gudang.price_max, gudang.price_basis), ('rent', 35_000, 'per_m2'))
        kost = parse_message('RUMAH KOS AKTIF Manyar\nLT 200\n20 kamar\nharga sewa kost perbulan 1,350,000\nharga : 2,5M nego')
        self.assertEqual((kost.transaction_type, kost.price_max), ('sale', 2_500_000_000))
        glued = parse_message('Dijual rumah Manyar\nLT375 LB400\n2 Lantai + Rooftop di Lt3\nHarga 5 M')
        self.assertEqual((glued.land_area_min, glued.building_area_min), (375, 400))
        self.assertEqual(parse_message('Dijual rumah\n2 Lantai + Rooftop di Lt3.\nLT 146m2 (7x21)\nHarga 2 M').land_area_min, 146)
        self.assertTrue(all(any(c.isalpha() for c in e) for e in parse_message('Cari rumah Manyar\n( utara, barat ❌️)\nBudget 2 M').exclusions))


class BehaviourRound1(unittest.TestCase):
    """Fixes for the behaviour review: purpose line, strict targets, budgets, transaction and classification gaps."""

    def test_purpose_line_comes_first_even_when_unconfirmed(self):
        request = row('Request rumah Klampis LT min 150')
        self.assertEqual(request['transaction_type'], 'unknown')
        explanation = match(request, row('Dijual rumah Klampis LT 160\nHarga 2 M'))['explanation']
        self.assertEqual(explanation[:2], ['Purpose: buy (not confirmed)', 'Unit: house ✓'])

    def test_a_strict_target_unknown_to_the_place_list_is_scoped_to_the_other_targets(self):
        request = row('Buyer reQuest\nKavling Graha Only OR Pakuwon Indah\nLt max 450m2\nBuget 23.5jt /m2')
        # v4.8: next to Pakuwon Indah (Surabaya Barat) a bare "Graha" is Graha Famili.
        self.assertEqual(request['strict_locations'], ['graha famili', 'pakuwon indah'])
        for place in ('Rungkut Surabaya', 'Sutorejo Surabaya Timur', 'Sidoarjo'):
            self.assertIsNone(match(dict(request), row(f'Dijual kavling {place}\nLT 200\nHarga 4 M')), place)
        self.assertEqual(match(dict(request), row('Dijual kavling Pakuwon Indah\nLT 200\nHarga 4 M'))['status'], 'hot')

    def test_a_both_request_with_one_budget_never_passes_unchecked_on_the_other_purpose(self):
        request = row('Cari rumah beli/sewa Manyar\nLT 300\nBudget 200 jt/th')
        self.assertEqual((request['transaction_type'], request['rent_price_max']), ('both', 200 * JT))
        result = match(request, row('Dijual/Disewakan rumah Manyar LT 300\nHarga jual 11 M\nHarga sewa 450 jt/th'))
        self.assertLessEqual(result['score'], 59)
        self.assertIn('Needs check: purchase budget not stated', result['explanation'])

    def test_apartment_spellings_name_the_building(self):
        request = row('Dicari sewa Apartemen Anderson studio\nBudget 35 jt/th')
        self.assertIsNone(match(request, row('Disewakan Apartement Puncak Permai 2BR\nHarga 36 jt/thn')))
        self.assertIsNotNone(match(request, row('Disewakan Apartment for rent Anderson studio\nHarga 36 jt/thn')))

    def test_budgets_in_headings_typos_and_request_sizes(self):
        heading = parse_message('*Buyer request gudang 4-5 M* :\nLokasi Waru\nminimal 500 m2')
        self.assertEqual((heading.price_min, heading.price_max, heading.land_area_min), (4 * M, 5 * M, 500))
        self.assertEqual(parse_message('Dicari rumah 2lt Citraland. Lt min 180. Budger 3.5M. No barat.').price_max, 3_500_000_000)
        self.assertEqual(parse_message('RENTER REQUEST GUDANG KENJERAN\nbajet 100- 150 jt').price_max, 150 * JT)
        kontrakan = parse_message('Cari rumah kontrakan\nLB: sekitar 80–100 m²\nBudget 50 jt/th')
        self.assertEqual((kontrakan.building_area_min, kontrakan.building_area_max), (80, 100))

    def test_transaction_and_classification_gaps(self):
        invest = parse_message('Ruko untuk investasi di Rungkut\nLT 100 LB 200\nSudah tersewa 100 jt/th\nHarga 3 M')
        self.assertEqual((invest.transaction_type, invest.price_max, invest.rent_price_max), ('sale', 3 * M, None))
        for text, txn in [('Mau beli rumah Pakuwon City budget 4 M', 'sale'), ('Ingin beli rumah Manyar LT 150 budget 3M', 'sale'),
                          ('Looking for warehouse for lease in Rungkut\nBudget 300 jt/year', 'rent'), ('WTB rmh di Kuningan budget 25 M', 'sale')]:
            parsed = parse_message(text)
            self.assertEqual((parsed.classification, parsed.transaction_type), ('buyer_request', txn), text)
        self.assertEqual(parse_message('Monggo yang mau sewa genset 10 kva harga sewa 500 ribu').classification != 'buyer_request', True)
        self.assertIsNone(parse_message('Dijual Apartemen Puncak Kertajaya\nLt 36\nType studio\nHarga 300 jt').land_area_min)
        self.assertEqual(parse_message('Dijual rumah Manyar\nLt 36\nHarga 1 M').land_area_min, 36)


if __name__ == '__main__':
    unittest.main()
