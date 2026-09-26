@echo off
REM Double-click this to back the database up to C:\Users\<you>\Backups.
REM It asks for the connection string rather than storing one, so no password
REM is ever written into this folder (which syncs to OneDrive).
setlocal
cd /d "%~dp0"

echo ============================================================
echo   NSE Dashboard - Database Backup
echo ============================================================
echo.
echo  You need your database connection string.
echo  Get it from: console.neon.tech
echo    - click the "NSE Dasboard" project
echo    - click "Connect" / "Connection Details"
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
echo  Backing up. The price table is large, so this takes a few minutes.
echo.

python scripts\backup_db.py
if errorlevel 1 (
  echo.
  echo  Something went wrong - copy the message above and send it to Claude.
) else (
  echo.
  echo  Done. The folder it wrote to is printed above.
)

echo.
pause
