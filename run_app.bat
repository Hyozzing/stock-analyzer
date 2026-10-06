@echo off
rem Stock Analyzer v0.3 실행 파일 - 더블클릭하면 브라우저에 앱이 열립니다.
rem 종료하려면 이 창을 닫거나 Ctrl+C를 누르세요.
rem 처음 한 번은 "python -m pip install -r requirements.txt"로 패키지를 설치하세요.
chcp 65001 > nul
cd /d "%~dp0"
where py > nul 2> nul
if %errorlevel%==0 (
    py -m streamlit run app.py
) else (
    python -m streamlit run app.py
)
pause
