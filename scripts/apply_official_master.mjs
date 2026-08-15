import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const companiesPath = path.join(root, 'data', 'companies.json')
const overridesPath = path.join(root, 'data', 'manual-overrides.json')
const screenerPath = path.join(root, 'public', 'data', 'screener.json')

const readJson = (target) => JSON.parse(fs.readFileSync(target, 'utf8'))
const writeJson = (target, value) => fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, 'utf8')

const companyData = readJson(companiesPath)
const overridesData = readJson(overridesPath)
const screenerData = readJson(screenerPath)
const resetFromScreener = process.argv.includes('--reset-from-screener')
const oldCompanies = new Map(companyData.companies.map((company) => [company.code, company]))
const screenerCompanies = new Map(screenerData.companies.map((company) => [company.code, company]))

const master = [
  { code: '058470', name: '리노공업', sector: '반도체 장비·부품', caqm: 96, finalVm: 69300, components: [30, 24, 20, 15, 7], reason: '반도체 테스트 소켓과 핀의 정밀가공 기술, 높은 고객 전환비용과 장기 수익성이 핵심 해자다.', risk: '고객 집중과 반도체 테스트 수요 변동, 증설 이후 가동률을 점검해야 한다.' },
  { code: '267260', name: 'HD현대일렉트릭', sector: '전력기기', caqm: 94, finalVm: 681200, components: [29, 25, 19, 13, 8], reason: '북미 전력망 교체와 데이터센터 전력 수요, 장기 수주잔고가 실적 가시성을 높인다.', risk: '변압기 업황 정상화, 원재료와 생산능력 확대에 따른 마진 변동이 핵심 위험이다.' },
  { code: '207940', name: '삼성바이오로직스', sector: '바이오 CDMO', caqm: 95, finalVm: 1652000, components: [29, 25, 19, 14, 8], reason: '글로벌 대형 생산능력과 규제기관 승인 이력, 장기 고객관계가 높은 진입장벽을 만든다.', risk: '대규모 증설 가동률, 고객 집중과 바이오의약품 가격 정책을 추적해야 한다.' },
  { code: '140860', name: '파크시스템스', sector: '반도체 계측', caqm: 94, finalVm: 243600, components: [30, 24, 19, 14, 7], reason: '산업용 AFM의 비접촉식 계측 기술과 고객 인증, 반복 수요가 독점적 지위를 지지한다.', risk: '고평가 부담, 반도체 고객 CAPEX와 신규 장비 채택 속도를 점검해야 한다.' },
  { code: '192820', name: '코스맥스', sector: '화장품 ODM', caqm: 92, finalVm: 288000, components: [28, 25, 18, 13, 8], reason: '글로벌 화장품 ODM 개발력과 고객 다변화, K-뷰티 수출 확대가 장기 성장의 축이다.', risk: '중국 사업 변동성, 고객사 재고와 원재료·인건비 상승에 따른 마진 위험이 있다.' },
  { code: '012450', name: '한화에어로스페이스', sector: '방산', caqm: 94, finalVm: 977700, components: [29, 25, 18, 13, 9], reason: '방산 수출 레퍼런스와 대규모 수주잔고, 엔진·지상체계 기술 장벽이 성장 가시성을 높인다.', risk: '계약 일정 지연, 원가 상승과 지정학적·정책 변수에 민감하다.' },
  { code: '003230', name: '삼양식품', sector: '글로벌 식품', caqm: 95, finalVm: 1339100, components: [29, 25, 20, 14, 7], reason: '불닭 브랜드의 글로벌 확장과 생산능력 증설이 높은 성장성과 수익성을 뒷받침한다.', risk: '단일 브랜드 집중, 원재료·환율 변동과 해외 소비 트렌드 변화가 위험이다.' },
  { code: '000810', name: '삼성화재', sector: '보험', caqm: 94, finalVm: 644500, components: [28, 22, 19, 15, 10], reason: '우수한 보험수익성·자본력과 안정적 투자자산, 지속적인 주주환원이 프리미엄 요인이다.', risk: '손해율, K-ICS, CSM 전개와 투자자산 평가손익을 함께 점검해야 한다.' },
  { code: '298040', name: '효성중공업', sector: '전력기기', caqm: 93, finalVm: 2611100, components: [28, 25, 19, 12, 9], reason: '고압 전력기기 공급부족과 북미 생산거점, 장기 수주잔고가 이익 성장을 지지한다.', risk: '프로젝트 원가와 납기, 전력기기 증설 경쟁에 따른 사이클 정상화 위험이 있다.' },
  { code: '105560', name: 'KB금융', sector: '금융', caqm: 94, finalVm: 186000, components: [29, 21, 19, 15, 10], reason: '은행·카드·증권·보험의 다각화, 우수한 자본력과 실제 소각을 포함한 주주환원이 강점이다.', risk: '부동산 PF, 신용비용과 CET1·NPL·연체율 변화를 점검해야 한다.' },
  { code: '214450', name: '파마리서치', sector: '바이오·의료미용', caqm: 95, finalVm: 540000, components: [28, 25, 20, 14, 8], price: 375500, reason: '리쥬란 브랜드와 PN 제조 역량, 의료진 채널 및 해외 확장이 높은 성장성과 마진을 만든다.', risk: '핵심 제품 집중, 의료미용 규제와 해외 유통 실행력이 주요 위험이다.' },
  { code: '214150', name: '클래시스', sector: '의료미용기기', caqm: 94, finalVm: 77000, components: [28, 24, 20, 15, 7], reason: '집속초음파 장비와 소모품 반복매출, 글로벌 유통 확장이 높은 수익성을 지지한다.', risk: '경쟁 장비 확산, 국가별 인허가와 해외 마케팅 비용을 점검해야 한다.' },
  { code: '009540', name: 'HD한국조선해양', sector: '조선', caqm: 92, finalVm: 524000, components: [28, 25, 18, 13, 8], reason: '고부가 선종 수주와 장기 인도 슬롯, 계열 조선소 포트폴리오가 이익 가시성을 높인다.', risk: '후판 가격, 공정 지연과 환율·해운 사이클 변화가 주요 위험이다.' },
  { code: '000270', name: '기아', sector: '자동차', caqm: 91, finalVm: 163300, components: [28, 21, 18, 15, 9], reason: '글로벌 브랜드와 생산 유연성, 높은 현금창출 및 주주환원이 장기 복리의 기반이다.', risk: '관세·환율, 전기차 전환 비용과 주요 시장 인센티브 확대를 점검해야 한다.' },
  { code: '138040', name: '메리츠금융지주', sector: '금융', caqm: 93, finalVm: 121600, components: [28, 22, 19, 14, 10], reason: '높은 자본효율과 ROE, 실제 자사주 매입·소각 중심의 명확한 자본배분이 강점이다.', risk: '보험 손해율, 증권·부동산 금융 익스포저와 규제자본 변동이 핵심 위험이다.' },
  { code: '086790', name: '하나금융지주', sector: '금융', caqm: 92, finalVm: 155000, components: [27, 21, 19, 15, 10], reason: '안정적 은행 이익과 비은행 확장, 개선된 자본비율과 주주환원이 기업가치를 지지한다.', risk: '환율 민감도, 신용비용과 CET1·NPL·연체율을 지속 점검해야 한다.' },
  { code: '000660', name: 'SK하이닉스', sector: '반도체', caqm: 93, finalVm: 2913000, components: [29, 25, 19, 13, 7], reason: 'HBM 기술 리더십과 선도 고객 인증이 AI 메모리 성장의 핵심 경쟁우위다.', risk: '메모리 가격 사이클과 대규모 CAPEX, 경쟁사의 HBM 수율 개선이 핵심 위험이다.' },
  { code: '030200', name: 'KT', sector: '통신·AX', caqm: 89, finalVm: 68500, components: [27, 19, 18, 15, 10], reason: '통신 현금흐름과 데이터센터·클라우드·AX 확장, 주주환원이 하방을 지지한다.', risk: '통신 규제, CAPEX와 신사업 실행 속도 및 인건비 부담을 점검해야 한다.' },
  { code: '005930', name: '삼성전자', sector: '반도체·전자', caqm: 91, finalVm: 389000, components: [29, 23, 17, 15, 7], reason: '메모리·파운드리·모바일·가전의 규모와 재무안정성이 장기 경쟁력의 기반이다.', risk: 'HBM 고객 인증, 파운드리 수율과 메모리 피크 이익의 지속성을 보수적으로 봐야 한다.' },
  { code: '005380', name: '현대차', sector: '자동차', caqm: 89, finalVm: 467900, components: [27, 20, 18, 15, 9], reason: '글로벌 생산·브랜드 포트폴리오, 하이브리드 경쟁력과 주주환원 확대가 강점이다.', risk: '관세·환율, 전기차 투자 회수와 금융부문 신용비용이 주요 변수다.' },
]

const componentObject = ([moat, growth, profitability, financialHealth, management]) => ({
  moat, growth, profitability, financialHealth, management,
})

const createFinancials = (screened) => {
  const metrics = screened?.metrics || {}
  return {
    revenue2025: Number.isFinite(metrics.revenue) ? Math.round(metrics.revenue / 100000000) : null,
    operatingMargin2025: metrics.operatingMarginPct ?? null,
    roe2025: metrics.roePct ?? null,
    debtRatio2025: metrics.debtRatioPct ?? null,
    eps2025: null,
  }
}

companyData.schemaVersion = 2
companyData.basisDate = '2026-08-13'
companyData.universeSize = 20
companyData.companies = master.map((item, index) => {
  const existing = oldCompanies.get(item.code)
  const screened = screenerCompanies.get(item.code)
  return {
    ...(existing || {}),
    code: item.code,
    officialOrder: index + 1,
    name: item.name,
    sector: item.sector,
    caqm: item.caqm,
    components: componentObject(item.components),
    currentPrice: item.price ?? (resetFromScreener ? null : existing?.currentPrice) ?? screened?.currentPrice ?? screened?.metrics?.price,
    priceBasisDate: item.price ? '2026-08-13' : (resetFromScreener ? '2026-08-10' : existing?.priceBasisDate ?? companyData.priceBasisDate ?? '2026-08-10'),
    priceSource: existing?.priceSource || '공공데이터포털 금융위원회 주식시세정보',
    reason: item.reason,
    risk: item.risk,
    financials: resetFromScreener ? createFinancials(screened) : existing?.financials || createFinancials(screened),
    sources: resetFromScreener ? [
      { label: 'OpenDART 정기보고서', url: 'https://opendart.fss.or.kr/' },
      { label: 'KRX 정보데이터시스템', url: 'https://data.krx.co.kr/' },
    ] : existing?.sources || [
      { label: 'OpenDART 정기보고서', url: 'https://opendart.fss.or.kr/' },
      { label: 'KRX 정보데이터시스템', url: 'https://data.krx.co.kr/' },
    ],
    review: {
      status: 'reviewed',
      reviewedAt: '2026-08-13',
      nextReviewAt: '2026-09-30',
    },
  }
})

const newAssumptions = {
  '140860': { forwardEps3y: 12969, historicalPer5y: 25, overseasPeerPer: 28, discountRate: 10, analystNote: '산업용 AFM 기술 해자와 글로벌 계측기업 비교를 반영한다.' },
  '192820': { forwardEps3y: 21882, historicalPer5y: 18, overseasPeerPer: 22, discountRate: 11, analystNote: '글로벌 ODM 성장과 중국 사업 변동성을 함께 반영한다.' },
  '214150': { forwardEps3y: 4659, historicalPer5y: 22, overseasPeerPer: 25, discountRate: 10, analystNote: '소모품 반복매출과 해외 인허가 확장을 반영한다.' },
  '000270': { forwardEps3y: 31905, historicalPer5y: 7, overseasPeerPer: 7, discountRate: 11, analystNote: '자동차 정상화 이익과 글로벌 완성차 배수를 보수적으로 적용한다.' },
  '086790': { valuationModel: 'bank_pbr', normalizedBps: 163158, targetPbr: 0.95, normalizedEps: 19375, crossCheckPer: 8, roundingUnit: 1000, analystNote: '정상화 BPS×PBR 0.95배를 주평가하고 정상화 EPS×PER 8배로 교차검증한다.' },
  '030200': { forwardEps3y: 9368, historicalPer5y: 10, overseasPeerPer: 10, discountRate: 11, analystNote: '통신 현금흐름과 AX 성장, 규제 위험을 함께 반영한다.' },
  '005380': { forwardEps3y: 79989, historicalPer5y: 8, overseasPeerPer: 8, discountRate: 11, analystNote: '자동차 정상화 이익과 글로벌 완성차 배수를 적용한다.' },
}

overridesData.schemaVersion = 5
overridesData.basisDate = '2026-08-13'
overridesData.status = 'reviewed'
overridesData.description = '복리자산 2045 국내 TOP20 공식 마스터입니다. CAQM은 해자·성장성·수익성·재무건전성·경영진과 주주환원을 평가하고, 공식 Final VM과 재현 가능한 모델 산출값은 분리해 관리합니다.'
overridesData.reviewPolicy = {
  ...(overridesData.reviewPolicy || {}),
  nextReviewAt: '2026-09-30',
}
overridesData.officialMaster = {
  version: '국내 TOP20 공식 마스터 · 2026.08.13',
  basisDate: '2026-08-13',
  changes: [
    { code: '214450', title: '파마리서치 TOP20 신규 편입', detail: 'CAQM 95 · Final VM 540,000원 / 에이피알 후보군 이동' },
    { code: '105560', title: 'KB금융 TOP20 신규 편입', detail: 'CAQM 94 · Final VM 186,000원 / 한국콜마 후보군 이동' },
    { code: '086790', title: '하나금융지주 VM 상향', detail: '122,500원 → 155,000원' },
    { code: '005930', title: '삼성전자 정상화 VM 반영', detail: '메모리 전용 기준 · Final VM 389,000원' },
    { code: '000660', title: 'SK하이닉스 정상화 VM 반영', detail: '메모리 전용 기준 · Final VM 2,913,000원' },
  ],
  candidates: [
    { code: '042700', name: '한미반도체', caqm: 94, finalVm: 205000, note: '반도체 장비 해자와 HBM CAPEX 민감도를 재검토합니다.' },
    { code: '319660', name: 'PSK', caqm: 91, finalVm: 164000, note: '공정 장비 점유율과 고객 CAPEX 지속성을 재검토합니다.' },
    { code: '052690', name: '한국전력기술', caqm: 91, finalVm: 73000, note: '원전 수주 가시성과 프로젝트 일정을 재검토합니다.' },
    { code: '034020', name: '두산에너빌리티', caqm: 90, finalVm: 44400, note: '원전·가스터빈 수주와 재무 개선을 재검토합니다.' },
    { code: '278470', name: '에이피알', caqm: null, finalVm: null, note: '파마리서치 편입에 따라 후보군에서 CAQM·VM을 재검토합니다.' },
    { code: '161890', name: '한국콜마', caqm: null, finalVm: null, note: 'KB금융 편입에 따라 후보군에서 CAQM·VM을 재검토합니다.' },
  ],
}

overridesData.companies = Object.fromEntries(master.map((item) => {
  const previous = overridesData.companies[item.code] || newAssumptions[item.code]
  if (!previous) throw new Error(`Missing VM assumptions for ${item.code}`)
  const assumption = {
    ...previous,
    ...(newAssumptions[item.code] || {}),
    officialFinalVm: item.finalVm,
    officialVmSource: '복리자산 2045 국내 TOP20 공식 마스터 · 2026.08.13',
    status: 'reviewed',
    epsReviewedAt: '2026-08-13',
    multipleReviewedAt: '2026-08-13',
  }
  if (item.code === '000810') Object.assign(assumption, { normalizedBps: 500000, targetPbr: 1.05, csmSotpAdjustment: 119500, normalizedEps: 80562, crossCheckPer: 8, roundingUnit: 100 })
  if (item.code === '138040') Object.assign(assumption, { normalizedBps: 80000, targetPbr: 1.25, normalizedEps: 17900, crossCheckPer: 8, primaryWeight: 0.5, roundingUnit: 100 })
  return [item.code, assumption]
}))

writeJson(companiesPath, companyData)
writeJson(overridesPath, overridesData)
console.log(`Applied official master: ${master.length} companies, ${overridesData.officialMaster.candidates.length} candidates${resetFromScreener ? ' (financials reset from screener)' : ''}`)
