"""Public IDs such as L-AB908 must be unique, ordered, readable and never run out."""
import os
import unittest

from entities import ALPHABET, decode_public_id, encode_public_id, exact_public_ids, normalize_id_query


class PublicIdFormatTests(unittest.TestCase):
    def test_examples_follow_the_requested_shape(self):
        self.assertEqual(encode_public_id('property_listing', 0), 'L-AA000')
        self.assertEqual(encode_public_id('buyer_request', 999), 'B-AA999')
        self.assertEqual(encode_public_id('buyer_request', 1000), 'B-AB000')
        self.assertEqual(encode_public_id('property_listing', 1908), 'L-AB908')

    def test_never_uses_confusing_letters(self):
        self.assertNotIn('I', ALPHABET)
        self.assertNotIn('O', ALPHABET)
        for seq in range(0, 40000, 7):
            self.assertRegex(encode_public_id('property_listing', seq), r'^L-[A-HJ-NP-Z]{2,}\d{3}$')

    def test_ids_are_unique_and_sort_naturally_within_a_length(self):
        seen = set()
        previous = None
        for seq in range(0, 60000):
            value = encode_public_id('property_listing', seq)
            self.assertNotIn(value, seen)
            seen.add(value)
            if previous and len(previous) == len(value):
                self.assertLess(previous, value)
            previous = value

    def test_a_complete_typed_id_has_exact_candidates_and_partial_text_has_none(self):
        self.assertEqual(sorted(exact_public_ids('AB908')), ['B-AB908', 'L-AB908'])
        self.assertEqual(exact_public_ids('LAB908')[-1], 'L-AB908')        # prefix reading
        self.assertEqual(sorted(exact_public_ids('LAB908')), ['B-LAB908', 'L-AB908', 'L-LAB908'])  # or three letters
        self.assertEqual(sorted(exact_public_ids('BAB908')), ['B-AB908', 'B-BAB908', 'L-BAB908'])
        for partial in ('AB', 'AB9', 'AB90', '908', 'A908', 'AB9081', 'IO908', 'LAB90'):
            self.assertEqual(exact_public_ids(partial), [], partial)
        for value in exact_public_ids('LAB908') + exact_public_ids('ZZ999'):
            self.assertIsNotNone(decode_public_id(value), value)           # only real, canonical IDs

    def test_capacity_reaches_millions_before_the_id_gets_longer(self):
        # 576,000 IDs with two letters, then 13,824,000 more with three.
        self.assertEqual(encode_public_id('property_listing', 575_999), 'L-ZZ999')
        self.assertEqual(encode_public_id('property_listing', 576_000), 'L-AAA000')
        self.assertEqual(encode_public_id('property_listing', 14_399_999), 'L-ZZZ999')
        self.assertEqual(encode_public_id('property_listing', 14_400_000), 'L-AAAA000')
        self.assertEqual(len(encode_public_id('property_listing', 5_000_000)), len('L-ABC123'))
        self.assertEqual(len(encode_public_id('property_listing', 10 ** 12)), 2 + 7 + 3)

    def test_round_trip(self):
        for seq in [0, 1, 999, 1000, 12345, 575_999, 576_000, 1_234_567, 14_399_999, 14_400_000, 987_654_321]:
            for kind in ('buyer_request', 'property_listing'):
                self.assertEqual(decode_public_id(encode_public_id(kind, seq)), (kind, seq))
        self.assertIsNone(decode_public_id('X-AB908'))
        self.assertIsNone(decode_public_id('L-IO908'))
        self.assertIsNone(decode_public_id('AB908'))

    def test_typed_queries_ignore_case_hyphen_and_spaces(self):
        self.assertEqual(normalize_id_query(' l-ab 908 '), 'LAB908')
        self.assertEqual(normalize_id_query('ab908'), 'AB908')
        self.assertEqual(normalize_id_query(''), '')

    def test_negative_sequence_is_rejected(self):
        with self.assertRaises(ValueError):
            encode_public_id('buyer_request', -1)


@unittest.skipUnless(os.getenv('XM_TEST_DATABASE_URL'), 'requires isolated XM_TEST_DATABASE_URL')
class PublicIdSqlParityTests(unittest.TestCase):
    """Bulk SQL allocation and Python decoding must agree on every ID."""

    def test_sql_function_matches_python(self):
        from pathlib import Path
        from test_workspace import connect_test_db
        samples = [0, 1, 9, 10, 99, 100, 999, 1000, 1001, 1908, 23999, 24000, 575_999, 576_000, 576_001, 600_123,
                   14_399_999, 14_400_000, 14_400_001, 345_000_000, 8_000_000_000]
        with connect_test_db() as conn:
            conn.execute(Path(__file__).with_name('schema.sql').read_text())
            for kind, prefix in (('buyer_request', 'B'), ('property_listing', 'L')):
                for seq in samples:
                    sql_value = conn.execute('SELECT xm.public_id(%s, %s::bigint) AS v', (prefix, seq)).fetchone()['v']
                    self.assertEqual(sql_value, encode_public_id(kind, seq), seq)
            rows = conn.execute("SELECT xm.public_id('L', n) AS v FROM generate_series(0, 4999, 1) AS n").fetchall()
            self.assertEqual([r['v'] for r in rows], [encode_public_id('property_listing', n) for n in range(5000)])


if __name__ == '__main__':
    unittest.main()
