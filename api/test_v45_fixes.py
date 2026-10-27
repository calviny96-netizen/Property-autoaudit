"""v4.5: fixes for the independent judge's findings on real data (v4.4 review): a rent per m² with a period is multiplied
out, "Panjang"/"Lebar" lines give an area, what a property suits or what surrounds it adds no category, "Bangunan 2 Lantai"
no longer hides "Luas Bangunan 87", central Surabaya streets ("Jl. Pahlawan") place a listing in Surabaya Pusat, and
"Tingkat: 1" is a storey count. Texts follow the judged listings (contact numbers are placeholders)."""
import unittest

from matching_rules import _count
from parser import parse_message, per_m2_terms
from test_v43_terms import JT, M, checks, match, row

SIER = ('🏭 DISEWAKAN | GUDANG & INDUSTRIAL PROPERTY — RAYA RUNGKUT INDUSTRI SIER, SURABAYA\n\n- Luas Tanah (LT): 12.841 m²\n'
        '- Luas Bangunan (LB): 9.610 m²\n- Listrik: 53.000 watt\n\nFASILITAS KAWASAN\n✅ Kawasan industri terpadu SIER\n'
        '✅ Rumah sakit\n✅ Kantor polisi\n\nCocok untuk kebutuhan pergudangan, pusat distribusi, logistik, maupun industri Besar.\n\n'
        '💰 HARGA SEWA: *Rp58.500/m² BANGUNAN/bulan Nego*\n📌 Min sewa: 2 tahun\n\n📩 Hubungi :\nGustav\n081200000101')
RENTER = 'Renter request\nGudang & Kantor Surabaya Timur\nRencana Sewa 2 tahun\nBudget Max : 110 juta/tahun\nMichael 081200000102'


class RentPerSquareMetre(unittest.TestCase):
    """WARM 29: "Rp58.500/m² BANGUNAN/bulan" × LB 9.610 m² ≈ Rp 562 jt per month, never Warm for 110 jt a year."""

    def test_reading_the_period_and_the_area(self):
        self.assertEqual(per_m2_terms('harga sewa: rp58.500/m² bangunan/bulan nego'), ('per_month', 'building'))
        self.assertEqual(per_m2_terms('bujet 30 rb-35 rb /meter/ bln'), ('per_month', None))
        self.assertEqual(per_m2_terms('harga sewa rp 200.000 / m² / tahun tanah'), ('per_year', 'land'))
        self.assertEqual(per_m2_terms('harga 15 jt/m2 nego'), (None, None))
        listing = row(SIER)
        self.assertEqual((listing['price_min'], listing['price_basis'], listing['building_area_min']), (58500, 'per_m2', 9610.0))
        # A price per m² of a period is a rent, also without a rent word.
        self.assertEqual(row('Gudang Rungkut\nLB 1.000 m²\nHarga Rp 50.000/m²/bulan')['transaction_type'], 'rent')

    def test_the_judged_pair_is_rejected(self):
        self.assertIsNone(match(RENTER, SIER))
        fits = match(RENTER.replace('110 juta', '7 M'), SIER)
        self.assertIsNotNone(fits)
        self.assertIn('Rp 58.500/m² per month × 9610 m² ≈ Rp 562,18 jt per month — within budget', fits['explanation'])

    def test_building_or_land_area(self):
        listing = 'Disewakan gudang Rungkut Industri\nLB 1.000 m²\nLT 1.500 m²\nHarga sewa Rp 50.000/m²/bulan'
        buyer = 'Renter request\nCari sewa gudang Rungkut\nBudget 600 jt/tahun'
        result = match(buyer, listing)
        self.assertEqual(result['status'], 'custom')  # v4.8: purpose, unit, area and budget only (4 of 5 details) is unclear
        self.assertIn('Rp 50.000/m² per month × 1000 m² ≈ Rp 50 jt per month — within budget', result['explanation'])
        self.assertIsNone(match(buyer.replace('600 jt', '110 jt'), listing))
        land = match(buyer, listing.replace('Rp 50.000/m²/bulan', 'Rp 400.000/m² tanah per tahun'))
        self.assertIn('Rp 400.000/m² per year × 1500 m² ≈ Rp 600 jt per year — within budget', land['explanation'])

    def test_an_unknown_area_or_period_stays_unproven(self):
        buyer = 'Renter request\nCari sewa gudang Rungkut\nBudget 600 jt/tahun'
        no_area = match(buyer, 'Disewakan gudang Rungkut Industri\nLT 1.500 m²\nHarga sewa Rp 50.000/m²/bulan')
        self.assertEqual(no_area['status'], 'custom')  # v4.8: an unproven rent is unclear, never Warm
        self.assertIn('Needs check: rent is per m² but the building area is not stated', checks(no_area))
        no_period = match(buyer, 'Disewakan gudang Rungkut Industri\nLB 1.000 m²\nHarga sewa Rp 50.000/m²')
        self.assertEqual(no_period['status'], 'custom')
        self.assertIn('Needs check: rent price basis differs (per m² vs period)', checks(no_period))

    def test_a_budget_per_square_metre(self):
        buyer = 'Renter request\nCari sewa gudang Rungkut\nBujet 30 rb-35 rb /meter/ bln'
        fits = match(buyer, 'Disewakan gudang Rungkut\nLB 1.500 m²\nHarga sewa 600 jt/tahun')
        self.assertIn('Rp 600 jt ÷ 1500 m² ≈ Rp 400.000/m² per year — within budget', fits['explanation'])
        self.assertIsNone(match(buyer, 'Disewakan gudang Rungkut\nLB 1.500 m²\nHarga sewa 1,25 M/tahun'))
        self.assertIn('Rent price within budget', match(buyer, 'Disewakan gudang Rungkut\nLB 1.000\nHarga sewa 32rb/m2/bln')['explanation'])
        unproven = match(buyer.replace(' /meter/ bln', '/m2'), 'Disewakan gudang Rungkut\nLB 1.500 m²\nHarga sewa 1,25 M/tahun')
        self.assertIn('Needs check: rent period not stated', checks(unproven))
        no_area = match(buyer, 'Disewakan gudang Rungkut\nHarga sewa 600 jt/tahun')
        self.assertIn('Needs check: rent budget is per m² but the building area is not stated', checks(no_area))

    def test_a_purchase_budget_never_meets_a_rent_per_square_metre(self):
        self.assertIsNone(match('Cari gudang Rungkut\nBudget 5 M', 'Dijual gudang Rungkut\nLB 1.000 m²\nHarga Rp 50.000/m²/bulan'))


class LengthAndWidth(unittest.TestCase):
    """WARM 23: "- Panjang 40 m" / "- Lebar 10.8 m" is a 432 m² warehouse."""
    SEDATI = ('*HOT LISTING LUAR KOTA*\n*Disewakan gudang di kompleks pergudangan 88 - Sedati , Juanda - Sidoarjo*\n- Panjang 40 m\n'
              '- Lebar 10.8 m\n- Listrik 6600 VA dan 1200 VA\n- Ruang kantor 6 ruang\nHarga Rp. 250 JT / th masih nego')

    def test_the_sides_give_the_area(self):
        listing = row(self.SEDATI)
        self.assertEqual((listing['land_area_min'], listing['building_area_min']), (432.0, 432.0))
        buyer = ('*Renter Request*\nBangunan pabrik/gudang industri\nArea sidoarjo/gresik\nLuas bangunan 2000-3000m2\n'
                 'Budget mengikuti\nPangki 081200000103')
        self.assertIsNone(match(buyer, self.SEDATI))
        self.assertIsNotNone(match(buyer.replace('2000-3000', '400-500'), self.SEDATI))

    def test_which_area_the_sides_measure(self):
        house = row('Dijual rumah Wiyung\nLebar depan 8 m\nPanjang 25 m\nHarga 2 M')
        self.assertEqual((house['land_area_min'], house['building_area_min']), (200.0, None))
        self.assertEqual(row('Dijual rumah Wiyung\nLT 300\nLebar 8 m\nPanjang 25 m\nHarga 2 M')['land_area_min'], 300.0)
        building = row('Disewakan gudang Margomulyo\nLT 1000 m2\nPanjang bangunan 40 m, lebar bangunan 20 m\nHarga 300 jt/th')
        self.assertEqual((building['land_area_min'], building['building_area_min']), (1000.0, 800.0))
        # The road is no side of the property.
        self.assertIsNone(row('Dijual tanah Gresik\nLebar jalan 10 m\nPanjang 50 m\nHarga 2 M')['land_area_min'])


class SurroundingsAndSuitableUses(unittest.TestCase):
    """WARM 31 / WARM 21: neighbourhood counts, landmarks and "cocok untuk" lists never add a category."""
    EASTERN_PARK = ('*Jual Ruko 3 lantai Eastern Park depan Sukolilo Dian Regency II*\nLokasi strategis, di dekat rencana OERR\n'
                    'Target pasar +-1500 rumah di sekitarnya\nDimensi 4,5 x 12; 3 lantai\nLuas tanah 54 sqm\nLuas bangunan 145 sqm\n'
                    'Harga jual 2,1M nego\nHarga sewa 65 jt/tahun nego')
    EX_RESTO = ('*Dijual* ex resto\nRaya Menganti  Wiyung  /  jalan kembar Surabaya Barat.\n_*(Cocok segala usaha) :*_\n🏥 Rumah sakit.\n'
                '🏨 Hotel.\n🍽️ Pujasera.\n⛪ Gereja.\n🏪 Super market, dll.\n*Spesifikasi :*\n✅ LT : 1.230m di _SHM tercantum 1.085m_\n'
                '✅ LB : +_ 2.000.\n✅ SHM.\n✅ Hadap Selatan.\n✅ Harga : 25M\nHubungi\n*Christine*\n*081200000104*')

    def test_the_judged_listings(self):
        self.assertEqual(row(self.EASTERN_PARK)['categories'], ['shophouse'])
        renter = ('‼️RENTER REQUEST ( S )‼️\n1. Rumah Surabaya Timur/Pusat/Barat ( Wajib Dalam Perumahan )\n3. Kamar min 2\n'
                  '7. Budget : 70 jt/thn\nMichael 081200000105')
        self.assertIsNone(match(renter, self.EASTERN_PARK))
        self.assertEqual(row(self.EX_RESTO)['categories'], ['commercial_building'])
        buyer = ('*BUYER REQUEST*\nDICARI RUMAH / RUKO\ndi daerah WIYUNG\nPeruntukan : gudang barang\nBudget menyesuaikan\n'
                 'THEO 081200000106')
        self.assertIsNone(match(buyer, self.EX_RESTO))

    def test_landmarks_and_neighbourhoods(self):
        for text, categories in (
                ('Dijual ruko Rungkut dekat sekolah, rumah sakit, hotel\nLT 100\nHarga 3 M', ['shophouse']),
                ('Disewakan gudang di kawasan hunian padat\nLT 300\nHarga 100 jt/th', ['warehouse']),
                ('Dijual Rumah Sakit tipe C Sidoarjo\nLT 5000\nHarga 50 M', []),
                ('Dijual ruko dekat apartemen Puri, depan ada soto ayam\nLT 100\nHarga 3 M', ['shophouse']),
                # An alternative after a landmark is still wanted.
                ('Dicari gudang dekat tol, ruko juga boleh\nBudget 3 M', ['warehouse', 'shophouse'])):
            self.assertEqual(parse_message(text).categories, categories, text)
        # A house described by its specs stays a house when a mall is only a landmark.
        house = parse_message('DIJUAL CEPAT!!\nTaman Pondok Indah Surabaya\n* Dekat dengan akses Tol, Mall, Pasar, Rumah Sakit, dll\n'
                              'LT/LB : 84m / 84m\nTingkat : 1lt\nKT: 2\nKM:1\nJual:Rp 800 jt nego')
        self.assertEqual((house.classification, house.categories), ('property_listing', ['house']))

    def test_suitable_uses(self):
        for text, categories in (
                ('Cocok untuk usaha\nRuko gandeng 2 unit Darmo\nHarga 5 M', ['shophouse']),
                ('Dijual tanah Wiyung, bisa dibangun ruko atau gudang\nLT 500\nHarga 5 M', ['land']),
                ('DIJUAL RUMAH\nCocok untuk:\n- Kantor\n- Gudang\nLT 200\nHarga 2 M', ['house']),
                ('*WISMA MUKTI KLAMPIS ANOM SIAP HUNI* _Row jalan lima mobil bisa untuk usaha._\nLT 228 m2\nLB 180 m2\nKT 4 KM 2\n'
                 'Harga 2,99 M', ['house']),
                # A former business is the asset only when nothing else is.
                ('For Sale Ruko San Antonio (Ex klinik kecantikan)\nLT 63m²\nHarga 4,5M', ['shophouse']),
                ('Dijual Tanah & Bangunan Ex Showroom\nLT 5100 m²\nHarga Rp 12jt/m²', ['land', 'commercial_building'])):
            self.assertEqual(parse_message(text).categories, categories, text)


class BuildingAreaAfterStoreys(unittest.TestCase):
    """CUSTOM 13: "Bangunan 2 Lantai" before "Luas Bangunan 87"."""
    HARVEST = ('*PALING MURAH Dijual/Disewakan Rumah Grand Harvest Arcadia Surabaya*\n📌 Belakang ada Taman yg Luas bisa dibangun '
               'bangunan yg besar\nBangunan 2 Lantai\nLuas Tanah 126 (7x18)\nLuas Bangunan 87\nKamar tidur 2\nHGB\n'
               '° *Harga Jual ~1,9M~ Turun Jadi Rp 1,6M Nego*\n° *Harga Sewa Rp 45 Jt/thn min 2 thn nego*\nGracios 081200000107')

    def test_the_area_after_the_storeys(self):
        listing = row(self.HARVEST)
        self.assertEqual((listing['land_area_min'], listing['building_area_min']), (126.0, 87.0))
        buyer = ('RENTER REQUEST\n- Dicari rumah/ruko/bangunan usaha untuk klinik di:\n📍 Wiyung–Menganti–Babatan–Royal Residence\n'
                 'Kriteria:\n• Bangunan ideal ≥200 m²\n• ⁠budget max 150 jt/th\nImam 081200000108')
        self.assertIsNone(match(buyer, self.HARVEST))
        self.assertEqual(row('Dijual Rumah\nBangunan 2 Lantai\nLT 126\nHarga 1,6M')['building_area_min'], None)

    def test_sides_and_heights_are_no_building_area(self):
        self.assertIsNone(row('Disewakan gudang\nLT 1000\nTinggi bangunan 7-8 m\nHarga 300 jt/th')['building_area_min'])
        self.assertEqual(row('Disewakan gudang Tanjungsari\nLuas tanah 10x10 = 100\nLUas bangunan full 100m\nTinggi bangunan 7-8m\n'
                             'Harga 60 jt/th')['building_area_min'], 100.0)


class CentralSurabayaStreets(unittest.TestCase):
    """CUSTOM 17: "Jl. Pahlawan no. 6 Surabaya (Pusat kota Surabaya)" is in Surabaya Pusat."""
    PAHLAWAN = ('Jual /Sewa\nRuko 3 lantai\nJl. Pahlawan no. 6 Surabaya\nSHM\nLT 195\nLB 540\nLokasi strategis(Pusat kota Surabaya) \n'
                'Listrik PLN\nJual:6, 5M nego\nSewa:250 juta/thn(minimal 2 tahun) \nHubungi:\nAnita 081200000109')

    def test_the_judged_pair(self):
        self.assertEqual(row(self.PAHLAWAN)['locations'], ['jl pahlawan', 'surabaya'])
        west = ('Renter request\nKlien lama pindah barat\nRuko surabaya barat\n3lantai baru\nBudget up to 300jt/th\nDavid 081200000110')
        self.assertIsNone(match(west, self.PAHLAWAN))
        centre = match('Renter request\nRuko Surabaya Pusat\nBudget up to 300jt/th', self.PAHLAWAN)
        self.assertIn('Inside Surabaya Pusat: Jl Pahlawan', centre['explanation'])

    def test_central_streets(self):
        for text, place in (('Dijual ruko jalan Pahlawan Surabaya', 'jl pahlawan'), ('Ruko Raya Pemuda Surabaya', 'jl pemuda'),
                            ('Ruko jl. Diponegoro Surabaya', 'raya diponegoro'), ('Gedung Raya Gubernur Suryo Sby', 'gubernur suryo'),
                            ('Ruko Raya Panglima Sudirman Surabaya', 'panglima sudirman'), ('Ruko Embong Malang', 'embong malang'),
                            ('Ruko Raya Kedungdoro', 'kedungdoro'), ('Ruko Jl. Tunjungan', 'tunjungan'), ('Ruko Raya Darmo', 'darmo')):
            self.assertIn(place, parse_message(f'{text}\nLT 100\nHarga 5 M').locations, text)
        # "Pahlawan" alone is no street ("Hari Pahlawan"), nor "Sumpah Pemuda".
        self.assertEqual(parse_message('Promo Hari Pahlawan, Sumpah Pemuda\nDijual rumah\nLT 100\nHarga 2 M').locations, [])

    def test_streets_many_towns_share(self):
        # A street name many towns have needs Surabaya (or a place in it) in the message.
        self.assertEqual(parse_message('Ruko Raya Pemuda\nLT 100\nHarga 5 M').locations, [])
        for text in ('Dijual tanah komersial jl. panglima sudirman, kec. taman, kota madiun\nLT 1000\nHarga 10 M',
                     'Dijual ruko\njalan raya panglima sudirman, kraksaan, jawa timur\nLT 100\nHarga 2 M',
                     'Dijual tanah jl basuki rahmat krian\nLT 500\nHarga 2 M',
                     'Disewakan Lahan Jl. Pahlawan Rempoa dekat Situgintung, luas 24.880m2, hrg. Rp. 1,5M/Ha/Thn'):
            locations = parse_message(text).locations
            self.assertFalse({'jl pahlawan', 'panglima sudirman', 'basuki rahmat'} & set(locations), text)
        self.assertNotIn('jl pahlawan', parse_message('Cari ruko jl pahlawan sidoarjo\nBudget 3 M').locations)
        request = parse_message('Cari BELI Tanah >> Raya Pemuda/Gubernur Suryo, Raya Panglima Sudirman, Raya Darmo\nLT 2500\n'
                                'Budget mengikuti')
        self.assertTrue({'jl pemuda', 'gubernur suryo', 'panglima sudirman', 'darmo'} <= set(request.locations))


class StoreyWords(unittest.TestCase):
    """WARM 33: "Tingkat: 1" is one storey, so a "Harus 2 lantai" buyer rejects it."""

    def test_reading(self):
        for text, storeys in (('tingkat: 1', (1, 1)), ('tingkat 2', (2, 2)), ('2 tingkat', (2, 2)), ('jumlah lantai: 2', (2, 2)),
                              ('tingkat okupansi 80%', None), ('ranjang tingkat 2', None), ('tingkat keamanan 24 jam', None)):
            self.assertEqual(_count(text, 'floors'), storeys, text)

    def test_the_judged_pair(self):
        buyer = ('*NEW BUYER REQUEST RUMAH BARU SBY TIMUR*\n- Luas mengikuti\n- Rumah baru (wajib)\n- Harus 2 lantai\n'
                 '*BUDGET 2M AN*\nMatthew 081200000111')
        listing = ('*NEGO BERAPAPUN DIPERTIMBANGKAN!*\nRumah Manyar Tirtoasri\nStrategis • Siap Huni • Carport 2 • Row 2 Mobil\n'
                   'Dimensi: 10x20\nLT: 200\nLB: 160\nTingkat: 1\nKT: 4+1\nSertifikat: SHM\nHarga: 2.1M nego\nCeline 081200000112')
        self.assertIsNone(match(buyer, listing))
        self.assertIn('Floors match', match(buyer, listing.replace('Tingkat: 1', 'Tingkat: 2'))['explanation'])


if __name__ == '__main__':
    unittest.main()
