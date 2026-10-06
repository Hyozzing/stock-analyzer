# Changelog

원칙: SEC 원본 값으로 계산하고 출력할 때만 billion USD로 변환합니다. 확인할 수 없는 값은 추정하거나 0으로 채우지 않고 N/A로 표시합니다.

## v0.3 (2026-10-01 ~ 2026-10-06)

### 웹 UI (2026-10-01)
- Streamlit 웹 UI 추가 (`app.py`). 계산은 기존 분석 엔진 `sec_financials.py`를 그대로 호출
- 티커를 최대 3개 입력해 조회 (공백 제거, 대문자 변환, 중복·빈 값 제거). Analyze 버튼을 눌렀을 때만 SEC 조회
- 등록되지 않은 티커도 SEC 티커 목록에서 CIK를 찾아 조회
- 잘못된 티커나 SEC 오류(404/403/429, 네트워크)가 있어도 해당 티커만 오류를 표시하고 나머지는 계속 분석
- 지표명을 `English (한국어)` 형식으로 통일 (예: `Revenue (매출)`, `Debt (이자부채)`)

### 데이터 추출 보완
- **NVDA CapEx (2026-10-01):** 표준 태그 대신 `PaymentsToAcquireProductiveAssets`로 보고하는 경우를 CapEx fallback에 추가. FY2026 $6.042B로 10-K와 일치, FCF·FCF Growth·FCF Margin 재계산
- **Debt fallback 레시피 (2026-10-06):** 회사별 검증 조합이 없는 기업은 아래 순서로 회계연도마다 계산
  1. 단기차입 + 유동성 장기부채 + 비유동 장기부채
  2. 단기·장기 이자부채 합계 태그
  3. 단기차입 + 장기부채 총액(유동분 포함)
  4. 유동 차입금 총액 + 비유동 장기부채
  - META: `LongTermDebtCurrent`를 보고하지 않아 N/A이던 Debt를 `LongTermDebt`로 계산 (FY2025 $58.74B)
- **Debt $0 규칙 (2026-10-06):** 모든 구성 항목이 0으로 명시 보고된 경우에만 $0 표시. 일부 항목이 미보고된 상태의 $0은 N/A (AAPL FY2012, NVDA FY2013이 N/A로 바뀜)
- **AMZN Liabilities (2026-10-06):** 총부채 합계 태그가 없는 연도는 `LiabilitiesAndStockholdersEquity − 총자본`으로 계산. 메자닌 자본이 보고된 연도는 제외. 유동부채 + 비유동부채 fallback도 회사 단위가 아니라 연도별로 적용하도록 변경

### 검증 보완 (2026-10-06)
- **TSLA 재무등식:** 상환 가능 비지배지분(메자닌 자본)이 보고된 연도는 Assets = Liabilities + 메자닌 자본 + Equity로 검증. diff $0.06B~$0.57B가 해당 항목과 정확히 일치함을 확인 (허용오차 변경 없음)
- **금융기업 (JPM):** 예금 부채를 보고하는 은행형 재무구조는 Debt·D/E·D/A를 계산하지 않고 N/A 처리. 예금·Repo도 이자부 조달이라 같은 의미의 이자부채를 정의할 수 없음
  - Operating Income·CapEx 미보고 사유도 화면에 안내 (은행 손익계산서에는 영업이익 단계가 없음)

### 화면 안내 (2026-10-06)
- Debt 계산에 사용한 XBRL 태그를 연도별로 표시. 미보고 항목을 0으로 간주한 경우 "미보고 → $0으로 간주" 표시
- N/A 사유 구분: SEC 미보고 / 태그 조합 불가 / $0 확정 불가 / 은행형 재무구조
- Liabilities 차감 계산, 메자닌 자본 포함 검증 여부를 작은 안내 문구로 표시

### 기타 (2026-10-06)
- `run_app.bat` (더블클릭 실행), `.streamlit/config.toml` (저장 시 자동 재실행)
- `README.md`, `CHANGELOG.md` 작성
- 회귀 테스트 대상 확대: GOOGL, ORCL, MSFT, NVDA, AAPL, META, AMZN, TSLA (+ JPM edge case)

## v0.2 (2026-09-08 ~ 2026-09-18)
- **FCF Growth (YoY)** 추가 (2026-09-08). 전년·당해 FCF가 모두 양수일 때만 계산하고, 부호가 바뀌거나 음수인 경우 N/M (Positive → Negative / Negative → Positive / Negative FCF), 전년 FCF가 0이면 N/A
- **재무등식 표시 개선** (2026-09-08): PASS면 표에서 검증 열을 숨기고 FAIL 연도가 있을 때만 경고
- **FCF Margin** 추가 (2026-09-09). FCF가 음수여도 음수 마진으로 표시, Revenue가 0이거나 없으면 N/A
- **Debt-to-Equity, Debt-to-Assets** 추가 (2026-09-09). Equity가 음수면 N/M (Negative Equity), 0이면 N/A
- **ROE** 추가 (2026-09-18). 모회사 귀속 순이익 / 평균 모회사 자본(전년 말 + 당해 말) / 2. 등식 검증용 Equity(비지배지분 포함)와 구분. 평균 자본이 음수면 N/M
- **기업 비교 요약** 추가 (2026-09-18). 최신 회계연도 9개 지표를 회계연도와 함께 나란히 표시 (우열 판정 없음)
- **MSFT 테스트 티커** 추가 (2026-09-18). 전 지표 검증
- **MSFT Debt** (2026-09-18): 잔액이 없는 해에 `CommercialPaper` 태그를 생략하는 경우 optional 구성요소로 처리. FY2026 $40.294B로 10-K Note 10과 일치

## v0.1 (~ 2026-09-07)
- SEC EDGAR Company Facts API로 10-K 데이터 수집 (GOOGL, ORCL)
- Revenue, Operating Income, Net Income, Diluted EPS, Assets, Liabilities와 Revenue Growth, EPS Growth, Operating Margin, Net Margin (대화 기록이 남기 전 작업)
- **Equity** 추가 (2026-09-07). 등식 검증용으로 비지배지분 포함 총자본을 우선 사용하고, 없으면 `StockholdersEquity`
- **재무상태표 등식 검증** (Assets = Liabilities + Equity, 원본 정수값 정확 비교)
- **Debt (이자부채)** 추가. 회사별로 중복 없이 구성 항목 합산 (GOOGL: 상업어음 + 유동성·비유동 장기부채, ORCL: 유동·비유동 Notes payable)
- **Operating Cash Flow, CapEx, Free Cash Flow** 추가. 연간(350일 이상) duration fact만 사용, CapEx는 양수 지출액으로 통일, FCF = OCF − CapEx
- 파일명 `sec_revenue.py` → `sec_financials.py`
