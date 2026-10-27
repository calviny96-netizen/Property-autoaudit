"""v4.3: Hot/Warm/Custom bands (R7), property terms the matcher understands (R8), "_1) Buyer …_" sections (R9).
The F1–F4 texts are the user's reported failures (contact numbers replaced)."""
import unittest

from location_index import LocationIndex, parse_table
from matching_rules import assess_pair, prepare_document
from parser import (financing_need, financing_offer, floor_level, floor_requirement, legal_requirement, legal_statuses,
                    parse_bubble, parse_message, tower_names, unit_types)
from test_location_index import table

M = 1_000_000_000
JT = 1_000_000
# Schema defaults after the v4.3 migration: Hot price 80–100 % of the budget, Warm 15 % beyond Hot for every factor.
SETTINGS = dict(hot_price_floor_pct=80, price_down_pct=15, price_up_pct=15, land_down_pct=15, land_up_pct=15,
                building_down_pct=15, building_up_pct=15, location_radius_km=4, location_weight_pct=35, land_weight_pct=20,
                building_weight_pct=15, price_weight_pct=20, semantic_weight_pct=5, data_quality_weight_pct=5)

F1_BUYER = '''*Buyer Request*
_Apartemen Eastcost atau Educity_
Type 2Br Furniture
Lantai di bawah 10
View bebas
*Pembelian KPA*
Bugdet under 350jt
malam ini survei pukul 19.00-19.30

Kirim Foto, spek, nomor unit.

David Tan
Brighton Eagle
081200000001'''
F1_LISTING = 'Jual apartemen Educity Yale studio corner\nLantai 20\nView city\nLuas 24 m\nSemi furnish\nSHM strata\nDijual rugi 275 jt'
F2 = '''*🔥 HOT BUYER REQUEST CARI RAYA !*

Tolong langsung kirim lengkap ya : spec + foto biar bisa langsung forward, dan tolong kirim yang sesuai saja biar sama2 ga buang waktu 🙏🏻

_1) Buyer corporate_
Cari BELI Tanah/Rumah Lama (mau dibangun gedung) kalau ada Gedung gpp dicoba
*>> Raya Pemuda/Gubernur Suryo, Raya Panglima Sudirman, Raya Basuki Rahmat, Raya Darmo, Raya Diponegoro, Raya Tunjungan, Raya Gubeng*
• LT 2500-3500 (paling min 2300 ga mau dibawah itu)
• Lebar min 40m (paling min 30m dipertimbangkan)
• Budget mengikuti (harga wajar)
• Note : jangan tawarkan ex hotel tanjung pangsud

_2) Buyer surat ijo_
Cari BELI Tanah/Rumah
*>> Raya Kertajaya, Raya Pucang Anom, Raya Ngagel Jaya**
• LT 300-400
• Cari yang *Surat Ijo*
• Budget mengikuti (harga wajar surat ijo ya, ga mau harga SHM/HGB)

Ditunggu Guys, thank you

*Joe Aditia - VERSUS*
📞 082100000002 (fast respon)

*VERSUS Real Estate*
_Breakthrough to the next level_'''
F2_LISTING = '''Dijual Rumah
Kertajaya Indah
MURAH !!!
Butuh segera laku

LT 380 m² (12x31)
LB 270m²
1 lantai
KT 3+1
Km 2+1
Hdp selatan
SHM

Harga 4.95 M turun hrg 4.65 M nett'''
F3_BUYER = '''Request buyer

Apartemen waterplace 2bedroom
Tower A atau C saja
Prefer maks lt 10
Boleh furnish/semi/kosong
Budget menyesuaikan

More info:
Sherly AS
https://wa.me/+628110000003'''
F4_BUYER = '''DICARI RUMAH DI GRAHA FAMILI SURABAYA

Budget: 10 - 15 Milyar CASH READY
Client serius, sudah siap survey kapan saja.

Kriteria: Luas, layak huni, dan siap transaksi.

Tolong foto dan spek

Wen Propnex Citraland
081600000004'''


def rows(text):
    out = []
    for parsed in parse_bubble(text):
        value = parsed.dict()
        value['document_type'] = value.pop('classification')
        value['extraction_confidence'] = value.pop('confidence')
        value['raw_text'] = text
        out.append(value)
    return out


def row(text):
    return rows(text)[0]


def match(buyer, listing, settings=SETTINGS, index=None):
    request = dict(buyer) if isinstance(buyer, dict) else row(buyer)
    offer = dict(listing) if isinstance(listing, dict) else row(listing)
    if index:
        prepare_document(request, index)
        prepare_document(offer, index)
    return assess_pair(request, offer, settings, 50.0, index)


def checks(result):
    return [line for line in result['explanation'] if line.startswith('Needs check:') or line.startswith('Location not confirmed')]


class UserFailures(unittest.TestCase):
    def test_f1_studio_and_floor_20_never_match_a_2br_below_10_kpa_buyer(self):
        buyer = row(F1_BUYER)
        self.assertEqual((buyer['categories'], buyer['price_max']), (['apartment'], 350 * JT))
        self.assertIsNone(match(buyer, F1_LISTING))
        self.assertIsNone(match(buyer, F1_LISTING.replace('Lantai 20', 'Lantai 8')))       # studio ≠ 2BR
        self.assertIsNone(match(buyer, F1_LISTING.replace('studio corner', '2BR')))        # floor 20 is not below 10
        fits = match(buyer, F1_LISTING.replace('studio corner', '2BR').replace('Lantai 20', 'Lantai 8'))
        self.assertIsNotNone(fits)
        self.assertIn('Unit type: 2BR ✓', fits['explanation'])
        self.assertIn('Floor 8 ✓', fits['explanation'])
        # "View bebas" is any view, "Pembelian KPA" is the payment method: neither is a view requirement.
        self.assertFalse(any('view' in line.lower() for line in checks(fits)), fits['explanation'])
        self.assertEqual(financing_need(buyer['raw_text'].lower()), 'kpa')

    def test_f2_two_buyers_surat_ijo_and_hard_land_minimum(self):
        corporate, surat_ijo = rows(F2)
        self.assertEqual([(p['segment_index'], p['segment_count']) for p in (corporate, surat_ijo)], [(0, 2), (1, 2)])
        self.assertEqual({(p['contact_name'], p['contact_phone']) for p in (corporate, surat_ijo)},
                         {('Joe Aditia', '6282100000002')})
        self.assertTrue(corporate['segment_text'].startswith('_1) Buyer corporate_'))
        self.assertTrue(surat_ijo['segment_text'].startswith('_2) Buyer surat ijo_'))
        self.assertNotIn('surat', corporate['segment_text'].lower().split('buyer corporate')[1])
        self.assertEqual((corporate['land_area_min'], corporate['land_area_max']), (2500, 3500))
        self.assertEqual((surat_ijo['land_area_min'], surat_ijo['land_area_max']), (300, 400))
        self.assertNotIn('hotel', corporate['categories'])  # "jangan tawarkan ex hotel" refuses a hotel
        self.assertEqual(surat_ijo['requirements'], [])     # "ga mau harga SHM/HGB" requires neither
        # Buyer 2 wants Surat Ijo; the Kertajaya Indah house is SHM.
        self.assertIsNone(match(surat_ijo, F2_LISTING))
        self.assertIsNone(match(surat_ijo, 'Dijual tanah Kertajaya\nLT 350\nSHM\nHarga 3 M'))
        self.assertIn('Legal status: Surat Ijo ✓', match(surat_ijo, 'Dijual tanah Kertajaya\nLT 350\nSurat ijo\nHarga 3 M')['explanation'])
        unknown = match(surat_ijo, 'Dijual tanah Kertajaya\nLT 350\nHarga 3 M')
        self.assertEqual(unknown['status'], 'custom')
        self.assertIn('Needs check: legal status not stated — buyer only accepts Surat Ijo', unknown['explanation'])
        # Buyer 1: LT 2500–3500, "paling min 2300 ga mau dibawah itu" is never loosened by the 15 % allowance.
        self.assertIsNone(match(corporate, 'Dijual tanah Raya Darmo\nLT 2299\nSHM\nHarga 100 M'))
        self.assertIsNone(match(corporate, 'Dijual tanah Raya Darmo\nLT 2200\nSHM\nHarga 100 M'))
        edge = match(corporate, 'Dijual tanah Raya Darmo\nLT 2300\nSHM\nHarga 100 M')
        self.assertEqual(edge['status'], 'custom')  # v4.8: "Budget mengikuti" is no budget and the width is unproven
        self.assertIn('Needs check: land area 2300 m² is below the requested 2500–3500 m² (within tolerance)', edge['explanation'])
        self.assertEqual(match(corporate, 'Dijual tanah Raya Darmo\nLT 2600\nSHM\nHarga 100 M')['land'], 100.0)

    def test_f3_only_the_towers_the_buyer_names(self):
        buyer = row(F3_BUYER)
        listing = 'Dijual Apartmen Waterplace 2BR\n\n{}\nlantai 8\nLuas 56m\nKT 2\nKM 1\n*Harga 700 juta nego*'
        self.assertIsNone(match(buyer, listing.format('Tower B')))
        for tower in ('A', 'C'):
            accepted = match(buyer, listing.format('Tower ' + tower))
            self.assertEqual(accepted['status'], 'custom', tower)  # v4.8: "Budget menyesuaikan" is no budget: unclear
            self.assertIn('Needs check: buyer budget not stated', checks(accepted))
            self.assertIn(f'Tower {tower} ✓', accepted['explanation'])
        unstated = match(buyer, listing.format('Full furnish'))
        self.assertEqual(unstated['status'], 'custom')
        self.assertEqual(checks(unstated)[0], 'Needs check: tower not stated — buyer only accepts Tower A or C')
        self.assertLessEqual(unstated['score'], 79)

    def test_f4_ten_to_fifteen_milyar_never_matches_five(self):
        buyer = row(F4_BUYER)
        self.assertEqual((buyer['price_min'], buyer['price_max']), (10 * M, 15 * M))

        def status(price):
            result = match(buyer, f'Dijual rumah Graha Famili\nLT 500 LB 600\nHarga {price}')
            return result and result['status']
        self.assertIsNone(status('5,25 M'))   # Warm floor 10 M × 0,85 = 8,5 M
        self.assertIsNone(status('8,4 M'))
        # v4.8: the request states only purpose, category, area and budget (4 of 5 details): unclear, Custom.
        self.assertEqual(status('8,5 M'), 'custom')
        self.assertEqual(status('9 M'), 'custom')
        self.assertEqual(status('11 M'), 'custom')
        self.assertEqual(status('15 M'), 'custom')
        self.assertTrue(any(c.startswith('Needs check: buyer request has only') for c in
                            checks(match(buyer, 'Dijual rumah Graha Famili\nLT 500 LB 600\nHarga 11 M'))))
        self.assertEqual(status('17 M'), 'custom')   # 15 M × 1,15 = 17,25 M (inside the Warm allowance)
        self.assertIsNone(status('17,5 M'))


class Bands(unittest.TestCase):
    def test_single_budget_two_milyar(self):
        buyer = row('Cari beli rumah Manyar\nLT 100-200\nBudget 2 M')
        self.assertEqual((buyer['price_min'], buyer['price_max']), (None, 2 * M))

        def result(price):
            return match(buyer, f'Dijual rumah Manyar\nLT 150\nHarga {price}')
        self.assertEqual(result('1,6 M')['status'], 'hot')     # Hot 1,6–2 M
        self.assertEqual(result('2 M')['status'], 'hot')
        low = result('1,59 M')
        self.assertEqual(low['status'], 'warm')                # Warm 1,36 M …
        self.assertIn('Needs check: price Rp 1,59 M is 20% below the budget Rp 2 M', low['explanation'])
        self.assertEqual(result('1,36 M')['status'], 'warm')
        self.assertIsNone(result('1,35 M'))
        high = result('2,3 M')
        self.assertEqual(high['status'], 'warm')               # … 2,3 M
        self.assertIn('Needs check: price Rp 2,3 M is 15% above the budget Rp 2 M (within tolerance)', high['explanation'])
        self.assertIsNone(result('2,31 M'))
        # The Hot floor is a setting.
        self.assertEqual(match(buyer, 'Dijual rumah Manyar\nLT 150\nHarga 1,7 M', {**SETTINGS, 'hot_price_floor_pct': 90})['status'], 'warm')

    def test_land_three_hundred(self):
        buyer = row('Cari rumah Manyar\nLT 300\nBudget 5 M')
        self.assertEqual((buyer['land_area_min'], buyer['land_area_max']), (300, 300))

        def result(lt):
            return match(buyer, f'Dijual rumah Manyar\nLT {lt}\nHarga 4,5 M')
        self.assertEqual(result(300)['status'], 'hot')
        self.assertEqual(result(260)['status'], 'warm')        # Warm 255–345 m²
        self.assertEqual(result(255)['status'], 'warm')
        self.assertIsNone(result(250))
        self.assertEqual(result(345)['status'], 'warm')
        self.assertIsNone(result(350))
        # "LT min 300 tidak bisa kurang": a hard minimum keeps its floor.
        hard = row('Cari rumah Manyar\nLT min 300 tidak bisa kurang\nBudget 5 M')
        self.assertIsNone(match(hard, 'Dijual rumah Manyar\nLT 290\nHarga 4,5 M'))
        self.assertEqual(match(hard, 'Dijual rumah Manyar\nLT 300\nHarga 4,5 M')['status'], 'hot')

    def test_nearby_cluster_is_custom(self):
        index = LocationIndex(parse_table(table([[1, 'Alpha Garden', '', '', 'Beta Garden (1.5)'], [2, 'Beta Garden', '', '', '']])))
        nearby = match(row('Cari rumah Alpha Garden LT 100 Budget 3 M'), row('Dijual rumah Beta Garden LT 100\nHarga 2,8 M'), index=index)
        self.assertEqual(nearby['status'], 'custom')
        self.assertGreaterEqual(nearby['pre_cap_score'], 60)
        self.assertLessEqual(nearby['score'], 79)
        self.assertEqual(checks(nearby)[0], 'Needs check: buyer approval for a nearby location (within 4 km)')
        self.assertIn('Nearby alternative: Alpha Garden → Beta Garden · 1.5 km', nearby['explanation'])
        same = match(row('Cari beli rumah Alpha Garden LT 100 Budget 3 M'), row('Dijual rumah Alpha Garden LT 100\nHarga 2,8 M'), index=index)
        self.assertEqual(same['status'], 'hot')

    def test_unconfirmed_area_is_custom_never_warm(self):
        city_only = match('Cari rumah Surabaya Barat LT 100 Budget 3 M', 'Dijual rumah Surabaya LT 100 Harga 2,8 M')
        self.assertEqual(city_only['status'], 'custom')
        self.assertIn('Needs check: requested location not confirmed', checks(city_only))
        self.assertIsNone(match('Cari rumah Surabaya Barat LT 100 Budget 3 M', 'Dijual rumah Manyar LT 100 Harga 2,8 M'))
        # A wish ("prefer area Wiyung") is no requirement: the region the buyer asks for matches, the wish is checked.
        wish = match('Cari rumah Surabaya Barat\nPrefer area Wiyung\nBudget 3 M', 'Dijual rumah Citraland\nHarga 2,8 M')
        self.assertEqual(wish['status'], 'custom')  # v4.8: an unconfirmed preference (and a vague request) is unclear
        self.assertIn('Needs check: preferred area not confirmed (Wiyung)', wish['explanation'])
        # "no Northwest" refuses Northwest.
        self.assertIsNone(match('Cari rumah Sby Barat - no Northwest\nBudget 3 M', 'Dijual rumah Northwest Citraland\nHarga 2,8 M'))


class LegalAndFinancing(unittest.TestCase):
    def test_legal_terms(self):
        self.assertEqual(legal_statuses('shm strata'), {'strata'})
        self.assertEqual(legal_statuses('shmrs'), {'strata'})
        self.assertEqual(legal_statuses('sertifikat shm-rs'), {'strata'})
        self.assertEqual(legal_statuses('shgb, bisa shm'), {'hgb', 'shm'})
        self.assertEqual(legal_statuses('surat hijau (ipt)'), {'surat_ijo'})
        self.assertEqual(legal_statuses('petok d / girik'), {'petok'})
        self.assertEqual(legal_statuses('sertifikat hak milik, bukan surat ijo'), {'shm'})
        self.assertEqual(legal_statuses('ppjb'), {'ppjb'})
        self.assertEqual(legal_statuses('ajb'), {'ajb'})
        self.assertEqual(legal_statuses('hgb di atas hpl'), {'hgb', 'hpl'})
        self.assertEqual(legal_requirement('cari yang surat ijo\nga mau harga shm/hgb'), ({'surat_ijo'}, {'shm', 'hgb'}))
        self.assertEqual(legal_requirement('harus shm'), ({'shm'}, set()))
        self.assertEqual(legal_requirement('legalitas shm'), ({'shm'}, set()))
        self.assertEqual(legal_requirement('shm, petok juga boleh'), ({'shm', 'petok'}, set()))
        self.assertEqual(legal_requirement('petok juga boleh'), (set(), set()))
        self.assertEqual(legal_requirement('prefer shm'), (set(), set()))
        self.assertEqual(legal_requirement('tidak mau petok'), (set(), {'petok'}))

    def test_legal_matching(self):
        shm_only = 'Cari rumah Manyar\nSHM only\nBudget 3 M'
        self.assertIn('Legal status: SHM ✓', match(shm_only, 'Dijual rumah Manyar\nSHM\nHarga 2,8 M')['explanation'])
        self.assertIsNone(match(shm_only, 'Dijual rumah Manyar\nHGB\nHarga 2,8 M'))
        self.assertIsNotNone(match(shm_only, 'Dijual rumah Manyar\nSHGB, bisa SHM\nHarga 2,8 M'))
        unknown = match(shm_only, 'Dijual rumah Manyar\nHarga 2,8 M')
        self.assertEqual(unknown['status'], 'custom')
        self.assertEqual(checks(unknown)[0], 'Needs check: legal status not stated — buyer only accepts SHM')
        # A buyer without a legal requirement gets no legal check at all.
        free = match('Cari beli rumah Manyar\nLT 100-200\nBudget 3 M', 'Dijual rumah Manyar\nLT 150\nHGB\nHarga 2,8 M')
        self.assertEqual(free['status'], 'hot')
        self.assertFalse(any('legal' in line.lower() for line in free['explanation']))
        self.assertIsNone(match('Cari rumah Manyar\nTidak mau petok\nBudget 3 M', 'Dijual rumah Manyar\nPetok D\nHarga 2,8 M'))
        # An apartment "SHM" is the strata SHM (SHMRS).
        self.assertIsNotNone(match('Cari apartemen Educity 2BR\nHarus SHM\nBudget 400 jt', 'Dijual apartemen Educity 2BR\nSHMRS\nHarga 380 jt'))

    def test_financing(self):
        self.assertEqual(financing_need('pembelian kpa'), 'kpa')
        self.assertEqual(financing_need('beli via kpr'), 'kpr')
        self.assertIsNone(financing_need('bisa cash / kpr'))
        self.assertIsNone(financing_need('cash keras'))
        self.assertEqual(financing_offer('harga 1 m cash keras saja'), 'cash_only')
        self.assertEqual(financing_offer('tidak bisa kpr'), 'cash_only')
        self.assertEqual(financing_offer('bisa kpr'), 'mortgage')
        self.assertEqual(financing_offer('harga cash keras'), 'cash_hard')
        kpr = 'Cari rumah Manyar\nBeli KPR\nLT 100-200\nBudget 3 M'
        self.assertIsNone(match(kpr, 'Dijual rumah Manyar\nSHM\nHarga 2,8 M cash keras only'))
        self.assertIsNone(match(kpr, 'Dijual rumah Manyar\nSurat ijo\nHarga 2,8 M'))
        self.assertIsNone(match(kpr, 'Dijual rumah Manyar\nPPJB\nHarga 2,8 M'))
        self.assertIn('Financing: KPR possible ✓', match(kpr, 'Dijual rumah Manyar\nSHM, bisa KPR\nHarga 2,8 M')['explanation'])
        self.assertEqual(match(kpr, 'Dijual rumah Manyar\nLT 150\nSHM\nHarga 2,8 M')['status'], 'hot')   # unknown → no flag
        cash = match('Cari beli rumah Manyar\nLT 100-200\nCash ready\nBudget 3 M', 'Dijual rumah Manyar\nLT 150\nSHM\nHarga 2,8 M cash keras only')
        self.assertEqual(cash['status'], 'hot')


class UnitTowerFloorTerms(unittest.TestCase):
    def test_unit_types(self):
        self.assertEqual(unit_types('type 2br furniture'), {'2br'})
        self.assertEqual(unit_types('apartemen waterplace 2bedroom'), {'2br'})
        self.assertEqual(unit_types('tipe studio corner'), {'studio'})
        self.assertEqual(unit_types('tipe studio\nkt 1\nkm 1'), {'studio'})   # an explicit studio wins over its one bedroom
        self.assertEqual(unit_types('3/4 bedroom'), {'3br', '4br'})
        self.assertEqual(unit_types('1 kamar tidur'), {'1br'})
        self.assertEqual(unit_types('2 kamar mandi'), set())
        self.assertEqual(unit_types('penthouse lantai 30'), {'penthouse'})
        unknown = match('Cari apartemen Educity 2BR\nBudget 400 jt', 'Dijual apartemen Educity\nHarga 380 jt')
        self.assertEqual(unknown['status'], 'custom')
        self.assertEqual(checks(unknown)[0], 'Needs check: unit type not stated — buyer wants 2BR')
        self.assertIsNone(match('Cari apartemen Educity studio\nBudget 400 jt', 'Dijual apartemen Educity 2BR\nHarga 380 jt'))
        # Houses keep the bedroom-count rules.
        self.assertIsNotNone(match('Cari rumah Manyar KT 3\nBudget 3 M', 'Dijual rumah Manyar KT 3\nHarga 2,8 M'))

    def test_towers_and_blocks(self):
        self.assertEqual(tower_names('tower a atau c saja'), ({'a', 'c'}, set()))
        self.assertEqual(tower_names('tower a/c'), ({'a', 'c'}, set()))
        self.assertEqual(tower_names('tw b'), ({'b'}, set()))
        self.assertEqual(tower_names('tower anderson'), ({'anderson'}, set()))
        self.assertEqual(tower_names('educity yale tower lantai 29'), ({'yale'}, set()))
        self.assertEqual(tower_names('tower b lantai 21'), ({'b'}, set()))
        self.assertEqual(tower_names('rumah blok gc 5'), (set(), {'gc'}))
        self.assertEqual(tower_names('rumah di blok belakang'), (set(), set()))
        named = 'Cari apartemen Pakuwon Mall Tower Anderson 1BR\nBudget 1 M'
        self.assertIn('Tower Anderson ✓', match(named, 'Dijual apartemen Anderson Pakuwon Mall 1BR\nHarga 900 jt')['explanation'])
        self.assertIsNone(match(named, 'Dijual apartemen Pakuwon Mall Tower Benson 1BR\nHarga 900 jt'))

    def test_floor_levels(self):
        self.assertEqual(floor_requirement('lantai di bawah 10\nview bebas'), (None, 9, False))
        self.assertEqual(floor_requirement('lantai max 18'), (None, 18, False))
        self.assertEqual(floor_requirement('prefer maks lt 10'), (None, 10, True))
        self.assertEqual(floor_requirement('high floor'), (15, None, False))
        self.assertEqual(floor_requirement('lt bawah'), (None, 10, False))
        self.assertEqual(floor_requirement('lantai 5-15'), (5, 15, False))
        self.assertIsNone(floor_requirement('lt 2500-3500'))
        self.assertEqual(floor_level('tower b lantai 20'), (20, 20))
        self.assertEqual(floor_level('lt 20'), (20, 20))
        self.assertEqual(floor_level('lantai 31 sky garden'), (31, 31))
        self.assertIsNone(floor_level('lb 27m² || lt 27m²'))
        buyer = 'Cari apartemen Educity 2BR\nLantai max 18\nBudget 400 jt'
        self.assertIsNone(match(buyer, 'Dijual apartemen Educity 2BR\nLantai 31 sky garden\nHarga 380 jt'))
        self.assertEqual(match(buyer, 'Dijual apartemen Educity 2BR\nLantai 18\nHarga 380 jt')['status'], 'hot')
        unknown = match(buyer, 'Dijual apartemen Educity 2BR\nHarga 380 jt')
        self.assertEqual(unknown['status'], 'custom')   # v4.8: a floor level not stated is unclear (was Warm)
        self.assertIn('Needs check: floor level not stated (buyer wants floor 18 or lower)', unknown['explanation'])
        # A house's storeys ("2 lantai") and LT (luas tanah) are not floor levels.
        self.assertIsNotNone(match('Cari rumah Manyar\nLT 120\nBudget 3 M', 'Dijual rumah Manyar\nLT 120\n2 lantai\nHarga 2,8 M'))

    def test_view_bebas_is_no_requirement(self):
        free = match('Cari apartemen Educity 2BR\nView bebas\nBudget 400 jt', 'Dijual apartemen Educity 2BR\nHarga 380 jt')
        self.assertFalse(any('view' in line.lower() for line in checks(free)))
        viewing = match('Segera viewing\nCari apartemen Educity 2BR\nBudget 400 jt', 'Dijual apartemen Educity 2BR\nHarga 380 jt')
        self.assertFalse(any('view' in line.lower() for line in checks(viewing)))
        pool = match('Cari apartemen Educity 2BR\nView pool\nBudget 400 jt', 'Dijual apartemen Educity 2BR\nHarga 380 jt')
        self.assertIn('Needs check: view pool not confirmed', pool['explanation'])

    def test_property_words_are_no_places(self):
        for text in ('Dicari apartemen Educity\nLokasi: hook, row jalan, carport\nArea: semi furnish, kosongan, nett, BU, nego, PBG, IMB, IPL\n'
                     'Budget 500 jt',
                     'Dijual rumah Manyar hook row jalan 2 mobil carport 2 KT 3 KM 2 LT 120 LB 150 hadap selatan semi furnished kosongan '
                     'nett BU nego PBG IMB IPL 500rb SHM bisa KPR Harga 2 M'):
            p = parse_message(text)
            self.assertFalse({'hook', 'row jalan', 'carport', 'semi furnish', 'kosongan', 'nett', 'pbg', 'imb', 'ipl', 'shm', 'kpr'}
                             & set(p.locations), p.locations)


class Sections(unittest.TestCase):
    def test_wrapped_numbered_buyer_headings_split(self):
        parts = parse_bubble('*1) Buyer rumah*\nCari rumah Manyar\nBudget 2 M\n\n*2) Buyer apartemen*\nCari apartemen Educity 2BR\nBudget 400 jt\n\n'
                             'Hub Andi 081311110005')
        self.assertEqual([(p.segment_index, p.categories, p.price_max) for p in parts],
                         [(0, ['house'], 2 * M), (1, ['apartment'], 400 * JT)])
        self.assertEqual({p.contact_phone for p in parts}, {'6281311110005'})


class ReviewFixes(unittest.TestCase):
    """v4.3 fix pass: real failures found on the sample (texts shortened, contacts removed)."""

    def test_milliard_price_is_read_and_unread_prices_are_held_back(self):
        listing = 'DIJUAL RMH GRAHA FAMILY\nLT : 252 m2 (12 X 21)\nLB : 275 m2\nRP 6,9 MILLIARD NEGO'
        self.assertEqual(row(listing)['price_max'], 6_900_000_000)
        self.assertIsNone(match(F4_BUYER, listing))                     # Warm floor 8,5 M
        for text, value in (('Harga 1 millyar nego', M), ('Harga 3 miliard', 3 * M), ('Price: IDR 7,2 Billions, Nego', 7.2 * M),
                            ('Harga Update 6,4 nego', 6.4 * M), ('Start from 3,4 M', 3.4 * M)):
            p = parse_message('Dijual rumah Manyar LT 200\n' + text)
            self.assertEqual(p.price_max if p.price_max is not None else p.price_min, value, text)
        rent = parse_message('Disewakan apartemen Orchard 2BR\nNo Furnish : 115jt / thn')
        self.assertEqual((rent.price_max, rent.price_basis), (115 * JT, 'per_year'))
        # A price written in a way that is still not read is never "not stated": the pair is held back (≤59).
        held = match('Cari rumah Manyar\nBudget 3 M', 'Dijual rumah Manyar LT 200\nHarga Rp 1.250.000 m (nego)')
        self.assertLessEqual(held['score'], 59)
        self.assertIn('Needs check: listing price could not be read; pair held back', held['explanation'])

    def test_towers_written_as_one_word_or_a_range(self):
        self.assertEqual(tower_names('tower abc only!!')[0], {'a', 'b', 'c'})
        self.assertEqual(tower_names('tw a-c')[0], {'a', 'b', 'c'})
        self.assertEqual(tower_names('tower a s/d c')[0], {'a', 'b', 'c'})
        self.assertEqual(tower_names('tower anderson')[0], {'anderson'})
        self.assertEqual(tower_names('blok ff, p, pp, r')[1], {'ff', 'p', 'pp', 'r'})   # estate blocks stay whole
        buyer = 'New request!\nCari BELI *Apartemen Waterplace*\nTower *ABC ONLY!!*\nCari tipe 2br\nBudget under *600jt*'
        self.assertEqual(match(buyer, 'Dijual Apartemen Waterplace Tower A 2BR Lantai 12\nHarga 550 jt')['status'], 'hot')
        self.assertIsNone(match(buyer, 'Dijual Apartemen Waterplace Tower E 2BR Lantai 12\nHarga 550 jt'))
        # "Waterplace 3BR / Tower ABC" names an apartment without the word "apartemen".
        short = row('*Buyer Request*\n✅️ Waterplace 3BR\n✅️ Tower ABC\n✅️ Budget 800jt nego')
        self.assertEqual(short['categories'], ['apartment'])
        self.assertIn('Tower B ✓', match(short, 'Dijual Apartemen Waterplace Tower B\n3BR\nLantai 12\nHarga 780 jt')['explanation'])
        refused = row('*Apartemen Waterplace*\n(❌ Tower E & F)\n3 KT\nBudget under 900 jt')
        self.assertIsNone(match(refused, 'Dijual Apartemen Waterplace Tower E 3BR\nHarga 850 jt'))

    def test_unknown_or_misspelt_cluster_is_never_same_area(self):
        index = LocationIndex(parse_table(table([
            [1, 'Cluster Lisbon Granada (Pakuwon Indah)', '', 'Pakuwon Indah', 'Cluster Palazzate (Pakuwon Indah) (1.4)'],
            [2, 'Cluster Palazzate (Pakuwon Indah)', '', 'Pakuwon Indah', '']])))
        listing = 'Dijual rumah Pakuwon Indah Riz Wood\nLT 200\nHarga 4,5 M'
        for buyer in ('Dicari rumah Pakuwon Indah Pallazate\nBudget 5 M', 'Dicari rumah Pakuwon Indah Florentina\nBudget 5 M'):
            result = match(buyer, listing, index=index)
            self.assertEqual(result['status'], 'custom', buyer)
            self.assertTrue(any('exact cluster not confirmed' in line for line in checks(result)), result['explanation'])
        self.assertEqual(row('Dicari rumah Pakuwon Indah Pallazate') and
                         prepare_document(row('Dicari rumah Pakuwon Indah Pallazate'), index)['_geo_ids'],
                         ['cluster palazzate pakuwon indah'])
        self.assertEqual(match('Dicari beli rumah Pakuwon Indah Pallazate\nLT 150-250\nBudget 5 M',
                               'Dijual rumah Pakuwon Indah Cluster Palazzate\nLT 200\nHarga 4,5 M', index=index)['status'], 'hot')
        self.assertEqual(match('Dicari beli rumah Pakuwon Indah\nLT 150-250\nBudget 5 M', listing, index=index)['status'], 'hot')
        # A list separator ends the words after a development name ("Pakuwon Indah | Graha Famili", "… – Lidah Wetan").
        self.assertEqual(index.area_followers('pakuwon indah | graha famili | citraland'), [('pakuwon indah', [])])
        self.assertEqual(match('Cari beli rumah Pakuwon Indah | Wiyung\nLT 150-250\nBudget 5 M', listing, index=index)['status'], 'hot')

    def test_floor_levels_with_punctuation_lt_lb_and_unit_numbers(self):
        for text in ('Lantai 20, view city', 'Lantai 20.', 'Lantai 20 unit 05', 'Tower Harvard Unit 3105'):
            self.assertIsNone(match(F1_BUYER, f'Jual apartemen Educity 2BR\n{text}\nHarga 300 jt'), text)
        self.assertEqual(floor_level('lantai 37, hanya ada 4 unit'), (37, 37))
        self.assertEqual(floor_level('lantai 7 unit 06 (a706)'), (7, 7))
        self.assertEqual(floor_level('lt 35 lb 35\nlantai 5'), (5, 5))         # LT/LB is land and building area
        fits = match(F1_BUYER, 'Jual apartemen Educity 2BR\nLT 35 LB 35\nLantai 5\nHarga 300 jt')
        self.assertIn('Floor 5 ✓', fits['explanation'])

    def test_unit_types_ignore_maid_rooms_furniture_sizes_and_links(self):
        for text in ('3 kt + 1 kamar pembantu', '3BR + 1BR', '4 kamar tidur, 1 kamar pembantu'):
            self.assertNotIn('1br', unit_types(text.lower()), text)
        self.assertEqual(unit_types('1 bed single'), set())
        self.assertEqual(unit_types('ranjang susun 2 bed'), set())
        self.assertEqual(unit_types('luas 21 m2, 1 kt'), {'1br'})
        self.assertEqual(unit_types('https://x.com/apartemen-3-1br-murah 2br'), {'2br'})
        self.assertEqual(unit_types('tipe 3br dijadikan 2br ada 1 kt + 1 km pembantu'), {'2br'})
        self.assertEqual(unit_types('apartment waterplace 3 br\ntower f\n3 kt menjadi 2 kt'), {'2br'})
        self.assertEqual(unit_types('kamar tidur 1 (bisa disekat jadi 2 kamar)'), {'1br'})
        # "1BR ya bukan Studio": a refused unit type rejects a listing that has it.
        renter = '*Renter Request*\nApartment 1BR Surabaya Barat\n• 1BR ya bukan Studio\n• Surabaya Barat ONLY ya\nBudget 40 jt/th'
        self.assertIsNone(match(renter, 'Disewakan Apartemen Puncak CBD Tower A 1BR (Studio)\nSurabaya Barat\nHarga 35 jt/th'))
        self.assertIsNotNone(match(renter, 'Disewakan Apartemen Puncak CBD Tower A 1BR\nSurabaya Barat\nHarga 35 jt/th'))

    def test_several_properties_in_one_part_are_held_back(self):
        edition = ('*EDISI APARTEMEN*\n\n1. APARTEMEN GUNAWANGSA MERR\nTower B\nType 1 BR\nHarga 205 jt\n\n'
                   '2. APARTEMEN EDUCITY\nTower Yale\nStudio\nHarga 250 jt\n\n3. APARTEMEN WATERPLACE\nTower A\n2BR\nLantai 8\nHarga 650 jt')
        result = match(F3_BUYER, edition)
        if result is not None:
            self.assertLessEqual(result['score'], 59)

    def test_area_readings(self):
        self.assertEqual((row('Dijual rumah Manyar\nLT/LB : 383/400\nHarga 5 M')['land_area_min'],
                          row('Dijual rumah Manyar\nLT/LB : 383/400\nHarga 5 M')['building_area_min']), (383, 400))
        for text, land in (('LT.. 375 m2', 375), ('LT (+ dimensi): 280 m2', 280), ('Luas Tanah (LT): 82,5 m²', 82.5),
                           ('LT : +/- 480 m2', 480), ('L.Tanah: 14.060 mtr', 14060), ('Luas 135m2 (9x15)', 135)):
            self.assertEqual(row(f'Dijual rumah Manyar\n{text}\nHarga 2 M')['land_area_min'], land, text)
        minimum = row('Cari rumah Manyar\nMinimal Luas tanah 150 m²\nBudget 3 M')
        self.assertEqual((minimum['land_area_min'], minimum['land_area_max']), (150, None))
        self.assertEqual(match(minimum, 'Dijual rumah Manyar LT 200 Harga 2,8 M')['status'], 'hot')
        warehouse = row('Disewakan gudang Margomulyo\nLuas 700 m2 @ 200 jt/th.')
        self.assertEqual((warehouse['land_area_min'], warehouse['building_area_min'], warehouse['price_max']), (700, 700, 200 * JT))
        self.assertIsNone(match('Cari sewa gudang Margomulyo\nLuas bangunan 2000-3000 m2\nBudget 500 jt/th', warehouse))

    def test_legal_preferences_conditions_and_spellings(self):
        self.assertEqual(legal_requirement('surat hgb murni lebih disuka'), (set(), set()))
        self.assertEqual(legal_requirement('cari penjual yang bukan asbun sok ngerti trx ajb'), (set(), set()))
        self.assertEqual(legal_requirement('pembayaran kpr pastikan uda perpanjang hgb nya'), (set(), set()))
        self.assertEqual(legal_requirement('buyer cari rumah kost di siwalankerto shm saja budget 2m diutamakan bu'), ({'shm'}, set()))
        self.assertEqual(legal_statuses('sertifikat : s h m'), {'shm'})
        self.assertEqual(legal_statuses('sertifikat : h g b'), {'hgb'})
        self.assertEqual(legal_statuses('surat : hijau'), {'surat_ijo'})
        self.assertEqual(legal_statuses('s.ijo'), {'surat_ijo'})
        self.assertEqual(legal_statuses('kitchen set hpl'), set())
        self.assertEqual(legal_statuses('bukan hak pakai / hpl, shm'), {'shm'})
        self.assertIsNone(match('Cari rumah Manyar\nPayment KPR\nBudget 3 M', 'Dijual rumah Manyar\nSurat : Hijau\nHarga 2,8 M'))
        self.assertIsNone(match('Cari rumah Kertajaya\nCari yang surat ijo\nBudget 3 M', 'Dijual rumah Kertajaya\nSertifikat : S H M\nHarga 2,8 M'))
        self.assertIsNotNone(match('Buyer lahan Driyorejo\nLT 5000\nsurat HGB Murni lebih disuka\nBudget 2-2,5 jt/m2',
                                   'Dijual tanah Driyorejo LT 5000\nSHM\nHarga 2,2 jt/m2'))

    def test_location_evidence(self):
        top = 'Hubungi\nFitria\nBrighton Home Manyar\n081200000010\n\nDIJUAL RUMAH BARU\nRUNGKUT MAPAN\nLT 110\nHarga 1,7 M'
        self.assertEqual(row(top)['locations'], ['rungkut mapan'])
        self.assertEqual(row(top)['contact_name'], 'Fitria')
        self.assertIsNone(match('Cari rumah Manyar Tompotika\nBudget 2 M', top))
        loop = row('*Renter Request*\n1. Sewa ruko Surabaya barat Loop only\n2. LT 80-150\n3. Budget 100 jt/th')
        self.assertEqual(loop['strict_locations'], ['the loop'])  # v4.6: The Loop (Graha Famili) is a known place
        elsewhere = match(loop, 'Disewakan ruko Surabaya Barat LT 100 Harga 90 jt/th')
        self.assertEqual(elsewhere['status'], 'custom')
        self.assertLessEqual(elsewhere['score'], 79)                       # Custom is never scored above Warm
        self.assertEqual(match(loop, 'Disewakan ruko The Loop Citraland LT 100 Harga 90 jt/th')['status'], 'hot')
        gresik = 'JualSewa Rumah\nOPRA CITY - Gresik\nLT 60 LB 53\nRp 22.500.000/tahun\n• Akses Terdekat: Tol Romokalisari, CBD Citraland Surabaya'
        self.assertIsNone(match('Cari sewa rumah surabaya barat\nBudget 30 jt/th', gresik))
        title = row('*DISEWAKAN GUDANG, SELANGKAH KE EXIT TOL ROMOKALISARI, LOKASI PERGUDANGAN ROMOKALISARI - GRESIK*\nLT 1060 m²\n'
                    'Harga sewa 350 jt/tahun')
        self.assertIn('gresik', title['locations'])
        darmo = match('RENTER REQ\n- 50-80jt/th\n- ruko area surabaya barat\n- satelit darmo dsk',
                      'Disewakan ruko Citraland Utara LT 100 Harga 70 jt/th')
        self.assertEqual(darmo['status'], 'custom')

    def test_other_apartment_buildings_are_rejected(self):
        self.assertIsNone(match('Sewa Apart Cityloft 2BR\nBudget 100 jt/th', 'Disewakan Apartemen La Riz Mansion 2BR\nHarga 90 jt/th'))
        self.assertIsNone(match('Cari apartemen Amor atau Bella 2BR\nBudget 1 M', 'Dijual Apartemen Educity 2BR\nHarga 900 jt'))
        listed = 'Cari apartemen sby barat (tanglin, orchard, anderson, benson, waterplace) 2BR\nBudget 1 M'
        self.assertIsNone(match(listed, 'Dijual Apartemen Puncak CBD 2BR\nHarga 900 jt'))
        self.assertIn('Same building: Tanglin', match(listed, 'Dijual Apartemen Tanglin Pakuwon Mall 2BR\nHarga 900 jt')['explanation'])
        self.assertIsNone(match('Dicari sewa apartemen lariz laviz pakuwon mall 2-3BR\nBudget 150 jt/thn',
                                'Disewakan Anderson Pakuwon Mall 3BR\nHarga 140 jt/thn'))
        self.assertEqual(match('Cari apartemen Grand Sungkono Lagoon 2BR\nBudget 1 M',
                               'Dijual Apartemen GSL tower Venetian 2BR\nHarga 900 jt')['status'], 'hot')

    def test_categories_purpose_blocks_and_bedrooms(self):
        self.assertEqual(row('Dijual tanah industri mojokerto LT 5000 harga 600rb per m2')['categories'], ['land'])
        self.assertEqual(row('Dijual gudang industri Margomulyo LT 1000 Harga 10 M')['categories'], ['warehouse'])
        house = row('Cari Rumah harga BU Surabaya Barat\nBudget 2 M\nkalau ada hitung tanah boleh coba send')
        self.assertEqual(house['categories'], ['house'])
        self.assertIsNone(match(house, 'Dijual tanah Citraland LT 150 Harga 1,8 M'))
        header = rows('*LIST JUAL & SEWA APARTEMEN PAKUWON*\n\n1. Orchard 2BR\nLantai 12\nSewa 120 jt/thn\n\n'
                      '2. La Viz Mansion 2BR\nLantai 8\nHARGA: 1.95M\n\nHub Andi 081311110000')
        self.assertEqual([p['transaction_type'] for p in header], ['rent', 'sale'])
        self.assertEqual(row('Dijual Apartemen Orchard 2BR\nHarga Jual : 385.000.000\ndikurangi dengan harga oper sewa')['transaction_type'], 'sale')
        both = match('Cari sewa apartemen La Viz 2BR', 'Dijual / Disewakan apartemen La Viz Mansion 2BR\nHARGA: 1,95 M')
        self.assertEqual(both['status'], 'custom')
        self.assertIn('Needs check: rent offer not confirmed (listing states only a sale price)', both['explanation'])
        closed = 'Cari rumah Graha Famili prefer blok FF, P, PP, R, opsi lainnya blok A/AA, B/BB, jangan tawarkan blok lain\nBudget 15 M'
        self.assertIsNone(match(closed, 'Dijual rumah Graha Famili blok K\nHarga 14 M'))
        self.assertIn('Blok PP ✓', match(closed, 'Dijual rumah Graha Famili blok PP\nHarga 14 M')['explanation'])
        # A wished block ("prefer blok FF") is no requirement: blok K is no reject. v4.8: an unmet wish is never Hot.
        wish = 'Cari beli rumah Graha Famili prefer blok FF\nLT 300-500\nBudget 15 M'
        other = match(wish, 'Dijual rumah Graha Famili blok K\nLT 400\nHarga 14 M')
        self.assertEqual(other['status'], 'custom')
        self.assertIn('Needs check: requirement not verified: “prefer blok ff”', other['explanation'])
        self.assertEqual(match(wish, 'Dijual rumah Graha Famili blok FF\nLT 400\nHarga 14 M')['status'], 'hot')
        natura = match('Cari rumah Citraland Utama / Graha Natura\nBudget 3 M', 'Dijual rumah Graha Natura\nHarga 2,8 M')
        self.assertFalse(any('Citraland Utama' in line for line in natura['explanation']))
        self.assertIsNone(match('Cari sewa rumah surabaya barat 3-4 kamar budget 30jt/thn', 'Disewakan rumah Citraland KT 2\nHarga 28 jt/thn'))
        self.assertIsNotNone(match('Renter rumah Graha only\nLantai 1 ada 2kamar\nBudget 150jt/thn',
                                   'Disewakan rumah Graha Famili\nKT 4+2\nHarga 130 jt/thn'))

    def test_views_and_sections(self):
        self.assertIsNone(match('Sewa apartemen Educity 2BR\nTidak mau view pool\nBudget 30 jt/th',
                                'Disewakan apartemen Educity 2BR view pool\nHarga 28 jt/th'))
        city = match('Cari apartemen Educity 2BR\ncity view yang indah\nBudget 400 jt', 'Dijual apartemen Educity 2BR view city\nHarga 380 jt')
        self.assertEqual(city['status'], 'hot')
        parts = rows('RENTER *A* REQUEST\nAPARTMENT BELLA/AMOR PAKUWON CITY\nSEWA 6 BULAN\nBUDGET YANG MURAH\n\n'
                     'RENTER *B* REQUEST\nAPARTMENT SURABAYA BARAT 2BR\nSEWA 3 BULAN\nBUDGET IKUT\n\nELIA\n081200000011')
        self.assertEqual(len(parts), 2)
        estate = rows('Dijual 3 rumah Saronojiwo Estate di Surabaya Selatan.\n\nSertipikat : SHM\n\nAda 3 tipe :\nKavling A\nLT : 79 m2\n'
                      'LB : 153 m2\nKamar Tidur : 4\nHarga : Rp.1,85M\n\nKavling B\nLT : 126 m2\nLB : 232 m2\nKamar Tidur : 3+1\n'
                      'Harga : Rp.2,25M\n\nKavling C\nLT : 61 m2\nLB : 118 m2\nKamar Tidur : 3\nHarga : Rp.1,495M')
        self.assertEqual(len(estate), 3)
        self.assertTrue(all(p['categories'] == ['house'] and 'surabaya selatan' in p['locations'] for p in estate), estate)


class FinalRoundFixes(unittest.TestCase):
    """The final judge's findings (short synthetic texts)."""

    def test_unit_type_survives_kosongan_furnished_and_semi(self):
        for text, unit in (('1 br kosongan', '1br'), ('2 br kosongan lantai 9', '2br'), ('type 2 bedroom kosong', '2br'),
                           ('2br furnished', '2br'), ('3 br semi furnish', '3br')):
            self.assertEqual(unit_types(text), frozenset({unit}), text)
        self.assertEqual(unit_types('3 kt + 1 kamar kosong'), frozenset({'3br'}))  # a spare room is still no unit type
        buyer = '*HOT SEWA REQUEST*\nApartemen grand sungkono lagoon\nTower Caspian / 1 BR kosongan\nBudget 60 jt/thn\nsewa 2 thn'
        self.assertIsNone(match(buyer, 'Disewakan Apartemen GSL Tower Caspian\nTipe Studio Lt 20\nKosongan\nSewa 55 jt/tahun'))
        self.assertIsNone(match(buyer, 'Disewakan Apartemen GSL Tower Caspian\n3 BR Lt 20\nKosongan\nSewa 55 jt/tahun'))
        same = match(buyer, 'Disewakan Apartemen GSL Tower Caspian\n1 BR Lt 20\nKosongan\nSewa 55 jt/tahun')
        self.assertEqual(same['status'], 'hot')
        self.assertIn('Unit type: 1BR ✓', same['explanation'])
        furnished = match(buyer, 'Disewakan Apartemen GSL Tower Caspian\nOne Bedroom Lt 20\nFurnished baru\nSewa 55 jt/tahun')
        self.assertIsNone(furnished)  # v4.7: kosongan vs furnished is a proven conflict

    def test_total_and_per_m2_budget_are_two_budgets(self):
        buyer = 'Buyer Req:\nCari tanah/rumah daerah pondok tjandra\n- luas sekitar 200-300 m2\n- budget 2,5-3M atau 9jt/m2'
        parsed = row(buyer)
        self.assertEqual((parsed['price_min'], parsed['price_max'], parsed['price_basis']), (2.5 * M, 3 * M, 'total'))
        self.assertIsNone(match(buyer, 'Dijual rumah Pondok Tjandra\nLT 300\nHarga 4,05 M'))   # 13,5 jt/m² and 4,05 M
        self.assertIsNone(match(buyer, 'Dijual rumah Pondok Tjandra\nLT 238\nHarga 5,9 M'))
        self.assertEqual(match(buyer, 'Dijual rumah Pondok Tjandra\nLT 250\nHarga 2,9 M')['status'], 'hot')
        per_m2 = match(buyer, 'Dijual tanah Pondok Tjandra\nLT 250\nHarga 9 jt/m2')       # the per-m² budget fits
        self.assertEqual(per_m2['status'], 'hot')
        self.assertIsNone(match(buyer, 'Dijual tanah Pondok Tjandra\nLT 400\nHarga 14 jt/m2'))
        listing = row('Dijual tanah Citraland\nLT 200\nHarga 3 M / 15 jt per m2')
        self.assertEqual((listing['price_max'], listing['price_basis']), (3 * M, 'total'))
        self.assertEqual((row('Buyer cari tanah Citraland\nBudget 8-9 jt/m2')['price_max'], row('Renter cari rumah\nBudget 15-20 juta/tahun')['price_basis']),
                         (9 * JT, 'per_year'))

    def test_building_proof_uses_whole_names(self):
        buyer = 'BUYER REQUEST\nCari Apartemen water place tower C, A dan B saja\n3 Bed Room full furnished budget dibawah 1 M'
        self.assertIsNone(match(buyer, 'Apartemen Wisma Gading Permai\nKelapa Gading Jakut\nLuas 82 M\nHarga Jual Rp. 998Jt\n'
                                       '1 Waterheater Listrik Merk Ariston'))
        unnamed = match('Buyer request\nCari apartemen water place 2BR\nBudget 800 jt', 'Dijual apartemen\n2BR full furnish\nWater heater\nHarga 750 jt')
        self.assertTrue(unnamed is None or unnamed['status'] == 'custom', unnamed)
        self.assertFalse(unnamed and any(line.startswith('Same building') for line in unnamed['explanation']))
        named = match('Buyer request\nCari apartemen water place 2BR\nBudget 800 jt', 'Dijual apartemen Waterplace 2BR\nWater heater\nHarga 750 jt')
        self.assertEqual(named['status'], 'hot')
        self.assertTrue(any(line.startswith('Same') and 'Waterplace' in line for line in named['explanation']), named['explanation'])

    def test_lahan_parkir_is_no_land(self):
        self.assertEqual(row('Dicari Beli Ruko Surabaya Barat\nAda lahan parkiran\nBudget 3 M')['categories'], ['shophouse'])
        self.assertEqual(row('Cari beli rumah Surabaya\nlahan parkir luas lebih disukai\nBudget 12 M')['categories'], ['house'])
        self.assertIn('land', row('Dijual lahan 2000 m2 Gresik\nHarga 5 M')['categories'])
        self.assertIsNone(match('Dicari Beli Ruko Surabaya Barat\nAda lahan parkiran\nBudget 3 M',
                                'Dijual tanah kavling Citraland\nLuas 12 x 22 (264 m2)\nHarga 3 M'))

    def test_common_land_area_forms(self):
        for text, area in (('Dijual tanah Citraland\nLuas 1.200 m2\nHarga 10 M', 1200), ('Dijual tanah Citraland\nLT: 1200m\nHarga 10 M', 1200),
                           ('Dijual tanah Citraland\nluas tanah ±1.2 ha\nHarga 10 M', 12000), ('Dijual tanah Citraland\n20x60\nHarga 10 M', 1200),
                           ('Dijual tanah Citraland\nL 500\nHarga 10 M', 500), ('Jual Toko Bangkingan\nL tanah : 418 m\nHarga 2,9 M', 418),
                           ('Dijual Tanah Kavling Citraland\n9 x 15 = 135 m2\n12.8 jt / m2', 135),
                           ('Dijual tanah Graha Family\ndimensi : 14X31=434M. Rp 20jt/m2', 434),
                           ('Dijual tanah Pier\nLs : 15.376 m2 ( 124 x 124 )\nHarga 30 M', 15376),
                           ('Dijual rumah Bukit Palma\nUkuran: 72m² (6x12)\nHarga 1,5 M', 72),
                           ('Disewakan gudang bumi maspion\nTanah 550 m2\nBangunan 500 m2\nHarga sewa 200 jt/th', 550),
                           ('Dijual tanah Citraland LT @ 450 ( 15x30 )\nHarga 15 jt/m2', 450)):
            self.assertEqual(row(text)['land_area_min'], area, text)
        self.assertIsNone(row('Dijual rumah\nKamar 3x4 = 12 m2\nHarga 1 M')['land_area_min'])
        self.assertEqual((row('Cari tanah Surabaya Barat\nLuas Tanah 2000-1Ha\nBudget mengikuti')['land_area_min'],
                          row('Cari tanah Surabaya Barat\nLuas Tanah 2000-1Ha\nBudget mengikuti')['land_area_max']), (2000, 10000))
        # Out-of-band plots are rejected instead of Warm with "land area not stated".
        self.assertIsNone(match('Buyer request\nDicari tanah untuk kos-kosan area Surabaya\nLuasan +/- 800 m2\nBudget 4 M',
                                'Dijual Tanah Kavling Citraland Surabaya Barat\n9 x 15 = 135 m2\nPPJB\n12.8 jt / m2'))
        self.assertIsNone(match('Dibutuhkan tanah Surabaya\nLuas: Minimal 4 hektare (40.000 m²)\nBudget 40 M',
                                'Jual tanah Raya Bangkingan Surabaya\nL tanah : 418 m\nHarga 2,9 M'))

    def test_strict_buyer_and_listing_in_that_building(self):
        for buyer, listing, name in (('Buyer request apart 2BR\nBenson only\nBudget 1,2 M', 'Dijual Apartemen BENSON\nType 2 BR\nLantai 8\nHarga 1,1 M', 'Benson'),
                                     ('Buyer request\nApart Pakuwon mall Only\n3BR\nBudget 2,5 M', 'Dijual apartemen 3 BEDROOM\nANDERSON PAKUWON\nHarga 2,2 M', 'Pakuwon Mall')):
            result = match(buyer, listing)
            self.assertEqual(result['status'], 'hot', result['explanation'])
            self.assertIn('Same building: ' + name, result['explanation'])
            self.assertFalse(any(line.startswith('Location not confirmed') for line in result['explanation']))
        unnamed = match('Buyer request apart 2BR\nBenson only\nBudget 1,2 M', 'Dijual Apartemen 2 BR\nLantai 8\nHarga 1,1 M')
        self.assertEqual(unnamed['status'], 'custom')

    def test_headline_price_drop_is_no_price(self):
        self.assertEqual(row('_*HARGA TERJUN 700 JUTA RUMAH WIYUNG 2 LANTAI*_\nDIJUAL RUMAH\nBABATAN MUKTI\nLT 175\n'
                             '*Harga : 2,3 M --> 1,6M nego tipis*')['price_max'], 1.6 * M)
        self.assertEqual(row('TURUN 300 JT! Rumah Citraland\nLT 200\nHarga 2,7 M nego')['price_max'], 2.7 * M)
        self.assertEqual(row('Dijual rumah Citraland\nLT 200\nHarga 2 M\nHarga turun 1,6 M')['price_max'], 1.6 * M)
        self.assertEqual(row('HARGA TERJUN 1,5 M\nDijual rumah Wiyung\nLT 175')['price_max'], 1.5 * M)  # the only amount stays
        self.assertIsNone(match('BUYER REQUEST\nBeli Rumah Daerah Wiyung\nKamar minim 3\nBudget Beli Under Rp 1 M',
                                '_*HARGA TERJUN 700 JUTA RUMAH WIYUNG*_\nDIJUAL RUMAH\nLT 175\nKamar tidur : 4+1\n*Harga : 2,3 M --> 1,6M nego*'))

    def test_contact_name_skips_courtesy_words(self):
        parts = rows('Dijual tanah Raya Bibis\nLuas tanah 9900 m\nHarga Rp 6 juta/m nego\n\nPlease 📲\n*RATNA LIE*\n'
                     '*BRIGHTON AMAZING*\nhttps://wa.me/628110000020')
        self.assertEqual((parts[0]['contact_name'], parts[0]['contact_phone']), ('RATNA LIE', '628110000020'))

    def test_unread_requirements_keep_pairs_out_of_hot(self):
        factory = match('DICARI EX PABRIK\nLT min 3 ha\nKota : Mojokerto\nPersyaratan :\n- SHM, Sumber mata air\n- SIPA',
                        'Dijual Pabrik di Mojosari Mojokerto\nLT 7,8 ha\nSertipikat SHM\nHarga 175M')
        self.assertEqual(factory['status'], 'custom')  # v4.8: unconfirmed requirements are Custom, never Warm
        self.assertIn('Needs check: water source ("sumber mata air") not confirmed', factory['explanation'])
        self.assertIn('Needs check: water extraction permit (SIPA) not confirmed', factory['explanation'])
        wood = match('Buyer request\nArea Mojokerto\nEx pabrik kayu\nDiutamakan yg ad mesin potong\nBudget 40 M',
                     'Dijual pabrik briket Mojokerto\nLT 2,1 Ha\nHarga jual 36 M')
        self.assertEqual(wood['status'], 'custom')
        self.assertIn('Needs check: former use ("ex pabrik kayu") not confirmed', wood['explanation'])
        self.assertIn('Needs check: equipment ("mesin potong") not confirmed', wood['explanation'])
        lake = 'RENTER REQUEST\nRumah Northwest\nKHUSUS DEPAN DANAU\nKT 3\nBudget 40-55 jt/thn'
        self.assertIn('Needs check: location "depan danau" not confirmed',
                      match(lake, 'Disewakan rumah Northwest Central Citraland\nKamar Tidur 3\nharga 40jt/th')['explanation'])
        self.assertEqual(match(lake, 'Disewakan rumah Northwest Lake depan danau\nKamar Tidur 3\nharga 40jt/th')['status'], 'hot')
        self.assertEqual(row('Request renter\nRumah sby timur\nPeruntukan kantor & gudang mesin\nBudget 100 jt/th')['categories'], ['house'])
        semi = 'Renter request\nApartemen Educity 1BR\nSemi kosongan (ada AC)\nBudget 30 jt/th'
        self.assertFalse(any('unfurnished' in line for line in match(semi, 'Disewakan apartemen Educity 1BR semi furnished\nHarga 28 jt/th')['explanation']))


if __name__ == '__main__':
    unittest.main()
