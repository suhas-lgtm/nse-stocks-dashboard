@echo off
REM Double-click to run the dashboard on this machine at http://localhost:8501
REM It asks for the connection string rather than storing one, so no password
REM is written into this folder (which syncs to OneDrive).
setlocal
cd /d "%~dp0"

echo ============================================================
echo   NSE Research Center - run locally
echo ============================================================
echo.
echo  You need your database connection string.
echo  Get it from: console.neon.tech
echo    - click the "NSE Dasboard" project
echo    - click "Connect"
echo    - copy the whole line starting with postgresql://
echo.
echo  To paste into this window: right-click, or press Ctrl+V.
echo.

set "DBURL="
set /p DBURL="Paste the connection string here, then press Enter: "

if "%DBURL%"=="" (
  echo.
  echo  Nothing pasted. Closing.
  echo.
  pause
  exit /b 1
)

set "DATABASE_URL=%DBURL%"

echo.
echo  Starting. When it says "You can now view your Streamlit app",
echo  open this in your browser:
echo.
echo      http://localhost:8501
echo.
echo  Leave this window open while you use the dashboard.
echo  Press Ctrl+C here to stop it.
echo.

python -m streamlit run dashboard.py --server.port 8501 --server.headless true --browser.gatherUsageStats false

echo.
pause
