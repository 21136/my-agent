@echo off

REM my-agent Terminal (Ink UI by default · TERMINAL-MODE §6.6)
REM Legacy prompt_toolkit bottom TUI: set MY_AGENT_TERMINAL_UI=legacy
REM Disable Ink on Windows only: set MY_AGENT_TERMINAL_INK_WINDOWS=0
REM Native scrollback (legacy): set MY_AGENT_TERMINAL_LAYOUT=scroll

REM - Double-click or run from any cwd (Claude-style: agent uses your shell cwd).
REM - Auto re-opens inside Windows Terminal when available (WT_SESSION is set there).

setlocal EnableExtensions

pushd "%~dp0"

chcp 65001 >nul 2>&1

set "PYTHONIOENCODING=utf-8"

set "PYTHONUTF8=1"

set "MY_AGENT_PYTHON=python"

if exist "%~dp0.venv\Scripts\python.exe" set "MY_AGENT_PYTHON=%~dp0.venv\Scripts\python.exe"

if not defined MY_AGENT_TERMINAL_UI set "MY_AGENT_TERMINAL_UI=ink"

if not defined MY_AGENT_TERMINAL_LAYOUT set "MY_AGENT_TERMINAL_LAYOUT=bottom"

REM Dev default: run terminal-ui TypeScript source (tsx), not stale dist/*.js.
REM Force compiled bundle: set MY_AGENT_TERMINAL_USE_DIST=1

if not exist "%~dp0terminal-ui\node_modules\.bin\tsx.cmd" (
    echo [my-agent] terminal-ui deps missing — running npm install...
    pushd "%~dp0terminal-ui"
    call npm install
    if errorlevel 1 (
        echo [my-agent] npm install failed. Install Node.js 20+ and retry.
        popd
        popd
        pause
        exit /b 1
    )
    popd
    echo.
)

REM FILE-GUARD: background watcher for session/source truncation
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\file-sentinel\start-sentinel.ps1" >nul 2>&1

if "%MY_AGENT_PYTHON%"=="python" where python >nul 2>&1

if "%MY_AGENT_PYTHON%"=="python" if errorlevel 1 (

    echo [my-agent] Python not found. Install Python 3.12+ and add it to PATH.

    popd

    pause

    exit /b 1

)

REM Preflight: surface interface-lock conflicts before WT hand-off (avoids silent flash-close).
"%MY_AGENT_PYTHON%" "%~dp0tools\check-terminal-lock.py" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [my-agent] 无法启动 Terminal：已有实例占用会话锁。
    "%MY_AGENT_PYTHON%" "%~dp0tools\check-terminal-lock.py"
    echo.
    echo 处理办法（无需结束正在使用的 python 进程）：
    echo   - 运行: "%~dp0my-agent" terminal --clear-lock
    echo   - 或手动删除: data\sessions\.interface.lock
    echo.
    popd
    pause
    exit /b 1
)

if not defined WT_SESSION (

    where wt >nul 2>&1

    if not errorlevel 1 (

        echo [my-agent] Opening Windows Terminal...

        REM Relaunch inside WT at repo root (-d sets cwd; avoid nested quotes).
        start "" wt -d "%~dp0." --title "my-agent" cmd /k call "%~f0" %*

        popd

        exit /b 0

    )

    echo [my-agent] Tip: install Windows Terminal for Claude-style input — https://aka.ms/terminal

    echo.

)

"%MY_AGENT_PYTHON%" "%~dp0my-agent" terminal %*

set "EXIT_CODE=%ERRORLEVEL%"

popd

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [my-agent] Terminal exited with code %EXIT_CODE%.
    echo.
    echo Common fixes:
    echo   - Run: my-agent terminal --clear-lock
    echo   - Or delete data\sessions\.interface.lock
    echo.
    pause
)

exit /b %EXIT_CODE%
