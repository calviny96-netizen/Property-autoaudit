"""Structured glossary API: defaults, validation, saving (pending reprocess) and suggestions."""
import unittest
from unittest.mock import patch

from harness import ServerTestCase

LISTING_CL = 'Dijual rumah CL blok A\nLT 100 LB 80\nHarga 2 M\nHub Budi 081234567890'
LISTING_KENARI = 'Dijual rumah\nLokasi: Taman Kenari Asri\nLT 100 LB 80\nHarga 1,5 M\nHub Budi 0812-3456-7890'
LISTING_KENARI_2 = 'Disewakan rumah\nLokasi: Taman Kenari Asri, strategis\nLT 120\nHarga 100 jt/tahun\nInfo Sinta 081398765432'
LISTING_CITRALAND = 'Dijual rumah\nLokasi: Citraland\nLT 100\nHarga 2 M'
LOCATION_CL = {'term': 'CL', 'canonical': 'citraland', 'kind': 'location', 'parent': 'surabaya barat', 'note': 'Agent shorthand'}


class GlossaryRoutesTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.owner, self.boss = self.make_company('Glossary')
        self.member, self.staff = self.make_member(self.boss)

    # -- helpers -------------------------------------------------------------
    def save(self, entries, client=None, status=200):
        return self.call(client or self.boss, '/glossary/entries', 'PUT', {'entries': entries}, status=status)

    def error(self, entries):
        return self.save(entries, status=400)['detail']

    def jobs(self):
        from db import connect
        with connect() as conn:
            return conn.execute('SELECT * FROM xm.maintenance_jobs WHERE company_id=%s ORDER BY created_at',
                                (self.company['company_id'],)).fetchall()

    def documents(self):
        from db import connect
        with connect() as conn:
            return {r['raw_text']: r for r in conn.execute(
                'SELECT d.*, r.raw_text FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id '
                'WHERE d.company_id=%s AND d.active', (self.company['company_id'],)).fetchall()}

    def reprocess_now(self):
        """Admin presses Reprocess; the worker runs the job without Qdrant."""
        import matcher
        import reindex
        import worker
        self.call(self.boss, '/reprocess', 'POST', status=202)
        with patch.object(reindex, 'upsert', lambda batch: len(batch)), \
                patch.object(matcher, 'qdrant_query', lambda *args, **kwargs: []):
            self.assertTrue(worker.process_maintenance())

    # -- reading -------------------------------------------------------------
    def test_entries_list_defaults_and_english_categories(self):
        data = self.call(self.staff, '/glossary/entries')
        self.assertEqual(data['entries'], [])
        self.assertIn({'term': 'sby', 'canonical': 'surabaya', 'kind': 'synonym', 'parent': None}, data['defaults'])
        self.assertIn({'term': 'citraland', 'canonical': 'citraland', 'kind': 'location', 'parent': 'surabaya barat'},
                      data['defaults'])
        self.assertIn({'term': 'surabaya barat', 'canonical': 'surabaya barat', 'kind': 'location', 'parent': 'surabaya'},
                      data['defaults'])
        labels = {c['key']: c['label'] for c in data['categories']}
        self.assertEqual(labels, {'warehouse': 'Warehouse', 'factory': 'Factory', 'shophouse': 'Shophouse', 'villa': 'Villa',
                                  'land': 'Land', 'apartment': 'Apartment', 'office': 'Office', 'hotel': 'Hotel',
                                  'house': 'House', 'commercial_building': 'Commercial building'})

    def test_property_terms_are_a_read_only_grouped_list(self):
        data = self.call(self.staff, '/glossary/terms')
        groups = {group['key']: group for group in data['groups']}
        self.assertEqual(list(groups), ['legal', 'unit_type', 'tower', 'financing', 'floor', 'condition', 'other'])
        terms = {key: {term['term'] for term in group['terms']} for key, group in groups.items()}
        self.assertTrue({'SHM', 'HGB / SHGB', 'Surat Ijo', 'SHMRS / SHSRS / strata', 'PPJB', 'AJB', 'Petok D / Girik',
                         'HPL'} <= terms['legal'])
        self.assertEqual([term['term'] for term in groups['unit_type']['terms']], ['Studio', '1BR', '2BR', '3BR', 'Penthouse'])
        self.assertTrue({'KPR', 'KPA', 'Cash keras', 'Cash bertahap'} <= terms['financing'])
        # Legal titles and unit types follow the keys the parser reads.
        import parser
        for key, attribute in (('legal', 'LEGAL_LABELS'), ('unit_type', 'UNIT_TYPE_LABELS')):
            labels = getattr(parser, attribute, None)
            if labels:
                keys = {term['key'] for term in groups[key]['terms']}
                self.assertTrue(keys <= set(labels), (key, keys - set(labels)))
        self.assertTrue({'Low floor', 'High floor'} <= terms['floor'])
        self.assertTrue({'KT', 'KM', 'LT', 'LB', 'Hook', 'Row jalan', 'Nett', 'BU'} <= terms['other'])
        aliases = {term['term']: term['aliases'] for group in data['groups'] for term in group['terms']}
        self.assertIn('IPT', aliases['Surat Ijo'])
        self.assertIn('SHM strata', aliases['SHMRS / SHSRS / strata'])
        for group in data['groups']:
            self.assertTrue(group['label'] and group['terms'])
            for term in group['terms']:
                with self.subTest(term=term['term']):
                    self.assertEqual(set(term), {'key', 'term', 'aliases', 'meaning'})
                    self.assertTrue(0 < len(term['meaning']) <= 200 and term['meaning'].endswith('.'))
                    self.assertIsInstance(term['aliases'], list)
        self.assertEqual(self.call(self.boss, '/glossary/terms'), data)          # the same for every role
        self.call(self.boss, '/glossary/terms', 'PUT', {'groups': []}, status=405)  # built-in, not editable

    # -- permissions ---------------------------------------------------------
    def test_members_can_read_but_only_company_admins_can_save(self):
        self.save([LOCATION_CL], client=self.staff, status=403)
        self.assertEqual(self.call(self.staff, '/glossary/entries')['entries'], [])
        self.call(self.staff, '/glossary/suggestions')
        saved = self.save([LOCATION_CL])
        self.assertEqual(saved['entries'], [{'term': 'cl', 'canonical': 'citraland', 'kind': 'location',
                                             'parent': 'surabaya barat', 'note': 'Agent shorthand'}])
        self.assertEqual(self.call(self.staff, '/glossary/entries')['entries'], saved['entries'])

    # -- validation ----------------------------------------------------------
    def test_validation_errors_are_clear_and_save_nothing(self):
        self.assertIn('Duplicate term "cl"', self.error([LOCATION_CL, {'term': ' cl ', 'canonical': 'citra land'}]))
        self.assertIn('the type must be synonym, location or category', self.error([{'term': 'x1', 'canonical': 'y', 'kind': 'place'}]))
        self.assertIn('choose one of these categories', self.error([{'term': 'kost', 'canonical': 'boarding', 'kind': 'category'}]))
        self.assertIn('Commercial building', self.error([{'term': 'kost', 'canonical': 'boarding', 'kind': 'category'}]))
        self.assertIn('Row 1: the term needs 1–100 characters', self.error([{'term': ' ', 'canonical': 'y'}]))
        self.assertIn('the term needs 1–100 characters', self.error([{'term': 'x' * 101, 'canonical': 'y'}]))
        self.assertIn('the standard name needs 1–100 characters', self.error([{'term': 'abc', 'canonical': ''}]))
        self.assertIn('the standard name needs 1–100 characters', self.error([{'term': 'abc', 'canonical': 'y' * 101}]))
        self.assertIn('the parent area can have at most 100 characters',
                      self.error([{'term': 'abc', 'canonical': 'abc', 'kind': 'location', 'parent': 'p' * 101}]))
        self.assertIn('at most 2,000', self.error([{'term': f't{i}', 'canonical': 'x'} for i in range(2001)]))
        self.assertIn('cannot be inside itself',
                      self.error([{'term': 'abc', 'canonical': 'abc', 'kind': 'location', 'parent': 'ABC'}]))
        loop = self.error([{'term': 'alpha', 'canonical': 'alpha', 'kind': 'location', 'parent': 'beta'},
                           {'term': 'beta', 'canonical': 'beta', 'kind': 'location', 'parent': 'alpha'}])
        self.assertIn('the parent areas form a loop', loop)
        # A company entry may not turn the built-in hierarchy into a loop either.
        self.assertIn('loop', self.error([{'term': 'surabaya', 'canonical': 'surabaya', 'kind': 'location', 'parent': 'citraland'}]))
        self.assertIn('Use one parent area per place',
                      self.error([{'term': 'cl', 'canonical': 'citraland', 'kind': 'location', 'parent': 'wiyung'},
                                  {'term': 'citra', 'canonical': 'citraland', 'kind': 'location', 'parent': 'surabaya timur'}]))
        self.assertEqual(self.call(self.boss, '/glossary/entries')['entries'], [])

    def test_saving_normalises_and_replaces_all_rows(self):
        first = self.save([LOCATION_CL, {'term': 'Kost', 'canonical': 'House', 'kind': 'category', 'parent': 'ignored'},
                           {'term': 'tower x', 'canonical': 'Commercial Building', 'kind': 'category'},
                           {'alias': 'rgcy', 'canonical': 'Regency'}])
        self.assertEqual([(e['term'], e['canonical'], e['kind'], e['parent']) for e in first['entries']], [
            ('cl', 'citraland', 'location', 'surabaya barat'), ('kost', 'house', 'category', None),
            ('rgcy', 'regency', 'synonym', None), ('tower x', 'commercial_building', 'category', None)])
        self.assertIsNone(first['job'])
        # Legacy dict API keeps working on the same rows.
        self.assertEqual(self.call(self.staff, '/glossary')['cl'], 'citraland')
        second = self.save([{'term': 'pci', 'canonical': 'pakuwon city', 'kind': 'location', 'parent': 'surabaya timur'}])
        self.assertEqual([e['term'] for e in second['entries']], ['pci'])
        self.assertEqual(self.call(self.staff, '/glossary'), {'pci': 'pakuwon city'})
        self.assertEqual(self.save([])['entries'], [])

    # -- pending reprocess ---------------------------------------------------
    def test_saving_marks_results_out_of_date_without_starting_a_rebuild(self):
        self.save([LOCATION_CL])  # no data yet: nothing becomes out of date
        self.assertFalse(self.call(self.staff, '/reprocess/status')['pending'])
        self.process(self.upload(self.owner, texts=[LISTING_CL]))
        saved = self.save([{**LOCATION_CL, 'note': 'Changed note only'}])
        self.assertFalse(saved['changed'])  # notes never change parsing
        self.assertFalse(self.call(self.staff, '/reprocess/status')['pending'])
        saved = self.save([LOCATION_CL, {'term': 'kost', 'canonical': 'house', 'kind': 'category'}])
        self.assertTrue(saved['changed'])
        self.assertIsNone(saved['job'])
        self.assertEqual((saved['reprocess']['pending'], saved['reprocess']['reasons']), (True, ['Glossary updated']))
        self.assertEqual(self.jobs(), [])
        self.save([LOCATION_CL])
        self.assertEqual(self.call(self.staff, '/reprocess/status')['reasons'], ['Glossary updated'])  # deduplicated
        self.reprocess_now()
        status = self.call(self.staff, '/reprocess/status')
        self.assertFalse(status['pending'])
        self.assertEqual(status['job']['status'], 'completed')

    def test_change_saved_while_a_reprocess_runs_stays_pending_after_it(self):
        import matcher
        import reindex
        import worker
        self.process(self.upload(self.owner, texts=[LISTING_CL]))
        self.save([LOCATION_CL])
        self.call(self.boss, '/reprocess', 'POST', status=202)
        saved_during_run = []

        def reindex_and_save():
            # The job already read the glossary; an admin saves another change meanwhile.
            count = original()
            saved_during_run.append(self.save([LOCATION_CL, {'term': 'kost', 'canonical': 'house', 'kind': 'category'}]))
            return count
        original = reindex.reindex_documents
        with patch.object(reindex, 'reindex_documents', reindex_and_save), patch.object(reindex, 'upsert', lambda b: len(b)), \
                patch.object(matcher, 'qdrant_query', lambda *args, **kwargs: []):
            self.assertTrue(worker.process_maintenance())
        self.assertTrue(saved_during_run[0]['reprocess']['pending'])
        status = self.call(self.staff, '/reprocess/status')
        self.assertEqual(status['job']['status'], 'completed')
        self.assertTrue(status['pending'])
        self.assertEqual(status['reasons'], ['Glossary updated'])
        self.assertEqual([j['status'] for j in self.jobs()], ['completed'])  # the next run waits for the admin

    # -- effect on parsing ---------------------------------------------------
    def test_location_entry_changes_parse_results_after_reprocess(self):
        from parser import location_ancestors, parse_message
        self.process(self.upload(self.owner, texts=[LISTING_CL, 'Dijual kost 10 kamar LT 200 Harga 3 M']))
        before = self.documents()[LISTING_CL]
        self.assertNotIn('citraland', before['locations'])
        entries = self.save([LOCATION_CL, {'term': 'kost', 'canonical': 'house', 'kind': 'category'}])['entries']
        # The saved rows are exactly what ingest/reindex hand to the parser.
        from db import connect
        from reindex import glossary_entries
        from tenant import workspace_scope
        with workspace_scope(self.company['company_id']), connect() as conn:
            glossary = glossary_entries(conn)
        self.assertEqual(glossary, entries)
        self.assertIn('citraland', parse_message(LISTING_CL, glossary=glossary).locations)
        self.assertEqual(location_ancestors('cl', glossary), ['surabaya barat', 'surabaya'])
        self.reprocess_now()
        after = self.documents()
        self.assertIn('citraland', after[LISTING_CL]['locations'])
        self.assertIn('citraland', after[LISTING_CL]['normalized_text'])
        self.assertEqual(after[LISTING_CL]['id'], before['id'])
        self.assertIn('house', after['Dijual kost 10 kamar LT 200 Harga 3 M']['categories'])
        # New uploads use the saved glossary right away.
        self.process(self.upload(self.owner, texts=['Dijual ruko CL LT 80 Harga 3 M'], day=10))
        self.assertIn('citraland', self.documents()['Dijual ruko CL LT 80 Harga 3 M']['locations'])

    # -- suggestions -----------------------------------------------------------
    def test_suggestions_list_unknown_places_with_clean_samples(self):
        self.process(self.upload(self.owner, texts=[LISTING_KENARI, LISTING_KENARI_2, LISTING_CITRALAND]))
        items = self.call(self.staff, '/glossary/suggestions')['items']
        phrases = {item['phrase']: item for item in items}
        self.assertIn('taman kenari asri', phrases)
        kenari = phrases['taman kenari asri']
        self.assertEqual((kenari['count'], kenari['kind_hint']), (2, 'location'))
        self.assertIn('Taman Kenari Asri', kenari['sample'])
        self.assertLessEqual(len(kenari['sample']), 160)
        self.assertNotRegex(kenari['sample'].replace(' ', '').replace('-', ''), r'\d{8,}')
        for known in ('citraland', 'strategis'):
            self.assertNotIn(known, phrases)
        self.assertEqual(len(self.call(self.staff, '/glossary/suggestions?limit=1')['items']), 1)
        # A glossary place is no longer a suggestion ...
        self.save([{'term': 'tka', 'canonical': 'taman kenari asri', 'kind': 'location', 'parent': 'wiyung'}])
        self.assertNotIn('taman kenari asri', {i['phrase'] for i in self.call(self.staff, '/glossary/suggestions')['items']})
        # ... and neither is a cluster alias in the location index.
        self.save([])
        self.assertIn('taman kenari asri', {i['phrase'] for i in self.call(self.staff, '/glossary/suggestions')['items']})
        self.call(self.boss, '/location-index/clusters', 'POST', {'name': 'Kenari Asri Residence', 'aliases': ['Taman Kenari Asri'], 'area': ''})
        self.assertNotIn('taman kenari asri', {i['phrase'] for i in self.call(self.staff, '/glossary/suggestions')['items']})


if __name__ == '__main__':
    unittest.main()
