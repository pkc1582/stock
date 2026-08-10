#!/usr/bin/env python3
"""Build the public CAVM/VM snapshot from reviewed and human-editable inputs.

Only derived values are written to ``public/data/latest.json``. API keys and raw
provider responses are never read or written by this module.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COMPANIES_PATH = ROOT / "data" / "companies.json"
OVERRIDES_PATH = ROOT / "data" / "manual-overrides.json"
ISSUES_PATH = ROOT / "data" / "issues.json"
OUTPUT_PATH = ROOT / "public" / "data" / "latest.json"

COMPONENT_LIMITS = {
    "moat": 30,
    "growth": 25,
    "profitability": 20,
    "financialHealth": 15,
    "management": 10,
}
DEFAULT_OVERSEAS_ADJUSTMENT_WEIGHT = 0.30
DEFAULT_ROUNDING_UNIT = 100
VALUATION_MODEL_LABELS = {
    "standard_per": "일반기업 EPS·PER",
    "memory_normalized": "메모리 정상화 EPS·PER",
    "bank_pbr": "은행·금융지주 BPS·PBR",
    "financial_hybrid": "금융 하이브리드 PBR·PER",
    "insurance_sotp": "보험 PBR·CSM·SOTP",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def require_number(value: Any, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return number


def component_scores(company: dict[str, Any], override: dict[str, Any]) -> dict[str, int]:
    base = company.get("components")
    if not isinstance(base, dict):
        raise ValueError(f"{company.get('code')}: components must be an object")

    manual_components = override.get("componentOverrides") or {}
    if not isinstance(manual_components, dict):
        raise ValueError(f"{company.get('code')}: componentOverrides must be an object")

    result: dict[str, int] = {}
    for key, maximum in COMPONENT_LIMITS.items():
        raw = manual_components.get(key, base.get(key))
        score = require_number(raw, f"{company.get('code')}.{key}", 0)
        if score > maximum:
            raise ValueError(f"{company.get('code')}.{key} exceeds {maximum}")
        if not score.is_integer():
            raise ValueError(f"{company.get('code')}.{key} must be an integer")
        result[key] = int(score)

    issue_overrides = override.get("issueOverrides") or {}
    if not isinstance(issue_overrides, dict):
        raise ValueError(f"{company.get('code')}: issueOverrides must be an object")
    for issue_id, adjustment in issue_overrides.items():
        if not isinstance(adjustment, dict):
            raise ValueError(f"{company.get('code')}.{issue_id}: override must be an object")
        component = adjustment.get("component")
        delta = adjustment.get("delta", 0)
        if component is None and delta == 0:
            continue
        if component not in COMPONENT_LIMITS:
            raise ValueError(f"{company.get('code')}.{issue_id}: unknown component")
        numeric_delta = require_number(delta, f"{company.get('code')}.{issue_id}.delta")
        adjusted = result[component] + numeric_delta
        if adjusted < 0 or adjusted > COMPONENT_LIMITS[component]:
            raise ValueError(f"{company.get('code')}.{issue_id}: adjusted score is out of range")
        if not adjusted.is_integer():
            raise ValueError(f"{company.get('code')}.{issue_id}: adjusted score must be an integer")
        result[component] = int(adjusted)
    return result


def rounded_won(value: float, unit: int = DEFAULT_ROUNDING_UNIT) -> int:
    """Round won values to a review-friendly unit without banker's rounding."""
    if unit <= 0:
        raise ValueError("rounding unit must be positive")
    return int(math.floor(value / float(unit) + 0.5) * unit)


def rounded_hundred(value: float) -> int:
    """Backward-compatible KRW 100 rounding used by the standard VM."""
    return rounded_won(value, 100)


def adjusted_per(
    historical_per_5y: float,
    overseas_peer_per: float,
    adjustment_weight: float = DEFAULT_OVERSEAS_ADJUSTMENT_WEIGHT,
) -> tuple[float, float]:
    """Use five-year PER as the base and apply part of the overseas gap."""
    correction_per = round((overseas_peer_per - historical_per_5y) * adjustment_weight, 2)
    return correction_per, round(historical_per_5y + correction_per, 2)


def final_vm_for(
    forward_eps_3y: float,
    historical_per_5y: float,
    overseas_peer_per: float,
    discount_rate: float,
    adjustment_weight: float = DEFAULT_OVERSEAS_ADJUSTMENT_WEIGHT,
) -> tuple[float, float, int]:
    """Return the applied PER and discounted Final VM."""
    correction_per, applied_per = adjusted_per(
        historical_per_5y,
        overseas_peer_per,
        adjustment_weight,
    )
    three_year_value = forward_eps_3y * applied_per
    final_vm = rounded_hundred(three_year_value / ((1 + discount_rate / 100) ** 3))
    return correction_per, applied_per, final_vm


def normalized_memory_vm_for(
    normalized_eps: float,
    normalized_per: float,
    discount_rate: float,
    horizon_years: int = 2,
    rounding_unit: int = 1_000,
) -> tuple[int, int]:
    """Value a cyclical memory company from mid-cycle EPS, not peak EPS."""
    future_value = normalized_eps * normalized_per
    final_vm = rounded_won(
        future_value / ((1 + discount_rate / 100) ** horizon_years),
        rounding_unit,
    )
    return rounded_won(future_value, rounding_unit), final_vm


def bank_pbr_vm_for(
    normalized_bps: float,
    target_pbr: float,
    normalized_eps: float,
    cross_check_per: float,
    rounding_unit: int = 100,
) -> tuple[int, int, int]:
    """Use BPS×PBR as the bank primary value and EPS×PER only as a check."""
    primary_vm = rounded_won(normalized_bps * target_pbr, rounding_unit)
    cross_check_vm = rounded_won(normalized_eps * cross_check_per, rounding_unit)
    return primary_vm, cross_check_vm, primary_vm


def financial_hybrid_vm_for(
    normalized_bps: float,
    target_pbr: float,
    normalized_eps: float,
    cross_check_per: float,
    primary_weight: float = 0.60,
    rounding_unit: int = 100,
) -> tuple[int, int, int]:
    """Blend PBR and PER for securities-led or mixed financial groups."""
    if primary_weight < 0 or primary_weight > 1:
        raise ValueError("primaryWeight must be between 0 and 1")
    primary_vm = rounded_won(normalized_bps * target_pbr, rounding_unit)
    cross_check_vm = rounded_won(normalized_eps * cross_check_per, rounding_unit)
    final_vm = rounded_won(
        primary_vm * primary_weight + cross_check_vm * (1 - primary_weight),
        rounding_unit,
    )
    return primary_vm, cross_check_vm, final_vm


def insurance_sotp_vm_for(
    normalized_bps: float,
    target_pbr: float,
    csm_sotp_adjustment: float,
    normalized_eps: float,
    cross_check_per: float,
    rounding_unit: int = 100,
) -> tuple[int, int, int]:
    """Value insurers with PBR plus a separately reviewable CSM/SOTP adjustment."""
    primary_vm = rounded_won(normalized_bps * target_pbr + csm_sotp_adjustment, rounding_unit)
    cross_check_vm = rounded_won(normalized_eps * cross_check_per, rounding_unit)
    return primary_vm, cross_check_vm, primary_vm


def rating_for(cavm: int, gap_rate: float | None) -> tuple[str, str]:
    if cavm < 70:
        return "★☆☆☆☆", "투자 제외"
    if cavm < 80:
        return "★★☆☆☆", "후보"
    if gap_rate is None:
        return "★★★☆☆", "VM 검토 필요"
    if gap_rate <= -20:
        return "★★★★★", "적극 매수"
    if gap_rate <= -10:
        return "★★★★☆", "분할매수"
    return "★★★☆☆", "관찰"


def data_status_for(companies_data: dict[str, Any]) -> str:
    """Describe what was actually refreshed without overstating provenance."""
    market_status = str(companies_data.get("marketDataStatus", ""))
    financial_status = str(companies_data.get("financialDataStatus", ""))

    if market_status == "updated_from_data_go_kr":
        market_label = "공공데이터포털 시세 API 갱신"
    elif market_status == "partially_updated_from_data_go_kr":
        market_label = "공공데이터포털 시세 API 부분 갱신"
    else:
        market_label = "2026-08-07 정규장 종가 교차검증"

    if financial_status == "updated_from_opendart_latest_reports":
        financial_label = "OpenDART 기업별 최신 보고서 확인 완료"
    elif financial_status == "partially_updated_from_opendart_latest_reports":
        summary = companies_data.get("financialDataSummary", {})
        success_count = summary.get("latestReportSuccessCount") if isinstance(summary, dict) else None
        complete_count = summary.get("latestReportCompleteCount") if isinstance(summary, dict) else None
        company_count = summary.get("companyCount") if isinstance(summary, dict) else None
        if success_count is not None and company_count:
            financial_label = f"OpenDART 최신 보고서 확인 {success_count}/{company_count}"
            if complete_count is not None and complete_count != company_count:
                financial_label += f" · 주요지표 완전 {complete_count}/{company_count}"
        else:
            financial_label = "OpenDART 기업별 최신 보고서 부분 확인"
    elif financial_status in {"updated_from_opendart_2025_annual", "verified_from_opendart_2025_annual"}:
        financial_label = "OpenDART 2025 사업보고서 갱신"
    else:
        financial_label = "2025 연간 재무 스냅샷 교차검증"

    return f"{financial_label} · {market_label} / VM 수동가정 초안"


def public_issues(
    company_code: str,
    all_issues: list[dict[str, Any]],
    issue_overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    allowed = ("id", "title", "category", "priority", "status", "impact", "openedAt", "nextReviewAt")
    for issue in all_issues:
        if issue.get("companyCode") != company_code:
            continue
        item = {key: issue[key] for key in allowed if key in issue}
        manual = issue_overrides.get(issue.get("id"), {})
        if isinstance(manual, dict):
            for key in ("title", "priority", "status", "impact", "nextReviewAt"):
                if key in manual:
                    item[key] = manual[key]
            if manual.get("delta", 0):
                item["scoreImpact"] = {
                    "component": manual.get("component"),
                    "delta": manual.get("delta"),
                    "note": manual.get("note", ""),
                }
        selected.append(item)
    selected.sort(key=lambda item: (item.get("priority") != "high", item.get("nextReviewAt", ""), item.get("id", "")))
    return selected


def valuation_for(
    code: str,
    assumption: dict[str, Any],
    overseas_adjustment_weight: float,
) -> dict[str, Any]:
    """Build one reviewed VM payload using the sector-specific model."""
    model = str(assumption.get("valuationModel", "standard_per"))
    if model not in VALUATION_MODEL_LABELS:
        raise ValueError(f"{code}.valuationModel is not supported: {model}")

    rounding_unit_value = require_number(
        assumption.get("roundingUnit", DEFAULT_ROUNDING_UNIT),
        f"{code}.roundingUnit",
        1,
    )
    if not rounding_unit_value.is_integer():
        raise ValueError(f"{code}.roundingUnit must be an integer")
    rounding_unit = int(rounding_unit_value)

    if model == "standard_per":
        forward_eps = require_number(assumption.get("forwardEps3y"), f"{code}.forwardEps3y", 0)
        historical_per_5y = require_number(
            assumption.get("historicalPer5y", assumption.get("peerPer")),
            f"{code}.historicalPer5y",
            0,
        )
        overseas_peer_per = require_number(
            assumption.get("overseasPeerPer", assumption.get("peerPer")),
            f"{code}.overseasPeerPer",
            0,
        )
        discount_rate = require_number(assumption.get("discountRate"), f"{code}.discountRate", 0)
        if discount_rate >= 100:
            raise ValueError(f"{code}.discountRate must be below 100")
        correction_per, applied_per, final_vm = final_vm_for(
            forward_eps,
            historical_per_5y,
            overseas_peer_per,
            discount_rate,
            overseas_adjustment_weight,
        )
        return {
            "valuationModel": model,
            "valuationModelLabel": VALUATION_MODEL_LABELS[model],
            "valuationHorizonYears": 3,
            "forwardEps3y": int(forward_eps) if forward_eps.is_integer() else forward_eps,
            "historicalPer5y": int(historical_per_5y) if historical_per_5y.is_integer() else historical_per_5y,
            "overseasPeerPer": int(overseas_peer_per) if overseas_peer_per.is_integer() else overseas_peer_per,
            "overseasCorrectionPer": int(correction_per) if correction_per.is_integer() else correction_per,
            "overseasAdjustmentWeightPct": round(overseas_adjustment_weight * 100, 1),
            "appliedPer": int(applied_per) if applied_per.is_integer() else applied_per,
            "discountRate": int(discount_rate) if discount_rate.is_integer() else discount_rate,
            "primaryVm": final_vm,
            "crossCheckVm": None,
            "finalVm": final_vm,
        }

    normalized_eps = require_number(assumption.get("normalizedEps"), f"{code}.normalizedEps", 0)
    normalized_per = require_number(
        assumption.get("normalizedPer", assumption.get("crossCheckPer")),
        f"{code}.normalizedPer",
        0,
    )

    if model == "memory_normalized":
        discount_rate = require_number(assumption.get("discountRate"), f"{code}.discountRate", 0)
        horizon_value = require_number(assumption.get("valuationHorizonYears", 2), f"{code}.valuationHorizonYears", 1)
        if not horizon_value.is_integer():
            raise ValueError(f"{code}.valuationHorizonYears must be an integer")
        horizon_years = int(horizon_value)
        future_value, final_vm = normalized_memory_vm_for(
            normalized_eps,
            normalized_per,
            discount_rate,
            horizon_years,
            rounding_unit,
        )
        return {
            "valuationModel": model,
            "valuationModelLabel": VALUATION_MODEL_LABELS[model],
            "valuationHorizonYears": horizon_years,
            "forwardEps3y": int(normalized_eps) if normalized_eps.is_integer() else normalized_eps,
            "normalizedEps": int(normalized_eps) if normalized_eps.is_integer() else normalized_eps,
            "normalizedPer": int(normalized_per) if normalized_per.is_integer() else normalized_per,
            "appliedPer": int(normalized_per) if normalized_per.is_integer() else normalized_per,
            "discountRate": int(discount_rate) if discount_rate.is_integer() else discount_rate,
            "primaryVm": future_value,
            "crossCheckVm": None,
            "finalVm": final_vm,
            "cycleChecks": ["DRAM 계약가격", "HBM 가격·출하량", "재고", "CAPEX", "고객사 재고"],
        }

    normalized_bps = require_number(assumption.get("normalizedBps"), f"{code}.normalizedBps", 0)
    target_pbr = require_number(assumption.get("targetPbr"), f"{code}.targetPbr", 0)
    common = {
        "valuationModel": model,
        "valuationModelLabel": VALUATION_MODEL_LABELS[model],
        "valuationHorizonYears": 0,
        "forwardEps3y": int(normalized_eps) if normalized_eps.is_integer() else normalized_eps,
        "normalizedEps": int(normalized_eps) if normalized_eps.is_integer() else normalized_eps,
        "normalizedPer": int(normalized_per) if normalized_per.is_integer() else normalized_per,
        "normalizedBps": int(normalized_bps) if normalized_bps.is_integer() else normalized_bps,
        "targetPbr": int(target_pbr) if target_pbr.is_integer() else target_pbr,
        "appliedPer": int(normalized_per) if normalized_per.is_integer() else normalized_per,
        "discountRate": 0,
    }
    if model == "bank_pbr":
        primary_vm, cross_check_vm, final_vm = bank_pbr_vm_for(
            normalized_bps,
            target_pbr,
            normalized_eps,
            normalized_per,
            rounding_unit,
        )
    elif model == "financial_hybrid":
        primary_weight = require_number(assumption.get("primaryWeight", 0.60), f"{code}.primaryWeight", 0)
        primary_vm, cross_check_vm, final_vm = financial_hybrid_vm_for(
            normalized_bps,
            target_pbr,
            normalized_eps,
            normalized_per,
            primary_weight,
            rounding_unit,
        )
        common["primaryWeightPct"] = round(primary_weight * 100, 1)
    else:
        csm_sotp_adjustment = require_number(
            assumption.get("csmSotpAdjustment", 0),
            f"{code}.csmSotpAdjustment",
        )
        primary_vm, cross_check_vm, final_vm = insurance_sotp_vm_for(
            normalized_bps,
            target_pbr,
            csm_sotp_adjustment,
            normalized_eps,
            normalized_per,
            rounding_unit,
        )
        common["csmSotpAdjustment"] = int(csm_sotp_adjustment) if csm_sotp_adjustment.is_integer() else csm_sotp_adjustment

    common.update({
        "primaryVm": primary_vm,
        "crossCheckVm": cross_check_vm,
        "finalVm": final_vm,
    })
    return common


def main() -> None:
    companies_data = load_json(COMPANIES_PATH)
    overrides_data = load_json(OVERRIDES_PATH)
    issues_data = load_json(ISSUES_PATH)

    companies = companies_data.get("companies")
    overrides = overrides_data.get("companies")
    issues = issues_data.get("issues")
    if not isinstance(companies, list) or not isinstance(overrides, dict) or not isinstance(issues, list):
        raise ValueError("input JSON has an invalid top-level shape")

    valuation_policy = overrides_data.get("valuationPolicy") or {}
    if not isinstance(valuation_policy, dict):
        raise ValueError("valuationPolicy must be an object")
    overseas_adjustment_weight = require_number(
        valuation_policy.get("overseasAdjustmentWeight", DEFAULT_OVERSEAS_ADJUSTMENT_WEIGHT),
        "valuationPolicy.overseasAdjustmentWeight",
        0,
    )
    if overseas_adjustment_weight > 1:
        raise ValueError("valuationPolicy.overseasAdjustmentWeight must be at most 1")

    seen_codes: set[str] = set()
    built: list[dict[str, Any]] = []
    for company in companies:
        if not isinstance(company, dict):
            raise ValueError("each company must be an object")
        code = str(company.get("code", ""))
        if len(code) != 6 or not code.isdigit():
            raise ValueError(f"invalid stock code: {code!r}")
        if code in seen_codes:
            raise ValueError(f"duplicate stock code: {code}")
        seen_codes.add(code)

        assumption = overrides.get(code)
        if not isinstance(assumption, dict):
            raise ValueError(f"missing manual VM assumption for {code}")
        declared_cavm = require_number(company.get("cavm"), f"{code}.cavm", 0)
        base_total = sum(require_number((company.get("components") or {}).get(key), f"{code}.components.{key}", 0) for key in COMPONENT_LIMITS)
        if declared_cavm != base_total:
            raise ValueError(f"{code}: declared CAVM does not equal the component sum")
        components = component_scores(company, assumption)
        cavm = sum(components.values())

        current_price = int(require_number(company.get("currentPrice"), f"{code}.currentPrice", 0))
        valuation = valuation_for(code, assumption, overseas_adjustment_weight)
        final_vm = int(valuation["finalVm"])
        gap_rate = round((current_price - final_vm) / final_vm * 100, 1) if final_vm > 0 else None
        rating, opinion = rating_for(cavm, gap_rate)
        vm_status = str(assumption.get("status", overrides_data.get("status", "draft")))
        if vm_status != "reviewed":
            opinion = {
                "적극 매수": "적극 검토(가정)",
                "분할매수": "분할 검토(가정)",
                "관찰": "관찰(가정)",
                "후보": "후보(가정)",
                "투자 제외": "투자 제외(가정)",
            }.get(opinion, "VM 검토 필요(가정)")
        issue_overrides = assumption.get("issueOverrides") or {}

        base_review = company.get("review") or {}
        next_review = min(
            str(base_review.get("nextReviewAt", overrides_data.get("reviewPolicy", {}).get("nextReviewAt", "9999-12-31"))),
            str(overrides_data.get("reviewPolicy", {}).get("nextReviewAt", "9999-12-31")),
        )
        built.append(
            {
                "code": code,
                "name": str(company.get("name", "")),
                "sector": str(company.get("sector", "")),
                "cavm": cavm,
                "components": components,
                "currentPrice": current_price,
                "priceBasisDate": str(company.get("priceBasisDate", companies_data.get("priceBasisDate", ""))),
                "priceSource": str(company.get("priceSource", "")),
                **valuation,
                "vmStatus": vm_status,
                "vmNote": str(assumption.get("analystNote", "")),
                "gapRate": gap_rate,
                "rating": rating,
                "opinion": opinion,
                "reason": str(company.get("reason", "")),
                "risk": str(company.get("risk", "")),
                "issues": public_issues(code, issues, issue_overrides),
                "financials": company.get("financials", {}),
                "sources": company.get("sources", []),
                "review": {
                    "status": f"CAVM {base_review.get('status', 'pending')} / VM {assumption.get('status', overrides_data.get('status', 'draft'))}",
                    "reviewedAt": str(base_review.get("reviewedAt", companies_data.get("basisDate", ""))),
                    "nextReviewAt": next_review,
                },
            }
        )

    built.sort(
        key=lambda item: (
            -item["cavm"],
            -item["components"]["moat"],
            -item["components"]["growth"],
            -item["components"]["profitability"],
            -item["components"]["financialHealth"],
            item["name"],
        )
    )
    for rank, item in enumerate(built, start=1):
        item["rank"] = rank
        # Keep rank first for easier review and stable UI consumption.
        item_order = ["rank"] + [key for key in item if key != "rank"]
        item_copy = {key: item[key] for key in item_order}
        item.clear()
        item.update(item_copy)

    kst = timezone(timedelta(hours=9))
    output = {
        "generatedAt": datetime.now(kst).isoformat(timespec="seconds"),
        "basisDate": str(companies_data.get("basisDate", "")),
        "dataStatus": data_status_for(companies_data),
        "financialDataSummary": companies_data.get("financialDataSummary", {}),
        "methodology": {
            "version": "CAVM Official v1.0 · Sector VM v2.0",
            "weights": COMPONENT_LIMITS,
            "formula": f"일반기업은 과거 5년 평균 PER에 해외 유사기업 차이의 {overseas_adjustment_weight * 100:g}%를 보정한다. 은행·금융지주는 정상화 BPS × 적정 PBR을 주평가하고 정상화 EPS × PER로 교차검증한다. 증권·복합금융은 PBR·PER를 병행하며, 보험은 PBR에 CSM·SOTP 조정을 더한다. 메모리 반도체는 2~3년 정상화 EPS × 정상 PER를 현재가치로 할인한다. 괴리율 = (현재가 - Final VM) ÷ Final VM × 100",
            "ratingPolicy": "CAVM 80점 이상을 기본 품질 통과로 보고, VM 초안 기준 괴리율 -20% 이하는 적극 검토, -20% 초과~-10% 이하는 분할 검토, -10% 초과는 관찰로 표시한다. VM이 검토 완료되기 전에는 매수 표현을 사용하지 않는다.",
            "selectionPolicy": "표시 순서는 CAVM, 해자, 성장성, 수익성, 재무건전성 순이다. 최종 경계 동점은 업종 분산을 사람이 검토한다.",
            "disclaimer": "CAVM은 기업의 질, VM은 가격을 평가하는 내부 분석 모델입니다. VM 입력값은 사람이 검토하는 초안이며 매수·매도 권유나 수익 보장이 아닙니다. 금융사는 CET1·연체율·NPL·대손비용·실제 자사주 소각을, 보험사는 K-ICS·CSM·SOTP를, 메모리 반도체는 가격·재고·CAPEX와 사이클 위치를 함께 확인합니다.",
        },
        "summary": {
            "universeSize": int(companies_data.get("universeSize", len(built))),
            "top20Count": len(built),
            "averageCavm": round(sum(item["cavm"] for item in built) / len(built), 1) if built else 0,
            "undervaluedCount": sum(1 for item in built if item["gapRate"] is not None and item["gapRate"] < 0),
        },
        "companies": built,
    }
    atomic_write_json(OUTPUT_PATH, output)
    print(f"built {len(built)} companies -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
