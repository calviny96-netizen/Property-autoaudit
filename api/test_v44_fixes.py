"""v4.4: fixes for the independent judge's findings on real data (v4.3 review): landmarks are no location, unread buyer
sizes, ❎ exclusion marks, joined tower letters, "pusat kota Surabaya", unknown warehouse complexes, lease terms,
"lebar di atas 10", facing words hidden by zero-width characters or written as "Utara, ppjb". Short synthetic texts
(contact numbers are placeholders)."""
import hashlib
import unittest

from parser import (clean_text, facing_directions, lease_need, lease_offer, message_hash, parse_message, strip_landmarks,
                    tower_names)
from test_v43_terms import JT, M, checks, match, row


class Landmarks(unittest.TestCase):
    """A place named only as nearby or a facility is never the listing's location or building proof."""
    EDUCITY = ('*Disewakan Apartment Educity Tower Princeton*\nLantai 27\nType Studio\n'
               'Fasilitas tersedia : Kolam renang, Tempat Gym, dekat sekolah Gloria, Xin Zhong, Cita Hati, '
               'Falkutas WM kedokteran dan Eastcost Mall\nSewa 27 juta/ thn. Nego\nChen 081200000010')

    def test_a_mall_in_the_facility_list_is_no_building(self):
        listing = row(self.EDUCITY)
        self.assertEqual(listing['locations'], ['educity'])
        buyer = 'Dicari sewa apartemen eastcoast lama dekat Hokky 1Br atau studio\nEvita 081200000011'
        self.assertIsNone(match(buyer, listing))
        self.assertEqual(match('Dicari sewa apartemen Educity studio\nEvita 081200000011', listing)['status'], 'custom')  # v4.8: no budget

    def test_items_after_dekat_stay_landmarks(self):
        listing = row('Dijual Rumah 1 lantai, lokasi strategis dekat pintu tol Satelit, tempat kuliner Raya Darmo Permai I, '
                      'Pasar Permai\n\nKUPANG INDAH\nLT 388 m2\nHarga 4,6 M\nDjaja 081200000012')
        self.assertNotIn('darmo permai', listing['locations'])
        self.assertIsNone(match('Buyer Request\nRumah Area Darmo Permai\nBudget 5M', listing))
        for line, place in (('Rumah Dukuh Pakis depan kampus Petra', 'petra'), ('Ruko Wiyung depan Pakuwon Mall', 'pakuwon mall'),
                            ('Rumah Nginden Intan, tinggal jalan kaki ke UNTAG', 'untag')):
            self.assertNotIn(place, parse_message(f'Dijual {line}\nLT 100\nHarga 2 M').locations, line)
        # The front part of an estate is still the estate.
        self.assertIn('citraland', parse_message('Dijual rumah cluster depan Citraland\nLT 100\nHarga 2 M').locations)
        self.assertEqual(strip_landmarks('lokasi depan mall delta plaza'), 'lokasi ')

    def test_landmarks_are_neither_exclusions_nor_building_proof(self):
        buyer = 'Cari rumah Wiyung, tidak mau Citraland\nBudget 2 M'
        self.assertIsNotNone(match(buyer, 'Dijual rumah Wiyung\nLT 100\nDekat Citraland\nHarga 1,9 M'))
        self.assertIsNone(match(buyer, 'Dijual rumah Citraland\nLT 100\nHarga 1,9 M'))
        self.assertIsNone(match('Cari apartemen Educity 2BR\nBudget 400 jt',
                                'Dijual Apartemen Eastcoast Residence 2BR dekat Educity\nHarga 380 jt'))


class BuyerSizes(unittest.TestCase):
    def test_size_after_the_asset_word(self):
        buyer = row('Renter Request gudang untuk industri\nProduksi pvc\nGudang 1500-2000\nDaerah benowo, Domas, Gresik\n'
                    'Sewa 2 thn\nBudget menyesuaikan lokasi\nGeng 081200000013')
        self.assertEqual((buyer['land_area_min'], buyer['land_area_max']), (1500.0, 2000.0))
        small = 'Disewakan gudang Bumi Maspion Romokalisari\nLT 600 m²\nLB 480 m²\nHarga Sewa Rp 200 Juta/Tahun\nMinimal Sewa 2 Tahun'
        self.assertIsNone(match(buyer, small))
        self.assertIsNotNone(match(buyer, small.replace('LT 600', 'LT 1800').replace('LB 480', 'LB 1500')))
        self.assertEqual(row('Cari tanah Wiyung\nTanah 300-500 m2\nBudget 5 M')['land_area_max'], 500.0)
        # A budget written after the asset is no size.
        self.assertIsNone(row('Cari gudang Margomulyo\nGudang 2-3 M')['land_area_min'])

    def test_ideal_and_minimum_building_sizes(self):
        buyer = row('Renter Request :\n- Rumah, ruko, bangunan usaha untuk klinik\nArea : Wiyung\nKriteria:\n'
                    '• Bangunan ideal ≥200 m²\n• Parkir min. 3 mobil\n-Budget 150jt/tahun\nImam 081200000014')
        self.assertEqual((buyer['building_area_min'], buyer['building_area_max']), (200.0, None))
        self.assertIsNone(match(buyer, 'Disewakan rumah 2 lantai Wiyung\nLuas tanah 60 m2\nLuas bangunan 120 m2\n'
                                       'Harga sewa 50 jt / per thn'))
        fits = match(buyer, 'Disewakan rumah Wiyung\nLT 330 m²\nLB 275 m²\nHarga Sewa : Rp 135.000.000 / tahun')
        self.assertIn('Building area: 100%', fits['explanation'])
        self.assertEqual(row('Cari rumah Wiyung\nluas bangunan min 200\nBudget 3 M')['building_area_min'], 200.0)


class ExclusionMarks(unittest.TestCase):
    BUYER = ('*BUYER REQUEST*\n\nGudang\nLuas min. 500 m2\nArea Mastrip, Waru,\nSurabaya Timur\nArea Kenjeran,Kedung cowek ❎️\n'
             'Surabaya Barat❎️\nAkses truk ban double\nBudget 5 M\nIme\n081200000015')

    def test_a_cross_after_places_refuses_them(self):
        buyer = row(self.BUYER)
        self.assertTrue({'kenjeran', 'kedung cowek', 'surabaya barat'} <= set(buyer['exclusions']))
        self.assertFalse({'kenjeran', 'kedung cowek', 'surabaya barat'} & set(buyer['locations']))
        self.assertIn('waru', buyer['locations'])
        self.assertIsNone(match(buyer, 'DI JUAL GUDANG\nLOKASI MARGOMULYO\nLT : 594 m2\nHARGA JUAL : 5,6 M (NEGO)'))
        self.assertIsNotNone(match(buyer, 'Dijual gudang Waru\nLT 600 m2\nHarga 4,8 M'))

    def test_a_tick_keeps_its_place(self):
        buyer = parse_message('Cari rumah\nSurabaya Timur ✅, Surabaya Barat ❌\nBudget 2 M')
        self.assertIn('surabaya timur', buyer.locations)
        self.assertNotIn('surabaya barat', buyer.locations)
        self.assertIn('surabaya barat', buyer.exclusions)
        self.assertNotIn('surabaya timur', buyer.exclusions)


class TowerLetters(unittest.TestCase):
    BUYER = ('*HOT BUYER!!!*\n\n- Apartemen Waterplace ABC only\n- 3Bedroom Ori\n- Budget dibawah 900jt\n'
             '- Kosong/semi/full furnish\n*Priska - 081200000016*')

    def test_letters_after_a_building_or_tower(self):
        for text in ('apartemen waterplace abc only', 'apartemen educity tower abc only', 'waterplace a/b/c saja'):
            self.assertEqual(tower_names(text)[0], {'a', 'b', 'c'}, text)
        for text in ('apartemen educity di lantai 10', 'waterplace bed only', 'apartemen waterplace ada 2 unit'):
            self.assertEqual(tower_names(text)[0], set(), text)

    def test_other_towers_are_rejected(self):
        listing = '*Dijual Apartemen 3 BR di Waterplace (Tower E lantai 9)*\n• Hak Milik Strata Title\n• 3 KT + 2 KM\n• *870 juta nego*'
        self.assertIsNone(match(self.BUYER, listing))
        self.assertIsNotNone(match(self.BUYER, listing.replace('Tower E', 'Tower B')))


class CityCentre(unittest.TestCase):
    BUYER = 'Buyer Request\n\nYang punya rumah di pusat kota Surabaya untuk kitchen bakery.\nBudget rp. 10M\nLuki 081200000017'

    def test_pusat_kota_surabaya_is_the_surabaya_pusat_region(self):
        buyer = row(self.BUYER)
        self.assertEqual(buyer['locations'], ['surabaya pusat'])
        for listing in ('DIJUAL RUMAH ROYAL RESIDENCE 3 LANTAI\nLT 180 m²\nSHM\nRp 6,9 M',
                        'DIJUAL RUMAH MODERN DI KARAH JAMBANGAN SURABAYA\nLt 311m²\nSHM\nHarga 7,5 M'):
            self.assertIsNone(match(buyer, listing), listing)
        inside = match(buyer, 'Dijual rumah Tunjungan\nLT 300 LB 400\nSHM\nHarga 9 M')
        self.assertEqual(inside['status'], 'custom')  # v4.8: too few buyer details is unclear (Custom)
        self.assertNotIn('Needs check: city-centre location not confirmed', inside['explanation'])
        self.assertEqual(row('Renter request\nRuko tengah Kota Surabaya\nBudget 200 jt/th')['locations'], ['surabaya pusat'])

    def test_a_bare_city_centre_stays_a_check(self):
        result = match('Buyer Request\nRumah di tengah kota\nBudget 10M', 'Dijual rumah Tunjungan\nLT 300\nHarga 9 M')
        self.assertIn('Needs check: city-centre location not confirmed', checks(result))
        # A listing's "tengah kota Surabaya" is a sales pitch, not its region.
        self.assertNotIn('surabaya pusat', row('Dijual rumah Nginden Intan\nLokasi strategis di tengah kota Surabaya\n'
                                                'LT 144\nHarga 1,9 M')['locations'])


class WarehouseComplex(unittest.TestCase):
    BUYER = ('URGENT RENTER REQUEST\n1. Gudang Suri Mulia untuk Produk FMCG\n2. LB : 800-1.200 m² (ada kantor)\n'
             '3. Rencana Sewa 2 tahun\n4. Budget : 250 - 400 jt/thn\nMichael 081200000018')

    def test_suri_mulia_is_in_margomulyo(self):
        buyer = row(self.BUYER)
        self.assertEqual(buyer['locations'], ['suri mulia'])
        gresik = ('Disewakan Gudang & Pabrik Strategis di Jalan Raya Cangkir, Driyorejo, Gresik.\n'
                  'LT 3.575 m² | Bangunan ±1.000 m²\nHarga 300 juta pertahun')
        self.assertIsNone(match(buyer, gresik))
        self.assertIsNotNone(match(buyer, 'Disewakan gudang Suri Mulia Margomulyo\nLT 1300 m²\nLB 1000 m²\nHarga sewa 300 jt/th'))
        # A buyer who names Surabaya next to an unknown complex never gets a Gresik warehouse.
        self.assertIsNone(match(self.BUYER.replace('Suri Mulia', 'Sumber Makmur Surabaya'), gresik))


class LeaseTerm(unittest.TestCase):
    BUYER = 'RENTER *B* REQUEST\nAPARTMENT SURABAYA BARAT 2BR\nSEWA 3 BULAN\nBUDGET IKUT'

    def test_reading(self):
        for text, months in (('sewa 3 bulan', 3), ('rencana sewa 2 tahun', 24), ('sewa 1-2 tahun', 24), ('sewa 9bln', 9),
                             ('sewa min 3 tahun', 36), ('sewa 50 jt/tahun', None), ('budget sewa 30 jt per bulan', None)):
            self.assertEqual(lease_need(text), months, text)
        for text, offer in (('hrg sewa 85 jt / thn\nminim 2 thn', (24, False)), ('(minim sewa 1 thn)', (12, False)),
                            ('harga sewa 40 jt/tahun - (min. 1 tahun)', (12, False)), ('minimal sewa 2 tahun', (24, False)),
                            ('sewa min 1 th', (12, False)), ('5 jt/bln, bisa bulanan', (None, True)), ('min 2 kt', (None, False))):
            self.assertEqual(lease_offer(text), offer, text)

    def test_a_short_lease_never_matches_a_minimum_of_years(self):
        for minimum in ('Minim 2 thn', '(Minim sewa 1 thn)', 'Harga Sewa 40 jt/tahun - (min. 1 tahun)'):
            listing = f'Disewakan\nWaterplace Apartemen\nTower B lantai 16\n2KT 2KM\nHrg sewa 85 jt / thn\n{minimum}'
            self.assertIsNone(match(self.BUYER, listing), minimum)
        unstated = match(self.BUYER, 'Disewakan Waterplace Apartemen\nTower B\n2KT\nHrg sewa 85 jt / thn')
        self.assertEqual(unstated['status'], 'custom')  # v4.8: an unstated minimum lease is unclear
        self.assertIn('Needs check: minimum lease not stated (renter wants 3 months)', unstated['explanation'])
        monthly = match(self.BUYER, 'Disewakan Waterplace Apartemen\nTower B\n2KT\nHarga sewa 8 jt / bulan')
        self.assertFalse(any('lease' in line for line in monthly['explanation']))

    def test_lease_of_years(self):
        buyer = 'Renter request\nCari sewa rumah Citraland\nSewa 1th\nBudget 70jt/th'
        self.assertIsNone(match(buyer, 'Disewakan rumah Citraland\nLT 120\nHarga 65 jt/th\nmin 2 tahun'))
        fits = match(buyer.replace('Sewa 1th', 'Sewa 2 thn'), 'Disewakan rumah Citraland\nLT 120\nHarga 65 jt/th\nMinimal sewa 2 tahun')
        self.assertIn('Lease term fits (minimum 2 years)', fits['explanation'])
        # A lease of a year or more needs no stated minimum.
        self.assertFalse(any('lease' in line for line in match(buyer, 'Disewakan rumah Citraland\nLT 120\nHarga 65 jt/th')['explanation']))


class Width(unittest.TestCase):
    BUYER = 'Renter Request\n\ntanah / rumah\nlebar di atas 10\nTenggilis / Ngagel\nbuat cafe\nBudget : sementara mengikuti'

    def test_minimum_width_words(self):
        listing = 'DIJUAL/DISEWAKAN RUMAH KUTISARI 1 LANTAI\nLT 176 ; 8x22\nLB 60\nHARGA SEWA 47,5jt/th NEGO'
        for buyer in (self.BUYER, self.BUYER.replace('di atas 10', '> 10 m'), self.BUYER.replace('di atas 10', '≥ 10'),
                      self.BUYER.replace('di atas 10', 'min 10')):
            self.assertIsNone(match(buyer, listing), buyer)
        self.assertIn('Width matches', match(self.BUYER, listing.replace('8x22', '12x22'))['explanation'])

    def test_frontage_width(self):
        buyer = 'Renter request\nRuko surabaya barat\nLebar depan min 8\nBudget up to 300jt/th'
        self.assertIsNone(match(buyer, 'Disewakan ruko Tandes Surabaya Barat\nLT 60 (4x15)\nHarga 280 jt/th'))
        self.assertIn('Width matches', match(buyer, 'Disewakan ruko Tandes Surabaya Barat\nLT 200\nLebar depan 10 meter\n'
                                                    'Harga 280 jt/th')['explanation'])


class Facing(unittest.TestCase):
    KENNY = ('*BUYER REQUEST* ‼️\n- Rumah Siap Huni Area Pakuwon Indah / VBR\n- \u2060*HARUS HADAP BARAT / UTARA* '
             '(Hadap Barat lebih baik)\n- \u2060Luas di 200 - 300m2\n- \u2060Budget 5-7 M\nKenny 081200000019')

    def test_zero_width_characters_and_wishes(self):
        self.assertEqual(clean_text('a\u2060*b*\u200bc\u00add'), 'a *b* cd')
        buyer = row(self.KENNY)
        self.assertEqual(set(buyer['facing']), {'barat', 'utara'})
        self.assertIsNone(match(buyer, 'DIJUAL RUMAH PAKUWON INDAH\nLT 210 m²\nHadap selatan\nSHM\nHarga 6,5M'))
        self.assertIn('Facing matches', match(buyer, 'DIJUAL RUMAH PAKUWON INDAH\nLT 210 m²\nHadap Utara\nSHM\nHarga 6,5M')['explanation'])
        self.assertEqual(facing_directions('prefer hadap selatan'), [])
        # Message hashes keep their old normalisation, so re-uploads stay duplicates.
        old = 'x\u2066y'.lower()
        self.assertEqual(message_hash('c', 't', 'a', 'X\u2066Y'), hashlib.sha256(f'c|t|a|{old}'.encode()).hexdigest())

    def test_listing_facing_forms(self):
        self.assertEqual(row('Dijual rumah The mansion Pakuwon Indah\nLT 190m (10x19) • LB 300m\nUtara, ppjb\n4,99M')['facing'], ['utara'])
        self.assertEqual(row('Dijual rumah Pakuwon Indah\nLT 190\nHadap: U\nHarga 4,99M')['facing'], ['utara'])
        self.assertEqual(row('Dijual rumah Araya\nLT 411\nBarat - Utara\nHarga 10 M')['facing'], ['utara', 'barat'])
        buyer = '*BUYER REQUEST*\n*Dicari Rumah Pakuwon Indah*\n- LT 200m²\n- Hadap harus Barat\n*Budget 5m*'
        self.assertIsNone(match(buyer, '*Dijual rumah The mansion Pakuwon Indah*\nLT 190m (10x19) • LB 300m\nUtara, ppjb\n4,99M'))

    def test_a_refused_direction_is_no_wanted_one(self):
        for line in ('Tidak mau hadap barat', 'Tdk mau hdp barat', '❌️Hadap Barat❌️'):
            buyer = row(f'Buyer Request\nRumah Citraland\nLT min 100\n{line}\nBudget 2,5M')
            self.assertEqual(buyer['facing'], [], line)
            self.assertIsNone(match(buyer, 'Dijual rumah Citraland\nLT 120\nHadap Barat\nHarga 2 M'), line)
            self.assertIsNotNone(match(buyer, 'Dijual rumah Citraland\nLT 120\nHadap Utara\nHarga 2 M'), line)


if __name__ == '__main__':
    unittest.main()
