#!/usr/bin/env python3
"""Refresh Korean closing prices, preferring the free Yahoo Finance chart API.

Primary source: Yahoo Finance's public chart endpoint
(https://query1.finance.yahoo.com/v8/finance/chart/<code>.KS or .KQ), queried
with no API key. Each code is tried against both the .KS (KOSPI) and .KQ
(KOSDAQ) suffixes; whichever response has the newer ``meta.regularMarketTime``
is used (querying a KOSDAQ code with .KS, or vice versa, silently returns a
different, stale instrument rather than failing, so both must be checked).
Codes that fail keep their previously verified price — a full 20/20 snapshot
is no longer required to write a partial update.

Fallback source (only when Yahoo returns zero usable quotes): the data.go.kr
stock-price API, unchanged from before. Required secret (either name is
accepted): STOCK_API_SERVICE_KEY or DATA_GO_KR_API_KEY. Optional:
STOCK_API_ENDPOINT. That provider publishes reference-date data on the
following business day, so we try each prior KST business date, newest first,
and a snapshot is written only when every configured stock has one valid
quote on that same date (the original all-or-nothing rule, kept only for this
fallback path).

Raw responses and credentials are never persisted.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "companies.json"
DEFAULT_ENDPOINT = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
SOURCE_LABEL = "공공데이터포털 금융위원회 주식시세정보"

LOOKBACK_DAYS = 8
CODE_QUERY_PAGE_SIZE = 10
MAX_CONCURRENT_REQUESTS = 8
MAX_HTTP_ATTEMPTS_PER_REQUEST = 2
MAX_API_CALLS = LOOKBACK_DAYS * 20 * MAX_HTTP_ATTEMPTS_PER_REQUEST
HTTP_TIMEOUT_SECONDS = 15
TOTAL_HTTP_BUDGET_SECONDS = 105
RETRY_DELAY_SECONDS = 0.25
SUCCESS_CODES = {"0", "00"}
STOCK_CODE_PATTERN = re.compile(r"^(?:A)?(\d{6})$")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SUFFIXES = (".KS", ".KQ")
YAHOO_RANGE = "10d"
YAHOO_INTERVAL = "1d"
YAHOO_USER_AGENT = "Mozilla/5.0"
YAHOO_SOURCE_LABEL = "Yahoo Finance 차트 API (KRX 정규장 종가)"
YAHOO_HTTP_TIMEOUT_SECONDS = 15
YAHOO_MAX_HTTP_ATTEMPTS_PER_REQUEST = 2
YAHOO_RETRY_DELAY_SECONDS = 0.25
YAHOO_MAX_CONCURRENT_REQUESTS = 8
YAHOO_TOTAL_HTTP_BUDGET_SECONDS = 60
KST = timezone(timedelta(hours=9))


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


def fetch_yahoo_json(url: str, timeout: float = YAHOO_HTTP_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": YAHOO_USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise MarketRefreshError("yahoo response was not an object")
    return decoded


def fetch_yahoo_symbol(symbol: str, deadline: float) -> dict[str, Any] | None:
    """Fetch one Yahoo chart symbol. Any failure (network, shape, missing
    result) is swallowed and reported as None so one bad symbol never aborts
    the run; partial per-code failure is expected and tolerated."""

    url = (
        f"{YAHOO_CHART_URL.format(symbol=quote(symbol, safe=''))}"
        f"?range={YAHOO_RANGE}&interval={YAHOO_INTERVAL}"
    )
    attempts = 0
    while attempts < YAHOO_MAX_HTTP_ATTEMPTS_PER_REQUEST:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        attempts += 1
        try:
            payload = fetch_yahoo_json(url, min(YAHOO_HTTP_TIMEOUT_SECONDS, remaining))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, MarketRefreshError):
            if attempts < YAHOO_MAX_HTTP_ATTEMPTS_PER_REQUEST:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                time.sleep(min(YAHOO_RETRY_DELAY_SECONDS, remaining))
                continue
            return None
        chart = payload.get("chart")
        if not isinstance(chart, dict) or chart.get("error"):
            return None
        results = chart.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return None
        return results[0]
    return None


def extract_latest_close(result: dict[str, Any]) -> tuple[int, str] | None:
    """Return (price, KST-date) for the most recent trading day with a
    non-null close, newest first."""

    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    quote_list = indicators.get("quote") if isinstance(indicators, dict) else None
    if not isinstance(timestamps, list) or not timestamps or not isinstance(quote_list, list) or not quote_list:
        return None
    first_quote = quote_list[0]
    closes = first_quote.get("close") if isinstance(first_quote, dict) else None
    if not isinstance(closes, list) or len(closes) != len(timestamps):
        return None
    for timestamp, close in zip(reversed(timestamps), reversed(closes)):
        if close is None or not isinstance(timestamp, (int, float)):
            continue
        price = normalized_price(round(close))
        if price is None:
            continue
        kst_date = datetime.fromtimestamp(timestamp, tz=KST).date().isoformat()
        return price, kst_date
    return None


def fetch_yahoo_quote(code: str, deadline: float) -> tuple[int, str] | None:
    """Try both the .KS and .KQ suffixes and keep whichever response is
    actually the freshest (meta.regularMarketTime), since querying the wrong
    market for a code returns a different, stale instrument instead of an
    error."""

    candidates: list[tuple[float, int, str]] = []
    for suffix in YAHOO_SUFFIXES:
        symbol = f"{code}{suffix}"
        result = fetch_yahoo_symbol(symbol, deadline)
        if result is None:
            continue
        meta = result.get("meta")
        if not isinstance(meta, dict):
            continue
        regular_market_time = meta.get("regularMarketTime")
        if not isinstance(regular_market_time, (int, float)) or isinstance(regular_market_time, bool):
            continue
        if str(meta.get("symbol", "")).upper() != symbol:
            continue
        parsed = extract_latest_close(result)
        if parsed is None:
            continue
        price, kst_date = parsed
        candidates.append((float(regular_market_time), price, kst_date))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, price, kst_date = candidates[-1]
    return price, kst_date


def fetch_yahoo_snapshot(stock_codes: list[str], deadline: float) -> dict[str, tuple[int, str]]:
    quotes: dict[str, tuple[int, str]] = {}
    worker_count = min(YAHOO_MAX_CONCURRENT_REQUESTS, len(stock_codes))
    if worker_count <= 0:
        return quotes

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="yahoo-quote") as executor:
        futures = {
            executor.submit(fetch_yahoo_quote, stock_code, deadline): stock_code
            for stock_code in stock_codes
        }
        for future in as_completed(futures):
            stock_code = futures[future]
            result = future.result()
            if result is not None:
                quotes[stock_code] = result
    return quotes


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


class ApiCallBudget:
    """Thread-safe upper bound for provider calls in one refresh."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = Lock()

    def claim(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise MarketRefreshError("provider API call budget exhausted")
            self.used += 1


def request_with_budget(
    url: str, deadline: float, call_budget: ApiCallBudget
) -> dict[str, Any]:
    last_error: Exception | None = None
    attempts = 0
    while attempts < MAX_HTTP_ATTEMPTS_PER_REQUEST:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MarketRefreshError("provider request time budget exhausted") from last_error
        attempts += 1
        call_budget.claim()
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


def fetch_code_quote(
    endpoint: str,
    key: str,
    basis_date: str,
    stock_code: str,
    deadline: float,
    call_budget: ApiCallBudget,
) -> int | None:
    parameters = {
        "resultType": "json",
        "pageNo": "1",
        "numOfRows": str(CODE_QUERY_PAGE_SIZE),
        "basDt": basis_date,
        "likeSrtnCd": stock_code,
    }
    url = service_url(endpoint, key, parameters)
    payload = request_with_budget(url, deadline, call_budget)
    items, total_count, _ = response_page(payload, 1)

    if total_count == 0:
        return None
    if total_count != 1 or len(items) != 1:
        raise MarketRefreshError(
            f"provider returned {total_count} rows for exact stock query {stock_code}"
        )

    item = items[0]
    returned_date = normalized_date(item.get("basDt"))
    if returned_date != normalized_date(basis_date):
        raise MarketRefreshError(
            f"provider returned a mismatched basis date for {stock_code}"
        )
    returned_code = normalized_code(item.get("srtnCd"))
    if returned_code != stock_code:
        raise MarketRefreshError(
            f"provider returned stock {returned_code or 'invalid'} for requested {stock_code}"
        )
    price = normalized_price(item.get("clpr"))
    if price is None:
        raise MarketRefreshError(
            f"provider returned an invalid closing price for {stock_code} on {returned_date}"
        )
    return price


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


def fetch_snapshot_quotes(
    endpoint: str,
    key: str,
    basis_date: str,
    stock_codes: list[str],
    deadline: float,
    call_budget: ApiCallBudget,
) -> dict[str, int]:
    quotes: dict[str, int] = {}
    futures: dict[Future[int | None], str] = {}
    worker_count = min(MAX_CONCURRENT_REQUESTS, len(stock_codes))

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="market-quote",
    ) as executor:
        for stock_code in stock_codes:
            future = executor.submit(
                fetch_code_quote,
                endpoint,
                key,
                basis_date,
                stock_code,
                deadline,
                call_budget,
            )
            futures[future] = stock_code

        try:
            for future in as_completed(futures):
                stock_code = futures[future]
                price = future.result()
                if price is not None:
                    quotes[stock_code] = price
        except BaseException:
            for pending in futures:
                pending.cancel()
            raise

    if not set(quotes).issubset(stock_codes):
        raise MarketRefreshError("provider response produced an unexpected quote set")
    return quotes


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


def apply_partial_quotes(data: dict[str, Any], quotes: dict[str, tuple[int, str]]) -> bool:
    """Update only the companies present in ``quotes``; companies with no
    fresh quote this run keep their previously verified price untouched.
    Never regresses an individual company to an older basis date."""

    companies = data["companies"]
    target_codes = configured_codes(companies)
    unknown = set(quotes) - target_codes
    if unknown:
        raise MarketRefreshError(f"quotes include codes outside the configured universe: {sorted(unknown)}")

    changed = False
    applied_any = False
    newest_basis_date = parse_existing_basis_date(data.get("priceBasisDate"))
    for company in companies:
        code = str(company["code"])
        result = quotes.get(code)
        if result is None:
            continue
        price, basis_date = result
        existing_company_date = normalized_date(company.get("priceBasisDate")) or ""
        if basis_date < existing_company_date:
            continue
        applied_any = True
        if (
            company.get("currentPrice") != price
            or company.get("priceBasisDate") != basis_date
            or company.get("priceSource") != YAHOO_SOURCE_LABEL
        ):
            changed = True
        company["currentPrice"] = price
        company["priceBasisDate"] = basis_date
        company["priceSource"] = YAHOO_SOURCE_LABEL
        if basis_date > newest_basis_date:
            newest_basis_date = basis_date

    if not applied_any:
        return False

    if newest_basis_date != data.get("priceBasisDate"):
        data["priceBasisDate"] = newest_basis_date
        changed = True
    if data.get("marketDataStatus") != "updated_from_yahoo_finance":
        data["marketDataStatus"] = "updated_from_yahoo_finance"
        changed = True
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
    stock_codes = [str(company["code"]) for company in companies]
    if set(stock_codes) != target_codes:
        raise MarketRefreshError("configured stock order does not match the validated universe")
    existing_basis_date = parse_existing_basis_date(data.get("priceBasisDate"))
    deadline = time.monotonic() + TOTAL_HTTP_BUDGET_SECONDS
    call_budget = ApiCallBudget(MAX_API_CALLS)
    incomplete_dates: list[str] = []

    # The official feed is published on the following business day, so today's
    # basis date is intentionally excluded. Weekend dates can never contain a
    # KRX closing snapshot and are skipped without consuming API traffic.
    for offset in range(1, LOOKBACK_DAYS + 1):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        candidate_iso = candidate.isoformat()
        if candidate_iso < existing_basis_date:
            break

        quotes = fetch_snapshot_quotes(
            endpoint,
            service_key,
            candidate.strftime("%Y%m%d"),
            stock_codes,
            deadline,
            call_budget,
        )
        if not quotes:
            continue
        if set(quotes) != target_codes:
            incomplete_dates.append(candidate_iso)
            continue
        return apply_quotes(data, candidate_iso, quotes), candidate_iso

    detail = f"; incomplete dates: {', '.join(incomplete_dates)}" if incomplete_dates else ""
    raise MarketRefreshError(
        f"provider response has no complete 20-company snapshot in the prior "
        f"{LOOKBACK_DAYS} calendar dates{detail}"
    )


def main() -> int:
    try:
        data = load_data()
    except (OSError, json.JSONDecodeError, MarketRefreshError) as error:
        print(f"market refresh failed; verified snapshot preserved: {error}", file=sys.stderr)
        return 1

    try:
        companies = data.get("companies")
        if not isinstance(companies, list):
            raise MarketRefreshError("data/companies.json has an invalid companies list")
        target_codes = configured_codes(companies)
        stock_codes = [str(company["code"]) for company in companies]
        if set(stock_codes) != target_codes:
            raise MarketRefreshError("configured stock order does not match the validated universe")

        yahoo_deadline = time.monotonic() + YAHOO_TOTAL_HTTP_BUDGET_SECONDS
        yahoo_quotes = fetch_yahoo_snapshot(stock_codes, yahoo_deadline)

        if yahoo_quotes:
            changed = apply_partial_quotes(data, yahoo_quotes)
            if changed:
                atomic_write(data)
            success_count = len(yahoo_quotes)
            suffix = "" if changed else "; no file changes"
            print(f"market refresh via Yahoo Finance updated {success_count}/{len(stock_codes)} companies{suffix}")
            return 0

        print("Yahoo Finance returned no usable quotes; falling back to data.go.kr")
    except MarketRefreshError as error:
        print(f"Yahoo Finance refresh failed; falling back to data.go.kr: {error}")

    service_key = os.environ.get("STOCK_API_SERVICE_KEY") or os.environ.get("DATA_GO_KR_API_KEY")
    if not service_key:
        print("market refresh skipped: no fallback API key; verified snapshot preserved")
        return 0

    endpoint = os.environ.get("STOCK_API_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    try:
        changed, basis_date = refresh_market(data, endpoint, service_key, current_kst_date())
        if changed:
            atomic_write(data)
            print(f"market refresh (data.go.kr fallback) atomically updated 20/20 companies for {basis_date}")
        else:
            print(f"market refresh (data.go.kr fallback) already current for {basis_date}; no file changes")
        return 0
    except (MarketRefreshError, OSError, json.JSONDecodeError) as error:
        print(f"market refresh failed; verified snapshot preserved: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
