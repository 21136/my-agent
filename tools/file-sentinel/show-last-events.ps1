#Requires -Version 5.1
param(
    [int]$Count = 20
)
$logPath = Join-Path $env:USERPROFILE '.claude\sentinel\events.jsonl'
if (-not (Test-Path -LiteralPath $logPath)) {
    Write-Host "No events yet: $logPath"
    exit 0
}
Get-Content -LiteralPath $logPath -Tail $Count | ForEach-Object {
    try { $_ | ConvertFrom-Json | ConvertTo-Json -Depth 6 } catch { $_ }
}
