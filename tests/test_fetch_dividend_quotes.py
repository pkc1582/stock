from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

FM_SPEC = importlib.util.spec_from_file_location("fetch_market_under_test", ROOT / "scripts" / "fetch_market.py")
if FM_SPEC is None or FM_SPEC.loader is None:
    raise RuntimeError("could not load scripts/fetch_market.py")
fm = importlib.util.module_from_spec(FM_SPEC)
sys.modules[FM_SPEC.name] = fm
FM_SPEC.loader.exec_module(fm)

DQ_SPEC = importlib.util.spec_from_file_location(
    "fetch_dividend_quotes_under_test", ROOT / "scripts" / "fetch_dividend_quotes.py"
)
if DQ_SPEC is None or DQ_SPEC.loader is None:
    raise RuntimeError("could not load scripts/fetch_dividend_quotes.py")
dq = importlib.util.module_from_spec(DQ_SPEC)
sys.modules[DQ_SPEC.name] = dq
DQ_SPEC.loader.exec_module(dq)


CODES = [f"{number:06d}" for number in range(1, 11)]
TODAY = date(2026, 8, 9)  # Sunday


class LoadExistingQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "dividend-quotes.json"

    def test_missing_file_returns_empty(self) -> None:
        with patch.object(dq, "QUOTES_PATH", self.path):
            self.assertEqual(dq.load_existing_quotes(), {})

    def test_new_per_code_shape_is_read_as_is(self) -> None:
        self.path.write_text(json.dumps({
            "schemaVersion": 2, "source": "x",
            "quotes": {"111111": {"price": 100, "basisDate": "2026-08-01"}},
        }), encoding="utf-8")
        with patch.object(dq, "QUOTES_PATH", self.path):
            self.assertEqual(dq.load_existing_quotes(), {"111111": {"price": 100, "basisDate": "2026-08-01"}})

    def test_legacy_flat_shape_is_migrated_on_read(self) -> None:
        self.path.write_text(json.dumps({
            "schemaVersion": 1, "basisDate": "2026-08-01", "source": "x",
            "quotes": {"111111": 100},
        }), encoding="utf-8")
        with patch.object(dq, "QUOTES_PATH", self.path):
            self.assertEqual(dq.load_existing_quotes(), {"111111": {"price": 100, "basisDate": "2026-08-01"}})


class MainIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.track_path = Path(self.tmp.name) / "dividend-track.json"
        self.quotes_path = Path(self.tmp.name) / "dividend-quotes.json"
        self.track_path.write_text(
            json.dumps({"companies": [{"code": code} for code in CODES]}), encoding="utf-8"
        )

    def run_main(self, yahoo_quotes, data_go_kr_mock=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(dq, "TRACK_PATH", self.track_path),
            patch.object(dq, "QUOTES_PATH", self.quotes_path),
            patch.object(dq, "refresh_via_yahoo", return_value=yahoo_quotes),
            patch.object(fm, "current_kst_date", return_value=TODAY),
            patch.object(dq, "refresh_via_data_go_kr", data_go_kr_mock or Mock(side_effect=AssertionError("fallback should not run"))),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = dq.main()
        return result, stdout.getvalue(), stderr.getvalue()

    def test_partial_yahoo_success_writes_only_successful_codes(self) -> None:
        result, stdout, stderr = self.run_main({CODES[0]: (5_000, "2026-08-07")})

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("1/10", stdout)
        saved = json.loads(self.quotes_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["quotes"][CODES[0]], {"price": 5_000, "basisDate": "2026-08-07"})
        self.assertEqual(saved["source"], fm.YAHOO_SOURCE_LABEL)
        self.assertNotIn(CODES[1], saved["quotes"])

    def test_existing_newer_code_is_not_regressed(self) -> None:
        self.quotes_path.write_text(json.dumps({
            "schemaVersion": 2, "source": "x",
            "quotes": {CODES[0]: {"price": 9_999, "basisDate": "2026-08-10"}},
        }), encoding="utf-8")

        result, stdout, _ = self.run_main({CODES[0]: (1, "2026-08-05")})

        self.assertEqual(result, 0)
        self.assertIn("0/10", stdout)
        saved = json.loads(self.quotes_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["quotes"][CODES[0]], {"price": 9_999, "basisDate": "2026-08-10"})

    def test_total_yahoo_failure_falls_back_to_data_go_kr(self) -> None:
        fallback = Mock(return_value={code: {"price": 1_000, "basisDate": "2026-08-07"} for code in CODES})
        with patch.dict(os.environ, {"STOCK_API_SERVICE_KEY": "test key"}):
            result, stdout, _ = self.run_main({}, data_go_kr_mock=fallback)

        self.assertEqual(result, 0)
        self.assertIn("falling back to data.go.kr", stdout)
        fallback.assert_called_once()
        saved = json.loads(self.quotes_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], fm.SOURCE_LABEL)
        self.assertEqual(len(saved["quotes"]), 10)

    def test_total_failure_with_no_key_preserves_existing_file(self) -> None:
        self.quotes_path.write_text(json.dumps({
            "schemaVersion": 2, "source": "x", "quotes": {CODES[0]: {"price": 1, "basisDate": "2026-08-01"}},
        }), encoding="utf-8")
        original = self.quotes_path.read_bytes()

        with patch.dict(os.environ, {}, clear=True):
            result, stdout, _ = self.run_main({})

        self.assertEqual(result, 0)
        self.assertIn("skipped", stdout)
        self.assertEqual(self.quotes_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
