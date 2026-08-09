#!/usr/bin/env python3
"""Refresh annual compatibility fields and the latest OpenDART report.

The legacy ``revenue2025``/``eps2025`` fields remain tied to the 2025 annual
report.  Newer quarterly and semi-annual data is stored separately under
``financials.latestReport`` so values from different reporting periods are
never presented as one statement.

Requires ``DART_API_KEY``. Corporate-code XML and statement responses are
processed in memory only; no raw provider response or credential is committed.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import sys
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "companies.json"
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIAL_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
KST = ZoneInfo("Asia/Seoul")

NO_DATA_STATUSES = frozenset({"013", "014"})
MIN_SUCCESS_RATE = 0.80
LOOKBACK_YEARS = 2


class DartRefreshError(RuntimeError):
    """Base exception for failures that must preserve the existing snapshot."""


class DartApiError(DartRefreshError):
    """OpenDART returned an error that must not be treated as missing data."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.api_message = message
        super().__init__(f"OpenDART status {status}: {message or 'unknown API error'}")


class DartDataError(DartRefreshError):
    """OpenDART returned a structurally inconsistent report."""


class CoverageError(DartRefreshError):
    """Too few companies returned a usable latest report."""


@dataclass(frozen=True)
class ReportCandidate:
    report_year: int
    report_code: str
    kind: str
    period_end: date
    elapsed_months: int

    @property
    def is_annual(self) -> bool:
        return self.report_code == "11011"

    @property
    def period_label(self) -> str:
        if self.is_annual:
            return f"{self.report_year}년 사업보고서"
        return f"{self.report_year}년 {self.kind} 누적"


@dataclass(frozen=True)
class StatementReport:
    candidate: ReportCandidate
    fs_div: str
    rcept_no: str
    rows: list[dict[str, Any]]


REPORT_PERIODS = (
    ("11011", "사업보고서", 12, 31, 12),
    ("11014", "3분기", 9, 30, 9),
    ("11012", "반기", 6, 30, 6),
    ("11013", "1분기", 3, 31, 3),
)

LATEST_VALUE_KEYS = (
    "revenueEok",
    "operatingMargin",
    "roeAnnualized",
    "debtRatio",
    "epsCumulative",
)


def api_url(endpoint: str, key: str, parameters: dict[str, str]) -> str:
    encoded_key = key if "%" in key else quote(key, safe="")
    return f"{endpoint}?crtfc_key={encoded_key}&{urlencode(parameters)}"


def request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "compound-asset-2045/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def corp_code_map(key: str) -> dict[str, str]:
    payload = request_bytes(api_url(CORP_CODE_URL, key, {}))
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        xml_payload = archive.read("CORPCODE.xml")
    root = ElementTree.fromstring(xml_payload)
    mapping: dict[str, str] = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if len(stock_code) == 6 and corp_code:
            mapping[stock_code] = corp_code
    return mapping


def report_candidates(as_of: date, lookback_years: int = LOOKBACK_YEARS) -> list[ReportCandidate]:
    """Return plausible reports in descending period-end order.

    The current universe uses December fiscal year-ends. A candidate is probed
    after its reporting period ends; an expected 013/014 response simply moves
    discovery to the next older period.
    """

    candidates: list[ReportCandidate] = []
    for report_year in range(as_of.year, as_of.year - lookback_years - 1, -1):
        for report_code, kind, month, day, elapsed_months in REPORT_PERIODS:
            period_end = date(report_year, month, day)
            if period_end <= as_of:
                candidates.append(
                    ReportCandidate(
                        report_year=report_year,
                        report_code=report_code,
                        kind=kind,
                        period_end=period_end,
                        elapsed_months=elapsed_months,
                    )
                )
    candidates.sort(key=lambda candidate: candidate.period_end, reverse=True)
    return candidates


def annual_2025_candidate() -> ReportCandidate:
    return ReportCandidate(2025, "11011", "사업보고서", date(2025, 12, 31), 12)


def _statement_rows(
    key: str,
    corp_code: str,
    candidate: ReportCandidate,
    fs_div: str,
) -> list[dict[str, Any]] | None:
    url = api_url(
        FINANCIAL_URL,
        key,
        {
            "corp_code": corp_code,
            "bsns_year": str(candidate.report_year),
            "reprt_code": candidate.report_code,
            "fs_div": fs_div,
        },
    )
    payload = json.loads(request_bytes(url).decode("utf-8"))
    if not isinstance(payload, dict):
        raise DartDataError("OpenDART statement response must be a JSON object")

    status = str(payload.get("status", ""))
    if status in NO_DATA_STATUSES:
        return None
    if status != "000":
        raise DartApiError(status or "missing", str(payload.get("message", "")))

    raw_rows = payload.get("list")
    if not isinstance(raw_rows, list) or not raw_rows:
        # OFS fallback is intentionally limited to explicit 013/014 responses.
        raise DartDataError("OpenDART status 000 did not include statement rows")
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if not rows:
        raise DartDataError("OpenDART statement response contained no object rows")
    return rows


def _validated_receipt(rows: list[dict[str, Any]], candidate: ReportCandidate) -> str:
    report_codes = {str(row.get("reprt_code", "")) for row in rows if row.get("reprt_code")}
    report_years = {str(row.get("bsns_year", "")) for row in rows if row.get("bsns_year")}
    receipt_numbers = {str(row.get("rcept_no", "")).strip() for row in rows if row.get("rcept_no")}

    if report_codes and report_codes != {candidate.report_code}:
        raise DartDataError("OpenDART rows contain a different report code")
    if report_years and report_years != {str(candidate.report_year)}:
        raise DartDataError("OpenDART rows contain a different business year")
    if len(receipt_numbers) != 1:
        raise DartDataError("OpenDART rows must belong to exactly one receipt")
    return next(iter(receipt_numbers))


def fetch_report(key: str, corp_code: str, candidate: ReportCandidate) -> StatementReport | None:
    """Fetch CFS first and use OFS only when CFS explicitly has no data."""

    rows = _statement_rows(key, corp_code, candidate, "CFS")
    if rows is not None:
        return StatementReport(candidate, "CFS", _validated_receipt(rows, candidate), rows)

    rows = _statement_rows(key, corp_code, candidate, "OFS")
    if rows is None:
        return None
    return StatementReport(candidate, "OFS", _validated_receipt(rows, candidate), rows)


def parse_amount(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text == "-":
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def find_row(
    rows: list[dict[str, Any]],
    ids: tuple[str, ...],
    names: tuple[str, ...],
    statement_divisions: tuple[str, ...],
) -> dict[str, Any] | None:
    eligible = [row for row in rows if str(row.get("sj_div", "")) in statement_divisions]
    for row in eligible:
        if str(row.get("account_id", "")) in ids:
            return row
    patterns = [re.compile(pattern) for pattern in names]
    for row in eligible:
        account_name = str(row.get("account_nm", "")).replace(" ", "")
        if any(pattern.fullmatch(account_name) for pattern in patterns):
            return row
    return None


def _flow_amount(row: dict[str, Any] | None, candidate: ReportCandidate) -> float | None:
    if row is None:
        return None
    key = "thstrm_amount" if candidate.is_annual else "thstrm_add_amount"
    return parse_amount(row.get(key))


def report_metrics(report: StatementReport) -> dict[str, float | int | None]:
    """Derive one internally consistent metric set from exactly one report."""

    rows = report.rows
    candidate = report.candidate
    revenue_row = find_row(
        rows,
        ("ifrs-full_Revenue",),
        (r"매출액", r"영업수익", r"수익\(매출액\)"),
        ("IS", "CIS"),
    )
    operating_row = find_row(
        rows,
        ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"),
        (r"영업이익", r"영업이익\(손실\)"),
        ("IS", "CIS"),
    )
    profit_row = find_row(
        rows,
        ("ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_ProfitLoss"),
        (r"지배기업소유주지분당기순이익", r"당기순이익"),
        ("IS", "CIS"),
    )
    liabilities_row = find_row(
        rows,
        ("ifrs-full_Liabilities",),
        (r"부채총계",),
        ("BS",),
    )
    equity_row = find_row(
        rows,
        ("ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity"),
        (r"자본총계",),
        ("BS",),
    )
    eps_row = find_row(
        rows,
        ("ifrs-full_BasicEarningsLossPerShare",),
        (r"기본주당이익", r"기본주당손익"),
        ("IS", "CIS"),
    )

    revenue = _flow_amount(revenue_row, candidate)
    operating = _flow_amount(operating_row, candidate)
    profit = _flow_amount(profit_row, candidate)
    eps = _flow_amount(eps_row, candidate)
    liabilities = parse_amount(liabilities_row.get("thstrm_amount")) if liabilities_row else None
    equity = parse_amount(equity_row.get("thstrm_amount")) if equity_row else None
    prior_equity = parse_amount(equity_row.get("frmtrm_amount")) if equity_row else None

    revenue_eok = int(round(revenue / 100_000_000)) if revenue is not None else None
    operating_margin = round(operating / revenue * 100, 2) if operating is not None and revenue else None
    debt_ratio = round(liabilities / equity * 100, 2) if liabilities is not None and equity and equity > 0 else None

    roe_annualized: float | None = None
    if profit is not None and equity and equity > 0:
        average_equity = (equity + prior_equity) / 2 if prior_equity and prior_equity > 0 else equity
        roe_annualized = round(
            profit / average_equity * 100 * (12 / candidate.elapsed_months),
            2,
        )

    return {
        "revenueEok": revenue_eok,
        "operatingMargin": operating_margin,
        "roeAnnualized": roe_annualized,
        "debtRatio": debt_ratio,
        "epsCumulative": int(round(eps)) if eps is not None else None,
    }


def latest_report_payload(report: StatementReport, fetched_at: datetime) -> dict[str, Any]:
    metrics = report_metrics(report)
    missing_metrics = [key for key in LATEST_VALUE_KEYS if metrics[key] is None]
    return {
        "reportYear": report.candidate.report_year,
        "reportCode": report.candidate.report_code,
        "periodLabel": report.candidate.period_label,
        "fsDiv": report.fs_div,
        "rceptNo": report.rcept_no,
        "periodEnd": report.candidate.period_end.isoformat(),
        "fetchedAt": fetched_at.astimezone(KST).isoformat(timespec="seconds"),
        **metrics,
        "dataQuality": "complete" if not missing_metrics else "partial",
        "missingMetrics": missing_metrics,
    }


def update_annual_2025_fields(financials: dict[str, Any], report: StatementReport) -> bool:
    """Update legacy fields from the 2025 annual report only."""

    if report.candidate.report_year != 2025 or not report.candidate.is_annual:
        raise DartDataError("legacy 2025 fields require the 2025 annual report")
    metrics = report_metrics(report)
    mapping = {
        "revenue2025": "revenueEok",
        "operatingMargin2025": "operatingMargin",
        "roe2025": "roeAnnualized",
        "debtRatio2025": "debtRatio",
        "eps2025": "epsCumulative",
    }
    changed = False
    for legacy_key, metric_key in mapping.items():
        value = metrics[metric_key]
        if value is not None and financials.get(legacy_key) != value:
            financials[legacy_key] = value
            changed = True
    return changed


def _existing_period_end(financials: dict[str, Any]) -> date | None:
    latest = financials.get("latestReport")
    if not isinstance(latest, dict):
        return None
    raw = latest.get("periodEnd")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def refresh_data(
    source: dict[str, Any],
    key: str,
    *,
    as_of: date | None = None,
    fetched_at: datetime | None = None,
    sleep_seconds: float = 0.08,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a refreshed copy; raise before any file write on unsafe results."""

    checked_at = fetched_at or datetime.now(KST)
    checked_date = as_of or checked_at.astimezone(KST).date()
    candidates = report_candidates(checked_date)
    if not candidates:
        raise DartDataError("no report candidate is available for the requested date")

    companies = source.get("companies")
    if not isinstance(companies, list) or not companies:
        raise DartDataError("companies.json must contain at least one company")

    working = copy.deepcopy(source)
    working_companies = working["companies"]
    mapping = corp_code_map(key)
    report_cache: dict[tuple[str, int, str], StatementReport | None] = {}

    def cached_report(corp_code: str, candidate: ReportCandidate) -> StatementReport | None:
        cache_key = (corp_code, candidate.report_year, candidate.report_code)
        if cache_key not in report_cache:
            report_cache[cache_key] = fetch_report(key, corp_code, candidate)
        return report_cache[cache_key]

    success_count = 0
    complete_count = 0
    partial_count = 0
    failed_count = 0
    annual_2025_report_count = 0
    annual_2025_partial_count = 0
    annual_2025_changed_count = 0
    preserved_newer_count = 0

    for company in working_companies:
        if not isinstance(company, dict):
            failed_count += 1
            continue
        stock_code = str(company.get("code", ""))
        corp_code = mapping.get(stock_code)
        if not corp_code:
            failed_count += 1
            continue

        latest_report: StatementReport | None = None
        for candidate in candidates:
            latest_report = cached_report(corp_code, candidate)
            if latest_report is not None:
                break

        financials = company.get("financials")
        if not isinstance(financials, dict):
            financials = {}
            company["financials"] = financials

        if latest_report is None:
            failed_count += 1
        else:
            success_count += 1
            payload = latest_report_payload(latest_report, checked_at)
            if payload["dataQuality"] == "complete":
                complete_count += 1
            else:
                partial_count += 1

            existing_period_end = _existing_period_end(financials)
            if existing_period_end and existing_period_end > latest_report.candidate.period_end:
                preserved_newer_count += 1
            else:
                financials["latestReport"] = payload

        annual_report = cached_report(corp_code, annual_2025_candidate())
        if annual_report is not None:
            annual_2025_report_count += 1
            annual_metrics = report_metrics(annual_report)
            if any(annual_metrics[key] is None for key in LATEST_VALUE_KEYS):
                annual_2025_partial_count += 1
            if update_annual_2025_fields(financials, annual_report):
                annual_2025_changed_count += 1

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    company_count = len(working_companies)
    success_rate = success_count / company_count
    if success_rate < MIN_SUCCESS_RATE:
        raise CoverageError(
            f"latest-report coverage {success_count}/{company_count} "
            f"({success_rate:.1%}) is below {MIN_SUCCESS_RATE:.0%}"
        )

    fully_refreshed = (
        success_count == company_count
        and complete_count == company_count
        and annual_2025_report_count == company_count
        and annual_2025_partial_count == 0
    )
    status = (
        "updated_from_opendart_latest_reports"
        if fully_refreshed
        else "partially_updated_from_opendart_latest_reports"
    )
    summary = {
        "checkedAt": checked_at.astimezone(KST).isoformat(timespec="seconds"),
        "asOf": checked_date.isoformat(),
        "companyCount": company_count,
        "latestReportSuccessCount": success_count,
        "latestReportCompleteCount": complete_count,
        "latestReportPartialCount": partial_count,
        "latestReportFailedCount": failed_count,
        "latestReportSuccessRate": round(success_rate, 4),
        "minimumSuccessRate": MIN_SUCCESS_RATE,
        "annual2025ReportCount": annual_2025_report_count,
        "annual2025PartialCount": annual_2025_partial_count,
        "annual2025ChangedCount": annual_2025_changed_count,
        "preservedNewerReportCount": preserved_newer_count,
    }
    working["financialDataStatus"] = status
    working["financialDataSummary"] = summary
    return working, summary


def atomic_write(value: dict[str, Any], path: Path = DATA_PATH) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def refresh_file(
    key: str,
    *,
    path: Path = DATA_PATH,
    as_of: date | None = None,
    fetched_at: datetime | None = None,
    sleep_seconds: float = 0.08,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        source = json.load(handle)
    refreshed, summary = refresh_data(
        source,
        key,
        as_of=as_of,
        fetched_at=fetched_at,
        sleep_seconds=sleep_seconds,
    )
    atomic_write(refreshed, path)
    return summary


def main() -> int:
    key = os.environ.get("DART_API_KEY")
    if not key:
        print("OpenDART refresh failed: DART_API_KEY is not set; snapshot preserved", file=sys.stderr)
        return 1

    try:
        summary = refresh_file(key)
    except (
        DartRefreshError,
        HTTPError,
        URLError,
        TimeoutError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        KeyError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        print(f"OpenDART refresh failed: {error}; snapshot preserved", file=sys.stderr)
        return 1

    print(
        "OpenDART refresh verified "
        f"{summary['latestReportSuccessCount']}/{summary['companyCount']} latest reports; "
        f"2025 annual reports {summary['annual2025ReportCount']}/{summary['companyCount']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
