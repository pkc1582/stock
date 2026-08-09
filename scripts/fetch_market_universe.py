#!/usr/bin/env python3
"""Fetch a complete KOSPI/KOSDAQ market snapshot from data.go.kr.

Required secret:
  STOCK_API_SERVICE_KEY (DATA_GO_KR_API_KEY is also accepted)

Optional:
  MARKET_UNIVERSE_API_ENDPOINT or STOCK_API_ENDPOINT

The provider publishes one reference-date snapshot on the following business
day.  This script probes exact prior business dates newest first and downloads
all advertised pages using a deliberately small page size.  A new
``data/market-universe.json`` is installed atomically only when pagination is
complete, every row has the same requested date, short codes are unique, both
target markets are present, and every persisted numeric field is valid.
Credentials and raw responses are never persisted.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "market-universe.json"
DEFAULT_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/"
    "GetStockSecuritiesInfoService/getStockPriceInfo"
)
SOURCE_NAME = "금융위원회 주식시세정보"
SOURCE_PAGE = "https://www.data.go.kr/data/15094808/openapi.do"
KST = ZoneInfo("Asia/Seoul")

# Full-market responses are intentionally paged in small chunks so gateway
# truncation is detectable and a failed page never produces a partial file.
PAGE_SIZE = 200
MAX_PAGES = 40
LOOKBACK_DAYS = 10
HTTP_TIMEOUT_SECONDS = 20
MAX_HTTP_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 0.25
PAGE_REQUEST_DELAY_SECONDS = 0.20
SUCCESS_CODES = frozenset({"0", "00"})
TARGET_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
MIN_KOSPI_ROWS = 500
MIN_KOSDAQ_ROWS = 800
MIN_TARGET_ROWS = 1_500
MIN_PRIOR_SNAPSHOT_RATIO = 0.80
STOCK_CODE_PATTERN = re.compile(r"^(?:A)?(\d{6})$")
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


class MarketUniverseError(RuntimeError):
    """Raised when a response cannot safely replace the verified snapshot."""


def current_kst_date() -> date:
    return datetime.now(KST).date()


def generated_at_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def service_url(endpoint: str, service_key: str, parameters: dict[str, str]) -> str:
    encoded_key = service_key if "%" in service_key else quote(service_key, safe="")
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}serviceKey={encoded_key}&{urlencode(parameters)}"


def transport_error_label(error: BaseException) -> str:
    if isinstance(error, HTTPError):
        return f"HTTPError code={error.code}"
    return type(error).__name__


def fetch_json(url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "compound-asset-2045-market-universe/1.0",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    last_error: BaseException | None = None
    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw_payload = response.read()
            payload = json.loads(raw_payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise MarketUniverseError("provider response was not a JSON object")
            return payload
        except MarketUniverseError:
            raise
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            last_error = error
            if attempt < MAX_HTTP_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    raise MarketUniverseError(
        "provider transport failure after "
        f"{MAX_HTTP_ATTEMPTS} attempt(s): {transport_error_label(last_error or RuntimeError())}"
    ) from None


def parsed_int(value: Any, field: str, *, positive: bool = False) -> int:
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as error:
        raise MarketUniverseError(f"provider response has invalid {field}") from error
    if number < 0 or (positive and number == 0):
        raise MarketUniverseError(f"provider response has invalid {field}")
    return number


def response_page(
    payload: dict[str, Any], expected_page: int
) -> tuple[list[dict[str, Any]], int, int]:
    response = payload.get("response")
    if not isinstance(response, dict):
        raise MarketUniverseError("provider response is missing response")
    header = response.get("header")
    if not isinstance(header, dict):
        raise MarketUniverseError("provider response is missing header")
    result_code = str(header.get("resultCode", "")).strip()
    if result_code not in SUCCESS_CODES:
        raise MarketUniverseError(f"provider API error code={result_code or 'unknown'}")

    body = response.get("body")
    if not isinstance(body, dict):
        raise MarketUniverseError("provider response is missing body")
    total_count = parsed_int(body.get("totalCount"), "totalCount")
    page_size = parsed_int(body.get("numOfRows"), "numOfRows", positive=True)
    page_number = parsed_int(body.get("pageNo"), "pageNo", positive=True)
    if page_number != expected_page:
        raise MarketUniverseError(
            f"provider returned page {page_number} while page {expected_page} was requested"
        )

    items_container = body.get("items")
    if items_container in (None, "", {}):
        raw_items: Any = []
    elif isinstance(items_container, dict):
        raw_items = items_container.get("item", [])
    else:
        raise MarketUniverseError("provider response has invalid items")
    if isinstance(raw_items, dict):
        items = [raw_items]
    elif isinstance(raw_items, list) and all(isinstance(item, dict) for item in raw_items):
        items = raw_items
    else:
        raise MarketUniverseError("provider response has invalid item rows")

    if len(items) > page_size:
        raise MarketUniverseError("provider returned more rows than numOfRows")
    if total_count == 0 and items:
        raise MarketUniverseError("provider returned rows despite zero totalCount")
    if total_count > 0 and not items:
        raise MarketUniverseError("provider response was truncated before the advertised rows")
    return items, total_count, page_size


def normalize_basis_date(value: Any) -> str | None:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def normalize_code(value: Any) -> str | None:
    match = STOCK_CODE_PATTERN.fullmatch(str(value).strip().upper())
    return match.group(1) if match else None


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_market_row(row: dict[str, Any], expected_date: str) -> dict[str, Any] | None:
    basis_date = normalize_basis_date(row.get("basDt"))
    if basis_date != expected_date:
        raise MarketUniverseError("provider returned a row for a different basis date")
    market = normalized_text(row.get("mrktCtg")).upper()
    if market not in {"KOSPI", "KOSDAQ", "KONEX"}:
        raise MarketUniverseError(f"provider returned unsupported market {market or 'missing'}")
    if market not in TARGET_MARKETS:
        return None

    code = normalize_code(row.get("srtnCd"))
    if code is None:
        raise MarketUniverseError("provider returned an invalid short code")
    isin = normalized_text(row.get("isinCd")).upper()
    if not ISIN_PATTERN.fullmatch(isin):
        raise MarketUniverseError(f"provider returned an invalid ISIN for {code}")
    name = normalized_text(row.get("itmsNm"))
    if not name:
        raise MarketUniverseError(f"provider returned a missing name for {code}")

    return {
        "code": code,
        "isin": isin,
        "name": name,
        "market": market,
        "closePrice": parsed_int(row.get("clpr"), "closing price", positive=True),
        "tradingVolume": parsed_int(row.get("trqu"), "trading volume"),
        "tradingValue": parsed_int(row.get("trPrc"), "trading value"),
        "listedShares": parsed_int(row.get("lstgStCnt"), "listed shares", positive=True),
        "marketCap": parsed_int(row.get("mrktTotAmt"), "market capitalization", positive=True),
    }


def fetch_exact_date(
    endpoint: str, service_key: str, basis_date: str
) -> tuple[list[dict[str, Any]], int]:
    requested_date = basis_date.replace("-", "")

    def fetch_page(page_number: int) -> tuple[list[dict[str, Any]], int, int]:
        url = service_url(
            endpoint,
            service_key,
            {
                "resultType": "json",
                "pageNo": str(page_number),
                "numOfRows": str(PAGE_SIZE),
                "basDt": requested_date,
            },
        )
        return response_page(fetch_json(url), page_number)

    first_items, total_count, response_page_size = fetch_page(1)
    if total_count == 0:
        return [], 0
    if response_page_size != PAGE_SIZE:
        raise MarketUniverseError("provider changed numOfRows for the requested page")

    expected_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
    if expected_pages > MAX_PAGES:
        raise MarketUniverseError(
            f"provider advertised {expected_pages} pages, exceeding the safety limit"
        )
    rows = list(first_items)
    for page_number in range(2, expected_pages + 1):
        time.sleep(PAGE_REQUEST_DELAY_SECONDS)
        page_items, page_total, page_size = fetch_page(page_number)
        if page_total != total_count or page_size != PAGE_SIZE:
            raise MarketUniverseError("provider pagination metadata changed between pages")
        rows.extend(page_items)
    if len(rows) != total_count:
        raise MarketUniverseError(
            f"provider advertised {total_count} rows but returned {len(rows)}"
        )
    return rows, total_count


def build_snapshot(
    raw_rows: list[dict[str, Any]], total_count: int, basis_date: str
) -> dict[str, Any]:
    securities: list[dict[str, Any]] = []
    seen_all_codes: set[str] = set()
    for row in raw_rows:
        if normalize_basis_date(row.get("basDt")) != basis_date:
            raise MarketUniverseError("provider returned mixed basis dates")
        code = normalize_code(row.get("srtnCd"))
        if code is None:
            raise MarketUniverseError("provider returned an invalid short code")
        if code in seen_all_codes:
            raise MarketUniverseError(f"provider returned duplicate short code {code}")
        seen_all_codes.add(code)
        parsed = parse_market_row(row, basis_date)
        if parsed is not None:
            securities.append(parsed)

    if len(raw_rows) != total_count:
        raise MarketUniverseError("provider row count is incomplete")
    market_counts = {
        market: sum(security["market"] == market for security in securities)
        for market in sorted(TARGET_MARKETS)
    }
    if any(count == 0 for count in market_counts.values()):
        raise MarketUniverseError("complete snapshot must contain both KOSPI and KOSDAQ")
    if (
        market_counts["KOSPI"] < MIN_KOSPI_ROWS
        or market_counts["KOSDAQ"] < MIN_KOSDAQ_ROWS
        or len(securities) < MIN_TARGET_ROWS
    ):
        raise MarketUniverseError(
            "provider snapshot was too small to represent the full KOSPI/KOSDAQ market"
        )

    securities.sort(key=lambda security: (security["market"], security["code"]))
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at_kst(),
        "basisDate": basis_date,
        "status": "complete_provider_pagination",
        "source": {
            "name": SOURCE_NAME,
            "page": SOURCE_PAGE,
            "updatePolicy": "daily; reference-date data is normally published after 13:00 on T+1 business day",
        },
        "validation": {
            "exactBasisDate": True,
            "providerRowCountMatched": True,
            "duplicateShortCodes": 0,
            "allAdvertisedPagesFetched": True,
        },
        "counts": {
            "providerRows": total_count,
            "kospi": market_counts["KOSPI"],
            "kosdaq": market_counts["KOSDAQ"],
            "storedRows": len(securities),
        },
        "securities": securities,
    }


def latest_complete_snapshot(
    endpoint: str, service_key: str, today: date
) -> dict[str, Any]:
    for offset in range(1, LOOKBACK_DAYS + 1):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        candidate_iso = candidate.isoformat()
        rows, total_count = fetch_exact_date(endpoint, service_key, candidate_iso)
        if not rows:
            continue
        return build_snapshot(rows, total_count, candidate_iso)
    raise MarketUniverseError(
        f"provider returned no complete market snapshot in the prior {LOOKBACK_DAYS} days"
    )


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_against_prior_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    """Reject an implausible collapse relative to the last verified market file."""

    if not path.exists():
        return
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    prior_rows = prior.get("securities") if isinstance(prior, dict) else None
    current_rows = snapshot.get("securities")
    if not isinstance(prior_rows, list) or not isinstance(current_rows, list):
        return
    if len(prior_rows) >= MIN_TARGET_ROWS and len(current_rows) < len(prior_rows) * MIN_PRIOR_SNAPSHOT_RATIO:
        raise MarketUniverseError(
            "provider snapshot shrank by more than the allowed safety margin"
        )


def main() -> int:
    service_key = os.environ.get("STOCK_API_SERVICE_KEY") or os.environ.get(
        "DATA_GO_KR_API_KEY"
    )
    if not service_key:
        print("market universe refresh skipped: no API key; existing snapshot preserved")
        return 0
    endpoint = (
        os.environ.get("MARKET_UNIVERSE_API_ENDPOINT")
        or os.environ.get("STOCK_API_ENDPOINT")
        or DEFAULT_ENDPOINT
    ).strip() or DEFAULT_ENDPOINT
    try:
        snapshot = latest_complete_snapshot(
            endpoint, service_key, current_kst_date()
        )
        validate_against_prior_snapshot(OUTPUT_PATH, snapshot)
        atomic_write(OUTPUT_PATH, snapshot)
        print(
            "market universe refresh atomically stored "
            f"{snapshot['counts']['storedRows']} KOSPI/KOSDAQ rows "
            f"for {snapshot['basisDate']}"
        )
        return 0
    except (MarketUniverseError, OSError) as error:
        print(
            f"market universe refresh failed; existing snapshot preserved: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
