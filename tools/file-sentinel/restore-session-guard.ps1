#Requires -Version 5.1
<#
.SYNOPSIS
  Restore wiped session files from data/.session-guard/latest backups.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File restore-session-guard.ps1
#>
param(
    [string]$RepoRoot,
    [switch]$DryRun
)

if (-not $RepoRoot) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..\')).Path
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$guardRoot = Join-Path $RepoRoot 'data\.session-guard'
$sessionsRoot = Join-Path $RepoRoot 'data\sessions'

if (-not (Test-Path -LiteralPath $guardRoot)) {
    Write-Host "No session guard backups at $guardRoot"
    exit 0
}

$restored = 0
Get-ChildItem -LiteralPath $guardRoot -Directory | ForEach-Object {
    $sid = $_.Name
    $latest = Join-Path $_.FullName 'latest'
    if (-not (Test-Path -LiteralPath $latest)) { return }
    $destDir = Join-Path $sessionsRoot $sid
    if (-not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    foreach ($name in @('meta.json', 'messages.jsonl', 'goal.md')) {
        $src = Join-Path $latest $name
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $srcLen = (Get-Item -LiteralPath $src).Length
        if ($srcLen -le 8) { continue }
        $dest = Join-Path $destDir $name
        $destLen = 0
        if (Test-Path -LiteralPath $dest) {
            $destLen = (Get-Item -LiteralPath $dest).Length
        }
        if ($destLen -gt 8) { continue }
        if ($DryRun) {
            Write-Host "[dry-run] would restore $dest from $src ($srcLen bytes)"
        } else {
            Copy-Item -LiteralPath $src -Destination $dest -Force
            Write-Host "restored $dest ($srcLen bytes)"
        }
        $restored++
    }
}

Write-Host "done: $restored file(s)"
