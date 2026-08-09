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
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sync_universe_under_test", ROOT / "scripts" / "sync_universe.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load scripts/sync_universe.py")
universe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = universe
SPEC.loader.exec_module(universe)


TODAY = date(2026, 8, 9)  # Sunday
LATEST_DATE = "2026-08-07"


def listed_row(
    number: int,
    basis_date: str = LATEST_DATE,
    *,
    market: str | None = None,
    name: str | None = None,
    corporation_name: str | None = None,
) -> dict:
    code = f"{number:06d}"
    return {
        "basDt": basis_date.replace("-", ""),
        "srtnCd": code,
        "isinCd": f"KR{code}0000",
        "mrktCtg": market or ("KOSPI" if number % 2 else "KOSDAQ"),
        "itmsNm": name or f"기업{number}",
        "crno": f"{number:013d}",
        "corpNm": corporation_name or f"기업{number} 주식회사",
    }


def response_payload(
    items: list[dict],
    *,
    total_count: int | None = None,
    page_number: int = 1,
    page_size: int = universe.PAGE_SIZE,
    result_code: str = "00",
    result_message: str = "NORMAL SERVICE",
) -> dict:
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": result_message},
            "body": {
                "numOfRows": page_size,
                "pageNo": page_number,
                "totalCount": len(items) if total_count is None else total_count,
                "items": {"item": items},
            },
        }
    }


def route_pages(pages: dict[tuple[str, int], dict]):
    def route(url: str, _timeout: float = universe.HTTP_TIMEOUT_SECONDS) -> dict:
        parameters = parse_qs(urlparse(url).query)
        key = (parameters["basDt"][0], int(parameters["pageNo"][0]))
        return pages.get(key, response_payload([]))

    return route


class SyncUniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.output_path = Path(self.temporary_directory.name) / "universe.json"

    def run_main(self, side_effect: object, *, environment: dict[str, str] | None = None):
        fetch_mock = Mock(side_effect=side_effect) if callable(side_effect) else Mock(return_value=side_effect)
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = (
            {
                "KRX_LISTED_API_KEY": "test listed key",
                "KRX_LISTED_API_ENDPOINT": "https://example.test/getItemInfo",
            }
            if environment is None
            else environment
        )
        with (
            patch.object(universe, "OUTPUT_PATH", self.output_path),
            patch.object(universe, "MIN_KOSPI_ROWS", 1),
            patch.object(universe, "MIN_KOSDAQ_ROWS", 1),
            patch.object(universe, "MIN_TARGET_ROWS", 2),
            patch.object(universe, "fetch_json", fetch_mock),
            patch.object(universe, "current_kst_date", return_value=TODAY),
            patch.object(universe, "generated_at_kst", return_value="2026-08-09T21:00:00+09:00"),
            patch.dict(os.environ, env, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = universe.main()
        return result, fetch_mock, stdout.getvalue(), stderr.getvalue()

    def test_tiny_two_market_snapshot_is_not_treated_as_full_universe(self) -> None:
        rows = [listed_row(1), listed_row(2)]
        with self.assertRaises(universe.UniverseSyncError):
            universe.build_snapshot(rows, len(rows), LATEST_DATE)

    def test_new_alphanumeric_equity_code_is_supported(self) -> None:
        self.assertEqual(universe.normalize_code("A0001A0"), "0001A0")

    def test_downloads_every_advertised_page_and_writes_processed_snapshot(self) -> None:
        rows = [listed_row(number) for number in range(1, universe.PAGE_SIZE + 5)]
        pages = {
            ("20260807", 1): response_payload(
                rows[: universe.PAGE_SIZE], total_count=len(rows), page_number=1
            ),
            ("20260807", 2): response_payload(
                rows[universe.PAGE_SIZE :], total_count=len(rows), page_number=2
            ),
        }

        result, fetch_mock, stdout, stderr = self.run_main(route_pages(pages))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("atomically stored", stdout)
        self.assertEqual(fetch_mock.call_count, 2)
        requested = [parse_qs(urlparse(call.args[0]).query) for call in fetch_mock.call_args_list]
        self.assertEqual({item["basDt"][0] for item in requested}, {"20260807"})
        self.assertEqual([item["pageNo"][0] for item in requested], ["1", "2"])
        self.assertEqual({item["numOfRows"][0] for item in requested}, {str(universe.PAGE_SIZE)})
        self.assertNotIn("test listed key", stdout + stderr)

        snapshot = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["basisDate"], LATEST_DATE)
        self.assertIs(snapshot["managed_item_checked"], False)
        self.assertEqual(snapshot["counts"]["providerRows"], len(rows))
        self.assertEqual(len(snapshot["companies"]), len(rows))
        self.assertEqual(snapshot["companies"][0]["managementStatus"], "unverified")
        self.assertIs(snapshot["companies"][0]["classificationVerified"], False)
        self.assertTrue(any("관리종목" in item for item in snapshot["limitations"]))
        self.assertNotIn("response", snapshot)
        self.assertNotIn("serviceKey", json.dumps(snapshot))

    def test_empty_latest_business_date_falls_back_to_previous_date(self) -> None:
        rows = [
            listed_row(1, "2026-08-06", market="KOSPI"),
            listed_row(2, "2026-08-06", market="KOSDAQ"),
        ]
        pages = {
            ("20260807", 1): response_payload([]),
            ("20260806", 1): response_payload(rows),
        }

        result, fetch_mock, _, stderr = self.run_main(route_pages(pages))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fetch_mock.call_count, 2)
        snapshot = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["basisDate"], "2026-08-06")

    def test_classification_is_conservative_and_explainable(self) -> None:
        cases = {
            ("삼성전자", "삼성전자 주식회사"): ("common_candidate", None),
            ("삼성전자우", "삼성전자 주식회사"): ("name_likely_preferred", "name_indicates_preferred"),
            ("현대차2우B", "현대자동차 주식회사"): ("name_likely_preferred", "name_indicates_preferred"),
            ("신한제16호스팩", "신한제16호기업인수목적 주식회사"): ("name_likely_spac", "name_indicates_spac"),
            ("SK리츠", "에스케이위탁관리부동산투자회사"): ("name_likely_reit", "name_indicates_reit"),
        }
        for names, expected in cases.items():
            with self.subTest(names=names):
                self.assertEqual(universe.classify_name(*names), expected)

    def test_duplicate_code_preserves_existing_file(self) -> None:
        original = b'{"verified":true}\n'
        self.output_path.write_bytes(original)
        rows = [
            listed_row(1, market="KOSPI"),
            listed_row(1, market="KOSDAQ", name="다른 이름"),
        ]

        result, _, _, stderr = self.run_main(response_payload(rows))

        self.assertEqual(result, 1)
        self.assertIn("duplicate", stderr)
        self.assertEqual(self.output_path.read_bytes(), original)
        self.assertFalse(self.output_path.with_suffix(".json.tmp").exists())

    def test_mixed_basis_dates_preserve_existing_file(self) -> None:
        original = b'{"verified":true}\n'
        self.output_path.write_bytes(original)
        rows = [
            listed_row(1, market="KOSPI"),
            listed_row(2, "2026-08-06", market="KOSDAQ"),
        ]

        result, _, _, stderr = self.run_main(response_payload(rows))

        self.assertEqual(result, 1)
        self.assertIn("basis date", stderr)
        self.assertEqual(self.output_path.read_bytes(), original)

    def test_truncated_or_drifting_pagination_preserves_existing_file(self) -> None:
        original = b'{"verified":true}\n'
        self.output_path.write_bytes(original)
        first = [listed_row(number) for number in range(1, universe.PAGE_SIZE + 1)]
        second = [listed_row(universe.PAGE_SIZE + 1)]
        pages = {
            ("20260807", 1): response_payload(
                first, total_count=universe.PAGE_SIZE + 2, page_number=1
            ),
            ("20260807", 2): response_payload(
                second, total_count=universe.PAGE_SIZE + 1, page_number=2
            ),
        }

        result, _, _, stderr = self.run_main(route_pages(pages))

        self.assertEqual(result, 1)
        self.assertIn("pagination metadata", stderr)
        self.assertEqual(self.output_path.read_bytes(), original)

    def test_provider_message_and_key_are_not_logged(self) -> None:
        original = b'{"verified":true}\n'
        self.output_path.write_bytes(original)
        payload = response_payload(
            [], result_code="22", result_message="secret request detail"
        )

        result, _, stdout, stderr = self.run_main(payload)

        self.assertEqual(result, 1)
        self.assertIn("code=22", stderr)
        self.assertNotIn("secret request detail", stdout + stderr)
        self.assertNotIn("test listed key", stdout + stderr)
        self.assertEqual(self.output_path.read_bytes(), original)

    def test_stock_key_is_used_as_documented_fallback(self) -> None:
        rows = [listed_row(1, market="KOSPI"), listed_row(2, market="KOSDAQ")]
        result, fetch_mock, _, stderr = self.run_main(
            response_payload(rows),
            environment={
                "STOCK_API_SERVICE_KEY": "fallback key",
                "KRX_LISTED_API_ENDPOINT": "https://example.test/getItemInfo",
            },
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        parameters = parse_qs(urlparse(fetch_mock.call_args.args[0]).query)
        self.assertEqual(parameters["serviceKey"], ["fallback key"])

    def test_missing_key_does_not_touch_existing_file(self) -> None:
        original = b'{"verified":true}\n'
        self.output_path.write_bytes(original)

        result, fetch_mock, stdout, stderr = self.run_main(
            response_payload([]), environment={}
        )

        self.assertEqual(result, 0)
        self.assertEqual(fetch_mock.call_count, 0)
        self.assertIn("skipped", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(self.output_path.read_bytes(), original)

    def test_transport_error_is_bounded_and_credential_safe(self) -> None:
        secret_url = "https://example.test/?serviceKey=private-value"
        with (
            patch.object(universe, "urlopen", side_effect=URLError(secret_url)) as opener,
            patch.object(universe.time, "sleep"),
            self.assertRaises(universe.UniverseSyncError) as raised,
        ):
            universe.fetch_json(secret_url)

        self.assertEqual(opener.call_count, universe.MAX_HTTP_ATTEMPTS)
        self.assertIn("URLError", str(raised.exception))
        self.assertNotIn("private-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
