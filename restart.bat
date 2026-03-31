@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo === kiro-ngx deploy ===

:: Step 1: optional git pull
if "%~1"=="--pull" (
    echo [1/3] Pulling latest code...
    git fetch origin
    git pull --ff-only 2>nul || echo   ff-only failed, local changes exist, skipping pull
) else (
    echo [1/3] Skipping code pull (pass --pull to enable)
)

:: Step 2: venv
if not exist "venv\Scripts\activate.bat" (
    echo [2/3] Creating virtual environment...
    if exist venv rmdir /s /q venv
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Is Python 3.10+ installed?
        goto :eof
    )
) else (
    echo [2/3] Virtual environment exists
)
call venv\Scripts\activate.bat
pip install -q -r requirements.txt

:: Step 3: start service
echo [3/3] Starting service...
set "TARGET_PORT=8991"
if defined PORT set "TARGET_PORT=%PORT%"
set "PID_FILE=.kiro.pid"

:: Read old PID from file
set "OLD_PID="
if exist "%PID_FILE%" (
    set /p OLD_PID=<"%PID_FILE%"
)

:: If no PID file, find by listening port
if "!OLD_PID!"=="" (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":!TARGET_PORT! " ^| findstr "LISTENING"') do (
        set "OLD_PID=%%a"
    )
)

:: Kill old process
if not "!OLD_PID!"=="" (
    echo Stopping old process (PID: !OLD_PID!)...
    taskkill /PID !OLD_PID! /F >nul 2>&1
    :: Wait up to 5s for process to exit
    set "_wait=0"
    :wait_loop
    if !_wait! GEQ 10 goto :stopped
    tasklist /FI "PID eq !OLD_PID!" 2>nul | findstr "!OLD_PID!" >nul 2>&1
    if errorlevel 1 goto :stopped
    timeout /t 1 /nobreak >nul
    set /a _wait+=1
    goto :wait_loop
)
:stopped

:: Launch in background
start "" /b cmd /c "venv\Scripts\python main.py > kiro.log 2>&1"

:: Wait and capture new PID
timeout /t 2 /nobreak >nul
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":!TARGET_PORT! " ^| findstr "LISTENING"') do (
    echo %%a> "%PID_FILE%"
    echo Service started (PID: %%a), log: kiro.log
    goto :healthcheck
)
echo Service started, log: kiro.log

:healthcheck
:: Health check: wait for port
set "_hc=0"
:hc_loop
if !_hc! GEQ 10 goto :hc_timeout
timeout /t 1 /nobreak >nul
netstat -ano 2>nul | findstr ":!TARGET_PORT! " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo Port !TARGET_PORT! is ready
    goto :done
)
set /a _hc+=1
goto :hc_loop

:hc_timeout
echo WARNING: Port !TARGET_PORT! not ready after 10s, check kiro.log

:done
endlocal
