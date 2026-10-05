#!/usr/bin/env python3
"""Apply the 2026-10-05 Korean Re VM confirmation (PBR·PER 50:50 = 14,800원).

Touches only data/manual-overrides.json. Run scripts/build_dataset.py afterwards.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "manual-overrides.json"

data = json.loads(PATH.read_text(encoding="utf-8"))
om = data["officialMaster"]
for c in om["candidates"]:
    if c["code"] == "003690":
        before = c.get("finalVm")
        c["finalVm"] = 14800
        c["note"] = ("VM 확정(2026-10-05): PBR (ROE 11.37%, COE 10% → 1.17배 × 2027F BPS 18,581 ÷ 1.1 = 19,800) + "
                     "PER (2027F EPS 2,047 × 증권사 확정 5년 평균 5.26배 ÷ 1.1 = 9,800) 50:50. iM 8/18 단일. "
                     "D10 배당성장 트랙 편입(DGQM 79.4). 3Q26 일본 지진 손실 반영 후 재계산.")
        break
else:
    raise SystemExit("코리안리(003690) not found in officialMaster.candidates")
om["changes"] = [{
    "code": "003690", "name": "코리안리재보험", "date": "2026-10-05", "field": "VM 확정",
    "before": f"{before:,}" if before else "—", "after": "14,800",
    "reason": "가결정(현재 PER 사용)을 증권사 확정 5년 평균 PER로 교체한 PBR·PER 50:50 기본 원칙으로 확정했습니다.",
}] + om.get("changes", [])
PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("updated 003690:", before, "-> 14800")
