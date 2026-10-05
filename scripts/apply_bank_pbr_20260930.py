#!/usr/bin/env python3
"""Apply the 2026-09-30 bank VM method change (PBR 100%).

CEO confirmed on 2026-09-30: banks/financial holding companies are valued
with BPS x fair PBR only (PER leg dropped). COE: large banks 9.5%, regional 10%.
Touches only manual-overrides.json. Run scripts/build_dataset.py afterwards.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = ROOT / "data" / "manual-overrides.json"
BASIS_DATE = "2026-09-30"
SOURCE = "CAR 은행 VM PBR 100% 전환 · 2026.09.30"

SHINHAN_NOTE = ("PBR 100%(COE 9.5%, g 2%). 키움 4/27: ROE 9.7% → 1.027배 × 2027E BPS 139,134원 ÷ 1.1 = 129,860원 / "
                "한화 4/24: ROE 9.2% → 0.96배 × 2027E BPS 144,443원 ÷ 1.1 = 126,060원 → 평균 128,000원. "
                "FCFE DCF 검증 120,400원(COE 10%). 3개월 목표가 평균 140,000원 이하.")
KB_NOTE = ("PBR 100%(COE 9.5%, g 2%). 키움 4/27: ROE 10.63% → 1.151배 × 2027E BPS 181,494원 ÷ 1.1 = 190,000원 / "
           "유안타 4/28: ROE 10.70% → 1.160배 × 2027E BPS 178,875원 ÷ 1.1 = 188,600원 → 평균 189,300원. "
           "두 자료 모두 2Q 이전(보수적). 3개월 목표가 평균 228,800원 이하.")

data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
comp = data["companies"]

# 신한지주 (G20 19위)
sh = comp["055550"]
sh_before = sh.get("officialFinalVm")
sh.update({
    "officialFinalVm": 128000,
    "officialVmSource": SOURCE,
    "targetPbr": 1.0,
    "normalizedBps": 141789,
    "epsReviewedAt": BASIS_DATE,
    "multipleReviewedAt": BASIS_DATE,
    "analystNote": SHINHAN_NOTE,
})

# KB금융 (관찰기업, D10 평가 대상)
kb = comp["105560"]
kb_before = kb.get("officialFinalVm")
kb.update({
    "officialFinalVm": 189300,
    "officialVmSource": SOURCE,
    "targetPbr": 1.155,
    "normalizedBps": 180185,
    "epsReviewedAt": BASIS_DATE,
    "multipleReviewedAt": BASIS_DATE,
    "analystNote": KB_NOTE,
})

om = data["officialMaster"]
found = False
for c in om["candidates"]:
    if c["code"] == "105560":
        c["finalVm"] = 189300
        c["note"] = "관찰기업(CAQM 69.9). 2026-09-30 은행 PBR 100% 전환으로 VM 186,000→189,300원. D10 배당성장 트랙 평가 대상."
        found = True
if not found:
    raise SystemExit("KB금융(105560) not found in officialMaster.candidates")

om["version"] = "국내 TOP20 공식 마스터 · 2026-09-30 (은행 VM PBR 100% 전환)"
om["basisDate"] = BASIS_DATE
om["changes"] = [
    {
        "code": None,
        "name": "은행 2종목",
        "date": BASIS_DATE,
        "field": "VM 방법론 변경 (PBR 100%)",
        "before": f"신한지주 {sh_before:,} · KB금융 {kb_before:,} (PBR·PER 50:50)",
        "after": "신한지주 128,000 · KB금융 189,300 (PBR 100%)",
        "reason": "밸류업 이전의 낮은 과거 PER이 은행 가치를 25~45% 끌어내려 PER 절반을 없앴습니다. 은행 DCF로 검증하면 PBR 값과 ±5% 안에서 일치합니다. COE는 대형은행 9.5%, 지방은행 10%입니다.",
    }
] + om.get("changes", [])

OVERRIDES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("updated 055550:", sh_before, "->", 128000, "| 105560:", kb_before, "->", 189300)
