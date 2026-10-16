@echo off
REM Double-click to fetch mutual fund holdings into the database.
REM
REM This runs from your machine on purpose. Advisorkhoj throttles datacentre
REM IPs: the same job takes ~0.2s per request here and over an hour on GitHub's
REM runners. It is also resumable, so months already stored are skipped and it
REM is safe to run again if it stops partway.
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Mutual fund holdings - fetch into the database
echo ============================================================
echo.
echo  1) Database connection string
echo     console.neon.tech  ^>  NSE Dasboard  ^>  Connect
echo     (right-click to paste)
echo.

set "DBURL="
set /p DBURL="Paste the connection string: "
if "%DBURL%"=="" (
  echo. & echo  Nothing pasted. Closing. & echo.
  pause & exit /b 1
)

echo.
echo  2) Advisorkhoj API key
echo     Press Enter on its own to use the sample key from their docs.
echo.

set "AKKEY="
set /p AKKEY="Paste the API key (or press Enter): "
if "%AKKEY%"=="" set "AKKEY=4390fdc0-ac8b-4055-a3f8-bb26d3e8419d"

echo.
echo  3) How many months of holdings?
echo     2 is enough for the month-on-month comparison. 6 gives more history.
echo     Press Enter for 2.
echo.

set "MONTHS="
set /p MONTHS="Months (or press Enter): "
if "%MONTHS%"=="" set "MONTHS=2"

set "DATABASE_URL=%DBURL%"
set "ADVISORKHOJ_KEY=%AKKEY%"

echo.
echo  Fetching %MONTHS% month(s). Roughly a minute or two per month.
echo.

python scripts\fetch_mf_holdings.py --months %MONTHS%

if errorlevel 1 (
  echo.
  echo  It stopped early. Whatever finished is already saved - run this
  echo  again later and it will skip what it has and carry on.
) else (
  echo.
  echo  Done. Refresh the dashboard and open the MF Investments tab.
)

echo.
pause
