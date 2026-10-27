"""v4.8: HOT needs a score of at least 90 and every requirement proven; WARM only differs in price and/or land/building size
inside the Warm allowance; anything unclear is CUSTOM. The A–F texts are the user's reported live pairs (buyers posted 17–18
Oct, Hot only); contact numbers are placeholders."""
import unittest

from locations import default_places, expand_place_suffixes
from matching_rules import HOT_MIN_SCORE, _furnishing
from test_v43_terms import checks, match, row, rows

try:  # the PDF report needs reportlab (installed in the API image)
    from report import indonesian
except ImportError:
    indonesian = None

A_BUYER = ('*Buyer Request*\nRumah hitung tanah daerah Manyar2an Surabaya, budget 1,5 M\nNgga mau TS, ngga mau hadap barat\n'
           'Andi 081200000801')
A_MULYOSARI = ('Di jual : Rumah Mulyosari NEW GRESS MINIMALIS STRATEGIS\nLT : 5x17 (85) / LB : 140\nKT 3+1 KM 3\nSHM\nHadap Utara\n'
               'Harga 1,45 M nego\nBudi 081200000802')
A_ROYAL = ('*Royal Residence (JUAL & SEWA)*\n\n>>>Royal Residence Oakwood\nRumah Minimalis. Depan Danau\nLT 4x10=40 | LB 60 | 2 lantai\n'
           'KT 2 | KM 2\nSHM | Hadap Selatan\n1.2M nego\n\n*Junaidy - KONIG Property*\n0878-0000-0803')
A_SARONOJIWO = (
    'Dijual 5 rumah Saronojiwo Estate sedang progress pembangunan di Surabaya Selatan.\n\nSertipikat : SHM\n'
    'Lokasi Perumahan sangat strategis :\n-Sangat dekat dengan Raya Jemursari (+/-350 meter)\n'
    '-Dekat dengan Universitas UBAYA (+/- 1 km)\n-Dekat ke bandara (15 km)\n\nAda 5 tipe :\n'
    'Kavling A\nLT : 6x13 = 79 m2\nLB : 153 m2 (2 lt)\nKamar Tidur : 4\nKamar Mandi : 3\nHadap : Barat\nHarga :\nRp.1,85M\n\n'
    'Kavling B (tipe pojok)\nLT : 9x1,67 = 126 m2\nLB : 232 m2 (2 lt)\nKamar Tidur : 3+1\nKamar Mandi : 3+1\nHadap : Barat-Selatan\n'
    'Harga : Rp.2,25M\n\nKavling C\nLT : 5,75x12,39 = 61 m2\nLB : 118 m2 (2 lt)\nKamar Tidur : 3\nKamar Mandi : 3\nHadap : Selatan\n'
    'Harga : Rp.1,495M\n\nKavling D\nLT : 5,75x14,95 = 76 m2\nLB : 146 m2 (2 lt)\nKamar Tidur : 4\nKamar Mandi : 3\n'
    'Hadap : Selatan\nHarga : Rp.1,75M\n\nKavling E\nLT : 5,85x11,5 = 57 m2\nLB : 112 m2 (2 lt)\nKamar Tidur : 3\nKamar Mandi : 3\n'
    'Hadap : Selatan\nHarga : Rp.1,5M\n\n*Chat WA :*\nJasmin\nKonig Property\n081200000804')
B_BUYER = ('Dicari rumah beli 1 lantai (wajib) kamar 2 atau 3 daerah sby timur karang empat karang asem dsk  budget 800-900jt \n\n\n'
           'Evita 123 property\n081200000805')
B_MEDAYU = ('Dijual Rumah Surabaya Selatan Siap Huni\n\n\nDaerah Medayu Utara Rungkut Surabaya\nDekat Wonorejo Rungkut\nDekat Pandugo\n'
            'Dekat Merr \nNo Blusuk Blusuk\n\n\nLT.  100 ( 5 x 20 )\nLB.    75\nKamar Tidur 2\nKamar Mandi  1\nFull Bangunan\n'
            'Hadap Utara\nLegalitas SHM\nListrik. 2200 + PDAM\nTandon Bawah\nAda Carport Mobil\nROW 6 meter\nHarga 850 Juta Nego\n\n\n'
            'Hub :\nLulu Tan \nKONIG PROPERTY \n081200000806')
C_BUYER = 'Buyer req\n\nApartemen puncak tidar\nHanya tower a dan B\n2br furblnish\nBudget under  275jt\n\nHUBUNGI:\nYONGKY 081200000807'
C_GUNAWANGSA = ('*Dijual Apartemen Gunawangsa Tidar*\n\nTower A Lantai 28\nTipe 2 BR+\nLuas 40.63m²\nHadap Timur\nView Pool dan City\n'
                'PPJB\nListrik 1300 watt\nKondisi kosongan \nHarga sdh termasuk :\n*2 unit AC\n*Kulkas\n*Mesin cuci\n*TV\n*Springbed\n'
                'Ada Gym dan playground anak. \n\n*Harga 260 JUTA NEGO TIPIS*\n\nBEE LING-BRM\n081200000808')
D_BUYER = ('*Buyer Request*\nDicari rumah daerah *Rungkut* \nKT : 3\nKM :2 \nRow Jalan : 2 mobil\nPrioritas rumah baru\n\n'
           '*Budget 1,6 M - 1,8 M*\n\n\nHariadi Gading Pro\n081200000809')
D_NIRWANA = ('*DIJUAL Rumah Second FULL RENOV*\n*Nirwana Eksekutif , Rungkut* 🏡\n\nLT 160 m² (8x20)\nLB +/- 120 m²\n1 lantai\n3 KT+1\n'
             '2 KM +1 \nKT Utama - KM dalam\nPLN 2200 - Non token\nPDAM + Pompa baru\nRow jalan 3 mobil\nCarport 1 mobil\n'
             'Hadap Selatan\n\nIPL 130rb / Bulan\n\n*Harga 1.7 M Nego*\n\n\n\n*Ivan Prayogo*\n*Konig Property*\n081200000810')
E_RENTER = ('*Renter Rumah*\n\nWajib minimalist \nFurnished \n\nArea prefer CITRALAND \nGraha - Pakuwon boleh ditawarkan \n\n'
            'Budget 350 jt/tahun \n\nHubungi:\n*SYLVANA*\nGALAXY CITRALAND \n081200000811')
E_PALM_HILL = ('Disewakan rumah di Palm Hill Citraland\n\n\nLuas tanah 14.5x28.5 (413)\nLb 2lantai\nListrik 6600\nKT 4+1\nKM 4+1\n'
               'Service area terpisah\nFurnish\nGarasi 2mobil\nCarport 2mobil\nBarat\nRow jalan kembar\nHarga 350juta/setahun\n\n\n\n'
               'Ellen KONIG Property\n081200000812')
F_RENTER = ('*_Renter request_*\n\nRumah Royal Residence/Wisata Bukit Mas \n\nBudget 40-60 juta pertahun \n\nCari 3 kamar ( WAJIB )\n\n'
            'Masuk bulan depan maximal \n\n\nInfo please : Ocean BRIGHTON Graha Famili 081200000813')
F_ROYAL = ('*Royal Residence (JUAL & SEWA)*\n\n>>>Royal Residence Siap Huni. AC tiap kamar + Korden\nDekat danau (Canal Park)\n'
           'LT 8x16=128 | LB 125 | 2 lantai\nKT 3+1 | KM 3+1\nSHM\nHadap Selatan\nSewa 55jt/th Nego\n\n*Junaidy - KONIG Property*\n'
           '0878-0000-0814')


class StatusPolicy(unittest.TestCase):
    BUYER = 'Cari beli rumah Manyar Kertoarjo\nLT 200-300\nKT 4\nSHM\nbudget 3-3,5 M\nAndi 081200000815'
    LISTING = 'Dijual Rumah Manyar Kertoarjo\nLT 250 LB 200\nKT 4\nSHM\nHadap timur\nHarga {}\nBudi 081200000816'

    def test_hot_needs_ninety_and_no_check(self):
        self.assertEqual(HOT_MIN_SCORE, 90)
        hot = match(self.BUYER, self.LISTING.format('3,2 M nego'))
        self.assertEqual(hot['status'], 'hot', hot['explanation'])
        self.assertGreaterEqual(hot['score'], 90)
        self.assertEqual(checks(hot), [])

    def test_price_or_size_deviation_alone_is_warm(self):
        price = match(self.BUYER, self.LISTING.format('3,8 M'))
        self.assertEqual(price['status'], 'warm', price['explanation'])
        self.assertLessEqual(price['score'], 79)
        self.assertEqual(checks(price), ['Needs check: price Rp 3,8 M is 9% above the maximum budget Rp 3,5 M (within tolerance)'])
        size = match(self.BUYER, self.LISTING.replace('LT 250', 'LT 180').format('3,2 M'))
        self.assertEqual(size['status'], 'warm', size['explanation'])

    def test_anything_unclear_is_custom(self):
        for listing in (self.LISTING.replace('LT 250 LB 200', 'LB 200').format('3,2 M'),        # land area not stated
                        self.LISTING.replace('KT 4\n', '').format('3,2 M'),                     # bedrooms not stated
                        self.LISTING.replace('SHM\n', '').format('3,8 M')):                    # legal status not stated
            result = match(self.BUYER, listing)
            self.assertEqual(result['status'], 'custom', result['explanation'])
            self.assertLessEqual(result['score'], 79)
        vague = match('Cari rumah Manyar budget 3,5 M\nAndi 081200000817', self.LISTING.format('3,2 M'))
        self.assertEqual(vague['status'], 'custom')
        no_budget = match('Cari beli rumah Manyar Kertoarjo\nLT 200-300\nKT 4\nSHM\nAndi 081200000818', self.LISTING.format('3,2 M'))
        self.assertEqual(no_budget['status'], 'custom')
        self.assertIn('Needs check: buyer budget not stated', checks(no_budget))

    def test_marked_requirements(self):
        buyer = self.BUYER.replace('SHM', 'SHM\nNgga mau TS\nWajib one gate system')
        silent = match(buyer, self.LISTING.format('3,2 M'))
        self.assertEqual(silent['status'], 'custom')
        self.assertIn('Needs check: requirement not verified: “ngga mau ts”', checks(silent))
        self.assertIn('Needs check: requirement not verified: “wajib one gate system”', checks(silent))
        proven = match(buyer, self.LISTING.replace('SHM', 'SHM\nBukan TS\nOne gate system').format('3,2 M'))
        self.assertEqual(proven['status'], 'hot', proven['explanation'])
        self.assertIn('Buyer requirement met: “ngga mau ts” ✓', proven['explanation'])
        self.assertIsNone(match(buyer, self.LISTING.replace('SHM', 'SHM\nPosisi TS\nOne gate system').format('3,2 M')))
        # Timing, survey and payment readiness are no requirements; refusing a named property is checked by its name.
        for line in ('Masuk bulan depan maximal', 'Segera survey', 'Cash ready', 'Wajib survey besok', 'Jangan tawarkan ex hotel Mirama'):
            result = match(self.BUYER.replace('SHM', 'SHM\n' + line), self.LISTING.format('3,2 M'))
            self.assertEqual(result['status'], 'hot', (line, result['explanation']))

    def test_wishes_are_never_hot_unless_shown(self):
        buyer = self.BUYER.replace('SHM', 'SHM\nPrefer hadap timur')
        self.assertEqual(match(buyer, self.LISTING.format('3,2 M'))['status'], 'hot')
        west = match(buyer, self.LISTING.replace('Hadap timur', 'Hadap barat').format('3,2 M'))
        self.assertEqual(west['status'], 'custom')
        self.assertIn('Needs check: requirement not verified: “prefer hadap timur”', checks(west))

    @unittest.skipIf(indonesian is None, 'reportlab is not installed')
    def test_indonesian_report_lines(self):
        self.assertEqual(indonesian('Requirement not verified: “wajib minimalist”'), 'Syarat buyer belum terbukti: “wajib minimalist”')
        self.assertEqual(indonesian('Buyer requirement met: “ngga mau ts” ✓'), 'Syarat buyer terpenuhi: “ngga mau ts” ✓')
        self.assertEqual(indonesian('Exact location not confirmed (buyer named “lebo agung”)'),
                         'Lokasi persisnya belum pasti (buyer menyebut “lebo agung”)')
        self.assertEqual(indonesian('Building condition (buyer wants a move-in ready house)'),
                         'Kondisi bangunan (buyer cari rumah siap huni)')


class SampleRefusalsAndParts(unittest.TestCase):
    """Pairs from the 12–18 Oct sample that turned Hot once the blanket "exclusions" item was replaced by per-phrase checks."""

    def test_a_refused_width_is_no_minimum(self):
        buyer = ('*BUYER ADA DUIT NGANGGUR*\nCARI RUMAH DI SURABAYA BARAT TIDAK MAU LEBAR 5 HARGA B.U POKOK BUJET UNDER 1M\n'
                 'Ema 081200000830')
        self.assertIsNone(match(buyer, 'Dijual rumah baru Northwest Lake Surabaya Barat\nLT 55 (5x11) LB 50\nKT 2\nPPJB\nHarga 950jt'))
        wider = match(buyer, 'Dijual Rumah Northwest Park Surabaya Barat\nLT 72 (6x12) LB 36\nKT 2\nHGB\nHarga 925 juta')
        self.assertEqual(wider['status'], 'custom')   # "harga B.U": a below-market price needs a comparison
        self.assertIn('Width matches', wider['explanation'])
        self.assertIn('Needs check: below-market price needs a comparison; "negotiable" is not proof', checks(wider))

    def test_truck_access_and_a_gated_estate_need_the_listing_to_say_so(self):
        buyer = 'Buyer request gudang Waru\nLuas minimal 400 m2\nBudget 4-5 M\nJalan bisa truk ban double\nAndi 081200000831'
        listing = 'Dijual gudang Tropodo Waru\nLT 450 LB 450\nSHM\nHarga 4,3 M\nBudi 081200000832'
        self.assertIn('Needs check: special access or use', checks(match(buyer, listing)))
        self.assertNotIn('Needs check: special access or use', checks(match(buyer, listing.replace('SHM', 'SHM\nAkses container'))))
        estate = buyer.replace('Jalan bisa truk ban double', 'Cari gudang dimana kanan kirinya ada gudang orang lain (pergudangan)')
        self.assertIn('Needs check: warehouse estate (pergudangan) not confirmed', checks(match(estate, listing)))
        self.assertEqual(checks(match(estate, listing.replace('gudang Tropodo', 'gudang Pergudangan Tropodo'))), [])
        renter = 'Renter request\nRumah Surabaya Timur (Wajib Dalam Perumahan)\nKamar min 2\nBudget 70 jt/thn\nSari 081200000833'
        house = 'Disewakan rumah Kutisari Indah\nLT 78 LB 120\nKT 4\nHarga 70 juta/tahun\nRina 081200000834'
        self.assertIn('Needs check: requirement not verified: “wajib dalam perumahan”', checks(match(renter, house)))
        self.assertEqual(checks(match(renter, house.replace('rumah Kutisari', 'rumah perumahan Kutisari'))), [])

    def test_a_kecamatan_after_a_development_only_says_where_it_is(self):
        buyer = 'Buyer Request\n- Rumah Greenlake Citraland Wiyung\n- Luas 120\n- Budget maksimal 2M\nAndi 081200000837'
        self.assertIsNone(match(buyer, 'Dijual rumah Gunungsari Indah Wiyung\nLT 120 (8x15)\nSHM\nHarga 1,9M\nBudi 081200000838'))
        self.assertEqual(match(buyer, 'Dijual rumah Greenlake Citraland\nLT 120\nSHM\nHarga 1,9 M\nBudi 081200000838')['status'], 'hot')

    def test_every_place_after_gak_sampai_is_refused(self):
        buyer = ('Request buyer gudang\nBudget 4-5 M\nLokasi Surabaya atau waru\nLuas minimal 500 m2\n'
                 'Area tertarik Tropodo rungkut brebek mastrip benowo tapi gak sampai osowilangun gak sampai buduran kecuali istimewa\n'
                 'Ririn 081200000839')
        self.assertEqual(row(buyer)['exclusions'], ['buduran', 'osowilangun'])   # "kecuali istimewa" refuses nothing
        self.assertIsNone(match(buyer, 'Dijual gudang\nPergud. Osowilangun Indah, Surabaya\nLT 750 m2\nSHGB\nHarga 5,25 M nego\n'
                                       'Alex 081200000840'))

    def test_deket_is_a_landmark_and_graha_is_graha_famili(self):
        listing = row('Darmo Baru Barat hitung tanah\ndeket raya darmo permai, hr muhammad\nLT 162m2 9x18\nSHM\nHarga 1,4 M\nRonald 081200000841')
        self.assertEqual(listing['locations'], ['darmo baru'])
        buyer = row('Buyer reQuest\nKavling Graha Only OR Pakuwon Indah\nLt max 450m2\nBuget 23.5jt /m2\nListya 081200000842')
        self.assertEqual(buyer['strict_locations'], ['graha famili', 'pakuwon indah'])
        self.assertIsNone(match(buyer, 'HOT LISTING GRAHA PAKUWON CITRALAND\nKavling Citraland cluster Esplanade Park\nLT 400\n'
                                       'Harga 18,5 jt/m2\nBudi 081200000843'))

    def test_conditions_kost_and_lokasi_depan(self):
        buyer = ('Buyer Request\nRumah di Surabaya Barat (darmo2an, citraland, dan sekitarnya)\nKalau bisa rumah baru, kalau rumah lama '
                 'minim renov\nLT 150\nBudget 2M\nCaca 081200000844')
        old = match(buyer, 'Hitung harga tanah bonus rumah\nDijual rumah Citraland Utama\nLT 172\nSemi furnished\nHarga 1,9 M\nMel 081200000845')
        self.assertIn('Needs check: building condition (buyer accepts an old house only if it needs little renovation)', checks(old))
        kost = 'Buyer Request\nRumah kost AKTIF area tenggilis\nLT 200m2\nKT 10\nBudget 3M\nDebby 081200000846'
        listing = 'Dijual rumah Tenggilis Utara\nLT 200 M2\nKT 10\nSHM\nHarga 2,5 M\nRensi 081200000847'
        self.assertIn('Needs check: boarding house (kost) not confirmed', checks(match(kost, listing)))
        self.assertEqual(match(kost, listing.replace('Dijual rumah', 'Dijual rumah kost aktif'))['status'], 'hot')
        renter = 'Renter request\nCari sewa rumah Citraland lokasi depan\nKT 3\nBudget 70jt-90jt/th\nFull furnish\nFerry 081200000848'
        self.assertIn('Needs check: exact location not confirmed (buyer named Citraland Depan)',
                      checks(match(renter, 'Disewakan rumah NWL Citraland full furnish\nLT 135\nKT 3\nHarga 85jt/th\nNike 081200000849')))

    def test_under_this_skip_is_a_hard_minimum(self):
        buyer = 'Buyer Request\nRumah Daerah Darmo Permai\nLuas minim 200m ( Yang Under ini skip ya )\nKT 4\nBudget 5 M\nAndi 081200000850'
        self.assertIsNone(match(buyer, 'Dijual rumah Darmo Permai\nLT 173\nKT 4\nSHM\nHarga 4,5 M\nBudi 081200000851'))
        self.assertIsNotNone(match(buyer, 'Dijual rumah Darmo Permai\nLT 210\nKT 4\nSHM\nHarga 4,5 M\nBudi 081200000851'))

    def test_a_landmark_is_no_requested_area(self):
        renter = 'Renter Request\nRumah lokasi Manyar (dkt sklh Petra)\n3 kmr tidur\nHrg kisaran 50jt- 60jt/thn\nMeme 081200000852'
        far = ('Disewakan rumah siap huni\nPondok Chandra Indah dekat Sport Club. Strategis dekat merr, jalan tol, juanda, & sekolah petra\n'
               'KT 3\nHarga: 65 jt / thn\nErny 081200000853')
        self.assertEqual(row(far)['locations'], ['pondok tjandra'])   # "jalan tol" is no place of its own
        self.assertIsNone(match(renter, far))
        self.assertIn('Inside Manyar: Manyar Jaya', match(renter, 'Disewakan rumah Manyar Jaya\nKT 3\nHarga 55 jt/th\nBudi 081200000854')['explanation'])

    def test_the_front_part_of_a_development(self):
        buyer = ('Buyer request\nRumah siap huni Citraland Depan (Citraland dalam jangan di kasih)\nHarus hadap utara\nLuas 200 - 300m2\n'
                 'Budget 5-7 M\nKenny 081200000835')
        listing = 'Dijual rumah Raffles Garden Citraland siap huni\nLT 288\nKT 4\nSHM\nHadap Utara\nHarga 5,25 M\nFong 081200000836'
        result = match(buyer, listing)
        self.assertEqual(result['status'], 'custom')
        self.assertIn('Needs check: exact location not confirmed (buyer named Citraland Depan)', checks(result))
        front = match(buyer, listing.replace('Raffles Garden Citraland', 'Citraland cluster depan'))
        self.assertEqual(front['status'], 'hot', front['explanation'])


class CaseA_TeardownManyar(unittest.TestCase):
    def test_manyar2an_is_manyar_and_its_surroundings(self):
        for text in ('Manyar2an', 'Manyar²an', 'manyar-an', 'manyar 2an', 'Manyaran'):
            self.assertEqual(expand_place_suffixes(text).lower(), 'manyar dsk', text)
        for text in ('Gayungan', 'Pandaan', 'Krembangan', 'jalan', 'taman', 'lapangan', 'daerah manyar'):
            self.assertEqual(expand_place_suffixes(text), text)
        buyer = row(A_BUYER)
        self.assertEqual(buyer['document_type'], 'buyer_request')
        self.assertIn('manyar', buyer['locations'])

    def test_new_or_modern_houses_never_fit_a_teardown_buyer(self):
        self.assertIsNone(match(A_BUYER, A_MULYOSARI))
        self.assertIsNone(match(A_BUYER, A_ROYAL))
        for part in rows(A_SARONOJIWO):
            self.assertIsNone(match(A_BUYER, part))
        old = 'Dijual rumah lama hitung tanah Manyar Jaya\nLT 8x22=176 | LB 140\nSHM | Hadap Utara\nbukan TS\n1.5M Nego\nBudi 081200000819'
        for word in ('NEW GRESS', 'Gress', 'baru gress', 'brand new', 'Minimalis', 'Rumah baru', 'sedang progress pembangunan',
                     'sedang dibangun', 'inden', 'renov total', 'full renov', 'siap huni', 'new minimalis'):
            self.assertIsNone(match(A_BUYER, old.replace('rumah lama hitung tanah', 'rumah ' + word)), word)
        fits = match(A_BUYER, old)
        self.assertEqual(fits['status'], 'hot', fits['explanation'])
        self.assertIn('Inside Manyar: Manyar Jaya', fits['explanation'])

    def test_royal_residence_is_not_manyar(self):
        self.assertIsNone(match(A_BUYER, A_ROYAL.replace('Rumah Minimalis. ', 'Rumah lama. ')))
        elsewhere = match(A_BUYER, A_MULYOSARI.replace('NEW GRESS MINIMALIS ', 'rumah lama '))
        self.assertEqual(elsewhere['status'], 'custom')
        self.assertIn('Needs check: exact location not confirmed', checks(elsewhere))


class CaseB_NamedPlaces(unittest.TestCase):
    def test_place_data(self):
        places = default_places()
        self.assertEqual(places.recognize('karang empat karang asem'), ['karang empat', 'karang asem'])
        self.assertEqual(places.ancestors('karang asem')[:3], ('ploso', 'tambaksari', 'surabaya timur'))
        self.assertEqual(places.kecamatan('medayu'), 'rungkut')
        self.assertTrue(places.far_apart('medayu', ['karang empat', 'karang asem']))
        self.assertFalse(places.far_apart('kalijudan', ['karang asem']))   # Mulyorejo borders Tambaksari

    def test_far_kecamatan_is_rejected_nearby_one_is_custom(self):
        self.assertIsNone(match(B_BUYER, B_MEDAYU))
        listing = B_MEDAYU.replace('Surabaya Selatan', 'Surabaya Timur').replace('Dekat Wonorejo Rungkut\nDekat Pandugo\n', '') \
            .replace('Full Bangunan', '1 lantai')
        near = match(B_BUYER, listing.replace('Medayu Utara Rungkut', 'Kalijudan'))
        self.assertEqual(near['status'], 'custom')
        self.assertIn('Needs check: exact location not confirmed', checks(near))
        inside = match(B_BUYER, listing.replace('Medayu Utara Rungkut', 'Karang Asem'))
        self.assertEqual(inside['status'], 'hot', inside['explanation'])
        unstated_floors = match(B_BUYER, B_MEDAYU.replace('Medayu Utara Rungkut', 'Karang Asem').replace('Surabaya Selatan', 'Surabaya Timur'))
        self.assertEqual(unstated_floors['status'], 'custom')   # "1 lantai (wajib)": the listing never states its storeys

    def test_an_unrecognised_requested_place_needs_the_listing_to_name_it(self):
        buyer = 'Dicari rumah daerah sby timur lebo agung dsk\nLT 100\nKT 3\nbudget 800-900jt\nEvita 081200000820'
        listing = 'Dijual rumah Mulyosari Surabaya Timur\nLT 100\nKT 3\nSHM\nHarga 850 jt\nBudi 081200000821'
        result = match(buyer, listing)
        self.assertEqual(result['status'], 'custom')
        self.assertIn('Needs check: exact location not confirmed (buyer named “lebo agung”)', checks(result))
        self.assertNotIn('Needs check: exact location not confirmed (buyer named “lebo agung”)',
                         checks(match(buyer, listing.replace('Mulyosari', 'Lebo Agung'))))


class CaseC_ApartmentBuilding(unittest.TestCase):
    def test_a_shared_word_never_proves_the_building(self):
        self.assertIsNone(match(C_BUYER, C_GUNAWANGSA))
        self.assertIsNone(match(C_BUYER, C_GUNAWANGSA.replace('Kondisi kosongan', 'Full furnish')))
        same = C_GUNAWANGSA.replace('Gunawangsa Tidar', 'Puncak Tidar')
        self.assertIsNone(match(C_BUYER, same))   # furnished buyer, "Kondisi kosongan" (appliances included or not)
        self.assertIsNotNone(match(C_BUYER, same.replace('Kondisi kosongan', 'Full furnish')))

    def test_furnishing_spellings(self):
        for text in ('2br furblnish', 'furnis', 'furnised', 'fullfurnish', 'full furnished', 'ff'):
            self.assertEqual(_furnishing(text, True), 'full', text)
        for text in ('semi furnish', 'sf'):
            self.assertEqual(_furnishing(text, True), 'semi', text)
        self.assertEqual(_furnishing('unfurnised'), 'empty')
        self.assertEqual(_furnishing('kondisi kosongan\nharga sdh termasuk 2 unit ac, kulkas, tv'), 'empty')
        self.assertIsNone(_furnishing('prefer blok ff, p, pp'))


class CaseD_NewHouse(unittest.TestCase):
    def test_second_full_renov_is_not_new(self):
        result = match(D_BUYER, D_NIRWANA)
        self.assertEqual(result['status'], 'custom')
        self.assertIn('Needs check: building condition (buyer wants a new house)', checks(result))
        self.assertNotIn('Building condition: new / move-in ready ✓', result['explanation'])
        new = match(D_BUYER, D_NIRWANA.replace('Rumah Second FULL RENOV', 'Rumah Baru Gress'))
        self.assertEqual(new['status'], 'hot', new['explanation'])
        self.assertIn('Building condition: new / move-in ready ✓', new['explanation'])
        self.assertEqual(match(D_BUYER, D_NIRWANA.replace('Rumah Second FULL RENOV', 'Rumah bekas'))['status'], 'custom')


class CaseE_RenterWishes(unittest.TestCase):
    def test_locations(self):
        self.assertEqual(row(E_RENTER)['locations'], ['citraland', 'graha famili', 'pakuwon indah'])
        self.assertEqual(row(E_PALM_HILL)['locations'], ['citraland'])   # "Service area terpisah" is no place

    def test_wajib_minimalist_needs_the_listing_to_say_it(self):
        result = match(E_RENTER, E_PALM_HILL)
        self.assertEqual(result['status'], 'custom')
        self.assertEqual(checks(result), ['Needs check: requirement not verified: “wajib minimalist”'])
        proven = match(E_RENTER, E_PALM_HILL.replace('Furnish\n', 'Furnish\nRumah minimalis\n'))
        self.assertEqual(proven['status'], 'hot', proven['explanation'])
        self.assertIn('Buyer requirement met: “wajib minimalist” ✓', proven['explanation'])

    def test_also_offered_areas_are_accepted_but_not_preferred(self):
        graha = match(E_RENTER, E_PALM_HILL.replace('Palm Hill Citraland', 'Graha Famili').replace('Furnish\n', 'Furnish\nminimalis\n'))
        self.assertEqual(graha['status'], 'custom')
        self.assertIn('Needs check: preferred area not confirmed (Citraland)', checks(graha))
        self.assertIsNone(match(E_RENTER, E_PALM_HILL.replace('Palm Hill Citraland', 'Manyar')))


class CaseF_PositiveControl(unittest.TestCase):
    def test_stays_hot(self):
        result = match(F_RENTER, F_ROYAL)
        self.assertEqual(result['status'], 'hot', result['explanation'])
        self.assertEqual(checks(result), [])
        self.assertGreaterEqual(result['score'], HOT_MIN_SCORE)


if __name__ == '__main__':
    unittest.main()
