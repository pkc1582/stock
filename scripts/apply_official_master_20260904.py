#!/usr/bin/env python3
"""Apply the 2026-09-04 official TOP20 master update.

One-off content update script (mirrors apply_master_20260822.py). Replaces
the TOP20 roster with the CAQM-ranked list finalized on 2026-09-04, updates
VM assumptions/official Final VM values, records the two flagged
qualitative warnings, and refreshes the officialMaster candidate pool.

Run scripts/build_dataset.py afterwards to regenerate public/data/latest.json.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANIES_PATH = ROOT / "data" / "companies.json"
OVERRIDES_PATH = ROOT / "data" / "manual-overrides.json"

SOURCE_PREFIX = "CAR 국내 TOP20 공식 마스터 · 2026.09.04"
BASIS_DATE = "2026-09-04"
NEXT_REVIEW = "2026-09-30"

# rank, code, name, sector, caqm, finalVm, vmDate, warning
MASTER = [
    (1, "058470", "리노공업", "반도체소부장", 94, 93900, "2026-05-21", None),
    (2, "105560", "KB금융", "금융-은행", 91, 186000, "2026-08-28", None),
    (3, "039490", "키움증권", "금융-증권", 91, 340000, "2026-08-28", None),
    (4, "000810", "삼성화재", "금융-보험", 91, 785000, "2026-08-28", None),
    (5, "055550", "신한지주", "금융-은행", 90, 113000, "2026-08-28", None),
    (6, "079550", "LIG디펜스앤에어로스페이스", "방산", 89, 758100, "2026-08-07", None),
    (7, "006800", "미래에셋증권", "금융-증권", 89, 35000, "2026-08-28", None),
    (8, "298040", "효성중공업", "전력기기", 88, 3924900, "2026-08-21", None),
    (9, "214450", "파마리서치", "화장품/의료기기", 86, 417800, "2026-09-03", None),
    (10, "003690", "코리안리재보험", "금융-보험", 87, 18000, "2026-08-28", None),
    (11, "267260", "HD현대일렉트릭", "전력기기", 85, 1298500, "2026-08-21", None),
    (12, "010120", "LS ELECTRIC", "전력기기", 84, 186700, "2026-07-23", None),
    (13, "000660", "SK하이닉스", "반도체", 83, 493300, "2026-08-24", None),
    (14, "005930", "삼성전자", "반도체", 83, 493300, "2026-08-25", None),
    (15, "214150", "클래시스", "화장품/미용기기", 81, 106600, "2026-08-19", "최대주주 베인캐피탈 경영권 매각 추진 이력, 잔여지분 54% 보유중"),
    (16, "033780", "KT&G", "소비재", 80, 152500, "2026-08-06", None),
    (17, "021240", "코웨이", "렌탈구독", 80, 116900, "2026-08-07", None),
    (18, "012450", "한화에어로스페이스", "방산", 80, 1433700, "2026-08-21", None),
    (19, "052690", "한전기술", "원전", 78, 165400, "2026-08-07", "배당 삭감 확인(2025→2026E)"),
    (20, "140860", "파크시스템스", "반도체소부장", 76, 355800, "2026-07-31", None),
]

COMPONENT_WEIGHTS = {"moat": 30, "growth": 25, "profitability": 20, "financialHealth": 15, "management": 10}


def component_split(caqm):
    parts = {}
    total = 0
    for key, weight in COMPONENT_WEIGHTS.items():
        value = round(caqm * weight / 100)
        parts[key] = value
        total += value
    parts["moat"] += caqm - total
    return parts


REASON = {
    "058470": "반도체 테스트 소켓과 핀의 정밀가공 기술, 높은 고객 전환비용과 장기 수익성이 핵심 해자다.",
    "105560": "은행·카드·증권·보험의 다각화, 우수한 자본력과 실제 소각을 포함한 주주환원이 강점이다.",
    "039490": "리테일·해외주식 브로커리지 점유율과 낮은 비용구조, 자기자본 확대에 따른 이익 레버리지가 강점이다.",
    "000810": "우수한 보험수익성·자본력과 안정적 투자자산, 지속적인 주주환원이 프리미엄 요인이다.",
    "055550": "은행·카드·증권의 균형 잡힌 포트폴리오와 자본배분 규율, 안정적 주주환원이 강점이다.",
    "079550": "정밀유도무기·항공전자 수출 레퍼런스와 수주잔고 확대가 성장 가시성을 높인다.",
    "006800": "해외법인 네트워크와 IB·자산운용 다각화, 자기자본 활용도가 강점이다.",
    "298040": "고압 전력기기 공급부족과 북미 생산거점, 장기 수주잔고가 이익 성장을 지지한다.",
    "214450": "리쥬란 브랜드와 PN 제조 역량, 의료진 채널 및 해외 확장이 높은 성장성과 마진을 만든다.",
    "003690": "국내 유일 재보험사로서의 과점적 지위와 해외 원수보험 네트워크가 안정적 이익 기반이다.",
    "267260": "북미 전력망 교체와 데이터센터 전력 수요, 장기 수주잔고가 실적 가시성을 높인다.",
    "010120": "전력기기·자동화 포트폴리오와 해외 생산거점 확대가 안정적 수주잔고를 지지한다.",
    "000660": "HBM 기술 리더십과 선도 고객 인증이 AI 메모리 성장의 핵심 경쟁우위다.",
    "005930": "메모리·파운드리·모바일·가전의 규모와 재무안정성이 장기 경쟁력의 기반이다.",
    "214150": "집속초음파 장비와 소모품 반복매출, 글로벌 유통 확장이 높은 수익성을 지지한다.",
    "033780": "담배 본업의 안정적 현금흐름과 건강기능식품·해외 담배 성장이 방어적 복리자산을 만든다.",
    "021240": "정수기·공기청정기 렌탈 구독 모델의 반복매출과 해외 확장이 안정적 현금창출을 지지한다.",
    "012450": "방산 수출 레퍼런스와 대규모 수주잔고, 엔진·지상체계 기술 장벽이 성장 가시성을 높인다.",
    "052690": "원전 설계·엔지니어링 독점적 지위와 신규 원전 수주 파이프라인이 장기 성장 동력이다.",
    "140860": "산업용 AFM의 비접촉식 계측 기술과 고객 인증, 반복 수요가 독점적 지위를 지지한다.",
}

RISK = {
    "058470": "고객 집중과 반도체 테스트 수요 변동, 증설 이후 가동률을 점검해야 한다.",
    "105560": "부동산 PF, 신용비용과 CET1·NPL·연체율 변화를 점검해야 한다.",
    "039490": "거래대금·금리 민감도가 높고 해외주식 관련 규제·환율 변동이 이익 변동성을 키운다.",
    "000810": "손해율, K-ICS, CSM 전개와 투자자산 평가손익을 함께 점검해야 한다.",
    "055550": "신용비용, 부동산 PF 익스포저와 CET1·연체율 변화를 지속 점검해야 한다.",
    "079550": "계약 일정 지연과 원가 상승, 지정학적·정책 변수에 민감하다.",
    "006800": "해외법인 손익 변동성과 시장 변동성에 따른 트레이딩·평가손익 리스크가 있다.",
    "298040": "프로젝트 원가와 납기, 전력기기 증설 경쟁에 따른 사이클 정상화 위험이 있다.",
    "214450": "핵심 제품 집중, 의료미용 규제와 해외 유통 실행력이 주요 위험이다.",
    "003690": "해외 원수보험 손해율과 대형 재해 손실, 환율 변동이 이익 변동성을 키운다.",
    "267260": "변압기 업황 정상화, 원재료와 생산능력 확대에 따른 마진 변동이 핵심 위험이다.",
    "010120": "원자재 가격과 해외 수주 경쟁, 전력기기 사이클 정상화가 주요 위험이다.",
    "000660": "메모리 가격 사이클과 대규모 CAPEX, 경쟁사의 HBM 수율 개선이 핵심 위험이다.",
    "005930": "HBM 고객 인증, 파운드리 수율과 메모리 피크 이익의 지속성을 보수적으로 봐야 한다.",
    "214150": "경쟁 장비 확산, 국가별 인허가와 해외 마케팅 비용을 점검해야 한다. 최대주주 베인캐피탈의 잔여지분 매각 가능성도 주가 변동 요인이다.",
    "033780": "궐련 판매량 장기 감소 추세와 해외 규제, 신사업 투자 회수 속도가 위험이다.",
    "021240": "국내 렌탈 시장 경쟁 심화와 해외 사업 확장 비용, 금리 변동에 따른 판매 방식 손익 변동이 있다.",
    "012450": "계약 일정 지연, 원가 상승과 지정학적·정책 변수에 민감하다.",
    "052690": "배당 삭감 이력이 있어 주주환원 지속성을 점검해야 하며, 원전 정책·수주 일정 지연이 위험이다.",
    "140860": "고평가 부담, 반도체 고객 CAPEX와 신규 장비 채택 속도를 점검해야 한다.",
}

# code -> (valuationModel, assumption fields)
ASSUMPTIONS = {
    "058470": None,  # reuse existing override
    "105560": None,
    "000810": None,
    "298040": None,
    "214450": None,
    "267260": None,
    "140860": None,
    "000660": None,
    "005930": None,
    "012450": None,
    "214150": None,
    "039490": ("bank_pbr", {"normalizedBps": 309091, "targetPbr": 1.1, "normalizedEps": 35789, "crossCheckPer": 9.5, "roundingUnit": 1000}),
    "055550": ("bank_pbr", {"normalizedBps": 129884, "targetPbr": 0.87, "normalizedEps": 14125, "crossCheckPer": 8, "roundingUnit": 1000}),
    "079550": ("standard_per", {"forwardEps3y": 60648, "historicalPer5y": 12.5, "overseasPeerPer": 12.5, "discountRate": 11}),
    "006800": ("bank_pbr", {"normalizedBps": 31818, "targetPbr": 1.1, "normalizedEps": 3684, "crossCheckPer": 9.5, "roundingUnit": 100}),
    "003690": ("insurance_sotp", {"normalizedBps": 18000, "targetPbr": 1.0, "csmSotpAdjustment": 0, "normalizedEps": 1800, "crossCheckPer": 10, "roundingUnit": 100}),
    "010120": ("standard_per", {"forwardEps3y": 14957, "historicalPer5y": 12.5, "overseasPeerPer": 12.5, "discountRate": 11}),
    "033780": ("standard_per", {"forwardEps3y": 12220, "historicalPer5y": 12.5, "overseasPeerPer": 12.5, "discountRate": 10}),
    "021240": ("standard_per", {"forwardEps3y": 9363, "historicalPer5y": 12.5, "overseasPeerPer": 12.5, "discountRate": 10}),
    "052690": ("standard_per", {"forwardEps3y": 13253, "historicalPer5y": 12.5, "overseasPeerPer": 12.5, "discountRate": 10}),
}

CANDIDATES = [
    {"code": "207940", "name": "삼성바이오로직스", "caqm": 73, "finalVm": 1597300, "note": "글로벌 CDMO 대형 증설 가동률과 고객 집중도를 재검토 중인 후보군입니다."},
    {"code": "271560", "name": "오리온", "caqm": 72, "finalVm": 151300, "note": "중국·베트남 등 해외 법인 성장성과 환율 민감도를 재검토 중인 후보군입니다."},
    {"code": "071050", "name": "한국금융지주", "caqm": 63.7, "finalVm": None, "note": "VM 재계산이 필요한 후보군입니다."},
]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


companies_data = load(COMPANIES_PATH)
overrides_data = load(OVERRIDES_PATH)

old_by_code = {c["code"]: c for c in companies_data["companies"]}

new_companies = []
for rank, code, name, sector, caqm, final_vm, vm_date, warning in MASTER:
    existing = old_by_code.get(code, {})
    components = component_split(caqm)
    entry = {
        "code": code,
        "officialOrder": rank,
        "name": name,
        "sector": sector,
        "caqm": caqm,
        "components": components,
        "currentPrice": final_vm,
        "priceBasisDate": vm_date,
        "priceSource": existing.get("priceSource", "공공데이터포털 금융위원회 주식시세정보"),
        "reason": REASON[code],
        "risk": RISK[code],
        "financials": existing.get("financials", {
            "revenue2025": None,
            "operatingMargin2025": None,
            "roe2025": None,
            "debtRatio2025": None,
            "eps2025": None,
        }),
        "sources": existing.get("sources", [
            {"label": "OpenDART 정기보고서", "url": "https://opendart.fss.or.kr/"},
            {"label": "KRX 정보데이터시스템", "url": "https://data.krx.co.kr/"},
        ]),
        "review": {
            "status": "reviewed",
            "reviewedAt": BASIS_DATE,
            "nextReviewAt": NEXT_REVIEW,
        },
    }
    if warning:
        entry["warning"] = warning
    new_companies.append(entry)

companies_data["schemaVersion"] = 2
companies_data["basisDate"] = BASIS_DATE
companies_data["priceBasisDate"] = BASIS_DATE
companies_data["universeSize"] = 20
companies_data["companies"] = new_companies

overrides_by_code = overrides_data["companies"]
for rank, code, name, sector, caqm, final_vm, vm_date, warning in MASTER:
    spec = ASSUMPTIONS.get(code)
    if spec is None:
        assumption = overrides_by_code.get(code)
        if assumption is None:
            raise SystemExit(f"missing VM assumption to reuse for {code}")
    else:
        model, fields = spec
        assumption = {"valuationModel": model, **fields, "componentOverrides": {}, "issueOverrides": {}}
        overrides_by_code[code] = assumption
    assumption["officialFinalVm"] = final_vm
    assumption["officialVmSource"] = f"{SOURCE_PREFIX} (VM 산출 기준일 {vm_date})"
    assumption["status"] = "reviewed"
    assumption["epsReviewedAt"] = vm_date
    assumption["multipleReviewedAt"] = vm_date

overrides_data["schemaVersion"] = 5
overrides_data["basisDate"] = BASIS_DATE
overrides_data["status"] = "reviewed"
overrides_data["description"] = "CAR 국내 TOP20 공식 마스터입니다. CAQM은 해자·성장성·수익성·재무건전성·경영진과 주주환원을 평가하고, 공식 Final VM과 재현 가능한 모델 산출값은 분리해 관리합니다."
overrides_data["reviewPolicy"] = {
    **overrides_data.get("reviewPolicy", {}),
    "nextReviewAt": NEXT_REVIEW,
}
overrides_data["officialMaster"] = {
    "version": f"국내 TOP20 공식 마스터 · {BASIS_DATE}",
    "basisDate": BASIS_DATE,
    "changes": [
        {"code": "039490", "title": "키움증권 TOP20 신규 편입", "detail": "CAQM 91 · Final VM 340,000원"},
        {"code": "055550", "title": "신한지주 TOP20 신규 편입", "detail": "CAQM 90 · Final VM 113,000원"},
        {"code": "079550", "title": "LIG디펜스앤에어로스페이스 TOP20 신규 편입", "detail": "CAQM 89 · Final VM 758,100원"},
        {"code": "006800", "title": "미래에셋증권 TOP20 신규 편입", "detail": "CAQM 89 · Final VM 35,000원"},
        {"code": "003690", "title": "코리안리재보험 TOP20 신규 편입", "detail": "CAQM 87 · Final VM 18,000원"},
        {"code": "010120", "title": "LS ELECTRIC TOP20 신규 편입", "detail": "CAQM 84 · Final VM 186,700원"},
        {"code": "214150", "title": "클래시스 TOP20 신규 편입", "detail": "CAQM 81 · Final VM 106,600원 · 최대주주 매각 이력 경고 반영"},
        {"code": "033780", "title": "KT&G TOP20 신규 편입", "detail": "CAQM 80 · Final VM 152,500원"},
        {"code": "021240", "title": "코웨이 TOP20 신규 편입", "detail": "CAQM 80 · Final VM 116,900원"},
        {"code": "052690", "title": "한전기술 TOP20 신규 편입", "detail": "CAQM 78 · Final VM 165,400원 · 배당 삭감 경고 반영"},
        {"code": "207940", "title": "삼성바이오로직스 후보군 이동", "detail": "CAQM 73 · Final VM 1,597,300원"},
        {"code": "192820", "title": "코스맥스 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "003230", "title": "삼양식품 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "138040", "title": "메리츠금융지주 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "278470", "title": "에이피알 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "161890", "title": "한국콜마 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "086790", "title": "하나금융지주 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "000270", "title": "기아 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "030200", "title": "KT TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
        {"code": "329180", "title": "HD현대중공업 TOP20 제외", "detail": "정기 재선정에서 순위 밖으로 이동"},
    ],
    "candidates": CANDIDATES,
}

dump(COMPANIES_PATH, companies_data)
dump(OVERRIDES_PATH, overrides_data)
print(f"Applied 2026-09-04 official master: {len(new_companies)} companies, {len(CANDIDATES)} candidates")
