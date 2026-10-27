"""v4.2 pipeline over real HTTP + PostgreSQL: one WhatsApp bubble with several requests/listings
becomes one document (entity, public ID, matches) per part; settings expose the tolerance model."""
import re
import unittest
from io import BytesIO
from unittest.mock import patch

import parser as bubble_parser
from harness import ServerTestCase

SIGNATURE = 'Wiwik Konig property\n087851480789'
WIWIK_PHONE = '6287851480789'
WAREHOUSE_PART = '#Buyer 1\nDicari gudang untuk beli\nArea Waru Sidoarjo\nLuas 300-500m2\nBudget max 5M'
HOUSE_PART = '#Buyer 2\nDicari rumah untuk disewa\nArea Rungkut Surabaya Timur\nLT min 300m2\nBudget sewa 60 - 120 jt / th'
SHOP_PART = '#Buyer 3\nDicari ruko untuk beli\nArea Darmo\nLT 100\nBudget 3M'
BUBBLE = f'{WAREHOUSE_PART}\n\n{HOUSE_PART}\n\n{SIGNATURE}'
BUBBLE3 = f'{WAREHOUSE_PART}\n\n{HOUSE_PART}\n\n{SHOP_PART}\n\n{SIGNATURE}'
LISTING_WAREHOUSE = 'Dijual gudang Waru Sidoarjo LT 400 m2 SHM Harga 4,5 M\nHubungi Budi 081234567890'
LISTING_RENT_HOUSE = 'Disewakan rumah Rungkut Surabaya Timur LT 350 m2 akses truk Harga 100 jt/th\nInfo Sari 6282233744657'
# User example A (one bubble, two requests) as it arrived from WhatsApp.
EXAMPLE_A = '''#Buyer 1
Dicari gudang (untuk beli)
wilayah surabaya ke arah waru, tidak sampe margomulyo atau dkp
IMB gudang
Luas 300-500m2
Penyimpanan tinner
Budget max 5M

#Renter 1
Rumah
LT min 300m2
Area lebak, rungkut, darmahusada, ngagel, kenjeran , mulyosari
Peruntukan: kantor & gudang mesin (genst, bor)
Wajib akses yang bisa masuk truk
Prefer hadap selatan
Sewa min 3 tahun
Budget 60 - 120 jt / th

Wiwik Konig property
087851480789'''


def marker_bubble(text, author=None, glossary=None, max_parts=12, order=None):
    """Deterministic stand-in for parser.parse_bubble: one part per '#…' section (the last one takes
    the rest when ``max_parts`` is reached), every part parsed with the shared signature."""
    body, signature = bubble_parser.split_contact(text)
    blocks = [b.strip() for b in re.split(r'(?m)^(?=#)', body) if b.strip()]
    if len(blocks) > max_parts:
        blocks = blocks[:max_parts - 1] + ['\n\n'.join(blocks[max_parts - 1:])]
    if len(blocks) < 2:
        part = bubble_parser.parse_message(text, author, glossary)
        part.segment_index, part.segment_count, part.segment_text = 0, 1, None
        return [part]
    if order:
        blocks = [blocks[i] for i in order]
    parts = []
    for index, block in enumerate(blocks):
        part = bubble_parser.parse_message(block + '\n\n' + signature, author, glossary)
        part.segment_index, part.segment_count, part.segment_text = index, len(blocks), block
        parts.append(part)
    return parts


def splitter(**options):
    return patch.object(bubble_parser, 'parse_bubble', lambda text, author=None, glossary=None:
                        marker_bubble(text, author, glossary, **options), create=True)


class PipelineCase(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.user, self.boss = self.make_company('V42')
        self.scope = self.company['company_id']

    def run_import(self, messages, **options):
        job = self.upload(self.user, messages=messages, agent='Wiwik')
        with splitter(**options):
            self.process(job)
        return job

    def rows(self, direction='buyer'):
        return self.call(self.boss, f'/workspace?direction={direction}')['rows']

    def recs(self, source_id, direction='buyer'):
        return self.call(self.boss, '/workspace/recommendations', 'POST',
                         {'direction': direction, 'ids': [source_id]})['groups'][0]['recommendations']

    def documents(self, raw_text, active_only=True):
        from db import connect
        with connect() as conn:
            return conn.execute(
                '''SELECT d.*, e.public_id, r.classification AS raw_classification FROM xm.documents d
                   JOIN xm.raw_messages r ON r.id = d.raw_message_id LEFT JOIN xm.entities e ON e.entity_id = d.entity_id
                   WHERE d.company_id = %s AND r.raw_text = %s''' + (' AND d.active' if active_only else '') +
                ' ORDER BY r.sent_at, d.segment_index', (self.scope, raw_text)).fetchall()

    def reindex(self, **options):
        import matcher
        import reindex
        from tenant import workspace_scope
        inactive = []
        with workspace_scope(self.scope), splitter(**options), patch.object(reindex, 'upsert', lambda batch: len(batch)), \
                patch.object(reindex, '_request', lambda method, path, body: inactive.extend(body['points'])), \
                patch.object(matcher, 'qdrant_query', lambda *args, **kwargs: []):
            reindex.reindex_documents()
            matcher.recompute_matches(self.scope)
        return inactive


class MultiContextBubbleTests(PipelineCase):
    def test_one_bubble_with_two_requests_becomes_two_documents_with_their_own_ids_and_matches(self):
        job = self.run_import([(BUBBLE, '~ Wiwik'), (LISTING_WAREHOUSE, '~ Budi'), (LISTING_RENT_HOUSE, '~ Sari')])
        docs = self.documents(BUBBLE)
        self.assertEqual([d['segment_index'] for d in docs], [0, 1])
        self.assertEqual({d['segment_count'] for d in docs}, {2})
        self.assertEqual([d['segment_text'] for d in docs], [WAREHOUSE_PART, HOUSE_PART])
        self.assertEqual({d['contact_phone'] for d in docs}, {WIWIK_PHONE})        # same owner
        self.assertEqual(len({d['contact_name'] for d in docs}), 1)
        self.assertEqual([d['document_type'] for d in docs], ['buyer_request', 'buyer_request'])
        self.assertEqual([d['transaction_type'] for d in docs], ['sale', 'rent'])
        self.assertEqual([d['categories'] for d in docs], [['warehouse'], ['house']])
        self.assertEqual({d['raw_classification'] for d in docs}, {'buyer_request'})
        self.assertEqual(len({d['public_id'] for d in docs}), 2)
        self.assertEqual(len({d['qdrant_point_id'] for d in docs}), 2)
        from db import connect
        with connect() as conn:
            status = conn.execute('SELECT * FROM xm.imports WHERE id=%s', (job['id'],)).fetchone()
        self.assertEqual((status['request_count'], status['listing_count'], status['processed_messages'], status['ignored_count']),
                         (2, 2, 3, 0))

        buyers = self.rows('buyer')
        self.assertEqual(len(buyers), 2)
        by_part = {row['segment_text']: row for row in buyers}
        for row in buyers:
            self.assertEqual(row['raw_text'], BUBBLE)   # "Show original message" keeps the whole bubble
        warehouse, house = by_part[WAREHOUSE_PART], by_part[HOUSE_PART]
        self.assertNotEqual(warehouse['public_id'], house['public_id'])
        warehouse_targets = [r['raw_text'] for r in self.recs(warehouse['id'])]
        house_targets = [r['raw_text'] for r in self.recs(house['id'])]
        self.assertIn(LISTING_WAREHOUSE, warehouse_targets)
        self.assertNotIn(LISTING_RENT_HOUSE, warehouse_targets)
        self.assertIn(LISTING_RENT_HOUSE, house_targets)
        self.assertNotIn(LISTING_WAREHOUSE, house_targets)
        # The listing side sees one buyer per part, not the whole bubble twice.
        listing = next(row for row in self.rows('property') if row['raw_text'] == LISTING_WAREHOUSE)
        self.assertEqual([r.get('segment_text') for r in self.recs(listing['id'], 'property')], [WAREHOUSE_PART])

    def test_reposting_the_bubble_groups_each_part_with_its_twin(self):
        self.run_import([(BUBBLE, '~ Wiwik', '2026-09-01T10:00:00'), (LISTING_WAREHOUSE, '~ Budi', '2026-09-02T10:00:00'),
                         (BUBBLE, '~ Wiwik', '2026-09-03T10:00:00'), (LISTING_RENT_HOUSE, '~ Sari', '2026-09-04T10:00:00')])
        self.assertEqual(len(self.documents(BUBBLE)), 4)
        buyers = self.rows('buyer')
        self.assertEqual(sorted(row['segment_text'] for row in buyers), sorted([WAREHOUSE_PART, HOUSE_PART]))
        self.assertEqual({row['duplicate_count'] for row in buyers}, {2})
        self.assertEqual(len({row['public_id'] for row in buyers}), 2)
        docs = self.documents(BUBBLE)
        ids = {}
        for doc in docs:
            ids.setdefault(doc['segment_text'], set()).add(doc['public_id'])
        self.assertEqual({key: len(value) for key, value in ids.items()}, {WAREHOUSE_PART: 1, HOUSE_PART: 1})
        listing = next(row for row in self.rows('property') if row['raw_text'] == LISTING_WAREHOUSE)
        targets = self.recs(listing['id'], 'property')
        self.assertEqual([(r['segment_text'], r['duplicate_count']) for r in targets], [(WAREHOUSE_PART, 2)])
        stats = self.call(self.boss, '/stats')
        self.assertEqual((stats['buyer_requests'], stats['listings']), (2, 2))

    def test_reindex_after_a_parser_change_keeps_ids_of_unchanged_parts(self):
        self.run_import([(BUBBLE3, '~ Wiwik'), (LISTING_WAREHOUSE, '~ Budi')], max_parts=2)
        before = self.documents(BUBBLE3)
        merged = f'{HOUSE_PART}\n\n{SHOP_PART}'
        self.assertEqual([d['segment_text'] for d in before], [WAREHOUSE_PART, merged])
        warehouse_id, merged_doc = before[0]['id'], before[1]['id']
        warehouse_public, merged_public = before[0]['public_id'], before[1]['public_id']

        # The parser now finds three parts: the unchanged first part keeps its document and public ID.
        self.assertEqual(self.reindex(), [])
        after = self.documents(BUBBLE3)
        self.assertEqual([d['segment_text'] for d in after], [WAREHOUSE_PART, HOUSE_PART, SHOP_PART])
        self.assertEqual({d['segment_count'] for d in after}, {3})
        self.assertEqual((after[0]['id'], after[0]['public_id']), (warehouse_id, warehouse_public))
        self.assertEqual(after[1]['id'], merged_doc)                     # same position continues
        self.assertNotIn(after[1]['public_id'], (warehouse_public, merged_public))  # but it is a new text
        self.assertEqual(len({d['public_id'] for d in after}), 3)
        self.assertEqual({d['contact_phone'] for d in after}, {WIWIK_PHONE})
        ids = {d['segment_text']: (d['id'], d['public_id']) for d in after}

        # Parts found in another order keep their documents and IDs by text.
        self.assertEqual(self.reindex(order=[2, 0, 1]), [])
        reordered = self.documents(BUBBLE3)
        self.assertEqual([d['segment_text'] for d in reordered], [SHOP_PART, WAREHOUSE_PART, HOUSE_PART])
        self.assertEqual({d['segment_text']: (d['id'], d['public_id']) for d in reordered}, ids)

        # Fewer parts again: vanished parts are deactivated (and their vectors), never deleted.
        inactive = self.reindex(max_parts=2)
        back = self.documents(BUBBLE3)
        self.assertEqual([d['segment_text'] for d in back], [WAREHOUSE_PART, merged])
        self.assertEqual((back[0]['id'], back[0]['public_id']), (warehouse_id, warehouse_public))
        self.assertEqual(back[1]['public_id'], merged_public)           # the merged text's ID comes back
        everything = self.documents(BUBBLE3, active_only=False)
        self.assertEqual(len(everything), 4)
        self.assertEqual(len({(d['raw_message_id'], d['segment_index']) for d in everything}), 4)
        self.assertEqual(len(inactive), 2)
        self.assertEqual(len(self.rows('buyer')), 2)

    def test_a_whole_bubble_marked_sold_keeps_its_status_when_reprocessing_splits_it(self):
        # v4.1 stored the bubble as one document; the warehouse part was also posted on its own (still ready).
        self.run_import([(BUBBLE, '~ Wiwik'), (WAREHOUSE_PART, '~ Wiwik'), (LISTING_WAREHOUSE, '~ Budi'),
                         (LISTING_RENT_HOUSE, '~ Sari')], max_parts=1)
        whole = self.documents(BUBBLE)
        self.assertEqual([(d['segment_count'], d['segment_text']) for d in whole], [(1, None)])
        self.call(self.boss, '/entities/status', 'POST', {'ids': [whole[0]['public_id']], 'status': 'sold', 'note': 'Closed in Sept'})

        self.reindex()
        parts = self.documents(BUBBLE)
        self.assertEqual([d['segment_text'] for d in parts], [WAREHOUSE_PART, HOUSE_PART])
        self.assertNotIn(whole[0]['public_id'], [d['public_id'] for d in parts])
        from db import connect
        with connect() as conn:
            entities = {row['public_id']: row for row in conn.execute(
                'SELECT public_id, status, status_note FROM xm.entities WHERE company_id=%s', (self.scope,)).fetchall()}
            carried = conn.execute(
                "SELECT entity_id, details FROM xm.audit_events WHERE company_id=%s AND event_type='entity_status' AND details ? 'carried_from'",
                (self.scope,)).fetchall()
        warehouse, house = (entities[d['public_id']] for d in parts)
        self.assertEqual((house['status'], house['status_note']), ('sold', 'Closed in Sept'))
        self.assertEqual(warehouse['status'], 'ready')       # an existing ID keeps its own status
        self.assertEqual([(row['entity_id'], row['details']['carried_from']) for row in carried],
                         [(parts[1]['public_id'], whole[0]['public_id'])])
        # Sold parts are not offered as cards; nothing changes on the next reprocess.
        self.assertEqual([row.get('segment_text') or row['raw_text'] for row in self.rows('buyer')], [WAREHOUSE_PART])
        self.reindex()
        again = self.documents(BUBBLE)
        self.assertEqual([d['public_id'] for d in again], [d['public_id'] for d in parts])
        with connect() as conn:
            self.assertEqual([conn.execute('SELECT status FROM xm.entities WHERE public_id=%s AND company_id=%s',
                                           (d['public_id'], self.scope)).fetchone()['status'] for d in again], ['ready', 'sold'])

    def test_every_part_of_a_split_bubble_continues_its_on_hold_status(self):
        self.run_import([(BUBBLE3, '~ Wiwik'), (LISTING_WAREHOUSE, '~ Budi')], max_parts=1)
        whole = self.documents(BUBBLE3)
        self.call(self.boss, '/entities/status', 'POST', {'ids': [whole[0]['public_id']], 'status': 'on_hold'})
        self.reindex()
        parts = self.documents(BUBBLE3)
        self.assertEqual(len(parts), 3)
        from db import connect
        with connect() as conn:
            statuses = [conn.execute('SELECT status FROM xm.entities WHERE public_id=%s AND company_id=%s',
                                     (d['public_id'], self.scope)).fetchone()['status'] for d in parts]
        self.assertEqual(statuses, ['on_hold'] * 3)
        self.assertEqual(self.rows('buyer'), [])

    @unittest.skipUnless(hasattr(bubble_parser, 'parse_bubble'), 'parser.parse_bubble not available yet')
    def test_user_example_a_with_the_real_parser(self):
        job = self.upload(self.user, messages=[(EXAMPLE_A, '~ Wiwik Konig')], agent='Wiwik')
        self.process(job)
        docs = self.documents(EXAMPLE_A)
        self.assertEqual(len(docs), 2)
        self.assertEqual({d['contact_phone'] for d in docs}, {WIWIK_PHONE})
        self.assertEqual(len({d['public_id'] for d in docs}), 2)
        self.assertEqual([d['document_type'] for d in docs], ['buyer_request', 'buyer_request'])
        self.assertEqual([d['transaction_type'] for d in docs], ['sale', 'rent'])
        self.assertTrue(all(d['segment_text'] and '087851480789' not in d['segment_text'] for d in docs))


class SettingsTests(PipelineCase):
    def test_tolerances_and_radius_are_saved_validated_and_mirrored(self):
        settings = self.call(self.boss, '/settings')
        self.assertEqual({key: float(settings[key]) for key in ('price_down_pct', 'price_up_pct', 'land_down_pct', 'land_up_pct',
                                                                 'building_down_pct', 'building_up_pct', 'location_radius_km')},
                         {'price_down_pct': 15, 'price_up_pct': 15, 'land_down_pct': 15, 'land_up_pct': 15,   # v4.3 Warm allowances
                          'building_down_pct': 15, 'building_up_pct': 15, 'location_radius_km': 4})
        saved = self.call(self.boss, '/settings', 'PUT', {'price_down_pct': 40, 'price_up_pct': 10, 'land_down_pct': 5,
                                                          'land_up_pct': 15, 'building_down_pct': 25, 'building_up_pct': 30,
                                                          'location_radius_km': 2.5})
        # price_floor_pct keeps its R3 meaning (lower bound = budget × floor %): 100 − price_down_pct.
        expect = {'price_down_pct': 40, 'price_up_pct': 10, 'land_down_pct': 5, 'land_up_pct': 15, 'building_down_pct': 25,
                  'building_up_pct': 30, 'location_radius_km': 2.5, 'price_tolerance_pct': 10, 'price_floor_pct': 60,
                  'land_tolerance_pct': 15, 'building_tolerance_pct': 30, 'location_weight_pct': 35}
        for source in (saved, self.call(self.boss, '/settings')):
            self.assertEqual({key: float(source[key]) for key in expect}, expect)
        for bad in ({'location_radius_km': 4.5}, {'location_radius_km': -1}, {'price_up_pct': 101}, {'land_down_pct': -5}):
            self.call(self.boss, '/settings', 'PUT', bad, status=422)
        self.call(self.boss, '/settings', 'PUT', {'location_weight_pct': 50}, status=400)   # weights must add up to 100
        member, staff = self.make_member(self.boss)
        self.call(staff, '/settings', 'PUT', {'price_up_pct': 20}, status=403)
        # An older client echoes everything and changes one single tolerance: both directions follow it.
        current = self.call(self.boss, '/settings')
        self.call(self.boss, '/settings', 'PUT', {**current, 'land_tolerance_pct': 12})
        after = self.call(self.boss, '/settings')
        self.assertEqual((float(after['land_down_pct']), float(after['land_up_pct']), float(after['land_tolerance_pct'])), (12, 12, 12))
        self.assertEqual((float(after['price_down_pct']), float(after['building_up_pct'])), (40, 30))

    def test_the_same_payload_saved_twice_stores_the_same_values(self):
        keys = ('price_down_pct', 'price_up_pct', 'land_down_pct', 'land_up_pct', 'building_down_pct', 'building_up_pct',
                'price_floor_pct', 'price_tolerance_pct', 'land_tolerance_pct', 'building_tolerance_pct')
        snapshot = self.call(self.boss, '/settings')
        # An older client sends its whole GET body back with one single tolerance changed.
        payload = {**snapshot, 'land_tolerance_pct': float(snapshot['land_tolerance_pct']) + 1}
        first = self.call(self.boss, '/settings', 'PUT', payload)
        second = self.call(self.boss, '/settings', 'PUT', payload)
        self.assertEqual({key: float(first[key]) for key in keys}, {key: float(second[key]) for key in keys})
        edited = float(snapshot['land_tolerance_pct']) + 1
        self.assertEqual((float(second['land_down_pct']), float(second['land_up_pct'])), (edited, edited))
        # Mixed payloads: the new fields decide; a single tolerance that equals their mirror is only an echo.
        mixed = {'land_down_pct': 5, 'land_up_pct': 15, 'land_tolerance_pct': 15, 'price_down_pct': 30, 'price_floor_pct': 70}
        for _ in range(2):
            saved = self.call(self.boss, '/settings', 'PUT', mixed)
            self.assertEqual(tuple(float(saved[key]) for key in ('land_down_pct', 'land_up_pct', 'price_down_pct', 'price_floor_pct')),
                             (5, 15, 30, 70))
        # The R3 floor from an older client: lower bound = 60 % of the budget = 40 % below it.
        saved = self.call(self.boss, '/settings', 'PUT', {'price_floor_pct': 60})
        self.assertEqual((float(saved['price_down_pct']), float(saved['price_floor_pct'])), (40, 60))
        # A single tolerance sent with only one of its two new fields fills the other one.
        saved = self.call(self.boss, '/settings', 'PUT', {'building_down_pct': 5, 'building_tolerance_pct': 25})
        self.assertEqual((float(saved['building_down_pct']), float(saved['building_up_pct'])), (5, 25))
        again = self.call(self.boss, '/settings', 'PUT', {'building_down_pct': 5, 'building_tolerance_pct': 25})
        self.assertEqual((float(again['building_down_pct']), float(again['building_up_pct'])), (5, 25))

    def test_saving_tolerances_marks_results_out_of_date(self):
        self.run_import([(BUBBLE, '~ Wiwik'), (LISTING_WAREHOUSE, '~ Budi')])
        self.assertFalse(self.call(self.boss, '/reprocess/status')['pending'])
        current = self.call(self.boss, '/settings')
        self.call(self.boss, '/settings', 'PUT', current)                   # nothing changed
        self.assertFalse(self.call(self.boss, '/reprocess/status')['pending'])
        self.call(self.boss, '/settings', 'PUT', {'location_radius_km': 1})
        status = self.call(self.boss, '/reprocess/status')
        self.assertTrue(status['pending'])
        self.assertIn('Matching settings changed', status['reasons'])

    def test_location_radius_is_read_from_settings_and_capped(self):
        from matcher import location_radius
        self.assertEqual([location_radius(value) for value in ({'location_radius_km': 2.5}, {'location_radius_km': 9},
                                                                {'location_radius_km': None}, None, {'location_radius_km': 0})],
                         [2.5, 4.0, 4.0, 4.0, 0.0])


class ReportPartTests(unittest.TestCase):
    def test_pdf_prints_the_part_with_its_label_and_the_signature(self):
        from pypdf import PdfReader
        from report import build_report, message_text, part_label
        source = {'raw_text': BUBBLE, 'segment_text': HOUSE_PART, 'segment_index': 1, 'segment_count': 2, 'public_id': 'B-AA001'}
        target = {'raw_text': LISTING_RENT_HOUSE, 'segment_text': None, 'segment_count': 1, 'public_id': 'L-AA000',
                  'status': 'hot', 'score': 90, 'contact_phone': '6282233744657', 'contact_name': 'Sari'}
        self.assertEqual(part_label(source), 'Bagian 2 dari 2')
        self.assertIsNone(part_label(target))
        self.assertEqual(message_text(target), LISTING_RENT_HOUSE)
        self.assertEqual(message_text(source), HOUSE_PART + '\n\n' + SIGNATURE)
        text = PdfReader(BytesIO(build_report([(source, target)], 'buyer'))).pages[0].extract_text()
        self.assertIn('Bagian 2 dari 2', text)
        self.assertIn('Rungkut', text)
        self.assertIn('087851480789', text)
        self.assertNotIn('gudang untuk beli', text)


if __name__ == '__main__':
    unittest.main()
