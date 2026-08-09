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
    "fetch_market_under_test", ROOT / "scripts" / "fetch_market.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load scripts/fetch_market.py")
market = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = market
SPEC.loader.exec_module(market)


CODES = [f"{number:06d}" for number in range(1, 21)]
TODAY = date(2026, 8, 9)


def company_data(basis_date: str = "2026-08-01", official: bool = False) -> dict:
    source = market.SOURCE_LABEL if official else "verified fixture"
    status = "updated_from_data_go_kr" if official else "verified_fixture"
    return {
        "priceBasisDate": basis_date,
        "marketDataStatus": status,
        "companies": [
            {
                "code": code,
                "name": f"Company {code}",
                "currentPrice": index * 1_000,
                "priceBasisDate": basis_date,
                "priceSource": source,
            }
            for index, code in enumerate(CODES, start=1)
        ],
    }


def quote(code: str, basis_date: str, price: int | str | None = None) -> dict:
    return {
        "srtnCd": code,
        "basDt": basis_date.replace("-", ""),
        "clpr": price if price is not None else int(code) * 100 + 10_000,
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


def route_dates(payloads: dict[str, object]):
    """Build a fetch_json side effect keyed by requested YYYYMMDD basDt."""

    def route(url: str, _timeout: float = market.HTTP_TIMEOUT_SECONDS) -> dict:
        requested_date = parse_qs(urlparse(url).query)["basDt"][0]
        value = payloads.get(requested_date, response_payload([]))
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, dict):
            raise AssertionError(f"invalid routed payload for {requested_date}")
        return value

    return route


class FetchMarketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.data_path = Path(self.temporary_directory.name) / "companies.json"

    def write_fixture(self, value: dict) -> bytes:
        self.data_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.data_path.read_bytes()

    def read_fixture(self) -> dict:
        return json.loads(self.data_path.read_text(encoding="utf-8"))

    def run_main(self, side_effect: object) -> tuple[int, Mock, str, str]:
        if callable(side_effect) or isinstance(side_effect, BaseException):
            fetch_mock = Mock(side_effect=side_effect)
        else:
            fetch_mock = Mock(return_value=side_effect)
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {
            "STOCK_API_SERVICE_KEY": "test service key",
            "STOCK_API_ENDPOINT": "https://example.test/getStockPriceInfo",
        }
        with (
            patch.object(market, "DATA_PATH", self.data_path),
            patch.object(market, "fetch_json", fetch_mock),
            patch.object(market, "current_kst_date", return_value=TODAY),
            patch.object(market.time, "sleep"),
            patch.dict(os.environ, environment, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = market.main()
        return result, fetch_mock, stdout.getvalue(), stderr.getvalue()

    def assert_failed_without_write(
        self, side_effect: object, value: dict | None = None
    ) -> tuple[Mock, str]:
        original = self.write_fixture(value or company_data())
        result, fetch_mock, _, stderr = self.run_main(side_effect)
        self.assertEqual(result, 1)
        self.assertIn("verified snapshot preserved", stderr)
        self.assertEqual(self.data_path.read_bytes(), original)
        self.assertFalse(self.data_path.with_suffix(".json.tmp").exists())
        return fetch_mock, stderr

    def requested_dates(self, fetch_mock: Mock) -> list[str]:
        return [
            parse_qs(urlparse(call.args[0]).query)["basDt"][0]
            for call in fetch_mock.call_args_list
        ]

    def test_exact_date_probe_skips_non_trading_day_and_updates_atomically(self) -> None:
        self.write_fixture(company_data())
        items = [quote(code, "2026-08-08") for code in CODES]
        router = route_dates({"20260809": response_payload([]), "20260808": response_payload(items)})

        result, fetch_mock, stdout, stderr = self.run_main(router)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("20/20", stdout)
        self.assertEqual(self.requested_dates(fetch_mock), ["20260809", "20260808"])
        for call in fetch_mock.call_args_list:
            parameters = parse_qs(urlparse(call.args[0]).query)
            self.assertEqual(parameters["pageNo"], ["1"])
            self.assertEqual(parameters["numOfRows"], ["5000"])
            self.assertIn("basDt", parameters)
            self.assertNotIn("beginBasDt", parameters)
            self.assertNotIn("endBasDt", parameters)
            self.assertLessEqual(call.args[1], 15)

        updated = self.read_fixture()
        self.assertEqual(updated["priceBasisDate"], "2026-08-08")
        self.assertEqual(updated["marketDataStatus"], "updated_from_data_go_kr")
        self.assertEqual({item["code"] for item in updated["companies"]}, set(CODES))
        self.assertEqual({item["priceBasisDate"] for item in updated["companies"]}, {"2026-08-08"})
        self.assertEqual({item["priceSource"] for item in updated["companies"]}, {market.SOURCE_LABEL})

    def test_two_exact_date_pages_are_combined_within_safe_limit(self) -> None:
        self.write_fixture(company_data())
        first_page = [quote("999999", "2026-08-09", 1) for _ in range(20)]
        second_page = [quote(code, "2026-08-09") for code in CODES]

        def fetch_for_page(url: str, _timeout: float) -> dict:
            parameters = parse_qs(urlparse(url).query)
            self.assertEqual(parameters["basDt"], ["20260809"])
            page_number = int(parameters["pageNo"][0])
            if page_number == 1:
                return response_payload(
                    first_page, total_count=40, page_number=1, page_size=20
                )
            return response_payload(
                second_page, total_count=40, page_number=2, page_size=20
            )

        result, fetch_mock, _, stderr = self.run_main(fetch_for_page)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(self.read_fixture()["priceBasisDate"], "2026-08-09")

    def test_incomplete_latest_date_falls_back_to_complete_prior_date(self) -> None:
        self.write_fixture(company_data())
        latest = [quote(code, "2026-08-09") for code in CODES[:-1]]
        prior = [quote(code, "2026-08-08") for code in CODES]
        router = route_dates(
            {"20260809": response_payload(latest), "20260808": response_payload(prior)}
        )

        result, fetch_mock, _, stderr = self.run_main(router)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(self.requested_dates(fetch_mock), ["20260809", "20260808"])
        updated = self.read_fixture()
        self.assertEqual(updated["priceBasisDate"], "2026-08-08")
        self.assertEqual({item["priceBasisDate"] for item in updated["companies"]}, {"2026-08-08"})

    def test_exact_date_filter_mismatch_is_never_accepted(self) -> None:
        items = [quote(code, "2026-08-09") for code in CODES]
        items[-1]["basDt"] = "20260808"

        self.assert_failed_without_write(
            route_dates({"20260809": response_payload(items)})
        )

    def test_no_complete_date_preserves_original_file(self) -> None:
        incomplete_by_date = {
            (TODAY.replace(day=TODAY.day - offset)).strftime("%Y%m%d"): response_payload(
                [
                    quote(code, TODAY.replace(day=TODAY.day - offset).isoformat())
                    for code in CODES[:-1]
                ]
            )
            for offset in range(market.LOOKBACK_DAYS)
        }

        fetch_mock, _ = self.assert_failed_without_write(route_dates(incomplete_by_date))

        self.assertEqual(fetch_mock.call_count, market.LOOKBACK_DAYS)

    def test_invalid_price_preserves_original_file(self) -> None:
        items = [quote(code, "2026-08-09") for code in CODES]
        items[-1]["clpr"] = 0

        self.assert_failed_without_write(
            route_dates({"20260809": response_payload(items)})
        )

    def test_duplicate_company_row_preserves_original_file(self) -> None:
        items = [quote(code, "2026-08-09") for code in CODES]
        items.append(quote(CODES[0], "2026-08-09"))

        self.assert_failed_without_write(
            route_dates({"20260809": response_payload(items)})
        )

    def test_more_than_two_pages_is_rejected_before_second_request(self) -> None:
        payload = response_payload(
            [quote("999999", "2026-08-09", 1)],
            total_count=10_001,
            page_size=5_000,
        )

        fetch_mock, _ = self.assert_failed_without_write(
            route_dates({"20260809": payload})
        )

        self.assertEqual(fetch_mock.call_count, 1)

    def test_truncated_page_preserves_original_file(self) -> None:
        items = [quote(code, "2026-08-09") for code in CODES[:-1]]
        payload = response_payload(items, total_count=20)

        self.assert_failed_without_write(
            route_dates({"20260809": payload})
        )

    def test_provider_error_logs_only_result_code(self) -> None:
        payload = response_payload(
            [], result_code="22", result_message="secret request detail"
        )

        _, stderr = self.assert_failed_without_write(
            route_dates({"20260809": payload})
        )

        self.assertIn("provider API error code=22", stderr)
        self.assertNotIn("secret request detail", stderr)
        self.assertNotIn("test service key", stderr)

    def test_transport_retry_is_limited_to_two_and_log_is_safe(self) -> None:
        fetch_mock, stderr = self.assert_failed_without_write(
            URLError("https://example.test/?serviceKey=private-value")
        )

        self.assertEqual(fetch_mock.call_count, 2)
        self.assertIn("URLError", stderr)
        self.assertNotIn("private-value", stderr)
        self.assertNotIn("test service key", stderr)

    def test_http_error_log_includes_status_but_not_url(self) -> None:
        error = HTTPError(
            "https://example.test/?serviceKey=private-value",
            503,
            "unavailable",
            None,
            None,
        )

        _, stderr = self.assert_failed_without_write(error)

        self.assertIn("HTTPError code=503", stderr)
        self.assertNotIn("private-value", stderr)
        self.assertNotIn("example.test", stderr)

    def test_older_date_is_not_requested_or_used(self) -> None:
        latest_empty = response_payload([])
        stored_date_incomplete = response_payload(
            [quote(code, "2026-08-08") for code in CODES[:-1]]
        )
        router = route_dates(
            {"20260809": latest_empty, "20260808": stored_date_incomplete}
        )

        fetch_mock, _ = self.assert_failed_without_write(
            router,
            value=company_data(basis_date="2026-08-08"),
        )

        self.assertEqual(self.requested_dates(fetch_mock), ["20260809", "20260808"])

    def test_identical_official_snapshot_is_not_rewritten(self) -> None:
        value = company_data(basis_date="2026-08-08", official=True)
        for company, code in zip(value["companies"], CODES, strict=True):
            company["currentPrice"] = int(code) * 100 + 10_000
        original = self.write_fixture(value)
        items = [quote(code, "2026-08-08") for code in CODES]
        router = route_dates(
            {"20260809": response_payload([]), "20260808": response_payload(items)}
        )

        result, fetch_mock, stdout, stderr = self.run_main(router)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertIn("no file changes", stdout)
        self.assertEqual(self.data_path.read_bytes(), original)

    def test_runtime_limits_remain_below_two_minutes(self) -> None:
        self.assertEqual(market.HTTP_TIMEOUT_SECONDS, 15)
        self.assertGreaterEqual(market.LOOKBACK_DAYS, 7)
        self.assertLessEqual(market.LOOKBACK_DAYS, 10)
        self.assertLess(market.TOTAL_HTTP_BUDGET_SECONDS, 120)


if __name__ == "__main__":
    unittest.main()
