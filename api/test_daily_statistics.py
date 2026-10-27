"""Daily counters must not launch repeated scans, including concurrent tabs."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from runtime_cache import daily_snapshot


class DailyStatisticsTests(unittest.TestCase):
    def test_persistence_refresh_day_and_company_isolation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XM_STATS_DIR': directory}):
            calls = []

            def load():
                calls.append(1)
                return {'count': len(calls)}

            with ThreadPoolExecutor(max_workers=6) as pool:
                answers = list(pool.map(lambda _: daily_snapshot('a', 'stats', load), range(6)))
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(answer['count'] == 1 for answer in answers))
            self.assertEqual(daily_snapshot('a', 'stats', load)['count'], 1)
            self.assertEqual(daily_snapshot('b', 'stats', load)['count'], 2)
            self.assertEqual(daily_snapshot('a', 'stats', load, refresh=True)['count'], 3)
            for path in Path(directory).glob('*.json'):
                data = json.loads(path.read_text())
                data['day'] = '2000-01-01'
                path.write_text(json.dumps(data))
            self.assertEqual(daily_snapshot('a', 'stats', load)['count'], 4)

    def test_failure_preserves_last_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XM_STATS_DIR': directory}):
            daily_snapshot('a', 'stats', lambda: {'count': 5})
            def fail():
                raise RuntimeError('database unavailable')
            with self.assertRaises(RuntimeError):
                daily_snapshot('a', 'stats', fail, refresh=True)
            self.assertEqual(daily_snapshot('a', 'stats', fail)['count'], 5)
