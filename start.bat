@echo off
REM my-agent CLI launcher (TASKS T-210). Double-click to open the conversation REPL.
REM ---------------------------------------------------------------------------
REM DOC-08 / S-50 / T-1824-02 — Windows CLI UTF-8 strategy (see docs/DESKTOP.md §3.8.1)
REM   chcp 65001          → console code page UTF-8 (default CN Windows is CP936/GBK)
REM   PYTHONIOENCODING    → Python stdout/stderr encode as utf-8
REM   PYTHONUTF8=1        → enable UTF-8 mode (PEP 540)
REM Prefer this launcher over bare `python agent-core\main.py` to avoid GBK mojibake.
REM Sidecar/Electron spawn UTF-8 is T-1824-03 (not this bat).
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

where python >nul 2>&1
if errorlevel 1 (
    echo [my-agent] Python not found. Install Python 3.12+ and add it to PATH.
    pause
    exit /b 1
)

python "%~dp0agent-core\main.py" %*
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 pause
exit /b %EXIT_CODE%
