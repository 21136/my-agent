# Merge current stable branch into experiment/sandbox inside the sandbox worktree.
param(
    [string]$SandboxBranch = "experiment/sandbox"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$SandboxPath = Join-Path $RepoRoot ".worktrees\sandbox"

Set-Location $RepoRoot

if (-not (Test-Path $SandboxPath)) {
    Write-Host "[sync] Sandbox missing. Run init-sandbox.bat first." -ForegroundColor Red
    exit 1
}

$stableBranch = (& git rev-parse --abbrev-ref HEAD).Trim()
$ahead = & git rev-list --count "$SandboxBranch..$stableBranch" 2>$null
if ($LASTEXITCODE -ne 0) { $ahead = "?" }

if ($ahead -eq "0") {
    Write-Host "[sync] OK: sandbox branch already contains $stableBranch."
    exit 0
}

Write-Host "[sync] Merging $stableBranch into $SandboxBranch at $SandboxPath ($ahead commits)..."
Set-Location $SandboxPath
& git merge $stableBranch -X theirs --no-edit -m "chore(sandbox): sync with $stableBranch after stable merge"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[sync] FAIL: resolve conflicts in .worktrees\sandbox then commit." -ForegroundColor Red
    exit 1
}

Write-Host "[sync] Done. Restart start-desktop.bat so 5174 picks up the synced tree."
