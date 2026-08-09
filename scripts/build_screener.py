#!/usr/bin/env python3
"""Combine universe, market, and OpenDART data into a transparent pre-screen.

This is a quantitative *candidate funnel*, not a CAVM result.  Every unreviewed
company keeps ``cavm: null``.  The output contains at most 200 candidates and a
reasoned record for every company that was rejected or fell below the cutoff.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNIVERSE_PATHS = (
    ROOT / "data" / "universe.json",
    ROOT / "data" / "krx-universe.json",
    ROOT / "data" / "stock-universe.json",
)
MARKET_PATH = ROOT / "data" / "market-universe.json"
FINANCIAL_PATH = ROOT / "data" / "screen-financials.json"
OUTPUT_PATH = ROOT / "public" / "data" / "screener.json"
KST = ZoneInfo("Asia/Seoul")

MAX_CANDIDATES = 200
MIN_MARKET_CAP = 100_000_000_000  # 1,000억원
MIN_LIQUIDITY_VALUE = 100_000_000  # 일 거래대금 또는 제공된 평균 거래대금 1억원
MIN_REVENUE = 100_000_000_000  # 비금융사 연매출 1,000억원
MIN_ROE = 5.0
MAX_DEBT_RATIO = 250.0
MIN_REVENUE_GROWTH = -20.0
MIN_MARKET_MATCH_RATIO = 0.90
MIN_FINANCIAL_RECORD_MATCH_RATIO = 0.95
KRW_CURRENCY_LABELS = frozenset({"KRW", "WON", "원"})

STOCK_CODE_PATTERN = re.compile(r"^(?:A)?([0-9A-HJ-NP-TV-Z]{6})$", re.IGNORECASE)
FINANCIAL_KEYWORDS = (
    "금융",
    "은행",
    "뱅크",
    "증권",
    "보험",
    "카드",
    "캐피탈",
    "저축은행",
    "손해보험",
    "생명보험",
)


class ScreenerError(RuntimeError):
    """Input data cannot produce a trustworthy screening result."""


def _alias(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def normalize_stock_code(value: Any) -> str | None:
    match = STOCK_CODE_PATTERN.fullmatch(str(value or "").strip().upper())
    return match.group(1).upper() if match else None


def parse_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "--", "N/A", "n/a", "null"}:
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


def _records(payload: Any, label: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        raw = None
        for key in ("securities", "items", "companies", "universe", "data"):
            if isinstance(payload.get(key), list):
                raw = payload[key]
                break
        if raw is None:
            raise ScreenerError(f"{label} has no supported record array")
    else:
        raise ScreenerError(f"{label} must be a JSON object or array")
    return [record for record in raw if isinstance(record, dict)]


def _index(payload: Any, label: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    order: list[str] = []
    result: dict[str, dict[str, Any]] = {}
    for record in _records(payload, label):
        code = normalize_stock_code(_alias(record, "code", "srtnCd", "stockCode", "stock_code"))
        if code is None:
            continue
        if code in result:
            raise ScreenerError(f"{label} contains duplicate stock code {code}")
        order.append(code)
        result[code] = record
    if not result:
        raise ScreenerError(f"{label} contains no valid six-character stock code")
    return order, result


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def discover_universe_path() -> Path:
    for path in DEFAULT_UNIVERSE_PATHS:
        if path.exists():
            return path
    return DEFAULT_UNIVERSE_PATHS[0]


def _payload_limitations(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    values: list[str] = []
    for key in ("dataLimitations", "limitations"):
        values.extend(_reason_values(payload.get(key)))
    return list(dict.fromkeys(values))


def _basis_date(value: Any) -> str | None:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return text or None


def market_basis_date(payload: Any, market_index: dict[str, dict[str, Any]]) -> str | None:
    if isinstance(payload, dict):
        top_level = _alias(payload, "basisDate", "priceBasisDate", "basDt", "asOf")
        if top_level not in (None, ""):
            return _basis_date(top_level)
    dates = {
        _basis_date(record_alias(record, "basisDate", "priceBasisDate", "basDt", "date"))
        for record in market_index.values()
    }
    usable = sorted(date for date in dates if date)
    return usable[-1] if usable else None


def _truthy_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "yes", "y", "1", "included", "eligible"}:
        return True
    if normalized in {"false", "no", "n", "0", "excluded", "ineligible"}:
        return False
    return None


def _reason_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def upstream_exclusions(universe: dict[str, Any]) -> list[dict[str, str]]:
    exclusion_values: list[str] = []
    for key in (
        "exclusionReasons",
        "exclusionReason",
        "exclusion_reasons",
        "reasons",
        "excludeReasons",
    ):
        exclusion_values.extend(_reason_values(universe.get(key)))
    exclusion = universe.get("exclusion")
    if isinstance(exclusion, dict):
        exclusion_values.extend(_reason_values(exclusion.get("reasons")))

    eligible = _truthy_boolean(
        _alias(
            universe,
            "eligibleForPreliminaryScreen",
            "eligible",
            "included",
            "isEligible",
        )
    )
    status = str(_alias(universe, "screeningStatus", "eligibilityStatus", "status") or "").lower()
    explicitly_excluded = eligible is False or status in {"excluded", "ineligible", "rejected"}
    if not explicitly_excluded and not exclusion_values:
        return []
    if not exclusion_values:
        exclusion_values = ["유니버스 원천에서 제외 대상으로 표시됨"]
    return [
        {"code": "universe_exclusion", "message": message, "source": "universe"}
        for message in dict.fromkeys(exclusion_values)
    ]


def _nested_records(record: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield record
    for key in ("marketData", "market", "quote", "metrics", "financials"):
        value = record.get(key)
        if isinstance(value, dict):
            yield value


def record_alias(record: dict[str, Any], *names: str) -> Any:
    for candidate in _nested_records(record):
        value = _alias(candidate, *names)
        if value not in (None, ""):
            return value
    return None


def metric_values(financial: dict[str, Any] | None, key: str) -> tuple[int | float | None, int | float | None, float | None, str]:
    if not isinstance(financial, dict):
        return None, None, None, "missing"
    metrics = financial.get("metrics")
    metric = metrics.get(key) if isinstance(metrics, dict) else None
    if isinstance(metric, dict):
        current = parse_number(_alias(metric, "current", "value", "currentValue"))
        previous = parse_number(_alias(metric, "previous", "prior", "previousValue"))
        change = parse_number(_alias(metric, "changePct", "growthPct", "yoyPct"))
        status = str(metric.get("status") or ("available" if current is not None else "missing"))
        if change is None and current is not None and previous not in (None, 0):
            change = round((current - previous) / abs(previous) * 100, 2)
        return current, previous, float(change) if change is not None else None, status

    aliases: dict[str, tuple[str, ...]] = {
        "revenue": ("revenue", "revenue2025", "sales"),
        "operatingProfit": ("operatingProfit", "operatingIncome"),
        "netIncome": ("netIncome", "profitLoss", "profit"),
        "assets": ("assets", "totalAssets"),
        "liabilities": ("liabilities", "totalLiabilities"),
        "equity": ("equity", "totalEquity"),
        "operatingCashFlow": ("operatingCashFlow", "cashFlowFromOperations", "cfo"),
        "capex": ("capex", "capitalExpenditures"),
    }
    current = parse_number(record_alias(financial, *aliases.get(key, (key,))))
    previous = parse_number(record_alias(financial, f"previous{key[0].upper()}{key[1:]}", f"prior{key[0].upper()}{key[1:]}"))
    change = None
    if current is not None and previous not in (None, 0):
        change = round((current - previous) / abs(previous) * 100, 2)
    return current, previous, change, "available" if current is not None else "missing"


def safe_ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100, 2)


def is_financial_company(universe: dict[str, Any], market: dict[str, Any] | None) -> bool:
    for record in (universe, market or {}):
        explicit = _truthy_boolean(_alias(record, "isFinancial", "financialCompany"))
        if explicit is not None:
            return explicit
    combined = " ".join(
        str(
            record_alias(
                record,
                "sector",
                "industry",
                "industryName",
                "indutyNm",
                "name",
                "corporationName",
                "corpName",
                "corp_name",
                "itmsNm",
            )
            or ""
        )
        for record in (universe, market or {})
    )
    if any(keyword in combined for keyword in FINANCIAL_KEYWORDS):
        return True
    industry_code = str(record_alias(universe, "industryCode", "indutyCode", "induty_code") or "")
    digits = "".join(character for character in industry_code if character.isdigit())
    return len(digits) >= 2 and digits[:2] in {"64", "65", "66"}


def market_metrics(market: dict[str, Any] | None) -> dict[str, int | float | str | None]:
    if not isinstance(market, dict):
        return {
            "price": None,
            "marketCap": None,
            "volume": None,
            "liquidityValue": None,
            "liquidityBasis": None,
            "basisDate": None,
        }
    price = parse_number(record_alias(market, "currentPrice", "closePrice", "clpr", "price"))
    market_cap = parse_number(record_alias(market, "marketCap", "mrktTotAmt", "marketCapitalization"))
    volume = parse_number(
        record_alias(
            market,
            "averageVolume20d",
            "avgVolume20d",
            "tradingVolume",
            "volume",
            "accTrdvol",
        )
    )
    liquidity = parse_number(
        record_alias(
            market,
            "averageTradingValue20d",
            "avgTradingValue20d",
            "tradingValue",
            "tradeValue",
            "accTrdval",
            "trqu",
        )
    )
    liquidity_basis = "provider_trading_value" if liquidity is not None else None
    if liquidity is None and price is not None and volume is not None:
        liquidity = price * volume
        liquidity_basis = "price_times_volume"
    basis = record_alias(market, "basisDate", "priceBasisDate", "basDt", "date")
    return {
        "price": price,
        "marketCap": market_cap,
        "volume": volume,
        "liquidityValue": liquidity,
        "liquidityBasis": liquidity_basis,
        "basisDate": str(basis).strip() if basis not in (None, "") else None,
    }


def _gate_reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message, "source": "quant_gate"}


def report_period(financial: dict[str, Any] | None) -> str | None:
    if not isinstance(financial, dict):
        return None
    year = parse_number(financial.get("reportYear"))
    code = str(financial.get("reportCode") or "").strip()
    period_ends = {
        "11013": "03-31",
        "11012": "06-30",
        "11014": "09-30",
        "11011": "12-31",
    }
    if isinstance(year, int) and code in period_ends:
        return f"{year:04d}-{period_ends[code]}"
    supplied = _alias(financial, "periodEnd", "reportPeriod", "basisDate")
    return str(supplied).strip() if supplied not in (None, "") else None


def financial_currencies(financial: dict[str, Any] | None) -> list[str]:
    if not isinstance(financial, dict) or not isinstance(financial.get("metrics"), dict):
        return []
    return sorted(
        {
            str(metric.get("currency")).strip().upper()
            for metric in financial["metrics"].values()
            if isinstance(metric, dict) and metric.get("currency")
        }
    )


def _log_score(value: int | float | None, low: float, high: float, points: float) -> float:
    if value is None or value <= 0:
        return 0.0
    if high <= low:
        return 0.0
    position = (math.log10(value) - math.log10(low)) / (math.log10(high) - math.log10(low))
    return round(max(0.0, min(1.0, position)) * points, 2)


def _linear_score(value: float | None, low: float, high: float, points: float, *, neutral: float = 0.5) -> float:
    if value is None:
        return round(points * neutral, 2)
    position = (value - low) / (high - low)
    return round(max(0.0, min(1.0, position)) * points, 2)


def _inverse_score(value: float | None, best: float, worst: float, points: float, *, neutral: float = 0.5) -> float:
    if value is None:
        return round(points * neutral, 2)
    position = (worst - value) / (worst - best)
    return round(max(0.0, min(1.0, position)) * points, 2)


def evaluate_company(
    code: str,
    universe: dict[str, Any],
    market: dict[str, Any] | None,
    financial: dict[str, Any] | None,
) -> dict[str, Any]:
    reasons = upstream_exclusions(universe)
    financial_type = "financial" if is_financial_company(universe, market) else "non_financial"
    market_values = market_metrics(market)

    revenue, _, revenue_growth, revenue_status = metric_values(financial, "revenue")
    operating_profit, _, operating_growth, operating_status = metric_values(financial, "operatingProfit")
    net_income, _, net_income_growth, net_income_status = metric_values(financial, "netIncome")
    assets, previous_assets, _, _ = metric_values(financial, "assets")
    liabilities, _, _, _ = metric_values(financial, "liabilities")
    equity, previous_equity, _, _ = metric_values(financial, "equity")
    operating_cash_flow, _, cfo_growth, cfo_status = metric_values(financial, "operatingCashFlow")
    capex, _, _, capex_status = metric_values(financial, "capex")
    currencies = financial_currencies(financial)
    unsupported_currencies = [
        currency for currency in currencies if currency not in KRW_CURRENCY_LABELS
    ]

    average_equity = None
    if equity is not None and equity > 0:
        average_equity = (
            (equity + previous_equity) / 2
            if previous_equity is not None and previous_equity > 0
            else equity
        )
    roe = safe_ratio(net_income, average_equity)
    operating_margin = safe_ratio(operating_profit, revenue)
    debt_ratio = safe_ratio(liabilities, equity if equity is not None and equity > 0 else None)

    if operating_cash_flow is not None and capex is not None:
        # CapEx can be stored either as a positive spend or a negative cash-flow row.
        capex_spend = abs(capex)
        fcf_value: int | float | None = operating_cash_flow - capex_spend
        fcf_status = "available"
    else:
        fcf_value = None
        fcf_status = "pending_capex_not_collected" if capex is None else "pending_operating_cash_flow"

    if market is None:
        reasons.append(_gate_reason("missing_market_record", "시장 데이터가 없습니다."))
    if financial is None:
        reasons.append(_gate_reason("missing_financial_record", "OpenDART 재무 데이터가 없습니다."))
    elif str(financial.get("status", "")) in {"missing_corp_code", "no_report_rows"}:
        report_year = parse_number(financial.get("reportYear"))
        report_label = (
            f"{report_year} 사업보고서" if isinstance(report_year, int) else "선택된 사업보고서"
        )
        reasons.append(
            _gate_reason(
                str(financial.get("status")),
                f"{report_label} 주요계정을 확보하지 못했습니다.",
            )
        )
    if unsupported_currencies:
        reasons.append(
            _gate_reason(
                "currency_conversion_pending",
                "원화가 아닌 재무제표 통화는 환산 전 정량 비교에서 제외합니다: "
                + ", ".join(unsupported_currencies),
            )
        )

    market_cap = market_values["marketCap"]
    liquidity = market_values["liquidityValue"]
    if market_cap is None:
        reasons.append(_gate_reason("missing_market_cap", "시가총액이 없어 규모 게이트를 계산할 수 없습니다."))
    elif market_cap < MIN_MARKET_CAP:
        reasons.append(_gate_reason("market_cap_below_minimum", "시가총액이 1,000억원 미만입니다."))
    if liquidity is None:
        reasons.append(_gate_reason("missing_liquidity", "거래량·거래대금이 없어 유동성을 계산할 수 없습니다."))
    elif liquidity < MIN_LIQUIDITY_VALUE:
        reasons.append(_gate_reason("liquidity_below_minimum", "거래대금 환산값이 1억원 미만입니다."))

    if net_income is None:
        reasons.append(_gate_reason("missing_net_income", "당기순이익을 확인할 수 없습니다."))
    elif net_income <= 0:
        reasons.append(_gate_reason("non_positive_net_income", "당기순이익이 0 이하입니다."))
    if roe is None:
        reasons.append(_gate_reason("missing_roe", "순이익과 평균자본으로 ROE를 계산할 수 없습니다."))
    elif roe < MIN_ROE:
        reasons.append(_gate_reason("roe_below_minimum", "ROE가 5% 미만입니다."))

    if financial_type == "non_financial":
        if revenue is None:
            reasons.append(_gate_reason("missing_revenue", "비금융사의 매출액을 확인할 수 없습니다."))
        elif revenue < MIN_REVENUE:
            reasons.append(_gate_reason("revenue_below_minimum", "연매출이 1,000억원 미만입니다."))
        if operating_profit is None:
            reasons.append(_gate_reason("missing_operating_profit", "비금융사의 영업이익을 확인할 수 없습니다."))
        elif operating_profit <= 0:
            reasons.append(_gate_reason("non_positive_operating_profit", "영업이익이 0 이하입니다."))
        if revenue_growth is None:
            reasons.append(_gate_reason("missing_revenue_growth", "전기 대비 매출 성장률을 계산할 수 없습니다."))
        elif revenue_growth < MIN_REVENUE_GROWTH:
            reasons.append(_gate_reason("revenue_contraction", "매출이 전기 대비 20% 넘게 감소했습니다."))
        if debt_ratio is None:
            reasons.append(_gate_reason("missing_debt_ratio", "부채총계와 자본총계로 부채비율을 계산할 수 없습니다."))
        elif debt_ratio > MAX_DEBT_RATIO:
            reasons.append(_gate_reason("debt_ratio_above_maximum", "부채비율이 250%를 초과했습니다."))

    # Cash generation is evaluated when available. The batch endpoint normally
    # leaves it pending, so absence is disclosed but does not silently reject
    # the whole market before the full-statement follow-up.
    if operating_cash_flow is not None and operating_cash_flow <= 0:
        reasons.append(_gate_reason("non_positive_operating_cash_flow", "영업현금흐름이 0 이하입니다."))
    if fcf_value is not None and fcf_value <= 0:
        reasons.append(_gate_reason("non_positive_fcf", "정의된 FCF가 0 이하입니다."))

    if financial_type == "financial":
        operating_margin_score = 7.5
        revenue_growth_score = 7.5
        growth_metric = net_income_growth
        balance_score = 10.0
    else:
        operating_margin_score = _linear_score(operating_margin, 0, 25, 15)
        revenue_growth_score = _linear_score(revenue_growth, -20, 30, 15)
        growth_metric = operating_growth
        balance_score = _inverse_score(debt_ratio, 0, MAX_DEBT_RATIO, 20)
    cash_score = 5.0 if operating_cash_flow is None else (10.0 if operating_cash_flow > 0 else 0.0)
    score_components = {
        "roe": _linear_score(roe, 0, 20, 20),
        "operatingMargin": operating_margin_score,
        "revenueGrowth": revenue_growth_score,
        "earningsGrowth": _linear_score(growth_metric, -20, 40, 10),
        "balanceSheet": balance_score,
        "cashGeneration": cash_score,
        "marketScale": _log_score(market_cap, MIN_MARKET_CAP, 10_000_000_000_000, 7),
        "liquidity": _log_score(liquidity, MIN_LIQUIDITY_VALUE, 10_000_000_000, 3),
    }
    quant_score = round(sum(score_components.values()), 2)

    name = record_alias(universe, "name", "itmsNm", "stockName", "corpName", "corp_name")
    if name in (None, "") and isinstance(market, dict):
        name = record_alias(market, "name", "itmsNm", "stockName", "corpName", "corp_name")
    market_name = record_alias(universe, "market", "mrktCtg", "marketName", "corpCls", "corp_cls")
    if market_name in (None, "") and isinstance(market, dict):
        market_name = record_alias(market, "market", "mrktCtg", "marketName", "corpCls", "corp_cls")
    sector = record_alias(universe, "sector", "industry", "industryName", "indutyNm")

    pending_checks = ["cavm_qualitative_review"]
    if operating_cash_flow is None:
        pending_checks.append("operating_cash_flow_full_statement_fetch")
    if fcf_status != "available":
        pending_checks.append(fcf_status)
    return {
        "code": code,
        "name": str(name).strip() if name not in (None, "") else None,
        "market": str(market_name).strip() if market_name not in (None, "") else None,
        "sector": str(sector).strip() if sector not in (None, "") else None,
        "companyType": financial_type,
        "quantScore": quant_score,
        "scoreComponents": score_components,
        "cavm": None,
        "cavmStatus": "not_reviewed",
        "reportPeriod": report_period(financial),
        "metrics": {
            **market_values,
            "revenue": revenue,
            "revenueGrowthPct": revenue_growth,
            "operatingProfit": operating_profit,
            "operatingProfitGrowthPct": operating_growth,
            "operatingMarginPct": operating_margin,
            "netIncome": net_income,
            "netIncomeGrowthPct": net_income_growth,
            "assets": assets,
            "previousAssets": previous_assets,
            "liabilities": liabilities,
            "equity": equity,
            "previousEquity": previous_equity,
            "roePct": roe,
            "debtRatioPct": debt_ratio,
            "operatingCashFlow": operating_cash_flow,
            "operatingCashFlowGrowthPct": cfo_growth,
            "operatingCashFlowStatus": cfo_status,
            "capex": capex,
            "capexStatus": capex_status,
            "fcf": fcf_value,
            "fcfStatus": fcf_status,
            "currencies": currencies,
            "revenueStatus": revenue_status,
            "operatingProfitStatus": operating_status,
            "netIncomeStatus": net_income_status,
        },
        "pendingChecks": pending_checks,
        "rejectionReasons": reasons,
    }


def methodology() -> dict[str, Any]:
    return {
        "version": "quant-pre-screen-v1",
        "purpose": "코스피·코스닥 정량 후보 압축; CAVM 정성평가를 대체하지 않음",
        "candidateLimit": MAX_CANDIDATES,
        "gates": [
            {"id": "market_cap", "appliesTo": "all", "rule": f">= {MIN_MARKET_CAP} KRW"},
            {"id": "liquidity", "appliesTo": "all", "rule": f">= {MIN_LIQUIDITY_VALUE} KRW"},
            {"id": "net_income", "appliesTo": "all", "rule": "> 0"},
            {"id": "roe", "appliesTo": "all", "rule": f">= {MIN_ROE}%"},
            {"id": "revenue", "appliesTo": "non_financial", "rule": f">= {MIN_REVENUE} KRW"},
            {"id": "operating_profit", "appliesTo": "non_financial", "rule": "> 0"},
            {"id": "revenue_growth", "appliesTo": "non_financial", "rule": f">= {MIN_REVENUE_GROWTH}%"},
            {"id": "debt_ratio", "appliesTo": "non_financial", "rule": f"<= {MAX_DEBT_RATIO}%"},
            {"id": "operating_cash_flow", "appliesTo": "when_available", "rule": "> 0"},
            {"id": "fcf", "appliesTo": "when_capex_and_cfo_available", "rule": "> 0"},
        ],
        "financialSectorPolicy": "일반기업 부채비율·매출·영업이익 게이트 제외; 순이익·ROE·시장 게이트 유지",
        "fcfFormula": "operatingCashFlow - abs(capex)",
        "missingCashFlowPolicy": "CapEx 또는 CFO 미수집 시 pending으로 공개하고 이 단계의 탈락 게이트로 쓰지 않음",
        "score": {
            "maximum": 100,
            "weights": {
                "roe": 20,
                "operatingMargin": 15,
                "revenueGrowth": 15,
                "earningsGrowth": 10,
                "balanceSheet": 20,
                "cashGeneration": 10,
                "marketScale": 7,
                "liquidity": 3,
            },
            "financialNeutralScores": {
                "operatingMargin": 7.5,
                "revenueGrowth": 7.5,
                "balanceSheet": 10,
            },
        },
    }


def build_screener(
    universe_payload: Any,
    market_payload: Any,
    financial_payload: Any,
    *,
    generated_at: datetime | None = None,
    candidate_limit: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive")
    universe_order, universe = _index(universe_payload, "universe")
    _, market = _index(market_payload, "market data")
    _, financials = _index(financial_payload, "financial data")
    market_match_count = sum(code in market for code in universe)
    financial_match_count = sum(code in financials for code in universe)
    universe_date = (
        _basis_date(_alias(universe_payload, "basisDate", "basDt", "asOf"))
        if isinstance(universe_payload, dict)
        else None
    )
    selected_market_date = market_basis_date(market_payload, market)
    if universe_date and selected_market_date and universe_date != selected_market_date:
        raise ScreenerError(
            "universe and market snapshots use different basis dates "
            f"({universe_date} != {selected_market_date})"
        )
    if market_match_count / len(universe) < MIN_MARKET_MATCH_RATIO:
        raise ScreenerError(
            "market/universe code coverage was below the safety threshold "
            f"({market_match_count}/{len(universe)})"
        )
    if financial_match_count / len(universe) < MIN_FINANCIAL_RECORD_MATCH_RATIO:
        raise ScreenerError(
            "financial/universe record coverage was below the safety threshold "
            f"({financial_match_count}/{len(universe)})"
        )

    evaluated = [
        evaluate_company(code, universe[code], market.get(code), financials.get(code))
        for code in universe_order
    ]
    passing = [company for company in evaluated if not company["rejectionReasons"]]
    passing.sort(key=lambda item: (-item["quantScore"], item["code"]))
    candidates = passing[:candidate_limit]
    outside = passing[candidate_limit:]
    for company in outside:
        company["rejectionReasons"].append(
            _gate_reason(
                "outside_top_quant_cutoff",
                f"정량 게이트를 통과했지만 상위 {candidate_limit}위 밖입니다.",
            )
        )

    rejected = [company for company in evaluated if company["rejectionReasons"]]
    rejected.sort(key=lambda item: (-item["quantScore"], item["code"]))
    for rank, company in enumerate(candidates, start=1):
        company["quantRank"] = rank
        company.pop("rejectionReasons", None)

    def add_public_aliases(company: dict[str, Any], *, excluded: bool) -> None:
        metrics = company["metrics"]
        reason_objects = company.get("rejectionReasons", [])
        reason_messages = [str(reason.get("message", "")) for reason in reason_objects]
        company["screenStatus"] = "excluded_quant" if excluded else "review_pending"
        company["status"] = company["screenStatus"]
        company["quantPassed"] = not excluded
        company["reviewPending"] = not excluded
        company["screenScore"] = company["quantScore"]
        company["currentPrice"] = metrics.get("price")
        market_cap = metrics.get("marketCap")
        company["marketCapEok"] = (
            round(market_cap / 100_000_000, 2) if isinstance(market_cap, (int, float)) else None
        )
        company["roe"] = metrics.get("roePct")
        company["operatingMargin"] = metrics.get("operatingMarginPct")
        company["debtRatio"] = metrics.get("debtRatioPct")
        company["exclusionReasons"] = reason_messages
        company["exclusionReason"] = " · ".join(reason_messages) if reason_messages else None

    for company in candidates:
        add_public_aliases(company, excluded=False)
    for company in rejected:
        add_public_aliases(company, excluded=True)
    all_companies = sorted(candidates + rejected, key=lambda item: item["code"])

    selected_at = generated_at or datetime.now(KST)
    if selected_at.tzinfo is None:
        selected_at = selected_at.replace(tzinfo=KST)
    universe_limitations = _payload_limitations(universe_payload)
    universe_limitations.extend(
        limitation
        for limitation in (
            "전체시장 재무는 최근 널리 이용 가능한 사업보고서 주요계정 기준이며 최신 분기 수치가 아닙니다.",
            "OpenDART 다중회사 주요계정 API는 CapEx를 제공하지 않아 CFO·FCF 검증은 정성 검토 후보의 2차 전체재무제표 수집 전까지 보류됩니다.",
            "원화가 아닌 공시 통화는 환율 환산 전 정량 후보에서 제외합니다.",
            "금융업 구분은 업종코드가 없는 경우 기업명 키워드로 추정하므로 정성 검토에서 재확인해야 합니다.",
        )
        if limitation not in universe_limitations
    )
    managed_item_checked = False
    if isinstance(universe_payload, dict):
        supplied_managed_check = _truthy_boolean(
            _alias(universe_payload, "managedItemChecked", "managed_item_checked")
        )
        managed_item_checked = supplied_managed_check is True
    market_date = selected_market_date
    return {
        "schemaVersion": 1,
        "generatedAt": selected_at.astimezone(KST).isoformat(timespec="seconds"),
        "basisDate": market_date,
        "marketBasisDate": market_date,
        "managedItemChecked": managed_item_checked,
        "dataLimitations": universe_limitations,
        "methodology": methodology(),
        "summary": {
            "total": len(universe),
            "excluded": len(rejected),
            "passed": len(candidates),
            "pending": len(candidates),
            "universeCount": len(universe),
            "marketMatchedCount": market_match_count,
            "financialMatchedCount": financial_match_count,
            "candidateCount": len(candidates),
            "rejectedCount": len(rejected),
            "financialCandidateCount": sum(item["companyType"] == "financial" for item in candidates),
            "cavmReviewedCount": 0,
        },
        "companies": all_companies,
        "candidates": candidates,
        "rejected": rejected,
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


def compact_public_output(value: dict[str, Any]) -> dict[str, Any]:
    """Remove duplicated verbose arrays while preserving every company record."""

    compact = dict(value)
    compact["candidateCodes"] = [
        company["code"] for company in value.get("candidates", [])
    ]
    compact.pop("candidates", None)
    compact.pop("rejected", None)
    return compact


def build_file(
    *,
    universe_path: Path | None = None,
    market_path: Path = MARKET_PATH,
    financial_path: Path = FINANCIAL_PATH,
    output_path: Path = OUTPUT_PATH,
    generated_at: datetime | None = None,
    candidate_limit: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    selected_universe = universe_path or discover_universe_path()
    output = build_screener(
        load_json(selected_universe),
        load_json(market_path),
        load_json(financial_path),
        generated_at=generated_at,
        candidate_limit=candidate_limit,
    )
    atomic_write(compact_public_output(output), output_path)
    return output["summary"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--market", type=Path, default=MARKET_PATH)
    parser.add_argument("--financials", type=Path, default=FINANCIAL_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--limit", type=int, default=MAX_CANDIDATES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = build_file(
            universe_path=arguments.universe,
            market_path=arguments.market,
            financial_path=arguments.financials,
            output_path=arguments.output,
            candidate_limit=arguments.limit,
        )
    except (ScreenerError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"screener build failed: {error}; prior output preserved", file=sys.stderr)
        return 1
    print(
        "screener atomically wrote "
        f"{summary['candidateCount']} candidates and {summary['rejectedCount']} rejections"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
