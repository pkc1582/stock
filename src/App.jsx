import { useEffect, useMemo, useState } from 'react'

const REPOSITORY_URL = 'https://github.com/psa1582/stock'
const DATA_EDIT_URL = `${REPOSITORY_URL}/edit/main/data/manual-overrides.json`
const DATA_URL = `${import.meta.env.BASE_URL}data/latest.json`

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

  return {
    ...company,
    rank: firstNumber(company.rank) ?? index + 1,
    code: String(company.code || company.ticker || '').padStart(6, '0'),
    name: company.name || company.companyName || company.nameKo || `기업 ${index + 1}`,
    sector: company.sector || company.industry || '미분류',
    cavm: firstNumber(company.cavm, company.cavmScore, company.score) ?? 0,
    components: {
      moat: firstNumber(components.moat, components.competitiveAdvantage) ?? 0,
      growth: firstNumber(components.growth) ?? 0,
      profitability: firstNumber(components.profitability, components.profit) ?? 0,
      financialHealth: firstNumber(components.financialHealth, components.financial) ?? 0,
      management: firstNumber(components.management, components.shareholderReturn) ?? 0,
    },
    currentPrice,
    priceBasisDate: company.priceBasisDate || market.priceAsOf || market.asOf || null,
    forwardEps3y: firstNumber(company.forwardEps3y, valuation.forwardEps3Y, valuation.eps3y),
    peerPer: firstNumber(company.peerPer, valuation.peerPer, valuation.comparablePer),
    discountRate: firstNumber(company.discountRate, valuation.discountRate),
    finalVm,
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
    },
    sources: (company.sources || []).map((source, sourceIndex) => (
      typeof source === 'string'
        ? { label: `근거 자료 ${sourceIndex + 1}`, url: source }
        : source
    )),
    review: company.review || {
      status: company.reviewStatus || '검토 필요',
      reviewedAt: null,
      nextReviewAt: null,
    },
  }
}

function normalizeSnapshot(raw) {
  const companies = (raw.companies || raw.top20 || raw.data || [])
    .map(normalizeCompany)
    .sort((a, b) => a.rank - b.rank)
  const average = companies.length
    ? companies.reduce((sum, company) => sum + company.cavm, 0) / companies.length
    : 0

  return {
    ...raw,
    generatedAt: raw.generatedAt || raw.asOf || null,
    basisDate: raw.basisDate || raw.asOf || null,
    dataStatus: raw.dataStatus || '공개 자료 기반 분석 스냅샷',
    methodology: raw.methodology || {
      version: raw.methodologyVersion || 'CAVM Official v1.0',
      weights: {},
      formula: '',
      disclaimer: raw.disclaimer || '',
    },
    summary: {
      universeSize: firstNumber(raw.summary?.universeSize, raw.summary?.companyCount) ?? companies.length,
      top20Count: firstNumber(raw.summary?.top20Count) ?? companies.length,
      averageCavm: firstNumber(raw.summary?.averageCavm, raw.summary?.avgCavm) ?? average,
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

const gapTone = (gap) => {
  if (gap === null || gap === undefined) return 'neutral'
  if (gap <= -10) return 'attractive'
  if (gap <= 5) return 'fair'
  return 'expensive'
}

const gapLabel = (gap) => {
  if (gap === null || gap === undefined) return 'VM 입력 대기'
  if (gap <= -10) return '할인 구간'
  if (gap <= 5) return '적정가 근접'
  return '가격 부담'
}

function LogoMark() {
  return (
    <span className="logo-mark" aria-hidden="true">
      <span>20</span><span>45</span>
    </span>
  )
}

function Header({ basisDate }) {
  const [open, setOpen] = useState(false)
  const links = [
    ['#matrix', 'CAVM × 가격'],
    ['#top20', 'TOP20'],
    ['#company', '기업 분석'],
    ['#methodology', '평가 기준'],
  ]

  return (
    <header className="site-header">
      <div className="header-inner">
        <a className="brand" href="#top" aria-label="복리자산 2045 홈">
          <LogoMark />
          <span className="brand-copy">
            <strong>복리자산 2045</strong>
            <small>CAVM RESEARCH</small>
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
  const leader = snapshot.companies[0]
  const summary = snapshot.summary
  return (
    <section className="hero" id="top">
      <div className="hero-grid page-shell">
        <div className="hero-copy">
          <div className="eyebrow"><span /> COMPOUND ASSET RESEARCH</div>
          <h1>좋은 기업과<br /><em>좋은 가격</em>이 만나는 곳.</h1>
          <p className="hero-lead">
            주가를 예측하지 않습니다. 경쟁우위와 현금창출력을 먼저 평가하고,
            충분한 가격 매력이 생길 때까지 기다립니다.
          </p>
          <blockquote>
            “좋은 기업을 찾는 것은 CAVM의 역할이고,<br />좋은 가격을 찾는 것은 VM의 역할이다.”
          </blockquote>
          <div className="hero-actions">
            <a className="button primary" href="#matrix">오늘의 매트릭스 보기 <span>↘</span></a>
            <a className="button ghost" href="#methodology">산정 기준 확인</a>
          </div>
          <div className="data-line">
            <span className="status-dot" />
            <strong>{snapshot.dataStatus}</strong>
            <span>{formatDate(snapshot.basisDate)} 기준</span>
          </div>
        </div>
        <div className="hero-panel" aria-label="오늘의 CAVM 요약">
          <div className="panel-kicker">TODAY&apos;S RESEARCH NOTE</div>
          <div className="hero-panel-top">
            <div>
              <span className="muted-label">CAVM LEADER</span>
              <h2>{leader?.name || '분석 중'}</h2>
              <p>{leader?.sector || 'TOP20 데이터를 불러오는 중입니다.'}</p>
            </div>
            <div className="score-orbit" style={{ '--score': `${leader?.cavm || 0}%` }}>
              <span>{leader?.cavm || '—'}</span>
              <small>/ 100</small>
            </div>
          </div>
          <div className="mini-matrix" aria-hidden="true">
            <span className="axis y">QUALITY</span>
            <span className="axis x">VALUE</span>
            <span className="matrix-line horizontal" />
            <span className="matrix-line vertical" />
            <span className="matrix-zone ideal">좋은 기업<br />좋은 가격</span>
            {snapshot.companies.slice(0, 8).map((company, index) => (
              <span
                key={company.code}
                className={`mini-dot tone-${gapTone(company.gapRate)}`}
                style={{
                  left: `${42 + Math.min(48, (company.cavm - 82) * 2.7)}%`,
                  top: `${Math.max(10, Math.min(87, 51 + (company.gapRate || 0) * 0.42))}%`,
                  '--delay': `${index * 80}ms`,
                }}
              />
            ))}
          </div>
          <div className="hero-stats">
            <div><strong>{summary.universeSize}</strong><span>검토 후보</span></div>
            <div><strong>{summary.averageCavm.toFixed(1)}</strong><span>평균 CAVM</span></div>
            <div><strong>{summary.undervaluedCount}</strong><span>VM 대비 할인</span></div>
          </div>
        </div>
      </div>
    </section>
  )
}

function SectionHeading({ eyebrow, title, description, aside }) {
  return (
    <div className="section-heading">
      <div>
        <div className="section-eyebrow">{eyebrow}</div>
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {aside && <div className="heading-aside">{aside}</div>}
    </div>
  )
}

function SummaryStrip({ snapshot }) {
  const summary = snapshot.summary
  const generated = snapshot.generatedAt
    ? new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(snapshot.generatedAt))
    : '확인 중'

  const items = [
    ['검토 유니버스', `${summary.universeSize}개`, '정량·정성 검토 후보'],
    ['공식 TOP20', `${summary.top20Count}개`, 'CAVM 동점 규칙 반영'],
    ['평균 품질점수', summary.averageCavm.toFixed(1), '100점 만점'],
    ['저평가 구간', `${summary.undervaluedCount}개`, '괴리율 0% 미만'],
  ]

  return (
    <section className="summary-section page-shell" aria-label="데이터 요약">
      <div className="summary-strip">
        {items.map(([label, value, note]) => (
          <div className="summary-item" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{note}</small>
          </div>
        ))}
        <div className="summary-refresh">
          <span className="refresh-icon" aria-hidden="true">↻</span>
          <span>마지막 계산</span>
          <strong>{generated}</strong>
        </div>
      </div>
    </section>
  )
}

function MatrixChart({ companies, selectedCode, onSelect }) {
  const points = companies.filter((company) => Number.isFinite(company.cavm) && Number.isFinite(company.gapRate))
  const width = 1040
  const height = 600
  const margin = { top: 58, right: 42, bottom: 74, left: 78 }
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom
  const xMin = Math.min(80, Math.floor(Math.min(...points.map((point) => point.cavm), 80) / 5) * 5)
  const xMax = 100
  const gapValues = points.map((point) => point.gapRate)
  const yMin = Math.min(-60, Math.floor(Math.min(...gapValues, -40) / 20) * 20)
  const yMax = Math.max(60, Math.ceil(Math.max(...gapValues, 40) / 50) * 50)
  const x = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * innerWidth
  const y = (value) => margin.top + ((yMax - value) / (yMax - yMin)) * innerHeight
  const xTicks = []
  for (let value = Math.ceil(xMin / 5) * 5; value <= xMax; value += 5) xTicks.push(value)
  const yStep = yMax - yMin > 180 ? 50 : 20
  const yTicks = []
  for (let value = Math.ceil(yMin / yStep) * yStep; value <= yMax; value += yStep) yTicks.push(value)
  const cutX = x(90)
  const zeroY = y(0)
  const selected = points.find((point) => point.code === selectedCode)

  return (
    <div className="chart-shell">
      <div className="chart-toolbar">
        <div className="chart-legend" aria-label="괴리율 범례">
          <span><i className="legend-dot attractive" /> 할인 구간</span>
          <span><i className="legend-dot fair" /> 적정가 근접</span>
          <span><i className="legend-dot expensive" /> 가격 부담</span>
        </div>
        <span className="chart-note">원을 선택하면 기업 분석이 바뀝니다</span>
      </div>
      <div className="chart-scroll">
        {points.length ? (
          <svg
            className="matrix-chart"
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-labelledby="matrix-title matrix-desc"
          >
            <title id="matrix-title">한국 TOP20 CAVM과 현재가 괴리율 매트릭스</title>
            <desc id="matrix-desc">오른쪽으로 갈수록 기업의 질 점수가 높고 아래로 갈수록 적정가격보다 저렴합니다.</desc>
            <defs>
              <filter id="point-shadow" x="-100%" y="-100%" width="300%" height="300%">
                <feDropShadow dx="0" dy="3" stdDeviation="4" floodOpacity="0.2" />
              </filter>
            </defs>
            <rect x={margin.left} y={margin.top} width={cutX - margin.left} height={zeroY - margin.top} className="zone low-expensive" />
            <rect x={cutX} y={margin.top} width={width - margin.right - cutX} height={zeroY - margin.top} className="zone high-expensive" />
            <rect x={margin.left} y={zeroY} width={cutX - margin.left} height={height - margin.bottom - zeroY} className="zone low-attractive" />
            <rect x={cutX} y={zeroY} width={width - margin.right - cutX} height={height - margin.bottom - zeroY} className="zone high-attractive" />
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
            <text x={margin.left} y={34} className="zone-label danger">낮은 CAVM · 비싼 가격</text>
            <text x={width - margin.right} y={34} className="zone-label caution" textAnchor="end">높은 CAVM · 비싼 가격</text>
            <text x={margin.left} y={height - margin.bottom - 18} className="zone-label muted">가치함정 주의</text>
            <text x={width - margin.right} y={height - margin.bottom - 18} className="zone-label ideal" textAnchor="end">좋은 기업 · 좋은 가격 ↘</text>
            {points.map((company, index) => {
              const tone = gapTone(company.gapRate)
              const isSelected = company.code === selectedCode
              const cx = x(company.cavm)
              const cy = y(company.gapRate)
              const labelRight = cx < width - 180
              const labelY = index % 3 === 0 ? -13 : index % 3 === 1 ? 20 : 4
              return (
                <g
                  key={company.code}
                  className={`data-point tone-${tone}${isSelected ? ' is-selected' : ''}`}
                  role="button"
                  tabIndex="0"
                  aria-label={`${company.name}, CAVM ${company.cavm}점, 괴리율 ${formatPercent(company.gapRate, true)}`}
                  onClick={() => onSelect(company.code)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onSelect(company.code)
                    }
                  }}
                >
                  <circle cx={cx} cy={cy} r={isSelected ? 12 : 9} className="point-halo" />
                  <circle cx={cx} cy={cy} r={isSelected ? 8 : 6} className="point-core" filter="url(#point-shadow)" />
                  <text x={cx + (labelRight ? 12 : -12)} y={cy + labelY} className="point-label" textAnchor={labelRight ? 'start' : 'end'}>
                    {company.name}
                  </text>
                  <title>{company.name} · CAVM {company.cavm} · 괴리율 {formatPercent(company.gapRate, true)}</title>
                </g>
              )
            })}
            <text x={margin.left + innerWidth / 2} y={height - 16} className="axis-title" textAnchor="middle">CAVM 품질 점수 → 높을수록 좋은 기업</text>
            <text transform={`translate(22 ${margin.top + innerHeight / 2}) rotate(-90)`} className="axis-title" textAnchor="middle">괴리율 (%) → 낮을수록 좋은 가격</text>
          </svg>
        ) : (
          <div className="empty-state">VM 입력이 완료되면 매트릭스가 표시됩니다.</div>
        )}
      </div>
      {selected && (
        <div className={`selected-chart-card tone-${gapTone(selected.gapRate)}`}>
          <span className="rank-token">#{selected.rank}</span>
          <div><strong>{selected.name}</strong><small>{selected.sector}</small></div>
          <div><span>CAVM</span><strong>{selected.cavm}</strong></div>
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
          eyebrow="THE CAVM MATRIX"
          title={<>기업의 질과 가격을<br />한 화면에서 봅니다.</>}
          description="가로축은 CAVM, 세로축은 현재가의 Final VM 대비 괴리율입니다. 오른쪽 아래에 가까울수록 우리가 기다린 조건에 부합합니다."
          aside={<><strong>괴리율 음수</strong><span>= 적정가보다 저렴</span></>}
        />
        <MatrixChart companies={snapshot.companies} selectedCode={selectedCode} onSelect={onSelect} />
      </div>
    </section>
  )
}

function Top20Table({ companies, selectedCode, onSelect }) {
  const [query, setQuery] = useState('')
  const [sector, setSector] = useState('전체 업종')
  const [sort, setSort] = useState('rank')
  const sectors = useMemo(() => ['전체 업종', ...new Set(companies.map((company) => company.sector))], [companies])
  const visible = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return companies
      .filter((company) => sector === '전체 업종' || company.sector === sector)
      .filter((company) => !normalizedQuery || `${company.name} ${company.code}`.toLowerCase().includes(normalizedQuery))
      .sort((a, b) => {
        if (sort === 'cavm') return b.cavm - a.cavm || a.rank - b.rank
        if (sort === 'gap') return (a.gapRate ?? Infinity) - (b.gapRate ?? Infinity)
        return a.rank - b.rank
      })
  }, [companies, query, sector, sort])

  const choose = (company) => {
    onSelect(company.code)
    window.requestAnimationFrame(() => document.getElementById('company')?.scrollIntoView({ behavior: 'smooth' }))
  }

  return (
    <section className="top20-section" id="top20">
      <div className="page-shell">
        <SectionHeading
          eyebrow="KOREA TOP20"
          title="CAVM 우선순위"
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
            {[['rank', '공식 순위'], ['cavm', 'CAVM'], ['gap', '가격 매력']].map(([value, label]) => (
              <button type="button" key={value} className={sort === value ? 'active' : ''} onClick={() => setSort(value)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>순위</th><th>기업</th><th>CAVM</th><th>해자</th><th>성장</th><th>수익</th><th>재무</th><th>환원</th><th>현재가</th><th>Final VM</th><th>괴리율</th><th>판단</th>
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
                    <button className="company-cell" type="button" onClick={() => choose(company)}>
                      <strong>{company.name}</strong><small>{company.code} · {company.sector}</small>
                    </button>
                  </td>
                  <td><strong className="cavm-value">{company.cavm}</strong></td>
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

function CompanyDetail({ company }) {
  if (!company) return null
  const financials = company.financials
  const issueUrl = `${REPOSITORY_URL}/issues/new?title=${encodeURIComponent(`[${company.code}] ${company.name} 리서치 업데이트`)}&body=${encodeURIComponent(`기업: ${company.name} (${company.code})\n검토할 항목:\n근거 링크:\n제안 변경사항:`)}`
  const metrics = [
    ['2025 매출', formatEok(financials.revenue2025)],
    ['영업이익률', formatPercent(financials.operatingMargin2025)],
    ['ROE', formatPercent(financials.roe2025)],
    ['부채비율', formatPercent(financials.debtRatio2025)],
    ['2025 EPS', formatWon(financials.eps2025)],
  ]

  return (
    <section className="company-section" id="company">
      <div className="page-shell">
        <div className="company-header">
          <div>
            <div className="company-rankline"><span>OFFICIAL #{company.rank}</span><span>{company.code}</span><span>{company.sector}</span></div>
            <h2>{company.name}</h2>
            <p>{company.reason}</p>
          </div>
          <div className="company-score-card">
            <span>CAVM</span><strong>{company.cavm}</strong><small>/ 100</small>
          </div>
        </div>

        <div className="company-layout">
          <article className="detail-card score-card">
            <div className="card-heading"><span>01</span><div><small>QUALITY MODEL</small><h3>CAVM 세부 점수</h3></div></div>
            <ScoreBreakdown company={company} />
          </article>

          <article className="detail-card valuation-card">
            <div className="card-heading"><span>02</span><div><small>VALUE MODEL</small><h3>가격과 적정가</h3></div></div>
            <div className="valuation-hero">
              <div><span>현재 괴리율</span><strong className={gapTone(company.gapRate)}>{formatPercent(company.gapRate, true)}</strong><small>{gapLabel(company.gapRate)}</small></div>
              <div className="valuation-price"><span>현재가</span><strong>{formatWon(company.currentPrice)}</strong><small>{formatDate(company.priceBasisDate)}</small></div>
            </div>
            <div className="vm-flow">
              <div><span>3년 예상 EPS</span><strong>{formatWon(company.forwardEps3y)}</strong></div>
              <i>×</i>
              <div><span>비교 PER</span><strong>{company.peerPer ?? '—'}배</strong></div>
              <i>→</i>
              <div><span>할인율</span><strong>{formatPercent(company.discountRate)}</strong></div>
              <i>→</i>
              <div className="vm-result"><span>Final VM</span><strong>{formatWon(company.finalVm)}</strong></div>
            </div>
            <div className="opinion-banner">
              <div><span>{company.ratingStars}</span><strong>{company.opinion || company.ratingLabel || '검토 중'}</strong></div>
              <small>{company.vmStatus === 'draft' ? 'VM 가정 초안' : company.vmStatus}</small>
            </div>
            <p className="vm-note"><strong>가정 안내</strong>{company.vmNote}</p>
          </article>

          <article className="detail-card financial-card">
            <div className="card-heading"><span>03</span><div><small>FINANCIAL SNAPSHOT</small><h3>2025 핵심 재무</h3></div></div>
            <div className="financial-grid">
              {metrics.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
            </div>
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
              <div><span>검토 상태</span><strong>{company.review?.status || '검토 필요'}</strong></div>
              <div><span>최근 검토</span><strong>{formatDate(company.review?.reviewedAt)}</strong></div>
              <div><span>다음 검토</span><strong>{formatDate(company.review?.nextReviewAt)}</strong></div>
            </div>
            <div className="source-links">
              {company.sources.map((source, index) => (
                <a href={source.url} target="_blank" rel="noreferrer" key={`${source.url}-${index}`}>
                  <span>{String(index + 1).padStart(2, '0')}</span>{source.label || source.url}<i>↗</i>
                </a>
              ))}
            </div>
            <div className="editor-actions">
              <a className="button primary small" href={DATA_EDIT_URL} target="_blank" rel="noreferrer">EPS·가정 직접 수정 ↗</a>
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
          title="CAVM은 이렇게 계산합니다."
          description="숫자로 확인할 수 있는 항목은 공식 재무자료로 갱신하고, 해자와 경영 판단은 근거와 검토일을 남겨 사람이 책임 있게 평가합니다."
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
          <div><span>STEP 1</span><strong>CAVM</strong><p>좋은 기업인가?</p></div>
          <i>+</i>
          <div><span>STEP 2</span><strong>VM</strong><p>좋은 가격인가?</p></div>
          <i>=</i>
          <div className="flow-result"><span>DECISION</span><strong>두 조건의 교집합</strong><p>그때 투자를 검토합니다.</p></div>
        </div>
        <div className="formula-card">
          <div><span>VALUE MODEL</span><h3>Final VM 산식</h3></div>
          <p><strong>3년 예상 EPS</strong><i>×</i><strong>해외 유사기업 PER</strong><i>→</i><strong>3년 후 가치</strong><i>÷</i><strong>(1 + 할인율)<sup>3</sup></strong></p>
          <small>성장기업 10% · 일반기업 11% · 경기민감기업 12%를 원칙으로 하며, 가정은 기업별 근거와 함께 검토합니다.</small>
        </div>
        <div className="decision-policy">
          <div>
            <span>DECISION RULE</span>
            <h3>가격 판단 표시 기준</h3>
          </div>
          <ul>
            <li><strong>★★★★★</strong><span>괴리율 -20% 이하</span><small>적극 검토</small></li>
            <li><strong>★★★★☆</strong><span>-20% 초과 ~ 10% 이하</span><small>분할 검토</small></li>
            <li><strong>★★★☆☆</strong><span>10% 초과</span><small>관찰</small></li>
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
        <div className="footer-brand"><LogoMark /><div><strong>복리자산 2045</strong><span>CAVM RESEARCH</span></div></div>
        <p>좋은 기업을 적정가보다 싸게 사서 오래 보유한다.</p>
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
      <div><strong>복리자산 2045</strong><span>최신 리서치 데이터를 불러오는 중입니다.</span></div>
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
        setSelectedCode((current) => current || normalized.companies[0].code)
      })
      .catch((fetchError) => {
        if (fetchError.name !== 'AbortError') setError(fetchError.message || '알 수 없는 오류')
      })
    return () => controller.abort()
  }, [retryKey])

  const selectedCompany = snapshot?.companies.find((company) => company.code === selectedCode)
    || snapshot?.companies[0]

  if (error) return <ErrorScreen message={error} onRetry={() => setRetryKey((value) => value + 1)} />
  if (!snapshot) return <LoadingScreen />

  return (
    <>
      <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
      <Header basisDate={snapshot.basisDate} />
      <main id="main-content">
        <Hero snapshot={snapshot} />
        <SummaryStrip snapshot={snapshot} />
        <MatrixSection snapshot={snapshot} selectedCode={selectedCompany?.code} onSelect={setSelectedCode} />
        <Top20Table companies={snapshot.companies} selectedCode={selectedCompany?.code} onSelect={setSelectedCode} />
        <CompanyDetail company={selectedCompany} />
        <Methodology methodology={snapshot.methodology} />
      </main>
      <Footer snapshot={snapshot} />
    </>
  )
}
