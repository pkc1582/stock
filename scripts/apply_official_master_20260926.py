#!/usr/bin/env python3
"""Apply the 2026-09-26 TOP20 VM re-verification (VM 4-step, new standards).

One-off content update (follows the 2026-09-22 v3 rebuild). Changes:
- Final VM for every TOP20 name is now confirmed under the new standards
  (multi-broker EPS averages, K×D pre-pricing check, broker-target cap).
- 동진쎄미켐 (005290) is removed from the TOP20 (no broker coverage for
  3 years; VM EPS was a blog estimate) and 후보군 1위 LIG디펜스앤에어로스페이스
  (079550) is promoted to rank 20 with management 5.0 -> 3.0
  (executive bribery indictment, 2026-08-20), CAQM 76.0 -> 74.0.
- officialMaster version/changes/candidates are refreshed.

Run scripts/build_dataset.py afterwards to regenerate public/data/latest.json.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANIES_PATH = ROOT / "data" / "companies.json"
OVERRIDES_PATH = ROOT / "data" / "manual-overrides.json"

BASIS_DATE = "2026-09-26"
NEXT_REVIEW = "2026-10-31"
SOURCE = "CAR TOP20 VM 재검증 · 2026.09.26"

# code -> (officialFinalVm, analystNote)
VM_UPDATES = {
    "005930": (493300, "2사(현대차·DS) 2027F EPS 평균 × PER 6배. 2026-09-25 새 기준(복수 EPS·목표가 상한) 점검 결과 유지."),
    "000660": (2879100, "2028F EPS(미래에셋) × 정상 PER 8배 ÷ 1.1². 2사 평균 적용 시 3,135,300원이나 사이클 산업 장기 지속성 불확실로 보수적 유지(2026-09-25)."),
    "403870": (57700, "2028F EPS × 5년 평균 PER 38.73배(프리미엄 0.66%) ÷ 1.1². 목표가 상한 점검 후 유지(2026-09-25)."),
    "267260": (954200, "2027E EPS 3사(교보·SK·미래에셋) 평균 34,246원 × 30.65배(5년 평균 30.52배 + K×D 선반영 체크) ÷ 1.1."),
    "298040": (3472000, "2027F EPS 2분기 실적 이후 2사(대신·SK) 평균 126,969원 × 30.08배(5년 평균 26.63배 + K×D) ÷ 1.1."),
    "007660": (168800, "2027F EPS(유안타) × 33.55배 ÷ 1.1. 메리츠·다올 영업이익 전망과 4% 이내 교차확인, 목표가 상한 점검 후 유지(2026-09-25)."),
    "329180": (516900, "PBR-고든 방식 기각. 조선 3사 선행 PER 최고(14.9배) + 경쟁력 프리미엄 10% = 16.4배 × 5사 평균 2028F EPS 38,164원 ÷ 1.1²."),
    "062040": (274700, "5년 PER 사용 불가(상장 2년) → PEG 방식. 3사 평균 2028F EPS 11,460원 × PER 29배(목표가 평균 상한 280,000원 준수) ÷ 1.1²."),
    "003230": (1427500, "2028E EPS(LS) × 17.62배 ÷ 1.1². 목표가 상한 점검 후 유지(2026-09-25)."),
    "012450": (1025500, "2028F EPS 3사(DS·유안타·키움, 이상치 대신 제외) 평균 67,585원 × 5년 평균 PER 18.36배(K×D 결과 프리미엄 0%) ÷ 1.1²."),
    "278470": (524100, "2028F EPS 3사(교보·유안타·LS) 평균 22,180원 × 5년 평균 PER 28.59배(K×D 결과 프리미엄 0%) ÷ 1.1²."),
    "033780": (164900, "2027F EPS 2사(한화·DS) 평균 13,994원 × 12.96배(5년 평균 12.67배 + 프리미엄 2.28%) ÷ 1.1."),
    "214150": (60300, "2028F EPS 3사 평균 3,841원 × PER 19배(성장 둔화로 과거 PER 하향, 목표가 평균 상한 62,300원 준수) ÷ 1.1²."),
    "000810": (629500, "SOTP: 보험 본업(삼성전자 배당 제외 2027F EPS 55,144원 × 손보 peer PER 6.64배 ÷ 1.1 = 332,900원) + 삼성전자 지분(8,697만주 시가 × 50% 할인 = 주당 296,600원)."),
    "058470": (75200, "2028F EPS 2사(신한·메리츠) 평균 3,379원 × 5년 평균 PER 26.94배(프리미엄 0%) ÷ 1.1². 7~9월 파업 영향은 3분기 실적으로 확인 필요."),
    "237690": (171500, "5년 평균 PER 왜곡(136배) → PEG 방식. 2사 평균 2028F EPS 6,322원 × 32.8배(성장률) ÷ 1.1²."),
    "214450": (479300, "2028F EPS(LS, 보수적) 25,868원 × 22.42배(5년 평균 21.84배 + 프리미엄 2.64%) ÷ 1.1². 국내 리쥬란 성장 둔화 반영."),
    "010120": (151700, "2028F EPS 최신 2사(하나·유안타) 평균 6,845원 × 5년 평균 PER 26.81배(K×D 결과 프리미엄 0%) ÷ 1.1²."),
    "055550": (99500, "PBR(ROE 9.7%, COE 9.5%, g 2% → 1.027배 × 2027 BPS 144,443원 ÷ 1.1)과 PER(2027F EPS 12,830원 × 5.5배 ÷ 1.1) 50:50."),
    "079550": (681200, "2028F EPS 2사(DS·키움, 이상치 iM 제외) 평균 34,654원 × 23.78배(5년 평균 23.21배 + K×D) ÷ 1.1²."),
}

LIG_ENTRY = {
    "code": "079550",
    "name": "LIG디펜스앤에어로스페이스",
    "sector": "방산",
    "caqm": 74.0,
    "components": {
        "moatIndustry": 20.0,
        "moatCross": 8.8,
        "growth": 14.8,
        "profitability": 15.9,
        "financialHealth": 8.5,
        "management": 3.0,
        "shareholderReturn": 3.0,
    },
    "currentPrice": 699000,
    "priceBasisDate": "2026-09-23",
    "reason": "정밀유도무기(천궁-Ⅱ·L-SAM) 국내 사실상 독점과 중동 수출 레퍼런스, 수주잔고 확대가 성장 가시성을 높인다.",
    "risk": "임원 뇌물 기소(2026-08-20)에 따른 방사청 입찰참가 제한 여부, 수주-매출 인식 시차와 환율 변동을 점검해야 한다.",
    "warning": "임원 2명 방사청 뇌물 혐의 기소(2026-08-20) — 부정당업자 제재 결정 대기",
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


companies_data = load(COMPANIES_PATH)
overrides_data = load(OVERRIDES_PATH)

# --- companies.json: replace 동진쎄미켐 with LIG at rank 20
companies = companies_data["companies"]
idx = next(i for i, c in enumerate(companies) if c["code"] == "005290")
template = companies[idx]
lig = {
    "code": LIG_ENTRY["code"],
    "officialOrder": 20,
    "name": LIG_ENTRY["name"],
    "sector": LIG_ENTRY["sector"],
    "caqm": LIG_ENTRY["caqm"],
    "components": LIG_ENTRY["components"],
    "currentPrice": LIG_ENTRY["currentPrice"],
    "priceBasisDate": LIG_ENTRY["priceBasisDate"],
    "priceSource": template.get("priceSource", "공공데이터포털 금융위원회 주식시세정보"),
    "reason": LIG_ENTRY["reason"],
    "risk": LIG_ENTRY["risk"],
    "financials": {
        "revenue2025": None,
        "operatingMargin2025": None,
        "roe2025": None,
        "debtRatio2025": None,
        "eps2025": None,
    },
    "sources": template.get("sources", []),
    "review": {"status": "reviewed", "reviewedAt": BASIS_DATE, "nextReviewAt": NEXT_REVIEW},
    "warning": LIG_ENTRY["warning"],
}
companies[idx] = lig
for c in companies:
    c.setdefault("review", {})
    c["review"].update({"status": "reviewed", "reviewedAt": BASIS_DATE, "nextReviewAt": NEXT_REVIEW})
companies_data["basisDate"] = BASIS_DATE

# --- manual-overrides.json: official VM values
ov = overrides_data["companies"]
for code, (vm, note) in VM_UPDATES.items():
    if code not in ov:
        raise SystemExit(f"missing override entry for {code}")
    ov[code]["officialFinalVm"] = vm
    ov[code]["officialVmSource"] = SOURCE
    ov[code]["analystNote"] = note
    ov[code]["status"] = "reviewed"
    ov[code]["epsReviewedAt"] = BASIS_DATE
    ov[code]["multipleReviewedAt"] = BASIS_DATE
ov.pop("005290", None)
overrides_data["basisDate"] = BASIS_DATE

om = overrides_data["officialMaster"]
om["version"] = "국내 TOP20 공식 마스터 · 2026-09-26 (TOP20 VM 새 기준 재검증 완료)"
om["basisDate"] = BASIS_DATE
om["candidates"] = [c for c in om["candidates"] if c["code"] != "079550"]
new_changes = [
    {
        "code": None,
        "name": "TOP20 전종목",
        "date": BASIS_DATE,
        "field": "Final VM 재검증 (새 기준)",
        "before": "종목별 기존 VM(단일 증권사 EPS·임의 프리미엄 혼재)",
        "after": "복수 증권사 EPS 평균 + K×D 선반영 체크 + 3개월 증권사 목표가 평균을 VM 상한으로 적용",
        "reason": "TOP20 20종목의 VM을 동일 기준으로 재산출했습니다. 우리 VM은 증권사 목표가 평균을 넘지 않도록 보수적으로 제한합니다.",
    },
    {
        "code": "005290",
        "name": "동진쎄미켐",
        "date": BASIS_DATE,
        "field": "검토대상 제외",
        "before": "TOP20 20위 (CAQM 77.0)",
        "after": "제외",
        "reason": "최근 3년간 증권사 커버리지가 없어 신뢰할 만한 EPS 컨센서스를 확보할 수 없습니다(장기예측불가 원칙).",
    },
    {
        "code": "079550",
        "name": "LIG디펜스앤에어로스페이스",
        "date": BASIS_DATE,
        "field": "후보군 → TOP20 20위 승격 · CAQM 조정",
        "before": "후보군 1위 (CAQM 76.0, VM 758,100원)",
        "after": "TOP20 20위 (CAQM 74.0, VM 681,200원)",
        "reason": "동진쎄미켐 제외로 승격. 임원 뇌물 기소(2026-08-20)를 반영해 경영진 점수를 5.0→3.0으로 조정했습니다.",
    },
    {
        "code": "000810",
        "name": "삼성화재",
        "date": BASIS_DATE,
        "field": "Final VM 방법론 변경 (SOTP)",
        "before": "785,000원",
        "after": "629,500원",
        "reason": "보험 본업 가치와 삼성전자 지분 가치(50% 할인)를 따로 계산해 합산했습니다. 특별배당은 지분 가치에 포함된 것으로 보고 이중계산하지 않습니다.",
    },
]
om["changes"] = new_changes + om.get("changes", [])

dump(COMPANIES_PATH, companies_data)
dump(OVERRIDES_PATH, overrides_data)
print("applied", len(VM_UPDATES), "VM updates; TOP20 =", [c["name"] for c in companies])
