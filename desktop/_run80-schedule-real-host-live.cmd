@echo off
setlocal
set REAL_MULTI_PROJECT_SCHEDULE_LIVE_OPT_IN=1
set REAL_MULTI_PROJECT_SCHEDULE_GUI_OPT_IN=1
node --test "%~dp0tests\multi-project-schedule-real-host-live.optin.test.mjs" "%~dp0tests\multi-project-schedule-real-host-live-browser-e2e.test.mjs"
endlocal
