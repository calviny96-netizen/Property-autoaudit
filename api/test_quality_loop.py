import json,unittest
from pathlib import Path
from parser import parse_message,split_contact
from matching_rules import assess_pair,_count
from test_matching_rules import SETTINGS

GLOSSARY={'lt':'luas tanah -- ukuran tanah suatu properti dalam m²',
 'lb':'luas bangunan -- ukuran bangunan/rumah',
 'kt':'kamar tidur -- jumlah ruang kamar tidur dalam properti',
 'kpr':'kredit pemilikan rumah -- fasilitas pembiayaan rumah',
 'nego':'nego (negotiable) -- harga masih bisa ditawar oleh calon pembeli'}

def row(text):
 r=parse_message(text,glossary=GLOSSARY).dict()
 r['raw_text']=text;r['extraction_confidence']=r.pop('confidence')
 return r

def match(buyer,property):return assess_pair(row(buyer),row(property),SETTINGS,100)

class QualityFeedbackLoop(unittest.TestCase):
 def test_count_bounds_and_fractional_floors(self):
  self.assertIsNone(_count('lb 200\nlt 100','floors'))
  self.assertEqual(_count('ruko 2lt','floors'),(2,2))
  self.assertIsNone(match('Buyer request ruko Surabaya Barat 5 lantai Budget 3 M','Dijual ruko Surabaya Barat 2,5 lantai Harga 2 M'))
  self.assertIsNone(match('Buyer request ruko Surabaya Barat 5lt Budget 3 M','Dijual ruko Surabaya Barat 3 lantai Harga 2 M'))
  # v4.3 band: Hot 80-100 % of the budget, Warm 15 % below that (3 M -> 2,04 M); 2 M is outside it.
  self.assertGreaterEqual(match('Buyer request rumah Surabaya Barat KT minimal 4 Budget 3 M','Dijual rumah Surabaya Barat KT 5 Harga 2,5 M')['score'],80)
  self.assertIsNone(match('Buyer request rumah Surabaya Barat KT minimal 4 Budget 3 M','Dijual rumah Surabaya Barat KT 5 Harga 2 M'))
  self.assertIsNone(match('Buyer request rumah Surabaya Barat KT maksimal 4 Budget 3 M','Dijual rumah Surabaya Barat KT 5 Harga 2 M'))
  self.assertIsNone(match('Buyer request rumah Surabaya Barat Kamar lebih dari 4 Budget 3 M','Dijual rumah Surabaya Barat KT 4+1 Harga 2 M'))
 def test_bundled_listings_are_withheld(self):
  r=match('Buyer request rumah Surabaya Barat Budget 3 M','Edisi Surabaya Barat\n1. Dijual rumah Surabaya Barat LT 100 Harga 2 M\n2. Dijual rumah Surabaya Barat LT 200 Harga 4 M')
  self.assertTrue(r is None or r['score']<60)
 def test_all_observed_false_hot_cases(self):
  for c in json.loads(Path(__file__).with_name('fixtures').joinpath('caesar_quality_cases.json').read_text()):
   with self.subTest(case=c['name']):
    r=match(c['buyer'],c['property'])
    if c['expected']=='reject':self.assertIsNone(r)
    else:self.assertTrue(r is not None and r['score']<80)
 def test_clear_positive_matches_remain_hot(self):
  for kind in ['rumah','ruko','gudang']:
   with self.subTest(kind=kind):
    self.assertGreaterEqual(match(f'Buyer request {kind} Surabaya Barat LT 100 Budget 2 M',f'Dijual {kind} Surabaya Barat LT 100 Harga 1,8 M')['score'],80)
 def test_glossary_descriptions_never_become_evidence(self):
  p=row('Dijual apartemen Anderson Surabaya LT 30 LB 30 KT 1 Harga 1 M nego KPR')
  self.assertEqual(p['categories'],['apartment'])
  self.assertNotIn('--',p['normalized_text'])
  self.assertFalse(any('nego' in v or 'kamar' in v for v in p['locations']))
 def test_inline_contact_does_not_discard_later_requirements(self):
  p=row('Buyer request kavling Citraland\nKirim spec ke wa.me/6281234567890\n\nCari beli tanah\nLuas 450-600\nBudget under 15 jt/m²\n\nHubungi agen 081234567890')
  self.assertEqual((p['land_area_min'],p['land_area_max']),(450,600))
 def test_price_spellings_and_unit_price(self):
  self.assertEqual(row('Buyer request rumah Citraland budget 3-4 Milyard')['price_max'],4_000_000_000)
  self.assertEqual(row('Buyer request rumah Surabaya Budget 600-700 jutaaan')['price_max'],700_000_000)
  p=row('Dijual tanah Surabaya LT 1049 Harga Jual 8 Juta /m² (8,392M) Nego')
  self.assertEqual((p['price_min'],p['price_max'],p['price_basis']),(8_000_000,8_000_000,'per_m2'))
  self.assertIsNone(match('Buyer request rumah Citraland under 3M','Dijual rumah Citraland Harga 4,5 M Nego'))
 def test_unreadable_budget_is_withheld(self):
  r=match('Buyer request rumah Surabaya Budget 800-900max','Dijual rumah Surabaya Harga 15 M')
  self.assertLess(r['score'],60)
 def test_contact_office_is_not_location(self):
  p=row('Buyer request apartemen Anderson\nBudget mengikuti\n\nAgent Ray White Darmo Permai\n081234567890')
  self.assertNotIn('darmo permai',p['locations'])
 def test_price_after_reduction(self):
  p=row('Turun harga dijual rumah Surabaya\nTurun harga dari 4,05 Milyar jadi\nHarga 3,75 Milyar')
  self.assertEqual(p['price_max'],3_750_000_000)
