@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-my-agent.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%MY_AGENT_NO_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
