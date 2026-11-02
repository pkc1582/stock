from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
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
TODAY = date(2026, 8, 9)  # Sunday
LATEST_TRADING_DATE = "2026-08-07"


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
    page_size: int = market.CODE_QUERY_PAGE_SIZE,
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


def complete_responses(basis_date: str) -> dict[tuple[str, str], object]:
    date_digits = basis_date.replace("-", "")
    return {
        (date_digits, code): response_payload([quote(code, basis_date)])
        for code in CODES
    }


def route_quotes(payloads: dict[tuple[str, str], object]):
    """Build a fetch_json side effect keyed by (YYYYMMDD basDt, stock code)."""

    def route(url: str, _timeout: float = market.HTTP_TIMEOUT_SECONDS) -> dict:
        parameters = parse_qs(urlparse(url).query)
        key = (parameters["basDt"][0], parameters["likeSrtnCd"][0])
        value = payloads.get(key, response_payload([]))
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, dict):
            raise AssertionError(f"invalid routed payload for {key}")
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
            # These tests exercise the data.go.kr fallback path exclusively;
            # force Yahoo to return no quotes so main() falls through to it.
            patch.object(market, "fetch_yahoo_snapshot", return_value={}),
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

    @staticmethod
    def requested_parameters(fetch_mock: Mock) -> list[dict[str, list[str]]]:
        return [
            parse_qs(urlparse(call.args[0]).query)
            for call in fetch_mock.call_args_list
        ]

    def test_code_filtered_business_date_updates_all_companies_atomically(self) -> None:
        self.write_fixture(company_data())
        result, fetch_mock, stdout, stderr = self.run_main(
            route_quotes(complete_responses(LATEST_TRADING_DATE))
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("20/20", stdout)
        self.assertEqual(fetch_mock.call_count, 20)
        parameters = self.requested_parameters(fetch_mock)
        self.assertEqual({item["basDt"][0] for item in parameters}, {"20260807"})
        self.assertEqual({item["likeSrtnCd"][0] for item in parameters}, set(CODES))
        for item in parameters:
            self.assertEqual(item["pageNo"], ["1"])
            self.assertEqual(item["numOfRows"], ["10"])
            self.assertNotIn("beginBasDt", item)
            self.assertNotIn("endBasDt", item)

        updated = self.read_fixture()
        self.assertEqual(updated["priceBasisDate"], LATEST_TRADING_DATE)
        self.assertEqual(updated["marketDataStatus"], "updated_from_data_go_kr")
        self.assertEqual({item["code"] for item in updated["companies"]}, set(CODES))
        self.assertEqual(
            {item["priceBasisDate"] for item in updated["companies"]},
            {LATEST_TRADING_DATE},
        )
        self.assertEqual(
            {item["priceSource"] for item in updated["companies"]},
            {"공공데이터포털 금융위원회 주식시세정보"},
        )

    def test_partial_latest_date_falls_back_to_complete_prior_date(self) -> None:
        payloads = complete_responses("2026-08-07")
        payloads[("20260807", CODES[-1])] = response_payload([])
        payloads.update(complete_responses("2026-08-06"))
        self.write_fixture(company_data())

        result, fetch_mock, _, stderr = self.run_main(route_quotes(payloads))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fetch_mock.call_count, 40)
        self.assertEqual(self.read_fixture()["priceBasisDate"], "2026-08-06")

    def test_all_missing_dates_preserve_original_file(self) -> None:
        fetch_mock, _ = self.assert_failed_without_write(route_quotes({}))

        requested_dates = {
            item["basDt"][0] for item in self.requested_parameters(fetch_mock)
        }
        self.assertNotIn("20260809", requested_dates)
        self.assertNotIn("20260808", requested_dates)
        self.assertEqual(
            requested_dates,
            {"20260807", "20260806", "20260805", "20260804", "20260803"},
        )

    def test_mismatched_code_is_never_accepted(self) -> None:
        payloads = complete_responses(LATEST_TRADING_DATE)
        payloads[("20260807", CODES[-1])] = response_payload(
            [quote(CODES[-2], LATEST_TRADING_DATE)]
        )
        self.assert_failed_without_write(route_quotes(payloads))

    def test_mismatched_date_is_never_accepted(self) -> None:
        payloads = complete_responses(LATEST_TRADING_DATE)
        payloads[("20260807", CODES[-1])] = response_payload(
            [quote(CODES[-1], "2026-08-06")]
        )
        self.assert_failed_without_write(route_quotes(payloads))

    def test_ambiguous_code_filter_preserves_original_file(self) -> None:
        payloads = complete_responses(LATEST_TRADING_DATE)
        payloads[("20260807", CODES[-1])] = response_payload(
            [
                quote(CODES[-1], LATEST_TRADING_DATE),
                quote(CODES[-1], LATEST_TRADING_DATE),
            ]
        )
        self.assert_failed_without_write(route_quotes(payloads))

    def test_invalid_price_preserves_original_file(self) -> None:
        payloads = complete_responses(LATEST_TRADING_DATE)
        payloads[("20260807", CODES[-1])] = response_payload(
            [quote(CODES[-1], LATEST_TRADING_DATE, 0)]
        )
        self.assert_failed_without_write(route_quotes(payloads))

    def test_truncated_response_preserves_original_file(self) -> None:
        payloads = complete_responses(LATEST_TRADING_DATE)
        payloads[("20260807", CODES[-1])] = response_payload([], total_count=1)
        self.assert_failed_without_write(route_quotes(payloads))

    def test_provider_error_logs_only_result_code(self) -> None:
        payload = response_payload(
            [], result_code="22", result_message="secret request detail"
        )
        _, stderr = self.assert_failed_without_write(payload)

        self.assertIn("provider API error code=22", stderr)
        self.assertNotIn("secret request detail", stderr)
        self.assertNotIn("test service key", stderr)

    def test_transport_retry_is_limited_and_error_is_safe(self) -> None:
        fetch_mock = Mock(
            side_effect=URLError("https://example.test/?serviceKey=private-value")
        )
        budget = market.ApiCallBudget(2)
        with (
            patch.object(market, "fetch_json", fetch_mock),
            patch.object(market.time, "sleep"),
            self.assertRaises(market.MarketRefreshError) as raised,
        ):
            market.request_with_budget(
                "https://example.test/?serviceKey=private-value",
                market.time.monotonic() + 30,
                budget,
            )

        message = str(raised.exception)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertIn("URLError", message)
        self.assertNotIn("private-value", message)

    def test_http_error_label_includes_status_but_not_url(self) -> None:
        error = HTTPError(
            "https://example.test/?serviceKey=private-value",
            503,
            "unavailable",
            None,
            None,
        )
        self.assertEqual(market.transport_error_label(error), "HTTPError code=503")

    def test_older_date_is_not_requested_or_used(self) -> None:
        fetch_mock, _ = self.assert_failed_without_write(
            route_quotes({}),
            value=company_data(basis_date=LATEST_TRADING_DATE),
        )

        self.assertEqual(
            {item["basDt"][0] for item in self.requested_parameters(fetch_mock)},
            {"20260807"},
        )

    def test_identical_official_snapshot_is_not_rewritten(self) -> None:
        value = company_data(basis_date=LATEST_TRADING_DATE, official=True)
        for company, code in zip(value["companies"], CODES, strict=True):
            company["currentPrice"] = int(code) * 100 + 10_000
        original = self.write_fixture(value)

        result, fetch_mock, stdout, stderr = self.run_main(
            route_quotes(complete_responses(LATEST_TRADING_DATE))
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fetch_mock.call_count, 20)
        self.assertIn("no file changes", stdout)
        self.assertEqual(self.data_path.read_bytes(), original)

    def test_runtime_and_traffic_limits_are_bounded(self) -> None:
        self.assertEqual(market.HTTP_TIMEOUT_SECONDS, 15)
        self.assertGreaterEqual(market.LOOKBACK_DAYS, 7)
        self.assertLessEqual(market.LOOKBACK_DAYS, 10)
        self.assertLess(market.TOTAL_HTTP_BUDGET_SECONDS, 120)
        self.assertLessEqual(market.MAX_CONCURRENT_REQUESTS, 8)
        self.assertLessEqual(market.MAX_API_CALLS, 400)


def yahoo_timestamp(iso_date: str, hour: int = 4) -> int:
    year, month, day = (int(part) for part in iso_date.split("-"))
    return int(datetime(year, month, day, hour, tzinfo=market.KST).timestamp())


def yahoo_payload(symbol: str, closes_by_date: dict[str, float | None], regular_market_time: int) -> dict:
    dates = sorted(closes_by_date)
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": symbol, "regularMarketTime": regular_market_time},
                    "timestamp": [yahoo_timestamp(iso) for iso in dates],
                    "indicators": {"quote": [{"close": [closes_by_date[iso] for iso in dates]}]},
                }
            ],
        }
    }


def yahoo_route(payloads: dict[str, object]):
    """Build a fetch_yahoo_json side effect keyed by exact symbol (e.g. '000001.KS')."""

    def route(url: str, _timeout: float = market.YAHOO_HTTP_TIMEOUT_SECONDS) -> dict:
        path = urlparse(url).path
        symbol = path.rsplit("/", 1)[-1]
        value = payloads.get(symbol, {"chart": {"result": [], "error": {"code": "Not Found"}}})
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, dict):
            raise AssertionError(f"invalid routed yahoo payload for {symbol}")
        return value

    return route


class YahooQuoteTests(unittest.TestCase):
    def test_prefers_the_suffix_with_the_newer_regular_market_time(self) -> None:
        payloads = {
            "005930.KS": yahoo_payload("005930.KS", {"2026-08-05": 70_000.0, "2026-08-06": 70_500.0}, 1_000),
            "005930.KQ": yahoo_payload("005930.KQ", {"2026-08-05": 1_234.0, "2026-08-06": 1_300.0}, 2_000),
        }
        with patch.object(market, "fetch_yahoo_json", yahoo_route(payloads)):
            result = market.fetch_yahoo_quote("005930", market.time.monotonic() + 30)

        self.assertEqual(result, (1_300, "2026-08-06"))

    def test_mismatched_symbol_in_meta_is_rejected(self) -> None:
        payload = yahoo_payload("000002.KS", {"2026-08-06": 500.0}, 1_000)
        with patch.object(market, "fetch_yahoo_json", yahoo_route({"000001.KS": payload})):
            result = market.fetch_yahoo_quote("000001", market.time.monotonic() + 30)

        self.assertIsNone(result)

    def test_trailing_null_close_falls_back_to_latest_non_null(self) -> None:
        payload = yahoo_payload("000001.KS", {"2026-08-05": 1_000.0, "2026-08-06": None}, 1_000)
        with patch.object(market, "fetch_yahoo_json", yahoo_route({"000001.KS": payload})):
            result = market.fetch_yahoo_quote("000001", market.time.monotonic() + 30)

        self.assertEqual(result, (1_000, "2026-08-05"))

    def test_both_suffixes_failing_returns_none(self) -> None:
        with patch.object(market, "fetch_yahoo_json", yahoo_route({})):
            result = market.fetch_yahoo_quote("999999", market.time.monotonic() + 30)

        self.assertIsNone(result)

    def test_http_error_on_one_suffix_does_not_block_the_other(self) -> None:
        payloads = {
            "000001.KS": HTTPError("https://example.test", 404, "not found", None, None),
            "000001.KQ": yahoo_payload("000001.KQ", {"2026-08-06": 900.0}, 1_000),
        }
        with patch.object(market, "fetch_yahoo_json", yahoo_route(payloads)):
            result = market.fetch_yahoo_quote("000001", market.time.monotonic() + 30)

        self.assertEqual(result, (900, "2026-08-06"))


class ApplyPartialQuotesTests(unittest.TestCase):
    @staticmethod
    def fixture() -> dict:
        return {
            "priceBasisDate": "2026-08-01",
            "marketDataStatus": "verified_fixture",
            "companies": [
                {"code": code, "name": f"Company {code}", "currentPrice": index * 1_000,
                 "priceBasisDate": "2026-08-01", "priceSource": "verified fixture"}
                for index, code in enumerate(CODES, start=1)
            ],
        }

    def test_only_quoted_codes_are_updated_rest_are_untouched(self) -> None:
        data = self.fixture()
        quotes = {CODES[0]: (99_000, "2026-08-07")}

        changed = market.apply_partial_quotes(data, quotes)

        self.assertTrue(changed)
        updated, *rest = data["companies"]
        self.assertEqual((updated["currentPrice"], updated["priceBasisDate"], updated["priceSource"]),
                          (99_000, "2026-08-07", market.YAHOO_SOURCE_LABEL))
        for company in rest:
            self.assertEqual(company["currentPrice"], (CODES.index(company["code"]) + 1) * 1_000)
            self.assertEqual(company["priceBasisDate"], "2026-08-01")
            self.assertEqual(company["priceSource"], "verified fixture")
        self.assertEqual(data["priceBasisDate"], "2026-08-07")
        self.assertEqual(data["marketDataStatus"], "updated_from_yahoo_finance")

    def test_never_regresses_a_company_to_an_older_date(self) -> None:
        data = self.fixture()
        data["companies"][0]["priceBasisDate"] = "2026-08-10"
        data["companies"][0]["currentPrice"] = 55_000
        quotes = {CODES[0]: (1, "2026-08-05")}

        changed = market.apply_partial_quotes(data, quotes)

        self.assertFalse(changed)
        self.assertEqual(data["companies"][0]["currentPrice"], 55_000)
        self.assertEqual(data["companies"][0]["priceBasisDate"], "2026-08-10")

    def test_unknown_code_is_rejected(self) -> None:
        data = self.fixture()
        with self.assertRaises(market.MarketRefreshError):
            market.apply_partial_quotes(data, {"999999": (1, "2026-08-07")})

    def test_no_quotes_is_a_no_op(self) -> None:
        data = self.fixture()
        self.assertFalse(market.apply_partial_quotes(data, {}))


class YahooIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.data_path = Path(self.temporary_directory.name) / "companies.json"
        self.data_path.write_text(
            json.dumps(company_data(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def run_main(self, yahoo_payloads: dict, data_go_kr_mock=None) -> tuple[int, str, str, Mock]:
        stdout, stderr = io.StringIO(), io.StringIO()
        data_go_kr_mock = data_go_kr_mock or Mock(side_effect=AssertionError("data.go.kr should not be called"))
        with (
            patch.object(market, "DATA_PATH", self.data_path),
            patch.object(market, "fetch_yahoo_json", yahoo_route(yahoo_payloads)),
            patch.object(market, "fetch_json", data_go_kr_mock),
            patch.object(market, "current_kst_date", return_value=TODAY),
            patch.object(market.time, "sleep"),
            patch.dict(os.environ, {}, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = market.main()
        return result, stdout.getvalue(), stderr.getvalue(), data_go_kr_mock

    def test_full_yahoo_success_never_calls_the_fallback(self) -> None:
        payloads = {
            f"{code}.KS": yahoo_payload(f"{code}.KS", {"2026-08-07": int(code) * 10}, 1_000)
            for code in CODES
        }
        result, stdout, stderr, fallback_mock = self.run_main(payloads)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("20/20", stdout)
        fallback_mock.assert_not_called()
        updated = json.loads(self.data_path.read_text(encoding="utf-8"))
        self.assertEqual({c["priceSource"] for c in updated["companies"]}, {market.YAHOO_SOURCE_LABEL})

    def test_partial_yahoo_success_updates_only_the_successful_codes(self) -> None:
        payloads = {
            f"{CODES[0]}.KS": yahoo_payload(f"{CODES[0]}.KS", {"2026-08-07": 12_345.0}, 1_000),
        }
        result, stdout, stderr, fallback_mock = self.run_main(payloads)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("1/20", stdout)
        fallback_mock.assert_not_called()
        updated = json.loads(self.data_path.read_text(encoding="utf-8"))
        first, *rest = updated["companies"]
        self.assertEqual(first["currentPrice"], 12_345)
        self.assertEqual(first["priceSource"], market.YAHOO_SOURCE_LABEL)
        for company in rest:
            self.assertEqual(company["priceSource"], "verified fixture")

    def test_total_yahoo_failure_falls_back_to_data_go_kr(self) -> None:
        fallback_mock = Mock(return_value=response_payload([quote(CODES[0], LATEST_TRADING_DATE)]))
        # Route every data.go.kr call through the same complete-snapshot fixture used elsewhere.
        fallback_mock.side_effect = route_quotes(complete_responses(LATEST_TRADING_DATE))
        with patch.dict(
            os.environ,
            {"STOCK_API_SERVICE_KEY": "test key", "STOCK_API_ENDPOINT": "https://example.test/getStockPriceInfo"},
        ):
            result, stdout, stderr, _ = self.run_main({}, data_go_kr_mock=fallback_mock)

        self.assertEqual(result, 0)
        self.assertIn("falling back to data.go.kr", stdout)
        self.assertIn("20/20", stdout)
        self.assertEqual(fallback_mock.call_count, 20)
        updated = json.loads(self.data_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["marketDataStatus"], "updated_from_data_go_kr")


if __name__ == "__main__":
    unittest.main()
