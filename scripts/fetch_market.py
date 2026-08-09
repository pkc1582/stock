#!/usr/bin/env python3
"""Refresh Korean closing prices from the free data.go.kr stock-price API.

Required secret (either name is accepted):
  STOCK_API_SERVICE_KEY or DATA_GO_KR_API_KEY

Optional:
  STOCK_API_ENDPOINT

The provider publishes reference-date data on the following business day.  A
single rolling-window bulk response is therefore safer and much faster than 20
per-company requests.  A snapshot is written only when every configured stock
has one valid quote on the same latest reference date.  Raw responses and
credentials are never persisted.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "companies.json"
DEFAULT_ENDPOINT = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
SOURCE_LABEL = "공공데이터포털 금융위원회 주식시세정보"

LOOKBACK_DAYS = 7
PAGE_SIZE = 10_000
MAX_PAGES = 2
MAX_HTTP_ATTEMPTS = 2
HTTP_TIMEOUT_SECONDS = 25
RETRY_DELAY_SECONDS = 1
SUCCESS_CODES = {"0", "00"}
STOCK_CODE_PATTERN = re.compile(r"^(?:A)?(\d{6})$")


class MarketRefreshError(RuntimeError):
    """Raised when a refresh cannot safely replace the verified snapshot."""


def load_data() -> dict[str, Any]:
    with DATA_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or not isinstance(value.get("companies"), list):
        raise MarketRefreshError("data/companies.json has an invalid shape")
    return value


def atomic_write(value: dict[str, Any]) -> None:
    temporary = DATA_PATH.with_suffix(".json.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(DATA_PATH)
    finally:
        if temporary.exists():
            temporary.unlink()


def service_url(endpoint: str, service_key: str, parameters: dict[str, str]) -> str:
    # data.go.kr commonly distributes an already percent-encoded key. Preserve
    # percent escapes in that case; otherwise encode the raw key exactly once.
    encoded_key = service_key if "%" in service_key else quote(service_key, safe="")
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}serviceKey={encoded_key}&{urlencode(parameters)}"


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "compound-asset-2045/1.0",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = response.read()
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise MarketRefreshError("provider response was not an object")
    return decoded


def positive_int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise MarketRefreshError(f"provider response has an invalid {field}") from error
    if number < 0 or (number == 0 and not allow_zero):
        raise MarketRefreshError(f"provider response has an invalid {field}")
    return number


def response_page(payload: dict[str, Any], expected_page: int) -> tuple[list[dict[str, Any]], int, int]:
    response = payload.get("response")
    if not isinstance(response, dict):
        raise MarketRefreshError("provider response is missing response")

    header = response.get("header")
    if not isinstance(header, dict):
        raise MarketRefreshError("provider response is missing header")
    result_code = str(header.get("resultCode", "")).strip()
    if result_code not in SUCCESS_CODES:
        message = str(header.get("resultMsg", "unknown provider error")).strip()
        raise MarketRefreshError(f"provider error {result_code or 'unknown'}: {message}")

    body = response.get("body")
    if not isinstance(body, dict):
        raise MarketRefreshError("provider response is missing body")

    total_count = positive_int(body.get("totalCount"), "totalCount", allow_zero=True)
    page_size = positive_int(body.get("numOfRows"), "numOfRows")
    page_number = positive_int(body.get("pageNo"), "pageNo")
    if page_number != expected_page:
        raise MarketRefreshError(
            f"provider returned page {page_number} while page {expected_page} was requested"
        )

    items_container = body.get("items")
    if items_container in (None, "", {}):
        items: Any = []
    elif isinstance(items_container, dict):
        items = items_container.get("item", [])
    else:
        raise MarketRefreshError("provider response has an invalid items container")

    if isinstance(items, dict):
        parsed_items = [items]
    elif isinstance(items, list) and all(isinstance(item, dict) for item in items):
        parsed_items = items
    else:
        raise MarketRefreshError("provider response has invalid items")

    if total_count > 0 and not parsed_items:
        raise MarketRefreshError("provider response was truncated before any items were returned")
    return parsed_items, total_count, page_size


def request_with_budget(url: str, attempts: list[int]) -> dict[str, Any]:
    last_error: Exception | None = None
    while attempts[0] < MAX_HTTP_ATTEMPTS:
        attempts[0] += 1
        try:
            return fetch_json(url)
        except MarketRefreshError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            last_error = error
            if attempts[0] < MAX_HTTP_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    raise MarketRefreshError(
        f"provider request failed after {attempts[0]} HTTP attempt(s): {type(last_error).__name__}"
    ) from last_error


def fetch_bulk_rows(endpoint: str, key: str, begin: str, end: str) -> list[dict[str, Any]]:
    attempts = [0]

    def fetch_page(page_number: int) -> tuple[list[dict[str, Any]], int, int]:
        parameters = {
            "resultType": "json",
            "pageNo": str(page_number),
            "numOfRows": str(PAGE_SIZE),
            "beginBasDt": begin,
            # The provider documents endBasDt as exclusive.
            "endBasDt": end,
        }
        url = service_url(endpoint, key, parameters)
        payload = request_with_budget(url, attempts)
        return response_page(payload, page_number)

    first_items, total_count, returned_page_size = fetch_page(1)
    required_pages = max(1, math.ceil(total_count / returned_page_size))
    if required_pages > MAX_PAGES:
        raise MarketRefreshError(
            f"provider returned {total_count} rows requiring {required_pages} pages; "
            f"the safe page limit is {MAX_PAGES}"
        )

    rows = list(first_items)
    for page_number in range(2, required_pages + 1):
        page_items, page_total, page_size = fetch_page(page_number)
        if page_total != total_count or page_size != returned_page_size:
            raise MarketRefreshError("provider pagination metadata changed between pages")
        rows.extend(page_items)

    if len(rows) != total_count:
        raise MarketRefreshError(
            f"provider response was truncated: expected {total_count} rows, received {len(rows)}"
        )
    return rows


def normalized_code(value: Any) -> str | None:
    match = STOCK_CODE_PATTERN.fullmatch(str(value).strip().upper())
    return match.group(1) if match else None


def normalized_date(value: Any) -> str | None:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def normalized_price(value: Any) -> int | None:
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def configured_codes(companies: list[Any]) -> set[str]:
    if len(companies) != 20:
        raise MarketRefreshError(f"expected exactly 20 companies, found {len(companies)}")
    codes: set[str] = set()
    for company in companies:
        if not isinstance(company, dict):
            raise MarketRefreshError("each configured company must be an object")
        code = normalized_code(company.get("code"))
        if code is None or code != str(company.get("code", "")):
            raise MarketRefreshError(f"invalid configured stock code: {company.get('code')!r}")
        if code in codes:
            raise MarketRefreshError(f"duplicate configured stock code: {code}")
        codes.add(code)
    return codes


def latest_complete_quotes(
    rows: list[dict[str, Any]], target_codes: set[str]
) -> tuple[str, dict[str, int]]:
    by_date: dict[str, dict[str, int]] = defaultdict(dict)
    observed_codes: set[str] = set()

    for item in rows:
        code = normalized_code(item.get("srtnCd"))
        if code not in target_codes:
            continue
        observed_codes.add(code)
        basis_date = normalized_date(item.get("basDt"))
        price = normalized_price(item.get("clpr"))
        if basis_date is None:
            raise MarketRefreshError(f"provider returned an invalid basis date for {code}")
        if price is None:
            raise MarketRefreshError(f"provider returned an invalid closing price for {code} on {basis_date}")
        if code in by_date[basis_date]:
            raise MarketRefreshError(f"provider returned duplicate rows for {code} on {basis_date}")
        by_date[basis_date][code] = price

    entirely_missing = sorted(target_codes - observed_codes)
    if entirely_missing:
        raise MarketRefreshError(
            "provider response is missing configured stocks: " + ", ".join(entirely_missing)
        )

    for basis_date in sorted(by_date, reverse=True):
        quotes = by_date[basis_date]
        if set(quotes) == target_codes:
            return basis_date, quotes

    raise MarketRefreshError("provider response has no single basis date shared by all configured stocks")


def parse_existing_basis_date(value: Any) -> str:
    parsed = normalized_date(value)
    if parsed is None:
        raise MarketRefreshError("data/companies.json has an invalid priceBasisDate")
    return parsed


def apply_quotes(data: dict[str, Any], basis_date: str, quotes: dict[str, int]) -> bool:
    companies = data["companies"]
    target_codes = configured_codes(companies)
    if set(quotes) != target_codes:
        raise MarketRefreshError("quote set does not match the configured company universe")

    existing_basis_date = parse_existing_basis_date(data.get("priceBasisDate"))
    if basis_date < existing_basis_date:
        raise MarketRefreshError(
            f"provider basis date {basis_date} is older than stored date {existing_basis_date}"
        )

    changed = data.get("marketDataStatus") != "updated_from_data_go_kr"
    if data.get("priceBasisDate") != basis_date:
        changed = True

    for company in companies:
        code = str(company["code"])
        price = quotes[code]
        if (
            company.get("currentPrice") != price
            or company.get("priceBasisDate") != basis_date
            or company.get("priceSource") != SOURCE_LABEL
        ):
            changed = True
        company["currentPrice"] = price
        company["priceBasisDate"] = basis_date
        company["priceSource"] = SOURCE_LABEL

    data["priceBasisDate"] = basis_date
    data["marketDataStatus"] = "updated_from_data_go_kr"
    return changed


def current_kst_date() -> date:
    return datetime.now(timezone(timedelta(hours=9))).date()


def refresh_market(
    data: dict[str, Any], endpoint: str, service_key: str, today: date
) -> tuple[bool, str]:
    companies = data.get("companies")
    if not isinstance(companies, list):
        raise MarketRefreshError("data/companies.json has an invalid companies list")
    target_codes = configured_codes(companies)
    begin = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    # endBasDt is exclusive, so tomorrow includes every available row through today.
    end = (today + timedelta(days=1)).strftime("%Y%m%d")
    rows = fetch_bulk_rows(endpoint, service_key, begin, end)
    basis_date, quotes = latest_complete_quotes(rows, target_codes)
    return apply_quotes(data, basis_date, quotes), basis_date


def main() -> int:
    service_key = os.environ.get("STOCK_API_SERVICE_KEY") or os.environ.get("DATA_GO_KR_API_KEY")
    if not service_key:
        print("market refresh skipped: no API key; verified snapshot preserved")
        return 0

    endpoint = os.environ.get("STOCK_API_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    try:
        data = load_data()
        changed, basis_date = refresh_market(data, endpoint, service_key, current_kst_date())
        if changed:
            atomic_write(data)
            print(f"market refresh atomically updated 20/20 companies for {basis_date}")
        else:
            print(f"market refresh already current for {basis_date}; no file changes")
        return 0
    except (MarketRefreshError, OSError, json.JSONDecodeError) as error:
        print(f"market refresh failed; verified snapshot preserved: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
