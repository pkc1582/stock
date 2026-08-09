#!/usr/bin/env python3
"""Build an annual OpenDART financial snapshot for the screening universe.

The inexpensive multi-company endpoint is intentionally used for the first
pass.  It supplies balance-sheet and income-statement major accounts for up to
100 issuers per request.  Operating cash flow is represented in the schema but
usually remains pending because ``fnlttMultiAcnt`` does not promise cash-flow
statement rows.  A later, smaller-company pass can fill it from the full
statement endpoint without changing this file format.

To conserve the daily OpenDART allowance, requests are sent only for securities
that remain preliminary-eligible in the universe and have both market
capitalization of at least KRW 100 billion and daily trading value of at least
KRW 100 million.  Every skipped security still receives an explicit placeholder
record so downstream coverage and rejection audits remain complete.

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
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
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
MARKET_PATH = ROOT / "data" / "market-universe.json"

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
DART_BATCH_SIZE = 25
HTTP_TIMEOUT_SECONDS = 20
MAX_HTTP_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 0.4

NO_DATA_STATUSES = frozenset({"013"})
STOCK_CODE_PATTERN = re.compile(r"^(?:A)?([0-9A-HJ-NP-TV-Z]{6})$", re.IGNORECASE)
MIN_CORP_CODE_COVERAGE = 0.70
MIN_REPORT_COVERAGE = 0.70
MIN_MARKET_CAP = 100_000_000_000
MIN_TRADING_VALUE = 100_000_000


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
    eligible_for_preliminary_screen: bool = True
    exclusion_reasons: tuple[str, ...] = ()
    classification: str | None = None


@dataclass(frozen=True)
class MarketSecurity:
    code: str
    market_cap: int | float | None
    trading_value: int | float | None


@dataclass(frozen=True)
class DartTargetDecision:
    target: bool
    status: str
    reasons: tuple[dict[str, str], ...]


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
    return match.group(1).upper() if match else None


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


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "yes", "y", "1", "eligible", "included"}:
        return True
    if normalized in {"false", "no", "n", "0", "ineligible", "excluded"}:
        return False
    return None


def _reason_values(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            if isinstance(item, dict):
                selected = _alias(item, "code", "reason", "message")
                if selected not in (None, ""):
                    values.append(str(selected).strip())
            elif str(item).strip():
                values.append(str(item).strip())
        return values
    return []


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
        exclusion_reasons: list[str] = []
        for key in (
            "exclusionReasons",
            "exclusionReason",
            "exclusion_reasons",
            "excludeReasons",
            "reasons",
        ):
            exclusion_reasons.extend(_reason_values(raw.get(key)))
        classification_value = _alias(raw, "classification", "securityClassification")
        classification = (
            str(classification_value).strip()
            if classification_value not in (None, "")
            else None
        )
        eligible_value = _boolean(
            _alias(
                raw,
                "eligibleForPreliminaryScreen",
                "eligible",
                "included",
                "isEligible",
            )
        )
        source_status = str(
            _alias(raw, "screeningStatus", "eligibilityStatus", "status") or ""
        ).strip().lower()
        explicitly_excluded = source_status in {"excluded", "ineligible", "rejected"}
        name_class_excluded = classification in {
            "name_likely_preferred",
            "name_likely_spac",
            "name_likely_reit",
        }
        eligible = (
            eligible_value is not False
            and not explicitly_excluded
            and not exclusion_reasons
            and not name_class_excluded
        )
        securities.append(
            UniverseSecurity(
                code=code,
                name=str(name_value).strip() if name_value not in (None, "") else None,
                market=str(market_value).strip() if market_value not in (None, "") else None,
                eligible_for_preliminary_screen=eligible,
                exclusion_reasons=tuple(dict.fromkeys(exclusion_reasons)),
                classification=classification,
            )
        )
    if not securities:
        raise ScreenFinancialError("universe did not contain a valid six-character stock code")
    return securities


def discover_universe_path() -> Path:
    for candidate in DEFAULT_UNIVERSE_PATHS:
        if candidate.exists():
            return candidate
    return DEFAULT_UNIVERSE_PATHS[0]


def load_universe(path: Path) -> list[UniverseSecurity]:
    with path.open("r", encoding="utf-8") as handle:
        return parse_universe(json.load(handle))


def parse_market(payload: Any) -> dict[str, MarketSecurity]:
    market: dict[str, MarketSecurity] = {}
    for raw in _record_array(payload):
        if not isinstance(raw, dict):
            continue
        code = normalize_stock_code(_alias(raw, "code", "srtnCd", "stockCode", "stock_code"))
        if code is None:
            continue
        if code in market:
            raise ScreenFinancialError(f"duplicate stock code in market data: {code}")
        market_cap = parse_amount(
            _alias(raw, "marketCap", "mrktTotAmt", "marketCapitalization")
        )
        trading_value = parse_amount(
            _alias(
                raw,
                "tradingValue",
                "tradeValue",
                "averageTradingValue20d",
                "avgTradingValue20d",
                "accTrdval",
            )
        )
        market[code] = MarketSecurity(
            code=code,
            market_cap=market_cap,
            trading_value=trading_value,
        )
    if not market:
        raise ScreenFinancialError("market data did not contain a valid six-character stock code")
    return market


def load_market(path: Path) -> tuple[Any, dict[str, MarketSecurity]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload, parse_market(payload)


def api_url(endpoint: str, key: str, parameters: dict[str, str]) -> str:
    encoded_key = key if "%" in key else quote(key, safe="")
    # OpenDART documents the multi-company corp_code list with literal commas.
    suffix = urlencode(parameters, safe=",")
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


UPSTREAM_REASON_MESSAGES = {
    "name_indicates_preferred": "종목명 기준 우선주 후보로 분류되어 1차 대상에서 제외했습니다.",
    "name_indicates_spac": "종목명 기준 SPAC 후보로 분류되어 1차 대상에서 제외했습니다.",
    "name_indicates_reit": "종목명 기준 REIT 후보로 분류되어 1차 대상에서 제외했습니다.",
    "not_preliminary_eligible": "상장 종목 원천에서 잠정 선별 비적격으로 표시했습니다.",
}


def _reason(code: str, source: str, message: str) -> dict[str, str]:
    return {"code": code, "source": source, "message": message}


def _upstream_reasons(security: UniverseSecurity) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    for value in security.exclusion_reasons:
        code = value if re.fullmatch(r"[a-z0-9_]+", value) else "upstream_exclusion"
        reasons.append(
            _reason(
                code,
                "universe",
                UPSTREAM_REASON_MESSAGES.get(code, value),
            )
        )
    if not security.eligible_for_preliminary_screen and not reasons:
        classification = str(security.classification or "")
        derived = {
            "name_likely_preferred": "name_indicates_preferred",
            "name_likely_spac": "name_indicates_spac",
            "name_likely_reit": "name_indicates_reit",
        }.get(classification, "not_preliminary_eligible")
        reasons.append(
            _reason(derived, "universe", UPSTREAM_REASON_MESSAGES[derived])
        )
    return reasons


def dart_target_decision(
    security: UniverseSecurity,
    market: MarketSecurity | None,
) -> DartTargetDecision:
    """Apply cheap upstream gates before spending a DART company request."""

    reasons = _upstream_reasons(security)
    if market is None:
        reasons.append(
            _reason(
                "missing_market_record",
                "market",
                "전 종목 시세 파일에 일치하는 종목이 없어 DART 조회를 보류했습니다.",
            )
        )
    else:
        if market.market_cap is None:
            reasons.append(
                _reason(
                    "missing_market_cap",
                    "market",
                    "시가총액이 없어 DART 조회를 보류했습니다.",
                )
            )
        elif market.market_cap < MIN_MARKET_CAP:
            reasons.append(
                _reason(
                    "market_cap_below_minimum",
                    "market",
                    "시가총액이 1,000억원 미만이어서 DART 조회를 생략했습니다.",
                )
            )
        if market.trading_value is None:
            reasons.append(
                _reason(
                    "missing_trading_value",
                    "market",
                    "거래대금이 없어 DART 조회를 보류했습니다.",
                )
            )
        elif market.trading_value < MIN_TRADING_VALUE:
            reasons.append(
                _reason(
                    "trading_value_below_minimum",
                    "market",
                    "일 거래대금이 1억원 미만이어서 DART 조회를 생략했습니다.",
                )
            )

    if not reasons:
        return DartTargetDecision(True, "dart_target", ())
    reason_codes = {reason["code"] for reason in reasons}
    if any(code.startswith("name_indicates_") for code in reason_codes):
        status = "skipped_name_gate"
    elif any(reason["source"] == "universe" for reason in reasons):
        status = "skipped_upstream_gate"
    else:
        status = "skipped_market_gate"
    return DartTargetDecision(False, status, tuple(reasons))


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


def skipped_financial_record(
    security: UniverseSecurity,
    decision: DartTargetDecision,
    *,
    report_year: int,
    report_code: str,
) -> dict[str, Any]:
    """Create an auditable placeholder without pretending DART was queried."""

    metric_status = "not_requested_due_to_pre_screen_gate"
    metrics = {
        key: {
            "current": None,
            "previous": None,
            "changePct": None,
            "status": metric_status,
            "accountId": None,
            "accountName": None,
            "statement": "CF" if key == "operatingCashFlow" else None,
            "currency": None,
        }
        for key in METRIC_SPECS
    }
    reasons = [dict(reason) for reason in decision.reasons]
    return {
        "code": security.code,
        "name": security.name,
        "market": security.market,
        "corpCode": None,
        "reportYear": report_year,
        "reportCode": report_code,
        "fsDiv": None,
        "rceptNo": None,
        "status": decision.status,
        "skipReasons": reasons,
        "dartRequest": {
            "target": False,
            "requested": False,
            "status": decision.status,
            "reasons": reasons,
        },
        "metrics": metrics,
        "dataQuality": {
            "availableMetricCount": 0,
            "metricCount": len(metrics),
            "missingMetrics": [],
            "pendingMetrics": [],
            "notRequestedMetrics": list(metrics),
            "warnings": [reason["code"] for reason in reasons],
            "currencies": [],
        },
    }


def build_snapshot(
    universe: list[UniverseSecurity],
    key: str,
    *,
    market: dict[str, MarketSecurity] | None = None,
    market_basis_date: str | None = None,
    report_year: int = REPORT_YEAR,
    report_code: str = REPORT_CODE,
    fetched_at: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    decisions = {
        security.code: (
            dart_target_decision(security, market.get(security.code))
            if market is not None
            else DartTargetDecision(True, "dart_target", ())
        )
        for security in universe
    }
    targets = [security for security in universe if decisions[security.code].target]
    if progress is not None:
        progress(
            "OpenDART pre-screen selected "
            f"{len(targets)}/{len(universe)} securities for financial requests"
        )
        progress("OpenDART corporate-code directory download started")
    directory = fetch_corp_directory(key) if targets else {}
    if progress is not None:
        progress("OpenDART corporate-code directory download completed")
    mapped = {security.code: directory.get(security.code) for security in targets}
    requested_entries = [entry for entry in mapped.values() if entry is not None]
    mapping_coverage = len(requested_entries) / len(targets) if targets else 1.0
    if targets and mapping_coverage < MIN_CORP_CODE_COVERAGE:
        raise DartDataError(
            "corp-code mapping coverage was below the safety threshold "
            f"({len(requested_entries)}/{len(targets)} DART targets)"
        )
    corp_to_stock = {entry.corp_code: entry.stock_code for entry in requested_entries}
    rows_by_stock: dict[str, list[dict[str, Any]]] = {security.code: [] for security in universe}
    batch_count = 0

    corp_codes = [entry.corp_code for entry in requested_entries]
    expected_batch_count = (
        (len(corp_codes) + DART_BATCH_SIZE - 1) // DART_BATCH_SIZE
    )
    for batch in chunked(corp_codes, DART_BATCH_SIZE):
        batch_count += 1
        started_at = time.monotonic()
        if progress is not None:
            progress(
                "OpenDART financial batch "
                f"{batch_count}/{expected_batch_count} started "
                f"({len(batch)} target companies)"
            )
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
        if progress is not None:
            progress(
                "OpenDART financial batch "
                f"{batch_count}/{expected_batch_count} completed "
                f"({len(batch)} target companies, "
                f"{time.monotonic() - started_at:.1f}s)"
            )

    companies: list[dict[str, Any]] = []
    for security in universe:
        decision = decisions[security.code]
        if not decision.target:
            companies.append(
                skipped_financial_record(
                    security,
                    decision,
                    report_year=report_year,
                    report_code=report_code,
                )
            )
            continue
        corp = mapped.get(security.code)
        record = company_financial_record(
            security,
            corp,
            rows_by_stock[security.code],
            report_year=report_year,
            report_code=report_code,
        )
        requested = corp is not None
        request_status = "requested" if requested else "missing_corp_code"
        request_reasons = (
            []
            if requested
            else [
                _reason(
                    "missing_corp_code",
                    "opendart_corp_directory",
                    "DART 기업 고유번호를 연결하지 못해 재무 요청을 보내지 못했습니다.",
                )
            ]
        )
        record["skipReasons"] = request_reasons
        record["dartRequest"] = {
            "target": True,
            "requested": requested,
            "status": request_status,
            "reasons": request_reasons,
        }
        companies.append(record)

    requested_stock_codes = {entry.stock_code for entry in requested_entries}
    available_count = sum(
        company["code"] in requested_stock_codes
        and company["status"] in {"available", "partial"}
        for company in companies
    )
    report_coverage = (
        available_count / len(requested_entries) if requested_entries else 1.0
    )
    if requested_entries and report_coverage < MIN_REPORT_COVERAGE:
        raise DartDataError(
            "annual-report coverage was below the safety threshold "
            f"({available_count}/{len(requested_entries)} requested DART companies)"
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
            "marketBasisDate": market_basis_date,
            "preRequestGates": {
                "preliminaryEligibility": True,
                "minimumMarketCapKrw": MIN_MARKET_CAP,
                "minimumTradingValueKrw": MIN_TRADING_VALUE,
            },
        },
        "summary": {
            "universeCount": len(universe),
            "dartTargetCount": len(targets),
            "dartRequestedCount": len(requested_entries),
            "preRequestSkippedCount": len(universe) - len(targets),
            "skippedNameGateCount": sum(
                decision.status == "skipped_name_gate" for decision in decisions.values()
            ),
            "skippedUpstreamGateCount": sum(
                any(reason["source"] == "universe" for reason in decision.reasons)
                for decision in decisions.values()
            ),
            "skippedMarketGateCount": sum(
                any(reason["source"] == "market" for reason in decision.reasons)
                for decision in decisions.values()
            ),
            "corpCodeMappedCount": len(requested_entries),
            "batchCount": batch_count,
            "reportAvailableCount": available_count,
            "corpCodeCoveragePct": round(mapping_coverage * 100, 2),
            "reportCoveragePct": round(report_coverage * 100, 2),
            "missingCorpCodeCount": sum(
                company["status"] == "missing_corp_code" for company in companies
            ),
            "noReportCount": sum(
                company["status"] == "no_report_rows" for company in companies
            ),
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
    market_path: Path | None = None,
    output_path: Path = OUTPUT_PATH,
    report_year: int | None = None,
    report_code: str = REPORT_CODE,
    fetched_at: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    selected_universe = universe_path or discover_universe_path()
    universe = load_universe(selected_universe)
    market_payload: Any | None = None
    market_index: dict[str, MarketSecurity] | None = None
    if market_path is not None:
        market_payload, market_index = load_market(market_path)
    market_basis_date = None
    if isinstance(market_payload, dict):
        raw_basis_date = _alias(market_payload, "basisDate", "basDt", "asOf")
        market_basis_date = str(raw_basis_date).strip() if raw_basis_date not in (None, "") else None
    selected_report_year = report_year if report_year is not None else latest_expected_annual_year()
    snapshot = build_snapshot(
        universe,
        key,
        market=market_index,
        market_basis_date=market_basis_date,
        report_year=selected_report_year,
        report_code=report_code,
        fetched_at=fetched_at,
        progress=progress,
    )
    atomic_write(snapshot, output_path)
    return snapshot["summary"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument(
        "--market",
        type=Path,
        default=MARKET_PATH,
        help="full-market snapshot used for the cheap pre-request gates",
    )
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
            market_path=arguments.market,
            output_path=arguments.output,
            report_year=arguments.year,
            report_code=arguments.report_code,
            progress=lambda message: print(message, flush=True),
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
        f"{summary['reportAvailableCount']}/{summary['dartRequestedCount']} requested companies; "
        f"skipped {summary['preRequestSkippedCount']}/{summary['universeCount']} before DART; "
        f"in {summary['batchCount']} batch(es)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
