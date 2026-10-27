import unittest
from parser import parse_message, score_range

class ParserRegression(unittest.TestCase):
    def test_ready_listing(self):
        p=parse_message('''*Wisata Bukit Mas Surabaya Barat*
*Yang butuh Rumah READY SIAP HUNI JAMIN INI COCOK 100%*_
LT: 160 m² (8x20)
LB: 179 m²
KT: 3+1
KM: 2+1
Sertifikat: SHM
Hadap: Timur
Bonus (Furnished): 4 AC
Harga 3,7 M NEGOOO
Contact :
*Santi Wijaya || 0817390035*
*DB Real Estate Indonesia*''')
        self.assertEqual(p.classification,'property_listing')
        self.assertEqual((p.land_area_min,p.building_area_min,p.price_max),(160,179,3700000000))
        self.assertEqual(p.facing,['timur'])
        self.assertEqual(p.contact_phone,'62817390035')
    def test_flattened_listing(self):
        p=parse_message('Lagi cari barang murah untuk kulakukan? Atau lagi cari gudang di Surabaya Timur? Atau cari tempat untuk produksi di Surabaya Timur? DIJUAL MURAH GUDANG/RUMAH PRODUKSI di KALIJUDAN MADYA Strategis >>> 2mt dari Raya Merr LT 321m² LB 485m² (bangunan 3 lantai) Lantai 1 : ruang produksi Lantai 2 : mess karyawan Lantai 3 : mushola Kamar Mandi 3 SHM (di bank) Listrik 33.000 watt Hadap Utara Ada lift barang dan Rak² barang Ada Air PDAM & Sumur Row jalan 2.5 mbl Harga Cuman 3.5M Contact : Nike - 0896 7605 2044 Xmarks Patos Pakuwon City Mall')
        self.assertEqual(p.classification,'property_listing')
        self.assertEqual((p.land_area_min,p.building_area_min,p.price_max),(321,485,3500000000))
        self.assertNotIn('pakuwon city',p.locations)
    def test_actual_request(self):
        p=parse_message('*Dicari Beli Rumah Graha Family, Bukit Darmo Golf (BDG), Pakuwon Indah cluster depan* Kriteria: 1. Luas Tanah +-350m² 2. Bangunan mewah, floor plan ngelos 3. Tidak mau rumah lama 4. Budget 10-15 Milyar Hubungi: Caesar Go XM Darmo 0878 5901 7101 wa.me/6287859017101')
        self.assertEqual(p.classification,'buyer_request')
        self.assertEqual((p.land_area_min,p.price_min,p.price_max),(350,10000000000,15000000000))
        self.assertEqual(p.contact_phones,['6287859017101'])
        self.assertNotIn('xm darmo',p.normalized_text)
    def test_signature_and_glossary(self):
        p=parse_message('Dijual rumah Rgcy dekat Nathos\nLT 200 LB 300 Harga 5 M\nsanti wijaya | 082312839219 DB Estate Citraland.')
        self.assertNotIn('citraland',p.locations)
        self.assertNotIn('citraland',p.normalized_text)
        self.assertIn('regency',p.locations)
        self.assertNotIn('national hospital',p.locations)  # Nearby landmark is not the address.
        self.assertIn('national hospital',p.normalized_text)
        self.assertIn('custom district',parse_message('Dicari rumah districtx',glossary={'districtx':'custom district'}).locations)
    def test_multiple_contacts(self):
        p=parse_message('Dijual rumah LT 100 Harga 1 M\nHubungi: Agent 0817390035 / 0878 5901 7101')
        self.assertEqual(p.contact_phones,['62817390035','6287859017101'])
    def test_tolerance_boundaries(self):
        self.assertEqual(score_range(100,100,100,100,0)[0],100)
        self.assertEqual(score_range(100,100,101,101,0)[0],0)
        self.assertGreater(score_range(100,100,110,110,10)[0],0)
        self.assertEqual(score_range(100,100,111,111,10)[0],0)

if __name__=='__main__': unittest.main()
