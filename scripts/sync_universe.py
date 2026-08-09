#!/usr/bin/env python3
"""Build a provisional KOSPI/KOSDAQ company universe from data.go.kr.

Required secret (the second name is a fallback):
  KRX_LISTED_API_KEY or STOCK_API_SERVICE_KEY

Optional:
  KRX_LISTED_API_ENDPOINT

The Financial Services Commission KRX-listed-instruments API does not expose
KRX security-group, stock-class, SPAC-section, or management-designation
fields.  Consequently this script applies deliberately conservative name
heuristics and records that limitation in the output.  Unmatched rows are
*common-stock candidates*, not officially verified common stocks.

The API is paged for one exact reference date at a time.  Nothing is written
unless every advertised page is present, all rows use that exact date, short
codes are unique, and both KOSPI and KOSDAQ are represented.  Credentials and
raw provider responses are never persisted.
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
OUTPUT_PATH = ROOT / "data" / "universe.json"
DEFAULT_ENDPOINT = (
    "https://apis.data.go.kr/1160100/service/"
    "GetKrxListedInfoService/getItemInfo"
)
SOURCE_NAME = "금융위원회 KRX상장종목정보"
SOURCE_PAGE = "https://www.data.go.kr/data/15094775/openapi.do"
KST = ZoneInfo("Asia/Seoul")

PAGE_SIZE = 200
MAX_PAGES = 50
LOOKBACK_DAYS = 10
HTTP_TIMEOUT_SECONDS = 30
MAX_HTTP_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0
PAGE_REQUEST_DELAY_SECONDS = 0.20
SUCCESS_CODES = frozenset({"0", "00"})
TARGET_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
MIN_KOSPI_ROWS = 500
MIN_KOSDAQ_ROWS = 800
MIN_TARGET_ROWS = 1_500
MIN_PRIOR_SNAPSHOT_RATIO = 0.80
STOCK_CODE_PATTERN = re.compile(r"^(?:A)?([0-9A-HJ-NP-TV-Z]{6})$", re.IGNORECASE)
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")

SPAC_PATTERN = re.compile(r"(?:스팩|SPAC|기업인수목적)", re.IGNORECASE)
REIT_PATTERN = re.compile(r"(?:리츠|REIT|부동산투자회사)", re.IGNORECASE)
PREFERRED_PATTERN = re.compile(
    r"(?:우선주|종류주|(?:\d+)?우(?:B|C)?(?:\(전환\)|전환)?)$",
    re.IGNORECASE,
)


class UniverseSyncError(RuntimeError):
    """Raised when an unverified response must not replace the snapshot."""


def current_kst_date() -> date:
    return datetime.now(KST).date()


def generated_at_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def service_url(endpoint: str, service_key: str, parameters: dict[str, str]) -> str:
    """Create a URL without double-encoding data.go.kr's encoded keys."""

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
            "User-Agent": "compound-asset-2045-universe/1.0",
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
                raise UniverseSyncError("provider response was not a JSON object")
            return payload
        except UniverseSyncError:
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
                time.sleep(RETRY_DELAY_SECONDS * attempt)
    raise UniverseSyncError(
        "provider transport failure after "
        f"{MAX_HTTP_ATTEMPTS} attempt(s): {transport_error_label(last_error or RuntimeError())}"
    ) from None


def nonnegative_int(value: Any, field: str, *, positive: bool = False) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise UniverseSyncError(f"provider response has invalid {field}") from error
    if parsed < 0 or (positive and parsed == 0):
        raise UniverseSyncError(f"provider response has invalid {field}")
    return parsed


def response_page(
    payload: dict[str, Any], expected_page: int
) -> tuple[list[dict[str, Any]], int, int]:
    response = payload.get("response")
    if not isinstance(response, dict):
        raise UniverseSyncError("provider response is missing response")
    header = response.get("header")
    if not isinstance(header, dict):
        raise UniverseSyncError("provider response is missing header")
    result_code = str(header.get("resultCode", "")).strip()
    if result_code not in SUCCESS_CODES:
        # Upstream messages can echo request details, so never include them.
        raise UniverseSyncError(f"provider API error code={result_code or 'unknown'}")

    body = response.get("body")
    if not isinstance(body, dict):
        raise UniverseSyncError("provider response is missing body")
    total_count = nonnegative_int(body.get("totalCount"), "totalCount")
    page_size = nonnegative_int(body.get("numOfRows"), "numOfRows", positive=True)
    page_number = nonnegative_int(body.get("pageNo"), "pageNo", positive=True)
    if page_number != expected_page:
        raise UniverseSyncError(
            f"provider returned page {page_number} while page {expected_page} was requested"
        )

    items_container = body.get("items")
    if items_container in (None, "", {}):
        raw_items: Any = []
    elif isinstance(items_container, dict):
        raw_items = items_container.get("item", [])
    else:
        raise UniverseSyncError("provider response has invalid items")
    if isinstance(raw_items, dict):
        items = [raw_items]
    elif isinstance(raw_items, list) and all(isinstance(item, dict) for item in raw_items):
        items = raw_items
    else:
        raise UniverseSyncError("provider response has invalid item rows")

    if len(items) > page_size:
        raise UniverseSyncError("provider returned more rows than numOfRows")
    if total_count == 0 and items:
        raise UniverseSyncError("provider returned rows despite zero totalCount")
    if total_count > 0 and not items:
        raise UniverseSyncError("provider response was truncated before the advertised rows")
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
    return match.group(1).upper() if match else None


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def compact_text(value: Any) -> str:
    return re.sub(r"[\s·ㆍ._-]+", "", normalized_text(value))


def classify_name(instrument_name: str, corporation_name: str) -> tuple[str, str | None]:
    """Return a conservative name-only class and an optional exclusion code."""

    combined = f"{compact_text(instrument_name)} {compact_text(corporation_name)}"
    if SPAC_PATTERN.search(combined):
        return "name_likely_spac", "name_indicates_spac"
    if REIT_PATTERN.search(combined):
        return "name_likely_reit", "name_indicates_reit"
    if PREFERRED_PATTERN.search(compact_text(instrument_name)):
        return "name_likely_preferred", "name_indicates_preferred"
    return "common_candidate", None


def parse_listed_row(row: dict[str, Any], expected_date: str) -> dict[str, Any] | None:
    basis_date = normalize_basis_date(row.get("basDt"))
    if basis_date != expected_date:
        raise UniverseSyncError("provider returned a row for a different basis date")

    market = normalized_text(row.get("mrktCtg")).upper()
    if market not in {"KOSPI", "KOSDAQ", "KONEX"}:
        raise UniverseSyncError(f"provider returned unsupported market {market or 'missing'}")
    if market not in TARGET_MARKETS:
        return None

    code = normalize_code(row.get("srtnCd"))
    if code is None:
        raise UniverseSyncError("provider returned an invalid short code")
    isin = normalized_text(row.get("isinCd")).upper()
    if not ISIN_PATTERN.fullmatch(isin):
        raise UniverseSyncError(f"provider returned an invalid ISIN for {code}")
    name = normalized_text(row.get("itmsNm"))
    corporation_name = normalized_text(row.get("corpNm"))
    if not name or not corporation_name:
        raise UniverseSyncError(f"provider returned a missing name for {code}")

    classification, exclusion_reason = classify_name(name, corporation_name)
    corporate_registration_number = re.sub(r"\D", "", str(row.get("crno", "")))
    if corporate_registration_number and len(corporate_registration_number) != 13:
        raise UniverseSyncError(
            f"provider returned an invalid corporate registration number for {code}"
        )

    return {
        "code": code,
        "isin": isin,
        "name": name,
        "corporationName": corporation_name,
        "corporateRegistrationNumber": corporate_registration_number or None,
        "market": market,
        "classification": classification,
        "classificationBasis": "conservative_name_heuristic",
        "classificationVerified": False,
        "managementStatus": "unverified",
        "eligibleForPreliminaryScreen": exclusion_reason is None,
        "exclusionReason": exclusion_reason,
    }


def fetch_exact_date(
    endpoint: str, service_key: str, basis_date: str
) -> tuple[list[dict[str, Any]], int]:
    """Fetch and validate every provider row for one exact YYYY-MM-DD date."""

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
        raise UniverseSyncError("provider changed numOfRows for the requested page")

    expected_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
    if expected_pages > MAX_PAGES:
        raise UniverseSyncError(
            f"provider advertised {expected_pages} pages, exceeding the safety limit"
        )
    rows = list(first_items)
    for page_number in range(2, expected_pages + 1):
        time.sleep(PAGE_REQUEST_DELAY_SECONDS)
        page_items, page_total, page_size = fetch_page(page_number)
        if page_total != total_count or page_size != PAGE_SIZE:
            raise UniverseSyncError("provider pagination metadata changed between pages")
        rows.extend(page_items)
    if len(rows) != total_count:
        raise UniverseSyncError(
            f"provider advertised {total_count} rows but returned {len(rows)}"
        )
    return rows, total_count


def build_snapshot(
    raw_rows: list[dict[str, Any]], total_count: int, basis_date: str
) -> dict[str, Any]:
    companies: list[dict[str, Any]] = []
    seen_all_codes: set[str] = set()
    for row in raw_rows:
        row_date = normalize_basis_date(row.get("basDt"))
        if row_date != basis_date:
            raise UniverseSyncError("provider returned mixed basis dates")
        code = normalize_code(row.get("srtnCd"))
        if code is None:
            raw_code = str(row.get("srtnCd", ""))[:20]
            raise UniverseSyncError(
                "provider returned an invalid short code "
                f"(type={type(row.get('srtnCd')).__name__}, value={raw_code!r})"
            )
        if code in seen_all_codes:
            raise UniverseSyncError(f"provider returned duplicate short code {code}")
        seen_all_codes.add(code)
        parsed = parse_listed_row(row, basis_date)
        if parsed is not None:
            companies.append(parsed)

    if len(raw_rows) != total_count:
        raise UniverseSyncError("provider row count is incomplete")
    market_counts = {
        market: sum(company["market"] == market for company in companies)
        for market in sorted(TARGET_MARKETS)
    }
    if any(count == 0 for count in market_counts.values()):
        raise UniverseSyncError("complete snapshot must contain both KOSPI and KOSDAQ")
    if (
        market_counts["KOSPI"] < MIN_KOSPI_ROWS
        or market_counts["KOSDAQ"] < MIN_KOSDAQ_ROWS
        or len(companies) < MIN_TARGET_ROWS
    ):
        raise UniverseSyncError(
            "provider snapshot was too small to represent the full KOSPI/KOSDAQ market"
        )

    companies.sort(key=lambda company: (company["market"], company["code"]))
    classifications = (
        "common_candidate",
        "name_likely_preferred",
        "name_likely_spac",
        "name_likely_reit",
    )
    class_counts = {
        classification: sum(
            company["classification"] == classification for company in companies
        )
        for classification in classifications
    }
    eligible_count = sum(
        bool(company["eligibleForPreliminaryScreen"]) for company in companies
    )
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at_kst(),
        "basisDate": basis_date,
        "status": "provisional_name_classification",
        "managed_item_checked": False,
        "source": {
            "name": SOURCE_NAME,
            "page": SOURCE_PAGE,
            "updatePolicy": "daily; reference-date data is normally published after 13:00 on T+1 business day",
        },
        "methodology": {
            "markets": ["KOSPI", "KOSDAQ"],
            "classification": "conservative_name_heuristic",
            "classificationVerified": False,
            "nameHeuristicExclusions": True,
            "preliminaryEligibility": "common name candidate only",
        },
        "limitations": [
            "이 API에는 공식 보통주·우선주 구분 필드가 없어, 제외 표식이 없는 종목은 잠정 보통주 후보로 분류했습니다.",
            "이 API에는 현재 관리종목 지정 상태가 없어 관리종목 제외가 아직 적용되지 않았습니다.",
            "이름 규칙은 명칭이 특이한 우선주·SPAC·REIT·투자회사·인프라펀드를 놓칠 수 있습니다.",
            "KRX 공식 종목분류와 지정 상태를 교차검증하기 전에는 1차 정량 선별에만 사용해야 합니다.",
        ],
        "counts": {
            "providerRows": total_count,
            "kospi": market_counts["KOSPI"],
            "kosdaq": market_counts["KOSDAQ"],
            "classified": class_counts,
            "preliminaryEligible": eligible_count,
            "knownExcluded": len(companies) - eligible_count,
        },
        "companies": companies,
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
    raise UniverseSyncError(
        f"provider returned no complete listed-instrument snapshot in the prior {LOOKBACK_DAYS} days"
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
    """Reject an implausible collapse relative to the last verified universe."""

    if not path.exists():
        return
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    prior_rows = prior.get("companies") if isinstance(prior, dict) else None
    current_rows = snapshot.get("companies")
    if not isinstance(prior_rows, list) or not isinstance(current_rows, list):
        return
    if len(prior_rows) >= MIN_TARGET_ROWS and len(current_rows) < len(prior_rows) * MIN_PRIOR_SNAPSHOT_RATIO:
        raise UniverseSyncError(
            "provider snapshot shrank by more than the allowed safety margin"
        )


def main() -> int:
    service_key = os.environ.get("KRX_LISTED_API_KEY") or os.environ.get(
        "STOCK_API_SERVICE_KEY"
    )
    if not service_key:
        print("universe sync skipped: no API key; existing snapshot preserved")
        return 0
    endpoint = (
        os.environ.get("KRX_LISTED_API_ENDPOINT", DEFAULT_ENDPOINT).strip()
        or DEFAULT_ENDPOINT
    )
    try:
        snapshot = latest_complete_snapshot(
            endpoint, service_key, current_kst_date()
        )
        validate_against_prior_snapshot(OUTPUT_PATH, snapshot)
        atomic_write(OUTPUT_PATH, snapshot)
        counts = snapshot["counts"]
        print(
            "universe sync atomically stored "
            f"{counts['kospi'] + counts['kosdaq']} KOSPI/KOSDAQ instruments "
            f"for {snapshot['basisDate']}"
        )
        return 0
    except (UniverseSyncError, OSError) as error:
        print(f"universe sync failed; existing snapshot preserved: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
