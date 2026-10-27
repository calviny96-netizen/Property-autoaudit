"""v4.10: one public ID per message content (signature, case, emoji and punctuation do not count).

Unit tests of ``parser.content_key`` plus PostgreSQL tests of grouping, ID merges, status and search."""
import unittest

from harness import ServerTestCase
from parser import content_hash, content_key, parse_bubble

# The live pair B-AA417 / B-AA418: one request, two signatures.
LIVE_BODY = ('DICARI RUMAH DI GRAHA FAMILI SURABAYA\n\nBudget: 10 - 15 Milyar CASH READY\n'
             'Client serius, sudah siap survey kapan saja.\n\nKriteria: Luas, layak huni, dan siap transaksi.\n\n'
             'Bapak/Ibu yang punya listing atau tetangga mau jual,\n')
LIVE_A = LIVE_BODY + 'Mohon infonya 🙏\nHay\nXmpc\n085100582515'
LIVE_B = LIVE_BODY + 'Mohon infonya hub: 081234581811 [ mr mario ]'

BUYER_1 = 'Buyer request rumah KONIG Surabaya Barat LT 100 Budget 2 M\nMohon infonya 🙏\nHay\nXmpc\n085100582515'
BUYER_2 = 'Buyer request rumah Konig Surabaya Barat LT 100 Budget 2 M\nMohon infonya hub: 081234581811 [ mr mario ]'
LISTING_1 = 'Dijual rumah Surabaya Barat LT 100 Harga 1,8 M\nContact: Budi 081234567890'
LISTING_2 = 'DIJUAL RUMAH SURABAYA BARAT LT 100 HARGA 1,8 M 🏡\nHubungi: Sari 6282233744657'
SARI, MARIO = '6282233744657', '6281234581811'


class ContentKeyTests(unittest.TestCase):
    def test_live_pair_with_two_signatures_is_one_key(self):
        self.assertEqual(content_key(LIVE_A), content_key(LIVE_B))
        self.assertEqual(content_hash(LIVE_A), content_hash(LIVE_B))
        self.assertIn('graha famili', content_key(LIVE_A))
        self.assertIn('10 15 milyar', content_key(LIVE_A))
        self.assertNotIn('0851', content_key(LIVE_A))

    def test_case_emoji_punctuation_and_spacing_do_not_count(self):
        self.assertEqual(content_key('Cari rumah KONIG Citraland'), content_key('cari rumah Konig  citraland 🏠!!'))
        self.assertEqual(content_key(BUYER_1), content_key(BUYER_2))
        self.assertEqual(content_key('LT120 budget 2,5M'), content_key('lt 120 Budget 2.5 m'))
        self.assertEqual(content_key(LISTING_1), content_key(LISTING_2))
        self.assertEqual(content_key('*Dijual* _ruko_ ~Manyar~ 5 M\nhttps://wa.me/6281234567890 @budi_property'),
                         content_key('Dijual ruko Manyar 5 M'))

    def test_another_budget_or_place_is_another_key(self):
        self.assertNotEqual(content_key('Cari rumah Citraland budget 2 M'), content_key('Cari rumah Citraland budget 3 M'))
        self.assertNotEqual(content_key('Cari rumah Citraland budget 2 M'), content_key('Cari rumah Pakuwon budget 2 M'))
        self.assertNotEqual(content_key(LIVE_A), content_key(LIVE_A.replace('10 - 15', '10 - 20')))
        # A price is never read as a phone number ("1.080.000.000" holds "080.000.000").
        self.assertNotEqual(content_key('Harga 1.080.000.000'), content_key('Harga 1.082.000.000'))

    def test_empty_content_falls_back_to_the_text(self):
        self.assertEqual(content_key('🙏🙏'), '')
        self.assertNotEqual(content_hash('🙏'), content_hash('🏠'))

    def test_parser_stores_the_key_of_the_bubble_or_part(self):
        self.assertEqual(parse_bubble(LIVE_A)[0].content_key, content_hash(LIVE_A))
        parts = parse_bubble('#Buyer 1\nCari rumah Citraland budget 2 M\n\n#Buyer 2\nCari ruko Manyar budget 5 M\n\nHub Budi 081234567890')
        if len(parts) > 1:
            self.assertEqual([part.content_key for part in parts], [content_hash(part.segment_text) for part in parts])
            self.assertEqual(len({part.content_key for part in parts}), len(parts))


class ContentGroupTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.user, self.boss = self.make_company('Content key')

    def import_messages(self, messages, agent='Caesar'):
        job = self.upload(self.user, messages=messages, agent=agent)
        self.process(job)
        return job

    def rows(self, direction='buyer', **query):
        from urllib.parse import urlencode
        return self.call(self.boss, f'/workspace?{urlencode({"direction": direction, **query})}')['rows']

    def test_same_body_other_signature_and_casing_is_one_card_with_the_oldest_id(self):
        self.import_messages([(LIVE_A, '~ Hay'), (LIVE_B, '~ Mario'), (BUYER_1, '~ Hay'), (BUYER_2, '~ Mario'),
                              (LISTING_1, '~ Budi'), (LISTING_2, '~ Sari')])
        buyers = self.rows('buyer')
        self.assertEqual(sorted(row['public_id'] for row in buyers), ['B-AA000', 'B-AA001'])
        for row in buyers:
            self.assertEqual(row['duplicate_count'], 2)
        live = next(row for row in buyers if 'GRAHA' in row['raw_text'])
        self.assertEqual(live['public_id'], 'B-AA000')          # numbered by the first posting
        self.assertIn('mr mario', live['raw_text'])              # the card shows the latest posting
        listings = self.rows('property')
        self.assertEqual([(row['public_id'], row['duplicate_count']) for row in listings], [('L-AA000', 2)])
        # Recommendations: one card for the listing posted twice, with both copies counted.
        house = next(row for row in buyers if 'KONIG' in row['raw_text'].upper() and 'GRAHA' not in row['raw_text'])
        group = self.call(self.boss, '/workspace/recommendations', 'POST', {'direction': 'buyer', 'ids': [house['id']]})['groups'][0]
        self.assertEqual([(rec['public_id'], rec['duplicate_count']) for rec in group['recommendations']], [('L-AA000', 2)])
        # Recent matches: one pair, one target card.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo('Asia/Jakarta')).date().isoformat()
        recent = self.call(self.boss, f'/matches/recent?direction=buyer&date_from={today}&date_to={today}')
        self.assertEqual(recent['totals']['pairs'], 1)
        self.assertEqual([m['target']['public_id'] for g in recent['groups'] for m in g['matches']], ['L-AA000'])
        self.assertEqual(recent['groups'][0]['matches'][0]['target']['duplicate_count'], 2)
        # Stats count contents, not postings.
        stats = self.call(self.boss, '/stats')
        self.assertEqual((stats['buyer_requests'], stats['listings']), (2, 1))

    def test_any_copy_signed_by_the_source_makes_the_listing_own(self):
        # The buyer is Mario's; the listing card shows Sari's latest copy, but an older copy carries Mario's number.
        self.import_messages([(BUYER_2, '~ Mario'),
                              ('Dijual rumah Surabaya Barat LT 100 Harga 1,8 M\nContact: Mario 081234581811', '~ Mario'),
                              (LISTING_2, '~ Sari')])
        buyer = self.rows('buyer')[0]
        recs = self.call(self.boss, '/workspace/recommendations', 'POST', {'direction': 'buyer', 'ids': [buyer['id']]})['groups'][0]['recommendations']
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['contact_phone'], SARI)
        self.assertEqual(recs[0]['ownership_tier'], 'own')

    def legacy_ids(self):
        """Rebuild the pre-v4.10 state: no content keys, one entity per exact text (IDs by first posting)."""
        from db import connect
        from entities import assign_entities
        from tenant import workspace_scope
        company = self.company['company_id']
        with workspace_scope(company), connect() as conn:
            for table in ('match_events', 'group_matches', 'document_group_members', 'document_groups', 'workspace_cache_state'):
                conn.execute(f'DELETE FROM xm.{table} WHERE company_id=%s', (company,))
            conn.execute('UPDATE xm.documents SET content_key=NULL, entity_id=NULL WHERE company_id=%s', (company,))
            conn.execute('DELETE FROM xm.entities WHERE company_id=%s', (company,))
            conn.execute('DELETE FROM xm.entity_counters WHERE company_id=%s', (company,))
            assign_entities(conn, company, rekey=False)
            ids = conn.execute('''SELECT r.raw_text, e.public_id FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
                                  JOIN xm.entities e ON e.entity_id=d.entity_id WHERE d.company_id=%s''', (company,)).fetchall()
            conn.commit()
        return {row['raw_text']: row['public_id'] for row in ids}

    def test_existing_ids_merge_into_the_oldest_and_keep_the_strictest_status(self):
        from db import connect
        from matcher import recompute_matches
        from tenant import workspace_scope
        company = self.company['company_id']
        self.import_messages([(BUYER_1, '~ Hay'), (BUYER_2, '~ Mario'), (LISTING_1, '~ Budi'), (LISTING_2, '~ Sari')])
        before = self.legacy_ids()
        self.assertEqual((before[BUYER_1], before[BUYER_2]), ('B-AA000', 'B-AA001'))
        self.assertEqual((before[LISTING_1], before[LISTING_2]), ('L-AA000', 'L-AA001'))
        # The newer copy was disabled; history and receipts point at both old IDs.
        with workspace_scope(company), connect() as conn:
            entities = {row['public_id']: row['entity_id'] for row in conn.execute(
                'SELECT public_id, entity_id FROM xm.entities WHERE company_id=%s', (company,)).fetchall()}
            self.assertEqual(len(entities), 4)
            conn.execute('''INSERT INTO xm.match_events(company_id, buyer_entity, listing_entity, first_score, last_score, temperature,
                                                        last_status, source, found_at, last_seen_at)
                            SELECT %s, b, l, 80, 80, 'hot', 'hot', 'import', now() - interval '1 day', now() - interval '1 day'
                            FROM unnest(%s::uuid[], %s::uuid[]) AS p(b, l)
                            ON CONFLICT DO NOTHING''',
                         (company, [entities['B-AA000'], entities['B-AA001'], entities['B-AA001']],
                          [entities['L-AA000'], entities['L-AA000'], entities['L-AA001']]))
            conn.execute('''INSERT INTO xm.match_deliveries(company_id, delivery_scope, buyer_entity_id, listing_entity_id, delivery_id)
                            VALUES (%s, 'wa', %s, %s, 'd1'), (%s, 'wa', %s, %s, 'd2')''',
                         (company, entities['B-AA001'], entities['L-AA001'], company, entities['B-AA000'], entities['L-AA000']))
            conn.commit()
        self.call(self.boss, '/entities/status', 'POST', {'ids': ['B-AA001'], 'status': 'deleted', 'note': 'spam'})
        self.call(self.boss, '/entities/status', 'POST', {'ids': ['L-AA000'], 'status': 'on_hold'})
        with workspace_scope(company):
            recompute_matches(company)   # fills the content keys and merges
        with workspace_scope(company), connect() as conn:
            rows = conn.execute('''SELECT e.document_type, e.public_id, e.status, e.status_note, count(d.id) AS copies
                                   FROM xm.entities e LEFT JOIN xm.documents d ON d.entity_id=e.entity_id
                                   WHERE e.company_id=%s GROUP BY 1, 2, 3, 4 ORDER BY 2''', (company,)).fetchall()
            self.assertEqual([(r['public_id'], r['status'], r['copies']) for r in rows],
                             [('B-AA000', 'deleted', 2), ('L-AA000', 'on_hold', 2)])
            self.assertEqual(rows[0]['status_note'], 'spam')       # the disabled buyer stays disabled
            aliases = conn.execute('SELECT old_public_id, public_id FROM xm.entity_aliases WHERE company_id=%s ORDER BY 1',
                                   (company,)).fetchall()
            self.assertEqual([(a['old_public_id'], a['public_id']) for a in aliases], [('B-AA001', 'B-AA000'), ('L-AA001', 'L-AA000')])
            events = conn.execute('''SELECT b.public_id AS buyer, l.public_id AS listing, me.found_at < now() - interval '12 hours' AS old
                                     FROM xm.match_events me JOIN xm.entities b ON b.entity_id=me.buyer_entity
                                     JOIN xm.entities l ON l.entity_id=me.listing_entity WHERE me.company_id=%s''', (company,)).fetchall()
            self.assertEqual([(e['buyer'], e['listing'], e['old']) for e in events], [('B-AA000', 'L-AA000', True)])
            receipts = conn.execute('SELECT buyer_entity_id, listing_entity_id FROM xm.match_deliveries WHERE company_id=%s',
                                    (company,)).fetchall()
            self.assertEqual(len(receipts), 1)
            self.assertEqual((str(receipts[0]['buyer_entity_id']), str(receipts[0]['listing_entity_id'])),
                             (str(entities['B-AA000']), str(entities['L-AA000'])))
            merged = conn.execute("SELECT count(*) AS n FROM xm.audit_events WHERE company_id=%s AND event_type='entity_merged'",
                                  (company,)).fetchone()['n']
            self.assertEqual(merged, 2)
        # The retired IDs still find the kept cards.
        listings = self.rows('property', stock_status='ready,on_hold', public_id='L-AA001')
        self.assertEqual([(r['public_id'], r['duplicate_count']) for r in listings], [('L-AA000', 2)])
        found = self.call(self.boss, '/buyers/list?search=B-AA001')['rows']
        self.assertEqual([(r['public_id'], r['entity_status']) for r in found], [('B-AA000', 'deleted')])
        self.assertEqual([r['public_id'] for r in self.call(self.boss, '/entities/lookup?q=B-AA001')], ['B-AA000'])
        # A status change by the retired ID acts on the kept one.
        result = self.call(self.boss, '/entities/status', 'POST', {'ids': ['B-AA001'], 'status': 'ready'})
        self.assertEqual([item['public_id'] for item in result['updated']], ['B-AA000'])
        # Steady state: another rebuild changes nothing, a new copy joins the kept ID.
        self.import_messages([(BUYER_1.replace('KONIG', 'konig') + ' 🙏', '~ Other')], agent='Second')
        buyers = self.rows('buyer')
        self.assertEqual([(r['public_id'], r['duplicate_count']) for r in buyers], [('B-AA000', 3)])

    def test_reprocess_fills_the_keys_and_merges_into_the_oldest_id(self):
        import matcher
        import reindex
        import worker
        from unittest.mock import patch
        from db import connect
        self.import_messages([(BUYER_1, '~ Hay'), (BUYER_2, '~ Mario')])
        self.assertEqual(sorted(self.legacy_ids().values()), ['B-AA000', 'B-AA001'])
        self.call(self.boss, '/entities/status', 'POST', {'ids': ['B-AA001'], 'status': 'deleted'})
        self.call(self.boss, '/reprocess', 'POST', status=202)
        with patch.object(reindex, 'upsert', lambda batch: len(batch)), \
                patch.object(matcher, 'qdrant_query', lambda *args, **kwargs: []):
            self.assertTrue(worker.process_maintenance())
        with connect() as conn:
            missing = conn.execute('SELECT count(*) AS n FROM xm.documents WHERE company_id=%s AND active AND content_key IS NULL',
                                   (self.company['company_id'],)).fetchone()['n']
            self.assertEqual(missing, 0)
        found = self.call(self.boss, '/buyers/list')['rows']
        self.assertEqual([(r['public_id'], r['entity_status'], r['duplicate_count']) for r in found], [('B-AA000', 'deleted', 2)])
        # A second Reprocess keeps the ID and the status.
        self.call(self.boss, '/reprocess', 'POST', status=202)
        with patch.object(reindex, 'upsert', lambda batch: len(batch)), \
                patch.object(matcher, 'qdrant_query', lambda *args, **kwargs: []):
            self.assertTrue(worker.process_maintenance())
        found = self.call(self.boss, '/buyers/list')['rows']
        self.assertEqual([(r['public_id'], r['entity_status'], r['duplicate_count']) for r in found], [('B-AA000', 'deleted', 2)])


if __name__ == '__main__':
    unittest.main()
