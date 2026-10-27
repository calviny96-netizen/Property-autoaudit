import unittest
from datetime import datetime

class DateFilterRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from workspace import date_filter
            cls.filter=staticmethod(date_filter)
        except ImportError:
            raise unittest.SkipTest('API dependencies required')

    def test_one_day_includes_last_minute(self):
        clause,params=self.filter('2026-09-11','2026-09-11')
        self.assertEqual(params,[datetime(2026,9,11),datetime(2026,9,12)])
        self.assertIn('r.sent_at < %s',clause)

    def test_range_with_times(self):
        _,params=self.filter('2026-08-31','2026-09-11','08:15','17:30')
        self.assertEqual(params,[datetime(2026,8,31,8,15),datetime(2026,9,11,17,31)])

    def test_bad_and_reversed_range(self):
        from fastapi import HTTPException
        for args in [('2026-09-12','2026-09-11'),('2026-09-11','2026-09-11','18:00','12:00'),('2026-02-30','2026-03-01'),('2026-09-11','2026-09-11','25:00','23:59')]:
            with self.assertRaises(HTTPException):self.filter(*args)

    def test_clear_filter(self):
        self.assertEqual(self.filter('',''),('',[]))

if __name__=='__main__':unittest.main()
