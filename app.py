"""
Stock Analyzer v0.3 - Streamlit MVP (1단계)

웹 화면에서 미국 주식 티커를 직접 입력하고 Analyze 버튼을 누르면
기존 v0.2 분석 엔진(sec_financials.py)을 호출해 결과를 표로 보여줍니다.

이 파일은 "화면"만 담당합니다. SEC 조회, XBRL 태그 fallback, 재무지표 계산,
N/A / N/M 예외처리, Balance Sheet 등식검증(A=L+E)은 모두 sec_financials.py의
기존 함수를 그대로 호출하며, 이 파일에서 다시 계산하지 않습니다.

실행: streamlit run app.py
"""

import os

import pandas as pd
import requests
import streamlit as st

from sec_financials import (
    BALANCE_ROUNDING_MAX_ASSET_RATIO,
    BALANCE_ROUNDING_MAX_UNITS,
    CAPEX_TAGS,
    OPERATING_INCOME_TAGS,
    SEC_USER_AGENT_ENV,
    SecUserAgentMissingError,
    TickerNotFoundError,
    analyze_company,
    format_amount_auto,
    format_debt_to_equity,
    format_eps,
    format_fcf_growth,
    format_percent,
    format_revenue,
    format_roe,
    get_sec_headers,
)

DEFAULT_TICKERS = ["GOOGL", "ORCL", "MSFT"]


def load_sec_user_agent_from_secrets() -> None:
    """
    Streamlit Secrets(로컬 .streamlit/secrets.toml 또는 Streamlit Cloud의
    Secrets 설정)에 SEC_USER_AGENT가 있으면 환경변수로 넘겨서
    sec_financials.py가 읽을 수 있게 합니다. 이미 환경변수가 있으면 그대로 둡니다.
    """
    try:
        user_agent = st.secrets.get(SEC_USER_AGENT_ENV)
    except Exception:  # secrets 파일이 아예 없으면 Streamlit이 예외를 냅니다.
        return
    if user_agent:
        os.environ.setdefault(SEC_USER_AGENT_ENV, str(user_agent))
MAX_TICKERS = 3

# 콘솔 버전과 동일하게 최근 5개 회계연도만 표시합니다.
DISPLAY_YEARS = 5


def normalize_tickers(raw_tickers: list) -> list:
    """
    입력된 티커들을 앞뒤 공백 제거 -> 대문자 변환 -> 빈 값 제거 -> 중복 제거
    순서로 정리합니다. 입력한 순서는 그대로 유지합니다.
    """
    normalized = []

    for raw_ticker in raw_tickers:
        ticker = raw_ticker.strip().upper()
        if not ticker:
            continue
        if ticker in normalized:
            continue
        normalized.append(ticker)

    return normalized


def describe_error(error: Exception, ticker: str) -> str:
    """조회/분석 중 발생한 예외를 사용자가 이해할 수 있는 문장으로 바꿉니다."""
    if isinstance(error, (TickerNotFoundError, SecUserAgentMissingError)):
        return str(error)

    if isinstance(error, requests.HTTPError):
        status_code = error.response.status_code if error.response is not None else None
        if status_code == 404:
            return (
                f"SEC에 '{ticker}'의 XBRL 재무 데이터(companyfacts)가 없습니다. "
                "미국 상장사가 아니거나 10-K를 제출하지 않는 기업일 수 있습니다."
            )
        if status_code == 403:
            return (
                "SEC가 요청을 거부했습니다(403). SEC는 User-Agent에 연락처를 넣도록 "
                "요구하며, 짧은 시간에 요청이 많으면 차단할 수 있습니다. "
                "잠시 후 다시 시도해 주세요."
            )
        if status_code == 429:
            return "SEC 요청 한도를 초과했습니다(429). 잠시 후 다시 시도해 주세요."
        return f"SEC 조회가 실패했습니다 (HTTP {status_code})."

    if isinstance(error, requests.Timeout):
        return "SEC 응답이 너무 오래 걸려 중단했습니다. 네트워크 상태를 확인해 주세요."

    if isinstance(error, requests.ConnectionError):
        return "SEC에 연결할 수 없습니다. 인터넷 연결 상태를 확인해 주세요."

    if isinstance(error, requests.RequestException):
        return f"SEC 조회 중 오류가 발생했습니다: {error}"

    return f"분석 중 예상치 못한 오류가 발생했습니다: {type(error).__name__}: {error}"


def format_money(value) -> str:
    """금액은 기존 format_revenue()로 표시하고, 값이 없으면 콘솔과 같이 N/A."""
    return format_revenue(value) if value is not None else "N/A"


def format_eps_value(value) -> str:
    """EPS는 기존 format_eps()로 표시하고, 값이 없으면 콘솔과 같이 N/A."""
    return format_eps(value) if value is not None else "N/A"


def build_financials_frame(result: dict, end_dates: list) -> pd.DataFrame:
    """
    analyze_company()가 돌려준 값을 '지표 x 회계연도' 표로 배치합니다.

    새로 계산하는 값은 없고, 기존 format_* 함수가 만든 문자열(N/A, N/M 표기
    포함)을 그대로 배치만 합니다.
    """
    revenue_by_end = dict(result["annual_revenue"])

    metric_rows = [
        ("Revenue (매출)", [format_money(revenue_by_end.get(end)) for end in end_dates]),
        ("Operating Income (영업이익)", [format_money(result["annual_operating_income"].get(end)) for end in end_dates]),
        ("Net Income (순이익)", [format_money(result["annual_net_income"].get(end)) for end in end_dates]),
        ("Diluted EPS (희석 주당순이익)", [format_eps_value(result["annual_eps"].get(end)) for end in end_dates]),
        ("Revenue Growth (매출 성장률)", [format_percent(result["revenue_growth"].get(end), show_sign=True) for end in end_dates]),
        ("Operating Margin (영업이익률)", [format_percent(result["operating_margin"].get(end)) for end in end_dates]),
        ("Net Margin (순이익률)", [format_percent(result["net_margin"].get(end)) for end in end_dates]),
        ("EPS Growth (EPS 성장률)", [format_percent(result["eps_growth"].get(end), show_sign=True) for end in end_dates]),
        ("Assets (자산)", [format_money(result["annual_assets"].get(end)) for end in end_dates]),
        ("Liabilities (부채)", [format_money(result["annual_liabilities"].get(end)) for end in end_dates]),
        ("Equity (자본)", [format_money(result["annual_equity"].get(end)) for end in end_dates]),
        ("Debt (이자부채)", [format_money(result["annual_debt"].get(end)) for end in end_dates]),
        ("Operating Cash Flow (영업현금흐름)", [format_money(result["annual_operating_cash_flow"].get(end)) for end in end_dates]),
        ("CapEx (자본적 지출)", [format_money(result["annual_capex"].get(end)) for end in end_dates]),
        ("Free Cash Flow (잉여현금흐름)", [format_money(result["annual_free_cash_flow"].get(end)) for end in end_dates]),
        ("FCF Growth (FCF 성장률)", [format_fcf_growth(result["fcf_growth"].get(end)) for end in end_dates]),
        ("FCF Margin (FCF 마진)", [format_percent(result["fcf_margin"].get(end)) for end in end_dates]),
        ("Debt-to-Equity (이자부채/자본)", [format_debt_to_equity(result["debt_to_equity"].get(end)) for end in end_dates]),
        ("Debt-to-Assets (이자부채/자산)", [format_percent(result["debt_to_assets"].get(end)) for end in end_dates]),
        ("ROE (자기자본이익률)", [format_roe(result["roe"].get(end)) for end in end_dates]),
    ]

    columns = [f"FY{end[:4]} ({end})" for end in end_dates]
    return pd.DataFrame(
        {column: [values[index] for _, values in metric_rows] for index, column in enumerate(columns)},
        index=[label for label, _ in metric_rows],
    )


def build_comparison_frame(summaries: list) -> pd.DataFrame:
    """
    각 기업의 최신 회계연도 지표를 나란히 비교하는 표를 만듭니다.
    콘솔의 build_comparison_table()과 같은 지표 구성이며, 값은 이미 포맷된
    문자열을 그대로 사용합니다.
    """
    metric_rows = [
        ("Revenue Growth (매출 성장률)", "revenue_growth"),
        ("Operating Margin (영업이익률)", "operating_margin"),
        ("Net Margin (순이익률)", "net_margin"),
        ("EPS Growth (EPS 성장률)", "eps_growth"),
        ("FCF Growth (FCF 성장률)", "fcf_growth"),
        ("FCF Margin (FCF 마진)", "fcf_margin"),
        ("ROE (자기자본이익률)", "roe"),
        ("Debt-to-Equity (이자부채/자본)", "debt_to_equity"),
        ("Debt-to-Assets (이자부채/자산)", "debt_to_assets"),
    ]

    return pd.DataFrame(
        {
            f"{summary['company_name']} / FY{summary['fiscal_year']} ({summary['end_date']})": [
                summary[key] for _, key in metric_rows
            ]
            for summary in summaries
        },
        index=[label for label, _ in metric_rows],
    )


def run_analysis(tickers: list) -> dict:
    """
    티커별로 analyze_company()를 호출합니다. 한 티커에서 오류가 나도 나머지
    티커 분석은 계속 진행하도록, 티커 단위로 예외를 잡아 메시지로 남깁니다.
    """
    companies = []
    errors = []

    progress = st.progress(0.0, text="SEC 데이터 조회 중...")

    for index, ticker in enumerate(tickers):
        progress.progress(index / len(tickers), text=f"SEC 데이터 조회 중... ({ticker})")
        try:
            result = analyze_company(ticker)
        except Exception as error:  # 티커 하나의 실패로 전체가 멈추지 않도록 처리
            errors.append((ticker, describe_error(error, ticker)))
            continue

        if not result["annual_revenue"]:
            # 콘솔 버전과 동일한 처리: 매출 데이터가 없으면 표를 만들지 않습니다.
            errors.append((
                ticker,
                f"{result['company_name']}: 10-K 기준 매출(Revenue) 데이터를 찾을 수 없어 "
                "분석을 진행할 수 없습니다.",
            ))
            continue

        companies.append(result)

    progress.empty()

    return {"companies": companies, "errors": errors}


def render_debt_source(debt_details: dict, end_dates: list) -> None:
    """
    Debt(이자부채)를 어떤 SEC XBRL 태그로 계산했는지, 계산하지 못했다면 그 이유가
    "SEC에 데이터 없음"인지 "프로그램이 태그 조합을 찾지 못함"인지 표시합니다.
    """
    status = debt_details["status"]

    if status == "deposit_institution":
        deposits = debt_details["deposits"]
        st.caption(
            "Debt 출처: 예금 부채를 보고하는 은행형 재무구조라 Debt / Debt-to-Equity / "
            "Debt-to-Assets를 계산하지 않습니다(N/A). 은행은 예금·Repo·차입금이 모두 "
            "이자부 조달이라 일반 기업과 같은 의미의 이자부채를 정할 수 없습니다. "
            f"(근거: {deposits['tag']} {format_revenue(deposits['value'])}, "
            f"FY{deposits['end'][:4]})"
        )
        return

    if status == "not_reported":
        st.caption(
            "Debt 출처: SEC 10-K 데이터에 이자부채 관련 태그가 없습니다 "
            "(회사가 이자부채를 보고하지 않음 - 무차입 가능성)."
        )
        return

    if status == "unmatched":
        st.warning(
            "Debt 출처: 이자부채 관련 태그("
            + ", ".join(debt_details["found_tags"])
            + ")는 있지만 연도별 합산 조합을 찾지 못해 N/A로 표시했습니다."
        )
        return

    lines = []
    for end in end_dates:
        info = debt_details["by_year"].get(end)
        if info is None:
            missing_tags = debt_details["unconfirmed_zero"].get(end)
            if missing_tags:
                lines.append(
                    f"- FY{end[:4]}: 보고된 항목은 0이지만 {', '.join(missing_tags)} "
                    "미보고로 $0 확정 불가 (N/A)"
                )
            else:
                lines.append(f"- FY{end[:4]}: 이 회계연도에 맞는 태그 조합 없음 (N/A)")
            continue
        components = " + ".join(
            f"{tag} {format_revenue(value)}" for tag, value in info["components"]
        )
        if info["assumed_zero"]:
            components += "".join(
                f" + {tags} 미보고 → $0으로 간주" for tags in info["assumed_zero"]
            )
        if info["total"] == 0:
            components += " (모든 구성요소가 0으로 명시 보고되어 $0 확정)"
        lines.append(f"- FY{end[:4]}: {info['method']} — {components}")

    with st.expander("Debt(이자부채) 계산에 사용한 SEC XBRL 태그"):
        st.markdown("\n".join(lines))


def describe_missing_years(values_by_end: dict, end_dates: list) -> str:
    """
    화면에 표시된 연도 중 값이 없는(N/A) 연도를 문구로 돌려줍니다.
    모두 있으면 빈 문자열, 모두 없으면 "표시된 전 회계연도"입니다.
    """
    missing = [end for end in end_dates if values_by_end.get(end) is None]
    if not missing:
        return ""
    if len(missing) == len(end_dates):
        return "표시된 전 회계연도"
    return ", ".join(f"FY{end[:4]}" for end in missing)


def render_metric_notes(result: dict, end_dates: list) -> None:
    """
    Operating Income과 CapEx가 N/A인 연도가 있으면 그 이유를 안내하고,
    Liabilities를 차감 계산으로 구한 연도가 있으면 그 출처를 안내합니다.

    N/A는 해당 연도의 10-K에 프로그램이 사용하는 태그가 보고되지 않았다는
    뜻이며, 값을 추정해서 채우지 않습니다. 예금 부채를 보고하는 은행형
    재무구조(debt_details의 "deposit_institution")이면 은행 재무제표 구조에
    따른 설명을 덧붙입니다.
    """
    is_deposit_institution = result["debt_details"]["status"] == "deposit_institution"
    notes = []

    missing_operating_income = describe_missing_years(result["annual_operating_income"], end_dates)
    if missing_operating_income:
        note = (
            f"Operating Income 출처: {missing_operating_income} N/A - "
            f"10-K에 {', '.join(OPERATING_INCOME_TAGS)} 태그가 보고되지 않아 표시하지 "
            "않습니다(추정하지 않음). Operating Margin도 함께 N/A입니다."
        )
        if is_deposit_institution:
            note += (
                " 은행 손익계산서에는 영업이익 항목이 없습니다. 순영업수익(순이자이익 + "
                "비이자이익)에서 대손충당금 전입액과 비이자비용을 빼면 바로 법인세 차감 전 "
                "이익이 되며, 이자비용이 영업 원가라서 일반 기업의 영업이익과 같은 단계가 "
                "존재하지 않습니다."
            )
        notes.append(note)

    missing_capex = describe_missing_years(result["annual_capex"], end_dates)
    if missing_capex:
        note = (
            f"CapEx 출처: {missing_capex} N/A - 10-K에 "
            f"{' / '.join(CAPEX_TAGS)} 태그가 보고되지 않아 표시하지 않습니다"
            "(유형자산 잔액 변동 등으로 추정하지 않음). 이 때문에 Free Cash Flow, "
            "FCF Growth, FCF Margin도 함께 N/A입니다."
        )
        if is_deposit_institution:
            note += (
                " 은행은 유형자산 취득액을 별도 항목 없이 기타 투자활동 순액에 포함해 "
                "보고하기도 합니다. 또한 은행의 영업현금흐름에는 트레이딩 "
                "자산·부채 증감과 대출 관련 자금 흐름이 섞여 있어, CapEx가 있더라도 "
                "FCF를 일반 기업처럼 해석하기 어렵습니다."
            )
        notes.append(note)

    # 총부채 합계 태그가 없어 "부채와 자본 총계 - 총자본"으로 구한 연도를 알립니다.
    derived_liabilities_years = [
        end
        for end in end_dates
        if result["liabilities_details"].get(end, {}).get("method") == "부채와 자본 총계 - 총자본"
    ]
    if derived_liabilities_years:
        latest_components = result["liabilities_details"][derived_liabilities_years[0]]["components"]
        notes.append(
            "Liabilities 출처: "
            + ", ".join(f"FY{end[:4]}" for end in derived_liabilities_years)
            + " - 10-K에 총부채(Liabilities) 합계 태그가 없어 "
            + " - ".join(f"{tag}" for tag, _value in latest_components)
            + "로 계산했습니다. 이 연도의 A=L+E 검증은 Assets와 부채·자본 총계의 일치 여부를 확인합니다."
        )

    # 메자닌(임시) 자본을 포함해 A=L+E를 검증한 연도를 알립니다.
    mezzanine_years = [
        end
        for end in end_dates
        if "temporary_equity" in result["balance_sheet_check"].get(end, {})
    ]
    if mezzanine_years:
        latest_check = result["balance_sheet_check"][mezzanine_years[0]]
        notes.append(
            "A=L+E 검증: "
            + ", ".join(
                f"FY{end[:4]} {format_revenue(result['balance_sheet_check'][end]['temporary_equity'])}"
                for end in mezzanine_years
            )
            + " - 부채와 자본 사이에 별도로 보고된 메자닌 자본("
            + " + ".join(latest_check["temporary_equity_tags"])
            + ")을 포함해 Assets = Liabilities + 메자닌 자본 + Equity로 검증했습니다. "
            "메자닌 자본은 Liabilities와 Equity 어느 쪽에도 더하지 않았습니다."
        )

    # 보고 단위 반올림 범위 안의 아주 작은 차이로 PASS (rounding) 처리한 연도를 알립니다.
    rounding_years = [
        end for end in end_dates if result["balance_sheet_check"].get(end, {}).get("rounding")
    ]
    if rounding_years:
        notes.append(
            "A=L+E 검증: "
            + ", ".join(
                f"FY{end[:4]} PASS (rounding, diff "
                f"{format_amount_auto(result['balance_sheet_check'][end]['diff'])})"
                for end in rounding_years
            )
            + " - 재무제표 보고 단위 반올림으로 생길 수 있는 범위(보고 단위 "
            f"{BALANCE_ROUNDING_MAX_UNITS}단위 이내, 총자산의 "
            f"{BALANCE_ROUNDING_MAX_ASSET_RATIO * 100:g}% 이내)의 차이라 PASS로 보았습니다."
        )

    # Debt 출처 문구와 같은 작은 캡션으로 한 줄씩 표시합니다.
    for note in notes:
        st.caption(note)


def render_company(result: dict) -> dict:
    """
    한 기업의 표를 화면에 그리고, 기업 비교 요약에 쓸 최신 회계연도 값을
    정리해서 반환합니다.
    """
    st.subheader(result["company_name"])

    end_dates = [end for end, _ in result["annual_revenue"][:DISPLAY_YEARS]]
    # st.table은 20개 지표 행을 안쪽 스크롤 없이 한 번에 보여줍니다.
    st.table(build_financials_frame(result, end_dates))
    render_debt_source(result["debt_details"], end_dates)
    render_metric_notes(result, end_dates)

    # 등식검증(A=L+E)은 표에 넣지 않고, 화면에 표시된 연도 중 FAIL이 있을 때만
    # 경고로 보여줍니다(콘솔 버전과 동일한 방식).
    balance_sheet_check = result["balance_sheet_check"]
    failed_years = sorted(
        (
            (end, check)
            for end, check in balance_sheet_check.items()
            if end in set(end_dates) and not check["is_valid"]
        ),
        reverse=True,
    )
    if failed_years:
        lines = "\n".join(
            f"- FY{end[:4]} ({end}): FAIL (diff: {format_amount_auto(check['diff'])})"
            for end, check in failed_years
        )
        st.warning(f"등식검증(A=L+E) 실패 회계연도가 있습니다:\n{lines}")

    latest_end_date = result["annual_revenue"][0][0]
    return {
        "company_name": result["company_name"],
        "fiscal_year": latest_end_date[:4],
        "end_date": latest_end_date,
        "revenue_growth": format_percent(result["revenue_growth"].get(latest_end_date), show_sign=True),
        "operating_margin": format_percent(result["operating_margin"].get(latest_end_date)),
        "net_margin": format_percent(result["net_margin"].get(latest_end_date)),
        "eps_growth": format_percent(result["eps_growth"].get(latest_end_date), show_sign=True),
        "fcf_growth": format_fcf_growth(result["fcf_growth"].get(latest_end_date)),
        "fcf_margin": format_percent(result["fcf_margin"].get(latest_end_date)),
        "roe": format_roe(result["roe"].get(latest_end_date)),
        "debt_to_equity": format_debt_to_equity(result["debt_to_equity"].get(latest_end_date)),
        "debt_to_assets": format_percent(result["debt_to_assets"].get(latest_end_date)),
    }


def main():
    st.set_page_config(page_title="Stock Analyzer v0.3", layout="wide")
    load_sec_user_agent_from_secrets()
    st.title("Stock Analyzer v0.3")
    st.caption(
        "SEC EDGAR Company Facts(10-K) 기준 최근 5개 회계연도 재무지표. "
        "금액 단위는 십억 달러(B), EPS는 USD/share입니다."
    )

    with st.form("ticker_form"):
        columns = st.columns(MAX_TICKERS)
        raw_tickers = [
            columns[index].text_input(
                f"티커 {index + 1}",
                value=DEFAULT_TICKERS[index],
                key=f"ticker_{index}",
            )
            for index in range(MAX_TICKERS)
        ]
        submitted = st.form_submit_button("Analyze")

    # Analyze를 눌렀을 때만 SEC 조회/분석을 실행하고, 결과는 session_state에
    # 보관합니다(입력칸을 수정해 화면이 다시 그려져도 결과가 남아 있도록).
    if submitted:
        tickers = normalize_tickers(raw_tickers)
        if not tickers:
            st.session_state.pop("analysis", None)
            st.error("분석할 티커를 1개 이상 입력해 주세요.")
            return
        # SEC 연락처가 설정되지 않았으면 티커마다 같은 오류를 반복하지 않고 한 번만 안내합니다.
        try:
            get_sec_headers()
        except SecUserAgentMissingError as error:
            st.session_state.pop("analysis", None)
            st.error(str(error))
            return
        else:
            st.session_state["analysis"] = run_analysis(tickers)

    analysis = st.session_state.get("analysis")
    if analysis is None:
        st.info("티커를 입력하고 Analyze를 누르면 SEC 데이터를 조회합니다.")
        return

    for ticker, message in analysis["errors"]:
        st.error(f"[{ticker}] {message}")

    summaries = [render_company(result) for result in analysis["companies"]]

    if len(summaries) > 1:
        st.subheader("기업 비교 요약 (각 기업의 최신 회계연도 기준)")
        st.table(build_comparison_frame(summaries))


# Streamlit은 이 스크립트를 __main__으로 실행합니다. 이 가드를 두면 다른
# 스크립트에서 app.py의 함수만 import해서 쓸 때 화면이 그려지지 않습니다.
if __name__ == "__main__":
    main()
