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
SPEC = importlib.util.spec_from_file_location("fetch_market_under_test", ROOT / "scripts" / "fetch_market.py")
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
    page_size: int = 10_000,
    result_code: str = "00",
) -> dict:
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": "NORMAL SERVICE"},
            "body": {
                "numOfRows": page_size,
                "pageNo": page_number,
                "totalCount": len(items) if total_count is None else total_count,
                "items": {"item": items},
            },
        }
    }


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
        fetch_mock = Mock(return_value=side_effect) if isinstance(side_effect, dict) else Mock(side_effect=side_effect)
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

    def assert_failed_without_write(self, side_effect: object, value: dict | None = None) -> Mock:
        original = self.write_fixture(value or company_data())
        result, fetch_mock, _, stderr = self.run_main(side_effect)
        self.assertEqual(result, 1)
        self.assertIn("verified snapshot preserved", stderr)
        self.assertEqual(self.data_path.read_bytes(), original)
        self.assertFalse(self.data_path.with_suffix(".json.tmp").exists())
        return fetch_mock

    def test_one_bulk_page_updates_all_companies_atomically(self) -> None:
        self.write_fixture(company_data())
        items = [quote(code, "2026-08-08") for code in CODES]

        result, fetch_mock, stdout, stderr = self.run_main(response_payload(items))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("20/20", stdout)
        self.assertEqual(fetch_mock.call_count, 1)
        parameters = parse_qs(urlparse(fetch_mock.call_args.args[0]).query)
        self.assertEqual(parameters["pageNo"], ["1"])
        self.assertEqual(parameters["numOfRows"], ["10000"])
        self.assertEqual(parameters["beginBasDt"], ["20260802"])
        self.assertEqual(parameters["endBasDt"], ["20260810"])
        self.assertNotIn("likeSrtnCd", parameters)

        updated = self.read_fixture()
        self.assertEqual(updated["priceBasisDate"], "2026-08-08")
        self.assertEqual(updated["marketDataStatus"], "updated_from_data_go_kr")
        self.assertEqual({item["code"] for item in updated["companies"]}, set(CODES))
        self.assertEqual({item["priceBasisDate"] for item in updated["companies"]}, {"2026-08-08"})
        self.assertEqual({item["priceSource"] for item in updated["companies"]}, {market.SOURCE_LABEL})

    def test_two_bulk_pages_are_combined(self) -> None:
        self.write_fixture(company_data())
        first_page = [quote("999999", "2026-08-08", 1) for _ in range(10_000)]
        second_page = [quote(code, "2026-08-08") for code in CODES]

        def fetch_for_page(url: str) -> dict:
            page_number = int(parse_qs(urlparse(url).query)["pageNo"][0])
            if page_number == 1:
                return response_payload(first_page, total_count=10_020, page_number=1)
            return response_payload(second_page, total_count=10_020, page_number=2)

        result, fetch_mock, _, stderr = self.run_main(fetch_for_page)

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(self.read_fixture()["priceBasisDate"], "2026-08-08")

    def test_latest_incomplete_date_falls_back_to_latest_common_date(self) -> None:
        self.write_fixture(company_data())
        items = [quote(code, "2026-08-08") for code in CODES[:-1]]
        items.extend(quote(code, "2026-08-07") for code in CODES)

        result, _, _, stderr = self.run_main(response_payload(items))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        updated = self.read_fixture()
        self.assertEqual(updated["priceBasisDate"], "2026-08-07")
        self.assertEqual({item["priceBasisDate"] for item in updated["companies"]}, {"2026-08-07"})

    def test_rows_from_different_dates_are_never_mixed(self) -> None:
        items = [quote(code, "2026-08-08") for code in CODES[:10]]
        items.extend(quote(code, "2026-08-07") for code in CODES[10:])

        self.assert_failed_without_write(response_payload(items))

    def test_missing_company_preserves_original_file(self) -> None:
        items = [quote(code, "2026-08-08") for code in CODES[:-1]]

        self.assert_failed_without_write(response_payload(items))

    def test_invalid_price_preserves_original_file(self) -> None:
        items = [quote(code, "2026-08-08") for code in CODES]
        items[-1]["clpr"] = 0

        self.assert_failed_without_write(response_payload(items))

    def test_duplicate_company_row_preserves_original_file(self) -> None:
        items = [quote(code, "2026-08-08") for code in CODES]
        items.append(quote(CODES[0], "2026-08-08"))

        self.assert_failed_without_write(response_payload(items))

    def test_more_than_two_pages_is_rejected_before_second_request(self) -> None:
        payload = response_payload(
            [quote("999999", "2026-08-08")],
            total_count=20_001,
            page_size=10_000,
        )

        fetch_mock = self.assert_failed_without_write(payload)

        self.assertEqual(fetch_mock.call_count, 1)

    def test_truncated_page_preserves_original_file(self) -> None:
        items = [quote(code, "2026-08-08") for code in CODES[:-1]]
        payload = response_payload(items, total_count=20)

        self.assert_failed_without_write(payload)

    def test_provider_error_preserves_original_file(self) -> None:
        payload = response_payload([], result_code="22")

        self.assert_failed_without_write(payload)

    def test_http_attempt_budget_is_two(self) -> None:
        fetch_mock = self.assert_failed_without_write(URLError("temporary outage"))

        self.assertEqual(fetch_mock.call_count, 2)

    def test_older_common_date_cannot_regress_snapshot(self) -> None:
        items = [quote(code, "2026-08-07") for code in CODES]

        self.assert_failed_without_write(
            response_payload(items),
            value=company_data(basis_date="2026-08-08"),
        )

    def test_identical_official_snapshot_is_not_rewritten(self) -> None:
        value = company_data(basis_date="2026-08-08", official=True)
        for company, code in zip(value["companies"], CODES, strict=True):
            company["currentPrice"] = int(code) * 100 + 10_000
        original = self.write_fixture(value)
        items = [quote(code, "2026-08-08") for code in CODES]

        result, fetch_mock, stdout, stderr = self.run_main(response_payload(items))

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertIn("no file changes", stdout)
        self.assertEqual(self.data_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
