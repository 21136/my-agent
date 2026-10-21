# Pre-merge guard: stable worktree must not carry uncommitted product changes.
# Sandbox commits merge in via git; stable tree should be clean (or only unrelated paths).
param(
    [string]$StableBranch = "main",
    [string]$SandboxBranch = "experiment/sandbox",
    [switch]$AllowDirtyOutsideProduct
)

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ErrorActionPreference = "Stop"
$ProductPrefixes = @("agent-core/", "desktop/", "evolve/tools/", "evolve/skills/")

Set-Location $RepoRoot

function Fail([string]$Message) {
    Write-Host "[check] FAIL: $Message" -ForegroundColor Red
    exit 1
}

function Ok([string]$Message) {
    Write-Host "[check] OK: $Message" -ForegroundColor Green
}

function Warn([string]$Message) {
    Write-Host "[check] WARN: $Message" -ForegroundColor Yellow
}

if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    Fail "Not a git repository: $RepoRoot"
}

$sandboxPath = Join-Path $RepoRoot ".worktrees\sandbox"
if (-not (Test-Path $sandboxPath)) {
    Warn "Sandbox worktree missing ($sandboxPath). Run init-sandbox.bat first if you still need experiments."
}

function Test-GitRef([string]$BranchName): bool {
    $null = & git show-ref --verify --quiet "refs/heads/$BranchName" 2>$null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-GitRef $SandboxBranch)) {
    Fail "Branch '$SandboxBranch' not found. Run init-sandbox.bat first."
}

if (-not (Test-GitRef $StableBranch)) {
    Warn "Stable branch '$StableBranch' not found; using current HEAD for comparison."
    $StableBranch = (& git rev-parse --abbrev-ref HEAD).Trim()
}

$dirtyProduct = @()
foreach ($prefix in $ProductPrefixes) {
    $lines = & git status --porcelain -- $prefix 2>$null
    if ($lines) {
        $dirtyProduct += $lines
    }
}

if ($dirtyProduct.Count -gt 0) {
    Write-Host ""
    Write-Host "Stable worktree has uncommitted changes under product paths:" -ForegroundColor Red
    $dirtyProduct | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Before merging sandbox, either:"
    Write-Host "  - commit or stash these on stable, or"
    Write-Host "  - move experimental edits into .worktrees/sandbox only and restore stable copies"
    Write-Host ""
    Fail "Stable product tree is dirty."
}

Ok "Stable product paths are clean ($($ProductPrefixes -join ', '))."

if (-not $AllowDirtyOutsideProduct) {
    $allDirty = & git status --porcelain
    $other = $allDirty | Where-Object {
        $line = $_.Substring(3)
        -not ($ProductPrefixes | Where-Object { $line.StartsWith($_) -or $line.StartsWith($_.TrimEnd('/')) })
    }
    if ($other) {
        Warn "Other uncommitted files exist (docs/data/etc.). Review before merge:"
        $other | ForEach-Object { Write-Host "  $_" }
    }
}

$sandboxDirty = @()
if (Test-Path $sandboxPath) {
    Push-Location $sandboxPath
    foreach ($prefix in $ProductPrefixes) {
        $lines = & git status --porcelain -- $prefix 2>$null
        if ($lines) { $sandboxDirty += $lines }
    }
    Pop-Location
}

if ($sandboxDirty.Count -gt 0) {
    Write-Host ""
    Write-Host "Sandbox has uncommitted product changes — commit in worktree first:" -ForegroundColor Yellow
    $sandboxDirty | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "  cd .worktrees\sandbox"
    Write-Host "  git add -A && git commit -m \"...\""
    Fail "Sandbox not committed."
}

Ok "Sandbox product tree has no uncommitted changes."

$ahead = & git rev-list --count "$StableBranch..$SandboxBranch" 2>$null
if ($LASTEXITCODE -ne 0) { $ahead = "?" }
Write-Host ""
Write-Host "Commits on ${SandboxBranch} not in ${StableBranch}: $ahead"
& git log --oneline -10 "$StableBranch..$SandboxBranch" 2>$null
Write-Host ""
Ok "Ready to merge. From repo root:"
Write-Host "  git checkout $StableBranch"
Write-Host "  git merge $SandboxBranch"
Write-Host "Then restart start-desktop.bat and run your usual tests."
exit 0
