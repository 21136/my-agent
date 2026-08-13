@echo off
REM my-agent desktop launcher (TASKS T-904f). Default entry — Electron + grow shell.
setlocal
cd /d "%~dp0"

REM FILE-GUARD: background watcher for session/source truncation
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\file-sentinel\start-sentinel.ps1" >nul 2>&1

where python >nul 2>&1
if errorlevel 1 (
    echo [my-agent] Python not found. Install Python 3.12+ and add it to PATH.
    pause
    exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
    echo [my-agent] npm not found. Install Node.js LTS and add it to PATH.
    pause
    exit /b 1
)

cd desktop
if not exist node_modules (
    echo [my-agent] First run: npm install in desktop/ ...
    call npm install
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

call npm run dev
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 pause
exit /b %EXIT_CODE%
