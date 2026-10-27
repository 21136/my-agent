# Create isolated evolution sandbox (git worktree). Stable checkout is untouched.
param(
    [string]$Branch = "experiment/sandbox"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$SandboxPath = Join-Path $RepoRoot ".worktrees\sandbox"
$SandboxDesktop = Join-Path $SandboxPath "desktop"

Set-Location $RepoRoot

if (Test-Path $SandboxPath) {
    Write-Host "[sandbox] Already exists: $SandboxPath"
    $stableBranch = (& git rev-parse --abbrev-ref HEAD).Trim()
    $ahead = & git rev-list --count "$Branch..$stableBranch" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ahead -and [int]$ahead -gt 0) {
        Write-Host "[sandbox] WARN: stable ($stableBranch) is $ahead commit(s) ahead of $Branch."
        Write-Host "[sandbox]        After a merge into stable, run sync-sandbox-worktree.bat (or 壳预览 -> 同步 sandbox 与稳定)."
    }
    Write-Host "[sandbox] Remove with: git worktree remove .worktrees/sandbox"
    exit 0
}

$branchExists = $false
& git show-ref --verify --quiet "refs/heads/$Branch" 2>$null
if ($LASTEXITCODE -eq 0) { $branchExists = $true }

if ($branchExists) {
    Write-Host "[sandbox] Adding worktree at $SandboxPath (branch $Branch)..."
    & git worktree add $SandboxPath $Branch
} else {
    Write-Host "[sandbox] Creating branch $Branch and worktree at $SandboxPath..."
    & git worktree add -b $Branch $SandboxPath
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path $SandboxDesktop)) {
    Write-Host "[sandbox] ERROR: desktop/ missing in worktree."
    exit 1
}

Write-Host "[sandbox] npm install in sandbox/desktop..."
Set-Location $SandboxDesktop
& npm install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "[sandbox] Ready."
Write-Host "  Stable (do not use for experiments): $RepoRoot"
Write-Host "  Sandbox (Agent may change desktop + agent-core): $SandboxPath"
Write-Host "  Preview dev: restart start-desktop.bat — port 5174 serves sandbox when present."
Write-Host "  Candidate sidecar root (settings): $SandboxPath"
