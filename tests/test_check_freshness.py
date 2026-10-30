from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_freshness_under_test", ROOT / "scripts" / "check_freshness.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load scripts/check_freshness.py")
freshness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = freshness
SPEC.loader.exec_module(freshness)


class BusinessDaysTests(unittest.TestCase):
    def test_same_day_is_zero(self):
        self.assertEqual(freshness.business_days_between(date(2026, 10, 13), date(2026, 10, 13)), 0)

    def test_weekend_is_not_counted(self):
        # Tue 10/13 -> Fri 10/16 crosses Wed/Thu/Fri only.
        self.assertEqual(freshness.business_days_between(date(2026, 10, 13), date(2026, 10, 16)), 3)

    def test_crossing_weekend_does_not_add_extra_days(self):
        # Tue 10/13 -> Sat 10/17 still only 3 business days (weekend excluded).
        self.assertEqual(freshness.business_days_between(date(2026, 10, 13), date(2026, 10, 17)), 3)

    def test_crossing_weekend_into_next_week(self):
        # Tue 10/13 -> Mon 10/19 adds the following Monday.
        self.assertEqual(freshness.business_days_between(date(2026, 10, 13), date(2026, 10, 19)), 4)


class ParseDateTests(unittest.TestCase):
    def test_parses_iso_date(self):
        self.assertEqual(freshness.parse_date("2026-10-13"), date(2026, 10, 13))

    def test_none_and_invalid_return_none(self):
        self.assertIsNone(freshness.parse_date(None))
        self.assertIsNone(freshness.parse_date("not-a-date"))


if __name__ == "__main__":
    unittest.main()
