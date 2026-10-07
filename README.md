# Stock Analyzer v0.3

SEC EDGAR에 공시된 10-K XBRL 데이터로 미국 상장사의 최근 5개 회계연도 재무지표를 계산하고 비교하는 도구입니다.

**바로 써보기:** https://stock-analyzer-hyozzing.streamlit.app (설치 없이 브라우저에서 실행)

## 프로젝트 목적

- 미국 상장사의 재무제표를 SEC 원본 데이터에서 직접 가져와 지표로 정리합니다.
- 기업마다 다른 XBRL 태그 사용 방식에 대응하되, **값을 추정하거나 0으로 채우지 않습니다.** 확인할 수 없는 값은 N/A로 두고 그 이유를 보여줍니다.

## 주요 기능

- 티커를 최대 3개 입력해 최근 5개 회계연도 재무지표를 표로 조회
- 기업 비교 요약표 (각 기업의 최신 회계연도 기준)
- 지표별 출처 표시: Debt, Liabilities 계산에 사용한 XBRL 태그와 N/A 사유
- 재무상태표 등식 검증 (Assets = Liabilities + Equity)
- 콘솔 버전 제공 (`python sec_financials.py`)

## 사용 기술

- Python 3.12
- Streamlit (웹 화면), pandas (표 구성)
- requests (SEC API 조회), rich (콘솔 출력)

## 실행 방법

1. 패키지를 설치합니다.

   ```bash
   pip install -r requirements.txt
   ```

2. SEC 연락처를 설정합니다. SEC는 요청 시 User-Agent에 **연락 가능한 이메일**을 넣도록 요구합니다. 설정하지 않으면 조회 시 안내 메시지가 나옵니다.

   `.streamlit/secrets.toml` 파일을 만들고 아래처럼 적습니다. 이 파일은 `.gitignore`에 포함되어 GitHub에 올라가지 않습니다.

   ```toml
   SEC_USER_AGENT = "이름(또는 앱 이름) 연락용이메일@example.com"
   ```

   환경변수 `SEC_USER_AGENT`로 설정해도 됩니다. 환경변수가 secrets.toml보다 우선합니다.

3. 앱을 실행합니다.

   ```bash
   python -m streamlit run app.py
   ```

- Windows에서는 `run_app.bat`을 더블클릭해도 실행됩니다.
- `.streamlit/config.toml`의 `runOnSave = true` 설정으로, 코드를 저장하면 앱이 자동으로 다시 실행됩니다.
- Streamlit Community Cloud에 배포할 때는 앱 설정의 **Secrets**에 위 `SEC_USER_AGENT` 한 줄을 넣습니다.

## 주요 지표

| 구분 | 지표 |
|---|---|
| 손익 | Revenue, Operating Income, Net Income, Diluted EPS |
| 성장·수익성 | Revenue Growth, EPS Growth, Operating Margin, Net Margin, ROE |
| 재무상태 | Assets, Liabilities, Equity, Debt (이자부채) |
| 현금흐름 | Operating Cash Flow, CapEx, Free Cash Flow, FCF Growth, FCF Margin |
| 레버리지 | Debt-to-Equity, Debt-to-Assets |

- 금액 단위는 십억 달러(B), EPS는 USD/share입니다.
- Debt는 이자가 붙는 차입금(상업어음, 단기차입금, 장기부채)입니다. 리스 부채는 포함하지 않습니다.

## 데이터 출처

- [SEC EDGAR Company Facts API](https://www.sec.gov/edgar/sec-api-documentation) (`data.sec.gov/api/xbrl/companyfacts`)
- 10-K(연간 보고서) 데이터만 사용합니다.
- 티커는 SEC 티커 목록(`company_tickers.json`)으로 CIK를 찾아 조회합니다.

## Validation 방식

- **재무상태표 등식:** SEC 원본 정수값(USD)으로 Assets = Liabilities + Equity를 허용오차 없이 비교합니다.
  - 메자닌(임시) 자본이 보고된 연도는 Assets = Liabilities + 메자닌 자본 + Equity로 검증합니다. 메자닌 자본은 Liabilities와 Equity 어느 쪽에도 더하지 않습니다.
- **태그 fallback:** 회계연도마다 우선순위대로 태그를 시도합니다. 앞 태그로 값을 찾은 연도는 뒤 태그로 덮어쓰지 않습니다.
- **$0 판정:** Debt는 모든 구성 항목이 SEC에 0으로 명시 보고된 경우에만 $0으로 표시합니다. 데이터 부재, 태그 미탐지, 판단 불가능한 경우는 N/A입니다.
- **회귀 테스트:** 로직을 바꿀 때마다 GOOGL, ORCL, MSFT, NVDA, AAPL, META, AMZN의 전체 지표를 수정 전 결과와 비교해 기존 값이 바뀌지 않았는지 확인했습니다.

## 대표적인 Edge Case

| 기업 | 상황 | 처리 |
|---|---|---|
| META | 유동성 장기부채가 $0이라 `LongTermDebtCurrent` 태그를 보고하지 않음 | 유동분을 포함한 장기부채 총액 `LongTermDebt`로 Debt 계산 |
| MSFT 등 | 상업어음 잔액이 없는 해에 `CommercialPaper` 태그를 생략 | 0으로 간주하고 합산, 화면에 "미보고 → $0으로 간주" 표시 |
| ORCL | `Liabilities` 태그 미보고 | 유동부채 + 비유동부채로 계산 |
| AMZN | 재무상태표에 총부채 합계 줄이 없음 | 부채와 자본 총계 − 총자본으로 계산. 항목별 합산과 일치하는지 교차검증함 |
| TSLA | 상환 가능 비지배지분(메자닌 자본)이 별도 구간에 있어 등식 diff 발생 | 등식에 메자닌 자본을 포함해 검증 (diff $0) |
| JPM | 예금을 받는 은행형 재무구조 | Debt, D/E, D/A는 N/A (예금·Repo도 이자부 조달이라 같은 의미의 이자부채를 정의할 수 없음). 영업이익·CapEx 미보고 사유 표시 |

## 향후 계획

- 은행 전용 지표 추가 (예: 충당금 전 이익(PPNR), 예대율)
- 회귀 테스트를 자동화된 테스트 코드로 정리
- 10-K 본문(HTML) 재무제표와 XBRL 값의 자동 대조
- 분기(10-Q) 데이터 지원
