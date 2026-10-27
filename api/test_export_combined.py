"""Export of both directions per person: plan, parts, download, numbering, reasons callout and evidence highlights."""
import io
import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter

from export_all import download, group_filename, merge_pdf_parts
from matching_rules import assess_pair, evidence_spans
from parser import parse_message
from report import (BODY_BOTTOM, BODY_FONT_SIZE, BUTTON_Y, CALLOUT_MAX_HEIGHT, MARGIN, PAGE_WIDTH, STATUS_BAR_Y, build_report,
                    callout_items, indonesian, split_reasons)
from test_v4_features import BUYER_HOUSE, BUYER_RUKO, LISTING_HOUSE_A, LISTING_HOUSE_B, LISTING_RUKO, V4Case

SETTINGS = dict(land_tolerance_pct=10, building_tolerance_pct=20, price_tolerance_pct=10, location_weight_pct=35,
                land_weight_pct=20, building_weight_pct=15, price_weight_pct=20, semantic_weight_pct=5, data_quality_weight_pct=5)
DARK_BLUE = r'(?<![\d.])0?\.0431\d* 0?\.1647\d* 0?\.4352\d* rg'   # #0b2a6f, Listing → Buyer
SKY_BLUE = r'(?<![\d.])0?\.4901\d* 0?\.8274\d* 0?\.9882\d* rg'    # #7dd3fc, Buyer → Listing


def row(text):
    value = parse_message(text).dict()
    value['document_type'] = value.pop('classification')
    value['extraction_confidence'] = value.pop('confidence')
    value['raw_text'] = text
    return value


def assessed(buyer, listing):
    request, offer = row(buyer), row(listing)
    return request, offer, assess_pair(request, offer, SETTINGS, 50.0)


def phrases(text, spans):
    return [text[start:end] for start, end in spans]


def fill_colors(page):
    return page.get_contents().get_data().decode('latin-1')


class EvidenceSpanTests(unittest.TestCase):
    def test_simple_pair_marks_purpose_unit_place_and_price_on_both_sides(self):
        buyer, listing = 'Cari rumah Baruk budget 2M', 'Dijual rumah Baruk Barat LT 120 harga 1,7 M'
        request, offer, result = assessed(buyer, listing)
        self.assertIn('Same location: Baruk', result['explanation'])
        spans = evidence_spans(request, offer, result['explanation'], buyer, listing)
        self.assertEqual(phrases(buyer, spans['request']), ['Cari', 'rumah', 'Baruk', 'budget 2M'])
        self.assertEqual(phrases(listing, spans['listing']), ['Dijual', 'rumah', 'Baruk', 'harga 1,7 M'])

    def test_apartment_terms_rent_terms_and_areas_that_matched(self):
        buyer = 'Cari apartemen Waterplace tower A 2BR SHM budget 1,5 M KPR'
        listing = 'Dijual apartemen Waterplace Tower A 2BR SHM lantai 10 harga 1,4 M bisa KPR'
        request, offer, result = assessed(buyer, listing)
        spans = evidence_spans(request, offer, result['explanation'], buyer, listing)
        self.assertEqual(phrases(listing, spans['listing']),
                         ['Dijual', 'apartemen', 'Waterplace', 'Tower A', '2BR', 'SHM', 'harga 1,4 M', 'bisa KPR'])
        self.assertNotIn('lantai 10', phrases(listing, spans['listing']))  # the buyer asked for no floor
        buyer = 'Cari sewa rumah Citraland LT 200 hadap timur budget 100 jt/th minimal sewa 2 tahun'
        listing = 'Disewakan rumah Citraland LT 200 hadap timur 90 jt/th minimal 2 tahun'
        request, offer, result = assessed(buyer, listing)
        spans = evidence_spans(request, offer, result['explanation'], buyer, listing)
        self.assertEqual(phrases(buyer, spans['request']),
                         ['Cari', 'sewa', 'rumah', 'Citraland', 'LT 200', 'hadap timur', 'budget 100 jt/th', 'minimal sewa 2 tahun'])
        self.assertEqual(phrases(listing, spans['listing']),
                         ['Disewakan', 'rumah', 'Citraland', 'LT 200', 'hadap timur', '90 jt/th', 'minimal 2 tahun'])

    def test_needs_check_factors_and_signatures_are_never_marked(self):
        buyer = 'Cari rumah Surabaya Barat KT 3 budget 2 M'
        listing = 'Dijual rumah Surabaya Barat 3 KT harga 2,1 M\nRay White Surabaya Barat\nHub Sari 081234567890'
        request, offer, result = assessed(buyer, listing)
        self.assertTrue(any('above the budget' in line for line in result['explanation']))
        spans = evidence_spans(request, offer, result['explanation'], buyer, listing)
        # The price is above the budget (a check): no evidence; the agency line belongs to the signature.
        self.assertEqual(phrases(listing, spans['listing']), ['Dijual', 'rumah', 'Surabaya Barat', '3 KT'])
        self.assertEqual(phrases(buyer, spans['request']), ['Cari', 'rumah', 'Surabaya Barat', 'KT 3'])
        # Without reasons nothing is marked.
        self.assertEqual(evidence_spans(request, offer, [], buyer, listing), {'request': [], 'listing': []})


class ReasonTextTests(unittest.TestCase):
    CASES = [
        ('Purpose: buy ✓', 'Tujuan: beli'), ('Purpose: buy or rent ✓', 'Tujuan: beli atau sewa'),
        ('Unit: house, land ✓', 'Unit: rumah, tanah'), ('Same location: Baruk', 'Lokasi sama: Baruk'),
        ('Inside Surabaya Timur: Baruk', 'Di dalam Surabaya Timur: Baruk'), ('Same cluster: Pondok Nirwana', 'Cluster sama: Pondok Nirwana'),
        ('Same building: Anderson or Benson', 'Gedung sama: Anderson atau Benson'),
        ('Inside requested area: Citraland', 'Di dalam area yang diminta: Citraland'),
        ('Legal status: Strata title (SHMRS) ✓', 'Status legal: Strata (SHMRS)'), ('Unit type: 2BR or 3BR ✓', 'Tipe unit: 2BR atau 3BR'),
        ('Tower A or C ✓', 'Tower A atau C'), ('Floor 7 ✓', 'Lantai 7'), ('Floor level ✓', 'Level lantai sesuai'),
        ('Financing: KPR possible ✓', 'Pembiayaan: bisa KPR'), ('Facing matches', 'Hadap sesuai'),
        ('Lease term fits (minimum 2 years)', 'Masa sewa sesuai (minimal 2 tahun)'), ('Bedrooms match', 'Jumlah kamar tidur sesuai'),
        ('Price within budget', 'Harga masuk budget'), ('Rent price within budget', 'Harga sewa masuk budget'),
        ('Rp 8,5 jt/m² × 120 m² ≈ Rp 1,02 M — within budget', 'Rp 8,5 jt/m² × 120 m² ≈ Rp 1,02 M — masuk budget'),
        ('Needs check: price Rp 2,1 M is 5% above the budget Rp 2 M (within tolerance)',
         'Harga Rp 2,1 M 5% di atas budget Rp 2 M (masih dalam rentang Warm)'),
        ('Needs check: rent price Rp 70 jt is 30% below the minimum budget Rp 100 jt', 'Harga sewa Rp 70 jt 30% di bawah budget minimal Rp 100 jt'),
        ('Needs check: tower not stated — buyer only accepts Tower A or C', 'Tower tidak disebutkan — buyer hanya mau Tower A atau C'),
        ('Needs check: legal status not stated — buyer only accepts SHM', 'Status legal tidak disebutkan — buyer hanya mau SHM'),
        ('Needs check: unit type not stated — buyer wants 2BR', 'Tipe unit tidak disebutkan — buyer mau 2BR'),
        ('Needs check: floor level not stated (buyer wants floor 10 or lower)', 'Level lantai tidak disebutkan (buyer mau lantai 10 atau lebih rendah)'),
        ('Needs check: minimum lease not stated (renter wants 6 months)', 'Masa sewa minimal tidak disebutkan (penyewa mau 6 bulan)'),
        ('Needs check: facing not stated', 'Hadap tidak disebutkan'),
        ('Needs check: move-in ready not confirmed', 'Siap huni belum pasti'),
        ('Needs check: buyer named only the city; exact area not confirmed', 'Buyer hanya menyebut kota; area persisnya belum pasti'),
        ('Needs check: land area 90 m² is below the requested 100–120 m² (within tolerance)',
         'Luas tanah 90 m² di bawah permintaan 100–120 m² (masih dalam rentang Warm)'),
        ('Needs check: buyer approval for a nearby location (within 4 km)', 'Perlu persetujuan buyer untuk lokasi terdekat (dalam 4 km)'),
        ('Location not confirmed — buyer only accepts Citraland', 'Lokasi belum pasti — buyer hanya mau Citraland'),
        ('Needs check: parking availability and terms', 'Ketersediaan dan syarat parkir'),
        ('Needs check: water source ("sumber air") not confirmed', 'Sumber air ("sumber air") belum pasti'),
        ('Purpose: buy (not confirmed)', 'Tujuan belum pasti (beli)'),
    ]

    def test_known_reasons_read_in_indonesian(self):
        for english, expected in self.CASES:
            with self.subTest(english=english):
                positive, checks = split_reasons([english])
                self.assertEqual([indonesian(text) for text in positive + checks], [expected])

    def test_internal_lines_are_hidden_and_unknown_lines_stay_english(self):
        explanation = ['Purpose: rent ✓', 'Asset category matches: house', 'Location 100%', 'Land area: 100%',
                       'Building area: not requested (no score)', 'Price: 100%', 'Needs check: a brand new kind of check']
        self.assertEqual(callout_items({'explanation': explanation}),
                         [('positive', 'Tujuan: sewa'), ('check', 'A brand new kind of check')])
        self.assertEqual(callout_items(None), [])


class PairPageTests(unittest.TestCase):
    def page(self, explanation, buyer='Cari rumah Baruk budget 2M', listing='Dijual rumah Baruk Barat LT 120 harga 1,7 M'):
        source = {'raw_text': buyer, 'contact_phone': '081111111111'}
        target = {'raw_text': listing, 'contact_phone': '082222222222', 'status': 'warm', 'score': 75, 'explanation': explanation}
        return PdfReader(io.BytesIO(build_report([(source, target)], 'buyer', section='Andi', first_pair=7))).pages[0]

    def test_callout_bullets_under_the_status_bar_and_highlighted_text_still_reads(self):
        _, _, result = assessed('Cari rumah Baruk budget 2M', 'Dijual rumah Baruk Barat LT 120 harga 1,7 M')
        # A needs-check line is appended so the callout shows both kinds of bullet.
        page = self.page(result['explanation'] + ['Needs check: parking availability and terms'])
        text = page.extract_text()
        for words in ('No. 7', 'Alasan direkomendasikan', 'Tujuan: beli', 'Unit: rumah', 'Lokasi sama: Baruk', 'Harga masuk budget',
                      'Perlu dicek:', 'Cari rumah Baruk budget 2M', 'Dijual rumah Baruk Barat LT 120 harga 1,7 M'):
            self.assertIn(words, text)
        self.assertNotIn('Location 100%', text)
        self.assertNotIn('Asset category', text)
        self.assertNotIn('|', text.split('\n')[-1])  # the page number is stamped when the parts are merged
        positions = {}

        def inspect(found, cm, tm, font, size):
            for key in ('Hot', 'Warm', 'Tujuan', 'Buyer request', 'Cari rumah'):
                if found.startswith(key):
                    positions.setdefault(key, (tm[5] * cm[3] + cm[5], size))
        page.extract_text(visitor_text=inspect)
        self.assertGreater(positions['Warm'][0], positions['Tujuan'][0])         # bar, then reasons,
        self.assertGreater(positions['Tujuan'][0], positions['Buyer request'][0])  # then the column labels,
        self.assertGreater(positions['Buyer request'][0], positions['Cari rumah'][0])  # then the messages
        self.assertEqual(positions['Cari rumah'][1], BODY_FONT_SIZE)

    def test_many_reasons_shrink_the_callout_and_the_text_still_fits(self):
        many = [f'Needs check: requirement number {n} not confirmed' for n in range(40)] + ['Purpose: buy ✓']
        long_listing = '\n'.join(f'Baris {i}: rumah Baruk, harga 1,7 M' for i in range(80)) + '\nAKHIR'
        page = self.page(many, listing=long_listing)
        text = page.extract_text()
        self.assertIn('number 39', text)
        self.assertIn('AKHIR', text)
        baselines = []

        def inspect(found, cm, tm, font, size):
            if found.strip():
                baselines.append(tm[5] * cm[3] + cm[5])
        page.extract_text(visitor_text=inspect)
        # Nothing runs into the WhatsApp button: the messages shrink to the room the callout leaves.
        self.assertEqual([y for y in baselines if BUTTON_Y + 25 < y < BODY_BOTTOM - 0.5], [])
        self.assertLess(CALLOUT_MAX_HEIGHT, STATUS_BAR_Y - BODY_BOTTOM - 200)


class MergeAndNamingTests(unittest.TestCase):
    def test_parts_of_a_person_merge_in_section_order_and_number_per_person(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        for name, count in (('00000-b00000000', 1), ('00000-a00000000', 2), ('00000-a00000025', 1), ('00001-b00000000', 2)):
            writer = PdfWriter()
            for _ in range(count):
                writer.add_blank_page(width=595, height=842)
            writer.add_metadata({'/Title': name})
            writer.write(folder / f'{name}.pdf')
        sections = {'property': 3, 'buyer': 1}
        for index, key in enumerate(['~ Andi', '~ Citra']):
            (folder / f'{index:05d}.json').write_text(json.dumps({
                'key': key, 'title': key[2:], 'group_by': 'sender', 'contact_name': '', 'phone': '6281234567890', 'sources': 4,
                'direction': 'both', 'combined': True, 'sections': sections if index == 0 else {'buyer': 2}, 'first_page': 1}))
        with patch('export_all.folder', return_value=folder):
            response = download(uuid.uuid4(), output='grouped')
        self.assertIn('Semua-Pencocokan-Dua-Arah.zip', response.headers['content-disposition'])
        with ZipFile(io.BytesIO(response.body)) as archive:
            self.assertEqual(archive.namelist(), ['01_Andi_6281234567890_3listing_1buyer_hal1-4.pdf',
                                                  '02_Citra_6281234567890_2buyer_hal1-2.pdf'])
            first = PdfReader(io.BytesIO(archive.read(archive.namelist()[0])))
            self.assertEqual([page.extract_text().strip() for page in first.pages],
                             [f'Property Matchmaker | Hal. {n} dari 4' for n in range(1, 5)])
        self.assertEqual(group_filename(4, {'key': '628', 'group_by': 'phone', 'contact_name': 'Sari', 'phone': '628',
                                            'sources': 2, 'direction': 'buyer'}, 3), '05_Sari_628_2buyer_hal1-3.pdf')
        content, count = merge_pdf_parts(sorted(folder.glob('00000-*.pdf')), sorted(folder.glob('00001-*.pdf')))
        self.assertEqual(count, 6)
        self.assertEqual([page.extract_text().strip()[-13:] for page in PdfReader(io.BytesIO(content)).pages],
                         ['Hal. 1 dari 4', 'Hal. 2 dari 4', 'Hal. 3 dari 4', 'Hal. 4 dari 4', 'Hal. 1 dari 2', 'Hal. 2 dari 2'])


    def test_status_badge_leaves_room_for_the_longest_status_text(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from report import STATUS_BARS
        badge = stringWidth('No. 999', 'Helvetica-Bold', 11) + 24
        for _, _, text in STATUS_BARS.values():
            self.assertLess(MARGIN + 40 + stringWidth(text, 'Helvetica-Bold', 12), PAGE_WIDTH - MARGIN - badge)

    def test_workflow_exports_support_optional_both_directions(self):
        from integration import ExportRequest
        self.assertFalse(ExportRequest(request_id='x').combined)
        self.assertTrue(ExportRequest(request_id='x', combined=True).combined)


class CombinedExportTests(V4Case):
    """Match page: one export with a Listing → Buyer and a Buyer → Listing section per person."""
    def seed(self):
        return self.run_import([(LISTING_HOUSE_A, '~ Andi'), (LISTING_HOUSE_B, '~ Andi'), (BUYER_RUKO, '~ Andi'),
                                (LISTING_RUKO, '~ Citra'), (BUYER_HOUSE, '~ Citra'),
                                ('Cari rumah Surabaya Barat LT 100 budget 2,1 M', '~ Dodi')])

    def plan(self, **filters):
        return self.call(self.staff, '/export/all/plan', 'POST', {'statuses': 'hot,warm,custom', 'combined': True, **filters})

    def fetch(self, path):
        import urllib.request
        with self.staff.open(urllib.request.Request(self.base + path), timeout=30) as response:
            return response.read()

    def export(self, output='single', **filters):
        """Drive the export the way the browser does: per person, per section, slice by slice."""
        plan = self.plan(**filters)
        for index, group in enumerate(plan['groups']):
            page, pair = 1, 1
            for section in group['sections']:
                offset = 0
                while offset is not None:
                    done = self.call(self.staff, '/export/all/part', 'POST', {
                        'statuses': 'hot,warm,custom', **filters, 'combined': True, 'direction': section['direction'],
                        'group_by': plan['group_by'], 'token': plan['token'], 'index': index, 'group_key': group['key'],
                        'offset': offset, 'first_page': page, 'first_pair': pair})
                    page, pair, offset = page + done['pages'], pair + done['pairs'], done['next_offset']
        return plan, self.fetch(f"/export/all/{plan['token']}/download?output={output}")

    def test_plan_has_both_sections_per_person_with_counts_per_direction(self):
        self.seed()
        plan = self.plan()
        self.assertTrue(plan['combined'])
        people = {group['key']: group for group in plan['groups']}
        self.assertEqual(set(people), {'~ Andi', '~ Citra', '~ Dodi'})
        self.assertEqual([s['direction'] for s in people['~ Andi']['sections']], ['property', 'buyer'])
        self.assertEqual([s['direction'] for s in people['~ Dodi']['sections']], ['buyer'])
        self.assertEqual((people['~ Andi']['covers'], people['~ Dodi']['covers']), (2, 1))
        # Each section counts exactly what the one-direction export of that person counts.
        for direction in ('property', 'buyer'):
            single = {g['key']: g for g in self.call(self.staff, '/export/all/plan', 'POST',
                                                     {'statuses': 'hot,warm,custom', 'direction': direction})['groups']}
            for key, person in people.items():
                section = next((s for s in person['sections'] if s['direction'] == direction), None)
                self.assertEqual(section and (section['sources'], section['pages']),
                                 (single[key]['sources'], single[key]['pages']) if key in single else None, (key, direction))
        self.assertEqual(plan['totals']['covers'], 5)
        self.assertEqual(plan['totals']['pages'], sum(g['pages'] for g in plan['groups']))
        self.assertEqual([g['key'] for g in self.plan(group_keys=['~ Dodi'])['groups']], ['~ Dodi'])
        self.call(self.staff, '/export/all/plan', 'POST', {'combined': True, 'recent': True}, status=400)

    def test_single_pdf_two_coloured_sections_numbering_and_reasons(self):
        self.seed()
        plan, pdf = self.export()
        pages = PdfReader(io.BytesIO(pdf)).pages
        texts = [page.extract_text() for page in pages]
        self.assertEqual(len(pages), plan['totals']['pages'] + plan['totals']['covers'])
        andi = next(i for i, g in enumerate(plan['groups']) if g['key'] == '~ Andi')
        start = sum(g['pages'] + g['covers'] for g in plan['groups'][:andi])
        person = plan['groups'][andi]
        listing_pairs, buyer_pairs = (s['pages'] for s in person['sections'])
        total = person['pages'] + 2
        own = texts[start:start + total]
        # Section 1: dark blue cover, Listing → Buyer; section 2: sky blue cover, Buyer → Listing.
        self.assertIn('BAGIAN 1 DARI 2', own[0])
        self.assertLess(own[0].index('Listing'), own[0].index('Buyer'))
        self.assertRegex(fill_colors(pages[start]), DARK_BLUE)
        cover_two = start + 1 + listing_pairs
        self.assertIn('BAGIAN 2 DARI 2', texts[cover_two])
        self.assertLess(texts[cover_two].index('Buyer'), texts[cover_two].index('Listing'))
        self.assertRegex(fill_colors(pages[cover_two]), SKY_BLUE)
        self.assertNotRegex(fill_colors(pages[cover_two]), DARK_BLUE)
        self.assertIn('Andi', own[0])
        self.assertIn(f'{listing_pairs} halaman pasangan', own[0])
        # Pair numbers run on across both sections of the person; page numbers count the person's pages only.
        numbers = [int(m.group(1)) for text in own for m in [re.search(r'\bNo\. (\d+)', text)] if m]
        self.assertEqual(numbers, list(range(1, person['pages'] + 1)))
        for n, text in enumerate(own, 1):
            self.assertIn(f'Hal. {n} dari {total}', text)
        for group_start in [sum(g['pages'] + g['covers'] for g in plan['groups'][:i]) for i in range(len(plan['groups']))]:
            self.assertIn('Hal. 1 dari', texts[group_start])
            self.assertIn('BAGIAN 1 DARI', texts[group_start])
            self.assertIn('No. 1', texts[group_start + 1])
        # Pair pages explain the match in Indonesian.
        self.assertIn('Alasan direkomendasikan', own[1])
        self.assertIn('Tujuan: beli', own[1])
        self.assertTrue(any('Harga masuk budget' in text or 'Perlu dicek:' in text for text in own[1:]))
        self.assertNotIn('Location 100%', '\n'.join(texts))
        self.assertGreaterEqual(buyer_pairs, 1)

    def test_zip_has_one_file_per_person_each_from_page_one(self):
        self.seed()
        plan, archive = self.export(output='grouped')
        with ZipFile(io.BytesIO(archive)) as bundle:
            names = bundle.namelist()
            self.assertEqual(len(names), len(plan['groups']))
            for index, (name, group) in enumerate(zip(names, plan['groups']), 1):
                pages = PdfReader(io.BytesIO(bundle.read(name))).pages
                count = group['pages'] + group['covers']
                self.assertEqual(len(pages), count)
                self.assertTrue(name.startswith(f'{index:02d}_') and name.endswith(f'_hal1-{count}.pdf'), name)
                self.assertIn(f'Hal. 1 dari {count}', pages[0].extract_text())
                self.assertIn(f'Hal. {count} dari {count}', pages[-1].extract_text())
                self.assertIn('No. 1', pages[1].extract_text())
        andi = next(name for name in names if '_Andi_' in name)
        self.assertRegex(andi, r'_2listing_1buyer_hal1-\d+\.pdf$')


if __name__ == '__main__':
    unittest.main()
