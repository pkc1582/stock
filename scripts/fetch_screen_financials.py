#!/usr/bin/env python3
"""Build an annual OpenDART financial snapshot for the screening universe.

The inexpensive multi-company endpoint is intentionally used for the first
pass.  It supplies balance-sheet and income-statement major accounts for up to
100 issuers per request.  Operating cash flow is represented in the schema but
usually remains pending because ``fnlttMultiAcnt`` does not promise cash-flow
statement rows.  A later, smaller-company pass can fill it from the full
statement endpoint without changing this file format.

Required environment variable: ``DART_API_KEY``.
No credential or raw provider response is persisted.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNIVERSE_PATHS = (
    ROOT / "data" / "universe.json",
    ROOT / "data" / "krx-universe.json",
    ROOT / "data" / "stock-universe.json",
)
OUTPUT_PATH = ROOT / "data" / "screen-financials.json"

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
MULTI_ACCOUNT_URL = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json"
SOURCE_GUIDE_URL = (
    "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019017"
)
KST = ZoneInfo("Asia/Seoul")
_IMPORT_DATE = datetime.now(KST).date()
REPORT_YEAR = _IMPORT_DATE.year - (2 if _IMPORT_DATE.month < 4 else 1)
REPORT_CODE = "11011"
MAX_COMPANIES_PER_REQUEST = 100
HTTP_TIMEOUT_SECONDS = 30
MAX_HTTP_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 0.4

NO_DATA_STATUSES = frozenset({"013"})
STOCK_CODE_PATTERN = re.compile(r"^(?:A)?(\d{6})$")
MIN_CORP_CODE_COVERAGE = 0.70
MIN_REPORT_COVERAGE = 0.70


class ScreenFinancialError(RuntimeError):
    """A safe refresh cannot be completed."""


class DartApiError(ScreenFinancialError):
    """OpenDART returned an error response."""

    def __init__(self, status: str, message: str = "") -> None:
        self.status = status
        self.api_message = message
        super().__init__(f"OpenDART status {status or 'missing'}")


class DartDataError(ScreenFinancialError):
    """A provider response is internally inconsistent."""


def latest_expected_annual_year(as_of: date | None = None) -> int:
    """Return the latest annual-report year expected to be broadly available.

    Korean annual reports are generally filed by the end of March.  Before
    April, using year-1 would systematically produce an incomplete universe,
    so automation stays on year-2 until April begins.
    """

    selected = as_of or datetime.now(KST).date()
    return selected.year - (2 if selected.month < 4 else 1)


@dataclass(frozen=True)
class UniverseSecurity:
    code: str
    name: str | None
    market: str | None


@dataclass(frozen=True)
class CorpEntry:
    corp_code: str
    corp_name: str
    stock_code: str
    modify_date: str | None


@dataclass(frozen=True)
class MetricSpec:
    ids: tuple[str, ...]
    names: tuple[str, ...]
    statements: tuple[str, ...]


METRIC_SPECS: dict[str, MetricSpec] = {
    "revenue": MetricSpec(
        ("ifrs-full_Revenue", "dart_Revenue"),
        (
            "매출액",
            "수익(매출액)",
            "영업수익",
            "영업수익(매출액)",
            "보험영업수익",
        ),
        ("IS", "CIS"),
    ),
    "operatingProfit": MetricSpec(
        ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"),
        ("영업이익", "영업이익(손실)", "영업손익"),
        ("IS", "CIS"),
    ),
    "netIncome": MetricSpec(
        (
            "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "ifrs-full_ProfitLoss",
        ),
        (
            "지배기업의소유주에게귀속되는당기순이익",
            "지배기업소유주지분당기순이익",
            "당기순이익",
            "당기순이익(손실)",
            "당기순손익",
        ),
        ("IS", "CIS"),
    ),
    "assets": MetricSpec(
        ("ifrs-full_Assets",),
        ("자산총계",),
        ("BS",),
    ),
    "liabilities": MetricSpec(
        ("ifrs-full_Liabilities",),
        ("부채총계",),
        ("BS",),
    ),
    "equity": MetricSpec(
        ("ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity"),
        ("지배기업의소유주에게귀속되는자본", "자본총계"),
        ("BS",),
    ),
    "operatingCashFlow": MetricSpec(
        (
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "dart_CashFlowsFromUsedInOperatingActivities",
        ),
        ("영업활동현금흐름", "영업활동으로인한현금흐름"),
        ("CF",),
    ),
}


def _alias(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def normalize_stock_code(value: Any) -> str | None:
    match = STOCK_CODE_PATTERN.fullmatch(str(value or "").strip().upper())
    return match.group(1) if match else None


def _record_array(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ScreenFinancialError("universe must be an object or array")
    for key in ("securities", "items", "companies", "universe", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    raise ScreenFinancialError(
        "universe must contain a securities, items, companies, universe, or data array"
    )


def parse_universe(payload: Any) -> list[UniverseSecurity]:
    securities: list[UniverseSecurity] = []
    seen: set[str] = set()
    for raw in _record_array(payload):
        if not isinstance(raw, dict):
            continue
        code = normalize_stock_code(_alias(raw, "code", "srtnCd", "stockCode", "stock_code"))
        if code is None:
            continue
        if code in seen:
            raise ScreenFinancialError(f"duplicate stock code in universe: {code}")
        seen.add(code)
        name_value = _alias(raw, "name", "itmsNm", "stockName", "corpName", "corp_name")
        market_value = _alias(raw, "market", "mrktCtg", "marketName", "corpCls", "corp_cls")
        securities.append(
            UniverseSecurity(
                code=code,
                name=str(name_value).strip() if name_value not in (None, "") else None,
                market=str(market_value).strip() if market_value not in (None, "") else None,
            )
        )
    if not securities:
        raise ScreenFinancialError("universe did not contain a valid six-digit stock code")
    return securities


def discover_universe_path() -> Path:
    for candidate in DEFAULT_UNIVERSE_PATHS:
        if candidate.exists():
            return candidate
    return DEFAULT_UNIVERSE_PATHS[0]


def load_universe(path: Path) -> list[UniverseSecurity]:
    with path.open("r", encoding="utf-8") as handle:
        return parse_universe(json.load(handle))


def api_url(endpoint: str, key: str, parameters: dict[str, str]) -> str:
    encoded_key = key if "%" in key else quote(key, safe="")
    suffix = urlencode(parameters)
    return f"{endpoint}?crtfc_key={encoded_key}" + (f"&{suffix}" if suffix else "")


def request_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "compound-asset-2045-screen/1.0",
            "Accept-Encoding": "identity",
        },
    )
    last_error: BaseException | None = None
    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read()
        except HTTPError as error:
            last_error = error
            retryable = error.code == 429 or 500 <= error.code <= 599
            if not retryable or attempt == MAX_HTTP_ATTEMPTS:
                raise
        except (URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt == MAX_HTTP_ATTEMPTS:
                raise
        time.sleep(RETRY_DELAY_SECONDS * attempt)
    raise ScreenFinancialError("OpenDART transport retries were exhausted") from last_error


def parse_corp_code_zip(payload: bytes) -> dict[str, CorpEntry]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
            if len(xml_names) != 1:
                raise DartDataError("corp-code archive must contain exactly one XML file")
            xml_payload = archive.read(xml_names[0])
    except zipfile.BadZipFile as error:
        raise DartDataError("corp-code response was not a ZIP archive") from error

    try:
        root = ElementTree.fromstring(xml_payload)
    except ElementTree.ParseError as error:
        raise DartDataError("corp-code XML could not be parsed") from error

    result: dict[str, CorpEntry] = {}
    for item in root.findall("list"):
        stock_code = normalize_stock_code(item.findtext("stock_code"))
        corp_code = str(item.findtext("corp_code") or "").strip()
        if stock_code is None or not re.fullmatch(r"\d{8}", corp_code):
            continue
        entry = CorpEntry(
            corp_code=corp_code,
            corp_name=str(item.findtext("corp_name") or "").strip(),
            stock_code=stock_code,
            modify_date=str(item.findtext("modify_date") or "").strip() or None,
        )
        previous = result.get(stock_code)
        if previous is not None and previous.corp_code != entry.corp_code:
            raise DartDataError(f"corp-code archive maps stock {stock_code} more than once")
        result[stock_code] = entry
    if not result:
        raise DartDataError("corp-code archive did not contain listed stock mappings")
    return result


def fetch_corp_directory(key: str) -> dict[str, CorpEntry]:
    return parse_corp_code_zip(request_bytes(api_url(CORP_CODE_URL, key, {})))


def chunked(values: Sequence[str], size: int = MAX_COMPANIES_PER_REQUEST) -> Iterable[list[str]]:
    if size < 1 or size > MAX_COMPANIES_PER_REQUEST:
        raise ValueError(f"chunk size must be between 1 and {MAX_COMPANIES_PER_REQUEST}")
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def fetch_major_accounts_batch(
    key: str,
    corp_codes: Sequence[str],
    *,
    report_year: int = REPORT_YEAR,
    report_code: str = REPORT_CODE,
) -> list[dict[str, Any]]:
    if not corp_codes or len(corp_codes) > MAX_COMPANIES_PER_REQUEST:
        raise ValueError("a multi-account request requires 1 to 100 companies")
    if len(set(corp_codes)) != len(corp_codes):
        raise ValueError("a multi-account request cannot contain duplicate companies")
    url = api_url(
        MULTI_ACCOUNT_URL,
        key,
        {
            "corp_code": ",".join(corp_codes),
            "bsns_year": str(report_year),
            "reprt_code": report_code,
        },
    )
    try:
        payload = json.loads(request_bytes(url).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DartDataError("multi-account response was not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise DartDataError("multi-account response must be a JSON object")

    status = str(payload.get("status", "")).strip()
    if status in NO_DATA_STATUSES:
        return []
    if status != "000":
        raise DartApiError(status, str(payload.get("message", "")))
    raw_rows = payload.get("list")
    if not isinstance(raw_rows, list):
        raise DartDataError("successful multi-account response did not contain a list")
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if len(rows) != len(raw_rows):
        raise DartDataError("multi-account response contained a non-object row")
    for row in rows:
        row_year = str(row.get("bsns_year", "")).strip()
        row_code = str(row.get("reprt_code", "")).strip()
        if row_year and row_year != str(report_year):
            raise DartDataError("multi-account response contained a different business year")
        if row_code and row_code != report_code:
            raise DartDataError("multi-account response contained a different report code")
    return rows


def _normalized_account_name(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


def _find_metric_row(rows: list[dict[str, Any]], spec: MetricSpec) -> dict[str, Any] | None:
    eligible = [row for row in rows if str(row.get("sj_div", "")).strip() in spec.statements]
    for account_id in spec.ids:
        for row in eligible:
            if str(row.get("account_id", "")).strip() == account_id:
                return row
    accepted_names = {_normalized_account_name(name) for name in spec.names}
    for row in eligible:
        if _normalized_account_name(row.get("account_nm")) in accepted_names:
            return row
    return None


def parse_amount(value: Any) -> int | float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if negative:
        number = -number
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def percent_change(current: int | float | None, previous: int | float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def _metric_payload(row: dict[str, Any] | None, *, key: str) -> dict[str, Any]:
    if row is None:
        pending = key == "operatingCashFlow"
        return {
            "current": None,
            "previous": None,
            "changePct": None,
            "status": (
                "pending_not_available_from_multi_account_api" if pending else "missing"
            ),
            "accountId": None,
            "accountName": None,
            "statement": "CF" if pending else None,
            "currency": None,
        }
    current = parse_amount(row.get("thstrm_amount"))
    previous = parse_amount(row.get("frmtrm_amount"))
    status = "available" if current is not None else "missing"
    if current is not None and previous is None:
        status = "current_only"
    return {
        "current": current,
        "previous": previous,
        "changePct": percent_change(current, previous),
        "status": status,
        "accountId": str(row.get("account_id", "")).strip() or None,
        "accountName": str(row.get("account_nm", "")).strip() or None,
        "statement": str(row.get("sj_div", "")).strip() or None,
        "currency": str(row.get("currency", "")).strip().upper() or None,
    }


def _select_statement_rows(rows: list[dict[str, Any]]) -> tuple[str | None, str | None, list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    by_division = {
        division: [row for row in rows if str(row.get("fs_div", "")).strip() == division]
        for division in ("CFS", "OFS")
    }
    fs_div = "CFS" if by_division["CFS"] else ("OFS" if by_division["OFS"] else None)
    if fs_div is None:
        return None, None, [], warnings
    selected = by_division[fs_div]
    receipts = sorted(
        {str(row.get("rcept_no", "")).strip() for row in selected if row.get("rcept_no")}
    )
    receipt: str | None = None
    if receipts:
        receipt = receipts[-1]
        if len(receipts) > 1:
            selected = [
                row for row in selected if str(row.get("rcept_no", "")).strip() == receipt
            ]
            warnings.append("multiple_receipts_latest_selected")
    return fs_div, receipt, selected, warnings


def company_financial_record(
    security: UniverseSecurity,
    corp: CorpEntry | None,
    rows: list[dict[str, Any]],
    *,
    report_year: int,
    report_code: str,
) -> dict[str, Any]:
    fs_div, receipt, selected_rows, warnings = _select_statement_rows(rows)
    metrics = {
        key: _metric_payload(_find_metric_row(selected_rows, spec), key=key)
        for key, spec in METRIC_SPECS.items()
    }
    missing = [
        key for key, metric in metrics.items() if metric["status"] in {"missing", "current_only"}
    ]
    pending = [key for key, metric in metrics.items() if str(metric["status"]).startswith("pending_")]
    currencies = sorted(
        {
            str(metric.get("currency")).strip().upper()
            for metric in metrics.values()
            if metric.get("currency")
        }
    )
    if corp is None:
        status = "missing_corp_code"
    elif not selected_rows:
        status = "no_report_rows"
    elif missing:
        status = "partial"
    else:
        status = "available"
    return {
        "code": security.code,
        "name": security.name or (corp.corp_name if corp else None),
        "market": security.market,
        "corpCode": corp.corp_code if corp else None,
        "reportYear": report_year,
        "reportCode": report_code,
        "fsDiv": fs_div,
        "rceptNo": receipt,
        "status": status,
        "metrics": metrics,
        "dataQuality": {
            "availableMetricCount": sum(
                1 for metric in metrics.values() if metric["status"] in {"available", "current_only"}
            ),
            "metricCount": len(metrics),
            "missingMetrics": missing,
            "pendingMetrics": pending,
            "warnings": warnings,
            "currencies": currencies,
        },
    }


def build_snapshot(
    universe: list[UniverseSecurity],
    key: str,
    *,
    report_year: int = REPORT_YEAR,
    report_code: str = REPORT_CODE,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    directory = fetch_corp_directory(key)
    mapped = {security.code: directory.get(security.code) for security in universe}
    requested_entries = [entry for entry in mapped.values() if entry is not None]
    mapping_coverage = len(requested_entries) / len(universe) if universe else 0.0
    if mapping_coverage < MIN_CORP_CODE_COVERAGE:
        raise DartDataError(
            "corp-code mapping coverage was below the safety threshold "
            f"({len(requested_entries)}/{len(universe)})"
        )
    corp_to_stock = {entry.corp_code: entry.stock_code for entry in requested_entries}
    rows_by_stock: dict[str, list[dict[str, Any]]] = {security.code: [] for security in universe}
    batch_count = 0

    corp_codes = [entry.corp_code for entry in requested_entries]
    for batch in chunked(corp_codes):
        batch_count += 1
        returned_rows = fetch_major_accounts_batch(
            key,
            batch,
            report_year=report_year,
            report_code=report_code,
        )
        allowed_stocks = {corp_to_stock[corp_code] for corp_code in batch}
        for row in returned_rows:
            stock_code = normalize_stock_code(row.get("stock_code"))
            if stock_code is None:
                stock_code = corp_to_stock.get(str(row.get("corp_code", "")).strip())
            if stock_code is None or stock_code not in allowed_stocks:
                raise DartDataError("multi-account response contained an unexpected company")
            rows_by_stock[stock_code].append(row)

    companies = [
        company_financial_record(
            security,
            mapped[security.code],
            rows_by_stock[security.code],
            report_year=report_year,
            report_code=report_code,
        )
        for security in universe
    ]
    available_count = sum(company["status"] in {"available", "partial"} for company in companies)
    report_coverage = (
        available_count / len(requested_entries) if requested_entries else 0.0
    )
    if report_coverage < MIN_REPORT_COVERAGE:
        raise DartDataError(
            "annual-report coverage was below the safety threshold "
            f"({available_count}/{len(requested_entries)})"
        )
    selected_at = fetched_at or datetime.now(KST)
    if selected_at.tzinfo is None:
        selected_at = selected_at.replace(tzinfo=KST)
    return {
        "schemaVersion": 1,
        "generatedAt": selected_at.astimezone(KST).isoformat(timespec="seconds"),
        "source": {
            "provider": "OpenDART",
            "endpoint": "fnlttMultiAcnt",
            "guideUrl": SOURCE_GUIDE_URL,
            "reportYear": report_year,
            "reportCode": report_code,
            "cfsPolicy": "prefer_CFS_else_OFS_from_same_response",
            "cashFlowCoverage": "pending_unless_provider_returns_CF_rows",
        },
        "summary": {
            "universeCount": len(universe),
            "corpCodeMappedCount": len(requested_entries),
            "batchCount": batch_count,
            "reportAvailableCount": available_count,
            "corpCodeCoveragePct": round(mapping_coverage * 100, 2),
            "reportCoveragePct": round(report_coverage * 100, 2),
            "missingCorpCodeCount": sum(company["status"] == "missing_corp_code" for company in companies),
            "noReportCount": sum(company["status"] == "no_report_rows" for company in companies),
        },
        "companies": companies,
    }


def atomic_write(value: dict[str, Any], path: Path = OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def refresh_file(
    key: str,
    *,
    universe_path: Path | None = None,
    output_path: Path = OUTPUT_PATH,
    report_year: int | None = None,
    report_code: str = REPORT_CODE,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    selected_universe = universe_path or discover_universe_path()
    universe = load_universe(selected_universe)
    selected_report_year = report_year if report_year is not None else latest_expected_annual_year()
    snapshot = build_snapshot(
        universe,
        key,
        report_year=selected_report_year,
        report_code=report_code,
        fetched_at=fetched_at,
    )
    atomic_write(snapshot, output_path)
    return snapshot["summary"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="annual report business year (default: latest broadly available year)",
    )
    parser.add_argument("--report-code", default=REPORT_CODE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("screen financial refresh failed: DART_API_KEY is not set; snapshot preserved", file=sys.stderr)
        return 1
    try:
        summary = refresh_file(
            key,
            universe_path=arguments.universe,
            output_path=arguments.output,
            report_year=arguments.year,
            report_code=arguments.report_code,
        )
    except (
        ScreenFinancialError,
        OSError,
        HTTPError,
        URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        # Never echo transport URLs because they contain the API key.
        label = str(error) if isinstance(error, ScreenFinancialError) else type(error).__name__
        print(f"screen financial refresh failed: {label}; snapshot preserved", file=sys.stderr)
        return 1

    print(
        "screen financial refresh atomically wrote "
        f"{summary['reportAvailableCount']}/{summary['universeCount']} companies "
        f"in {summary['batchCount']} batch(es)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
