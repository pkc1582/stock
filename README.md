# 복리자산 2045

국내 기업을 **CAVM(기업의 질 평가모형)**로 평가하고, 3년 후 EPS와 해외 유사기업 PER로 산출한 Final VM과 현재 주가를 분리해 보는 공개 리서치 대시보드입니다.

> 좋은 기업을 찾는 것은 CAVM의 역할이고, 좋은 가격을 찾는 것은 VM의 역할입니다. 두 조건이 동시에 충족될 때만 투자를 검토합니다.

이 프로젝트는 매매 기능이 없으며 투자자문을 제공하지 않습니다.

## 평가 구조

CAVM은 총 100점입니다.

| 영역 | 배점 | 주요 평가 항목 |
|---|---:|---|
| 경쟁우위·해자 | 30 | 브랜드, 시장지배력, 기술, 네트워크 효과, 진입장벽, 원가·규제 우위 |
| 성장성 | 25 | 매출·EPS, 시장 확장, AI·신사업, 산업 성장 |
| 수익성 | 20 | ROE, ROIC, 영업이익률, FCF, 현금창출 지속성 |
| 재무건전성 | 15 | 차입금, 현금, 신용도, 위기 대응력 |
| 경영진·주주환원 | 10 | 자본배분, 배당, 실제 자사주 소각, 총주주환원율 |

자사주 매입만으로는 주주환원을 인정하지 않고 **실제 소각**만 반영합니다. 정량 항목은 자동 갱신하지만, 해자·경영진·이슈와 같은 정성 판단은 근거와 재검토일을 남긴 수동 검토를 거칩니다.

Final VM은 기본적으로 다음 흐름으로 산출합니다.

```text
3년 후 예상 EPS × 해외 유사기업 PER = 3년 후 가치
3년 후 가치 ÷ (1 + 할인율)^3 = Final VM
괴리율(%) = (현재가 - Final VM) ÷ Final VM × 100
```

할인율은 고성장 10%, 일반 11%, 경기민감 12%를 기본값으로 사용하되 조정 근거를 남깁니다.

현재 VM 입력은 사람의 검토 전 가정이므로 사이트에서는 매수 권유 대신 `적극 검토(가정)`, `분할 검토(가정)`, `관찰(가정)`로 표시합니다. CAVM 80점 이상을 기본 품질 통과로 보고, 괴리율 -20% 이하 / -20% 초과~10% 이하 / 10% 초과를 각각 세 구간으로 나눕니다. 최종 경계 동점은 해자, 성장성, 현금창출력, 재무건전성, 업종 분산 순으로 검토합니다.

## 데이터 흐름

```text
시세 API ───────┐
                     ├─> data/companies.json ──┐
OpenDART 재무 API ──┘                         ├─> public/data/latest.json ─> 대시보드
data/manual-overrides.json ────────────┘
data/issues.json ──────────────────────────┘
```

- `scripts/fetch_market.py`: 승인된 시세 API로 현재가와 가격 기준일을 갱신합니다.
- `scripts/fetch_dart.py`: OpenDART 실제 공시 재무 항목을 갱신합니다.
- `scripts/build_dataset.py`: 공시 값과 수동 가정을 병합하고 CAVM·VM·괴리율을 재계산합니다.
- API 키와 원본 API 응답은 저장소나 웹 브라우저에 저장하지 않습니다.

최초 공개본의 20개 기업은 2025 연간 재무지표와 2026-08-07 정규장 종가를 2026-08-09에 수동 교차검증한 스냅샷입니다. 원본 제공 페이지를 재배포하지 않으며, 저장소 Secrets가 등록된 뒤부터는 OpenDART와 공공데이터포털 API 결과로 갱신합니다. 해자·경영진·기업 이슈 같은 정성 점수는 자동으로 바꾸지 않고 사람의 근거 검토를 거칩니다.

## 자동 갱신 설정

GitHub 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에 다음 값을 등록합니다.

| Secret | 용도 |
|---|---|
| `DART_API_KEY` | OpenDART 인증키 |
| `STOCK_API_ENDPOINT` | 선택 사항. 미등록 시 공공데이터포털 주식시세 기본 endpoint 사용 |
| `STOCK_API_SERVICE_KEY` | 시세 API 서비스키 |

이후 **Settings → Actions → General → Workflow permissions**에서 Actions가 데이터 스냅샷을 커밋할 수 있도록 `Read and write permissions`를 선택합니다. 예약 작업은 Actions 화면의 `Run workflow`로도 즉시 실행할 수 있습니다.

| 워크플로 | 실행 시각 | 작업 |
|---|---|---|
| `Daily data refresh` | 평일 18:20 KST | 종가·공시·재무 갱신, 공개 JSON 재생성 |
| `Weekly CAVM rebuild` | 매주 토요일 09:00 KST | 최신 입력으로 CAVM 합계·VM·TOP20 재산정 |
| `Deploy GitHub Pages` | `main` 변경 후 | 웹 사이트 빌드·배포 |

GitHub 예약 실행은 부하에 따라 일부 지연될 수 있습니다. 휴장일이거나 API가 일시적으로 실패하면 스크립트는 마지막으로 검증된 스냅샷을 보존합니다.

## GitHub Pages 배포

1. **Settings → Pages**로 이동합니다.
2. Build and deployment의 Source를 **GitHub Actions**로 선택합니다.
3. `Deploy GitHub Pages` 워크플로를 처음 한 번 수동 실행합니다.

배포 워크플로의 권한은 코드 읽기, Pages 쓰기, OIDC 토큰으로 한정됩니다. 데이터 갱신 워크플로만 스냅샷 커밋을 위해 `contents: write`를 사용합니다.

## 사람이 3년 EPS·가정·이슈를 수정하는 법

일반 방문자는 공개 결과만 읽을 수 있고 원본을 직접 변경할 수 없습니다.

1. Issues에서 **CAVM / VM 조정 요청** 또는 **기업 이슈 Follow-up** 양식을 선택합니다.
2. 3년 EPS, 점수·가정, 원문 URL, 기준일, 다음 재검토일을 입력합니다.
3. 관리자가 사실과 추정을 구분해 검토한 뒤 `data/manual-overrides.json` 또는 `data/issues.json`을 PR로 수정합니다.
4. `python scripts/build_dataset.py`로 결과를 재생성하고 빌드를 통과한 뒤 병합합니다.
5. Follow-up 이슈는 다음 공시·분기 실적 확인 후 종료합니다.

수동 가정은 자동 갱신된 공식 재무값을 덮어쓸 수 있지만, 적용 이유·작성일·재검토일이 반드시 필요합니다.

## 로컬 실행

요구 환경은 Python 3.13, Node.js 24, pnpm 11입니다. Python 데이터 스크립트는 표준 라이브러리만 사용합니다.

```bash
python scripts/build_dataset.py
pnpm install --frozen-lockfile
pnpm run dev
```

배포 빌드:

```bash
pnpm run build
```

## 데이터 이용·공개 주의사항

- OpenDART를 통해 받은 공식 공시 항목을 우선하고 공시 기준일을 표시합니다.
- 시세는 재배포가 허용된 API를 사용해야 하며, 공개 사이트에는 제공자의 표시·지연·케싱 조건을 적용해야 합니다.
- Naver 증권·Google Finance 화면을 무단 크롤링해 정기 배포하는 구조는 사용하지 않습니다.
- 저장소에는 대시보드에 필요한 파생 지표·기준일·판단 근거만 커밋하고 API 원본 응답은 커밋하지 않습니다.
- 사용 전 OpenDART·공공데이터·시세 제공자의 현재 약관과 라이선스를 확인해야 합니다.

## 주의

CAVM은 대상을 줄이기 위한 일관된 분석 프레임이지 미래 수익을 보장하는 모형이 아닙니다. CAVM이 높더라도 VM과 현재가 비교, 업종 위험, 최신 공시를 별도로 확인해야 합니다.
