#Requires -Version 5.1
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$logDir = Join-Path $env:USERPROFILE '.claude\sentinel'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$outLog = Join-Path $logDir 'sentinel.stdout.log'
$errLog = Join-Path $logDir 'sentinel.stderr.log'

Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $here 'file-sentinel.ps1')
    ) `
    -WindowStyle Minimized `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog

Write-Host "file-sentinel started in background."
Write-Host "Events: $logDir\events.jsonl"
Write-Host "Stdout: $outLog"
