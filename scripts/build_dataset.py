#!/usr/bin/env python3
"""Build the public CAQM/VM snapshot from reviewed and human-editable inputs.

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
SCENARIO_LABELS = {
    "conservative": "보수",
    "base": "기준",
    "optimistic": "낙관",
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


def valuation_scenarios_for(
    valuation: dict[str, Any],
    assumption: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build transparent downside/base/upside cases with model-specific inputs."""
    model = str(valuation["valuationModel"])
    rounding_unit = int(assumption.get("roundingUnit", DEFAULT_ROUNDING_UNIT))
    base_vm = int(valuation["finalVm"])
    model_base_vm = float(valuation.get("modelCalculatedVm") or base_vm)

    def align_to_official_base(value: float) -> float:
        if valuation.get("officialVmOverride") and model_base_vm > 0:
            return value / model_base_vm * base_vm
        return value

    if model in {"standard_per", "memory_normalized"}:
        horizon_years = int(valuation.get("valuationHorizonYears", 3))
        base_eps = float(valuation.get("normalizedEps") or valuation.get("forwardEps3y") or 0)
        base_multiple = float(valuation.get("normalizedPer") or valuation.get("appliedPer") or 0)
        base_discount = float(valuation.get("discountRate") or 0)
        settings = {
            "conservative": (0.85 if model == "standard_per" else 0.80, 0.90, 1.5),
            "base": (1.0, 1.0, 0.0),
            "optimistic": (1.15, 1.10, -1.0),
        }
        result: dict[str, dict[str, Any]] = {}
        for key, (eps_factor, multiple_factor, discount_delta) in settings.items():
            adjusted_discount = max(0.0, base_discount + discount_delta)
            raw_value = (
                base_eps
                * eps_factor
                * base_multiple
                * multiple_factor
                / ((1 + adjusted_discount / 100) ** horizon_years)
            )
            value = rounded_won(align_to_official_base(raw_value), rounding_unit)
            if key == "base":
                value = base_vm
            eps_delta = round((eps_factor - 1) * 100)
            multiple_delta = round((multiple_factor - 1) * 100)
            result[key] = {
                "label": SCENARIO_LABELS[key],
                "finalVm": value,
                "assumptions": [
                    f"EPS {eps_delta:+d}%" if eps_delta else "공식 적용 EPS",
                    f"배수 {multiple_delta:+d}%" if multiple_delta else "공식 적용 배수",
                    f"할인율 {discount_delta:+g}%p" if discount_delta else "공식 할인율",
                ],
            }
        return result

    bps = float(valuation.get("normalizedBps") or 0)
    pbr = float(valuation.get("targetPbr") or 0)
    eps = float(valuation.get("normalizedEps") or 0)
    per = float(valuation.get("normalizedPer") or 0)
    primary_weight = float(valuation.get("primaryWeightPct") or 60) / 100
    csm_adjustment = float(valuation.get("csmSotpAdjustment") or 0)
    settings = {
        "conservative": (0.95, 0.90, 0.85),
        "base": (1.0, 1.0, 1.0),
        "optimistic": (1.05, 1.10, 1.15),
    }
    result = {}
    for key, (fundamental_factor, multiple_factor, csm_factor) in settings.items():
        primary = bps * fundamental_factor * pbr * multiple_factor
        cross_check = eps * fundamental_factor * per * multiple_factor
        if model == "financial_hybrid":
            value = primary * primary_weight + cross_check * (1 - primary_weight)
        elif model == "insurance_sotp":
            value = primary + csm_adjustment * csm_factor
        else:
            value = primary
        rounded_value = rounded_won(align_to_official_base(value), rounding_unit)
        if key == "base":
            rounded_value = base_vm
        fundamental_delta = round((fundamental_factor - 1) * 100)
        multiple_delta = round((multiple_factor - 1) * 100)
        assumptions = [
            f"BPS·EPS {fundamental_delta:+d}%" if fundamental_delta else "공식 적용 BPS·EPS",
            f"PBR·PER {multiple_delta:+d}%" if multiple_delta else "공식 적용 배수",
        ]
        if model == "insurance_sotp":
            csm_delta = round((csm_factor - 1) * 100)
            assumptions.append(f"CSM·SOTP {csm_delta:+d}%" if csm_delta else "공식 CSM·SOTP")
        result[key] = {
            "label": SCENARIO_LABELS[key],
            "finalVm": rounded_value,
            "assumptions": assumptions,
        }
    return result


def vm_confidence_for(
    company: dict[str, Any],
    assumption: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """Grade VM evidence freshness without claiming unavailable consensus data."""
    financials = company.get("financials") or {}
    latest_report = financials.get("latestReport") if isinstance(financials, dict) else None
    latest_report = latest_report if isinstance(latest_report, dict) else {}
    sources = company.get("sources") if isinstance(company.get("sources"), list) else []
    report_quality = str(latest_report.get("dataQuality", ""))
    vm_status = str(assumption.get("status", "draft"))
    model = str(assumption.get("valuationModel", "standard_per"))
    reasons: list[str] = []

    if not latest_report or report_quality not in {"complete", "partial"}:
        reasons.append("최신 보고서 핵심 지표 확인 필요")
    elif report_quality == "partial":
        reasons.append("최신 보고서 일부 계정 미수신")
    else:
        reasons.append("최신 보고서 주요 지표 수신 완료")

    if vm_status != "reviewed":
        reasons.append("VM 가정 최종 승인 전")
    else:
        reasons.append("VM 가정 검토 완료")

    if len(sources) < 2:
        reasons.append("비교·근거 자료 보강 필요")
    else:
        reasons.append("근거 자료 2건 이상 연결")

    has_model_inputs = (
        (model == "standard_per" and assumption.get("forwardEps3y") is not None and assumption.get("historicalPer5y") is not None)
        or (model == "memory_normalized" and assumption.get("normalizedEps") is not None and assumption.get("normalizedPer") is not None)
        or (model in {"bank_pbr", "financial_hybrid", "insurance_sotp"} and assumption.get("normalizedBps") is not None and assumption.get("targetPbr") is not None)
    )
    if not has_model_inputs or not latest_report:
        return "D", "핵심 데이터 보강 필요", reasons
    if vm_status == "reviewed" and report_quality == "complete" and len(sources) >= 2:
        return "B", "근거 확인 완료 · 일부 분석 가정", reasons
    return "C", "EPS 또는 배수 검토 필요", reasons


def change_log_for(
    previous: dict[str, Any] | None,
    current_companies: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    """Compare against the last committed public snapshot and keep material deltas."""
    empty = {
        "comparedAt": previous.get("generatedAt") if previous else None,
        "generatedAt": generated_at,
        "vm": [],
        "gap": [],
        "caqm": [],
        "entrants": [],
        "exits": [],
        "reports": [],
        "hasMaterialChanges": False,
    }
    if not previous or not isinstance(previous.get("companies"), list):
        return empty

    old_by_code = {str(item.get("code")): item for item in previous["companies"] if isinstance(item, dict)}
    new_by_code = {str(item.get("code")): item for item in current_companies}
    for code, company in new_by_code.items():
        old = old_by_code.get(code)
        if old is None:
            empty["entrants"].append({
                "code": code,
                "name": company.get("name"),
                "rank": company.get("rank"),
                "caqm": company.get("caqm"),
            })
            continue
        if old.get("finalVm") != company.get("finalVm"):
            empty["vm"].append({
                "code": code,
                "name": company.get("name"),
                "before": old.get("finalVm"),
                "after": company.get("finalVm"),
            })
        old_gap = old.get("gapRate")
        new_gap = company.get("gapRate")
        gap_delta = round(new_gap - old_gap, 1) if isinstance(old_gap, (int, float)) and isinstance(new_gap, (int, float)) else None
        old_gap_band = "적극 검토" if isinstance(old_gap, (int, float)) and old_gap <= -20 else "분할 검토" if isinstance(old_gap, (int, float)) and old_gap <= -10 else "관찰"
        new_gap_band = "적극 검토" if isinstance(new_gap, (int, float)) and new_gap <= -20 else "분할 검토" if isinstance(new_gap, (int, float)) and new_gap <= -10 else "관찰"
        if gap_delta is not None and (abs(gap_delta) >= 5 or old_gap_band != new_gap_band):
            empty["gap"].append({
                "code": code,
                "name": company.get("name"),
                "before": old_gap,
                "after": new_gap,
                "delta": gap_delta,
            })
        old_caqm = old.get("caqm", old.get("cavm"))
        new_caqm = company.get("caqm")
        caqm_delta = new_caqm - old_caqm if isinstance(old_caqm, (int, float)) and isinstance(new_caqm, (int, float)) else None
        if caqm_delta is not None and (abs(caqm_delta) >= 2 or old.get("rank") != company.get("rank")):
            empty["caqm"].append({
                "code": code,
                "name": company.get("name"),
                "before": old.get("caqm"),
                "after": company.get("caqm"),
            })
        old_report = ((old.get("financials") or {}).get("latestReport") or {})
        new_report = ((company.get("financials") or {}).get("latestReport") or {})
        if new_report.get("rceptNo") and old_report.get("periodEnd") != new_report.get("periodEnd"):
            empty["reports"].append({
                "code": code,
                "name": company.get("name"),
                "before": old_report.get("periodEnd"),
                "after": new_report.get("periodEnd"),
                "periodLabel": new_report.get("periodLabel"),
            })

    for code, company in old_by_code.items():
        if code not in new_by_code:
            empty["exits"].append({
                "code": code,
                "name": company.get("name"),
                "previousRank": company.get("rank"),
                "reason": "CAQM 정렬과 동점 규칙에 따라 TOP20 밖으로 이동",
            })

    empty["gap"].sort(key=lambda item: abs(item["delta"]), reverse=True)
    empty["hasMaterialChanges"] = any(empty[key] for key in ("vm", "gap", "caqm", "entrants", "exits", "reports"))
    return empty


def rating_for(caqm: int, gap_rate: float | None) -> tuple[str, str]:
    if caqm < 70:
        return "★☆☆☆☆", "투자 제외"
    if caqm < 80:
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


def apply_official_final_vm(
    code: str,
    valuation: dict[str, Any],
    assumption: dict[str, Any],
) -> dict[str, Any]:
    """Keep the reviewed master VM separate from the model's reproducible calculation."""
    official_value = assumption.get("officialFinalVm")
    if official_value is None:
        return valuation
    official_vm = require_number(official_value, f"{code}.officialFinalVm", 1)
    if not official_vm.is_integer():
        raise ValueError(f"{code}.officialFinalVm must be an integer")
    result = dict(valuation)
    result["modelCalculatedVm"] = int(valuation["finalVm"])
    result["finalVm"] = int(official_vm)
    result["officialVmOverride"] = True
    result["officialVmSource"] = str(
        assumption.get("officialVmSource", "CAP 국내 TOP20 공식 마스터")
    )
    return result


def main() -> None:
    previous_output = load_json(OUTPUT_PATH) if OUTPUT_PATH.exists() else None
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
        declared_caqm = require_number(company.get("caqm", company.get("cavm")), f"{code}.caqm", 0)
        base_total = sum(require_number((company.get("components") or {}).get(key), f"{code}.components.{key}", 0) for key in COMPONENT_LIMITS)
        if declared_caqm != base_total:
            raise ValueError(f"{code}: declared CAQM does not equal the component sum")
        components = component_scores(company, assumption)
        caqm = sum(components.values())

        current_price = int(require_number(company.get("currentPrice"), f"{code}.currentPrice", 0))
        valuation = valuation_for(code, assumption, overseas_adjustment_weight)
        valuation = apply_official_final_vm(code, valuation, assumption)
        vm_scenarios = valuation_scenarios_for(valuation, assumption)
        final_vm = int(valuation["finalVm"])
        gap_rate = round((current_price - final_vm) / final_vm * 100, 1) if final_vm > 0 else None
        rating, opinion = rating_for(caqm, gap_rate)
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
        confidence_grade, confidence_label, confidence_reasons = vm_confidence_for(company, assumption)
        next_review = min(
            str(base_review.get("nextReviewAt", overrides_data.get("reviewPolicy", {}).get("nextReviewAt", "9999-12-31"))),
            str(overrides_data.get("reviewPolicy", {}).get("nextReviewAt", "9999-12-31")),
        )
        built.append(
            {
                "code": code,
                "officialOrder": int(require_number(company.get("officialOrder", len(built) + 1), f"{code}.officialOrder", 1)),
                "name": str(company.get("name", "")),
                "sector": str(company.get("sector", "")),
                "caqm": caqm,
                "components": components,
                "currentPrice": current_price,
                "priceBasisDate": str(company.get("priceBasisDate", companies_data.get("priceBasisDate", ""))),
                "priceSource": str(company.get("priceSource", "")),
                **valuation,
                "vmScenarios": vm_scenarios,
                "vmConfidence": {
                    "grade": confidence_grade,
                    "label": confidence_label,
                    "reasons": confidence_reasons,
                },
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
                    "status": f"CAQM {base_review.get('status', 'pending')} / VM {assumption.get('status', overrides_data.get('status', 'draft'))}",
                    "reviewedAt": str(base_review.get("reviewedAt", companies_data.get("basisDate", ""))),
                    "nextReviewAt": next_review,
                    "basisDates": {
                        "price": str(company.get("priceBasisDate", companies_data.get("priceBasisDate", ""))),
                        "financialReport": str(((company.get("financials") or {}).get("latestReport") or {}).get("periodEnd", companies_data.get("basisDate", ""))),
                        "eps": str(assumption.get("epsReviewedAt", base_review.get("reviewedAt", companies_data.get("basisDate", "")))),
                        "multiple": str(assumption.get("multipleReviewedAt", base_review.get("reviewedAt", companies_data.get("basisDate", "")))),
                        "nextReview": next_review,
                    },
                },
            }
        )

    built.sort(
        key=lambda item: (
            -item["caqm"],
            item["officialOrder"],
            -item["components"]["moat"],
            -item["components"]["growth"],
            -item["components"]["profitability"],
            -item["components"]["financialHealth"],
            item["name"],
        )
    )
    previous_caqm: int | None = None
    display_rank = 0
    for position, item in enumerate(built, start=1):
        if item["caqm"] != previous_caqm:
            display_rank = position
            previous_caqm = item["caqm"]
        item["rank"] = display_rank
        item["position"] = position
        # Keep rank first for easier review and stable UI consumption.
        item_order = ["rank"] + [key for key in item if key != "rank"]
        item_copy = {key: item[key] for key in item_order}
        item.clear()
        item.update(item_copy)

    kst = timezone(timedelta(hours=9))
    generated_at = datetime.now(kst).isoformat(timespec="seconds")
    changes = change_log_for(previous_output, built, generated_at)
    latest_reports = [
        ((item.get("financials") or {}).get("latestReport") or {})
        for item in built
    ]
    financial_summary = {
        "companyCount": len(built),
        "latestReportSuccessCount": sum(1 for report in latest_reports if report.get("rceptNo")),
        "latestReportCompleteCount": sum(1 for report in latest_reports if report.get("dataQuality") == "complete"),
        "latestReportPartialCount": sum(1 for report in latest_reports if report.get("dataQuality") == "partial"),
    }
    status_input = dict(companies_data)
    status_input["financialDataSummary"] = financial_summary
    data_status = data_status_for(status_input)
    if str(overrides_data.get("status")) == "reviewed":
        data_status = data_status.replace("VM 수동가정 초안", "공식 마스터 VM 검토 완료")
    output = {
        "generatedAt": generated_at,
        "basisDate": str(companies_data.get("basisDate", "")),
        "dataStatus": data_status,
        "financialDataSummary": financial_summary,
        "changes": changes,
        "officialMaster": overrides_data.get("officialMaster", {}),
        "methodology": {
            "version": "CAQM Official v1.1 · Sector VM v2.0",
            "weights": COMPONENT_LIMITS,
            "formula": f"일반기업은 과거 5년 평균 PER에 해외 유사기업 차이의 {overseas_adjustment_weight * 100:g}%를 보정한다. 은행·금융지주는 정상화 BPS × 적정 PBR을 주평가하고 정상화 EPS × PER로 교차검증한다. 증권·복합금융은 PBR·PER를 병행하며, 보험은 PBR에 CSM·SOTP 조정을 더한다. 메모리 반도체는 2~3년 정상화 EPS × 정상 PER를 현재가치로 할인한다. 괴리율 = (현재가 - Final VM) ÷ Final VM × 100",
            "ratingPolicy": "CAQM은 가격과 VM을 제외하고 해자 30점, 성장성 25점, 수익성 20점, 재무건전성 15점, 경영진·주주환원 10점으로 평가한다. CAQM 80점 이상을 기본 품질 통과로 보고, VM 초안 기준 괴리율 -20% 이하는 적극 검토, -20% 초과~-10% 이하는 분할 검토, -10% 초과는 관찰로 표시한다. VM이 검토 완료되기 전에는 매수 표현을 사용하지 않는다.",
            "selectionPolicy": "공식 마스터는 CAQM 순위를 우선하며 동점은 승인된 마스터 순서를 유지한다. 정기 재선정 때는 해자, 성장성, 현금창출력, 재무건전성, 업종분산 순으로 검토한다.",
            "disclaimer": "CAQM은 기업의 질, VM은 가격을 평가하는 내부 분석 모델입니다. VM 입력값은 사람이 검토하는 초안이며 매수·매도 권유나 수익 보장이 아닙니다. 금융사는 CET1·연체율·NPL·대손비용·실제 자사주 소각을, 보험사는 K-ICS·CSM·SOTP를, 메모리 반도체는 가격·재고·CAPEX와 사이클 위치를 함께 확인합니다.",
        },
        "summary": {
            "universeSize": int(companies_data.get("universeSize", len(built))),
            "top20Count": len(built),
            "averageCaqm": round(sum(item["caqm"] for item in built) / len(built), 1) if built else 0,
            "undervaluedCount": sum(1 for item in built if item["gapRate"] is not None and item["gapRate"] < 0),
        },
        "companies": built,
    }
    atomic_write_json(OUTPUT_PATH, output)
    print(f"built {len(built)} companies -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
