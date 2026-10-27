"""Inspect exported PDFs, including pagination, text preservation and link placement."""
from io import BytesIO
import unittest
from urllib.parse import unquote

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from report import (build_report, BODY_FONT_SIZE, BODY_BOTTOM, BODY_TOP, BUTTON_Y, LABEL_Y, MARGIN, PAGE_WIDTH, STATUS_BARS,
                    STATUS_BAR_HEIGHT, STATUS_BAR_Y, fit_paragraph, pair_status)
from reportlab.lib.pagesizes import A4


def listing(text, phone='081234567890', score=85):
    return {'raw_text': text, 'normalized_text': text, 'contact_phone': phone, 'score': score}


@unittest.skipUnless(PdfReader, 'install api/requirements-test.txt to inspect PDF output')
class ReportPDFTests(unittest.TestCase):
    def read(self, pairs, direction='buyer'):
        return PdfReader(BytesIO(build_report(pairs, direction)))

    def test_exactly_one_a4_portrait_page_per_selected_pair(self):
        long_text = '\n'.join(f'Baris {i:03d}: rumah Surabaya Barat, luas 120 m2, harga Rp 2 M.' for i in range(100))
        pdf = self.read([(listing('Buyer singkat'), listing('Listing singkat')),
                         (listing(long_text + '\nAKHIR SUMBER'), listing(long_text + '\nAKHIR TARGET')),
                         (listing('Buyer tanpa pasangan'), None)])
        self.assertEqual(len(pdf.pages), 3)
        for page in pdf.pages:
            self.assertAlmostEqual(float(page.mediabox.width), A4[0], places=3)
            self.assertAlmostEqual(float(page.mediabox.height), A4[1], places=3)
        self.assertIn('AKHIR SUMBER', pdf.pages[1].extract_text())
        self.assertIn('AKHIR TARGET', pdf.pages[1].extract_text())
        self.assertIn('Baris 099', pdf.pages[1].extract_text())
        self.assertIn('Belum ada kecocokan.', pdf.pages[2].extract_text())

    def test_body_defaults_to_11pt_and_shrinks_only_the_long_column(self):
        long_text = '\n'.join(f'LONG {i}: Semua informasi listing harus tetap tercetak.' for i in range(90))
        page = self.read([(listing('SHORT body stays eleven'), listing(long_text))]).pages[0]
        sizes = {'short': set(), 'long': set()}

        def inspect(text, cm, tm, font, size):
            if 'SHORT' in text: sizes['short'].add(size)
            if 'LONG' in text: sizes['long'].add(size)

        page.extract_text(visitor_text=inspect)
        self.assertEqual(sizes['short'], {11})
        self.assertTrue(sizes['long'])
        self.assertTrue(all(0 < size < 11 for size in sizes['long']))

    def test_only_right_hand_contact_is_linked_in_both_directions(self):
        for direction in ['buyer', 'property']:
            with self.subTest(direction=direction):
                page = self.read([(listing('SUMBER', '081111111111'), listing('TARGET', '082222222222'))], direction).pages[0]
                links = [a.get_object() for a in page.get('/Annots', [])]
                self.assertEqual(len(links), 1)
                self.assertGreater(float(links[0]['/Rect'][0]), A4[0] / 2)
                self.assertIn('wa.me/628222222222', links[0]['/A']['/URI'])
                message = unquote(links[0]['/A']['/URI'])
                self.assertIn('listing properti' if direction == 'buyer' else 'kebutuhan properti', message)
                self.assertEqual(page.extract_text().count('WhatsApp:'), 1)
                self.assertNotIn('628111111111', page.extract_text())

    def test_status_is_one_full_width_bar_at_the_top_of_every_pair_page(self):
        cases = [({**listing('Hot target'), 'status': 'hot'}, 'Hot', 'kecocokan kuat'),
                 ({**listing('Warm target', score=70), 'status': 'warm'}, 'Warm', 'perlu dicek'),
                 ({**listing('Custom target', score=79), 'status': 'custom'}, 'Custom', 'syarat wajib buyer perlu dicek manual'),
                 (listing('Legacy row without a stored status', score=85), 'Hot', 'kecocokan kuat'),
                 (None, 'Belum cocok', 'Belum cocok')]
        pdf = self.read([(listing('Buyer'), target) for target, _, _ in cases])
        for page, (_, label, words) in zip(pdf.pages, cases):
            with self.subTest(label=label, words=words):
                found = []

                def inspect(text, cm, tm, font, size):
                    if words in text:
                        found.append((text, tm[5] if tm[5] else cm[5]))

                page.extract_text(visitor_text=inspect)
                self.assertEqual(len(found), 1, found)
                text, y = found[0]
                self.assertTrue(text.strip().startswith(label))
                # Inside the bar, below the logo rule and above both column labels; nothing at the bottom any more.
                self.assertGreater(y, STATUS_BAR_Y)
                self.assertLess(y, STATUS_BAR_Y + STATUS_BAR_HEIGHT)
                self.assertGreater(STATUS_BAR_Y, LABEL_Y + 32)
                self.assertLess(STATUS_BAR_Y + STATUS_BAR_HEIGHT, A4[1] - 85)
        self.assertEqual(pdf.pages[4].extract_text().count('Belum cocok'), 1)

    def test_pair_status_prefers_the_stored_status(self):
        self.assertEqual(pair_status({'status': 'custom', 'score': 79}), 'custom')
        self.assertEqual(pair_status({'status': 'warm', 'score': 90}), 'warm')
        self.assertEqual(pair_status({'score': 80}), 'hot')
        self.assertEqual(pair_status({'score': 65}), 'warm')
        self.assertEqual(pair_status(None), 'unmatched')

    def test_no_green_contact_for_missing_target_phone_or_unmatched_source(self):
        for target in [None, listing('No phone', '')]:
            page = self.read([(listing('Source has phone'), target)]).pages[0]
            self.assertEqual(len(page.get('/Annots', [])), 0)
            self.assertNotIn('WhatsApp:', page.extract_text())


class ReportLayoutTests(unittest.TestCase):
    def test_regions_do_not_overlap(self):
        self.assertLess(LABEL_Y + 32, STATUS_BAR_Y)          # column labels sit under the status bar
        self.assertLess(BODY_TOP, LABEL_Y)                   # text starts under the labels
        self.assertGreater(BODY_BOTTOM, BUTTON_Y + 25)       # and ends above the WhatsApp button
        self.assertGreaterEqual(BODY_TOP - BODY_BOTTOM, 577 - 0.5)  # as much room for text as before the bar

    def test_status_bar_texts_stay_indonesian_and_fit_inside_the_bar(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        self.assertEqual({status: text for status, (_, _, text) in STATUS_BARS.items()},
                         {'hot': 'Hot \u2014 kecocokan kuat', 'warm': 'Warm \u2014 perlu dicek',
                          'custom': 'Custom \u2014 syarat wajib buyer perlu dicek manual', 'unmatched': 'Belum cocok'})
        for _, _, text in STATUS_BARS.values():
            # Text starts after the icon (40 pt in) and must end before the bar's right edge.
            self.assertLess(40 + stringWidth(text, 'Helvetica-Bold', 12), PAGE_WIDTH - 2 * MARGIN - 10)

    def test_long_text_fits_above_actions_without_truncation_or_overflow(self):
        for text in ['Short listing', '\n'.join(['Long listing content.'] * 180), 'X' * 12000]:
            paragraph, height = fit_paragraph(text)
            self.assertLessEqual(height, BODY_TOP - BODY_BOTTOM)
            self.assertLessEqual(paragraph.style.fontSize, BODY_FONT_SIZE)
            self.assertGreater(paragraph.style.fontSize, 0)
            # ReportLab records each line's remaining width; negative would overflow.
            self.assertGreaterEqual(min(line.extraSpace for line in paragraph.blPara.lines) if paragraph.blPara.kind == 1 else
                                    min(line[0] for line in paragraph.blPara.lines), -0.01)


if __name__ == '__main__':
    unittest.main()
