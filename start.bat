@echo off
REM my-agent CLI launcher (TASKS T-210). Double-click to open the conversation REPL.
setlocal
cd /d "%~dp0"

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
