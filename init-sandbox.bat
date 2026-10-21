@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\init-sandbox.ps1"
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 pause
exit /b %EXIT_CODE%
