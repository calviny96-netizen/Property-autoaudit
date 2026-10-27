"""Recommendation lists rank by ownership tier: own stock, sales watchlist, company keyword, everyone else.

Seen from the person who posted the source (Ellen): her own listings first, then her teammates on the sales
watchlist, then any message carrying the company keyword ("Konig"), then other agents. Inside a tier: hot,
warm, custom, then score. The tier is read-time only: watchlist/keyword edits reorder without reprocessing.
"""
import itertools
import json
import random
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import ownership
from harness import ServerTestCase

ELLEN, BUDI, RINA, SARI = '081234560001', '081234560002', '081234560003', '081234560004'
TONO, WATI = '081234560005', '081234560006'


def e164(phone):
    return '62' + phone[1:]


# v4.10: one ID per content (signature and case do not count), so each buyer below has its own LT.
ELLEN_BUYER = f'Buyer request rumah Surabaya Barat LT 100 Budget 2 M\nContact: Ellen Konig {ELLEN}'
BUDI_BUYER = f'Buyer request rumah Surabaya Barat LT 101 Budget 2 M\nContact: Budi {BUDI}'
RINA_BUYER = f'Buyer request rumah Surabaya Barat LT 102 Budget 2 M\nContact: Rina Konig {RINA}'
SARI_BUYER = f'Buyer request rumah Surabaya Barat LT 103 Budget 2 M\nContact: Sari Brighton {SARI}'
ELLEN_LISTING = f'Dijual rumah Surabaya Barat LT 100 Harga 1,8 M\nContact: Ellen Konig {ELLEN}'
BUDI_LISTING = f'Dijual rumah Surabaya Barat LT 100 Harga 1,85 M\nContact: Budi {BUDI}'
RINA_LISTING = f'Dijual rumah Surabaya Barat LT 100 Harga 1,9 M\nContact: Rina Konig {RINA}'
SARI_LISTING = f'Dijual rumah Surabaya Barat LT 100 Harga 1,95 M\nContact: Sari Brighton {SARI}'
TONO_LISTING = f'Dijual rumah Surabaya Barat LT 100 Harga 1,75 M\nContact: Tono {TONO}'
WATI_LISTING = f'Dijual rumah Surabaya Barat LT 100 Harga 1,7 M\nContact: Wati {WATI}'

# No phone in the text: ownership falls back to the WhatsApp sender.
LOOSE_BUYER = 'Buyer request rumah Surabaya Barat LT 100 Budget 2 M'
LOOSE_OWN = 'Dijual rumah Surabaya Barat LT 100 Harga 1,8 M'
LOOSE_OWN_PHONE = f'Dijual rumah Surabaya Barat LT 100 Harga 1,85 M\nContact: Ellen {ELLEN}'
LOOSE_OTHER = 'Dijual rumah Surabaya Barat LT 100 Harga 1,9 M'
LOOSE_RINA_ELLEN = f'Dijual rumah Surabaya Barat LT 100 Harga 1,95 M\nContact: Ellen {ELLEN}'
LOOSE_BUYER_ELLEN = f'Buyer request rumah Surabaya Barat LT 101 Budget 2 M\nContact: Ellen {ELLEN}'


class TierRuleTests(unittest.TestCase):
    """The pure-Python rule (PDF/export paths) without a database."""

    def test_sender_key_strips_tilde_and_trailing_phone(self):
        self.assertEqual(ownership.sender_key('~ Ellen, +62 812-3456-0001'), 'ellen')
        self.assertEqual(ownership.sender_key('  ~Ellen  Wijaya ,  +62 812 3456 0001 '), 'ellen wijaya')
        self.assertEqual(ownership.sender_key('Ellen'), 'ellen')
        self.assertEqual(ownership.sender_key('+62 812-3456-0001'), '+62 812-3456-0001')
        self.assertEqual(ownership.sender_key(None), '')

    def test_phone_wins_over_sender_and_sender_only_without_a_phone(self):
        ellen = {'contact_phone': e164(ELLEN), 'contact_phones': [e164(ELLEN)], 'author': 'Ellen'}
        self.assertEqual(ownership.tier(ellen, {'contact_phones': ['0812 3456 0001'], 'author': 'Someone'}), 'own')
        # Both sides carry a phone and they differ: the same sender does not make it Ellen's.
        self.assertEqual(ownership.tier(ellen, {'contact_phone': e164(SARI), 'author': 'Ellen'}), 'other')
        self.assertEqual(ownership.tier(ellen, {'author': '~ Ellen, +62 812-3456-0001'}), 'own')
        self.assertEqual(ownership.tier({'author': ''}, {'author': ''}), 'other')
        team = {'contact_phone': e164(BUDI), 'raw_text': 'x Konig'}
        self.assertEqual(ownership.tier(ellen, team, [BUDI], ['konig']), 'team')
        self.assertEqual(ownership.tier(ellen, team, ownership.OwnershipRules((), ('KONIG',))), 'company')
        self.assertEqual(ownership.tier(ellen, {'normalized_text': 'promo konig'}, (), ['Konig']), 'company')
        self.assertEqual(ownership.tier(ellen, team), 'other')

    def test_sender_number_decides_when_both_authors_carry_one(self):
        self.assertEqual(ownership.sender_number('~ Ellen, +62 812-3456-0001'), e164(ELLEN))
        self.assertEqual(ownership.sender_number('+62\u00a0812\u20113456\u20110001'), e164(ELLEN))
        self.assertEqual(ownership.sender_number('Ellen, 0812 3456 0001'), e164(ELLEN))
        for author in ('Ellen', '~ Ellen', 'Ellen, 12345', 'Ellen, +62 812', None, ''):
            self.assertEqual(ownership.sender_number(author), '', author)
        # Two unsaved contacts both called Budi are two people.
        self.assertEqual(ownership.tier({'author': '~ Budi, +62 811-1111-1111'}, {'author': '~ Budi, +62 813-2222-2222'}), 'other')
        self.assertEqual(ownership.tier({'author': '~ Budi, +62 811-1111-1111'}, {'author': '+62 811-1111-1111'}), 'own')
        # A saved contact has no number in the author: the name decides.
        self.assertEqual(ownership.tier({'author': '~ Budi, +62 811-1111-1111'}, {'author': 'Budi'}), 'own')
        # A side without a phone in its text is identified by its sender's number, both ways.
        self.assertEqual(ownership.tier({'author': '~ Ellen, +62 812-3456-0001'}, {'contact_phone': e164(ELLEN), 'author': 'Rina'}), 'own')
        self.assertEqual(ownership.tier({'contact_phone': e164(ELLEN), 'author': 'Rina'}, {'author': '~ Ellen, +62 812-3456-0001'}), 'own')
        self.assertEqual(ownership.tier({'author': '~ Ellen, +62 812-3456-0001'}, {'contact_phone': e164(SARI), 'author': 'Rina'}), 'other')

    def test_sort_key_orders_tier_status_score_then_recency(self):
        now = datetime(2026, 10, 19)
        rows = [{'ownership_tier': 'other', 'status': 'hot', 'score': 99, 'sent_at': now},
                {'ownership_tier': 'own', 'status': 'custom', 'score': 61, 'sent_at': now},
                {'ownership_tier': 'own', 'status': 'hot', 'score': 70, 'sent_at': now - timedelta(days=2)},
                {'ownership_tier': 'own', 'status': 'hot', 'score': 70, 'sent_at': now},
                {'ownership_tier': 'team', 'status': 'warm', 'score': 80, 'sent_at': None}]
        ordered = sorted(rows, key=ownership.sort_key)
        self.assertEqual([(r['ownership_tier'], r['status'], r['sent_at']) for r in ordered],
                         [('own', 'hot', now), ('own', 'hot', now - timedelta(days=2)), ('own', 'custom', now),
                          ('team', 'warm', None), ('other', 'hot', now)])


class OwnershipTierTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.owner, self.boss = self.make_company('Tier')
        self.company_id = self.company['company_id']

    # -- helpers --------------------------------------------------------------
    def docs(self, text):
        """Every active copy of a text, oldest first."""
        from db import connect
        with connect() as conn:
            return conn.execute('''SELECT d.id, d.entity_id, r.author FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
              WHERE d.company_id=%s AND d.active AND r.raw_text=%s ORDER BY r.sent_at''', (self.company_id, text)).fetchall()

    def doc(self, text):
        rows = self.docs(text)
        self.assertEqual(len(rows), 1, text)
        return rows[0]

    def set_pair(self, buyer, listing, score, status='hot'):
        """Pin a pair's score and status, for every copy of both texts (so the order cannot come from the
        matcher's scores)."""
        from db import connect
        buyers, listings = self.docs(buyer), self.docs(listing)
        with connect() as conn:
            match = conn.execute('''UPDATE xm.matches SET score=%s, status=%s
              WHERE company_id=%s AND buyer_request_id=ANY(%s) AND property_listing_id=ANY(%s) RETURNING id''',
                                 (score, status, self.company_id, [b['id'] for b in buyers], [p['id'] for p in listings])).fetchall()
            self.assertTrue(match, (buyer, listing))
            conn.execute('UPDATE xm.group_matches SET score=%s, status=%s WHERE match_id=ANY(%s)', (score, status, [m['id'] for m in match]))
            conn.execute('''UPDATE xm.match_events SET last_score=%s, last_status=%s
              WHERE company_id=%s AND buyer_entity=ANY(%s) AND listing_entity=ANY(%s)''',
                         (score, status, self.company_id, [b['entity_id'] for b in buyers], [p['entity_id'] for p in listings]))
            conn.commit()

    def uncached(self):
        """Run the block without the group cache (the uncached fallback of /workspace/recommendations)."""
        from contextlib import contextmanager
        from db import connect

        @contextmanager
        def block():
            with connect() as conn:
                state = conn.execute('DELETE FROM xm.workspace_cache_state WHERE company_id=%s RETURNING refreshed_at',
                                     (self.company_id,)).fetchone()
                conn.commit()
            try:
                yield
            finally:
                with connect() as conn:
                    conn.execute('INSERT INTO xm.workspace_cache_state(company_id, refreshed_at) VALUES (%s,%s)',
                                 (self.company_id, state['refreshed_at']))
                    conn.commit()
        return block()

    def recommend(self, source, direction='buyer', **extra):
        groups = self.call(self.boss, '/workspace/recommendations', 'POST',
                           {'direction': direction, 'ids': [str(self.doc(source)['id'])], **extra})['groups']
        self.assertEqual(len(groups), 1)
        return groups[0]

    def order(self, rows, text='raw_text'):
        return [(row[text], row['ownership_tier']) for row in rows]

    def recent(self, direction, source):
        today = datetime.now(ZoneInfo('Asia/Jakarta')).date()
        query = {'direction': direction, 'date_from': (today - timedelta(days=1)).isoformat(),
                 'date_to': (today + timedelta(days=1)).isoformat(), 'source_entity': str(self.doc(source)['entity_id'])}
        groups = self.call(self.boss, '/matches/recent?' + urlencode(query))['groups']
        self.assertEqual(len(groups), 1)
        return groups[0]['matches']

    def assert_python_rule_agrees(self, source, rows):
        rules = self.rules()
        for row in rows:
            self.assertEqual(ownership.tier(source, row, rules), row['ownership_tier'], row['raw_text'])

    def rules(self):
        from db import connect
        with connect() as conn:
            return ownership.load_rules(conn, self.company_id)

    def seed(self):
        self.call(self.boss, '/search-default', 'PUT', {'terms': ['Konig']})
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': [BUDI]})
        messages = [(ELLEN_BUYER, '~ Ellen, +62 812-3456-0001'), (BUDI_BUYER, 'Budi'), (RINA_BUYER, 'Rina'),
                    (SARI_BUYER, 'Sari'), (ELLEN_LISTING, 'Ellen'), (BUDI_LISTING, 'Budi'), (RINA_LISTING, 'Rina'),
                    # Ellen forwarded Sari's listing: both sides have a phone, so the phone decides (not Ellen's).
                    (SARI_LISTING, '~ Ellen, +62 812-3456-0001'), (TONO_LISTING, 'Tono'), (WATI_LISTING, 'Wati')]
        self.process(self.upload(self.owner, messages=messages))
        # Scores rise as ownership falls, so only the tier can put Ellen's own stock first.
        for buyer, listing, score, status in [
                (ELLEN_BUYER, ELLEN_LISTING, 70, 'hot'), (ELLEN_BUYER, BUDI_LISTING, 80, 'hot'),
                (ELLEN_BUYER, RINA_LISTING, 90, 'hot'), (ELLEN_BUYER, TONO_LISTING, 75, 'hot'),
                (ELLEN_BUYER, SARI_LISTING, 99, 'warm'), (ELLEN_BUYER, WATI_LISTING, 98, 'custom'),
                (BUDI_BUYER, ELLEN_LISTING, 80, 'hot'), (RINA_BUYER, ELLEN_LISTING, 90, 'hot'),
                (SARI_BUYER, ELLEN_LISTING, 99, 'hot')]:
            self.set_pair(buyer, listing, score, status)

    # -- tests ----------------------------------------------------------------
    def test_every_recommendation_list_ranks_own_team_company_other(self):
        self.seed()
        buyer_side = [(ELLEN_LISTING, 'own'), (BUDI_LISTING, 'team'), (RINA_LISTING, 'company'),
                      # Inside a tier: hot, warm, custom (whatever the score).
                      (TONO_LISTING, 'other'), (SARI_LISTING, 'other'), (WATI_LISTING, 'other')]
        listing_side = [(ELLEN_BUYER, 'own'), (BUDI_BUYER, 'team'), (RINA_BUYER, 'company'), (SARI_BUYER, 'other')]

        group = self.recommend(ELLEN_BUYER)
        self.assertEqual(self.order(group['recommendations']), buyer_side)
        self.assertEqual([row['status'] for row in group['recommendations'][3:]], ['hot', 'warm', 'custom'])
        self.assert_python_rule_agrees(group['source'], group['recommendations'])
        # A target window uses the other target query; the order is the same.
        windowed = self.recommend(ELLEN_BUYER, listing_date_from='2026-01-01', listing_date_to='2026-12-31')
        self.assertEqual(self.order(windowed['recommendations']), buyer_side)

        group = self.recommend(ELLEN_LISTING, 'property')
        self.assertEqual(self.order(group['recommendations']), listing_side)
        self.assert_python_rule_agrees(group['source'], group['recommendations'])

        # Legacy buyer endpoints.
        ellen = str(self.doc(ELLEN_BUYER)['id'])
        single = self.call(self.boss, f'/buyers/{ellen}/recommendations')['recommendations']
        self.assertEqual(self.order(single, 'property_text'), buyer_side)
        batch = self.call(self.boss, '/buyers/recommendations/batch', 'POST', {'buyer_ids': [ellen], 'limit_per_buyer': 2})
        self.assertEqual(self.order(batch['groups'][0]['recommendations'], 'property_text'), buyer_side[:2])
        self.assertNotIn('rank', batch['groups'][0]['recommendations'][0])
        self.assertNotIn('ownership_rank', single[0])

        # New matches (recent): the target rows carry the tier too.
        matches = self.recent('buyer', ELLEN_BUYER)
        self.assertEqual([(m['target']['raw_text'], m['ownership_tier']) for m in matches], buyer_side)
        self.assertTrue(all(m['target']['ownership_tier'] == m['ownership_tier'] for m in matches))
        matches = self.recent('property', ELLEN_LISTING)
        self.assertEqual([(m['target']['raw_text'], m['ownership_tier']) for m in matches], listing_side)

        # Without the group cache (the uncached fallback) the order is the same.
        with self.uncached():
            self.assertEqual(self.order(self.recommend(ELLEN_BUYER)['recommendations']), buyer_side)
            self.assertEqual(self.order(self.recommend(ELLEN_LISTING, 'property')['recommendations']), listing_side)

    def test_selection_pdf_pages_follow_the_ranked_lists(self):
        self.seed()
        # Sources must carry the company keyword (company matching scope): Ellen and Rina.
        ellen, rina = str(self.doc(ELLEN_BUYER)['id']), str(self.doc(RINA_BUYER)['id'])
        rows = {row['raw_text']: str(row['id']) for source in (ELLEN_BUYER, RINA_BUYER)
                for row in self.recommend(source)['recommendations']}
        clicks = [(ellen, WATI_LISTING), (rina, ELLEN_LISTING), (ellen, RINA_LISTING), (ellen, ELLEN_LISTING), (ellen, BUDI_LISTING)]
        captured = []

        def fake_report(pairs, direction, **kwargs):
            captured.extend((str(source['id']), target['raw_text'], target['ownership_tier']) for source, target in pairs)
            return b'%PDF-1.4 test'
        with patch('report.build_report', fake_report):
            self.call(self.boss, '/export/pdf', 'POST', {'direction': 'buyer', 'pairs': [
                {'source_id': source, 'target_id': rows[text]} for source, text in clicks]})
        # Ellen's pages first (picked first), in list order: own, team, company, other; then Rina's.
        self.assertEqual(captured, [(ellen, ELLEN_LISTING, 'own'), (ellen, BUDI_LISTING, 'team'), (ellen, RINA_LISTING, 'company'),
                                    (ellen, WATI_LISTING, 'other'), (rina, ELLEN_LISTING, 'company')])

    def test_recent_export_pairs_rank_like_recent_matches(self):
        """matchlog.rank_pairs: the helper the Recent "Export all" PDF needs (export_all.recent_pairs)."""
        import matchlog
        from db import connect
        from tenant import workspace_scope
        self.seed()
        buyer = self.doc(ELLEN_BUYER)
        with workspace_scope(self.company_id), connect() as conn:
            fields = f'''SELECT {matchlog.PAIR_FIELDS} FROM xm.document_groups g JOIN xm.documents d ON d.id=g.group_id
              JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE g.company_id=current_setting('xm.workspace_id')'''
            source = conn.execute(fields + ' AND g.entity_id=%s', (buyer['entity_id'],)).fetchone()
            # Score order, as export_all.recent_pairs reads them.
            targets = conn.execute(fields.replace('SELECT ', 'SELECT me.last_score score, me.last_status status, ', 1)
                                   .replace(' WHERE ', ' JOIN xm.match_events me ON me.listing_entity=g.entity_id WHERE ', 1)
                                   + ' AND me.buyer_entity=%s ORDER BY me.last_score DESC', (buyer['entity_id'],)).fetchall()
            ranked = matchlog.rank_pairs(conn, [(source, target) for target in targets])
        self.assertIn('contact_phones', source)
        self.assertEqual([(target['raw_text'], target['ownership_tier']) for _, target in ranked],
                         [(text, tier) for text, tier in self.recent_order(ELLEN_BUYER)])
        self.assertEqual([target['ownership_tier'] for _, target in ranked], ['own', 'team', 'company', 'other', 'other', 'other'])

    def recent_order(self, source, direction='buyer'):
        return [(m['target']['raw_text'], m['ownership_tier']) for m in self.recent(direction, source)]

    def test_sql_rule_agrees_with_the_python_rule(self):
        """ownership.rank_sql (+ source_sql) and ownership.tier on the same rows, including awkward authors."""
        from db import connect
        authors = [None, '', '  ', '~', '~ Ellen', 'Ellen', 'ELLEN', 'Ellen  Wijaya', '~ Ellen, +62 812-3456-0001',
                   'Ellen, 0812 3456 0001', 'Ellen, +62\u00a0812\u20113456\u20110001', '+62 812-3456-0001', '~ Ellen ,', 'Ellen, 12345',
                   'Ellen, +62 812', '~ Budi, +62 811-1111-1111', '~ Budi, +62 813-2222-2222', 'Budi', '\tEllen\u202f',
                   'Ellen (Konig), +62 811 222 333', '0812-3456-0001, 0813-2222-2222']
        phone_sets = [(None, []), (e164(ELLEN), [e164(ELLEN)]), (None, [e164(ELLEN)]), (e164(SARI), [e164(SARI), e164(ELLEN)]),
                      ('', ['']), (e164(TONO), []), (None, ['6281111111111'])]
        texts = ['', 'Contact Ellen Konig', 'KONIG property', 'Brighton_Realty', 'BrightonXRealty', 'diskon 50% cash',
                 'diskon 50x', 'back\\slash', 'KÖNIG']
        rules = ownership.OwnershipRules((e164(TONO), '6281111111111'), ('Konig', 'Brighton_Realty', '50%', 'back\\slash', 'könig'))
        combos = list(itertools.product(phone_sets, authors, phone_sets, authors, texts))
        rnd = random.Random(7)
        rnd.shuffle(combos)
        # Some targets were also posted by other senders (copies): any copy may make the target own.
        cases = [{'i': i, 's_phone': sp[0], 's_phones': sp[1], 's_author': sa, 't_phone': tp[0], 't_phones': tp[1],
                  't_author': ta, 'raw_text': text, 'normalized_text': text.lower() if i % 2 else '',
                  'copies': rnd.sample(authors[1:], rnd.choice((0, 0, 1, 2)))}
                 for i, (sp, sa, tp, ta, text) in enumerate(combos[:8000])]
        rank, params, join = ownership.rank_sql(rules, 'src', 't', copies='cs')
        with connect() as conn:
            rows = conn.execute(f'''WITH cases AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS c(i int, s_phone text, s_phones text[], s_author text,
                  t_phone text, t_phones text[], t_author text, raw_text text, normalized_text text, copies text[])
              ), src AS MATERIALIZED (
                SELECT c.i, {ownership.source_sql('c')}
                FROM (SELECT i, s_phone contact_phone, s_phones contact_phones, s_author author FROM cases) c
              ), cs AS MATERIALIZED (
                {ownership.copy_senders_sql('copy_raw.i', 'FROM (SELECT i, unnest(copies) AS author FROM cases) copy_raw')}
              )
              SELECT t.i, {rank} AS rank, {ownership._sender_sql('t')} AS sender, {ownership._number_sql('t')} AS number
              FROM (SELECT i, t_phone contact_phone, t_phones contact_phones, t_author author, raw_text, normalized_text FROM cases) t
              JOIN src ON src.i=t.i LEFT JOIN cs ON cs.key=t.i {join} ORDER BY t.i''', [json.dumps(cases), *params]).fetchall()
        self.assertEqual(len(rows), len(cases))
        wrong = []
        for case, row in zip(cases, rows):
            source = {'contact_phone': case['s_phone'], 'contact_phones': case['s_phones'], 'author': case['s_author']}
            target = {'contact_phone': case['t_phone'], 'contact_phones': case['t_phones'],
                      'raw_text': case['raw_text'], 'normalized_text': case['normalized_text']}
            expected = min(ownership.tier_rank(ownership.tier(source, {**target, 'author': author}, rules))
                           for author in [case['t_author'], *case['copies']])
            if (row['rank'], row['sender'], row['number']) != (expected, ownership.sender_key(case['t_author']),
                                                                 ownership.sender_number(case['t_author'])):
                wrong.append((case, dict(row), expected))
        self.assertEqual(wrong[:5], [])
        self.assertGreater(sum(1 for row in rows if row['rank'] == 1), 500)

    def test_watchlist_and_keyword_changes_reorder_without_reprocessing(self):
        self.seed()
        from db import connect
        with connect() as conn:
            jobs = conn.execute('SELECT count(*) AS n FROM xm.maintenance_jobs WHERE company_id=%s', (self.company_id,)).fetchone()['n']
        # Budi leaves the watchlist: his listing (no company keyword) joins the other agents, by status then score.
        self.call(self.boss, '/stock/tracked', 'PUT', {'phones': []})
        self.assertEqual(self.order(self.recommend(ELLEN_BUYER)['recommendations']),
                         [(ELLEN_LISTING, 'own'), (RINA_LISTING, 'company'), (BUDI_LISTING, 'other'),
                          (TONO_LISTING, 'other'), (SARI_LISTING, 'other'), (WATI_LISTING, 'other')])
        # A second company keyword ("Brighton" in Sari's signature) moves her listing up at once.
        self.call(self.boss, '/search-default', 'PUT', {'terms': ['Konig', 'Brighton']})
        self.assertEqual(self.order(self.recommend(ELLEN_BUYER)['recommendations']),
                         [(ELLEN_LISTING, 'own'), (RINA_LISTING, 'company'), (SARI_LISTING, 'company'),
                          (BUDI_LISTING, 'other'), (TONO_LISTING, 'other'), (WATI_LISTING, 'other')])
        self.assertEqual([(m['target']['raw_text'], m['ownership_tier']) for m in self.recent('property', ELLEN_LISTING)],
                         # Both hot inside the company tier: the higher score (Sari 99) leads.
                         [(ELLEN_BUYER, 'own'), (SARI_BUYER, 'company'), (RINA_BUYER, 'company'), (BUDI_BUYER, 'other')])
        # Nothing was rebuilt: results are flagged out of date by the keyword change, but no job ran.
        self.assertTrue(self.call(self.boss, '/reprocess/status')['pending'])
        with connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM xm.maintenance_jobs WHERE company_id=%s',
                                          (self.company_id,)).fetchone()['n'], jobs)

    def test_sender_name_decides_when_a_side_has_no_phone(self):
        messages = [(LOOSE_BUYER, '~ Ellen, +62 812-3456-0001'), (LOOSE_OWN, 'Ellen'),
                    (LOOSE_OWN_PHONE, 'ellen'), (LOOSE_OTHER, '~ Sari, +62 812-3456-0004')]
        self.process(self.upload(self.owner, messages=messages))
        self.set_pair(LOOSE_BUYER, LOOSE_OWN, 70)
        self.set_pair(LOOSE_BUYER, LOOSE_OWN_PHONE, 65)
        self.set_pair(LOOSE_BUYER, LOOSE_OTHER, 99)
        group = self.recommend(LOOSE_BUYER)
        self.assertEqual(self.order(group['recommendations']),
                         [(LOOSE_OWN, 'own'), (LOOSE_OWN_PHONE, 'own'), (LOOSE_OTHER, 'other')])
        self.assert_python_rule_agrees(group['source'], group['recommendations'])
        group = self.recommend(LOOSE_OWN, 'property')
        self.assertEqual(self.order(group['recommendations']), [(LOOSE_BUYER, 'own')])

    def test_sender_numbers_and_the_number_in_the_text_decide_ownership(self):
        # Two unsaved contacts called Budi, no phone in either text: different numbers, different people.
        messages = [(LOOSE_BUYER, '~ Budi, +62 811-1111-1111'), (LOOSE_OWN, '~ Budi, +62 813-2222-2222'),
                    # Posted by Rina, but the text carries Ellen's number; Ellen sent the phone-less buyer.
                    (LOOSE_RINA_ELLEN, 'Rina'), (LOOSE_OTHER, '~ Budi, +62 811-1111-1111')]
        self.process(self.upload(self.owner, messages=messages))
        self.set_pair(LOOSE_BUYER, LOOSE_OWN, 99)
        self.set_pair(LOOSE_BUYER, LOOSE_RINA_ELLEN, 70)
        self.set_pair(LOOSE_BUYER, LOOSE_OTHER, 65)
        expected = [(LOOSE_OTHER, 'own'), (LOOSE_OWN, 'other'), (LOOSE_RINA_ELLEN, 'other')]
        group = self.recommend(LOOSE_BUYER)
        self.assertEqual(self.order(group['recommendations']), expected)
        self.assert_python_rule_agrees(group['source'], group['recommendations'])
        self.assertEqual(self.recent_order(LOOSE_BUYER), expected)

    def test_a_phoneless_buyer_owns_listings_carrying_its_senders_number(self):
        messages = [(LOOSE_BUYER, '~ Ellen, +62 812-3456-0001'), (LOOSE_RINA_ELLEN, 'Rina'), (LOOSE_OTHER, 'Sari'),
                    (LOOSE_BUYER_ELLEN, 'Rina')]
        self.process(self.upload(self.owner, messages=messages))
        self.set_pair(LOOSE_BUYER, LOOSE_RINA_ELLEN, 65)
        self.set_pair(LOOSE_BUYER, LOOSE_OTHER, 99)
        group = self.recommend(LOOSE_BUYER)
        self.assertEqual(self.order(group['recommendations']), [(LOOSE_RINA_ELLEN, 'own'), (LOOSE_OTHER, 'other')])
        self.assert_python_rule_agrees(group['source'], group['recommendations'])
        self.assertEqual(self.recent_order(LOOSE_BUYER), [(LOOSE_RINA_ELLEN, 'own'), (LOOSE_OTHER, 'other')])
        # The other way round: a listing without a phone, sent by "~ Ellen, +62 ...", owns a buyer carrying her number.
        self.process(self.upload(self.owner, messages=[(LOOSE_OWN, '~ Ellen, +62 812-3456-0001')], day=10))
        self.set_pair(LOOSE_BUYER_ELLEN, LOOSE_OWN, 70)
        group = self.recommend(LOOSE_OWN, 'property')
        self.assertEqual(dict(self.order(group['recommendations']))[LOOSE_BUYER_ELLEN], 'own')

    def test_any_copy_sent_by_the_source_sender_makes_it_own_in_every_path(self):
        # Ellen posted the listing without a phone; Budi reposted the same text later, so the card shows his copy.
        messages = [(LOOSE_BUYER, '~ Ellen, +62 812-3456-0001'), (LOOSE_OWN, 'Ellen'), (LOOSE_OWN, 'Budi'),
                    (LOOSE_OTHER, '~ Sari, +62 812-3456-0004')]
        self.process(self.upload(self.owner, messages=messages))
        self.assertEqual([row['author'] for row in self.docs(LOOSE_OWN)], ['Ellen', 'Budi'])
        self.set_pair(LOOSE_BUYER, LOOSE_OWN, 65)
        self.set_pair(LOOSE_BUYER, LOOSE_OTHER, 99)
        expected = [(LOOSE_OWN, 'own'), (LOOSE_OTHER, 'other')]
        cached = self.recommend(LOOSE_BUYER)['recommendations']
        self.assertEqual(self.order(cached), expected)
        self.assertEqual(cached[0]['author'], 'Budi')  # the shown copy is the latest one
        self.assertEqual(self.recent_order(LOOSE_BUYER), expected)
        with self.uncached():
            self.assertEqual(self.order(self.recommend(LOOSE_BUYER)['recommendations']), expected)
        # A target window that leaves out Ellen's copy: only Budi's copy counts, in both paths.
        window = {'listing_date_from': '2026-09-03', 'listing_date_to': '2026-09-30'}
        expected = [(LOOSE_OTHER, 'other'), (LOOSE_OWN, 'other')]
        self.assertEqual(self.order(self.recommend(LOOSE_BUYER, **window)['recommendations']), expected)
        with self.uncached():
            self.assertEqual(self.order(self.recommend(LOOSE_BUYER, **window)['recommendations']), expected)


if __name__ == '__main__':
    unittest.main()
