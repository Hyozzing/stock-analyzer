# Validation Case Studies

SEC XBRL 보고 방식의 기업별 차이로 생긴 문제 5건의 진단과 검증 기록입니다. 구현 상세는 [CHANGELOG.md](CHANGELOG.md)를 참고하세요.

## 1. NVDA — CapEx 태그 차이

- **Problem:** CapEx, FCF, FCF Growth, FCF Margin이 N/A
- **Cause:** NVDA는 표준 태그 `PaymentsToAcquirePropertyPlantAndEquipment` 대신 `PaymentsToAcquireProductiveAssets`(유형+무형자산 취득)로 보고
- **Fix:** CapEx fallback 태그 확장
- **Validation:** FY2026 CapEx $6.042B, FY2025 $3.236B로 10-K 현금흐름표와 일치. FCF = $102.718B − $6.042B = $96.676B
  - 참고: AMZN FY2016은 두 태그 값이 서로 달라($6.737B / $7.804B) 우선순위 규칙이 필요함(화면 표시 범위 밖)

## 2. META — Debt 구성 차이

- **Problem:** Debt, D/E, D/A가 모든 연도 N/A
- **Cause:** 유동성 장기부채가 없어 `LongTermDebtCurrent`를 보고하지 않음. 기본 공식이 이 태그를 필수로 요구
- **Fix:** 회계연도별 범용 Debt fallback recipe. META는 `LongTermDebt`(유동분 포함 장기부채 총액)로 계산
- **Validation:** FY2025 $58.74B, FY2024 $28.83B, FY2023 $18.39B, FY2022 $9.92B
  - 원금 만기표 합계 − 미상각 할인과 일치 (FY2025 $59.00B − $0.26B)
  - FY2021은 $0 확정 근거가 없어 N/A 유지

## 3. AMZN — Liabilities 미추출

- **Problem:** Liabilities가 FY2021~2025 N/A, 재무등식 검증 불가
- **Cause:** 재무상태표에 "Total liabilities" 합계 줄이 없어 `Liabilities`, `LiabilitiesNoncurrent` 미보고
- **Fix:** `LiabilitiesAndStockholdersEquity − StockholdersEquity`
- **Validation:** 5개년 A = L + E PASS (FY2025 $818.042B − $411.065B = $406.977B)
  - 차감으로 구한 값이라 PASS만으로는 부족해, 부채 항목 5개를 직접 더한 합계와 독립 대조함
  - 5개년 모두 달러 단위까지 일치

## 4. TSLA — 회계등식 FAIL

- **Problem:** FY2021~2025 A − (L + E) = $0.06B~$0.57B
- **Cause:** 부채와 자본 사이에 별도 보고된 메자닌 자본(상환 가능 비지배지분) 누락
- **Fix:** 메자닌 자본이 실제 보고된 연도만 A = L + 메자닌 + E로 검증. 허용오차는 0 유지
- **Validation:** 연도별 diff가 메자닌 태그 값과 정확히 일치 → 수정 후 5개년 diff $0 PASS

| FY | 2025 | 2024 | 2023 | 2022 | 2021 |
|---|---|---|---|---|---|
| 기존 diff = 메자닌 | $58M | $63M | $242M | $409M | $568M |

## 5. JPM — 금융기업 Edge Case

- **Problem:** Debt·D/E·D/A, Operating Income, CapEx·FCF 계열이 N/A
- **Cause:** 은행 재무구조 차이
  - 이자부 조달이 예금($1,938.9B)·Repo($442.4B)까지 포함되어 일반 기업과 같은 의미의 이자부채를 정의할 수 없음
  - 영업이익 단계 없음, CapEx 별도 미보고
- **Fix:** 예금 부채 보고 여부로 은행형 재무구조를 판별. 억지로 계산하지 않고 N/A와 사유 표시
- **Validation:** Net Income($57.05B)·ROE(16.13%)는 정상. 비은행 기준 티커 8개는 결과 변화 없음
  - **판단:** 버그 수정이 아니라 제품 적용범위 결정. 숫자를 만들 수 있는지보다 다른 기업과 같은 의미인지를 기준으로 판단함

---

**회귀 테스트:** 매 수정마다 GOOGL, ORCL, MSFT, NVDA, AAPL, META, AMZN, TSLA의 전체 지표를 수정 전후로 비교했고, 표시 5개년 값의 변화가 없음을 확인했습니다.
