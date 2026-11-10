@echo off
setlocal
set CURRENT_WORK_QUIET_DRAWER_GUI_OPT_IN=1
node --test "%~dp0tests\current-work-quiet-drawer.optin.test.mjs" "%~dp0tests\current-work-quiet-drawer-browser-e2e.test.mjs"
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
