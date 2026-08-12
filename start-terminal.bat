@echo off

REM my-agent Terminal (Ink UI by default · TERMINAL-MODE §6.6)
REM Legacy prompt_toolkit bottom TUI: set MY_AGENT_TERMINAL_UI=legacy
REM Disable Ink on Windows only: set MY_AGENT_TERMINAL_INK_WINDOWS=0
REM Native scrollback (legacy): set MY_AGENT_TERMINAL_LAYOUT=scroll

REM - Double-click or run from any cwd (Claude-style: agent uses your shell cwd).
REM - Auto re-opens inside Windows Terminal when available (WT_SESSION is set there).

setlocal EnableExtensions

chcp 65001 >nul 2>&1

set "PYTHONIOENCODING=utf-8"

set "PYTHONUTF8=1"

if not defined MY_AGENT_TERMINAL_UI set "MY_AGENT_TERMINAL_UI=ink"

if not defined MY_AGENT_TERMINAL_LAYOUT set "MY_AGENT_TERMINAL_LAYOUT=bottom"

where python >nul 2>&1

if errorlevel 1 (

    echo [my-agent] Python not found. Install Python 3.12+ and add it to PATH.

    pause

    exit /b 1

)



if not defined WT_SESSION (

    where wt >nul 2>&1

    if not errorlevel 1 (

        echo [my-agent] Opening Windows Terminal...

        REM Relaunch self inside WT. Do NOT pass internal markers — only user args (%*).

        start "" wt -d "%CD%" --title "my-agent" cmd /k call "%~f0" %*

        exit /b 0

    )

    echo [my-agent] Tip: install Windows Terminal for Claude-style input — https://aka.ms/terminal

    echo.

)



python "%~dp0my-agent" terminal %*

set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [my-agent] Terminal exited with code %EXIT_CODE%.
    echo If session is locked, close other Terminal windows or delete data\sessions\.interface.lock
    pause
)

exit /b %EXIT_CODE%
