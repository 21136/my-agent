@echo off
REM my-agent desktop launcher (TASKS T-904f). Default entry — Electron + grow shell.
setlocal
cd /d "%~dp0"

REM Default flash model for new sessions / registry (override with your own LLM_MODEL).
if not defined LLM_MODEL set "LLM_MODEL=0x567-flash"

REM Runaway v2 controller (Phase 60). Set MY_AGENT_RUNAWAY_V2=0 to use frozen v1 path.
if not defined MY_AGENT_RUNAWAY_V2 set "MY_AGENT_RUNAWAY_V2=1"

set "MY_AGENT_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "MY_AGENT_PYTHON=%~dp0.venv\Scripts\python.exe"

REM FILE-GUARD: background watcher for session/source truncation
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\file-sentinel\start-sentinel.ps1" >nul 2>&1

if "%MY_AGENT_PYTHON%"=="python" where python >nul 2>&1
if "%MY_AGENT_PYTHON%"=="python" if errorlevel 1 (
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

REM Main shell 5173 + Electron; shell preview lane 5174 (see desktop/scripts/dev-with-preview.mjs).
REM Set MY_AGENT_SKIP_PREVIEW_DEV=1 to skip 5174.
if exist "%~dp0.worktrees\sandbox\desktop\package.json" (
    echo [my-agent] 5173 stable desktop ^| 5174 sandbox preview ^(.worktrees/sandbox^)
) else if not defined MY_AGENT_SKIP_PREVIEW_DEV (
    echo [my-agent] 5173 stable ^| 5174 preview ^(no sandbox — run init-sandbox.bat for isolated experiments^)
) else (
    echo [my-agent] Dev: main 5173 only ^(preview lane skipped^)
)
if not defined MY_AGENT_SKIP_PREVIEW_DEV (
    echo [my-agent] Skip 5174: set MY_AGENT_SKIP_PREVIEW_DEV=1
)

call npm run dev
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 pause
exit /b %EXIT_CODE%
