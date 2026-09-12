#Requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path.TrimEnd("\", "/")
$repoRootTokens = @(
    $repoRoot.ToLowerInvariant(),
    $repoRoot.Replace("\", "/").ToLowerInvariant()
)
$protectedPids = New-Object 'System.Collections.Generic.HashSet[int]'
$candidatePids = New-Object 'System.Collections.Generic.HashSet[int]'
$processByPid = @{}

function Add-ProtectedAncestors {
    param([int]$ProcessId)

    $seen = New-Object 'System.Collections.Generic.HashSet[int]'
    $current = $ProcessId
    while ($current -gt 0 -and $seen.Add($current)) {
        [void]$protectedPids.Add($current)
        if (-not $processByPid.ContainsKey($current)) {
            break
        }
        $parent = [int]$processByPid[$current].ParentProcessId
        if ($parent -le 0 -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
}

function Get-ProcessSnapshot {
    try {
        return @(
            Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
                ForEach-Object {
                    [pscustomobject]@{
                        ProcessId = [int]$_.ProcessId
                        ParentProcessId = [int]$_.ParentProcessId
                        Name = [string]$_.Name
                        CommandLine = [string]$_.CommandLine
                        ExecutablePath = [string]$_.ExecutablePath
                        CreationTime = try { ([DateTime]$_.CreationDate).ToUniversalTime() } catch { $null }
                    }
                }
        )
    } catch {
        Write-Warning "Win32_Process query failed; using a reduced process view: $($_.Exception.Message)"
        return @(
            Get-Process -ErrorAction SilentlyContinue |
                ForEach-Object {
                    $path = ""
                    try { $path = [string]$_.Path } catch { }
                    [pscustomobject]@{
                        ProcessId = [int]$_.Id
                        ParentProcessId = 0
                        Name = "$($_.ProcessName).exe"
                        CommandLine = ""
                        ExecutablePath = $path
                        CreationTime = try { $_.StartTime.ToUniversalTime() } catch { $null }
                    }
                }
        )
    }
}

function Test-RepoProcess {
    param($Process)

    $identity = "{0}`n{1}" -f $Process.CommandLine, $Process.ExecutablePath
    $identity = $identity.ToLowerInvariant().Replace("/", "\")
    foreach ($token in $repoRootTokens) {
        $normalizedToken = $token.Replace("/", "\")
        if (
            $identity.Contains($normalizedToken + "\") -or
            $identity.Contains($normalizedToken + "/") -or
            $identity.Contains('"' + $normalizedToken + '"') -or
            $identity.Contains("'" + $normalizedToken + "'")
        ) {
            return $true
        }
    }
    return $false
}

function Test-PathUnderRepo {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $false
    }
    try {
        $normalized = [System.IO.Path]::GetFullPath($PathValue).TrimEnd("\", "/").ToLowerInvariant()
        $root = $repoRoot.TrimEnd("\", "/").ToLowerInvariant()
        return $normalized -eq $root -or $normalized.StartsWith($root + "\")
    } catch {
        return $false
    }
}

function Convert-ToUtcDateTime {
    param([object]$Value)

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $null
    }
    try {
        return ([DateTimeOffset]::Parse([string]$Value)).UtcDateTime
    } catch {
        return $null
    }
}

function Test-ProcessNearStateCreation {
    param($Process, $State)

    $stateTime = Convert-ToUtcDateTime -Value $State.created_at
    if ($null -eq $stateTime -or $null -eq $Process.CreationTime) {
        return $false
    }
    try {
        $processTime = ([DateTime]$Process.CreationTime).ToUniversalTime()
        # The worker and its PTY are spawned immediately after the state file.
        # A small window also tolerates slow process creation without accepting
        # a PID that was recycled much later.
        return [Math]::Abs(($processTime - $stateTime).TotalMinutes) -le 5
    } catch {
        return $false
    }
}

function Test-InteractiveStateProcess {
    param($Process, $State, [string]$Property)

    if ($null -eq $Process -or -not (Test-PathUnderRepo -PathValue ([string]$State.cwd_absolute))) {
        return $false
    }

    # A command line containing this checkout is authoritative. This handles
    # stale state metadata where the worker did not get a final state update.
    if (Test-RepoProcess -Process $Process) {
        return $true
    }

    $stateName = [string]$State.state
    if ($Property -eq "worker_pid") {
        if ($stateName -notin @("starting", "running")) {
            return $false
        }
    } elseif ($stateName -ne "running" -or $State.alive -ne $true) {
        return $false
    }

    # When Windows denies command-line inspection, creation time is the safe
    # fallback for a detached worker/PTY. It rejects stale or reused PIDs.
    return Test-ProcessNearStateCreation -Process $Process -State $State
}

function Add-StateProcess {
    param($State, [string]$Property, [string]$StatePath)

    $value = 0
    try { $value = [int]$State.$Property } catch { $value = 0 }
    if ($value -le 0 -or -not $processByPid.ContainsKey($value)) {
        return
    }
    $process = $processByPid[$value]
    if (Test-InteractiveStateProcess -Process $process -State $State -Property $Property) {
        Add-ProcessTreeIds -ProcessId $value
    } else {
        Write-Verbose "Ignored unverified $Property $value from $StatePath"
    }
}

function Add-ProcessTreeIds {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return
    }
    [void]$candidatePids.Add($ProcessId)
}

Write-Host "my-agent process cleanup"
Write-Host "Root: $repoRoot"
if ($DryRun) {
    Write-Host "Mode: dry run (no process will be terminated)" -ForegroundColor Yellow
}

$processes = Get-ProcessSnapshot
foreach ($process in $processes) {
    $processByPid[[int]$process.ProcessId] = $process
}
Add-ProtectedAncestors -ProcessId $PID

# Seed the set with processes whose command line or executable path names this
# checkout, then expand descendants and verified launcher parents.
foreach ($process in $processes) {
    if (([int]$process.ProcessId -ne $PID) -and (Test-RepoProcess -Process $process)) {
        Add-ProcessTreeIds -ProcessId ([int]$process.ProcessId)
    }
}

# Persistent interactive-terminal workers are detached on Windows and may no
# longer be descendants of the desktop process. Their state files are the
# authoritative PID registry for cleanup.
$interactiveRoot = Join-Path $repoRoot "data\interactive-terminals"
if (Test-Path -LiteralPath $interactiveRoot) {
    foreach ($statePath in (Get-ChildItem -LiteralPath $interactiveRoot -Filter "state.json" -File -Recurse -ErrorAction SilentlyContinue)) {
        try {
            $state = Get-Content -LiteralPath $statePath.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($property in @("worker_pid", "pid")) {
                Add-StateProcess -State $state -Property $property -StatePath $statePath.FullName
            }
        } catch {
            Write-Warning "Cannot read interactive terminal state: $($statePath.FullName)"
        }
    }
}

# Expand descendants until stable. This catches Electron's renderer, the
# Python sidecar, Vite children, shells, and commands launched by the app.
$changed = $true
while ($changed) {
    $changed = $false
    foreach ($process in $processes) {
        $pidValue = [int]$process.ProcessId
        $parentValue = [int]$process.ParentProcessId
        if ($parentValue -gt 0 -and $candidatePids.Contains($parentValue)) {
            if ($candidatePids.Add($pidValue)) {
                $changed = $true
            }
        }
    }
}

# Include project launchers above a matched sidecar, but stop at protected or
# system shells. This removes a stale npm/cmd wrapper without ever targeting
# Explorer or the shell that is running this cleanup script.
foreach ($seedPid in @($candidatePids | ForEach-Object { [int]$_ })) {
    $current = $seedPid
    $seen = New-Object 'System.Collections.Generic.HashSet[int]'
    while ($processByPid.ContainsKey($current) -and $seen.Add($current)) {
        $parent = [int]$processByPid[$current].ParentProcessId
        if ($parent -le 0 -or $parent -eq $current -or $protectedPids.Contains($parent)) {
            break
        }
        if (-not $processByPid.ContainsKey($parent)) {
            break
        }
        $parentProcess = $processByPid[$parent]
        $parentName = ([string]$parentProcess.Name).ToLowerInvariant()
        if ($parentName -in @("explorer.exe", "services.exe", "wininit.exe", "winlogon.exe", "svchost.exe", "conhost.exe")) {
            break
        }
        if (Test-RepoProcess -Process $parentProcess) {
            [void]$candidatePids.Add($parent)
            $current = $parent
            continue
        }
        break
    }
}

$killIds = @(
    $candidatePids |
        ForEach-Object { [int]$_ } |
        Where-Object { $_ -gt 0 -and -not $protectedPids.Contains($_) } |
        Sort-Object -Unique
)

if ($killIds.Count -eq 0) {
    Write-Host "No my-agent processes found."
} else {
    Write-Host ("Matched process ids: " + ($killIds -join ", "))
}

# Kill roots first with /T so detached descendants and child shells are also
# covered. A later pass handles workers whose parent was not visible.
$killIdSet = New-Object 'System.Collections.Generic.HashSet[int]'
foreach ($value in $killIds) { [void]$killIdSet.Add($value) }
$killRoots = @(
    $killIds |
        Where-Object {
            $parent = 0
            if ($processByPid.ContainsKey($_)) { $parent = [int]$processByPid[$_].ParentProcessId }
            -not $killIdSet.Contains($parent)
        }
)
if ($killRoots.Count -eq 0) { $killRoots = $killIds }

foreach ($processId in $killRoots) {
    $label = if ($processByPid.ContainsKey($processId)) {
        "{0} ({1})" -f $processId, $processByPid[$processId].Name
    } else {
        "$processId (detached worker)"
    }
    if ($DryRun) {
        Write-Host "Would terminate $label"
        continue
    }
    Write-Host "Terminating $label ..."
    try {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $processId /T /F *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  terminated" -ForegroundColor Green
        } else {
            Write-Warning "  taskkill returned exit code $LASTEXITCODE"
        }
    } catch {
        Write-Warning "  taskkill failed: $($_.Exception.Message)"
    }
}

if (-not $DryRun) {
    Start-Sleep -Milliseconds 400
}

# Remove only a stale lock. If the recorded owner is still alive, keep it and
# report it; deleting a live lock could allow two sessions to write together.
$lockPath = Join-Path $repoRoot "data\sessions\.interface.lock"
if (Test-Path -LiteralPath $lockPath) {
    $lockPid = 0
    try {
        $lock = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $lockPid = [int]$lock.pid
    } catch {
        $lockPid = 0
    }
    $lockProcessAlive = $false
    if ($lockPid -gt 0) {
        $lockProcessAlive = $null -ne (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)
    }
    if (-not $lockProcessAlive) {
        if ($DryRun) {
            Write-Host "Would remove stale interface lock: $lockPath"
        } else {
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $lockPath)) {
                Write-Host "Removed stale interface lock." -ForegroundColor Green
            } else {
                Write-Warning "Could not remove interface lock: $lockPath"
            }
        }
    } else {
        Write-Warning "Interface lock owner PID $lockPid is still alive; lock was kept."
    }
}

if (-not $DryRun) {
    $remaining = @(
        Get-ProcessSnapshot |
            Where-Object {
                ([int]$_.ProcessId -ne $PID) -and (Test-RepoProcess -Process $_)
            }
    )
    if ($remaining.Count -gt 0) {
        Write-Warning ("Matching processes still visible: " + (($remaining | ForEach-Object { "$($_.ProcessId)/$($_.Name)" }) -join ", "))
        Write-Warning "Run this script from an elevated PowerShell if one of them is access-protected."
        exit 2
    }
    Write-Host "my-agent process cleanup complete." -ForegroundColor Green
}
