#!/usr/bin/env python3
"""Refresh Korean closing prices from the free data.go.kr stock-price API.

Required secret (either name is accepted):
  STOCK_API_SERVICE_KEY or DATA_GO_KR_API_KEY

Optional:
  STOCK_API_ENDPOINT

The provider publishes reference-date data on the following business day.  We
therefore probe one exact KST calendar date at a time, newest first, instead of
downloading a large rolling window.  A snapshot is written only when every
configured stock has one valid quote on that same date.  Raw responses and
credentials are never persisted.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "companies.json"
DEFAULT_ENDPOINT = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
SOURCE_LABEL = "공공데이터포털 금융위원회 주식시세정보"

LOOKBACK_DAYS = 8
PAGE_SIZE = 5_000
MAX_PAGES = 2
MAX_HTTP_ATTEMPTS_PER_REQUEST = 2
HTTP_TIMEOUT_SECONDS = 15
TOTAL_HTTP_BUDGET_SECONDS = 105
RETRY_DELAY_SECONDS = 0.25
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


def fetch_json(url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "compound-asset-2045/1.0",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=timeout) as response:
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
        # Avoid echoing an upstream message because gateways can include request
        # details in it. The result code is enough to diagnose the API status.
        raise MarketRefreshError(f"provider API error code={result_code or 'unknown'}")

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
    if total_count == 0 and parsed_items:
        raise MarketRefreshError("provider response has items despite a zero totalCount")
    if len(parsed_items) > total_count:
        raise MarketRefreshError("provider response contains more items than totalCount")
    if len(parsed_items) > page_size:
        raise MarketRefreshError("provider response contains more items than numOfRows")
    return parsed_items, total_count, page_size


def transport_error_label(error: BaseException) -> str:
    """Return a credential-safe transport error label."""

    if isinstance(error, HTTPError):
        return f"HTTPError code={error.code}"
    return type(error).__name__


def request_with_budget(url: str, deadline: float) -> dict[str, Any]:
    last_error: Exception | None = None
    attempts = 0
    while attempts < MAX_HTTP_ATTEMPTS_PER_REQUEST:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MarketRefreshError("provider request time budget exhausted") from last_error
        attempts += 1
        try:
            return fetch_json(url, min(HTTP_TIMEOUT_SECONDS, remaining))
        except MarketRefreshError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            last_error = error
            if attempts < MAX_HTTP_ATTEMPTS_PER_REQUEST:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(RETRY_DELAY_SECONDS, remaining))
    raise MarketRefreshError(
        f"provider transport failure after {attempts} attempt(s): "
        f"{transport_error_label(last_error or RuntimeError())}"
    ) from last_error


def fetch_exact_date_rows(
    endpoint: str, key: str, basis_date: str, deadline: float
) -> list[dict[str, Any]]:
    def fetch_page(page_number: int) -> tuple[list[dict[str, Any]], int, int]:
        parameters = {
            "resultType": "json",
            "pageNo": str(page_number),
            "numOfRows": str(PAGE_SIZE),
            "basDt": basis_date,
        }
        url = service_url(endpoint, key, parameters)
        payload = request_with_budget(url, deadline)
        return response_page(payload, page_number)

    first_items, total_count, returned_page_size = fetch_page(1)
    if total_count == 0:
        return []

    required_pages = (total_count + returned_page_size - 1) // returned_page_size
    if required_pages > MAX_PAGES:
        raise MarketRefreshError(
            f"provider returned {total_count} rows requiring {required_pages} pages; "
            f"the safe page limit is {MAX_PAGES}"
        )

    if required_pages > 1 and len(first_items) != returned_page_size:
        raise MarketRefreshError("provider response was truncated before the next page")

    rows = list(first_items)
    for page_number in range(2, required_pages + 1):
        page_items, page_total, page_size = fetch_page(page_number)
        if page_total != total_count or page_size != returned_page_size:
            raise MarketRefreshError("provider pagination metadata changed between pages")
        if page_number < required_pages and len(page_items) != returned_page_size:
            raise MarketRefreshError("provider response was truncated before the next page")
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


def complete_quotes_for_date(
    rows: list[dict[str, Any]], target_codes: set[str], expected_date: str
) -> dict[str, int] | None:
    quotes: dict[str, int] = {}
    for item in rows:
        basis_date = normalized_date(item.get("basDt"))
        if basis_date != expected_date:
            raise MarketRefreshError(
                f"provider ignored exact-date filter: requested {expected_date}, returned "
                f"{basis_date or 'invalid'}"
            )
        code = normalized_code(item.get("srtnCd"))
        if code not in target_codes:
            continue
        price = normalized_price(item.get("clpr"))
        if price is None:
            raise MarketRefreshError(
                f"provider returned an invalid closing price for {code} on {expected_date}"
            )
        if code in quotes:
            raise MarketRefreshError(
                f"provider returned duplicate rows for {code} on {expected_date}"
            )
        quotes[code] = price

    return quotes if set(quotes) == target_codes else None


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
    existing_basis_date = parse_existing_basis_date(data.get("priceBasisDate"))
    deadline = time.monotonic() + TOTAL_HTTP_BUDGET_SECONDS
    incomplete_dates: list[str] = []

    for offset in range(LOOKBACK_DAYS):
        candidate = today - timedelta(days=offset)
        candidate_iso = candidate.isoformat()
        if candidate_iso < existing_basis_date:
            break

        rows = fetch_exact_date_rows(
            endpoint,
            service_key,
            candidate.strftime("%Y%m%d"),
            deadline,
        )
        if not rows:
            continue
        quotes = complete_quotes_for_date(rows, target_codes, candidate_iso)
        if quotes is None:
            incomplete_dates.append(candidate_iso)
            continue
        return apply_quotes(data, candidate_iso, quotes), candidate_iso

    detail = f"; incomplete dates: {', '.join(incomplete_dates)}" if incomplete_dates else ""
    raise MarketRefreshError(
        f"provider response has no complete 20-company snapshot in the latest "
        f"{LOOKBACK_DAYS} calendar dates{detail}"
    )


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
