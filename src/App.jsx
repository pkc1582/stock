import { useEffect, useMemo, useRef, useState } from 'react'

const REPOSITORY_URL = 'https://github.com/psa1582/stock'
const DATA_EDIT_URL = `${REPOSITORY_URL}/edit/main/data/manual-overrides.json`
const DATA_URL = `${import.meta.env.BASE_URL}data/latest.json`
const SCREENER_URL = `${import.meta.env.BASE_URL}data/screener.json`
const CANDIDATE_REVIEW_URL = `${REPOSITORY_URL}/issues/new?template=candidate-review.yml`
const SCREENER_ROW_LIMIT = 200
const WATCHLIST_STORAGE_KEY = 'compound-asset-2045-watchlist'

const companyCodeFromHash = () => {
  const match = window.location.hash.match(/^#company=(\d{6})$/)
  return match?.[1] || null
}

const SCORE_META = [
  { key: 'moat', label: '경쟁우위·해자', max: 30, short: '해자' },
  { key: 'growth', label: '장기 성장성', max: 25, short: '성장' },
  { key: 'profitability', label: '수익성·현금창출', max: 20, short: '수익' },
  { key: 'financialHealth', label: '재무건전성', max: 15, short: '재무' },
  { key: 'management', label: '경영진·주주환원', max: 10, short: '환원' },
]

const number = (value) => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value.replace(/[,%₩원\s]/g, ''))
  return Number.isFinite(parsed) ? parsed : null
}

const firstNumber = (...values) => {
  for (const value of values) {
    const parsed = number(value)
    if (parsed !== null) return parsed
  }
  return null
}

const normalizeText = (value) => String(value ?? '').trim()

function normalizeScreenerStatus(company) {
  const rawStatus = normalizeText(
    company.screenStatus
      || company.status
      || company.eligibility?.status
      || company.screeningStatus
      || company.screenResult
      || company.reviewStatus,
  ).toLowerCase()
  const excluded = company.excluded === true
    || company.eligible === false
    || company.eligibleForPreliminaryScreen === false
    || company.eligibility?.eligible === false
    || /(exclude|ineligible|filtered|제외|탈락)/.test(rawStatus)
  if (excluded) return 'excluded'
  if (company.reviewPending === true || /(review.pending|pending.review|검토.?대기)/.test(rawStatus)) return 'pending'
  if (company.quantPassed === true || /(quant.pass|qualified|screen.pass|정량.?통과)/.test(rawStatus)) return 'passed'
  if (/(pending|candidate|review|대기|후보)/.test(rawStatus)) return 'pending'
  if (/(pass|eligible|active|통과|적격)/.test(rawStatus)) return 'passed'
  return 'unclassified'
}

function normalizeScreenerCompany(company, index) {
  const metrics = company.metrics || company.financials || company.fundamentals || {}
  const marketData = typeof company.market === 'object' ? company.market : {}
  const eligibility = company.eligibility || {}
  const marketCapEok = firstNumber(company.marketCapEok, marketData.marketCapEok)
  const rawMarketCap = firstNumber(company.marketCap, marketData.marketCap, company.mrktTotAmt)
  const exclusionReason = company.exclusionReason
    || company.excludedReason
    || eligibility.reason
    || eligibility.exclusionReason
    || (Array.isArray(company.exclusionReasons) ? company.exclusionReasons.join(', ') : '')

  return {
    code: normalizeText(company.code || company.stockCode || company.ticker || company.srtnCd).padStart(6, '0'),
    name: company.name || company.companyName || company.nameKo || company.itmsNm || `기업 ${index + 1}`,
    market: normalizeText(
      typeof company.market === 'string'
        ? company.market
        : company.marketName || company.marketCategory || company.mrktCtg || marketData.name,
    ) || '미분류',
    sector: company.sector || company.industry || company.industryName || '미분류',
    status: normalizeScreenerStatus(company),
    quantPassed: company.quantPassed === true
      || /(quant.pass|qualified|screen.pass|정량.?통과)/.test(normalizeText(company.screenStatus || company.status).toLowerCase()),
    reviewPending: company.reviewPending === true
      || /(review.pending|pending.review|검토.?대기)/.test(normalizeText(company.screenStatus || company.status).toLowerCase()),
    screenScore: firstNumber(company.screenScore, company.quantScore, company.preScreenScore, company.score),
    currentPrice: firstNumber(company.currentPrice, company.closePrice, company.price, company.clpr, marketData.price),
    marketCapEok: marketCapEok ?? (rawMarketCap !== null && rawMarketCap > 10_000_000 ? rawMarketCap / 100_000_000 : rawMarketCap),
    roe: firstNumber(company.roe, company.roePct, metrics.roe, metrics.roePct, metrics.roeAnnualized),
    operatingMargin: firstNumber(
      company.operatingMargin,
      company.operatingMarginPct,
      metrics.operatingMargin,
      metrics.operatingMarginPct,
    ),
    debtRatio: firstNumber(company.debtRatio, company.debtRatioPct, metrics.debtRatio, metrics.debtRatioPct),
    reportPeriod: company.reportPeriod || company.periodEnd || metrics.periodEnd || metrics.latestReport?.periodEnd || null,
    exclusionReason: normalizeText(exclusionReason),
  }
}

function normalizeScreener(raw) {
  const source = Array.isArray(raw)
    ? raw
    : raw.companies || raw.items || raw.results || raw.data || raw.screener || []
  const companies = Array.isArray(source)
    ? source.filter((company) => company && typeof company === 'object').map(normalizeScreenerCompany)
    : []
  const summary = Array.isArray(raw) ? {} : raw.summary || raw.counts || {}
  const counts = companies.reduce((result, company) => {
    result[company.status] = (result[company.status] || 0) + 1
    return result
  }, {})
  const limitationsSource = Array.isArray(raw) ? [] : raw.dataLimitations || raw.limitations || []

  return {
    companies,
    generatedAt: Array.isArray(raw) ? null : raw.basisDate || raw.asOf || raw.generatedAt || null,
    summary: {
      total: firstNumber(summary.total, summary.universeSize, summary.companyCount, summary.storedRows) ?? companies.length,
      excluded: firstNumber(summary.excluded, summary.excludedCount, summary.knownExcluded) ?? counts.excluded ?? 0,
      passed: firstNumber(summary.passed, summary.quantPassed, summary.quantPassedCount) ?? counts.passed ?? 0,
      pending: firstNumber(summary.pending, summary.reviewPending, summary.reviewPendingCount) ?? counts.pending ?? 0,
    },
    limitations: Array.isArray(limitationsSource)
      ? limitationsSource.map(normalizeText).filter(Boolean)
      : [normalizeText(limitationsSource)].filter(Boolean),
    managedItemChecked: Array.isArray(raw)
      ? false
      : raw.managedItemChecked === true || raw.metadata?.managedItemChecked === true,
  }
}

function normalizeCompany(company, index) {
  const components = company.components || company.scores || company.scoreBreakdown || {}
  const valuation = company.valuation || company.vm || {}
  const market = company.market || {}
  const financials = company.financials || company.metrics2025 || {}
  const rating = typeof company.rating === 'object' ? company.rating : {}
  const currentPrice = firstNumber(company.currentPrice, market.priceKrw, market.price)
  const finalVm = firstNumber(company.finalVm, valuation.finalVm, valuation.fairValue)
  const calculatedGap = currentPrice !== null && finalVm
    ? ((currentPrice - finalVm) / finalVm) * 100
    : null
  const rawScenarios = company.vmScenarios || valuation.scenarios || {}
  const scenarioFor = (key, fallbackVm) => ({
    label: rawScenarios[key]?.label || ({ conservative: '보수', base: '기준', optimistic: '낙관' }[key]),
    finalVm: firstNumber(rawScenarios[key]?.finalVm, fallbackVm),
    assumptions: Array.isArray(rawScenarios[key]?.assumptions) ? rawScenarios[key].assumptions : [],
  })

  return {
    ...company,
    rank: firstNumber(company.rank) ?? index + 1,
    position: firstNumber(company.position) ?? index + 1,
    officialOrder: firstNumber(company.officialOrder) ?? index + 1,
    code: String(company.code || company.ticker || '').padStart(6, '0'),
    name: company.name || company.companyName || company.nameKo || `기업 ${index + 1}`,
    sector: company.sector || company.industry || '미분류',
    caqm: firstNumber(company.caqm, company.cavm, company.caqmScore, company.cavmScore, company.score) ?? 0,
    components: {
      moat: firstNumber(components.moat, components.competitiveAdvantage) ?? 0,
      growth: firstNumber(components.growth) ?? 0,
      profitability: firstNumber(components.profitability, components.profit) ?? 0,
      financialHealth: firstNumber(components.financialHealth, components.financial) ?? 0,
      management: firstNumber(components.management, components.shareholderReturn) ?? 0,
    },
    currentPrice,
    priceBasisDate: company.priceBasisDate || market.priceAsOf || market.asOf || null,
    valuationModel: company.valuationModel || valuation.model || 'standard_per',
    valuationModelLabel: company.valuationModelLabel || valuation.modelLabel || '일반기업 EPS·PER',
    valuationHorizonYears: firstNumber(company.valuationHorizonYears, valuation.horizonYears),
    forwardEps3y: firstNumber(company.forwardEps3y, valuation.forwardEps3Y, valuation.eps3y),
    normalizedEps: firstNumber(company.normalizedEps, valuation.normalizedEps),
    normalizedPer: firstNumber(company.normalizedPer, valuation.normalizedPer, valuation.crossCheckPer),
    normalizedBps: firstNumber(company.normalizedBps, valuation.normalizedBps),
    targetPbr: firstNumber(company.targetPbr, valuation.targetPbr),
    primaryVm: firstNumber(company.primaryVm, valuation.primaryVm),
    crossCheckVm: firstNumber(company.crossCheckVm, valuation.crossCheckVm),
    primaryWeightPct: firstNumber(company.primaryWeightPct, valuation.primaryWeightPct),
    csmSotpAdjustment: firstNumber(company.csmSotpAdjustment, valuation.csmSotpAdjustment),
    cycleChecks: Array.isArray(company.cycleChecks) ? company.cycleChecks : [],
    historicalPer5y: firstNumber(company.historicalPer5y, valuation.historicalPer5y, company.peerPer),
    overseasPeerPer: firstNumber(company.overseasPeerPer, valuation.overseasPeerPer, valuation.peerPer, valuation.comparablePer, company.peerPer),
    overseasCorrectionPer: firstNumber(company.overseasCorrectionPer, valuation.overseasCorrectionPer),
    overseasAdjustmentWeightPct: firstNumber(company.overseasAdjustmentWeightPct, valuation.overseasAdjustmentWeightPct),
    appliedPer: firstNumber(company.appliedPer, valuation.appliedPer, company.peerPer),
    discountRate: firstNumber(company.discountRate, valuation.discountRate),
    finalVm,
    modelCalculatedVm: firstNumber(company.modelCalculatedVm, valuation.modelCalculatedVm),
    officialVmOverride: Boolean(company.officialVmOverride || valuation.officialVmOverride),
    officialVmSource: company.officialVmSource || valuation.officialVmSource || '',
    vmScenarios: {
      conservative: scenarioFor('conservative', finalVm ? finalVm * 0.82 : null),
      base: scenarioFor('base', finalVm),
      optimistic: scenarioFor('optimistic', finalVm ? finalVm * 1.16 : null),
    },
    vmConfidence: company.vmConfidence || valuation.confidence || {
      grade: 'C',
      label: 'EPS 또는 배수 검토 필요',
      reasons: [],
    },
    gapRate: firstNumber(company.gapRate, valuation.gapPct, valuation.gapRate, calculatedGap),
    vmStatus: company.vmStatus || valuation.status || 'draft',
    vmNote: company.vmNote || valuation.note || 'VM 입력값은 검토 중인 가정입니다.',
    ratingStars: typeof company.rating === 'string' ? company.rating : rating.stars || '',
    ratingLabel: rating.label || company.opinion || company.action || '',
    opinion: company.opinion || rating.label || company.action || '',
    reason: company.reason || company.thesis || company.summary || '분석 근거 검토 중',
    risk: company.risk || company.risks || '핵심 위험 요인 검토 중',
    issues: Array.isArray(company.issues) ? company.issues : [],
    financials: {
      revenue2025: firstNumber(financials.revenue2025, financials.revenueKrw100m),
      operatingProfit2025: firstNumber(financials.operatingProfit2025, financials.operatingProfitKrw100m),
      operatingMargin2025: firstNumber(financials.operatingMargin2025, financials.operatingMarginPct),
      roe2025: firstNumber(financials.roe2025, financials.roePct),
      debtRatio2025: firstNumber(financials.debtRatio2025, financials.debtRatioPct),
      eps2025: firstNumber(financials.eps2025, financials.epsKrw),
      latestReport: financials.latestReport && typeof financials.latestReport === 'object'
        ? {
            ...financials.latestReport,
            revenueEok: firstNumber(financials.latestReport.revenueEok),
            operatingMargin: firstNumber(financials.latestReport.operatingMargin),
            roeAnnualized: firstNumber(financials.latestReport.roeAnnualized),
            debtRatio: firstNumber(financials.latestReport.debtRatio),
            epsCumulative: firstNumber(financials.latestReport.epsCumulative),
          }
        : null,
    },
    sources: (company.sources || []).map((source, sourceIndex) => (
      typeof source === 'string'
        ? { label: `근거 자료 ${sourceIndex + 1}`, url: source }
        : source
    )),
    review: {
      status: company.review?.status || company.reviewStatus || '검토 필요',
      reviewedAt: company.review?.reviewedAt || null,
      nextReviewAt: company.review?.nextReviewAt || null,
      basisDates: company.review?.basisDates || {},
    },
  }
}

function normalizeSnapshot(raw) {
  const companies = (raw.companies || raw.top20 || raw.data || [])
    .map(normalizeCompany)
    .sort((a, b) => a.position - b.position)
  const average = companies.length
    ? companies.reduce((sum, company) => sum + company.caqm, 0) / companies.length
    : 0
  const rawChanges = raw.changes || {}

  return {
    ...raw,
    generatedAt: raw.generatedAt || raw.asOf || null,
    basisDate: raw.basisDate || raw.asOf || null,
    dataStatus: raw.dataStatus || '공개 자료 기반 분석 스냅샷',
    changes: {
      comparedAt: rawChanges.comparedAt || null,
      generatedAt: rawChanges.generatedAt || null,
      vm: rawChanges.vm || [],
      gap: rawChanges.gap || [],
      caqm: rawChanges.caqm || rawChanges.cavm || [],
      entrants: rawChanges.entrants || [],
      exits: rawChanges.exits || [],
      reports: rawChanges.reports || [],
      hasMaterialChanges: rawChanges.hasMaterialChanges || false,
    },
    officialMaster: raw.officialMaster || { version: '', changes: [], candidates: [] },
    methodology: raw.methodology || {
      version: raw.methodologyVersion || 'CAQM Official v1.0',
      weights: {},
      formula: '',
      disclaimer: raw.disclaimer || '',
    },
    summary: {
      universeSize: firstNumber(raw.summary?.universeSize, raw.summary?.companyCount) ?? companies.length,
      top20Count: firstNumber(raw.summary?.top20Count) ?? companies.length,
      averageCaqm: firstNumber(
        raw.summary?.averageCaqm,
        raw.summary?.avgCaqm,
        raw.summary?.averageCavm,
        raw.summary?.avgCavm,
      ) ?? average,
      undervaluedCount: firstNumber(raw.summary?.undervaluedCount)
        ?? companies.filter((company) => company.gapRate !== null && company.gapRate < 0).length,
    },
    companies,
  }
}

const formatDate = (value) => {
  if (!value) return '기준일 확인 중'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric', month: 'long', day: 'numeric',
  }).format(date)
}

const formatDateTime = (value) => {
  if (!value) return '확인 중'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

const formatWon = (value) => (
  value === null || value === undefined
    ? '입력 대기'
    : `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value)}원`
)

const formatEok = (value) => (
  value === null || value === undefined
    ? '—'
    : `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value)}억원`
)

const formatPercent = (value, sign = false) => {
  if (value === null || value === undefined) return '—'
  const prefix = sign && value > 0 ? '+' : ''
  return `${prefix}${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1 }).format(value)}%`
}

const formatPerAdjustment = (value) => {
  if (value === null || value === undefined) return '—'
  const prefix = value > 0 ? '+' : ''
  return `${prefix}${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 }).format(value)}배`
}

const gapTone = (gap) => {
  if (gap === null || gap === undefined) return 'neutral'
  if (gap <= -20) return 'attractive'
  if (gap <= -10) return 'fair'
  return 'expensive'
}

const gapLabel = (gap) => {
  if (gap === null || gap === undefined) return 'VM 입력 대기'
  if (gap <= -20) return '적극 검토 구간'
  if (gap <= -10) return '분할 검토 구간'
  return '관찰 구간'
}

function LogoMark() {
  return (
    <span className="logo-mark" aria-hidden="true">
      <span>CAP</span>
    </span>
  )
}

function PwaInstallButton() {
  const [installPrompt, setInstallPrompt] = useState(null)
  const [installed, setInstalled] = useState(false)
  const [showGuide, setShowGuide] = useState(false)

  useEffect(() => {
    const standalone = window.matchMedia('(display-mode: standalone)').matches
      || window.navigator.standalone === true
    setInstalled(standalone)

    const handlePrompt = (event) => {
      event.preventDefault()
      setInstallPrompt(event)
      setShowGuide(false)
    }
    const handleInstalled = () => {
      setInstalled(true)
      setInstallPrompt(null)
      setShowGuide(false)
    }

    window.addEventListener('beforeinstallprompt', handlePrompt)
    window.addEventListener('appinstalled', handleInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', handlePrompt)
      window.removeEventListener('appinstalled', handleInstalled)
    }
  }, [])

  if (installed) return null

  const requestInstall = async () => {
    if (!installPrompt) {
      setShowGuide((visible) => !visible)
      return
    }
    await installPrompt.prompt()
    const choice = await installPrompt.userChoice
    if (choice.outcome === 'accepted') setInstallPrompt(null)
  }

  const isIos = /iphone|ipad|ipod/i.test(window.navigator.userAgent)
  return (
    <div className="pwa-install-control">
      <button type="button" className="button ghost install-app-button" onClick={requestInstall}>
        앱으로 설치 <span aria-hidden="true">＋</span>
      </button>
      {showGuide && (
        <p className="pwa-install-help" role="status">
          {isIos
            ? 'Safari의 공유 버튼을 누른 뒤 ‘홈 화면에 추가’를 선택하세요.'
            : '브라우저 메뉴에서 ‘앱 설치’ 또는 ‘홈 화면에 추가’를 선택하세요.'}
        </p>
      )}
    </div>
  )
}

function PwaUpdateNotice() {
  const [available, setAvailable] = useState(false)

  useEffect(() => {
    const handleUpdate = () => setAvailable(true)
    window.addEventListener('pwa-update-available', handleUpdate)
    return () => window.removeEventListener('pwa-update-available', handleUpdate)
  }, [])

  if (!available) return null
  return (
    <aside className="pwa-update-notice" role="status" aria-live="polite">
      <div><strong>새 데이터와 앱 버전이 준비됐습니다.</strong><span>새로고침하면 바로 적용됩니다.</span></div>
      <button type="button" onClick={() => window.location.reload()}>새로고침</button>
    </aside>
  )
}

function Header({ basisDate }) {
  const [open, setOpen] = useState(false)
  const links = [
    ['#changes', '이번 갱신'],
    ['#matrix', 'CAQM × 가격'],
    ['#top20', 'TOP20'],
    ['#candidates', '후보군'],
    ['#company', '기업 분석'],
    ['#methodology', '평가 기준'],
    ['#screener', '전체시장 스크리너'],
  ]

  return (
    <header className="site-header">
      <div className="header-inner">
        <a className="brand" href="#top" aria-label="CAP 홈">
          <LogoMark />
          <span className="brand-copy">
            <strong>CAP</strong>
            <small>CAQM RESEARCH</small>
          </span>
        </a>
        <button
          className="menu-button"
          type="button"
          aria-label="메뉴 열기"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <span /><span />
        </button>
        <nav className={open ? 'nav-links is-open' : 'nav-links'} aria-label="주요 메뉴">
          {links.map(([href, label]) => (
            <a key={href} href={href} onClick={() => setOpen(false)}>{label}</a>
          ))}
        </nav>
        <div className="header-date">
          <span className="live-dot" />
          {formatDate(basisDate)} 기준
        </div>
      </div>
    </header>
  )
}

function Hero({ snapshot }) {
  const summary = snapshot.summary
  const generated = snapshot.generatedAt
    ? new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(snapshot.generatedAt))
    : '확인 중'
  const goodPriceCompanies = snapshot.companies
    .filter((company) => Number.isFinite(company.gapRate) && company.gapRate <= -20)
    .sort((a, b) => a.gapRate - b.gapRate)
  const summaryItems = [
    ['검토 유니버스', `${summary.universeSize}개`, '정량·정성 검토 후보'],
    ['공식 TOP20', `${summary.top20Count}개`, 'CAQM 동점 규칙 반영'],
    ['평균 품질점수', summary.averageCaqm.toFixed(1), '100점 만점'],
    ['좋은 가격 기업', `${goodPriceCompanies.length}개`, '괴리율 -20% 이하'],
  ]

  return (
    <section className="hero" id="top">
      <div className="hero-grid page-shell">
        <div className="hero-copy">
          <div className="eyebrow"><span /> COMPOUND ASSET RESEARCH</div>
          <h1>좋은 기업과<br /><em>좋은 가격</em>이<span className="hero-desktop-break"><br /></span>{' '}만나는 곳.</h1>
          <p className="hero-lead">
            주가를 예측하지 않습니다. 경쟁우위와 현금창출력을 먼저 평가하고,
            충분한 가격 매력이 생길 때까지 기다립니다.
          </p>
          <blockquote>
            “좋은 기업을 찾는 것은 CAQM의 역할이고,<br />좋은 가격을 찾는 것은 VM의 역할이다.”
          </blockquote>
          <div className="hero-model-definitions" aria-label="CAQM과 VM 뜻">
            <article>
              <span>좋은 기업인가?</span>
              <div><strong>CAQM</strong><small>Compound Asset Quality Model</small></div>
              <p>기업이 장기간 복리성장을 만들 수 있는 품질을 평가합니다.</p>
            </article>
            <article>
              <span>좋은 가격인가?</span>
              <div><strong>VM</strong><small>Value Model</small></div>
              <p>기업의 예상 이익과 업종별 기준으로 현재 적정가치를 평가합니다.</p>
            </article>
          </div>
          <div className="hero-actions">
            <a className="button primary" href="#matrix">오늘의 매트릭스 보기 <span>↘</span></a>
            <a className="button ghost" href="#methodology">산정 기준 확인</a>
            <PwaInstallButton />
          </div>
          <div className="data-line">
            <span className="status-dot" />
            <strong>{snapshot.dataStatus}</strong>
            <span>{formatDate(snapshot.basisDate)} 기준</span>
          </div>
        </div>
        <div className="hero-panel hero-summary-panel" aria-label="CAP 데이터 요약">
          <div className="hero-summary-grid">
            {summaryItems.map(([label, value, note]) => (
              <div className="hero-summary-item" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
                <small>{note}</small>
              </div>
            ))}
          </div>
          <div className="hero-good-opportunities">
            <div className="hero-good-opportunities-heading">
              <span>좋은 기업 · 좋은 가격</span>
              <strong>{goodPriceCompanies.length}개 · 괴리율 -20% 이하</strong>
            </div>
            <ul aria-label="좋은 기업이면서 괴리율 -20% 이하인 기업">
              {goodPriceCompanies.map((company) => (
                <li key={company.code}>
                  <span>#{company.rank} {company.name}</span>
                  <strong>{formatPercent(company.gapRate, true)}</strong>
                </li>
              ))}
            </ul>
          </div>
          <div className="hero-summary-refresh">
            <span className="hero-refresh-icon" aria-hidden="true">↻</span>
            <div><span>마지막 계산</span><strong>{generated}</strong></div>
          </div>
        </div>
      </div>
    </section>
  )
}

function LatestChanges({ snapshot, onSelect }) {
  const changes = snapshot.changes || {}
  const officialMaster = snapshot.officialMaster || {}
  const financial = snapshot.financialDataSummary || {}
  const choose = (code) => {
    if (!code) return
    onSelect(code)
    window.requestAnimationFrame(() => document.getElementById('company')?.scrollIntoView({ behavior: 'smooth' }))
  }
  const groups = [
    {
      key: 'official',
      label: '공식 마스터 반영',
      tone: 'official',
      items: (officialMaster.changes || []).map((item) => ({
        ...item,
        name: item.title || item.name,
        copy: item.detail || '',
      })),
    },
    {
      key: 'vm',
      label: 'VM 변경',
      tone: 'vm',
      items: (changes.vm || []).map((item) => ({
        ...item,
        copy: `${formatWon(item.before)} → ${formatWon(item.after)}`,
      })),
    },
    {
      key: 'gap',
      label: '괴리율 변경',
      tone: 'gap',
      items: (changes.gap || []).slice(0, 4).map((item) => ({
        ...item,
        copy: `${formatPercent(item.before, true)} → ${formatPercent(item.after, true)}`,
      })),
    },
    {
      key: 'ranking',
      label: 'TOP20 진입·탈락',
      tone: 'ranking',
      items: [
        ...(changes.entrants || []).map((item) => ({
          ...item,
          copy: `신규 진입 · #${item.rank} · CAQM ${firstNumber(item.caqm, item.cavm) ?? '검토 중'}`,
        })),
        ...(changes.exits || []).map((item) => ({ ...item, copy: `탈락 · ${item.reason}` })),
      ],
    },
    {
      key: 'research',
      label: 'CAQM·보고서',
      tone: 'research',
      items: [
        ...(changes.caqm || []).map((item) => ({ ...item, copy: `CAQM ${item.before} → ${item.after}` })),
        ...(changes.reports || []).map((item) => ({ ...item, copy: `${item.periodLabel || '최신 보고서'} 반영 · ${formatDate(item.after)}` })),
      ],
    },
  ].filter((group) => group.items.length)

  return (
    <section className="changes-section" id="changes" aria-labelledby="changes-title">
      <div className="page-shell">
        <SectionHeading
          eyebrow="LATEST UPDATE"
          title="최근 공식 변경 이력"
          titleId="changes-title"
          description="TOP20 편입·탈락, 중요 VM·CAQM 변경, 가격 판단 전환, 최신 보고서 반영처럼 투자 판단에 의미 있는 변화만 기록합니다."
          aside={<span className="changes-basis">{officialMaster.version || `비교 기준 ${formatDateTime(changes.comparedAt)}`}</span>}
        />
        {groups.length ? (
          <div className="changes-grid">
            {groups.map((group) => (
              <article className={`change-group tone-${group.tone}`} key={group.key}>
                <div className="change-group-title"><span>{group.label}</span><strong>{group.items.length}건</strong></div>
                <div className="change-items">
                  {group.items.map((item, index) => (
                    <button type="button" key={`${item.code}-${index}`} onClick={() => choose(item.code)}>
                      <span>{item.name}</span><strong>{item.copy}</strong><i aria-hidden="true">↘</i>
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="no-material-change">
            <span aria-hidden="true">✓</span>
            <div><strong>새로운 공식 변경은 없습니다.</strong><p>단순 시세 변동과 내부 검토 과정은 제외하고, 투자 판단에 의미 있는 변화가 확인될 때 기록합니다.</p></div>
          </div>
        )}
        <div className="data-health-grid" aria-label="자동화 데이터 상태">
          <div><span>시세 기준일</span><strong>{formatDate(snapshot.companies[0]?.priceBasisDate)}</strong><small>공식 종가 스냅샷</small></div>
          <div><span>OpenDART 수신</span><strong>{financial.latestReportSuccessCount ?? '—'} / {financial.companyCount ?? snapshot.companies.length}</strong><small>기업별 최신 보고서</small></div>
          <div><span>주요 지표 완전</span><strong>{financial.latestReportCompleteCount ?? '—'}개</strong><small>누락 없는 기업</small></div>
          <div><span>마지막 자동 계산</span><strong>{formatDateTime(snapshot.generatedAt)}</strong><small>다음 실행 · 평일 18:20 KST</small></div>
        </div>
      </div>
    </section>
  )
}

function DataPolicy() {
  const policies = [
    {
      number: '01',
      status: '자동 수집',
      tone: 'automatic',
      title: '실제 주가와 공시 실적',
      description: '가격은 거래소·공공데이터의 일별 종가를, 재무는 OpenDART의 최신 사업·반기·분기보고서를 우선합니다.',
      meta: '주가 매일 · 재무 공시 반영',
    },
    {
      number: '02',
      status: '사람 검토',
      tone: 'reviewed',
      title: '3년 PMO 예상 EPS',
      description: '공시 실적과 회사 가이던스, 정상화 가정을 바탕으로 작성하고 담당자가 확인한 값만 VM 계산에 사용합니다.',
      meta: '가정·근거·검토일 기록',
    },
    {
      number: '03',
      status: '공개 제한',
      tone: 'restricted',
      title: '증권사 시장 컨센서스',
      description: '국내외 컨센서스는 공개 표시 권한이 확인된 데이터만 사용합니다. 무료 페이지를 무단 수집해 자동 게시하지 않습니다.',
      meta: '라이선스 확보 전 미연동',
    },
    {
      number: '04',
      status: '추적 관리',
      tone: 'monitored',
      title: '변경과 후속 검토',
      description: '실적 발표, 가이던스 변경, 해자 훼손과 자본배분 이슈가 생기면 기존 가정을 유지하지 않고 다시 검토합니다.',
      meta: '변경 이력과 다음 점검일 표시',
    },
  ]

  return (
    <section className="data-policy-section" id="data-policy" aria-labelledby="data-policy-title">
      <div className="page-shell">
        <SectionHeading
          eyebrow="DATA & ESTIMATE POLICY"
          title="실적과 예상치를 섞지 않습니다."
          titleId="data-policy-title"
          description="자동으로 들어오는 사실과 사람이 판단한 가정을 분리하고, 각 숫자의 출처·기준일·검토 상태를 함께 관리합니다."
          aside={<span className="policy-principle">공개 원자료 → 계산 → PMO 검토 → 게시</span>}
        />
        <div className="data-policy-grid">
          {policies.map((policy) => (
            <article className={`data-policy-card tone-${policy.tone}`} key={policy.number}>
              <div className="data-policy-topline">
                <span>{policy.number}</span>
                <strong>{policy.status}</strong>
              </div>
              <h3>{policy.title}</h3>
              <p>{policy.description}</p>
              <small>{policy.meta}</small>
            </article>
          ))}
        </div>
        <div className="data-policy-note">
          <div>
            <span aria-hidden="true">i</span>
            <p><strong>사이트의 ‘예상 EPS’는 자동 수집된 시장 컨센서스와 같은 뜻이 아닙니다.</strong> 별도 출처가 표시되지 않은 값은 CAP PMO가 공개 자료를 바탕으로 검토한 분석 가정입니다.</p>
          </div>
          <div className="data-policy-links" aria-label="주요 공식 데이터 출처">
            <a href="https://opendart.fss.or.kr/" target="_blank" rel="noreferrer">OpenDART ↗</a>
            <a href="https://openapi.krx.co.kr/" target="_blank" rel="noreferrer">KRX OpenAPI ↗</a>
          </div>
        </div>
      </div>
    </section>
  )
}

function SectionHeading({ eyebrow, title, titleId, description, aside }) {
  return (
    <div className="section-heading">
      <div>
        <div className="section-eyebrow">{eyebrow}</div>
        <h2 id={titleId}>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {aside && <div className="heading-aside">{aside}</div>}
    </div>
  )
}

const SCREENER_STATUS_META = {
  passed: { label: '정량 통과', tone: 'passed' },
  pending: { label: '검토 대기', tone: 'pending' },
  excluded: { label: '제외', tone: 'excluded' },
  unclassified: { label: '미분류', tone: 'neutral' },
}

function ScreenerSection({ screener, loadState, error, onRetry }) {
  const [query, setQuery] = useState('')
  const [market, setMarket] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const companies = screener?.companies || []
  const markets = useMemo(
    () => [...new Set(companies.map((company) => company.market).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ko')),
    [companies],
  )
  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return companies
      .filter((company) => market === 'all' || company.market === market)
      .filter((company) => {
        if (statusFilter === 'all') return true
        if (statusFilter === 'passed') return company.quantPassed
        if (statusFilter === 'pending') return company.reviewPending || company.status === 'pending'
        return company.status === statusFilter
      })
      .filter((company) => (
        !normalizedQuery
        || `${company.name} ${company.code} ${company.sector}`.toLowerCase().includes(normalizedQuery)
      ))
      .sort((a, b) => {
        const aScore = a.screenScore ?? Number.NEGATIVE_INFINITY
        const bScore = b.screenScore ?? Number.NEGATIVE_INFINITY
        return bScore - aScore || a.name.localeCompare(b.name, 'ko')
      })
  }, [companies, market, query, statusFilter])
  const visible = filtered.slice(0, SCREENER_ROW_LIMIT)
  const count = (value) => new Intl.NumberFormat('ko-KR').format(value || 0)

  return (
    <section className="screener-section" id="screener" aria-labelledby="screener-title">
      <div className="page-shell">
        <SectionHeading
          eyebrow="MARKET-WIDE QUANT SCREEN"
          title="전체시장 스크리너"
          titleId="screener-title"
          description="국내 상장기업의 공개 시장 데이터와 최근 널리 이용 가능한 연간 사업보고서를 먼저 정량 필터링합니다. 이 결과는 공식 CAQM이나 투자 의견이 아니라 PMO 심층 검토 대상을 좁히기 위한 사전 화면입니다."
          aside={screener?.generatedAt ? `데이터 ${formatDate(screener.generatedAt)} 기준` : '데이터 연결 상태 확인 중'}
        />

        {loadState === 'loading' && (
          <div className="screener-load-state" role="status" aria-live="polite">
            <span className="screener-spinner" aria-hidden="true" />
            <div><strong>전체시장 데이터를 불러오는 중입니다.</strong><p>공식 TOP20은 이 작업과 별도로 정상 표시됩니다.</p></div>
          </div>
        )}

        {loadState === 'unavailable' && (
          <div className="screener-load-state is-unavailable" role="status" aria-live="polite">
            <div>
              <strong>전체시장 스크리너 데이터가 아직 준비되지 않았습니다.</strong>
              <p>{error || '데이터 파일이 배포되면 이 영역만 자동으로 활성화됩니다. 공식 TOP20 데이터에는 영향이 없습니다.'}</p>
            </div>
            <button type="button" className="button screener-retry" onClick={onRetry}>스크리너 다시 불러오기</button>
          </div>
        )}

        {loadState === 'ready' && screener && (
          <>
            <div className="screener-status-grid" aria-label="전체시장 스크리닝 현황">
              {[
                ['전체 유니버스', screener.summary.total, '수집된 상장기업', 'total'],
                ['제외', screener.summary.excluded, '게이트 탈락·순위 밖', 'excluded'],
                ['정량 통과', screener.summary.passed, '재무·시장 필터 통과', 'passed'],
                ['검토 대기', screener.summary.pending, 'PMO 정성 검토 필요', 'pending'],
              ].map(([label, value, note, tone]) => (
                <article className={`screener-status-card tone-${tone}`} key={label}>
                  <span>{label}</span><strong>{count(value)}</strong><small>{note}</small>
                </article>
              ))}
            </div>

            <aside className="screener-notices" aria-label="스크리너 데이터 주의사항">
              <div><span aria-hidden="true">i</span><p><strong>공식 CAQM과 구분됩니다.</strong> 해자·경영진·자본배분은 자동 확정하지 않으며 사람이 근거를 검토한 기업만 공식 TOP20 후보가 됩니다.</p></div>
              {!screener.managedItemChecked && (
                <div className="warning"><span aria-hidden="true">!</span><p><strong>관리종목 여부 미확인</strong> 현재 스냅샷은 관리종목·거래정지·상장적격성 상태를 완전히 반영하지 않을 수 있으므로 거래소 공시를 별도로 확인해야 합니다.</p></div>
              )}
              <div><span aria-hidden="true">i</span><p><strong>데이터 한계</strong> 보고서 시차, 업종별 회계 차이, 신규상장·기업분할로 일부 지표가 비어 있거나 비교 가능성이 낮을 수 있습니다.</p></div>
              {screener.limitations.map((limitation, index) => (
                <div key={`${limitation}-${index}`}><span aria-hidden="true">i</span><p>{limitation}</p></div>
              ))}
            </aside>

            <div className="candidate-review-bar">
              <p><strong>미검토 후보는 공식 TOP20이 아닙니다.</strong> 해자·경영진·VM 근거를 확인한 뒤에만 공식 후보로 승격할 수 있습니다.</p>
              <a className="button candidate-review-link" href={CANDIDATE_REVIEW_URL} target="_blank" rel="noreferrer">정성 검토 요청 <span aria-hidden="true">↗</span></a>
            </div>

            <div className="screener-controls" role="search" aria-label="전체시장 기업 검색과 필터">
              <label className="screener-search">
                <span>기업 검색</span>
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="기업명·종목코드·업종"
                  autoComplete="off"
                />
              </label>
              <label>
                <span>시장</span>
                <select value={market} onChange={(event) => setMarket(event.target.value)}>
                  <option value="all">전체 시장</option>
                  {markets.map((item) => <option value={item} key={item}>{item}</option>)}
                </select>
              </label>
              <label>
                <span>상태</span>
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  <option value="all">전체 상태</option>
                  <option value="passed">정량 통과</option>
                  <option value="pending">검토 대기</option>
                  <option value="excluded">제외</option>
                  <option value="unclassified">미분류</option>
                </select>
              </label>
            </div>

            <div className="screener-result-line" id="screener-result-count" aria-live="polite">
              <strong>{count(filtered.length)}개 검색됨</strong>
              <span>정량점수 순 상위 {count(Math.min(filtered.length, SCREENER_ROW_LIMIT))}개 표시</span>
            </div>

            <div className="screener-table-wrap" tabIndex="0" role="region" aria-labelledby="screener-result-count">
              <table className="screener-table">
                <caption>전체시장 정량 스크리너 검색 결과</caption>
                <thead>
                  <tr>
                    <th scope="col">기업</th><th scope="col">시장·업종</th><th scope="col">상태</th><th scope="col">정량점수</th>
                    <th scope="col">현재가</th><th scope="col">시가총액</th><th scope="col">ROE</th><th scope="col">영업이익률</th>
                    <th scope="col">부채비율</th><th scope="col">재무 기준 / 제외 사유</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((company, index) => {
                    const statusMeta = SCREENER_STATUS_META[company.status] || SCREENER_STATUS_META.unclassified
                    return (
                      <tr key={`${company.code}-${index}`}>
                        <td data-label="기업"><div className="screener-company"><strong>{company.name}</strong><small>{company.code}</small></div></td>
                        <td data-label="시장·업종"><strong>{company.market}</strong><small className="screener-cell-note">{company.sector}</small></td>
                        <td data-label="상태"><span className={`screen-status tone-${statusMeta.tone}`}>{statusMeta.label}</span></td>
                        <td data-label="정량점수" className="numeric"><strong>{company.screenScore === null ? '—' : company.screenScore.toFixed(1)}</strong></td>
                        <td data-label="현재가" className="numeric">{formatWon(company.currentPrice)}</td>
                        <td data-label="시가총액" className="numeric">{formatEok(company.marketCapEok)}</td>
                        <td data-label="ROE" className="numeric">{formatPercent(company.roe)}</td>
                        <td data-label="영업이익률" className="numeric">{formatPercent(company.operatingMargin)}</td>
                        <td data-label="부채비율" className="numeric">{formatPercent(company.debtRatio)}</td>
                        <td data-label="재무 기준 / 제외 사유" className="screener-reason">
                          {company.exclusionReason || (company.reportPeriod ? formatDate(company.reportPeriod) : '세부 데이터 확인 필요')}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {!visible.length && <div className="screener-empty">선택한 조건에 맞는 기업이 없습니다.</div>}
            </div>
            {filtered.length > SCREENER_ROW_LIMIT && (
              <p className="screener-limit-note">브라우저 성능을 위해 상위 {SCREENER_ROW_LIMIT}개만 표시합니다. 검색어나 필터를 사용하면 원하는 기업을 더 빠르게 찾을 수 있습니다.</p>
            )}
          </>
        )}
      </div>
    </section>
  )
}

function chartLabelLayout(points, x, y, centerX, top, bottom) {
  const labels = new Map()
  const groups = { left: [], right: [] }

  points.forEach((company) => {
    const cx = x(company.caqm)
    const cy = y(company.gapRate)
    const side = cx >= centerX ? 'right' : 'left'
    groups[side].push({ company, cx, cy, labelY: Math.max(top, Math.min(bottom, cy + 4)) })
  })

  Object.entries(groups).forEach(([side, items]) => {
    items.sort((a, b) => a.labelY - b.labelY)
    const spacing = 25
    for (let index = 1; index < items.length; index += 1) {
      items[index].labelY = Math.max(items[index].labelY, items[index - 1].labelY + spacing)
    }
    if (items.length && items[items.length - 1].labelY > bottom) {
      const overflow = items[items.length - 1].labelY - bottom
      items.forEach((item) => { item.labelY -= overflow })
      for (let index = items.length - 2; index >= 0; index -= 1) {
        items[index].labelY = Math.min(items[index].labelY, items[index + 1].labelY - spacing)
      }
    }
    items.forEach((item) => labels.set(item.company.code, { ...item, side }))
  })

  return labels
}

function MatrixChart({ companies, selectedCode, onSelect }) {
  const scrollRef = useRef(null)
  const points = companies.filter((company) => Number.isFinite(company.caqm) && Number.isFinite(company.gapRate))
  const width = 1180
  const height = 700
  const margin = { top: 70, right: 190, bottom: 86, left: 190 }
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom
  const rawXMin = Math.min(...points.map((point) => point.caqm), 90)
  const rawXMax = Math.max(...points.map((point) => point.caqm), 90)
  const xMin = Math.max(0, Math.floor(rawXMin) - 1)
  const xMax = Math.min(100, Math.max(xMin + 10, Math.ceil(rawXMax) + 1))
  const gapValues = points.map((point) => point.gapRate)
  const yMin = Math.min(-60, Math.floor(Math.min(...gapValues, -40) / 20) * 20)
  const yMax = Math.max(60, Math.ceil(Math.max(...gapValues, 40) / 50) * 50)
  const x = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * innerWidth
  const y = (value) => margin.top + ((yMax - value) / (yMax - yMin)) * innerHeight
  const xTicks = []
  for (let value = Math.ceil(xMin / 2) * 2; value <= xMax; value += 2) xTicks.push(value)
  const yStep = yMax - yMin > 180 ? 50 : 20
  const yTicks = []
  for (let value = Math.ceil(yMin / yStep) * yStep; value <= yMax; value += yStep) yTicks.push(value)
  const cutX = x(90)
  const decisionY = y(-10)
  const selected = points.find((point) => point.code === selectedCode)
  const labels = chartLabelLayout(points, x, y, margin.left + innerWidth / 2, margin.top + 14, height - margin.bottom - 12)
  const renderPoints = [...points].sort((a, b) => Number(a.code === selectedCode) - Number(b.code === selectedCode))
  const goodPriceCompanies = [...points]
    .filter((company) => company.gapRate <= -20)
    .sort((a, b) => b.caqm - a.caqm || a.gapRate - b.gapRate)

  useEffect(() => {
    const container = scrollRef.current
    if (!container || !window.matchMedia('(max-width: 820px)').matches) return
    const frame = window.requestAnimationFrame(() => {
      container.scrollLeft = Math.max(0, x(90) - container.clientWidth / 2)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [points.length, xMin, xMax])

  return (
    <div className="chart-shell">
      <div className="chart-toolbar">
        <div className="chart-legend" aria-label="괴리율 범례">
          <span><i className="legend-dot attractive" /> -20% 이하 · 좋은 가격</span>
          <span><i className="legend-dot fair" /> -20%~-10% · 분할 검토</span>
          <span><i className="legend-dot expensive" /> -10% 초과 · 관찰</span>
          <span><i className="legend-diamond opportunity" /> TOP20 + 좋은 가격 · 특별 표시</span>
        </div>
        <span className="chart-note">
          <span className="chart-note-default">점 안 숫자 = 순위 · 기업명을 선택하면 상세 분석이 바뀝니다</span>
          <span className="chart-note-mobile">← 좌우로 이동 · 기업명을 누르면 상세 분석이 바뀝니다 →</span>
        </span>
      </div>
      <div
        className="chart-scroll"
        ref={scrollRef}
        tabIndex="0"
        aria-label="CAQM 가격 매트릭스. 모바일에서는 좌우로 이동할 수 있습니다."
      >
        {points.length ? (
          <svg
            className="matrix-chart"
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-labelledby="matrix-title matrix-desc"
          >
            <title id="matrix-title">한국 TOP20 CAQM과 현재가 괴리율 매트릭스</title>
            <desc id="matrix-desc">오른쪽으로 갈수록 기업의 질 점수가 높고 아래로 갈수록 적정가격보다 저렴합니다.</desc>
            <defs>
              <filter id="point-shadow" x="-100%" y="-100%" width="300%" height="300%">
                <feDropShadow dx="0" dy="3" stdDeviation="4" floodOpacity="0.2" />
              </filter>
            </defs>
            <rect x={margin.left} y={margin.top} width={cutX - margin.left} height={decisionY - margin.top} className="zone low-expensive" />
            <rect x={cutX} y={margin.top} width={width - margin.right - cutX} height={decisionY - margin.top} className="zone high-expensive" />
            <rect x={margin.left} y={decisionY} width={cutX - margin.left} height={height - margin.bottom - decisionY} className="zone low-attractive" />
            <rect x={cutX} y={decisionY} width={width - margin.right - cutX} height={height - margin.bottom - decisionY} className="zone high-attractive" />
            {xTicks.map((tick) => (
              <g key={`x-${tick}`}>
                <line x1={x(tick)} x2={x(tick)} y1={margin.top} y2={height - margin.bottom} className="grid-line" />
                <text x={x(tick)} y={height - margin.bottom + 30} className="tick-label" textAnchor="middle">{tick}</text>
              </g>
            ))}
            {yTicks.map((tick) => (
              <g key={`y-${tick}`}>
                <line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} className={tick === 0 ? 'grid-line zero' : 'grid-line'} />
                <text x={margin.left - 16} y={y(tick) + 5} className="tick-label" textAnchor="end">{tick > 0 ? '+' : ''}{tick}%</text>
              </g>
            ))}
            <line x1={cutX} x2={cutX} y1={margin.top} y2={height - margin.bottom} className="cut-line" />
            <line x1={margin.left} x2={width - margin.right} y1={decisionY} y2={decisionY} className="decision-line" />
            <text x={width - margin.right - 8} y={decisionY - 10} className="decision-label" textAnchor="end">관찰 경계 -10%</text>
            <text x={margin.left} y={42} className="zone-label danger">낮은 CAQM · 관찰</text>
            <text x={width - margin.right} y={42} className="zone-label caution" textAnchor="end">높은 CAQM · 관찰</text>
            {renderPoints.map((company) => {
              const tone = gapTone(company.gapRate)
              const isSelected = company.code === selectedCode
              const isGoodOpportunity = company.gapRate <= -20
              const cx = x(company.caqm)
              const cy = y(company.gapRate)
              const label = labels.get(company.code)
              const labelRight = label?.side === 'right'
              const labelX = labelRight ? width - margin.right + 18 : margin.left - 18
              const labelY = label?.labelY ?? cy
              return (
                <g
                  key={company.code}
                  className={`data-point tone-${tone}${isSelected ? ' is-selected' : ''}`}
                  role="button"
                  tabIndex="0"
                  aria-label={`${company.name}, CAQM ${company.caqm}점, 괴리율 ${formatPercent(company.gapRate, true)}${isGoodOpportunity ? ', 좋은 기업과 좋은 가격' : ''}`}
                  onClick={() => onSelect(company.code)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onSelect(company.code)
                    }
                  }}
                >
                  <line x1={cx + (labelRight ? 9 : -9)} y1={cy} x2={labelX + (labelRight ? -4 : 4)} y2={labelY - 4} className="point-leader" />
                  {isGoodOpportunity && (
                    <rect
                      x={cx - 12}
                      y={cy - 12}
                      width="24"
                      height="24"
                      rx="2"
                      className="good-opportunity-marker"
                      transform={`rotate(45 ${cx} ${cy})`}
                    />
                  )}
                  <circle cx={cx} cy={cy} r={isSelected ? 13 : 10} className="point-halo" />
                  <circle cx={cx} cy={cy} r={isSelected ? 10 : 8} className="point-core" filter="url(#point-shadow)" />
                  <text x={cx} y={cy + 3} className="point-rank" textAnchor="middle">{company.rank}</text>
                  <text x={labelX} y={labelY} className={`point-label${isSelected ? ' is-selected' : ''}${isGoodOpportunity ? ' is-good-opportunity' : ''}`} textAnchor={labelRight ? 'start' : 'end'}>
                    #{company.rank} {company.name}
                  </text>
                  <title>{company.name} · CAQM {company.caqm} · 괴리율 {formatPercent(company.gapRate, true)}</title>
                </g>
              )
            })}
            <text x={margin.left + innerWidth / 2} y={height - 20} className="axis-title" textAnchor="middle">CAQM 품질 점수 → 오른쪽일수록 우수</text>
            <text transform={`translate(27 ${margin.top + innerHeight / 2}) rotate(-90)`} className="axis-title" textAnchor="middle">괴리율 (%) → 아래일수록 저평가</text>
          </svg>
        ) : (
          <div className="empty-state">VM 입력이 완료되면 매트릭스가 표시됩니다.</div>
        )}
      </div>
      <div className="chart-opportunity-caption">
        <strong>좋은 기업 · 좋은 가격</strong>
        <span>TOP20 가운데 괴리율 -20% 이하인 기업을 금색 마름모로 표시합니다.</span>
      </div>
      <div className="matrix-opportunity-list" aria-label="좋은 가격 기업 바로 선택">
        <div className="matrix-opportunity-heading">
          <span>GOOD PRICE</span>
          <strong>괴리율 -20% 이하</strong>
          <small>그래프를 움직이지 않아도 기업을 바로 선택할 수 있습니다.</small>
        </div>
        <div className="matrix-opportunity-buttons">
          {goodPriceCompanies.map((company) => (
            <button
              type="button"
              className={company.code === selectedCode ? 'is-selected' : ''}
              onClick={() => onSelect(company.code)}
              key={company.code}
            >
              <strong>{company.name}</strong>
              <span>CAQM {company.caqm}</span>
              <b>{formatPercent(company.gapRate, true)}</b>
            </button>
          ))}
        </div>
      </div>
      {selected && (
        <div className={`selected-chart-card tone-${gapTone(selected.gapRate)}`}>
          <span className="rank-token">#{selected.rank}</span>
          <div><strong>{selected.name}</strong><small>{selected.sector}</small></div>
          <div><span>CAQM</span><strong>{selected.caqm}</strong></div>
          <div><span>괴리율</span><strong>{formatPercent(selected.gapRate, true)}</strong></div>
          <div><span>판단</span><strong>{selected.opinion || gapLabel(selected.gapRate)}</strong></div>
          <a href="#company">상세 보기 →</a>
        </div>
      )}
    </div>
  )
}

function MatrixSection({ snapshot, selectedCode, onSelect }) {
  return (
    <section className="matrix-section" id="matrix">
      <div className="page-shell">
        <SectionHeading
          eyebrow="THE CAQM MATRIX"
          title={<>기업의 질과 가격을<br />한 화면에서 봅니다.</>}
          description="가로축은 CAQM, 세로축은 현재가의 Final VM 대비 괴리율입니다. 오른쪽 아래에 가까울수록 우리가 기다린 조건에 부합합니다."
          aside={<><strong>좋은 가격 기준</strong><span>= 괴리율 -20% 이하</span></>}
        />
        <MatrixChart companies={snapshot.companies} selectedCode={selectedCode} onSelect={onSelect} />
      </div>
    </section>
  )
}

function downloadTop20Csv(companies) {
  const headers = ['순위', '종목코드', '기업', '업종', 'CAQM', '해자', '성장', '수익성', '재무건전성', '경영진·주주환원', '현재가', 'Final VM', '괴리율', 'VM 신뢰도', '판단']
  const rows = companies.map((company) => [
    company.rank, company.code, company.name, company.sector, company.caqm,
    company.components.moat, company.components.growth, company.components.profitability,
    company.components.financialHealth, company.components.management,
    company.currentPrice, company.finalVm, company.gapRate, company.vmConfidence?.grade || '', company.opinion,
  ])
  const escape = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`
  const csv = `\uFEFF${[headers, ...rows].map((row) => row.map(escape).join(',')).join('\r\n')}`
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `compound-asset-2045-top20-${new Date().toISOString().slice(0, 10)}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function Top20Table({ companies, selectedCode, onSelect, watchlist, onToggleWatch }) {
  const [query, setQuery] = useState('')
  const [sector, setSector] = useState('전체 업종')
  const [sort, setSort] = useState('rank')
  const [watchedOnly, setWatchedOnly] = useState(false)
  const sectors = useMemo(() => ['전체 업종', ...new Set(companies.map((company) => company.sector))], [companies])
  const visible = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return companies
      .filter((company) => sector === '전체 업종' || company.sector === sector)
      .filter((company) => !watchedOnly || watchlist.includes(company.code))
      .filter((company) => !normalizedQuery || `${company.name} ${company.code}`.toLowerCase().includes(normalizedQuery))
      .sort((a, b) => {
        if (sort === 'caqm') return b.caqm - a.caqm || a.position - b.position
        if (sort === 'gap') return (a.gapRate ?? Infinity) - (b.gapRate ?? Infinity)
        return a.position - b.position
      })
  }, [companies, query, sector, sort, watchedOnly, watchlist])

  const choose = (company) => {
    onSelect(company.code)
    window.requestAnimationFrame(() => document.getElementById('company')?.scrollIntoView({ behavior: 'smooth' }))
  }

  return (
    <section className="top20-section" id="top20">
      <div className="page-shell">
        <SectionHeading
          eyebrow="KOREA TOP20"
          title="CAQM 우선순위"
          description="점수가 같으면 해자 → 성장성 → 현금창출력 → 재무건전성 → 업종분산 순으로 정렬합니다. 가격 판단은 오른쪽 괴리율에서 별도로 확인하세요."
        />
        <div className="table-controls">
          <label className="search-field">
            <span className="sr-only">기업명 또는 종목코드 검색</span>
            <i aria-hidden="true">⌕</i>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="기업명 또는 종목코드" />
          </label>
          <label>
            <span className="sr-only">업종 선택</span>
            <select value={sector} onChange={(event) => setSector(event.target.value)}>
              {sectors.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          <div className="sort-tabs" role="group" aria-label="정렬 기준">
            {[['rank', '공식 순위'], ['caqm', 'CAQM'], ['gap', '가격 매력']].map(([value, label]) => (
              <button type="button" key={value} className={sort === value ? 'active' : ''} onClick={() => setSort(value)}>{label}</button>
            ))}
          </div>
          <button type="button" className={watchedOnly ? 'watchlist-filter is-active' : 'watchlist-filter'} onClick={() => setWatchedOnly((value) => !value)}>
            ★ 관심기업 {watchlist.length}
          </button>
          <button type="button" className="csv-export" onClick={() => downloadTop20Csv(visible)}>CSV 저장</button>
        </div>
        <p className="table-mobile-note" id="top20-table-note">← 표를 좌우로 밀어 현재가·Final VM·괴리율까지 확인하세요 →</p>
        <div className="table-wrap" tabIndex="0" role="region" aria-labelledby="top20-table-note">
          <table>
            <thead>
              <tr>
                <th>순위</th><th>기업</th><th>CAQM</th><th>해자</th><th>성장</th><th>수익</th><th>재무</th><th>환원</th><th>현재가</th><th>Final VM</th><th>괴리율</th><th>판단</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((company) => (
                <tr
                  key={company.code}
                  className={selectedCode === company.code ? 'selected-row' : ''}
                  onClick={() => choose(company)}
                >
                  <td><span className={`table-rank rank-${company.rank}`}>{company.rank}</span></td>
                  <td>
                    <div className="company-cell-wrap">
                      <button className="company-cell" type="button" onClick={() => choose(company)}>
                        <strong>{company.name}</strong><small>{company.code} · {company.sector}</small>
                        <em>{company.valuationModelLabel}</em>
                      </button>
                      <button
                        type="button"
                        className={watchlist.includes(company.code) ? 'table-watch is-active' : 'table-watch'}
                        aria-label={`${company.name} 관심기업 ${watchlist.includes(company.code) ? '해제' : '추가'}`}
                        onClick={(event) => { event.stopPropagation(); onToggleWatch(company.code) }}
                      >{watchlist.includes(company.code) ? '★' : '☆'}</button>
                    </div>
                  </td>
                  <td><strong className="caqm-value">{company.caqm}</strong></td>
                  {SCORE_META.map((meta) => <td key={meta.key} className="component-cell">{company.components[meta.key]}<small>/{meta.max}</small></td>)}
                  <td>{formatWon(company.currentPrice)}</td>
                  <td>{formatWon(company.finalVm)}</td>
                  <td><span className={`gap-pill ${gapTone(company.gapRate)}`}>{formatPercent(company.gapRate, true)}</span></td>
                  <td><span className="opinion-text">{company.opinion || gapLabel(company.gapRate)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!visible.length && <div className="empty-state">조건에 맞는 기업이 없습니다.</div>}
        </div>
        <p className="table-footnote">* 현재가와 Final VM은 데이터 기준일의 입력값입니다. VM 가정이 바뀌면 괴리율과 가격 판단도 달라집니다.</p>
      </div>
    </section>
  )
}

function CandidateWatchlist({ master }) {
  const candidates = master?.candidates || []
  if (!candidates.length) return null
  return (
    <section className="candidate-section" id="candidates" aria-labelledby="candidate-title">
      <div className="page-shell">
        <SectionHeading
          eyebrow="NEXT REVIEW"
          title="공식 후보군"
          titleId="candidate-title"
          description="TOP20 밖에서 다음 정기 재선정을 기다리는 핵심 기업입니다. 숫자가 없는 항목은 임의로 채우지 않고 검토 중으로 표시합니다."
          aside={<><strong>{candidates.length}개 후보</strong><span>CAQM·VM 재검토 대기</span></>}
        />
        <div className="candidate-grid">
          {candidates.map((candidate, index) => (
            <article key={candidate.code || candidate.name}>
              <div className="candidate-topline"><span>{String(index + 1).padStart(2, '0')}</span><small>{candidate.code}</small></div>
              <h3>{candidate.name}</h3>
              <div className="candidate-values">
                <div><span>CAQM</span><strong>{Number.isFinite(firstNumber(candidate.caqm, candidate.cavm)) ? firstNumber(candidate.caqm, candidate.cavm) : '검토 중'}</strong></div>
                <div><span>Final VM</span><strong>{Number.isFinite(candidate.finalVm) ? formatWon(candidate.finalVm) : '검토 중'}</strong></div>
              </div>
              <p>{candidate.note || '정성 근거와 VM 입력값을 재검토합니다.'}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}

function ScoreBreakdown({ company }) {
  return (
    <div className="score-breakdown">
      {SCORE_META.map((meta) => {
        const value = company.components[meta.key]
        const percent = Math.max(0, Math.min(100, (value / meta.max) * 100))
        return (
          <div className="score-row" key={meta.key}>
            <div><span>{meta.label}</span><strong>{value}<small> / {meta.max}</small></strong></div>
            <div className="score-track"><span style={{ width: `${percent}%` }} /></div>
          </div>
        )
      })}
    </div>
  )
}

function qualityEvidenceFor(company, meta) {
  const report = company.financials?.latestReport
  const reportUrl = report?.rceptNo
    ? `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${encodeURIComponent(report.rceptNo)}`
    : ''
  const checkedAt = report?.periodEnd || company.review?.reviewedAt
  const nextReviewAt = company.review?.nextReviewAt
  const reportSource = reportUrl
    ? { sourceLabel: 'OpenDART 공시 원문', sourceUrl: reportUrl }
    : { sourceLabel: '근거 링크 보강 중', sourceUrl: '' }
  const evidence = {
    moat: {
      title: '경쟁우위 판단 근거',
      detail: company.reason || '브랜드·기술·시장지배력의 정성 근거를 검토 중입니다.',
      status: company.reason ? 'reviewed' : 'pending',
      sourceLabel: '정성 근거 링크 보강 중',
      sourceUrl: '',
      checkedAt: company.review?.reviewedAt,
      nextReviewAt,
    },
    growth: {
      title: '최근 보고서 성장 지표',
      detail: report
        ? `${report.periodLabel || '최근 보고서'} 매출 ${formatEok(report.revenueEok)} · 장기 매출·EPS 추세와 산업 확장성을 함께 검토합니다.`
        : '최근 분기·반기 보고서의 매출과 EPS 추세 연결이 필요합니다.',
      status: report?.rceptNo ? 'verified' : 'pending',
      ...reportSource,
      checkedAt,
      nextReviewAt,
    },
    profitability: {
      title: '수익성과 현금창출 근거',
      detail: report
        ? `영업이익률 ${formatPercent(report.operatingMargin)} · 연환산 ROE ${formatPercent(report.roeAnnualized)}. FCF와 현금전환은 별도 검토합니다.`
        : 'ROE·영업이익률·FCF의 최신 공시 근거 연결이 필요합니다.',
      status: report?.rceptNo ? 'verified' : 'pending',
      ...reportSource,
      checkedAt,
      nextReviewAt,
    },
    financialHealth: {
      title: '재무건전성 근거',
      detail: report
        ? `최근 보고서 부채비율 ${formatPercent(report.debtRatio)}. 금융·보험사는 CET1·NPL·K-ICS 등 업종 전용 지표를 추가 확인합니다.`
        : '부채·현금·자본비율의 최신 공시 근거 연결이 필요합니다.',
      status: report?.rceptNo ? 'verified' : 'pending',
      ...reportSource,
      checkedAt,
      nextReviewAt,
    },
    management: {
      title: '경영진·주주환원 근거',
      detail: '최근 3년 배당과 실제 자사주 소각 완료분을 기준으로 검토합니다. 매입 발표만으로는 점수를 확정하지 않습니다.',
      status: 'pending',
      sourceLabel: '실행 내역 근거 보강 중',
      sourceUrl: '',
      checkedAt: company.review?.reviewedAt,
      nextReviewAt,
    },
  }
  return {
    ...evidence[meta.key],
    score: company.components[meta.key],
    max: meta.max,
  }
}

function QualityEvidenceLedger({ company }) {
  return (
    <details className="quality-evidence">
      <summary>
        <span><strong>CAQM 점수 근거 장부</strong><small>항목별 판단·출처·검토일</small></span>
        <i aria-hidden="true">+</i>
      </summary>
      <div className="quality-evidence-list">
        {SCORE_META.map((meta) => {
          const evidence = qualityEvidenceFor(company, meta)
          return (
            <article key={meta.key}>
              <div className="quality-evidence-heading">
                <div><span>{meta.short}</span><strong>{evidence.title}</strong></div>
                <b>{evidence.score} / {evidence.max}</b>
              </div>
              <p>{evidence.detail}</p>
              <div className="quality-evidence-meta">
                <span className={`evidence-status is-${evidence.status}`}>
                  {evidence.status === 'verified' ? '공시 확인' : evidence.status === 'reviewed' ? '정성 검토' : '근거 보강 중'}
                </span>
                {evidence.sourceUrl ? (
                  <a href={evidence.sourceUrl} target="_blank" rel="noreferrer">{evidence.sourceLabel} ↗</a>
                ) : <span>{evidence.sourceLabel}</span>}
                <time>확인 {formatDate(evidence.checkedAt)}</time>
                <time>다음 {formatDate(evidence.nextReviewAt)}</time>
              </div>
            </article>
          )
        })}
      </div>
    </details>
  )
}

function ValuationModelPanel({ company }) {
  const model = company.valuationModel
  const officialMasterNote = company.officialVmOverride ? (
    <div className="official-vm-note">
      <span>공식 마스터 적용</span>
      <strong>{formatWon(company.finalVm)}</strong>
      <small>{company.officialVmSource}{Number.isFinite(company.modelCalculatedVm) ? ` · 모델 재계산 ${formatWon(company.modelCalculatedVm)}` : ''}</small>
    </div>
  ) : null

  if (model === 'memory_normalized') {
    return (
      <div className="sector-vm-panel">
        <div className="sector-vm-title"><span>메모리 반도체 전용</span><strong>{company.valuationModelLabel}</strong></div>
        <div className="sector-vm-flow">
          <div><span>정상화 EPS</span><strong>{formatWon(company.normalizedEps)}</strong><small>2028E · 피크 이익 배제</small></div>
          <i>×</i>
          <div><span>정상 PER</span><strong>{company.normalizedPer ?? '—'}배</strong><small>해외 동종기업·HBM 경쟁력</small></div>
          <i>÷</i>
          <div><span>현재가치 할인</span><strong>{formatPercent(company.discountRate)}</strong><small>{company.valuationHorizonYears ?? 2}년</small></div>
          <i>→</i>
          <div className="vm-result"><span>Final VM</span><strong>{formatWon(company.finalVm)}</strong><small>사이클 중간 이익 기준</small></div>
        </div>
        <div className="vm-check-strip">
          <span>분기 재검증</span>
          <p>{company.cycleChecks.join(' · ')}</p>
        </div>
        {officialMasterNote}
      </div>
    )
  }

  if (model === 'bank_pbr' || model === 'financial_hybrid' || model === 'insurance_sotp') {
    const isInsurance = model === 'insurance_sotp'
    const isHybrid = model === 'financial_hybrid'
    return (
      <div className="sector-vm-panel">
        <div className="sector-vm-title"><span>{isInsurance ? '보험사 전용' : '금융회사 전용'}</span><strong>{company.valuationModelLabel}</strong></div>
        <div className="sector-vm-flow finance">
          <div><span>정상화 BPS</span><strong>{formatWon(company.normalizedBps)}</strong><small>지속 가능한 장부가치</small></div>
          <i>×</i>
          <div><span>적정 PBR</span><strong>{company.targetPbr ?? '—'}배</strong><small>ROE·자본비율·건전성</small></div>
          {isInsurance && <><i>+</i><div><span>CSM·SOTP 조정</span><strong>{formatWon(company.csmSotpAdjustment)}</strong><small>K-ICS·투자자산 병행</small></div></>}
          <i>→</i>
          <div className="vm-result"><span>{isHybrid ? `PBR ${company.primaryWeightPct ?? 60}% 기준` : '주평가 가치'}</span><strong>{formatWon(company.primaryVm)}</strong><small>{isHybrid ? 'PER와 가중 병행' : 'Final VM 기준'}</small></div>
        </div>
        <div className="vm-cross-check">
          <div><span>PER 교차검증</span><strong>{formatWon(company.normalizedEps)} × {company.normalizedPer ?? '—'}배</strong></div>
          <div><span>교차검증 가치</span><strong>{formatWon(company.crossCheckVm)}</strong></div>
          <div className="final"><span>Final VM</span><strong>{formatWon(company.finalVm)}</strong></div>
        </div>
        <div className="vm-check-strip">
          <span>배수 조정</span>
          <p>{isInsurance ? 'K-ICS · CSM · 손해율 · 투자자산 · 실제 소각' : 'ROE · CET1 · 연체율 · NPL · 대손비용 · 배당+실제 소각'}</p>
        </div>
        {officialMasterNote}
      </div>
    )
  }

  return (
    <>
      <div className="vm-flow">
        <div><span>3년 예상 EPS</span><strong>{formatWon(company.forwardEps3y)}</strong></div>
        <i>×</i>
        <div className="vm-per-card">
          <span>PER 산정</span>
          <strong>
            {company.historicalPer5y ?? '—'}배
            <b className={(company.overseasCorrectionPer ?? 0) < 0 ? 'negative' : 'positive'}>{formatPerAdjustment(company.overseasCorrectionPer)}</b>
            = {company.appliedPer ?? '—'}배
          </strong>
          <small>5년 평균 기준 · 해외 {company.overseasPeerPer ?? '—'}배와의 차이 중 {company.overseasAdjustmentWeightPct ?? 30}% 보정</small>
        </div>
        <i>→</i>
        <div><span>할인율</span><strong>{formatPercent(company.discountRate)}</strong></div>
        <i>→</i>
        <div className="vm-result"><span>Final VM</span><strong>{formatWon(company.finalVm)}</strong></div>
      </div>
      {officialMasterNote}
    </>
  )
}

function VmScenarioPanel({ company }) {
  const scenarios = company.vmScenarios || {}
  return (
    <div className="vm-scenario-panel">
      <div className="vm-scenario-heading">
        <div><span>VALUATION RANGE</span><strong>보수 · 기준 · 낙관 VM</strong></div>
        <small>매트릭스에는 기준 VM만 사용</small>
      </div>
      <div className="vm-scenario-grid">
        {['conservative', 'base', 'optimistic'].map((key) => {
          const scenario = scenarios[key] || {}
          const scenarioGap = scenario.finalVm
            ? ((company.currentPrice - scenario.finalVm) / scenario.finalVm) * 100
            : null
          return (
            <div className={`vm-scenario tone-${key}`} key={key}>
              <span>{scenario.label || key}</span>
              <strong>{formatWon(scenario.finalVm)}</strong>
              <small>괴리율 {formatPercent(scenarioGap, true)}</small>
              <p>{(scenario.assumptions || []).join(' · ')}</p>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SensitivityCalculator({ company }) {
  const [fundamentalAdjustment, setFundamentalAdjustment] = useState(0)
  const [multipleAdjustment, setMultipleAdjustment] = useState(0)
  const [discountAdjustment, setDiscountAdjustment] = useState(0)
  const horizon = Math.max(0, company.valuationHorizonYears || 0)
  const baseDiscount = Math.max(0, company.discountRate || 0)
  const adjustedDiscount = Math.max(0, baseDiscount + discountAdjustment)
  const discountFactor = horizon
    ? ((1 + baseDiscount / 100) ** horizon) / ((1 + adjustedDiscount / 100) ** horizon)
    : 1
  const calculatedVm = Math.max(0, Math.round(
    company.finalVm
      * (1 + fundamentalAdjustment / 100)
      * (1 + multipleAdjustment / 100)
      * discountFactor,
  ))
  const calculatedGap = calculatedVm > 0
    ? ((company.currentPrice - calculatedVm) / calculatedVm) * 100
    : null
  const isFinancial = ['bank_pbr', 'financial_hybrid', 'insurance_sotp'].includes(company.valuationModel)
  const reset = () => {
    setFundamentalAdjustment(0)
    setMultipleAdjustment(0)
    setDiscountAdjustment(0)
  }

  return (
    <details className="sensitivity-calculator">
      <summary><span>민감도 계산기</span><strong>내 가정으로 VM 확인</strong><i aria-hidden="true">+</i></summary>
      <div className="sensitivity-body">
        <div className="sensitivity-controls">
          <label>
            <span>{isFinancial ? 'BPS·EPS 조정' : 'EPS 조정'}<strong>{fundamentalAdjustment > 0 ? '+' : ''}{fundamentalAdjustment}%</strong></span>
            <input type="range" min="-20" max="20" step="1" value={fundamentalAdjustment} onChange={(event) => setFundamentalAdjustment(Number(event.target.value))} />
          </label>
          <label>
            <span>{isFinancial ? 'PBR·PER 조정' : 'PER 조정'}<strong>{multipleAdjustment > 0 ? '+' : ''}{multipleAdjustment}%</strong></span>
            <input type="range" min="-20" max="20" step="1" value={multipleAdjustment} onChange={(event) => setMultipleAdjustment(Number(event.target.value))} />
          </label>
          {horizon > 0 && (
            <label>
              <span>할인율 조정<strong>{discountAdjustment > 0 ? '+' : ''}{discountAdjustment}%p</strong></span>
              <input type="range" min="-2" max="2" step="0.5" value={discountAdjustment} onChange={(event) => setDiscountAdjustment(Number(event.target.value))} />
            </label>
          )}
        </div>
        <div className="sensitivity-result">
          <span>시험 VM</span><strong>{formatWon(calculatedVm)}</strong>
          <small>괴리율 {formatPercent(calculatedGap, true)}</small>
          <button type="button" onClick={reset}>공식 가정으로 초기화</button>
        </div>
        <p>이 계산은 현재 기기 화면에서만 실행되며 공식 VM과 공개 데이터에는 영향을 주지 않습니다.</p>
      </div>
    </details>
  )
}

function CompanyDetail({ company, isWatched, onToggleWatch }) {
  const [shareState, setShareState] = useState('')
  if (!company) return null
  const financials = company.financials
  const latestReport = financials.latestReport
  const latestIsAnnual = latestReport?.reportCode === '11011'
  const issueUrl = `${REPOSITORY_URL}/issues/new?title=${encodeURIComponent(`[${company.code}] ${company.name} 리서치 업데이트`)}&body=${encodeURIComponent(`기업: ${company.name} (${company.code})\n검토할 항목:\n근거 링크:\n제안 변경사항:`)}`
  const metrics = latestReport
    ? [
        [latestIsAnnual ? '연간 매출' : '누적 매출', formatEok(latestReport.revenueEok)],
        ['영업이익률', formatPercent(latestReport.operatingMargin)],
        [latestIsAnnual ? 'ROE' : '연환산 ROE', formatPercent(latestReport.roeAnnualized)],
        ['기말 부채비율', formatPercent(latestReport.debtRatio)],
        [latestIsAnnual ? '연간 EPS' : '누적 EPS', formatWon(latestReport.epsCumulative)],
      ]
    : [
        ['2025 매출', formatEok(financials.revenue2025)],
        ['영업이익률', formatPercent(financials.operatingMargin2025)],
        ['ROE', formatPercent(financials.roe2025)],
        ['부채비율', formatPercent(financials.debtRatio2025)],
        ['2025 EPS', formatWon(financials.eps2025)],
      ]

  const shareCompany = async () => {
    const url = new URL(window.location.href)
    url.hash = `company=${company.code}`
    try {
      if (navigator.share) {
        await navigator.share({ title: `${company.name} · CAP`, text: `${company.name} CAQM·VM 분석`, url: url.toString() })
        setShareState('공유 완료')
      } else {
        await navigator.clipboard.writeText(url.toString())
        setShareState('주소 복사됨')
      }
    } catch (error) {
      if (error?.name !== 'AbortError') setShareState('공유 실패')
    }
    window.setTimeout(() => setShareState(''), 2200)
  }

  return (
    <section className="company-section" id="company">
      <div className="page-shell">
        <div className="company-header">
          <div>
            <div className="company-rankline"><span>OFFICIAL #{company.rank}</span><span>{company.code}</span><span>{company.sector}</span></div>
            <h2>{company.name}</h2>
            <p>{company.reason}</p>
            <div className="company-actions">
              <button type="button" className={isWatched ? 'is-active' : ''} onClick={() => onToggleWatch(company.code)}>{isWatched ? '★ 관심기업 저장됨' : '☆ 관심기업 추가'}</button>
              <button type="button" onClick={shareCompany}>공유 주소 만들기</button>
              <button type="button" onClick={() => window.print()}>인쇄·PDF</button>
              {shareState && <span role="status">{shareState}</span>}
            </div>
          </div>
          <div className="company-score-card">
            <span>CAQM</span><strong>{company.caqm}</strong><small>/ 100</small>
          </div>
        </div>

        <div className="company-layout">
          <article className="detail-card score-card">
            <div className="card-heading"><span>01</span><div><small>QUALITY MODEL</small><h3>CAQM 세부 점수</h3></div></div>
            <ScoreBreakdown company={company} />
            <QualityEvidenceLedger company={company} />
          </article>

          <article className="detail-card valuation-card">
            <div className="card-heading"><span>02</span><div><small>VALUE MODEL</small><h3>가격과 적정가</h3></div></div>
            <div className="valuation-hero">
              <div><span>현재 괴리율</span><strong className={gapTone(company.gapRate)}>{formatPercent(company.gapRate, true)}</strong><small>{gapLabel(company.gapRate)}</small></div>
              <div className="valuation-price"><span>현재가</span><strong>{formatWon(company.currentPrice)}</strong><small>{formatDate(company.priceBasisDate)}</small></div>
            </div>
            <ValuationModelPanel company={company} />
            <VmScenarioPanel company={company} />
            <SensitivityCalculator company={company} />
            <div className="opinion-banner">
              <div><span>{company.ratingStars}</span><strong>{company.opinion || company.ratingLabel || '검토 중'}</strong></div>
              <small>{company.vmStatus === 'draft' ? 'VM 가정 초안' : company.vmStatus}</small>
            </div>
            <p className="vm-note"><strong>가정 안내</strong>{company.vmNote}</p>
          </article>

          <article className="detail-card financial-card">
            <div className="card-heading"><span>03</span><div><small>FINANCIAL SNAPSHOT</small><h3>{latestReport?.periodLabel || '2025 사업보고서'} 핵심 재무</h3></div></div>
            {latestReport && (
              <div className="financial-report-meta">
                <span>{latestReport.fsDiv === 'CFS' ? '연결 재무제표' : '별도 재무제표'}</span>
                <span>{formatDate(latestReport.periodEnd)} 기준</span>
                <span className={latestReport.dataQuality === 'complete' ? 'complete' : 'partial'}>
                  {latestReport.dataQuality === 'complete' ? '주요 지표 수신 완료' : '일부 계정 미수신'}
                </span>
                {latestReport.rceptNo && (
                  <a href={`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${latestReport.rceptNo}`} target="_blank" rel="noreferrer">공시 원문 ↗</a>
                )}
              </div>
            )}
            <div className="financial-grid">
              {metrics.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
            </div>
            {!latestReport && <p className="sector-note">최신 분기·반기 보고서가 수신되기 전까지 검증된 2025년 사업보고서 수치를 표시합니다.</p>}
            {(company.sector.includes('금융') || company.sector.includes('보험')) && (
              <p className="sector-note">금융·보험사는 일반기업 부채비율 대신 CET1, K-ICS, 손해율과 신용비용을 함께 평가합니다.</p>
            )}
          </article>

          <article className="detail-card thesis-card">
            <div className="card-heading"><span>04</span><div><small>RESEARCH JUDGEMENT</small><h3>핵심 판단과 리스크</h3></div></div>
            <div className="judgement-block positive"><span>투자 논리</span><p>{company.reason}</p></div>
            <div className="judgement-block risk"><span>핵심 리스크</span><p>{company.risk}</p></div>
          </article>

          <article className="detail-card followup-card">
            <div className="card-heading"><span>05</span><div><small>FOLLOW-UP</small><h3>지속 점검 항목</h3></div></div>
            <div className="issues-list">
              {company.issues.length ? company.issues.map((issue, index) => (
                <div className="issue-item" key={issue.id || issue.title || index}>
                  <div className="issue-topline">
                    <span className={`priority priority-${issue.priority || 'medium'}`}>{issue.priority === 'high' ? '중요' : '점검'}</span>
                    <span>{issue.status === 'monitoring' ? '모니터링 중' : issue.status || '검토 중'}</span>
                    <time>{issue.nextReviewAt ? `${formatDate(issue.nextReviewAt)} 재검토` : ''}</time>
                  </div>
                  <strong>{issue.title || issue.name || '점검 항목'}</strong>
                  <p>{issue.impact || issue.description || ''}</p>
                </div>
              )) : <div className="empty-inline">등록된 점검 항목이 없습니다.</div>}
            </div>
          </article>

          <article className="detail-card sources-card">
            <div className="card-heading"><span>06</span><div><small>EVIDENCE & REVIEW</small><h3>근거 자료와 검토 상태</h3></div></div>
            <div className="review-line">
              <div className={`confidence-grade grade-${String(company.vmConfidence?.grade || 'D').toLowerCase()}`}><span>VM 신뢰도</span><strong>{company.vmConfidence?.grade || 'D'} · {company.vmConfidence?.label || '검토 필요'}</strong></div>
              <div><span>검토 상태</span><strong>{company.review?.status || '검토 필요'}</strong></div>
              <div><span>최근 검토</span><strong>{formatDate(company.review?.reviewedAt)}</strong></div>
              <div><span>다음 검토</span><strong>{formatDate(company.review?.nextReviewAt)}</strong></div>
            </div>
            <div className="basis-date-grid">
              <div><span>주가 기준일</span><strong>{formatDate(company.review?.basisDates?.price || company.priceBasisDate)}</strong></div>
              <div><span>재무보고서 기준일</span><strong>{formatDate(company.review?.basisDates?.financialReport || latestReport?.periodEnd)}</strong></div>
              <div><span>예상 EPS 검토일</span><strong>{formatDate(company.review?.basisDates?.eps || company.review?.reviewedAt)}</strong></div>
              <div><span>PER·PBR 검토일</span><strong>{formatDate(company.review?.basisDates?.multiple || company.review?.reviewedAt)}</strong></div>
              <div><span>다음 재검토</span><strong>{formatDate(company.review?.basisDates?.nextReview || company.review?.nextReviewAt)}</strong></div>
            </div>
            {!!company.vmConfidence?.reasons?.length && (
              <div className="confidence-reasons">{company.vmConfidence.reasons.map((reason) => <span key={reason}>{reason}</span>)}</div>
            )}
            <div className="source-links">
              {company.sources.map((source, index) => (
                <a href={source.url} target="_blank" rel="noreferrer" key={`${source.url}-${index}`}>
                  <span>{String(index + 1).padStart(2, '0')}</span>{source.label || source.url}<i>↗</i>
                </a>
              ))}
            </div>
            <div className="editor-actions">
              <a className="button primary small" href={DATA_EDIT_URL} target="_blank" rel="noreferrer">VM·가정 직접 수정 ↗</a>
              <a className="button ghost small" href={issueUrl} target="_blank" rel="noreferrer">새 이슈 등록 ↗</a>
            </div>
          </article>
        </div>
      </div>
    </section>
  )
}

function Methodology({ methodology }) {
  return (
    <section className="method-section" id="methodology">
      <div className="page-shell">
        <SectionHeading
          eyebrow={methodology.version || 'OFFICIAL v1.0'}
          title="CAQM과 VM은 이렇게 구분합니다."
          description="CAQM(Compound Asset Quality Model)은 기업이 장기간 복리성장을 만들 수 있는지를 평가하고, VM(Value Model)은 그 기업의 현재 적정가치를 평가합니다."
        />
        <div className="method-grid">
          {[
            ['01', '경쟁우위·해자', '30점', '브랜드, 시장지배력, 기술·IP, 네트워크 효과, 전환비용, 원가·규제 장벽'],
            ['02', '장기 성장성', '25점', '매출과 EPS 추세, 산업 성장, 수주 가시성, 해외 확장, AI·신사업'],
            ['03', '수익성·현금창출', '20점', 'ROE와 ROIC, 영업이익률, FCF, 현금전환, 이익의 지속 가능성'],
            ['04', '재무건전성', '15점', '순차입금, 유동성, 이자부담, 신용등급, 위기 대응력과 자본비율'],
            ['05', '경영진·주주환원', '10점', '자본배분, 배당 지속성, 지배구조, 실제 자사주 소각과 총주주환원'],
          ].map(([numberLabel, title, score, copy]) => (
            <article key={numberLabel}>
              <div><span>{numberLabel}</span><strong>{score}</strong></div>
              <h3>{title}</h3>
              <p>{copy}</p>
            </article>
          ))}
        </div>
        <div className="method-flow">
          <div><span>STEP 1</span><strong>CAQM</strong><small>Compound Asset Quality Model</small><p>복리자산 품질평가 · 좋은 기업인가?</p></div>
          <i>+</i>
          <div><span>STEP 2</span><strong>VM</strong><small>Value Model</small><p>적정가치 평가 · 좋은 가격인가?</p></div>
          <i>=</i>
          <div className="flow-result"><span>DECISION</span><strong>두 조건의 교집합</strong><p>그때 투자를 검토합니다.</p></div>
        </div>
        <div className="formula-card">
          <div><span>GENERAL VALUE MODEL</span><h3>일반기업 Final VM</h3></div>
          <p><strong>3년 예상 EPS</strong><i>×</i><strong>기준 PER + 해외 보정</strong><i>→</i><strong>3년 후 가치</strong><i>÷</i><strong>(1 + 할인율)<sup>3</sup></strong></p>
          <small>기준 PER은 과거 5년 평균입니다. 해외 보정은 (해외 유사기업 PER - 기준 PER)의 30%만 반영합니다. 할인율은 성장 10% · 일반 11% · 경기민감 12%가 원칙이며, 현재 PER 입력값은 근거 검토 전 초기 이관값입니다.</small>
        </div>
        <div className="sector-model-grid">
          <article>
            <div className="sector-model-head"><span>FINANCIALS</span><strong>BPS·PBR 주평가</strong></div>
            <h3>은행·금융지주는 장부가치에서 출발합니다.</h3>
            <p className="sector-formula">정상화 BPS × 적정 PBR <i>→</i> 주평가<br />정상화 EPS × 적정 PER <i>→</i> 교차검증</p>
            <div className="roe-pbr-scale">
              <span>ROE 7~8%<b>0.60~0.75배</b></span>
              <span>ROE 9~10%<b>0.80~0.95배</b></span>
              <span>ROE 11~12%<b>1.00~1.15배</b></span>
            </div>
            <small>ROE가 자기자본비용보다 높고 지속 가능할 때만 1배 이상을 인정합니다. CET1·연체율·NPL·대손비용과 배당+실제 자사주 소각으로 배수를 조정합니다.</small>
            <div className="model-examples"><span>KB금융 <b>PBR 1.10 · PER 9.5 · VM 186,000원</b></span><span>하나금융 <b>PBR 0.95 · PER 8.0 · VM 155,000원</b></span></div>
          </article>
          <article>
            <div className="sector-model-head"><span>SEMICONDUCTOR</span><strong>정상화 EPS·PER</strong></div>
            <h3>메모리는 피크 EPS를 배제합니다.</h3>
            <p className="sector-formula">2~3년 정상화 EPS × 정상 PER<br />÷ (1 + 할인율)<sup>2~3</sup> <i>→</i> Final VM</p>
            <ul className="model-checks"><li>DRAM 계약가격</li><li>HBM 가격·출하량</li><li>재고</li><li>CAPEX</li><li>고객사 재고</li></ul>
            <small>Micron 등 해외 동종기업의 Forward PER·EV/EBITDA·영업이익률·ROE를 비교하고 HBM 경쟁력과 시장점유율로 프리미엄 또는 할인을 적용합니다.</small>
            <div className="model-examples"><span>삼성전자 <b>72,398원 × 6.5배 · VM 389,000원</b></span><span>SK하이닉스 <b>512,706원 × 7.0배 · VM 2,913,000원</b></span></div>
          </article>
          <article>
            <div className="sector-model-head"><span>FINANCIAL TYPES</span><strong>업종별 분리</strong></div>
            <h3>금융업 안에서도 같은 산식을 쓰지 않습니다.</h3>
            <div className="financial-type-list">
              <span><b>은행·금융지주</b>PBR 중심 · PER 교차검증</span>
              <span><b>증권·복합금융</b>PER·PBR 병행</span>
              <span><b>보험</b>PBR·CSM·SOTP 병행</span>
            </div>
            <small>주주환원은 발표한 매입액이 아니라 배당수익률과 실제 소각 완료분만 인정합니다. 보험은 K-ICS, CSM, 투자자산과 손해율을 함께 확인합니다.</small>
          </article>
        </div>
        <div className="decision-policy">
          <div>
            <span>DECISION RULE</span>
            <h3>가격 판단 표시 기준</h3>
          </div>
          <ul>
            <li><strong>★★★★★</strong><span>괴리율 -20% 이하</span><small>적극 검토</small></li>
            <li><strong>★★★★☆</strong><span>-20% 초과 ~ -10% 이하</span><small>분할 검토</small></li>
            <li><strong>★★★☆☆</strong><span>-10% 초과</span><small>관찰</small></li>
          </ul>
          <p>{methodology.ratingPolicy}</p>
        </div>
        <aside className="disclaimer">
          <span aria-hidden="true">!</span>
          <div><strong>분석 모델 사용 안내</strong><p>{methodology.disclaimer || '본 자료는 공개 정보를 바탕으로 한 연구용 분석이며 매수·매도 권유나 수익 보장이 아닙니다.'}</p></div>
        </aside>
      </div>
    </section>
  )
}

function Footer({ snapshot }) {
  return (
    <footer>
      <div className="page-shell footer-grid">
        <div className="footer-brand"><LogoMark /><div><strong>CAP</strong><span>CAQM RESEARCH</span></div></div>
        <p>좋은 기업을 적정가보다 싸게 사서 오래 보유한다.</p>
        <p className="footer-creator">제작자: PKC</p>
        <div className="footer-links">
          <a href={REPOSITORY_URL} target="_blank" rel="noreferrer">GitHub ↗</a>
          <a href="https://opendart.fss.or.kr/" target="_blank" rel="noreferrer">OpenDART ↗</a>
          <span>{formatDate(snapshot.basisDate)} 기준</span>
        </div>
      </div>
    </footer>
  )
}

function LoadingScreen() {
  return (
    <main className="loading-screen" aria-live="polite">
      <LogoMark />
      <div><strong>CAP</strong><span>최신 리서치 데이터를 불러오는 중입니다.</span></div>
      <span className="loading-bar"><i /></span>
    </main>
  )
}

function ErrorScreen({ message, onRetry }) {
  return (
    <main className="error-screen">
      <LogoMark />
      <p className="section-eyebrow">DATA CONNECTION</p>
      <h1>최신 데이터에 연결하지 못했습니다.</h1>
      <p>{message}</p>
      <button type="button" className="button primary" onClick={onRetry}>다시 불러오기</button>
    </main>
  )
}

export default function App() {
  const [snapshot, setSnapshot] = useState(null)
  const [selectedCode, setSelectedCode] = useState(null)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)
  const [screener, setScreener] = useState(null)
  const [screenerLoadState, setScreenerLoadState] = useState('loading')
  const [screenerError, setScreenerError] = useState('')
  const [screenerRetryKey, setScreenerRetryKey] = useState(0)
  const [openSharedCompany, setOpenSharedCompany] = useState(() => Boolean(companyCodeFromHash()))
  const [watchlist, setWatchlist] = useState(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(WATCHLIST_STORAGE_KEY) || '[]')
      return Array.isArray(stored) ? stored.filter((code) => /^\d{6}$/.test(code)) : []
    } catch {
      return []
    }
  })

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    fetch(DATA_URL, { cache: 'no-store', signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`데이터 응답 오류 (${response.status})`)
        return response.json()
      })
      .then((raw) => {
        const normalized = normalizeSnapshot(raw)
        if (!normalized.companies.length) throw new Error('분석 기업 데이터가 비어 있습니다.')
        setSnapshot(normalized)
        setSelectedCode((current) => {
          if (current && normalized.companies.some((company) => company.code === current)) return current
          const sharedCode = companyCodeFromHash()
          return normalized.companies.some((company) => company.code === sharedCode)
            ? sharedCode
            : normalized.companies[0].code
        })
      })
      .catch((fetchError) => {
        if (fetchError.name !== 'AbortError') setError(fetchError.message || '알 수 없는 오류')
      })
    return () => controller.abort()
  }, [retryKey])

  useEffect(() => {
    const controller = new AbortController()
    setScreenerLoadState('loading')
    setScreenerError('')
    fetch(SCREENER_URL, { cache: 'no-store', signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`스크리너 데이터 응답 오류 (${response.status})`)
        return response.json()
      })
      .then((raw) => {
        const normalized = normalizeScreener(raw)
        if (!normalized.companies.length) throw new Error('아직 공개된 전체시장 기업 데이터가 없습니다.')
        setScreener(normalized)
        setScreenerLoadState('ready')
      })
      .catch((fetchError) => {
        if (fetchError.name === 'AbortError') return
        setScreener(null)
        setScreenerError(fetchError.message || '스크리너 데이터를 불러오지 못했습니다.')
        setScreenerLoadState('unavailable')
      })
    return () => controller.abort()
  }, [screenerRetryKey])

  useEffect(() => {
    window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(watchlist))
  }, [watchlist])

  useEffect(() => {
    if (!openSharedCompany || !snapshot || !selectedCode) return
    window.requestAnimationFrame(() => {
      document.getElementById('company')?.scrollIntoView({ behavior: 'smooth' })
      setOpenSharedCompany(false)
    })
  }, [openSharedCompany, selectedCode, snapshot])

  useEffect(() => {
    const handleHashChange = () => {
      const code = companyCodeFromHash()
      if (code && snapshot?.companies.some((company) => company.code === code)) {
        setSelectedCode(code)
        window.requestAnimationFrame(() => document.getElementById('company')?.scrollIntoView({ behavior: 'smooth' }))
      }
    }
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [snapshot])

  const selectCompany = (code) => {
    setSelectedCode(code)
    const nextUrl = new URL(window.location.href)
    nextUrl.hash = `company=${code}`
    window.history.replaceState(null, '', nextUrl.toString())
  }

  const toggleWatchlist = (code) => {
    setWatchlist((current) => (
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code]
    ))
  }

  const selectedCompany = snapshot?.companies.find((company) => company.code === selectedCode)
    || snapshot?.companies[0]

  if (error) return <ErrorScreen message={error} onRetry={() => setRetryKey((value) => value + 1)} />
  if (!snapshot) return <LoadingScreen />

  return (
    <>
      <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
      <Header basisDate={snapshot.basisDate} />
      <PwaUpdateNotice />
      <main id="main-content">
        <Hero snapshot={snapshot} />
        <LatestChanges snapshot={snapshot} onSelect={selectCompany} />
        <DataPolicy />
        <MatrixSection snapshot={snapshot} selectedCode={selectedCompany?.code} onSelect={selectCompany} />
        <Top20Table
          companies={snapshot.companies}
          selectedCode={selectedCompany?.code}
          onSelect={selectCompany}
          watchlist={watchlist}
          onToggleWatch={toggleWatchlist}
        />
        <CandidateWatchlist master={snapshot.officialMaster} />
        <CompanyDetail
          key={selectedCompany?.code}
          company={selectedCompany}
          isWatched={watchlist.includes(selectedCompany?.code)}
          onToggleWatch={toggleWatchlist}
        />
        <Methodology methodology={snapshot.methodology} />
        <ScreenerSection
          screener={screener}
          loadState={screenerLoadState}
          error={screenerError}
          onRetry={() => setScreenerRetryKey((value) => value + 1)}
        />
      </main>
      <Footer snapshot={snapshot} />
    </>
  )
}
