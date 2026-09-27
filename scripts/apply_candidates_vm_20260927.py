#!/usr/bin/env python3
"""Apply the 2026-09-27 candidate (21~26위) provisional VM re-evaluation.

CEO confirmed the provisional (잠정) VMs on 2026-09-27. Only
officialMaster.candidates finalVm/note and the change log are touched.
Run scripts/build_dataset.py afterwards to regenerate public/data/latest.json.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = ROOT / "data" / "manual-overrides.json"
BASIS_DATE = "2026-09-27"

# code -> (finalVm, note)
UPDATES = {
    "006800": (29300, "잠정 VM(2026-09-27): PBR(ROE 3개년 14.47%, COE 10.5% → 1.47배 × 2027F BPS 24,463원 ÷ 1.1) + PER(2027F EPS 3,505원 × 5년평균 8.17배 ÷ 1.1) 50:50. 교보 단일소스."),
    "140860": (252500, "잠정 VM(2026-09-27): 2028F EPS 7,978원(iM, 2Q 쇼크 이전) × 과거 PER 38.3배(2022~25) ÷ 1.1². 3Q 이후 EPS로 재계산 예정."),
    "003690": (16800, "잠정 VM(2026-09-27): PBR(정상화 ROE 11.37%, COE 10% → 1.17배 × 2027F BPS 18,581원 ÷ 1.1) + PER(2027F EPS 2,047원 × 현재 PER 7.43배 ÷ 1.1) 50:50. iM 단일소스."),
    "271560": (146600, "잠정 VM(2026-09-27): 2028F EPS 14,825원(교보) × 11.97배(5년평균 11.67배 + 프리미엄 2.5%) ÷ 1.1². 키움 9/14 하향 반영 시 133,400원."),
    "021240": (102300, "잠정 VM(2026-09-27): 2028F EPS 12,898원(신한 역산) × 5년평균 PER 9.60배(K×D 결과 프리미엄 0%) ÷ 1.1²."),
    "207940": (1266900, "VM 유지(2026-09-27): 증권사 EPS 기준(분할 전후) 불일치·3조원 유상증자(11/30 신주상장)로 재계산 보류."),
}

data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
om = data["officialMaster"]
found = set()
for c in om["candidates"]:
    if c["code"] in UPDATES:
        vm, note = UPDATES[c["code"]]
        c["finalVm"] = vm
        c["note"] = note
        found.add(c["code"])
missing = set(UPDATES) - found
if missing:
    raise SystemExit(f"missing candidates: {missing}")

om["version"] = "국내 TOP20 공식 마스터 · 2026-09-27 (후보군 잠정 VM 새 기준 재평가)"
om["basisDate"] = BASIS_DATE
om["changes"] = [
    {
        "code": None,
        "name": "후보군 21~26위",
        "date": BASIS_DATE,
        "field": "잠정 VM 재평가 (새 기준)",
        "before": "미래에셋 35,000 · 파크시스템스 355,800 · 코리안리 18,000 · 오리온 151,300 · 코웨이 116,900",
        "after": "미래에셋 29,300 · 파크시스템스 252,500 · 코리안리 16,800 · 오리온 146,600 · 코웨이 102,300 (삼성바이오로직스 유지)",
        "reason": "후보군도 5년평균 PER·K×D·목표가 상한 기준으로 다시 계산했습니다. 일부 종목은 증권사 자료가 한 곳뿐이라 잠정치이며, 3분기 실적 이후 재점검합니다.",
    }
] + om.get("changes", [])

OVERRIDES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("updated", sorted(found))
