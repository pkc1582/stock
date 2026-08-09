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
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fetch_market_universe_under_test",
    ROOT / "scripts" / "fetch_market_universe.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load scripts/fetch_market_universe.py")
market = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = market
SPEC.loader.exec_module(market)


TODAY = date(2026, 8, 9)  # Sunday
LATEST_DATE = "2026-08-07"


def market_row(
    number: int,
    basis_date: str = LATEST_DATE,
    *,
    market_name: str | None = None,
    code: str | None = None,
    close_price: int | str | None = None,
) -> dict:
    short_code = code or f"{number:06d}"
    return {
        "basDt": basis_date.replace("-", ""),
        "srtnCd": short_code,
        "isinCd": f"KR{short_code}0000",
        "itmsNm": f"기업{number}",
        "mrktCtg": market_name or ("KOSPI" if number % 2 else "KOSDAQ"),
        "clpr": 10_000 + number if close_price is None else close_price,
        "trqu": number * 100,
        "trPrc": number * 1_000_000,
        "lstgStCnt": 1_000_000 + number,
        "mrktTotAmt": (10_000 + number) * (1_000_000 + number),
    }


def response_payload(
    items: list[dict],
    *,
    total_count: int | None = None,
    page_number: int = 1,
    page_size: int = market.PAGE_SIZE,
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
    def route(url: str, _timeout: float = market.HTTP_TIMEOUT_SECONDS) -> dict:
        parameters = parse_qs(urlparse(url).query)
        key = (parameters["basDt"][0], int(parameters["pageNo"][0]))
        return pages.get(key, response_payload([]))

    return route


class FetchMarketUniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.output_path = Path(self.temporary_directory.name) / "market-universe.json"

    def run_main(self, side_effect: object, *, environment: dict[str, str] | None = None):
        fetch_mock = Mock(side_effect=side_effect) if callable(side_effect) else Mock(return_value=side_effect)
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = (
            {
                "STOCK_API_SERVICE_KEY": "test stock key",
                "MARKET_UNIVERSE_API_ENDPOINT": "https://example.test/getStockPriceInfo",
            }
            if environment is None
            else environment
        )
        with (
            patch.object(market, "OUTPUT_PATH", self.output_path),
            patch.object(market, "MIN_KOSPI_ROWS", 1),
            patch.object(market, "MIN_KOSDAQ_ROWS", 1),
            patch.object(market, "MIN_TARGET_ROWS", 2),
            patch.object(market, "fetch_json", fetch_mock),
            patch.object(market, "current_kst_date", return_value=TODAY),
            patch.object(market, "generated_at_kst", return_value="2026-08-09T21:00:00+09:00"),
            patch.dict(os.environ, env, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = market.main()
        return result, fetch_mock, stdout.getvalue(), stderr.getvalue()

    def test_tiny_two_market_snapshot_is_not_treated_as_full_market(self) -> None:
        rows = [market_row(1), market_row(2)]
        with self.assertRaises(market.MarketUniverseError):
            market.build_snapshot(rows, len(rows), LATEST_DATE)

    def test_new_alphanumeric_equity_code_is_supported(self) -> None:
        self.assertEqual(market.normalize_code("A0001A0"), "0001A0")

    def test_small_pages_are_fully_downloaded_and_atomically_written(self) -> None:
        rows = [market_row(number) for number in range(1, market.PAGE_SIZE + 5)]
        pages = {
            ("20260807", 1): response_payload(
                rows[: market.PAGE_SIZE], total_count=len(rows), page_number=1
            ),
            ("20260807", 2): response_payload(
                rows[market.PAGE_SIZE :], total_count=len(rows), page_number=2
            ),
        }

        result, fetch_mock, stdout, stderr = self.run_main(route_pages(pages))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("atomically stored", stdout)
        self.assertEqual(fetch_mock.call_count, 2)
        requested = [parse_qs(urlparse(call.args[0]).query) for call in fetch_mock.call_args_list]
        self.assertEqual([params["pageNo"][0] for params in requested], ["1", "2"])
        self.assertEqual({params["numOfRows"][0] for params in requested}, {"200"})
        self.assertEqual({params["basDt"][0] for params in requested}, {"20260807"})

        snapshot = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["basisDate"], LATEST_DATE)
        self.assertEqual(snapshot["counts"]["providerRows"], len(rows))
        self.assertEqual(snapshot["counts"]["storedRows"], len(rows))
        self.assertEqual(len(snapshot["securities"]), len(rows))
        self.assertTrue(snapshot["validation"]["allAdvertisedPagesFetched"])
        self.assertNotIn("response", snapshot)
        self.assertNotIn("serviceKey", json.dumps(snapshot))
        first = snapshot["securities"][0]
        self.assertEqual(
            set(first),
            {
                "code",
                "isin",
                "name",
                "market",
                "closePrice",
                "tradingVolume",
                "tradingValue",
                "listedShares",
                "marketCap",
            },
        )

    def test_non_target_market_is_validated_but_not_stored(self) -> None:
        rows = [
            market_row(1, market_name="KOSPI"),
            market_row(2, market_name="KOSDAQ"),
            market_row(3, market_name="KONEX"),
        ]

        result, _, _, stderr = self.run_main(response_payload(rows))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        snapshot = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["counts"]["providerRows"], 3)
        self.assertEqual(snapshot["counts"]["storedRows"], 2)
        self.assertEqual({item["market"] for item in snapshot["securities"]}, {"KOSPI", "KOSDAQ"})

    def test_empty_latest_date_falls_back_to_prior_business_date(self) -> None:
        rows = [
            market_row(1, "2026-08-06", market_name="KOSPI"),
            market_row(2, "2026-08-06", market_name="KOSDAQ"),
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

    def assert_failure_preserves(self, payload: object) -> str:
        original = b'{"verified":true}\n'
        self.output_path.write_bytes(original)
        result, _, _, stderr = self.run_main(payload)
        self.assertEqual(result, 1)
        self.assertIn("existing snapshot preserved", stderr)
        self.assertEqual(self.output_path.read_bytes(), original)
        self.assertFalse(self.output_path.with_suffix(".json.tmp").exists())
        return stderr

    def test_duplicate_code_preserves_existing_file(self) -> None:
        rows = [
            market_row(1, market_name="KOSPI"),
            market_row(2, market_name="KOSDAQ", code="000001"),
        ]
        stderr = self.assert_failure_preserves(response_payload(rows))
        self.assertIn("duplicate", stderr)

    def test_mixed_basis_dates_preserve_existing_file(self) -> None:
        rows = [
            market_row(1, market_name="KOSPI"),
            market_row(2, "2026-08-06", market_name="KOSDAQ"),
        ]
        stderr = self.assert_failure_preserves(response_payload(rows))
        self.assertIn("basis date", stderr)

    def test_invalid_numeric_field_preserves_existing_file(self) -> None:
        rows = [
            market_row(1, market_name="KOSPI", close_price=0),
            market_row(2, market_name="KOSDAQ"),
        ]
        stderr = self.assert_failure_preserves(response_payload(rows))
        self.assertIn("closing price", stderr)

    def test_truncated_final_page_preserves_existing_file(self) -> None:
        first = [market_row(number) for number in range(1, market.PAGE_SIZE + 1)]
        second = [market_row(market.PAGE_SIZE + 1)]
        pages = {
            ("20260807", 1): response_payload(
                first, total_count=market.PAGE_SIZE + 2, page_number=1
            ),
            ("20260807", 2): response_payload(
                second, total_count=market.PAGE_SIZE + 2, page_number=2
            ),
        }
        stderr = self.assert_failure_preserves(route_pages(pages))
        self.assertIn("advertised", stderr)

    def test_pagination_metadata_drift_preserves_existing_file(self) -> None:
        first = [market_row(number) for number in range(1, market.PAGE_SIZE + 1)]
        second = [market_row(market.PAGE_SIZE + 1)]
        pages = {
            ("20260807", 1): response_payload(
                first, total_count=market.PAGE_SIZE + 1, page_number=1
            ),
            ("20260807", 2): response_payload(
                second, total_count=market.PAGE_SIZE + 1, page_number=2, page_size=100
            ),
        }
        stderr = self.assert_failure_preserves(route_pages(pages))
        self.assertIn("pagination metadata", stderr)

    def test_provider_message_and_key_are_not_logged(self) -> None:
        payload = response_payload(
            [], result_code="22", result_message="secret request detail"
        )
        stderr = self.assert_failure_preserves(payload)
        self.assertIn("code=22", stderr)
        self.assertNotIn("secret request detail", stderr)
        self.assertNotIn("test stock key", stderr)

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

    def test_transport_retries_are_bounded_and_credential_safe(self) -> None:
        secret_url = "https://example.test/?serviceKey=private-value"
        with (
            patch.object(market, "urlopen", side_effect=URLError(secret_url)) as opener,
            patch.object(market.time, "sleep"),
            self.assertRaises(market.MarketUniverseError) as raised,
        ):
            market.fetch_json(secret_url)

        self.assertEqual(opener.call_count, market.MAX_HTTP_ATTEMPTS)
        self.assertIn("URLError", str(raised.exception))
        self.assertNotIn("private-value", str(raised.exception))

    def test_http_error_label_has_status_without_url(self) -> None:
        error = HTTPError(
            "https://example.test/?serviceKey=private-value",
            503,
            "unavailable",
            None,
            None,
        )
        self.assertEqual(market.transport_error_label(error), "HTTPError code=503")


if __name__ == "__main__":
    unittest.main()
