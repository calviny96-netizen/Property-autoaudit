"""Hand-picked pairs from the Match page, across sources and both directions, grouped per person."""
import io
import re
import unittest
import uuid
from zipfile import ZipFile

from pypdf import PdfReader

from test_export_combined import DARK_BLUE, SKY_BLUE, fill_colors
from test_v4_features import BUYER_HOUSE, BUYER_RUKO, LISTING_HOUSE_A, LISTING_HOUSE_B, LISTING_RUKO, V4Case


class SelectionExportTests(V4Case):
    def seed(self):
        self.run_import([(LISTING_HOUSE_A, '~ Andi'), (LISTING_HOUSE_B, '~ Andi'), (BUYER_RUKO, '~ Andi'),
                         (LISTING_RUKO, '~ Citra'), (BUYER_HOUSE, '~ Citra')])
        rows = {row['raw_text']: row for direction in ('buyer', 'property') for row in self.rows(direction=direction)}
        return {text: rows[text] for text in (LISTING_HOUSE_A, LISTING_HOUSE_B, BUYER_RUKO, LISTING_RUKO, BUYER_HOUSE)}

    def pair(self, rows, direction, source, target):
        targets = self.recs(rows[source]['id'], direction)
        match = next(row for row in targets if row['raw_text'] == target)
        return {'direction': direction, 'source_id': rows[source]['id'], 'target_id': match['id']}

    def selection(self, rows):
        # Picked in a "hunting" order: switching sources and directions back and forth.
        return [self.pair(rows, 'buyer', BUYER_HOUSE, LISTING_HOUSE_B), self.pair(rows, 'property', LISTING_HOUSE_A, BUYER_HOUSE),
                self.pair(rows, 'property', LISTING_RUKO, BUYER_RUKO), self.pair(rows, 'buyer', BUYER_RUKO, LISTING_RUKO),
                self.pair(rows, 'property', LISTING_HOUSE_B, BUYER_HOUSE), self.pair(rows, 'buyer', BUYER_HOUSE, LISTING_HOUSE_A)]

    def test_zip_has_one_file_per_person_with_both_sections(self):
        rows = self.seed()
        pairs = self.selection(rows)
        archive = self.call(self.staff, '/export/selection', 'POST', {'pairs': pairs + pairs[:1]})  # a repeat counts once
        with ZipFile(io.BytesIO(archive)) as bundle:
            names = bundle.namelist()
            # Andi: 2 listings → buyers + 1 buyer → listing; Citra: 1 listing + 1 buyer (with two listings).
            self.assertEqual(len(names), 2)
            self.assertRegex(names[0], r'^01_Andi_\d+_2listing_1buyer_hal1-5\.pdf$')
            self.assertRegex(names[1], r'^02_Citra_\d+_1listing_1buyer_hal1-5\.pdf$')
            for name in names:
                pages = PdfReader(io.BytesIO(bundle.read(name))).pages
                texts = [page.extract_text() for page in pages]
                self.assertEqual(len(pages), 5)
                for n, text in enumerate(texts, 1):
                    self.assertIn(f'Hal. {n} dari 5', text)
                listing_pairs = 2 if name.startswith('01_') else 1
                cover_two = 1 + listing_pairs
                self.assertIn('BAGIAN 1 DARI 2', texts[0])
                self.assertRegex(fill_colors(pages[0]), DARK_BLUE)
                self.assertIn(f'{listing_pairs} halaman pasangan', texts[0])
                self.assertIn('BAGIAN 2 DARI 2', texts[cover_two])
                self.assertRegex(fill_colors(pages[cover_two]), SKY_BLUE)
                self.assertNotRegex(fill_colors(pages[cover_two]), DARK_BLUE)
                numbers = [int(m.group(1)) for text in texts for m in [re.search(r'\bNo\. (\d+)', text)] if m]
                self.assertEqual(numbers, [1, 2, 3])
                self.assertIn('Alasan direkomendasikan', texts[1])
            citra = [page.extract_text() for page in PdfReader(io.BytesIO(bundle.read(names[1]))).pages]
            self.assertIn('Citra', citra[0])
            # Citra's buyer has two listings: both follow the second cover, in the list order of the app.
            listing_ids = [re.search(r'Property listing \| (\S+)', text).group(1) for text in citra[3:5]]
            order = [row['public_id'] for row in self.recs(rows[BUYER_HOUSE]['id'], 'buyer', target_status=['ready', 'on_hold'])]
            self.assertEqual(listing_ids, [pid for pid in order if pid in listing_ids])

    def test_single_pdf_restarts_page_numbers_per_person(self):
        rows = self.seed()
        pdf = self.call(self.staff, '/export/selection', 'POST', {'pairs': self.selection(rows), 'output': 'single'})
        texts = [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]
        self.assertEqual(len(texts), 10)
        self.assertEqual([re.search(r'Hal\. (\d+) dari (\d+)', text).groups() for text in texts],
                         [(str(n), '5') for n in range(1, 6)] * 2)
        self.assertIn('Andi', texts[0])
        self.assertIn('Citra', texts[5])

    def test_pairs_outside_the_workspace_or_changed_are_refused(self):
        rows = self.seed()
        pair = self.pair(rows, 'buyer', BUYER_HOUSE, LISTING_HOUSE_A)
        self.call(self.staff, '/export/selection', 'POST', {'pairs': [{**pair, 'source_id': str(uuid.uuid4())}]}, status=404)
        self.call(self.staff, '/export/selection', 'POST', {'pairs': [{**pair, 'target_id': str(uuid.uuid4())}]}, status=409)
        # A source of the other direction is not found as a buyer.
        self.call(self.staff, '/export/selection', 'POST', {'pairs': [{**pair, 'source_id': rows[LISTING_RUKO]['id']}]}, status=404)
        self.call(self.staff, '/export/selection', 'POST', {'pairs': [pair] * 201}, status=422)
        # Another company cannot export these pairs.
        _, _, other = self.make_company('Other')
        self.call(other, '/export/selection', 'POST', {'pairs': [pair]}, status=404)


if __name__ == '__main__':
    unittest.main()
