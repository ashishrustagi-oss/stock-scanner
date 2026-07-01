@echo off
REM ============================================================
REM  Ashish Capital — Trade Runner
REM  Runs trade.py with credentials loaded from .env.local
REM  Called every 15 minutes by Windows Task Scheduler
REM  during market hours (9:15 AM - 3:30 PM IST, Mon-Fri)
REM ============================================================

REM Change to the repo directory
cd /d "C:\Users\HP\OneDrive\Desktop\Ashish\stock-scanner"

REM Check if market is open (Task Scheduler handles the schedule,
REM but this gives an extra safety check)
for /f "tokens=1-3 delims=:." %%a in ("%TIME%") do (
    set /a HOUR=%%a
    set /a MIN=%%b
)
set /a TIMECHECK=HOUR*100+MIN

REM Skip if before 9:15 AM or after 3:30 PM
if %TIMECHECK% LSS 915 (
    echo Market not open yet. Current time: %TIME%
    exit /b 0
)
if %TIMECHECK% GTE 1530 (
    echo Market closed. Current time: %TIME%
    exit /b 0
)

REM Load credentials from .env.local file
REM (This file is never committed to git — see .gitignore)
if not exist ".env.local" (
    echo ERROR: .env.local file not found.
    echo Please create it from .env.local.template
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" (
        set "%%a=%%b"
    )
)

REM Log the run
echo [%DATE% %TIME%] Starting trade cycle >> logs\trade_runner.log

REM Run trade.py using the Python in the repo's virtual environment
REM (or system Python if no venv)
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe trade.py >> logs\trade_runner.log 2>&1
) else (
    python trade.py >> logs\trade_runner.log 2>&1
)

echo [%DATE% %TIME%] Trade cycle complete >> logs\trade_runner.log
