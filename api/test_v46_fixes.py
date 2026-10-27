"""v4.6: fixes for the independent judge's findings on real data (v4.5 review): fractions ("1½ lantai") are decimals, a sale
pitch ("good investment, mudah disewakan") is no rent offer, "pojok" / "sudut" / "corner" are a hook, a development the
listing names wins over a cluster alias elsewhere ("Citra Harmoni / Cluster Stamford"), and places that left a proven-
different location Custom (Jl. Lombok, Jl. Teuku Umar, Villa Bukit Mas, The Loop, Kepatihan). Texts follow the judged
pairs (contact numbers are placeholders)."""
import unittest

from location_index import LocationIndex, parse_table
from locations import default_places
from matching_rules import _count, _required_locations, prepare_document
from parser import clean_text, corner_lot, parse_message
from test_location_index import table
from test_v43_terms import checks, match, row


class Fractions(unittest.TestCase):
    """WARM 2: "LB 300m² (1½ lantai)" is 1,5 storeys (NFKC alone wrote "11⁄2", read as 2)."""
    KERTAJAYA = ('*Dijual Rumah Kertajaya Indah*\n_200 meter dari Raya Dharmahusada_\nLT 300m² (12x25)\nLB 300m² (1½ lantai)\n'
                 'KT 4+1+1 // KM 3+1\nSHM on hand\nHadap Selatan\n*Harga 4.95M nego*\nNike 081200000201')
    BUYER = ('🔥Request buyer🔥\nBeli rumah 2lt siap huni\nLokasi sby timur ( dharma husada, kertajaya, manyar dsk )\nLT min 350\n'
             'Hadap timur selatan\nPayment cash\nHarga 5-7 M\nPriska 081200000202')

    def test_reading(self):
        for text, cleaned in (('LB 300m² (1½ lantai)', 'LB 300m2 (1,5 lantai)'), ('1 ½ Lantai', '1,5 Lantai'),
                              ('2¼ LANTAI', '2,25 LANTAI'), ('Harga 1¾ M', 'Harga 1,75 M'), ('AC ½ PK', 'AC 0,5 PK'),
                              ('LT 204 m² ( 13½ × 15 )', 'LT 204 m2 ( 13,5 × 15 )'), ('bangunan 11⁄2 lantai', 'bangunan 1,5 lantai'),
                              ('1 1⁄2 lantai', '1,5 lantai'), ('3⁄4 M', '0,75 M'), ('DIJUAL RUKO 3 1/2 lt', 'DIJUAL RUKO 3,5 lt'),
                              ('Rumah 1,1/2 lantai', 'Rumah 1,5 lantai'), ('LT/LB 1/2', 'LT/LB 1/2')):
            self.assertEqual(clean_text(text), cleaned, text)

    def test_numbers_everywhere(self):
        self.assertEqual(_count(clean_text('lb 300m² (1½ lantai)').lower(), 'floors'), (1.5, 1.5))
        self.assertEqual(row('Dijual tanah Wiyung\nLT 200\nHarga 1½ M')['price_min'], 1_500_000_000)
        self.assertEqual(row('Dijual tanah Wiyung\nLuas tanah 100½ m²\nHarga 2 M')['land_area_min'], 100.5)

    def test_the_judged_pair(self):
        self.assertIsNone(match(self.BUYER, self.KERTAJAYA))
        either = match(self.BUYER.replace('Beli rumah 2lt', 'Beli rumah 1 atau 2 lantai').replace('LT min 350', 'LT min 300'),
                       self.KERTAJAYA)
        self.assertIn('Floors match', either['explanation'])

    def test_storey_conditions_and_alternatives(self):
        for text, storeys in (('1 atau 2 lantai cukup', (1.0, 2.0)), ('2 lantai atau 1 lantai tapi lega', (1.0, 2.0)),
                              ('lebar min 6 jika 2 lantai & lebar 8 jika 1 lantai', None),
                              ('- lebar 6 - 2 lantai\n- lebar 8 - 1 lantai', None), ('harus 2 lantai', (2.0, 2.0))):
            self.assertEqual(_count(text, 'floors'), storeys, text)


class InvestmentPitch(unittest.TestCase):
    """WARM 13: a condo for sale "good investment, mudah disewakan" is no rent offer."""
    LA_RIZ = ('Condominium La Riz Mansion mewah\nLantai 6\nPrivate Lift\nLuas: 99 m2\n3 KT, 2+1 KM\nSemi Furnish\n'
              'Barang langka, good investment, mudah disewakan. Banyak diminati expatriat.\nTURUN HARGA\nRp4.5 M jadi 3.5m masih\nNego\n'
              'Info\nJUAL | BELI | SEWA |  KPR.PROPERTY\nAlbert 081200000203')
    RENTER = ('Hot Renter Request\nDicari sewa *Apartemen Benson ,Anderson, La riz,La viz*\n2 or 3 BR\nLuas min.79m²\n'
              'Sewa 1thn, segera\nBudget 100jt\nLiely 081200000204')

    def test_the_judged_pair(self):
        self.assertEqual(row(self.LA_RIZ)['transaction_type'], 'sale')
        self.assertIsNone(match(self.RENTER, self.LA_RIZ))

    def test_pitches_and_offers(self):
        for text, deal in (('Dijual ruko Darmo\nCocok untuk disewakan\nHarga 5 M', 'sale'),
                           ('Ruko Darmo, potensi sewa 150 jt/th\nHarga 5 M', 'sale'),
                           ('Apartemen Educity, gampang disewakan\nHarga 500 jt', 'sale'),
                           ('Disewakan apartemen Educity, mudah akses tol\nHarga 30 jt/th', 'rent'),
                           ('Dijual / Disewakan ruko Darmo\nHarga 5 M\nSewa 150 jt/th', 'both')):
            self.assertEqual(row(text)['transaction_type'], deal, text)
        # An investor buyer who wants something easy to rent out still buys.
        self.assertEqual(row('Buyer request\nCari apartemen yang mudah disewakan\nBudget 1 M')['transaction_type'], 'sale')


class CornerLots(unittest.TestCase):
    """WARM 20: "❌TIDAK MAU … Hook" refuses "Rumah Pojok"."""
    BUYER = ('‼️BUYER REQUEST ( S ) ‼️\n1. Rumah All Surabaya Barat ( Diutamakan Greenland )\n'
             '2. ❌TIDAK MAU Kampung, Hadap Timur, TS, Hook, Gang Buntu ❌\n3. Kamar Tidur : 3+1\n4. Budget Under 1 M\n'
             'Michael 081200000205')
    POJOK = ('Dijual segera\nRumah Pojok ( Surabaya Barat)\nLuas 146m2\n3 kmr tidur\n3 kmr mandi\nDekat Pasar Benowo\nHarga 700jt\n'
             'Tien 081200000206')

    def test_the_judged_pair(self):
        self.assertIsNone(match(self.BUYER, self.POJOK))
        for corner in ('Rumah posisi sudut', 'Rumah Hoek', 'Rumah corner', 'Rumah pojokan'):
            self.assertIsNone(match(self.BUYER, self.POJOK.replace('Rumah Pojok', corner)), corner)
        for plain in ('Rumah bukan hook', 'Rumah tidak pojok', 'Rumah sudut pandang luas'):
            self.assertIsNotNone(match(self.BUYER, self.POJOK.replace('Rumah Pojok', plain)), plain)
        # A refused hook is never a requirement ("corner lot not confirmed").
        self.assertNotIn('hook', row(self.BUYER)['requirements'])
        self.assertNotIn('Needs check: corner lot not confirmed', match(self.BUYER, self.POJOK.replace('Rumah Pojok', 'Rumah'))['explanation'])

    def test_refusing_by_another_name(self):
        buyer = 'Buyer request\nCari rumah Wiyung\nTidak mau rumah pojok\nBudget 2 M'
        self.assertIsNone(match(buyer, 'Dijual rumah Wiyung (Hook)\nLT 120\nHarga 1,8 M'))
        self.assertIsNotNone(match(buyer, 'Dijual rumah Wiyung\nLT 120\nHarga 1,8 M'))

    def test_requiring_a_corner(self):
        buyer = 'Buyer request\nCari rumah pojok Wiyung\nBudget 2 M'
        self.assertEqual(row(buyer)['requirements'], ['hook'])
        for listing in ('Dijual rumah Wiyung, posisi hook\nLT 120\nHarga 1,8 M', 'Dijual rumah pojok Wiyung\nLT 120\nHarga 1,8 M',
                        'Dijual rumah Wiyung\nPosisi : sudut\nLT 120\nHarga 1,8 M'):
            self.assertNotIn('Needs check: corner lot not confirmed', match(buyer, listing)['explanation'], listing)
        self.assertIn('Needs check: corner lot not confirmed', match(buyer, 'Dijual rumah Wiyung\nLT 120\nHarga 1,8 M')['explanation'])
        # "Unit hook boleh", "hook ga masalah": a corner is allowed, not required.
        for allowed in ('- UNIT HOOK BOLEH (DIUTAMAKAN GE1-16A)', 'hadap bebas, hook ga masalah', 'boleh pojok'):
            self.assertFalse(corner_lot(allowed.lower(), True), allowed)
        self.assertFalse(corner_lot('sudut pandang dari jalan raya'))
        self.assertTrue(corner_lot('tipe studio corner view city'))


class NamedDevelopmentWins(unittest.TestCase):
    """WARM 33: "*Citra Harmoni* / Cluster Stamford" is in Citra Harmoni (Taman, Sidoarjo), not Stamford Place CitraLand."""
    LISTING = ('*Rumah Dijual Cepat*\nSDH dapat PENYEWA !!\nINVESTASI menguntungkan !!\n*Citra Harmoni*\nCluster Stamford\nLt 90 m²\n'
               'Lb 75 m²\nKT 2\nKM 1\nHarga Rp 750juta nego.\nSHM Onhand.\nJohanes 081200000207')
    INDEX = LocationIndex(parse_table(table([[1, 'Stamford Place CitraLand', 'stamford', 'CitraLand', ''],
                                             [2, 'Riverside (Citra Harmoni)', '', 'Citra Harmoni', ''],
                                             [3, 'Cluster New York (Citra Harmoni)', '', 'Citra Harmoni', '']])))

    def test_the_judged_pair(self):
        listing = row(self.LISTING)
        self.assertEqual(listing['locations'], ['citra harmoni'])
        self.assertIsNone(match('*BUYER*\nCARI RUMAH DI SURABAYA BARAT\nPOKOK BUJET UNDER 1M\nEma 081200000208', self.LISTING))
        self.assertIsNone(match('*BUYER*\nCARI RUMAH DI SURABAYA BARAT\nPOKOK BUJET UNDER 1M', self.LISTING, index=self.INDEX))
        prepare_document(listing, self.INDEX)
        self.assertEqual(sorted(listing['_geo_ids']), ['cluster new york citra harmoni', 'riverside citra harmoni'])
        harmoni = match('Buyer request\nCari rumah Citra Harmoni\nBudget 1 M', self.LISTING, index=self.INDEX)
        self.assertIn('Same area: Citra Harmoni', harmoni['explanation'])

    def test_a_cluster_of_the_named_development_stays(self):
        listing = row('Dijual rumah Citraland cluster Stamford\nLT 90\nHarga 1 M')
        self.assertEqual(listing['locations'], ['citraland', 'stamford'])
        prepare_document(listing, self.INDEX)
        self.assertEqual(listing['_geo_ids'], ['stamford place citraland'])
        self.assertEqual(row('Dijual rumah Stamford\nLT 90\nHarga 1 M')['locations'], ['stamford'])


class PlaceData(unittest.TestCase):
    """CUSTOM 3 / 5 / 12 / 13 / 18: places that left a proven-different location Custom."""

    def setUp(self):
        self.places = default_places()

    def test_hierarchy(self):
        for place, chain in (('jl lombok', ('ngagel', 'wonokromo', 'surabaya selatan', 'surabaya')),
                             ('teuku umar', ('dr sutomo', 'tegalsari', 'surabaya pusat', 'surabaya')),
                             ('villa bukit mas', ('dukuh pakis', 'surabaya barat', 'surabaya')),
                             ('the loop', ('graha famili', 'surabaya barat', 'surabaya')),
                             ('kepatihan', ('menganti', 'gresik'))):
            self.assertEqual(self.places.ancestors(place), chain, place)
        for alias in ('loop graha famili', 'loop graha family', 'loop'):
            self.assertEqual(self.places.canonical(alias), 'the loop', alias)
        self.assertEqual(self.places.canonical('jakpus'), 'jakarta pusat')

    def test_recognition(self):
        for text, places in (('LANGKA !! TANAH PUSAT KOTA SURABAYA LOKASI STRATEGIS JALAN LOMBOK', ['surabaya', 'jl lombok']),
                             ('📍 Jl. Lombok & Jl. Flores, Surabaya', ['jl lombok', 'surabaya']),
                             ('💉 *TEUKU UMAR* 🩺', ['teuku umar']), ('Gudang Kepatihan dan sekitarnya', ['kepatihan']),
                             ('Ruko wajib loop graha famili', ['the loop']), ('Gili Gede, Lombok', []),
                             ('Selangkah ke loop dan clubhouse', [])):
            self.assertEqual(parse_message(f'Dijual {text}\nLT 100\nHarga 5 M').locations, places, text)
        # Streets other towns have too: Jl. Lombok needs Surabaya named; Teuku Umar is dropped next to another town.
        self.assertEqual(parse_message('Dijual ruko Jl. Lombok\nLT 100\nHarga 5 M').locations, [])
        for text in ('Dijual rumah Jl Teuku Umar Menteng jakpus\nLT 100\nHarga 9 M',
                     'Dijual villa Jl. Teuku Umar, Denpasar\nLT 300\nHarga 9 M'):
            self.assertNotIn('teuku umar', parse_message(text).locations, text)
        strict = row('*Renter Request*\n1. Sewa ruko Surabaya barat Loop only\n2. Budget 100 jt/th')
        self.assertEqual(strict['strict_locations'], ['the loop'])

    def test_the_judged_pairs(self):
        lombok = ('*FOR SALE!!*\n*LANGKA !! TANAH PUSAT KOTA SURABAYA LOKASI STRATEGIS JALAN LOMBOK*\nDimensi 40x60\nLt 2442m2\n'
                  'Hadap Utara- Timur (Hook)\nSHM\n*Harga 22.5Jt/m2 Nego*\nNela 081200000209')
        self.assertIsNone(match('Buyer request\nCari tanah Surabaya Barat\nBudget 60 M', lombok))
        self.assertIn('Inside Surabaya Selatan: Jl Lombok', match('Buyer request\nCari tanah Surabaya Selatan\nBudget 60 M',
                                                                  lombok)['explanation'])
        umar = ('_Dijual 🏠 lama tengah kota :_\n💉 *TEUKU UMAR* 🩺\nLT. 225m LB. 330m\n2 Lantai, Hadap Barat Daya\nKT. 4 KM. 4\n'
                '*SHM, Harga 5.15 M nego*‼️\nMeeFang 081200000210')
        self.assertIsNone(match('*BUYER REQUEST*\n_RUMAH MODERN / CLASSIC AREA GRAHA & PAKUWON INDAH_\nLUAS MIN 200m²\nBUDGET 7M', umar))
        loop = ('*Renter Request*\n1. Sewa ruko Surabaya barat Loop only\n2. Peruntukan resto\n3. Budget mengikuti\n'
                'Sentosa 081200000211')
        self.assertIsNone(match(loop, 'Disewakan Ruko 3 Lantai Citraland\nLT 5x17\nLB 234m²\nSHM\n250jt/thn Nego'))
        self.assertIsNone(match(loop, 'Disewakan ruko Sukomanunggal LT 100 Harga 90 jt/th'))
        inside = match(loop, 'Disewakan ruko Loop Graha Famili LT 100 Harga 90 jt/th')
        self.assertEqual(inside['status'], 'custom')  # v4.8: "peruntukan resto" is unverified (was Warm)
        self.assertEqual(checks(inside)[0], 'Needs check: suitability for the intended use')
        kepatihan = '*BUYER REQUEST*\n1. Gudang Kepatihan dan sekitarnya\n2. Luas tanah 300-500m2\n3. Budget mengikuti'
        self.assertIsNone(match(kepatihan, 'Gudang dijual murah\n*Krembangan*\nSurabaya Utara\nLT 295m² (8x36)\nSHM\n'
                                           '*Harga jual : 1,7M nego*'))
        self.assertIsNotNone(match(kepatihan, 'Dijual gudang Raya Kepatihan, Menganti Gresik\nLT 400\nHarga 4 M'))


class RegionAfterAPlace(unittest.TestCase):
    """CUSTOM 12: "Beli rumah villa bukit mas, Surabaya barat" wants Villa Bukit Mas; "Surabaya barat" says where it is."""
    BUYER = '*Buyer  Request*\nBeli rumah villa bukit mas, Surabaya barat\nSiap huni\nBudget under 3 M\nEddy 081200000212'
    CITRALAND = ('*Dijual*\n*Rumah di Citraland Utara*\n*Northwest Lake*\nLT 126 m² (7x18)\nLB 205 m² (2 lantai)\nKT 4 +1\n'
                 'Sertifikat PPJB\n*Harga  2,75 M Nego*\nFelix 081200000213')

    def test_the_judged_pair(self):
        self.assertEqual(_required_locations(row(self.BUYER), default_places()), ['villa bukit mas'])
        self.assertIsNone(match(self.BUYER, self.CITRALAND))
        self.assertIsNone(match(self.BUYER.replace(', Surabaya barat', ' Surabaya'), self.CITRALAND))
        self.assertIsNotNone(match(self.BUYER, 'Dijual rumah Villa Bukit Mas\nLT 200\nHarga 2,9 M'))
        # A listing that only names the region leaves the place unconfirmed, as before.
        self.assertEqual(match(self.BUYER, 'Dijual rumah Surabaya Barat\nLT 200\nHarga 2,9 M')['status'], 'custom')

    def test_alternatives_and_lists_keep_the_region(self):
        places = default_places()
        for text in ('Buyer request\nBeli rumah villa bukit mas atau Surabaya barat\nBudget 3 M',
                     'Buyer request\nRumah sekitar villa bukit mas, Surabaya barat\nBudget 3 M',
                     'Renter request\n- area mojokerto, Krian, sidoarjo, gresik, Trosobo\nBudget 1 M/th',
                     'Buyer request\nLokasi: Citraland, Pakuwon Indah, Surabaya Barat\nBudget 3 M'):
            buyer = row(text)
            self.assertEqual(_required_locations(buyer, places), buyer['locations'], text)
        self.assertEqual(match('Buyer request\nBeli rumah villa bukit mas atau Surabaya barat\nBudget 3 M', self.CITRALAND)['status'],
                         'custom')


if __name__ == '__main__':
    unittest.main()


class WordBoundaryFlags(unittest.TestCase):
    def test_baruk_is_not_rumah_baru(self):
        from matching_rules import _request_features
        def flags(text):
            row = {'raw_text': text, 'normalized_text': text.lower(), 'document_type': 'buyer_request'}
            return ' '.join(label for label, _ in _request_features(row)['flags'])
        self.assertNotIn('building condition', flags('Cari rumah Baruk budget 2M'))
        self.assertEqual(_request_features({'raw_text': 'Cari rumah baru di Manyar budget 2M', 'normalized_text': 'cari rumah baru di manyar budget 2m',
                                            'document_type': 'buyer_request'})['new_house'], 'strict')  # v4.7: a condition rule
        self.assertNotIn('intended use', flags('Cari rumah untuk pengusaha budget 2M'))
