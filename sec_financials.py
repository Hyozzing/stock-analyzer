"""
SEC EDGAR Company Facts API를 이용해
GOOGL(Alphabet), ORCL(Oracle), MSFT(Microsoft)의 최근 5개 연간 Revenue, Operating Income, Net Income,
EPS, Total Assets, Total Liabilities, Total Stockholders' Equity와
성장률/마진/ROE 지표를 출력하는 스크립트
"""

import os
import tomllib
from datetime import date
from pathlib import Path

import requests
from rich.console import Console
from rich.table import Table

# SEC는 요청 시 User-Agent에 "회사명/이름 이메일" 형식의 연락처를 넣도록 요구합니다.
# (요청 정책: https://www.sec.gov/os/webmaster-faq#developers)
#
# 개인 연락처가 코드와 GitHub에 남지 않도록 값은 코드 밖에서 읽습니다.
#   1) 환경변수 SEC_USER_AGENT
#   2) .streamlit/secrets.toml의 SEC_USER_AGENT (git에 올리지 않는 파일)
# 둘 다 없으면 SecUserAgentMissingError를 발생시켜 설정 방법을 안내합니다.
SEC_USER_AGENT_ENV = "SEC_USER_AGENT"
SECRETS_FILE = Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"


class SecUserAgentMissingError(Exception):
    """SEC 요청에 필요한 User-Agent(연락처)가 설정되지 않았을 때 발생합니다."""


def get_sec_headers() -> dict:
    """SEC 요청 헤더를 만듭니다. 연락처는 환경변수 또는 secrets.toml에서 읽습니다."""
    user_agent = os.environ.get(SEC_USER_AGENT_ENV, "").strip()

    if not user_agent and SECRETS_FILE.exists():
        with SECRETS_FILE.open("rb") as secrets_file:
            user_agent = str(tomllib.load(secrets_file).get(SEC_USER_AGENT_ENV, "")).strip()

    if not user_agent:
        raise SecUserAgentMissingError(
            "SEC 요청에 필요한 연락처가 설정되지 않았습니다. "
            f"환경변수 {SEC_USER_AGENT_ENV} 또는 .streamlit/secrets.toml에 "
            f'{SEC_USER_AGENT_ENV} = "이름 또는 앱 이름 연락용이메일@example.com" 형식으로 설정해 주세요.'
        )

    return {"User-Agent": user_agent}

# 회사별 CIK (SEC가 부여하는 고유 기업 번호). 10자리로 0을 채워야 합니다.
COMPANIES = {
    "GOOGL (Alphabet Inc.)": "0001652044",
    "ORCL (Oracle Corp.)": "0001341439",
    "MSFT (Microsoft Corp.)": "0000789019",
}

# COMPANIES 키의 맨 앞 티커로 "이미 검증이 끝난 회사"를 그대로 찾기 위한 색인.
# {티커: (COMPANIES 키 그대로의 회사명, CIK)} 형태입니다. 회사명을 COMPANIES 키와
# 똑같이 유지해야 DEBT_COMPONENT_TAGS 조회가 기존과 동일하게 동작하므로,
# 이름을 새로 만들지 않고 기존 키를 그대로 재사용합니다.
KNOWN_COMPANIES_BY_TICKER = {
    company_name.split(" ", 1)[0]: (company_name, cik)
    for company_name, cik in COMPANIES.items()
}

# 회사마다 매출을 나타내는 XBRL 태그 이름이 다를 수 있어서 여러 개를 순서대로 시도합니다.
REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]

# 영업이익(Operating Income) 태그. GOOGL, ORCL, MSFT 모두 표준 태그인
# OperatingIncomeLoss 하나로 5개년 10-K 데이터가 전부 확인되어 이것만 사용합니다.
OPERATING_INCOME_TAGS = [
    "OperatingIncomeLoss",
]

# 순이익(Net Income) 태그. GOOGL, ORCL, MSFT 모두 표준 태그인
# NetIncomeLoss 하나로 5개년 10-K 데이터가 전부 확인되어 이것만 사용합니다.
NET_INCOME_TAGS = [
    "NetIncomeLoss",
]

# 영업현금흐름(Operating Cash Flow) 태그. GOOGL, ORCL, MSFT 모두 표준 태그인
# NetCashProvidedByUsedInOperatingActivities 하나로 5개년 10-K 데이터가
# 전부 확인되어 이것만 사용합니다. Revenue/Operating Income/Net Income과
# 마찬가지로 회계연도 전체 기간(start~end)에 대한 duration 데이터입니다.
OPERATING_CASH_FLOW_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
]

# 자본적 지출(CapEx, Capital Expenditures) 태그. GOOGL, ORCL, MSFT 모두 표준
# 태그인 PaymentsToAcquirePropertyPlantAndEquipment 하나로 5개년 10-K
# 데이터가 전부 확인되어 이것만 사용합니다. 건물·서버·데이터센터 등
# 유형자산(Property, Plant and Equipment) 취득에 지출한 현금 흐름
# 항목입니다. Operating Cash Flow와 마찬가지로 회계연도 전체
# 기간(start~end)에 대한 duration 데이터입니다.
#
# PaymentsToAcquireProductiveAssets는 PP&E 취득에 더해 무형자산(intangible
# assets) 취득 지출까지 한 줄로 합쳐 보고하는 회사(예: NVDA, "Purchases
# related to property and equipment and intangible assets")를 위한
# fallback입니다. us-gaap 표준 분류체계에 등록된 일반 태그이며 NVDA 전용
# 하드코딩이 아닙니다. NVDA는 FY2022(10-K FY2024)부터 이 태그만 보고하고
# PaymentsToAcquirePropertyPlantAndEquipment는 FY2012 이후 보고하지 않아,
# 표준 태그만으로는 최근 연도의 CapEx를 전혀 찾을 수 없었습니다(실제 NVDA
# FY2026 10-K Consolidated Statements of Cash Flows의 $6.042B,
# FY2025의 $3.236B와 정확히 일치함을 확인했습니다). extract_annual_capex는
# 여러 태그를 회계연도 종료일별로 병합하는 방식(REVENUE_TAGS와 동일한
# 패턴)이라 같은 회사가 같은 연도에 두 태그를 동시에 보고하는 경우가
# 없는 한 중복 합산되지 않으며, GOOGL/ORCL/MSFT는 이 태그 자체가 XBRL
# 데이터에 없어 영향이 없음을 확인했습니다.
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]

# 희석 주당순이익(Diluted EPS) 태그. GOOGL, ORCL, MSFT 모두 표준 태그인
# EarningsPerShareDiluted 하나로 5개년 10-K 데이터가 전부 확인되어 이것만 사용합니다.
# 단위는 USD가 아니라 USD/shares 입니다.
EPS_TAGS = [
    "EarningsPerShareDiluted",
]

# 총자산(Total Assets) 태그. GOOGL, ORCL, MSFT 모두 표준 태그인
# Assets 하나로 5개년 데이터가 전부 확인되어 이것만 사용합니다.
# Assets는 Revenue 등과 달리 기간(duration)이 아니라 특정 시점(instant)의
# 잔액이라서 entry에 start 없이 end(회계연도 종료일)만 존재합니다.
ASSETS_TAGS = [
    "Assets",
]

# 총부채(Total Liabilities) 태그. GOOGL, MSFT는 표준 태그인 Liabilities로
# 바로 조회되지만, ORCL은 Liabilities 태그를 보고하지 않습니다. 이 경우
# 유동부채(LiabilitiesCurrent)와 비유동부채(LiabilitiesNoncurrent)를 더해서
# 총부채를 계산합니다. Assets와 마찬가지로 instant(시점) 데이터입니다.
LIABILITIES_TAGS = [
    "Liabilities",
]
LIABILITIES_CURRENT_TAG = "LiabilitiesCurrent"
LIABILITIES_NONCURRENT_TAG = "LiabilitiesNoncurrent"

# 총부채 합계 줄 자체가 재무상태표에 없는 회사용 마지막 fallback입니다.
# 예: AMZN은 2012회계연도부터 유동부채, 장기 리스부채, 장기부채, 기타
# 장기부채만 나열하고 "Total liabilities" 줄이 없어 Liabilities /
# LiabilitiesNoncurrent 태그를 보고하지 않습니다. 이 경우 회사가 보고한
# "부채와 자본 총계"에서 총자본(EQUITY_TAGS, 비지배지분 포함)을 빼서
# 총부채를 계산합니다.
#   Liabilities = LiabilitiesAndStockholdersEquity - Equity
LIABILITIES_AND_EQUITY_TAG = "LiabilitiesAndStockholdersEquity"

# 메자닌(임시) 자본 태그. 상환우선주, 상환 가능 비지배지분 등은 부채도 자본도
# 아닌 별도 구간으로 표시되므로, 위 차감 계산을 하면 그 금액이 부채에 섞입니다.
# 해당 회계연도에 이 태그 중 하나라도 0이 아닌 값으로 보고되면 차감 계산을
# 하지 않고 N/A로 둡니다(부채를 과대 계상하지 않기 위함).
TEMPORARY_EQUITY_TAGS = [
    "TemporaryEquityCarryingAmountAttributableToParent",
    "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
    "RedeemableNoncontrollingInterestEquityCarryingAmount",
]

# 총자본(Total Stockholders' Equity) 태그.
# Assets = Liabilities + Equity 등식 검증에는 비지배지분(Noncontrolling
# interests)까지 포함한 연결 기준 총자본이 맞아야 하므로
# StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest를
# 우선 사용합니다. ORCL처럼 비지배지분이 있는 회사는 이 태그로 조회되고,
# GOOGL, MSFT처럼 비지배지분이 없어 이 태그를 보고하지 않는 회사는 지배기업
# 소유주 귀속 총자본인 StockholdersEquity로 대체합니다(비지배지분이 없으므로
# 두 값은 동일합니다). Assets/Liabilities와 마찬가지로 재무상태표의 특정
# 시점(instant) 잔액이라 entry에 start 없이 end(회계연도 종료일)만 존재합니다.
EQUITY_TAGS = [
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "StockholdersEquity",
]

# ROE(자기자본이익률) 계산 전용 Equity 태그.
#
# 위 EQUITY_TAGS(Balance Sheet 등식 A=L+E 검증용)는 비지배지분을 포함한
# 연결 기준 총자본을 우선 사용하지만, ROE는 분자인 Net Income(아래
# NET_INCOME_TAGS의 NetIncomeLoss, 모회사/보통주 주주 귀속 순이익)과
# 분모의 귀속 대상을 맞춰야 하므로 항상 모회사 귀속(비지배지분 제외)
# Equity인 StockholdersEquity만 사용합니다.
#
# 실제 확인 결과:
# - GOOGL(Alphabet), MSFT(Microsoft): 비지배지분이 없어 StockholdersEquity가
#   바로 총자본이며, EQUITY_TAGS로 조회한 값과 동일합니다.
# - ORCL(Oracle): 비지배지분이 있어 StockholdersEquity(모회사 귀속, ROE용)와
#   StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest
#   (비지배지분 포함, Balance Sheet 등식 검증용) 값이 서로 다릅니다.
ROE_EQUITY_TAGS = [
    "StockholdersEquity",
]

# 이자부채(Total Debt, interest-bearing debt) 태그.
#
# 회사마다 Debt를 하나의 단일 태그가 아니라 여러 재무상태표 항목으로 나누어
# 보고하고 있어서, 항목별로 확인한 결과에 따라 회사마다 다른 조합으로
# 합산합니다(중복 합산 없음). 각 항목은 (우선순위 태그 목록, optional 여부)
# 튜플입니다.
#
# - optional=False(필수) 구성요소: 모든 필수 구성요소가 존재하는 회계연도만
#   계산에 포함합니다. 하나라도 누락되면 그 연도는 제외합니다(값을 임의로
#   추정하지 않기 위함). 장기부채처럼 회사가 계속 보유하는 핵심 구성요소가
#   여기 해당합니다.
# - optional=True 구성요소: 상업어음(CommercialPaper)처럼 특정 회계연도에
#   잔액이 없으면 재무제표에 그 항목 자체를 아예 싣지 않는(0으로 명시적
#   태깅하지 않는) 경우가 있는 구성요소입니다. 이런 항목은 해당 연도에
#   태그가 없으면 0으로 더합니다. "재무제표에 실리지 않은 항목은 잔액이
#   없다"는 일반적인 회계 관행에 따른 것이며, 특정 회사만을 위한 예외가
#   아니라 모든 회사에 동일하게 적용되는 규칙입니다.
#
#   이 규칙으로 MSFT FY2026을 계산한 값(LongTermDebtCurrent $9.227B +
#   LongTermDebtNoncurrent $31.067B = $40.294B)이 실제 10-K Note 10 - Debt에
#   명시된 "Total debt $40.294B"(상업어음 항목 없음)와 정확히 일치함을
#   확인해 검증했습니다.
#
# - GOOGL(Alphabet): 상업어음(CommercialPaper, optional. 명시적으로 $0을
#   보고하는 연도가 대부분이지만 optional로 두어도 결과는 동일),
#   장기부채의 유동성 부분(LongTermDebtCurrent, 필수), 장기부채의 비유동
#   부분(필수)을 각각 별도 항목으로 보고합니다. 장기부채 비유동 부분의
#   태그명이 연도에 따라 바뀌어서(2023회계연도부터 LongTermDebtNoncurrent
#   사용, 2021~2022회계연도는 LongTermDebtNoncurrent 없이 LongTermDebt
#   태그 하나로 비유동 장기부채를 보고) LongTermDebtNoncurrent를 우선
#   사용하고 없는 연도만 LongTermDebt로 대체합니다.
#   Total Debt = CommercialPaper(optional) + LongTermDebtCurrent
#                + (LongTermDebtNoncurrent 또는 LongTermDebt)
#
# - ORCL(Oracle): "Notes payable and other borrowings"를 유동
#   (NotesPayableCurrent, 필수)과 비유동(LongTermNotesPayable, 필수)으로
#   나누어 보고합니다(상업어음 프로그램 자체가 없어 CommercialPaper
#   구성요소를 사용하지 않음). 두 항목을 더한 값이 SEC가 별도로 제공하는
#   집계 태그 DebtLongtermAndShorttermCombinedAmount와 5개년 모두 정확히
#   일치함을 확인해 이 합산 방식이 올바름을 교차 검증했습니다.
#   Total Debt = NotesPayableCurrent + LongTermNotesPayable
#
# - MSFT(Microsoft): GOOGL과 동일한 태그 조합(CommercialPaper(optional) +
#   LongTermDebtCurrent(필수) + LongTermDebtNoncurrent(필수, 없는 연도만
#   LongTermDebt로 대체))을 사용합니다. MSFT는 상업어음 잔액이 $0인
#   회계연도(확인된 범위: FY2020~FY2022, FY2026)에 CommercialPaper 태그
#   자체를 보고하지 않는데, optional 처리로 이 연도들도 0을 더해 정상
#   계산됩니다.
#   Total Debt = CommercialPaper(optional) + LongTermDebtCurrent
#                + LongTermDebtNoncurrent
#
# 각 튜플의 태그 목록은 "우선순위 태그 목록"이며, 앞 태그로 값을 찾은
# 연도는 뒤 태그로 덮어쓰지 않습니다(EQUITY_TAGS의 fallback 방식과 동일).
# Assets/Liabilities/Equity와 마찬가지로 재무상태표의 특정 시점(instant)
# 잔액이라 entry에 start 없이 end(회계연도 종료일)만 존재합니다.
DEBT_COMPONENT_TAGS = {
    "GOOGL (Alphabet Inc.)": [
        (["CommercialPaper"], True),
        (["LongTermDebtCurrent"], False),
        (["LongTermDebtNoncurrent", "LongTermDebt"], False),
    ],
    "ORCL (Oracle Corp.)": [
        (["NotesPayableCurrent"], False),
        (["LongTermNotesPayable"], False),
    ],
    "MSFT (Microsoft Corp.)": [
        (["CommercialPaper"], True),
        (["LongTermDebtCurrent"], False),
        (["LongTermDebtNoncurrent", "LongTermDebt"], False),
    ],
}

# 일반(generic) 이자부채 계산에 쓰는 태그 그룹들입니다. 특정 회사 전용이 아니라
# 아래 DEBT_FALLBACK_RECIPES가 조합해서 씁니다.
#
# - 단기 차입(optional): 상업어음이 없으면 단기차입금 태그로 대체합니다.
#   ShortTermBorrowings는 상업어음을 포함해 보고되는 경우가 많아, 두 태그를
#   더하지 않고 우선순위 대체로만 사용합니다(중복 합산 방지).
DEBT_SHORT_TERM_TAGS = ["CommercialPaper", "ShortTermBorrowings"]
# - 장기부채의 유동성 부분
DEBT_CURRENT_PORTION_TAGS = [
    "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
]
# - 장기부채의 비유동 부분
DEBT_NONCURRENT_TAGS = [
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermNotesPayable",
    "LongTermDebt",
]
# - 장기부채 총액(유동성 부분 포함). us-gaap 정의상 LongTermDebt는
#   "current maturities 포함" 총액입니다. 예: AAPL FY2025 LongTermDebt
#   $90.68B = LongTermDebtCurrent $12.35B + LongTermDebtNoncurrent $78.33B.
DEBT_LONG_TERM_TOTAL_TAGS = ["LongTermDebt"]
# - 단기+장기 이자부채 합계를 한 번에 보고하는 집계 태그
DEBT_COMBINED_TOTAL_TAGS = ["DebtLongtermAndShorttermCombinedAmount"]
# - 유동 차입금 총액(상업어음 + 단기차입 + 유동성 장기부채를 이미 포함)
DEBT_CURRENT_TOTAL_TAGS = ["DebtCurrent"]

# 예금 부채 태그. 10-K 재무상태표에 예금 부채(0보다 큰 값)를 보고한 회사는
# 은행 등 예금을 받는 금융회사로 보고 Debt 계산식을 적용하지 않습니다.
#
# 은행은 예금, Repo(환매조건부매도), 단기차입, 장기부채가 모두 이자가 붙는
# 조달 수단이라 "이자부채"의 범위를 일반 기업처럼 정할 수 없고, 부채 자체가
# 영업 원재료여서 Debt-to-Equity / Debt-to-Assets를 일반 기업과 같은 의미로
# 비교할 수 없습니다. 예: JPM FY2025는 ShortTermBorrowings $64.8B +
# LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities $435.2B로
# 일반 기업 방식의 합계($500.0B)는 만들 수 있지만, 이자가 붙는 예금
# (InterestBearingDepositLiabilitiesDomestic $1,452.7B + Foreign $486.2B)과
# Repo(FederalFundsPurchasedAndSecuritiesSoldUnderAgreementsToRepurchase
# $442.4B)가 빠져 있어 같은 의미의 "이자부채"가 아닙니다.
DEPOSIT_LIABILITY_TAGS = [
    "Deposits",
    "InterestBearingDepositLiabilities",
    "NoninterestBearingDepositLiabilities",
]

# 이자부채 fallback 레시피(위에서부터 순서대로 시도).
#
# 회계연도마다 위 레시피부터 시도하고, 앞 레시피로 값을 구한 연도는 뒤 레시피로
# 덮어쓰지 않습니다. 레시피 안의 필수/optional 규칙은 DEBT_COMPONENT_TAGS와
# 같습니다. 한 레시피 안의 그룹끼리는 서로 겹치지 않는 항목만 더하도록 구성해서
# 중복 합산이 없습니다(예: DebtCurrent는 상업어음을 이미 포함하므로 단기 차입
# 그룹과 함께 쓰지 않음).
#
# 1) 구성요소 합산: 기존 DEFAULT 조합과 동일합니다(AAPL, NVDA는 이 레시피로
#    기존 값 그대로 계산됨).
# 2) 단기+장기 합계 태그: 회사가 직접 보고한 총액이라 가장 신뢰도가 높습니다.
# 3) 단기 차입 + 장기부채 총액: 유동성 장기부채가 $0이라 LongTermDebtCurrent
#    태그 자체를 싣지 않는 회사용입니다(예: META는 LongTermDebt =
#    LongTermDebtNoncurrent로 보고). "min_tags"로 장기부채 총액이 비유동
#    부분보다 작지 않은지 검증합니다. 작다면 LongTermDebt 태그를 비유동
#    부분만의 의미로 쓴 것이므로(유동분을 알 수 없음) 그 연도는 쓰지 않습니다.
# 4) 유동 차입금 총액 + 비유동 장기부채
DEBT_FALLBACK_RECIPES = [
    {
        "label": "단기차입 + 유동성 장기부채 + 비유동 장기부채",
        "groups": [
            (DEBT_SHORT_TERM_TAGS, True),
            (DEBT_CURRENT_PORTION_TAGS, False),
            (DEBT_NONCURRENT_TAGS, False),
        ],
    },
    {
        "label": "단기·장기 이자부채 합계 태그",
        "groups": [(DEBT_COMBINED_TOTAL_TAGS, False)],
    },
    {
        "label": "단기차입 + 장기부채 총액(유동분 포함)",
        "groups": [
            (DEBT_SHORT_TERM_TAGS, True),
            (DEBT_LONG_TERM_TOTAL_TAGS, False),
        ],
        "min_tags": DEBT_NONCURRENT_TAGS[:1],
    },
    {
        "label": "유동 차입금 총액 + 비유동 장기부채",
        "groups": [
            (DEBT_CURRENT_TOTAL_TAGS, False),
            (DEBT_NONCURRENT_TAGS, False),
        ],
    },
]


class TickerNotFoundError(Exception):
    """SEC가 공개하는 상장사 티커 목록에서 입력한 티커를 찾지 못했을 때 발생합니다."""


# SEC 티커 목록은 한 번 받아오면 세션 동안 바뀌지 않으므로 캐시해서 재사용합니다.
_SEC_TICKER_INDEX_CACHE = {}


def load_sec_ticker_index() -> dict:
    """
    SEC가 공개하는 전체 상장사 목록을 {티커: (10자리 CIK, 회사명)} 딕셔너리로
    반환합니다. 같은 프로세스 안에서는 처음 한 번만 내려받습니다.
    """
    if _SEC_TICKER_INDEX_CACHE:
        return _SEC_TICKER_INDEX_CACHE

    response = requests.get("https://www.sec.gov/files/company_tickers.json", headers=get_sec_headers())
    response.raise_for_status()

    for row in response.json().values():
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        _SEC_TICKER_INDEX_CACHE[ticker] = (
            f"{int(row['cik_str']):010d}",
            row.get("title", ticker),
        )

    return _SEC_TICKER_INDEX_CACHE


def resolve_company(ticker: str) -> tuple:
    """
    티커를 (회사명, CIK) 쌍으로 바꿉니다.

    COMPANIES에 이미 등록된 GOOGL / ORCL / MSFT는 SEC 티커 목록을 조회하지 않고
    기존 키와 CIK를 그대로 돌려줍니다. 회사명이 기존 키와 동일해야
    DEBT_COMPONENT_TAGS 조회 결과가 같고, 따라서 이미 검증된 계산 결과도
    바뀌지 않습니다. 그 밖의 티커만 SEC 티커 목록에서 CIK를 찾습니다.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise TickerNotFoundError("티커가 비어 있습니다.")

    if ticker in KNOWN_COMPANIES_BY_TICKER:
        return KNOWN_COMPANIES_BY_TICKER[ticker]

    ticker_index = load_sec_ticker_index()
    if ticker not in ticker_index:
        raise TickerNotFoundError(
            f"'{ticker}'를 SEC 티커 목록에서 찾을 수 없습니다. "
            "SEC에 보고하는 미국 상장사의 티커인지 확인해 주세요."
        )

    cik, title = ticker_index[ticker]
    return f"{ticker} ({title})", cik


def get_company_facts(cik: str) -> dict:
    """data.sec.gov에서 회사의 전체 XBRL 재무 데이터를 가져옵니다."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    response = requests.get(url, headers=get_sec_headers())
    response.raise_for_status()
    return response.json()


def to_date(date_str: str) -> date:
    year, month, day = date_str.split("-")
    return date(int(year), int(month), int(day))


def extract_annual_revenue(facts: dict) -> list:
    """
    10-K(연간 보고서)에 실린 연간 Revenue만 골라서
    (회계연도 종료일, 매출액) 리스트로 반환합니다. (최신순 정렬)
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    # 회사가 연도마다 다른 태그를 쓰기도 해서(예: Alphabet은 2022년만
    # RevenueFromContractWithCustomerExcludingAssessedTax를 사용) 모든 태그를 합칩니다.
    for tag in REVENUE_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            start = entry.get("start")
            end = entry.get("end")
            if not start or not end:
                continue

            # 시작일~종료일 차이가 350일 이상인 것만 "연간" 데이터로 인정
            days = (to_date(end) - to_date(start)).days
            if days < 350:
                continue

            # 같은 회계연도가 여러 번 나오면(정정 보고서 등) 마지막 값으로 덮어씁니다.
            annual_data[end] = entry["val"]

    return sorted(annual_data.items(), reverse=True)


def extract_annual_operating_income(facts: dict) -> list:
    """
    10-K(연간 보고서)에 실린 연간 Operating Income만 골라서
    (회계연도 종료일, 영업이익) 리스트로 반환합니다. (최신순 정렬)
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in OPERATING_INCOME_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            start = entry.get("start")
            end = entry.get("end")
            if not start or not end:
                continue

            # 시작일~종료일 차이가 350일 이상인 것만 "연간" 데이터로 인정
            days = (to_date(end) - to_date(start)).days
            if days < 350:
                continue

            # 같은 회계연도가 여러 번 나오면(정정 보고서 등) 마지막 값으로 덮어씁니다.
            annual_data[end] = entry["val"]

    return sorted(annual_data.items(), reverse=True)


def extract_annual_net_income(facts: dict) -> list:
    """
    10-K(연간 보고서)에 실린 연간 Net Income만 골라서
    (회계연도 종료일, 순이익) 리스트로 반환합니다. (최신순 정렬)
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in NET_INCOME_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            start = entry.get("start")
            end = entry.get("end")
            if not start or not end:
                continue

            # 시작일~종료일 차이가 350일 이상인 것만 "연간" 데이터로 인정
            days = (to_date(end) - to_date(start)).days
            if days < 350:
                continue

            # 같은 회계연도가 여러 번 나오면(정정 보고서 등) 마지막 값으로 덮어씁니다.
            annual_data[end] = entry["val"]

    return sorted(annual_data.items(), reverse=True)


def extract_annual_operating_cash_flow(facts: dict) -> list:
    """
    10-K(연간 보고서)에 실린 연간 Operating Cash Flow만 골라서
    (회계연도 종료일, 영업현금흐름) 리스트로 반환합니다. (최신순 정렬)

    Revenue/Operating Income/Net Income과 마찬가지로 회계연도 전체
    기간(start~end)에 대한 duration 데이터라서, 시작일~종료일 차이가
    350일 이상인 것만 "연간" 데이터로 인정해 분기 누적값(예: 6개월,
    9개월 중간 보고서의 YTD 수치)이 섞이지 않도록 걸러냅니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in OPERATING_CASH_FLOW_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            start = entry.get("start")
            end = entry.get("end")
            if not start or not end:
                continue

            # 시작일~종료일 차이가 350일 이상인 것만 "연간" 데이터로 인정
            days = (to_date(end) - to_date(start)).days
            if days < 350:
                continue

            # 같은 회계연도가 여러 번 나오면(정정 보고서 등) 마지막 값으로 덮어씁니다.
            annual_data[end] = entry["val"]

    return sorted(annual_data.items(), reverse=True)


def extract_annual_capex(facts: dict) -> list:
    """
    10-K(연간 보고서)에 실린 연간 CapEx(유형자산 취득 지출)만 골라서
    (회계연도 종료일, CapEx) 리스트로 반환합니다. (최신순 정렬)

    Operating Cash Flow와 마찬가지로 회계연도 전체 기간(start~end)에
    대한 duration 데이터라서, 시작일~종료일 차이가 350일 이상인 것만
    "연간" 데이터로 인정해 분기 누적값이 섞이지 않도록 걸러냅니다.

    SEC 원본 값이 현금흐름표 관례상 음수(현금 유출)로 표현되어 있을 수도
    있으므로, abs()로 부호를 양수 지출액으로 통일합니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in CAPEX_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            start = entry.get("start")
            end = entry.get("end")
            if not start or not end:
                continue

            # 시작일~종료일 차이가 350일 이상인 것만 "연간" 데이터로 인정
            days = (to_date(end) - to_date(start)).days
            if days < 350:
                continue

            # 같은 회계연도가 여러 번 나오면(정정 보고서 등) 마지막 값으로 덮어씁니다.
            annual_data[end] = abs(entry["val"])

    return sorted(annual_data.items(), reverse=True)


def extract_annual_eps(facts: dict) -> list:
    """
    10-K(연간 보고서)에 실린 연간 희석 EPS만 골라서
    (회계연도 종료일, EPS) 리스트로 반환합니다. (최신순 정렬)
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in EPS_TAGS:
        if tag not in us_gaap:
            continue

        # EPS는 USD가 아니라 USD/shares 단위로 보고됩니다.
        entries = us_gaap[tag]["units"].get("USD/shares", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            start = entry.get("start")
            end = entry.get("end")
            if not start or not end:
                continue

            # 시작일~종료일 차이가 350일 이상인 것만 "연간" 데이터로 인정
            days = (to_date(end) - to_date(start)).days
            if days < 350:
                continue

            # 같은 회계연도가 여러 번 나오면(정정 보고서 등) 마지막 값으로 덮어씁니다.
            annual_data[end] = entry["val"]

    return sorted(annual_data.items(), reverse=True)


def extract_annual_assets(facts: dict) -> list:
    """
    10-K에 실린 회계연도 종료 시점(instant)의 Total Assets를 골라서
    (회계연도 종료일, 총자산) 리스트로 반환합니다. (최신순 정렬)

    Revenue/Operating Income/Net Income/EPS는 "기간(start~end)" 동안 누적된
    값이라 350일 이상인지로 연간 데이터를 걸렀지만, Assets는 재무상태표
    항목이라 특정 시점의 잔액(end만 존재하고 start는 없음)입니다. 그래서
    기간 길이 검사 없이 form이 10-K인 entry만 골라서 사용합니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in ASSETS_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            end = entry.get("end")
            if not end:
                continue

            # 같은 회계연도가 여러 번 나오면(정정 보고서, 비교 재무제표 등)
            # 마지막 값으로 덮어씁니다.
            annual_data[end] = entry["val"]

    return sorted(annual_data.items(), reverse=True)


def extract_annual_liabilities(facts: dict) -> list:
    """
    (회계연도 종료일, 총부채) 리스트(최신순)만 필요한 경우를 위한 함수입니다.
    계산 방식은 extract_annual_liabilities_with_details()를 참고하세요.
    """
    annual_liabilities, _details = extract_annual_liabilities_with_details(facts)
    return annual_liabilities


def extract_annual_liabilities_with_details(facts: dict) -> tuple:
    """
    10-K에 실린 회계연도 종료 시점(instant)의 Total Liabilities를 골라서
    (총부채 리스트, 연도별 출처 dict)를 반환합니다. 리스트는
    (회계연도 종료일, 총부채) 형태로 최신순 정렬입니다.

    Assets와 마찬가지로 재무상태표의 시점 데이터(end만 존재)입니다.
    회계연도마다 아래 순서로 시도하고, 앞 방식으로 값을 구한 연도는 뒤
    방식으로 덮어쓰지 않습니다.
      1) Liabilities 태그
      2) LiabilitiesCurrent + LiabilitiesNoncurrent (예: ORCL)
      3) LiabilitiesAndStockholdersEquity - Equity (예: AMZN)
         메자닌 자본(TEMPORARY_EQUITY_TAGS)이 보고된 연도는 제외합니다.
    어느 방식으로도 구할 수 없는 연도는 결과에 넣지 않습니다(N/A, 0으로
    채우지 않음).

    3)으로 구한 연도는 Assets = Liabilities + Equity 검증이 사실상
    Assets = LiabilitiesAndStockholdersEquity 검증이 됩니다(부채를 차감으로
    구했으므로).

    출처 dict는 {회계연도 종료일: {"method": 방식 이름,
                                   "components": [(태그, 값), ...]}} 입니다.
    차감 항목의 값은 음수로 담습니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in LIABILITIES_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            end = entry.get("end")
            if not end:
                continue

            annual_data[end] = entry["val"]

    details = {
        end: {"method": "Liabilities 태그", "components": [("Liabilities", value)]}
        for end, value in annual_data.items()
    }

    # 2) Liabilities 태그가 없는 연도는 유동부채 + 비유동부채로 계산합니다.
    current_by_end = {}
    if LIABILITIES_CURRENT_TAG in us_gaap:
        for entry in us_gaap[LIABILITIES_CURRENT_TAG]["units"].get("USD", []):
            if entry.get("form") == "10-K" and entry.get("end"):
                current_by_end[entry["end"]] = entry["val"]

    noncurrent_by_end = {}
    if LIABILITIES_NONCURRENT_TAG in us_gaap:
        for entry in us_gaap[LIABILITIES_NONCURRENT_TAG]["units"].get("USD", []):
            if entry.get("form") == "10-K" and entry.get("end"):
                noncurrent_by_end[entry["end"]] = entry["val"]

    for end in current_by_end:
        if end in annual_data or end not in noncurrent_by_end:
            continue
        annual_data[end] = current_by_end[end] + noncurrent_by_end[end]
        details[end] = {
            "method": "유동부채 + 비유동부채",
            "components": [
                (LIABILITIES_CURRENT_TAG, current_by_end[end]),
                (LIABILITIES_NONCURRENT_TAG, noncurrent_by_end[end]),
            ],
        }

    # 3) 그래도 없는 연도는 부채와 자본 총계 - 총자본으로 계산합니다.
    total_by_end = _extract_instant_values_with_fallback(us_gaap, [LIABILITIES_AND_EQUITY_TAG])
    equity_by_end = _extract_instant_values_with_source(us_gaap, EQUITY_TAGS)
    temporary_equity_by_tag = [
        _extract_instant_values_with_fallback(us_gaap, [tag]) for tag in TEMPORARY_EQUITY_TAGS
    ]

    for end, total in total_by_end.items():
        if end in annual_data or end not in equity_by_end:
            continue
        if any(values.get(end) for values in temporary_equity_by_tag):
            continue
        equity_value, equity_tag = equity_by_end[end]
        annual_data[end] = total - equity_value
        details[end] = {
            "method": "부채와 자본 총계 - 총자본",
            "components": [
                (LIABILITIES_AND_EQUITY_TAG, total),
                (equity_tag, -equity_value),
            ],
        }

    return sorted(annual_data.items(), reverse=True), details


def extract_annual_equity(facts: dict) -> list:
    """
    10-K에 실린 회계연도 종료 시점(instant)의 Total Stockholders' Equity를
    골라서 (회계연도 종료일, 총자본) 리스트로 반환합니다. (최신순 정렬)

    Assets = Liabilities + Equity 등식과 맞도록 비지배지분 포함 총자본
    (StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest)을
    우선 사용하고, 이 태그가 없는 연도만 StockholdersEquity로 대체합니다.

    Assets/Liabilities와 마찬가지로 재무상태표의 시점 데이터(end만 존재)이며,
    기간 길이 검사 없이 form이 10-K인 entry만 골라서 사용합니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_data = {}

    for tag in EQUITY_TAGS:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            end = entry.get("end")
            if not end:
                continue

            # 이미 다른 태그로 값을 찾은 회계연도는 덮어쓰지 않습니다
            # (StockholdersEquity를 우선 사용하고, 없는 연도만 대체 태그로 채움).
            if end in annual_data:
                continue

            annual_data[end] = entry["val"]

    return sorted(annual_data.items(), reverse=True)


def extract_annual_parent_equity(facts: dict) -> list:
    """
    ROE 계산 전용으로 모회사 귀속(비지배지분 제외) Stockholders' Equity를
    회계연도 종료 시점(instant) 기준으로 골라서 (회계연도 종료일, 값) 리스트로
    반환합니다. (최신순 정렬)

    extract_annual_equity()가 반환하는 Balance Sheet 등식 검증용 Equity
    (비지배지분 포함 우선)와는 다른 값입니다. Net Income(모회사 귀속)과
    분모의 귀속 대상을 맞추기 위해 ROE_EQUITY_TAGS(StockholdersEquity)만
    사용합니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    values_by_end = _extract_instant_values_with_fallback(us_gaap, ROE_EQUITY_TAGS)
    return sorted(values_by_end.items(), reverse=True)


def _extract_instant_values_with_fallback(us_gaap: dict, tag_priority: list) -> dict:
    """
    시점(instant) 데이터를 담은 태그들을 우선순위대로 시도해서
    {회계연도 종료일: 값} 딕셔너리를 만듭니다. 10-K form만 사용하며,
    앞쪽 태그로 이미 값을 찾은 회계연도는 뒤쪽(대체) 태그로 덮어쓰지
    않습니다.
    """
    return {
        end: value
        for end, (value, _tag) in _extract_instant_values_with_source(us_gaap, tag_priority).items()
    }


def _extract_instant_values_with_source(us_gaap: dict, tag_priority: list) -> dict:
    """
    _extract_instant_values_with_fallback와 같은 규칙으로 값을 고르되,
    {회계연도 종료일: (값, 실제로 사용한 태그)} 형태로 반환합니다.
    """
    values_by_end = {}

    for tag in tag_priority:
        if tag not in us_gaap:
            continue

        entries = us_gaap[tag]["units"].get("USD", [])

        for entry in entries:
            if entry.get("form") != "10-K":
                continue

            end = entry.get("end")
            if not end:
                continue

            if end in values_by_end:
                continue

            values_by_end[end] = (entry["val"], tag)

    return values_by_end


def _apply_debt_recipe(us_gaap: dict, recipe: dict) -> dict:
    """
    레시피 하나로 계산할 수 있는 회계연도의 이자부채를
    {회계연도 종료일: (합계, [(사용한 태그, 값), ...])} 형태로 반환합니다.

      - 필수(optional=False) 그룹은 모두 존재하는 회계연도만 포함합니다.
        하나라도 누락되면 그 연도는 제외합니다(값을 임의로 추정하지 않기 위함).
      - optional 그룹은 해당 연도에 태그가 없으면 0으로 더합니다.
      - recipe에 "min_tags"가 있으면, 그 태그 값이 있는 연도는 계산 결과가
        그 값 이상일 때만 포함합니다(총액이 구성 부분보다 작으면 태그 의미가
        다르게 쓰인 것으로 보고 제외).
      - 합계가 0인 연도는 레시피의 모든 그룹(optional 포함)이 SEC에
        명시적으로 보고된 경우에만 포함합니다. optional 그룹이 보고되지 않아
        0으로 더해진 상태에서 합계가 0이면 "이자부채 $0"을 확정할 수 없으므로
        제외하고, 두 번째 반환값(0 확정 불가 연도)에 담습니다.

    반환값: ({연도: (합계, [(태그, 값), ...], 0으로 간주한 optional 태그 목록)},
             {0 확정 불가 연도: 보고되지 않은 태그 목록})
    """
    groups = [
        (_extract_instant_values_with_source(us_gaap, tag_priority), optional)
        for tag_priority, optional in recipe["groups"]
    ]
    mandatory_groups = [values for values, optional in groups if not optional]
    if not mandatory_groups:
        return {}, {}

    common_end_dates = set(mandatory_groups[0])
    for group_values in mandatory_groups[1:]:
        common_end_dates &= set(group_values)

    minimum_values = (
        _extract_instant_values_with_fallback(us_gaap, recipe["min_tags"])
        if recipe.get("min_tags")
        else {}
    )

    result = {}
    unconfirmed_zero = {}
    for end in common_end_dates:
        components = [
            (group_values[end][1], group_values[end][0])
            for group_values, _optional in groups
            if end in group_values
        ]
        total = sum(value for _tag, value in components)

        if end in minimum_values and total < minimum_values[end]:
            continue

        # 필수 그룹은 모두 존재하는 연도만 남았으므로, 여기서 빠진 그룹은
        # 보고되지 않아 0으로 더해진 optional 그룹입니다.
        missing_tags = [
            " / ".join(tag_priority)
            for (tag_priority, _optional), (group_values, _) in zip(recipe["groups"], groups)
            if end not in group_values
        ]

        if total == 0 and missing_tags:
            unconfirmed_zero[end] = missing_tags
            continue

        result[end] = (total, components, missing_tags)

    return result, unconfirmed_zero


def extract_annual_debt_with_details(facts: dict, company_name: str) -> tuple:
    """
    10-K에 실린 회계연도 종료 시점(instant)의 이자부채(Total Debt)를 계산해서
    (annual_debt 리스트, details dict)를 반환합니다.

    annual_debt는 (회계연도 종료일, 총이자부채) 리스트(최신순)입니다.

    계산 순서:
      1) DEBT_COMPONENT_TAGS에 등록된 회사는 그 검증된 조합을 먼저 적용합니다
         (예: MSFT의 CommercialPaper optional 처리 - FY2026 10-K Note 10 -
         Debt의 실제 Total Debt $40.294B와 대조해 확인).
      2) 이어서 DEBT_FALLBACK_RECIPES를 순서대로 적용해, 아직 값이 없는
         회계연도만 채웁니다. 앞에서 구한 연도는 덮어쓰지 않으므로 등록 회사의
         기존 결과는 바뀌지 않습니다.

    details는 화면/디버깅용 정보입니다.
      - "by_year": {회계연도 종료일: {"method": 레시피 이름,
                                      "components": [(태그, 값), ...],
                                      "assumed_zero": [태그, ...]}}
          assumed_zero는 SEC에 보고되지 않아 0으로 간주하고 더한 optional
          구성요소(예: 상업어음)입니다. 비어 있으면 모든 구성요소가 실제
          보고값입니다.
      - "status":
          "ok"           일부 또는 전체 연도의 Debt를 계산함
          "not_reported" SEC 10-K 데이터에 이자부채 관련 후보 태그가 하나도 없음
                         (회사가 이자부채를 보고하지 않음 - 무차입 가능성)
          "unmatched"    후보 태그는 있지만 어떤 레시피로도 조합하지 못함
                         (프로그램이 태그 조합을 찾지 못한 경우)
          "deposit_institution"
                         예금 부채(DEPOSIT_LIABILITY_TAGS)를 보고하는 은행형
                         재무구조라 Debt 계산식을 적용하지 않음. 이때
                         "deposits"에 가장 최근 예금 보고값
                         {"end", "tag", "value"}를 담습니다.
      - "found_tags": 10-K 데이터에 실제로 존재하는 후보 태그 목록
      - "unconfirmed_zero": {회계연도 종료일: 보고되지 않은 태그 목록}
            보고된 항목은 모두 0이지만 일부 구성요소가 보고되지 않아 총
            이자부채 $0을 확정할 수 없어 N/A로 둔 연도입니다.

    Debt $0은 레시피의 모든 구성요소가 SEC에 명시적으로 보고되고 그 합이 0일
    때만 표시합니다. 데이터 부재, 태그 미탐지, 판단 불가능한 경우는 0으로
    추정하지 않고 N/A로 둡니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    deposits_by_end = _extract_instant_values_with_source(us_gaap, DEPOSIT_LIABILITY_TAGS)
    reported_deposits = {
        end: (value, tag) for end, (value, tag) in deposits_by_end.items() if value > 0
    }
    if reported_deposits:
        latest_end = max(reported_deposits)
        return [], {
            "by_year": {},
            "unconfirmed_zero": {},
            "status": "deposit_institution",
            "found_tags": [],
            "deposits": {
                "end": latest_end,
                "tag": reported_deposits[latest_end][1],
                "value": reported_deposits[latest_end][0],
            },
        }

    recipes = []
    if company_name in DEBT_COMPONENT_TAGS:
        recipes.append({
            "label": "회사별 검증 조합",
            "groups": DEBT_COMPONENT_TAGS[company_name],
        })
    recipes.extend(DEBT_FALLBACK_RECIPES)

    by_year = {}
    unconfirmed_zero = {}
    for recipe in recipes:
        recipe_values, recipe_unconfirmed_zero = _apply_debt_recipe(us_gaap, recipe)
        for end, (total, components, assumed_zero) in recipe_values.items():
            if end in by_year:
                continue
            by_year[end] = {
                "method": recipe["label"],
                "total": total,
                "components": components,
                "assumed_zero": assumed_zero,
            }
        for end, missing_tags in recipe_unconfirmed_zero.items():
            unconfirmed_zero.setdefault(end, missing_tags)

    # 다른 레시피로 값을 구한 연도는 "0 확정 불가" 목록에서 뺍니다.
    unconfirmed_zero = {
        end: missing_tags
        for end, missing_tags in unconfirmed_zero.items()
        if end not in by_year
    }

    candidate_tags = list(dict.fromkeys(
        tag
        for recipe in recipes
        for tag_priority, _ in recipe["groups"]
        for tag in tag_priority
    ))
    found_tags = [
        tag
        for tag in candidate_tags
        if any(
            entry.get("form") == "10-K"
            for entry in us_gaap.get(tag, {}).get("units", {}).get("USD", [])
        )
    ]

    if by_year:
        status = "ok"
    elif found_tags:
        status = "unmatched"
    else:
        status = "not_reported"

    annual_debt = sorted(
        ((end, info["total"]) for end, info in by_year.items()),
        reverse=True,
    )
    details = {
        "by_year": by_year,
        "unconfirmed_zero": unconfirmed_zero,
        "status": status,
        "found_tags": found_tags,
    }
    return annual_debt, details


def extract_annual_debt(facts: dict, company_name: str) -> list:
    """
    (회계연도 종료일, 총이자부채) 리스트(최신순)만 필요한 경우를 위한 함수입니다.
    계산 방식은 extract_annual_debt_with_details()를 참고하세요.
    """
    annual_debt, _details = extract_annual_debt_with_details(facts, company_name)
    return annual_debt


def extract_annual_temporary_equity(facts: dict) -> dict:
    """
    10-K 재무상태표에 별도 구간으로 보고된 메자닌(임시) 자본을
    {회계연도 종료일: (합계, [(태그, 값), ...])} 형태로 반환합니다.

    메자닌 자본은 상환 가능 비지배지분, 상환우선주처럼 부채도 자본도 아닌
    항목으로, 재무상태표에서 부채 합계와 자본 합계 사이에 따로 표시됩니다
    (예: TSLA의 "Redeemable noncontrolling interests in subsidiaries").

    중복 합산을 막기 위해 메자닌 자본 총계 태그
    (TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests)
    가 있는 연도는 그 값만 쓰고, 없는 연도만 지배기업 귀속분
    (TemporaryEquityCarryingAmountAttributableToParent)과 상환 가능 비지배지분
    (RedeemableNoncontrollingInterestEquityCarryingAmount) 중 보고된 것을
    더합니다. 어느 태그도 보고되지 않은 연도는 결과에 넣지 않습니다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    total_tag = "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests"
    part_tags = [
        "TemporaryEquityCarryingAmountAttributableToParent",
        "RedeemableNoncontrollingInterestEquityCarryingAmount",
    ]

    result = {
        end: (value, [(total_tag, value)])
        for end, value in _extract_instant_values_with_fallback(us_gaap, [total_tag]).items()
    }

    parts_by_tag = [
        (tag, _extract_instant_values_with_fallback(us_gaap, [tag])) for tag in part_tags
    ]
    part_end_dates = set().union(*(values for _tag, values in parts_by_tag))
    for end in part_end_dates:
        if end in result:
            continue
        components = [(tag, values[end]) for tag, values in parts_by_tag if end in values]
        result[end] = (sum(value for _tag, value in components), components)

    return result


# 재무등식 반올림 허용 규칙.
#
# 회사는 재무상태표를 천 달러/백만 달러 단위로 반올림해 보고하고, 합계 줄도
# 각각 따로 반올림되므로 원본에 오류가 없어도 A와 L+E가 1~2단위 어긋날 수
# 있습니다(예: IREN FY2024 10-K 원문은 부채 55,346 + 자본 1,097,471 =
# 1,152,817인데 "부채와 자본 총계"는 1,152,819로 표기, 단위 $천 → $2,000 차이).
#
# 아래 두 조건을 모두 만족할 때만 "PASS (rounding)"으로 판정합니다.
#   1) |diff| <= 보고 단위 x BALANCE_ROUNDING_MAX_UNITS
#      보고 단위는 비교한 값들이 모두 나누어떨어지는 가장 큰 단위
#      ($1M -> $1K -> $1)로 추정합니다.
#   2) |diff| <= Assets x BALANCE_ROUNDING_MAX_ASSET_RATIO
#      백만 달러 단위 보고 기업이 1)만으로 최대 $2M까지 허용되지 않도록
#      회사 규모 대비 상한을 둡니다.
# 하나라도 넘으면 기존처럼 FAIL입니다. 메자닌 자본 누락(TSLA, $47M~$568M)
# 같은 의미 있는 차이는 1)에서 걸러집니다.
BALANCE_ROUNDING_MAX_UNITS = 2
BALANCE_ROUNDING_MAX_ASSET_RATIO = 0.0001  # 총자산의 0.01%
REPORTING_UNIT_CANDIDATES = [1_000_000, 1_000, 1]


def infer_reporting_unit(values: list) -> int:
    """값들이 모두 나누어떨어지는 가장 큰 보고 단위($1M, $1K, $1)를 추정합니다."""
    nonzero_values = [value for value in values if value]
    for unit in REPORTING_UNIT_CANDIDATES:
        if all(value % unit == 0 for value in nonzero_values):
            return unit
    return 1


def _judge_balance_sheet_diff(diff: int, assets_value: int, compared_values: list) -> dict:
    """
    diff가 0이면 PASS, 반올림 허용 규칙 안이면 PASS (rounding), 아니면 FAIL로
    판정합니다. 반올림 PASS인 경우에만 "rounding"과 "reporting_unit"을 담습니다.
    """
    if diff == 0:
        return {"is_valid": True, "diff": diff}

    reporting_unit = infer_reporting_unit(compared_values)
    within_units = abs(diff) <= reporting_unit * BALANCE_ROUNDING_MAX_UNITS
    within_ratio = abs(diff) <= abs(assets_value) * BALANCE_ROUNDING_MAX_ASSET_RATIO

    if within_units and within_ratio:
        return {"is_valid": True, "diff": diff, "rounding": True, "reporting_unit": reporting_unit}

    return {"is_valid": False, "diff": diff}


def validate_balance_sheet_equation(
    annual_assets: dict,
    annual_liabilities: dict,
    annual_equity: dict,
    annual_temporary_equity: dict = None,
) -> dict:
    """
    회계연도별로 재무상태표 등식 Assets = Liabilities + Equity가 성립하는지
    검증합니다.

    Assets/Liabilities/Equity가 모두 존재하는 회계연도 종료일에 대해서만,
    반올림 전 SEC 원본 정수 값(USD)으로 비교합니다. 반환값은
    {회계연도 종료일: {"is_valid": bool, "diff": int}} 형태이며,
    diff = Assets - (Liabilities + Equity) 입니다.

    diff가 0이 아니어도 보고 단위 반올림으로 설명되는 아주 작은 차이는
    "PASS (rounding)"으로 판정하고 "rounding": True, "reporting_unit"을 함께
    담습니다(규칙은 BALANCE_ROUNDING_MAX_UNITS 위 설명 참고).

    annual_temporary_equity(extract_annual_temporary_equity의 반환값)에
    메자닌 자본이 보고된 연도는 재무상태표 구조 그대로
    Assets = Liabilities + 메자닌 자본 + Equity로 검증하고, 결과에
    "temporary_equity"(합계)와 "temporary_equity_tags"를 함께 담습니다.
    메자닌 자본이 보고되지 않은 연도는 기존 등식을 그대로 씁니다.
    """
    annual_temporary_equity = annual_temporary_equity or {}
    result = {}

    for end_date, assets_value in annual_assets.items():
        liabilities_value = annual_liabilities.get(end_date)
        equity_value = annual_equity.get(end_date)

        if liabilities_value is None or equity_value is None:
            continue

        if end_date in annual_temporary_equity:
            temporary_value, temporary_components = annual_temporary_equity[end_date]
            diff = assets_value - (liabilities_value + temporary_value + equity_value)
            check = _judge_balance_sheet_diff(
                diff,
                assets_value,
                [assets_value, liabilities_value, temporary_value, equity_value],
            )
            check["temporary_equity"] = temporary_value
            check["temporary_equity_tags"] = [tag for tag, _value in temporary_components]
            result[end_date] = check
            continue

        diff = assets_value - (liabilities_value + equity_value)
        result[end_date] = _judge_balance_sheet_diff(
            diff, assets_value, [assets_value, liabilities_value, equity_value]
        )

    return result


def calculate_yoy_growth(annual_series: list) -> dict:
    """
    전년 대비 성장률(YoY Growth, %)을 계산합니다.

    계산식: (올해 값 - 작년 값) / 작년 값 * 100

    annual_series는 (회계연도 종료일, 값) 형태의 리스트이며, 최신 연도부터
    과거 순서로(내림차순) 정렬되어 있어야 합니다. 그래서 리스트에서 바로
    다음 항목이 "작년" 데이터가 됩니다. 가장 오래된 연도는 비교할 작년
    데이터가 없으므로 growth 값을 None으로 둡니다(출력 시 N/A로 표시).
    """
    growth_by_end_date = {}

    for i in range(len(annual_series)):
        end_date, current_value = annual_series[i]

        is_oldest_year = (i == len(annual_series) - 1)
        if is_oldest_year:
            growth_by_end_date[end_date] = None
            continue

        _, previous_value = annual_series[i + 1]
        growth = (current_value - previous_value) / previous_value * 100
        growth_by_end_date[end_date] = growth

    return growth_by_end_date


def calculate_margin(annual_numerator: dict, annual_denominator: dict) -> dict:
    """
    이익률(Margin, %)을 계산합니다.

    계산식: 이익 / Revenue * 100

    annual_numerator: {회계연도 종료일: 이익(Operating Income 또는 Net Income)}
    annual_denominator: {회계연도 종료일: Revenue}
    """
    margin_by_end_date = {}

    for end_date, revenue_value in annual_denominator.items():
        numerator_value = annual_numerator.get(end_date)
        if numerator_value is None:
            margin_by_end_date[end_date] = None
            continue

        margin = numerator_value / revenue_value * 100
        margin_by_end_date[end_date] = margin

    return margin_by_end_date


def calculate_free_cash_flow(annual_operating_cash_flow: dict, annual_capex: dict) -> dict:
    """
    잉여현금흐름(Free Cash Flow, FCF)을 계산합니다.

    계산식: FCF = Operating Cash Flow - CapEx

    SEC에서 별도로 조회하지 않고, 이미 가져온 Operating Cash Flow와 CapEx의
    SEC 원본(반올림 전) 값을 그대로 사용합니다. 두 값이 같은 회계연도
    종료일에 대해 모두 존재하는 연도만 계산하고, 하나라도 없으면 그
    연도는 None으로 둡니다(출력 시 N/A로 표시).
    """
    fcf_by_end_date = {}

    for end_date, ocf_value in annual_operating_cash_flow.items():
        capex_value = annual_capex.get(end_date)
        if capex_value is None:
            fcf_by_end_date[end_date] = None
            continue

        fcf_by_end_date[end_date] = ocf_value - capex_value

    return fcf_by_end_date


def calculate_fcf_growth(annual_free_cash_flow: dict) -> dict:
    """
    FCF의 전년 대비 성장률(YoY Growth, %)을 계산합니다.

    계산식: (당해연도 FCF / 전년도 FCF - 1) * 100

    billion 단위로 변환하거나 반올림하기 전의 SEC 원본 FCF 값(annual_free_cash_flow,
    calculate_free_cash_flow의 반환값)을 그대로 사용합니다. annual_free_cash_flow는
    화면에 표시하는 최근 5개년보다 더 긴 회사 전체 이력을 담고 있으므로(연간
    Operating Cash Flow/CapEx 자체가 SEC에서 5개년보다 많이 조회됨), 별도로 SEC를
    추가 조회하지 않아도 화면에 나오는 가장 오래된 연도의 성장률까지 바로 앞
    회계연도 데이터로 정확히 계산할 수 있습니다. 정렬 후 리스트에서 바로 다음
    항목이 "전년도" 데이터가 되므로, 같은 회사의 연속된 회계연도끼리만 비교됩니다.

    위 공식은 전년도 FCF와 당해연도 FCF가 "둘 다 양수"일 때만 적용합니다. 한쪽
    이상이 적자(0 이하)이면 증감률(%) 자체가 흑자전환/적자전환 같은 부호 변화를
    가려서 투자적으로 오해를 줄 수 있으므로, status로 상황을 구분해 계산 대신
    표시 문구를 결정하게 합니다.
      - 전년도 FCF == 0           -> status "ZERO_BASE" (0으로 나눌 수 없어 N/A)
      - 전년도 FCF > 0, 당해 <= 0 -> status "POS_TO_NEG" (N/M, 흑자->적자)
      - 전년도 FCF < 0, 당해 > 0  -> status "NEG_TO_POS" (N/M, 적자->흑자)
      - 전년도 FCF < 0, 당해 <= 0 -> status "NEG_NEG" (N/M, 계속 적자)
      - 비교할 전년도 데이터가 없는 가장 오래된 연도 -> status "NO_PRIOR_YEAR" (N/A)
      - 둘 다 양수                -> status "OK" (공식대로 growth 계산)
    """
    annual_series = sorted(
        ((end_date, value) for end_date, value in annual_free_cash_flow.items() if value is not None),
        reverse=True,
    )

    growth_by_end_date = {}

    for i in range(len(annual_series)):
        end_date, current_value = annual_series[i]

        is_oldest_year = (i == len(annual_series) - 1)
        if is_oldest_year:
            growth_by_end_date[end_date] = {"growth": None, "status": "NO_PRIOR_YEAR"}
            continue

        _, previous_value = annual_series[i + 1]

        if previous_value == 0:
            growth_by_end_date[end_date] = {"growth": None, "status": "ZERO_BASE"}
        elif previous_value > 0:
            if current_value > 0:
                growth = (current_value / previous_value - 1) * 100
                growth_by_end_date[end_date] = {"growth": growth, "status": "OK"}
            else:
                growth_by_end_date[end_date] = {"growth": None, "status": "POS_TO_NEG"}
        else:  # previous_value < 0
            if current_value > 0:
                growth_by_end_date[end_date] = {"growth": None, "status": "NEG_TO_POS"}
            else:
                growth_by_end_date[end_date] = {"growth": None, "status": "NEG_NEG"}

    return growth_by_end_date


def calculate_fcf_margin(annual_free_cash_flow: dict, annual_revenue: dict) -> dict:
    """
    FCF Margin(%)을 계산합니다.

    계산식: FCF Margin = FCF / Revenue * 100

    annual_free_cash_flow(calculate_free_cash_flow의 반환값)와 annual_revenue
    (annual_revenue_dict, SEC 원본 Revenue)를 회계연도 종료일 기준으로 매칭해서
    계산하며, billion 단위로 변환하거나 반올림하기 전의 원본 값을 그대로
    사용합니다. FCF가 음수여도 공식 그대로 계산해 음수 Margin을 반환합니다.
    FCF 데이터가 없거나, Revenue가 0이거나 없는 회계연도는 None으로 둡니다
    (출력 시 N/A로 표시).
    """
    margin_by_end_date = {}

    for end_date, fcf_value in annual_free_cash_flow.items():
        revenue_value = annual_revenue.get(end_date)
        if fcf_value is None or not revenue_value:
            margin_by_end_date[end_date] = None
            continue

        margin_by_end_date[end_date] = fcf_value / revenue_value * 100

    return margin_by_end_date


def calculate_debt_to_equity(annual_debt: dict, annual_equity: dict) -> dict:
    """
    부채비율(Debt-to-Equity, %)을 계산합니다.

    계산식: Debt-to-Equity = Debt / Equity * 100

    annual_debt(extract_annual_debt의 반환값을 dict로 만든 것)와 annual_equity를
    회계연도 종료일 기준으로 매칭해서 계산하며, billion 단위로 변환하거나
    반올림하기 전의 원본 값을 그대로 사용합니다. SEC를 추가로 조회하지 않습니다.

    반환값은 {회계연도 종료일: {"value": float 또는 None, "status": str}} 형태이며,
    status는 다음 중 하나입니다.
      - "OK": Equity > 0. 공식대로 계산한 값을 그대로 사용합니다.
      - "ZERO_EQUITY": Equity == 0. 0으로 나눌 수 없어 값은 None(N/A)입니다.
      - "NEGATIVE_EQUITY": Equity < 0(자본잠식 등). 값 자체는 공식대로 계산해
        두지만, 분모가 음수면 결과가 커 보이거나 부호가 뒤집혀 일반적인
        부채비율로 해석하면 오해를 줄 수 있으므로 화면에는 계산값 대신 N/M
        문구를 표시합니다(format_debt_to_equity에서 처리).
      - "NO_DATA": 해당 회계연도에 Debt 또는 Equity 데이터가 없어 값은
        None(N/A)입니다.
    """
    result = {}

    for end_date, debt_value in annual_debt.items():
        equity_value = annual_equity.get(end_date)

        if equity_value is None:
            result[end_date] = {"value": None, "status": "NO_DATA"}
        elif equity_value == 0:
            result[end_date] = {"value": None, "status": "ZERO_EQUITY"}
        elif equity_value < 0:
            value = debt_value / equity_value * 100
            result[end_date] = {"value": value, "status": "NEGATIVE_EQUITY"}
        else:
            value = debt_value / equity_value * 100
            result[end_date] = {"value": value, "status": "OK"}

    return result


def calculate_debt_to_assets(annual_debt: dict, annual_assets: dict) -> dict:
    """
    Debt-to-Assets(%)을 계산합니다.

    계산식: Debt-to-Assets = Debt / Assets * 100

    annual_debt와 annual_assets를 회계연도 종료일 기준으로 매칭해서 계산하며,
    billion 단위로 변환하거나 반올림하기 전의 원본 값을 그대로 사용합니다.
    SEC를 추가로 조회하지 않습니다. Assets가 0이거나 해당 회계연도에 없으면
    None으로 둡니다(출력 시 N/A로 표시).
    """
    ratio_by_end_date = {}

    for end_date, debt_value in annual_debt.items():
        assets_value = annual_assets.get(end_date)
        if not assets_value:
            ratio_by_end_date[end_date] = None
            continue

        ratio_by_end_date[end_date] = debt_value / assets_value * 100

    return ratio_by_end_date


def calculate_roe(annual_net_income: dict, annual_parent_equity: list) -> dict:
    """
    ROE(Return on Equity, 자기자본이익률, %)를 계산합니다.

    계산식: ROE = Net Income(모회사/보통주 주주 귀속) / Average Equity(모회사
    귀속) x 100
    Average Equity = (전년도 말 Equity + 당해연도 말 Equity) / 2

    annual_net_income: {회계연도 종료일: Net Income} (NET_INCOME_TAGS의
    NetIncomeLoss는 XBRL 관례상 이미 모회사 귀속 순이익이라 별도 태그 없이
    그대로 사용합니다).
    annual_parent_equity: extract_annual_parent_equity()의 반환값(최신순
    정렬된 (회계연도 종료일, 모회사 귀속 Equity) 리스트). 화면에 표시할
    최근 5개년보다 한 해 더 과거의 Equity까지 포함되어 있어야, 화면에 표시되는
    가장 오래된 연도의 Average Equity도 실제 전년도 데이터로 계산할 수
    있습니다. billion 단위로 변환하거나 반올림하기 전의 SEC 원본 값을
    그대로 사용합니다.

    반환값은 {회계연도 종료일: {"value": float 또는 None, "status": str}} 형태이며,
    status는 다음 중 하나입니다.
      - "OK": Average Equity > 0. 공식대로 계산한 값을 그대로 사용합니다.
      - "ZERO_AVERAGE": Average Equity == 0. 0으로 나눌 수 없어 값은
        None(N/A)입니다.
      - "NEGATIVE_AVERAGE": Average Equity < 0(자본잠식 등). 일반적인 ROE로
        해석하면 오해를 줄 수 있어 화면에는 계산값 대신 N/M 문구를
        표시합니다(format_roe에서 처리).
      - "NO_PRIOR_EQUITY": 전년도 말 Equity 데이터가 없어 Average Equity를
        계산할 수 없는 경우입니다(값은 None, N/A).
      - "NO_NET_INCOME": 해당 회계연도 Net Income 데이터가 없는 경우입니다
        (값은 None, N/A).
    """
    result = {}

    for i, (end_date, current_equity) in enumerate(annual_parent_equity):
        net_income_value = annual_net_income.get(end_date)
        if net_income_value is None:
            result[end_date] = {"value": None, "status": "NO_NET_INCOME"}
            continue

        is_oldest_year = (i == len(annual_parent_equity) - 1)
        if is_oldest_year:
            result[end_date] = {"value": None, "status": "NO_PRIOR_EQUITY"}
            continue

        _, previous_equity = annual_parent_equity[i + 1]
        average_equity = (current_equity + previous_equity) / 2

        if average_equity == 0:
            result[end_date] = {"value": None, "status": "ZERO_AVERAGE"}
        elif average_equity < 0:
            value = net_income_value / average_equity * 100
            result[end_date] = {"value": value, "status": "NEGATIVE_AVERAGE"}
        else:
            value = net_income_value / average_equity * 100
            result[end_date] = {"value": value, "status": "OK"}

    return result


def format_revenue(value: int) -> str:
    """숫자를 보기 쉽게 '단위: 십억 달러(B)'로 바꿔줍니다."""
    billions = value / 1_000_000_000
    return f"${billions:,.2f}B"


def format_amount_auto(value: int) -> str:
    """
    금액 크기에 맞춰 B(십억)/M(백만)/K(천) 단위를 자동으로 고릅니다.
    등식검증 diff처럼 작은 값이 $0.00B로 보이지 않게 할 때 씁니다.
    """
    for scale, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= scale:
            return f"${value / scale:,.2f}{suffix}"
    return f"${value:,}"


def format_eps(value: float) -> str:
    """EPS는 billion 단위로 바꾸지 않고 USD/share 그대로, 소수점 둘째 자리까지 표시합니다."""
    return f"${value:,.2f}"


def format_balance_check(check) -> str:
    """
    validate_balance_sheet_equation()의 개별 검증 결과를 PASS/FAIL 텍스트로
    바꿔줍니다. FAIL 또는 PASS (rounding)이면 Assets - (Liabilities + Equity)
    차이값을 크기에 맞는 B/M/K 단위로 함께 표시합니다. Assets/
    Liabilities/Equity 중 하나라도 없어서 검증하지 못한 연도는 N/A로
    표시합니다.
    """
    if check is None:
        return "[dim]N/A[/dim]"

    if check.get("rounding"):
        return f"[green]PASS (rounding)[/green] (diff: {format_amount_auto(check['diff'])})"

    if check["is_valid"]:
        return "[green]PASS[/green]"

    return f"[bold red]FAIL[/bold red] (diff: {format_amount_auto(check['diff'])})"


def format_percent(value, show_sign: bool = False) -> str:
    """Growth/Margin 값을 소수점 둘째 자리까지의 %로 표시합니다. 값이 없으면 N/A."""
    if value is None:
        return "N/A"
    sign = "+" if (show_sign and value >= 0) else ""
    return f"{sign}{value:,.2f}%"


FCF_GROWTH_STATUS_LABELS = {
    "ZERO_BASE": "N/A",
    "NO_PRIOR_YEAR": "N/A",
    "POS_TO_NEG": "N/M (Positive → Negative)",
    "NEG_TO_POS": "N/M (Negative → Positive)",
    "NEG_NEG": "N/M (Negative FCF)",
}


def format_fcf_growth(growth_info) -> str:
    """
    FCF Growth 값을 소수점 둘째 자리까지의 %로 표시합니다.

    growth_info가 없으면 N/A. status가 "OK"(전년도·당해연도 FCF 모두 양수)가
    아니면 계산값 대신 FCF_GROWTH_STATUS_LABELS에 정의된 문구
    (N/A 또는 N/M (...))를 그대로 표시합니다.
    """
    if growth_info is None:
        return "N/A"

    status = growth_info["status"]
    if status != "OK":
        return FCF_GROWTH_STATUS_LABELS[status]

    value = growth_info["growth"]
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.2f}%"


DEBT_TO_EQUITY_STATUS_LABELS = {
    "ZERO_EQUITY": "N/A",
    "NO_DATA": "N/A",
    "NEGATIVE_EQUITY": "N/M (Negative Equity)",
}


def format_debt_to_equity(info) -> str:
    """
    Debt-to-Equity 값을 소수점 둘째 자리까지의 %로 표시합니다.

    info가 없으면 N/A. status가 "OK"(Equity > 0)가 아니면 계산값 대신
    DEBT_TO_EQUITY_STATUS_LABELS에 정의된 문구(N/A 또는 N/M (Negative
    Equity))를 표시합니다. Equity가 음수인 경우 값 자체는 계산되어 있지만,
    일반적인 부채비율로 해석하면 오해를 줄 수 있어 화면에는 수치 대신
    N/M 문구를 보여줍니다.
    """
    if info is None:
        return "N/A"

    status = info["status"]
    if status != "OK":
        return DEBT_TO_EQUITY_STATUS_LABELS[status]

    return format_percent(info["value"])


ROE_STATUS_LABELS = {
    "ZERO_AVERAGE": "N/A",
    "NO_PRIOR_EQUITY": "N/A",
    "NO_NET_INCOME": "N/A",
    "NEGATIVE_AVERAGE": "N/M (Negative Equity)",
}


def format_roe(info) -> str:
    """
    ROE 값을 소수점 둘째 자리까지의 %로 표시합니다.

    info가 없으면 N/A. status가 "OK"(Average Equity > 0)가 아니면 계산값
    대신 ROE_STATUS_LABELS에 정의된 문구(N/A 또는 N/M (Negative Equity))를
    표시합니다.
    """
    if info is None:
        return "N/A"

    status = info["status"]
    if status != "OK":
        return ROE_STATUS_LABELS[status]

    return format_percent(info["value"])


def build_company_table(
    company_name: str,
    annual_revenue: list,
    annual_operating_income: dict,
    annual_net_income: dict,
    annual_operating_cash_flow: dict,
    annual_capex: dict,
    annual_free_cash_flow: dict,
    annual_eps: dict,
    annual_assets: dict,
    annual_liabilities: dict,
    annual_equity: dict,
    annual_debt: dict,
    revenue_growth: dict,
    operating_margin: dict,
    net_margin: dict,
    eps_growth: dict,
    fcf_growth: dict,
    fcf_margin: dict,
    debt_to_equity: dict,
    debt_to_assets: dict,
    roe: dict,
) -> Table:
    """
    한 회사의 연도별 Revenue / Operating Income / Net Income / EPS / Free Cash Flow와
    계산 지표(Growth, Margin)를 rich Table로 만듭니다.

    각 금액 옆 괄호 안에 해당 지표의 YoY Growth 또는 Margin(%)을 함께 표시해서
    컬럼 수를 줄이고 한눈에 보기 쉽게 구성합니다.

    등식검증(A=L+E) 결과는 이 테이블에는 표시하지 않습니다. 정상(PASS)인 경우
    화면을 불필요하게 채우지 않기 위해서이며, FAIL인 회계연도가 있을 때만
    main()에서 별도 경고 메시지로 보여줍니다.
    """
    table = Table(title=f"[bold]{company_name}[/bold]", header_style="bold magenta", show_lines=True)
    table.add_column("회계연도", justify="center", style="bold")
    table.add_column("종료일", justify="center", style="dim")
    table.add_column("Revenue (YoY Growth)", justify="right", style="green")
    table.add_column("Operating Income (Margin)", justify="right", style="cyan")
    table.add_column("Net Income (Margin)", justify="right", style="yellow")
    table.add_column("EPS Diluted (YoY Growth)", justify="right", style="bright_white")
    table.add_column("Operating Cash Flow", justify="right", style="bright_green")
    table.add_column("CapEx", justify="right", style="bright_yellow")
    table.add_column("Free Cash Flow (YoY Growth / Margin)", justify="right", style="bold bright_green")
    table.add_column("Total Assets", justify="right", style="magenta")
    table.add_column("Total Liabilities", justify="right", style="red")
    table.add_column("Total Equity", justify="right", style="blue")
    table.add_column("ROE", justify="right", style="bold blue")
    table.add_column("Total Debt (D/E / D/A)", justify="right", style="bright_red")

    for end_date, revenue_value in annual_revenue[:5]:
        fiscal_year = end_date[:4]

        revenue_text = f"{format_revenue(revenue_value)} ({format_percent(revenue_growth.get(end_date), show_sign=True)})"

        operating_income_value = annual_operating_income.get(end_date)
        if operating_income_value is None:
            operating_income_text = "[red]N/A[/red]"
        else:
            operating_income_text = (
                f"{format_revenue(operating_income_value)} "
                f"({format_percent(operating_margin.get(end_date))})"
            )

        net_income_value = annual_net_income.get(end_date)
        if net_income_value is None:
            net_income_text = "[red]N/A[/red]"
        else:
            net_income_text = f"{format_revenue(net_income_value)} ({format_percent(net_margin.get(end_date))})"

        eps_value = annual_eps.get(end_date)
        if eps_value is None:
            eps_text = "[red]N/A[/red]"
        else:
            eps_text = f"{format_eps(eps_value)} ({format_percent(eps_growth.get(end_date), show_sign=True)})"

        operating_cash_flow_value = annual_operating_cash_flow.get(end_date)
        operating_cash_flow_text = (
            format_revenue(operating_cash_flow_value)
            if operating_cash_flow_value is not None
            else "[red]N/A[/red]"
        )

        capex_value = annual_capex.get(end_date)
        capex_text = format_revenue(capex_value) if capex_value is not None else "[red]N/A[/red]"

        fcf_value = annual_free_cash_flow.get(end_date)
        if fcf_value is None:
            fcf_text = "[red]N/A[/red]"
        else:
            fcf_text = (
                f"{format_revenue(fcf_value)} "
                f"({format_fcf_growth(fcf_growth.get(end_date))} / "
                f"Margin: {format_percent(fcf_margin.get(end_date))})"
            )

        assets_value = annual_assets.get(end_date)
        assets_text = format_revenue(assets_value) if assets_value is not None else "[red]N/A[/red]"

        liabilities_value = annual_liabilities.get(end_date)
        liabilities_text = format_revenue(liabilities_value) if liabilities_value is not None else "[red]N/A[/red]"

        equity_value = annual_equity.get(end_date)
        equity_text = format_revenue(equity_value) if equity_value is not None else "[red]N/A[/red]"

        roe_text = format_roe(roe.get(end_date))

        debt_value = annual_debt.get(end_date)
        if debt_value is None:
            debt_text = "[red]N/A[/red]"
        else:
            debt_text = (
                f"{format_revenue(debt_value)} "
                f"(D/E: {format_debt_to_equity(debt_to_equity.get(end_date))} / "
                f"D/A: {format_percent(debt_to_assets.get(end_date))})"
            )

        table.add_row(
            f"FY{fiscal_year}",
            end_date,
            revenue_text,
            operating_income_text,
            net_income_text,
            eps_text,
            operating_cash_flow_text,
            capex_text,
            fcf_text,
            assets_text,
            liabilities_text,
            equity_text,
            roe_text,
            debt_text,
        )

    return table


def build_comparison_table(company_summaries: list) -> Table:
    """
    각 기업의 "최신 회계연도" 주요 지표를 나란히 비교하는 rich Table을 만듭니다.

    회사마다 회계연도 종료일이 다를 수 있어(예: GOOGL은 12월 결산, ORCL은
    5월 결산) 각 기업 컬럼 헤더에 그 기업의 최신 Fiscal Year와 종료일을
    함께 표시해서, 어느 회계연도 기준 값인지 명확히 구분합니다.

    company_summaries의 각 항목은 main()에서 이미 계산·포맷된 값을 그대로
    담은 dict이며, 이 함수는 새로 조회하거나 계산하지 않고 표로 배치만
    합니다. 기존 format_percent/format_fcf_growth/format_roe/
    format_debt_to_equity가 만든 N/A, N/M 표시도 그대로 유지됩니다.

    수치가 더 높거나 낮은 기업을 "우수"/"승자"로 표시하는 로직은 의도적으로
    넣지 않았습니다. 예를 들어 부채비율처럼 낮은 값이 더 나은 지표도 있어,
    단순 대소 비교로 자동 판단하면 오해를 줄 수 있기 때문입니다.
    """
    table = Table(
        title="[bold]기업 비교 요약 (각 기업의 최신 회계연도 기준)[/bold]",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("지표", style="bold")
    for summary in company_summaries:
        header = f"{summary['company_name']}\nFY{summary['fiscal_year']} ({summary['end_date']})"
        table.add_column(header, justify="right")

    metric_rows = [
        ("Revenue Growth (YoY)", "revenue_growth"),
        ("Operating Margin", "operating_margin"),
        ("Net Margin", "net_margin"),
        ("EPS Growth (YoY)", "eps_growth"),
        ("FCF Growth (YoY)", "fcf_growth"),
        ("FCF Margin", "fcf_margin"),
        ("ROE", "roe"),
        ("Debt-to-Equity", "debt_to_equity"),
        ("Debt-to-Assets", "debt_to_assets"),
    ]

    for label, key in metric_rows:
        table.add_row(label, *(summary[key] for summary in company_summaries))

    return table


def analyze_company(ticker: str) -> dict:
    """
    티커 하나를 SEC에서 조회하고, 기존 extract_* / calculate_* 함수로 모든 지표를
    계산해서 결과를 dict 하나로 모아 반환합니다.

    이 함수의 본문은 원래 main()의 회사별 반복문 안에 인라인으로 있던 코드를
    그대로 옮긴 것입니다(조회 순서와 계산 방식 동일). 콘솔 출력(main)과
    Streamlit 화면(app.py)이 같은 분석 엔진을 호출하게 만들기 위해 함수로만
    분리했고, 계산식은 바꾸지 않았습니다.

    반환 dict의 키는 build_company_table()의 인자 이름과 같게 맞췄습니다.
    티커를 찾을 수 없으면 TickerNotFoundError, SEC 조회가 실패하면 requests의
    예외가 그대로 올라가므로 호출하는 쪽에서 처리합니다.
    """
    company_name, cik = resolve_company(ticker)
    facts = get_company_facts(cik)

    # 원본 재무 데이터 (연도 전체 이력을 담고 있음 - 성장률 계산에 필요)
    annual_revenue = extract_annual_revenue(facts)
    annual_operating_income_list = extract_annual_operating_income(facts)
    annual_net_income_list = extract_annual_net_income(facts)
    annual_operating_cash_flow_list = extract_annual_operating_cash_flow(facts)
    annual_capex_list = extract_annual_capex(facts)
    annual_eps_list = extract_annual_eps(facts)
    annual_assets_list = extract_annual_assets(facts)
    annual_liabilities_list, liabilities_details = extract_annual_liabilities_with_details(facts)
    annual_equity_list = extract_annual_equity(facts)
    annual_parent_equity_list = extract_annual_parent_equity(facts)
    annual_debt_list, debt_details = extract_annual_debt_with_details(facts, company_name)

    annual_operating_income = dict(annual_operating_income_list)
    annual_net_income = dict(annual_net_income_list)
    annual_operating_cash_flow = dict(annual_operating_cash_flow_list)
    annual_capex = dict(annual_capex_list)
    annual_eps = dict(annual_eps_list)
    annual_assets = dict(annual_assets_list)
    annual_liabilities = dict(annual_liabilities_list)
    annual_equity = dict(annual_equity_list)
    annual_debt = dict(annual_debt_list)
    annual_revenue_dict = dict(annual_revenue)

    # 원본(반올림 전) 값으로 지표 계산
    balance_sheet_check = validate_balance_sheet_equation(
        annual_assets,
        annual_liabilities,
        annual_equity,
        extract_annual_temporary_equity(facts),
    )
    revenue_growth = calculate_yoy_growth(annual_revenue)
    eps_growth = calculate_yoy_growth(annual_eps_list)
    operating_margin = calculate_margin(annual_operating_income, annual_revenue_dict)
    net_margin = calculate_margin(annual_net_income, annual_revenue_dict)
    annual_free_cash_flow = calculate_free_cash_flow(annual_operating_cash_flow, annual_capex)
    fcf_growth = calculate_fcf_growth(annual_free_cash_flow)
    fcf_margin = calculate_fcf_margin(annual_free_cash_flow, annual_revenue_dict)
    debt_to_equity = calculate_debt_to_equity(annual_debt, annual_equity)
    debt_to_assets = calculate_debt_to_assets(annual_debt, annual_assets)
    roe = calculate_roe(annual_net_income, annual_parent_equity_list)

    return {
        "ticker": company_name.split(" ", 1)[0],
        "company_name": company_name,
        "annual_revenue": annual_revenue,
        "annual_operating_income": annual_operating_income,
        "annual_net_income": annual_net_income,
        "annual_operating_cash_flow": annual_operating_cash_flow,
        "annual_capex": annual_capex,
        "annual_free_cash_flow": annual_free_cash_flow,
        "annual_eps": annual_eps,
        "annual_assets": annual_assets,
        "annual_liabilities": annual_liabilities,
        "liabilities_details": liabilities_details,
        "annual_equity": annual_equity,
        "annual_debt": annual_debt,
        "debt_details": debt_details,
        "revenue_growth": revenue_growth,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "eps_growth": eps_growth,
        "fcf_growth": fcf_growth,
        "fcf_margin": fcf_margin,
        "debt_to_equity": debt_to_equity,
        "debt_to_assets": debt_to_assets,
        "roe": roe,
        "balance_sheet_check": balance_sheet_check,
    }


def main():
    console = Console()
    console.rule(
        "[bold blue]SEC EDGAR 연간 Revenue / Operating Income / Net Income / EPS / "
        "Operating Cash Flow / CapEx / Assets / Liabilities / Equity / Debt 조회 "
        "(최근 5개 회계연도)[/bold blue]"
    )
    console.print(
        "[dim]Total Equity 사용 태그: "
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest "
        "(비지배지분 포함, 우선 사용) / 없는 연도는 us-gaap:StockholdersEquity로 대체[/dim]"
    )
    console.print(
        "[dim]Total Debt 사용 태그 및 합산 로직:[/dim]\n"
        "[dim]  GOOGL: us-gaap:CommercialPaper + us-gaap:LongTermDebtCurrent + "
        "(us-gaap:LongTermDebtNoncurrent, 없는 연도는 us-gaap:LongTermDebt로 대체)[/dim]\n"
        "[dim]  ORCL : us-gaap:NotesPayableCurrent + us-gaap:LongTermNotesPayable[/dim]\n"
        "[dim]  MSFT : us-gaap:CommercialPaper(optional, 없는 연도는 $0으로 간주) "
        "+ us-gaap:LongTermDebtCurrent + us-gaap:LongTermDebtNoncurrent. MSFT는 "
        "상업어음 잔액이 $0인 일부 회계연도(확인된 범위: FY2020~FY2022, "
        "FY2026)에 us-gaap:CommercialPaper 태그 자체를 보고하지 않는데, 이 "
        "연도들은 재무제표에 잔액 없는 항목을 싣지 않은 것으로 보고 0을 더해 "
        "계산합니다(FY2026 10-K Note 10 - Debt의 실제 Total Debt $40.294B와 "
        "대조해 정확히 일치함을 확인함).[/dim]"
    )
    console.print(
        "[dim]Operating Cash Flow 사용 태그: "
        "us-gaap:NetCashProvidedByUsedInOperatingActivities "
        "(GOOGL, ORCL, MSFT 공통, 회계연도 전체 기간 duration fact)[/dim]"
    )
    console.print(
        "[dim]CapEx 사용 태그: "
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment "
        "(GOOGL, ORCL, MSFT 공통, 회계연도 전체 기간 duration fact, 부호는 항상 양수로 통일)[/dim]"
    )
    console.print(
        "[dim]Free Cash Flow(FCF) = Operating Cash Flow - CapEx "
        "(SEC 추가 조회 없이 기존 OCF/CapEx 원본 값으로 계산)[/dim]"
    )
    console.print(
        "[dim]FCF Growth(YoY) = (당해연도 FCF / 전년도 FCF - 1) x 100, "
        "billion 변환/반올림 전 원본 FCF 값 기준. 이 공식은 전년도·당해연도 FCF가 "
        "모두 양수일 때만 적용합니다. 전년도 FCF가 0이면 N/A. 흑자->적자는 "
        "N/M (Positive → Negative), 적자->흑자는 N/M (Negative → Positive), "
        "두 해 모두 적자면 N/M (Negative FCF)로 표시해, 부호가 바뀌거나 적자가 낀 "
        "구간의 증감률(%)을 액면 그대로의 '성장'으로 오해하지 않도록 구분했습니다.[/dim]"
    )
    console.print(
        "[dim]FCF Margin = FCF / Revenue x 100, billion 변환/반올림 전 원본 FCF·"
        "Revenue 값을 같은 회계연도끼리 매칭해 계산합니다(SEC 추가 조회 없음). "
        "FCF가 음수면 Margin도 음수로 그대로 표시하고, Revenue가 0이거나 없으면 "
        "N/A로 표시합니다.[/dim]"
    )
    console.print(
        "[dim]Debt-to-Equity = Debt / Equity x 100, Debt-to-Assets = Debt / Assets "
        "x 100. billion 변환/반올림 전 원본 Debt·Equity·Assets 값을 같은 "
        "회계연도끼리 매칭해 계산합니다(SEC 추가 조회 없음). Equity가 0이면 "
        "Debt-to-Equity는 N/A. Equity가 음수(자본잠식 등)면 계산값이 왜곡되어 "
        "보일 수 있어 수치 대신 N/M (Negative Equity)로 표시합니다. Assets가 "
        "0이거나 없으면 Debt-to-Assets는 N/A로 표시합니다.[/dim]"
    )
    console.print(
        "[dim]ROE = Net Income(모회사 귀속) / Average Equity(모회사 귀속) x 100, "
        "Average Equity = (전년도 말 + 당해연도 말 모회사 귀속 Equity) / 2. "
        "billion 변환/반올림 전 원본 값 기준. Net Income은 Balance Sheet 등식 "
        "검증(A=L+E)에 쓰는 비지배지분 포함 Equity가 아니라, 이와 귀속 대상을 "
        "맞춘 us-gaap:StockholdersEquity(모회사 귀속, 비지배지분 제외)를 분모로 "
        "사용합니다. GOOGL, MSFT는 비지배지분이 없어 StockholdersEquity가 곧 "
        "총자본과 동일하고, ORCL은 비지배지분이 있어 StockholdersEquity(모회사 "
        "귀속)가 "
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        "(비지배지분 포함, Balance Sheet 등식 검증용)보다 작습니다. 세 회사 모두 "
        "Net Income은 us-gaap:NetIncomeLoss(모회사 귀속)를 사용합니다(ORCL의 "
        "us-gaap:ProfitLoss는 비지배지분 포함 값이라 사용하지 않음). Average "
        "Equity가 0이면 N/A, 음수(자본잠식 등)면 N/M (Negative Equity)로 "
        "표시합니다.[/dim]"
    )

    comparison_rows = []

    for ticker in KNOWN_COMPANIES_BY_TICKER:
        # 조회와 계산은 analyze_company()가 전부 담당하고, main()은 그 결과를
        # 콘솔에 표시하는 역할만 합니다.
        result = analyze_company(ticker)

        company_name = result["company_name"]
        annual_revenue = result["annual_revenue"]
        annual_operating_income = result["annual_operating_income"]
        annual_net_income = result["annual_net_income"]
        annual_operating_cash_flow = result["annual_operating_cash_flow"]
        annual_capex = result["annual_capex"]
        annual_free_cash_flow = result["annual_free_cash_flow"]
        annual_eps = result["annual_eps"]
        annual_assets = result["annual_assets"]
        annual_liabilities = result["annual_liabilities"]
        annual_equity = result["annual_equity"]
        annual_debt = result["annual_debt"]
        revenue_growth = result["revenue_growth"]
        operating_margin = result["operating_margin"]
        net_margin = result["net_margin"]
        eps_growth = result["eps_growth"]
        fcf_growth = result["fcf_growth"]
        fcf_margin = result["fcf_margin"]
        debt_to_equity = result["debt_to_equity"]
        debt_to_assets = result["debt_to_assets"]
        roe = result["roe"]
        balance_sheet_check = result["balance_sheet_check"]

        console.print()
        if not annual_revenue:
            console.print(f"[bold]{company_name}[/bold]")
            console.print("  [red]매출 데이터를 찾을 수 없습니다.[/red]")
            continue

        table = build_company_table(
            company_name,
            annual_revenue,
            annual_operating_income,
            annual_net_income,
            annual_operating_cash_flow,
            annual_capex,
            annual_free_cash_flow,
            annual_eps,
            annual_assets,
            annual_liabilities,
            annual_equity,
            annual_debt,
            revenue_growth,
            operating_margin,
            net_margin,
            eps_growth,
            fcf_growth,
            fcf_margin,
            debt_to_equity,
            debt_to_assets,
            roe,
        )
        console.print(table)

        # 등식검증(A=L+E)은 화면 테이블에는 표시하지 않되, 화면에 표시되는
        # 5개년 중 FAIL이 있는 경우에만 경고를 별도로 띄웁니다. 검증 로직
        # 자체(validate_balance_sheet_equation)는 그대로 유지합니다.
        displayed_end_dates = {end_date for end_date, _ in annual_revenue[:5]}
        failed_years = sorted(
            (
                (end_date, check)
                for end_date, check in balance_sheet_check.items()
                if end_date in displayed_end_dates and not check["is_valid"]
            ),
            reverse=True,
        )
        if failed_years:
            console.print(f"[bold red][경고] {company_name} 등식검증(A=L+E) 실패 회계연도가 있습니다:[/bold red]")
            for end_date, check in failed_years:
                fiscal_year = end_date[:4]
                console.print(f"  FY{fiscal_year} ({end_date}): {format_balance_check(check)}")

        # 기업 비교 요약용: 이 회사의 "최신" 회계연도(annual_revenue의 첫 항목,
        # 최신순 정렬) 지표만 뽑아서 이미 계산/포맷 함수로 만든 텍스트 그대로
        # 저장합니다. 새로 조회하거나 계산하지 않습니다.
        latest_end_date = annual_revenue[0][0]
        comparison_rows.append({
            "company_name": company_name,
            "fiscal_year": latest_end_date[:4],
            "end_date": latest_end_date,
            "revenue_growth": format_percent(revenue_growth.get(latest_end_date), show_sign=True),
            "operating_margin": format_percent(operating_margin.get(latest_end_date)),
            "net_margin": format_percent(net_margin.get(latest_end_date)),
            "eps_growth": format_percent(eps_growth.get(latest_end_date), show_sign=True),
            "fcf_growth": format_fcf_growth(fcf_growth.get(latest_end_date)),
            "fcf_margin": format_percent(fcf_margin.get(latest_end_date)),
            "roe": format_roe(roe.get(latest_end_date)),
            "debt_to_equity": format_debt_to_equity(debt_to_equity.get(latest_end_date)),
            "debt_to_assets": format_percent(debt_to_assets.get(latest_end_date)),
        })

    if comparison_rows:
        console.print()
        console.print(build_comparison_table(comparison_rows))


if __name__ == "__main__":
    main()
