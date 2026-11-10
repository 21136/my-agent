@echo off
setlocal
set WORK_ITEM_MERGE_SPLIT_GUI_OPT_IN=1
node --test "%~dp0tests\work-item-merge-split.optin.test.mjs" "%~dp0tests\work-item-merge-split-browser-e2e.test.mjs"
set EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %EXIT_CODE%
