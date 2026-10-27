"""v4.3 pipeline/API: the Hot price floor setting, the matcher's storage rule (statuses from assess_pair are
stored unchanged, Custom only from 60), nearby-cluster alternatives and purpose-first retrieval."""
import json
import unittest
from unittest.mock import patch

from harness import ServerTestCase
from test_location_index import table

TOLERANCES = ('hot_price_floor_pct', 'price_down_pct', 'price_up_pct', 'land_down_pct', 'land_up_pct',
              'building_down_pct', 'building_up_pct', 'location_radius_km')
BUYER = 'Dicari rumah Zeta Park LT 100 Budget 2 M\nHub Rina 081311110000'
LISTING_SAME = 'Dijual rumah Zeta Park LT 100 Harga 1,9 M\nHub Andi 081322220000'
LISTING_NEARBY = 'Dijual rumah Omega Hills LT 100 Harga 1,9 M\nHub Budi 081333330000'
LISTING_FAR = 'Dijual rumah Kappa Cluster LT 100 Harga 1,9 M\nHub Citra 081344440000'
LISTING_RENT = 'Disewakan rumah Zeta Park LT 100 Harga 100 jt/th\nHub Dewi 081355550000'
# Omega Hills is 2 km from Zeta Park; Kappa Cluster has no neighbour.
CLUSTERS = table([[1, 'Zeta Park', '', 'Zeta Town', 'Omega Hills (2)'], [2, 'Omega Hills', '', 'Omega Town', ''],
                  [3, 'Kappa Cluster', '', 'Kappa Town', '']])


class V43Case(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.company, self.user, self.boss = self.make_company('V43')
        self.member, self.staff = self.make_member(self.boss)
        self.scope = self.company['company_id']

    def settings(self, client=None):
        return self.call(client or self.boss, '/settings')

    def values(self, row, keys=TOLERANCES):
        return {key: float(row[key]) for key in keys}


class HotPriceFloorSettingTests(V43Case):
    def test_defaults_are_the_v43_bands(self):
        # Hot = 80–100 % of the budget; Warm = 15 % beyond Hot for price, land and building; radius 4 km.
        self.assertEqual(self.values(self.settings(self.staff)),
                         {'hot_price_floor_pct': 80, 'price_down_pct': 15, 'price_up_pct': 15, 'land_down_pct': 15,
                          'land_up_pct': 15, 'building_down_pct': 15, 'building_up_pct': 15, 'location_radius_km': 4})

    def test_round_trip_keeps_the_other_fields(self):
        before = self.settings()
        saved = self.call(self.boss, '/settings', 'PUT', {'hot_price_floor_pct': 85})
        for source in (saved, self.settings(), self.settings(self.staff)):
            self.assertEqual(float(source['hot_price_floor_pct']), 85)
            self.assertEqual(self.values(source, TOLERANCES[1:]), self.values(before, TOLERANCES[1:]))
        # Saved together with the seven tolerance fields, and echoed back unchanged by a client sending its GET body.
        payload = {'hot_price_floor_pct': 90, 'price_down_pct': 10, 'price_up_pct': 20, 'land_down_pct': 5, 'land_up_pct': 25,
                   'building_down_pct': 12, 'building_up_pct': 18, 'location_radius_km': 3}
        self.assertEqual(self.values(self.call(self.boss, '/settings', 'PUT', payload)), payload)
        echo = self.call(self.boss, '/settings', 'PUT', self.settings())
        self.assertEqual(self.values(echo), payload)
        self.assertEqual(float(echo['price_floor_pct']), 90)        # legacy mirror: 100 − price_down_pct
        for edge in (50, 100):
            self.assertEqual(float(self.call(self.boss, '/settings', 'PUT', {'hot_price_floor_pct': edge})['hot_price_floor_pct']), edge)

    def test_an_echoed_get_body_never_brings_back_the_old_tolerances(self):
        # The single tolerances of a row the v4.3 migration (or a new company) set can still hold the old
        # mirrors (land 10, building 20, price floor 50); echoing them back is not an edit, also when sent twice.
        # Set explicitly so the guard stays covered once schema.sql re-mirrors those columns.
        from db import connect
        with connect() as conn:
            conn.execute('''UPDATE xm.match_settings SET price_floor_pct=50, price_tolerance_pct=15, land_tolerance_pct=10,
                            building_tolerance_pct=20 WHERE company_id=%s''', (self.scope,))
            conn.commit()
        defaults = self.values(self.settings())
        for _ in range(2):
            self.assertEqual(self.values(self.call(self.boss, '/settings', 'PUT', self.settings())), defaults)
        # A real edit of an old single tolerance still moves both of its new fields.
        saved = self.call(self.boss, '/settings', 'PUT', {**self.settings(), 'land_tolerance_pct': 12})
        self.assertEqual((float(saved['land_down_pct']), float(saved['land_up_pct'])), (12, 12))
        self.assertEqual(float(saved['price_down_pct']), defaults['price_down_pct'])

    def test_validation_and_permissions(self):
        for bad in (49.9, 100.5, -1, 'abc'):
            self.call(self.boss, '/settings', 'PUT', {'hot_price_floor_pct': bad}, status=422)
        self.call(self.staff, '/settings', 'PUT', {'hot_price_floor_pct': 85}, status=403)
        self.assertEqual(float(self.settings()['hot_price_floor_pct']), 80)

    def test_changing_it_marks_results_out_of_date(self):
        self.process(self.upload(self.user, messages=[(BUYER, '~ Rina'), (LISTING_SAME, '~ Andi')], agent='V43'))
        self.assertFalse(self.call(self.boss, '/reprocess/status')['pending'])
        self.call(self.boss, '/settings', 'PUT', {'hot_price_floor_pct': 80})       # unchanged value
        self.call(self.boss, '/settings', 'PUT', self.settings())                    # whole body echoed back
        self.assertFalse(self.call(self.boss, '/reprocess/status')['pending'])
        self.call(self.boss, '/settings', 'PUT', {'hot_price_floor_pct': 75})
        status = self.call(self.boss, '/reprocess/status')
        self.assertTrue(status['pending'])
        self.assertIn('Matching settings changed', status['reasons'])


class StorageRuleTests(unittest.TestCase):
    def test_custom_needs_sixty_and_other_statuses_fifty_five(self):
        from matcher import stored

        def result(status, score, **extra):
            return {'status': status, 'score': score, **extra}
        cases = [(None, False), (result('hot', 85), True), (result('warm', 55), True), (result('warm', 54.99), False),
                 (result('warm', 82), True), (result('custom', 60), True), (result('custom', 59.99), False),
                 (result('custom', 79, pre_cap_score=95), True), (result('custom', 60, pre_cap_score=58), False),
                 (result('reject', 90), False), (result(None, 90), False)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertIs(stored(value), expected)


class MatcherStorageTests(V43Case):
    def seed(self):
        self.process(self.upload(self.user, messages=[(BUYER, '~ Rina'), (LISTING_SAME, '~ Andi'), (LISTING_NEARBY, '~ Budi'),
                                                      (LISTING_FAR, '~ Citra'), (LISTING_RENT, '~ Dewi')], agent='V43'))
        from db import connect
        from location_index import parse_table
        with connect() as conn:
            conn.execute('INSERT INTO xm.location_indexes(company_id, data) VALUES (%s, %s::jsonb)',
                         (self.scope, json.dumps(parse_table(CLUSTERS))))
            conn.commit()

    def recompute(self, outcomes):
        """Run the matcher with assess_pair replaced: ``outcomes`` maps a listing text to (status, score)."""
        import matcher
        from tenant import workspace_scope
        seen = []

        def fake(request, listing, settings, semantic=50.0, location_index=None):
            text = listing.get('segment_text') or listing['raw_text']
            seen.append(text)
            if text not in outcomes:
                return None
            status, score = outcomes[text]
            return {'score': score, 'location': 100.0, 'land': 100.0, 'building': 100.0, 'price': 100.0, 'semantic': 50.0,
                    'status': status, 'explanation': [f'Stub {status}']}
        with workspace_scope(self.scope), patch.object(matcher, 'assess_pair', fake), \
                patch.object(matcher, 'qdrant_query', lambda *args, **kwargs: []):
            matcher.recompute_matches(self.scope)
        return seen

    def stored(self):
        from db import connect
        with connect() as conn:
            return {r['raw_text']: (r['status'], float(r['score'])) for r in conn.execute(
                '''SELECT m.status, m.score, r.raw_text FROM xm.matches m JOIN xm.documents d ON d.id=m.property_listing_id
                   JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE m.company_id=%s''', (self.scope,)).fetchall()}

    def test_statuses_from_assess_pair_are_stored_unchanged(self):
        self.seed()
        seen = self.recompute({LISTING_SAME: ('warm', 86.0), LISTING_NEARBY: ('custom', 70.0)})
        # A Warm pair keeps Warm even with a score of 80+; the nearby alternative keeps Custom.
        self.assertEqual(self.stored(), {LISTING_SAME: ('warm', 86.0), LISTING_NEARBY: ('custom', 70.0)})
        self.assertNotIn(LISTING_FAR, seen)          # no edge to Kappa Cluster: never retrieved
        self.assertNotIn(LISTING_RENT, seen)         # purpose first: a buy request never meets a rent listing
        self.recompute({LISTING_SAME: ('hot', 81.0), LISTING_NEARBY: ('custom', 59.0)})
        self.assertEqual(self.stored(), {LISTING_SAME: ('hot', 81.0)})   # Custom below 60 is not stored
        self.recompute({LISTING_SAME: ('warm', 56.0), LISTING_NEARBY: ('custom', 60.0)})
        self.assertEqual(self.stored(), {LISTING_SAME: ('warm', 56.0), LISTING_NEARBY: ('custom', 60.0)})
        # Rows below 60 stay out of the views; the Custom pair shows with its status.
        buyer = next(row for row in self.call(self.boss, '/workspace?direction=buyer')['rows'] if row['raw_text'] == BUYER)
        self.assertEqual((buyer['hot_count'], buyer['warm_count'], buyer['custom_count']), (0, 0, 1))

    def test_nearby_alternatives_follow_the_radius_setting(self):
        self.seed()
        self.assertIn(LISTING_NEARBY, self.recompute({}))
        self.call(self.boss, '/settings', 'PUT', {'location_radius_km': 1})
        self.assertNotIn(LISTING_NEARBY, self.recompute({}))
        self.call(self.boss, '/settings', 'PUT', {'location_radius_km': 2})
        self.assertIn(LISTING_NEARBY, self.recompute({}))


if __name__ == '__main__':
    unittest.main()
