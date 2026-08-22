#!/usr/bin/env python3
"""Apply the 2026-08-22 official TOP20 master update.

One-off content update script (mirrors the intent of apply_official_master.mjs
for the 2026-08-13 master). Reads the current data/companies.json and
data/manual-overrides.json, applies the CAQM/VM changes from the 2026-08-22
spec, and writes the results back. Run scripts/build_dataset.py afterwards to
regenerate public/data/latest.json.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANIES_PATH = ROOT / "data" / "companies.json"
OVERRIDES_PATH = ROOT / "data" / "manual-overrides.json"

SOURCE = "CAR 국내 TOP20 공식 마스터 · 2026.08.22"
BASIS_DATE = "2026-08-22"
NEXT_REVIEW = "2026-09-30"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


companies_data = load(COMPANIES_PATH)
overrides_data = load(OVERRIDES_PATH)

by_code = {c["code"]: c for c in companies_data["companies"]}

# --- 1. CAQM + component updates for existing TOP20 members that stay -----
CAQM_UPDATES = {
    # code: (caqm, components[moat, growth, profitability, financialHealth, management])
    "058470": (96, [30, 24, 20, 15, 7]),   # 리노공업 (변경없음)
    "000660": (96, [29, 25, 20, 15, 7]),   # SK하이닉스
    "298040": (96, [29, 25, 19, 14, 9]),   # 효성중공업
    "267260": (95, [29, 25, 19, 14, 8]),   # HD현대일렉트릭
    "207940": (95, [29, 25, 19, 14, 8]),   # 삼성바이오로직스 (변경없음)
    "214450": (95, [28, 25, 20, 14, 8]),   # 파마리서치 (스펙 표 누락 확인 후 유지, 변경없음)
    "140860": (95, [30, 24, 19, 14, 8]),   # 파크시스템스
    "105560": (95, [29, 22, 19, 15, 10]),  # KB금융
    "005930": (94, [29, 23, 20, 15, 7]),   # 삼성전자
    "192820": (94, [28, 25, 18, 15, 8]),   # 코스맥스
    "012450": (94, [29, 25, 18, 13, 9]),   # 한화에어로스페이스 (변경없음)
    "003230": (94, [28, 25, 20, 14, 7]),   # 삼양식품
    "000810": (94, [28, 22, 19, 15, 10]),  # 삼성화재 (변경없음)
    "138040": (94, [28, 22, 19, 15, 10]),  # 메리츠금융지주
    "086790": (92, [27, 21, 19, 15, 10]),  # 하나금융지주 (변경없음)
    "000270": (91, [28, 21, 18, 15, 9]),   # 기아 (변경없음)
    "030200": (91, [27, 21, 18, 15, 10]),  # KT
}

VM_UPDATES = {
    "058470": 69300,
    "000660": 2343000,
    "298040": 2547600,
    "267260": 649400,
    "207940": 1501800,
    "140860": 221500,
    "105560": 198844,
    "005930": 409719,
    "192820": 261800,
    "012450": 1156000,
    "003230": 1217400,
    "000810": 604600,
    "138040": 123046,
    "086790": 156662,
    "000270": 148500,
    "030200": 62300,
}

ORDER = [
    "058470", "000660", "298040", "267260", "207940", "214450", "140860", "105560",
    "005930", "192820", "012450", "003230", "000810", "138040", "278470", "161890",
    "086790", "000270", "030200", "329180",
]

for code, (caqm, comps) in CAQM_UPDATES.items():
    company = by_code[code]
    company["caqm"] = caqm
    company["components"] = {
        "moat": comps[0], "growth": comps[1], "profitability": comps[2],
        "financialHealth": comps[3], "management": comps[4],
    }
    company["review"]["reviewedAt"] = BASIS_DATE
    company["review"]["nextReviewAt"] = NEXT_REVIEW

# --- 2. Remove TOP20 members that were dropped ------------------------------
for code in ("214150", "009540", "005380"):  # 클래시스, HD한국조선해양, 현대차
    by_code.pop(code, None)

# --- 3. Add new TOP20 members -----------------------------------------------
REPORT_SOURCES = [
    {"label": "OpenDART 정기보고서", "url": "https://opendart.fss.or.kr/"},
    {"label": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr/"},
]

by_code["278470"] = {
    "code": "278470",
    "name": "에이피알",
    "sector": "뷰티테크·의료미용기기",
    "caqm": 93,
    "components": {"moat": 26, "growth": 25, "profitability": 20, "financialHealth": 13, "management": 9},
    "currentPrice": 384500,
    "priceBasisDate": "2026-08-20",
    "priceSource": "공공데이터포털 금융위원회 주식시세정보",
    "reason": "메디큐브·에이프릴스킨 자체 브랜드와 뷰티 디바이스(에이지알)의 해외 확장이 고성장·고수익성을 동시에 만든다.",
    "risk": "북미·일본 채널 재고와 디바이스 신제품 주기, 브랜드 확장 속도에 따른 마진 변동을 점검해야 한다.",
    "financials": {
        "revenue2025": 15273,
        "operatingMargin2025": 23.93,
        "roe2025": 64.97,
        "debtRatio2025": 73.11,
        "eps2025": 7737,
        "latestReport": {
            "reportYear": 2025,
            "reportCode": "11011",
            "periodLabel": "2025년 사업보고서",
            "fsDiv": "CFS",
            "rceptNo": "20260323001257",
            "periodEnd": "2025-12-31",
            "fetchedAt": "2026-08-22T12:11:00+09:00",
            "revenueEok": 15273,
            "operatingMargin": 23.93,
            "roeAnnualized": 64.97,
            "debtRatio": 73.11,
            "epsCumulative": 7737,
            "dataQuality": "complete",
            "missingMetrics": [],
        },
    },
    "sources": REPORT_SOURCES,
    "review": {"status": "reviewed", "reviewedAt": BASIS_DATE, "nextReviewAt": NEXT_REVIEW},
    "officialOrder": 0,
}

by_code["161890"] = {
    "code": "161890",
    "name": "한국콜마",
    "sector": "화장품 ODM",
    "caqm": 93,
    "components": {"moat": 28, "growth": 23, "profitability": 18, "financialHealth": 14, "management": 10},
    "currentPrice": 106700,
    "priceBasisDate": "2026-08-20",
    "priceSource": "공공데이터포털 금융위원회 주식시세정보",
    "reason": "국내외 화장품·제약 ODM 생산능력과 고객 다변화, 무석회 선케어 등 기술 포트폴리오가 안정적 성장을 지지한다.",
    "risk": "해외 법인 수익성 정상화 속도와 원재료·환율 변동, 고객사 재고 조정이 핵심 위험이다.",
    "financials": {
        "revenue2025": 27224,
        "operatingMargin2025": 8.80,
        "roe2025": 10.09,
        "debtRatio2025": 107.38,
        "eps2025": 7127,
        "latestReport": {
            "reportYear": 2025,
            "reportCode": "11011",
            "periodLabel": "2025년 사업보고서",
            "fsDiv": "CFS",
            "rceptNo": "20260318001196",
            "periodEnd": "2025-12-31",
            "fetchedAt": "2026-08-22T12:11:00+09:00",
            "revenueEok": 27224,
            "operatingMargin": 8.80,
            "roeAnnualized": 10.09,
            "debtRatio": 107.38,
            "epsCumulative": 7127,
            "dataQuality": "complete",
            "missingMetrics": [],
        },
    },
    "sources": REPORT_SOURCES,
    "review": {"status": "reviewed", "reviewedAt": BASIS_DATE, "nextReviewAt": NEXT_REVIEW},
    "officialOrder": 0,
}

by_code["329180"] = {
    "code": "329180",
    "name": "HD현대중공업",
    "sector": "조선·해양플랜트",
    "caqm": 90,
    "components": {"moat": 27, "growth": 24, "profitability": 18, "financialHealth": 12, "management": 9},
    "currentPrice": 506000,
    "priceBasisDate": "2026-08-20",
    "priceSource": "공공데이터포털 금융위원회 주식시세정보",
    "reason": "고부가 컨테이너선·LNG선 수주잔고와 방산·해양플랜트 포트폴리오가 조선 업사이클의 이익 성장을 지지한다.",
    "risk": "후판·인건비 상승, 공정 지연과 HD한국조선해양과의 밸류체인·지분 구조 중복을 함께 점검해야 한다.",
    "financials": {
        "revenue2025": 175806,
        "operatingMargin2025": 11.59,
        "roe2025": 15.15,
        "debtRatio2025": 180.07,
        "eps2025": 13486,
        "latestReport": {
            "reportYear": 2025,
            "reportCode": "11011",
            "periodLabel": "2025년 사업보고서",
            "fsDiv": "CFS",
            "rceptNo": "20260320000859",
            "periodEnd": "2025-12-31",
            "fetchedAt": "2026-08-22T12:11:00+09:00",
            "revenueEok": 175806,
            "operatingMargin": 11.59,
            "roeAnnualized": 15.15,
            "debtRatio": 180.07,
            "epsCumulative": 13486,
            "dataQuality": "complete",
            "missingMetrics": [],
        },
    },
    "sources": REPORT_SOURCES,
    "review": {"status": "reviewed", "reviewedAt": BASIS_DATE, "nextReviewAt": NEXT_REVIEW},
    "officialOrder": 0,
}

for idx, code in enumerate(ORDER, start=1):
    by_code[code]["officialOrder"] = idx

companies_data["companies"] = [by_code[code] for code in ORDER]
companies_data["schemaVersion"] = 2
companies_data["basisDate"] = BASIS_DATE
companies_data["universeSize"] = 20
fs = companies_data.get("financialDataSummary", {})
fs["checkedAt"] = "2026-08-22T12:11:00+09:00"
fs["asOf"] = BASIS_DATE
fs["companyCount"] = 20
companies_data["financialDataSummary"] = fs

# --- 4. manual-overrides.json: officialFinalVm updates ----------------------
overrides = overrides_data["companies"]
for code, vm in VM_UPDATES.items():
    entry = overrides[code]
    entry["officialFinalVm"] = vm
    entry["officialVmSource"] = SOURCE
    entry["status"] = "reviewed"
    entry["epsReviewedAt"] = BASIS_DATE
    entry["multipleReviewedAt"] = BASIS_DATE

overrides["278470"] = {
    "forwardEps3y": 11600,
    "historicalPer5y": 25,
    "overseasPeerPer": 28,
    "discountRate": 10,
    "componentOverrides": {},
    "issueOverrides": {},
    "status": "reviewed",
    "analystNote": "메디큐브·에이지알의 해외 확장과 뷰티 디바이스 성장성을 반영하되 채널 재고와 신제품 주기 리스크를 할인한다.",
    "officialFinalVm": 476100,
    "officialVmSource": SOURCE,
    "epsReviewedAt": BASIS_DATE,
    "multipleReviewedAt": BASIS_DATE,
}
overrides["161890"] = {
    "forwardEps3y": 9980,
    "historicalPer5y": 16,
    "overseasPeerPer": 18,
    "discountRate": 11,
    "componentOverrides": {},
    "issueOverrides": {},
    "status": "reviewed",
    "analystNote": "국내외 ODM 생산능력 확장과 고객 다변화를 반영하되 해외 법인 수익성 정상화 속도를 할인한다.",
    "officialFinalVm": 107700,
    "officialVmSource": SOURCE,
    "epsReviewedAt": BASIS_DATE,
    "multipleReviewedAt": BASIS_DATE,
}
overrides["329180"] = {
    "forwardEps3y": 21580,
    "historicalPer5y": 10,
    "overseasPeerPer": 10,
    "discountRate": 12,
    "componentOverrides": {},
    "issueOverrides": {},
    "status": "reviewed",
    "analystNote": "고부가 선종 수주잔고와 방산·해양플랜트 포트폴리오를 반영하되 조선 경기민감도와 HD한국조선해양과의 밸류체인 중복을 할인한다.",
    "officialFinalVm": 391000,
    "officialVmSource": SOURCE,
    "epsReviewedAt": BASIS_DATE,
    "multipleReviewedAt": BASIS_DATE,
}

overrides_data["schemaVersion"] = 5
overrides_data["basisDate"] = BASIS_DATE
overrides_data["status"] = "reviewed"
overrides_data["reviewPolicy"] = {
    **overrides_data.get("reviewPolicy", {}),
    "nextReviewAt": NEXT_REVIEW,
}

# --- 5. officialMaster: changelog + candidates -------------------------------
overrides_data["officialMaster"] = {
    "version": SOURCE,
    "basisDate": BASIS_DATE,
    "changes": [
        {"code": "298040", "title": "효성중공업 VM 2028E EPS 기준 재계산", "detail": "2,611,100원 → 2,547,600원"},
        {"code": "267260", "title": "HD현대일렉트릭 VM 2028E EPS 기준 재계산", "detail": "681,200원 → 649,400원"},
        {"code": "010120", "title": "LS ELECTRIC 후보군 신규 편입", "detail": "CAQM 87 · Final VM 130,600원 (2028E EPS 기준 재계산). TOP20 20위 산정되었으나 파마리서치 유지로 후보군으로 조정"},
        {"code": "329180", "title": "HD현대중공업 TOP20 신규 편입", "detail": "CAQM 90 · Final VM 391,000원"},
        {"code": "278470", "title": "에이피알 TOP20 신규 편입", "detail": "CAQM 93 · Final VM 476,100원 / 후보군에서 복귀"},
        {"code": "161890", "title": "한국콜마 TOP20 신규 편입", "detail": "CAQM 93 · Final VM 107,700원 / 후보군에서 복귀"},
        {"code": "105560", "title": "KB금융 VM 갱신", "detail": "186,000원 → 198,844원"},
        {"code": "009540", "title": "HD한국조선해양 TOP20 제외 → 후보군 이동", "detail": "HD현대중공업과 지분 중복(69~75% 보유 지주사 관계), CAQM 95(잠정치, 정식 재산정 보류) · VM 530,000원"},
        {"code": "214150", "title": "클래시스 TOP20 탈락 → 후보군 이동", "detail": "CAQM 85로 하락(재검토 결과), VM 54,000원"},
        {"code": "005380", "title": "현대차 TOP20·후보군 완전 제외", "detail": "CAQM 68로 재산정(관세 타격 반영), 후보군 기준(80점) 미달"},
        {"code": "214450", "title": "파마리서치 TOP20 유지", "detail": "2026-08-22 스펙 문서 표에서 누락되어 있었으나 삭제 사유가 없어 TOP20을 유지함. CAQM·VM 변경 없음"},
        {"code": "", "title": "할인기간 원칙(13장) 소급 적용", "detail": "대다수 종목 VM 재계산, 평균 약 -9% 조정. 현재가 대비 괴리율은 다음 세션에서 재계산 예정"},
    ],
    "candidates": [
        {"code": "009540", "name": "HD한국조선해양", "caqm": 95, "finalVm": 530000, "note": "TOP20에서 이동. HD현대중공업과 지분 중복으로 CAQM 정식 재산정 보류 중(잠정치)."},
        {"code": "010120", "name": "LS ELECTRIC", "caqm": 87, "finalVm": 130600, "note": "전력기기 3사 2028E EPS 재계산 반영. TOP20 20위권이었으나 파마리서치 유지로 후보군에 배치."},
        {"code": "319660", "name": "PSK(피에스케이)", "caqm": 86, "finalVm": 183900, "note": "반도체 후공정 세정·건식 식각 장비 점유율과 고객 CAPEX 지속성을 재검토합니다."},
        {"code": "041830", "name": "인바디", "caqm": 86, "finalVm": None, "note": "TOP20 진입 전까지 VM은 산정하지 않는 운영 원칙에 따라 VM 산정 예정으로 표기합니다."},
        {"code": "052690", "name": "한국전력기술", "caqm": 85, "finalVm": 67980, "note": "원전 수주 가시성과 프로젝트 일정을 재검토합니다."},
        {"code": "214150", "name": "클래시스", "caqm": 85, "finalVm": 54000, "note": "TOP20 탈락. 국내 장비·소모품 매출 둔화와 브라질 유통 전환 초기 실적을 재검토합니다."},
        {"code": "340570", "name": "티앤엘", "caqm": 84, "finalVm": None, "note": "TOP20 진입 전까지 VM은 산정하지 않는 운영 원칙에 따라 VM 산정 예정으로 표기합니다."},
        {"code": "060720", "name": "KH바텍", "caqm": 82, "finalVm": 15000, "note": "폴더블 힌지 점유율과 고객 다변화를 재검토합니다."},
        {"code": "095340", "name": "ISC", "caqm": 80, "finalVm": None, "note": "TOP20 진입 전까지 VM은 산정하지 않는 운영 원칙에 따라 VM 산정 예정으로 표기합니다."},
    ],
}

dump(COMPANIES_PATH, companies_data)
dump(OVERRIDES_PATH, overrides_data)
print("Applied 2026-08-22 master update.")
