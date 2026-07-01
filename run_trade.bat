@echo off
REM ============================================================
REM  Ashish Capital — Trade Runner
REM  Runs trade.py with credentials loaded from .env.local
REM  Called every 15 minutes by Windows Task Scheduler
REM  during market hours (9:15 AM - 3:30 PM IST, Mon-Fri)
REM ============================================================

REM Change to the repo directory
cd /d "C:\Users\HP\OneDrive\Desktop\Ashish\stock-scanner"

REM Create logs directory if it doesn't exist
if not exist "logs" mkdir logs

REM Load credentials from .env.local file
if not exist ".env.local" (
    echo ERROR: .env.local file not found.
    echo Please create it from .env.local.template
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do (
    if not "%%a"=="" (
        set "%%a=%%b"
    )
)

REM Log the run
echo [%DATE% %TIME%] Starting trade cycle >> logs\trade_runner.log

REM Run trade.py
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe trade.py >> logs\trade_runner.log 2>&1
) else (
    python trade.py >> logs\trade_runner.log 2>&1
)

echo [%DATE% %TIME%] Trade cycle complete >> logs\trade_runner.log
echo Done. Check logs\trade_runner.log for details.
