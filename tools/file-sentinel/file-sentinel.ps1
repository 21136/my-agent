#Requires -Version 5.1
<#
.SYNOPSIS
  Watch configured files for truncation / empty writes and keep an audit trail.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File D:\my-agent\tools\file-sentinel\file-sentinel.ps1
#>
[CmdletBinding()]
param(
    [string]$ConfigPath,
    [switch]$Once
)

if (-not $ConfigPath) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $ConfigPath = Join-Path $scriptDir 'watch-targets.json'
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-SentinelRoot {
    Join-Path $env:USERPROFILE '.claude\sentinel'
}

function Ensure-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Read-Config([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Config not found: $Path"
    }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    return ($raw | ConvertFrom-Json)
}

function Redact-Secrets([string]$Text) {
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $redacted = $Text
    $patterns = @(
        '(?i)("(?:api[_-]?key|auth[_-]?token|token|password|secret)"\s*:\s*")([^"]*)(")',
        '(?i)(Bearer\s+)([A-Za-z0-9._\-]+)',
        '(?i)(ak-[A-Za-z0-9]+)'
    )
    foreach ($pattern in $patterns) {
        $redacted = [regex]::Replace($redacted, $pattern, {
            param($m)
            if ($m.Groups.Count -ge 4) {
                return $m.Groups[1].Value + '***REDACTED***' + $m.Groups[3].Value
            }
            if ($m.Groups.Count -ge 3) {
                return $m.Groups[1].Value + '***REDACTED***'
            }
            return '***REDACTED***'
        })
    }
    return $redacted
}

function Get-FileFingerprint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [ordered]@{
            exists = $false
            length = 0
            sha256 = $null
            lastWriteTimeUtc = $null
            preview = $null
        }
    }
    $item = Get-Item -LiteralPath $Path
    $length = [int64]$item.Length
    $preview = $null
    $sha = $null
    if ($length -gt 0) {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        $shaObj = [System.Security.Cryptography.SHA256]::Create()
        try {
            $hash = $shaObj.ComputeHash($bytes)
            $sha = ([BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
        }
        finally {
            $shaObj.Dispose()
        }
        $maxPreview = [Math]::Min($length, 4096)
        $preview = Redact-Secrets([System.Text.Encoding]::UTF8.GetString($bytes, 0, $maxPreview))
    }
    return [ordered]@{
        exists = $true
        length = $length
        sha256 = $sha
        lastWriteTimeUtc = $item.LastWriteTimeUtc.ToString('o')
        preview = $preview
    }
}

function Get-SuspectProcesses {
    $names = @(
        'claude', 'cursor', 'Code', 'cc-switch', 'ccswitch', 'OneDrive', 'Dropbox',
        'GoogleDrive', 'eset', 'MsMpEng', 'SearchIndexer', 'git'
    )
    $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $n = $_.ProcessName
        foreach ($needle in $names) {
            if ($n -like "*$needle*") { return $true }
        }
        return $false
    } | Select-Object -First 40 Id, ProcessName, Path
    return @($procs)
}

function Write-EventLog([string]$LogPath, [hashtable]$Event) {
    $line = ($Event | ConvertTo-Json -Compress -Depth 6)
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Save-Snapshot([string]$SnapshotDir, [string]$TargetPath, [hashtable]$Before, [hashtable]$After, [string]$Kind) {
    $safeName = ($TargetPath -replace '[\\:]+', '_')
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $dir = Join-Path $SnapshotDir $safeName
    Ensure-Directory $dir

    $meta = [ordered]@{
        atUtc = (Get-Date).ToUniversalTime().ToString('o')
        kind = $Kind
        path = $TargetPath
        before = $Before
        after = $After
        suspects = Get-SuspectProcesses
    }
    $metaPath = Join-Path $dir "$stamp.meta.json"
    ($meta | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $metaPath -Encoding UTF8

    if ($After.exists -and $After.length -gt 0 -and (Test-Path -LiteralPath $TargetPath)) {
        $raw = Get-Content -LiteralPath $TargetPath -Raw -Encoding UTF8
        $redacted = Redact-Secrets $raw
        $snapPath = Join-Path $dir "$stamp.content.txt"
        $redacted | Set-Content -LiteralPath $snapPath -Encoding UTF8
    }

    $files = Get-ChildItem -LiteralPath $dir -File | Sort-Object Name -Descending
    if ($files.Count -gt $script:MaxSnapshotsPerFile) {
        $files | Select-Object -Skip $script:MaxSnapshotsPerFile | Remove-Item -Force
    }
}

function Try-AutoRestore {
    param(
        [string]$TargetPath,
        [hashtable]$Before,
        [string]$RepoRoot,
        [string]$SnapshotDir,
        [string]$LogPath
    )

    if (-not $Before.exists -or $Before.length -le $script:EmptyBytesThreshold) {
        return $null
    }

    if ($TargetPath -match '[\\/]data[\\/]sessions[\\/]([^\\/]+)[\\/](meta\.json|messages\.jsonl|goal\.md)$') {
        $sid = $Matches[1]
        $leaf = $Matches[2]
        $guardPath = Join-Path $RepoRoot "data\.session-guard\$sid\latest\$leaf"
        if (Test-Path -LiteralPath $guardPath) {
            $guardSize = (Get-Item -LiteralPath $guardPath).Length
            if ($guardSize -gt $script:EmptyBytesThreshold) {
                Copy-Item -LiteralPath $guardPath -Destination $TargetPath -Force
                Write-EventLog -LogPath $LogPath -Event @{
                    atUtc = (Get-Date).ToUniversalTime().ToString('o')
                    type = 'sentinel.restore'
                    path = $TargetPath
                    source = 'session-guard'
                    bytes = $guardSize
                }
                return 'session-guard'
            }
        }
    }

    $safeName = ($TargetPath -replace '[\\:]+', '_')
    $dir = Join-Path $SnapshotDir $safeName
    if (Test-Path -LiteralPath $dir) {
        $snap = Get-ChildItem -LiteralPath $dir -Filter '*.content.txt' | Sort-Object Name -Descending | Select-Object -First 1
        if ($snap) {
            Copy-Item -LiteralPath $snap.FullName -Destination $TargetPath -Force
            Write-EventLog -LogPath $LogPath -Event @{
                atUtc = (Get-Date).ToUniversalTime().ToString('o')
                type = 'sentinel.restore'
                path = $TargetPath
                source = 'sentinel-snapshot'
                snapshot = $snap.Name
            }
            return 'sentinel-snapshot'
        }
    }

    if ($RepoRoot -and (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
        $rel = $TargetPath.Substring($RepoRoot.Length).TrimStart('\', '/')
        $git = Get-Command git -ErrorAction SilentlyContinue
        if ($git) {
            & git -C $RepoRoot checkout HEAD -- $rel 2>$null
            if (Test-Path -LiteralPath $TargetPath) {
                $len = (Get-Item -LiteralPath $TargetPath).Length
                if ($len -gt $script:EmptyBytesThreshold) {
                    Write-EventLog -LogPath $LogPath -Event @{
                        atUtc = (Get-Date).ToUniversalTime().ToString('o')
                        type = 'sentinel.restore'
                        path = $TargetPath
                        source = 'git-head'
                        bytes = $len
                    }
                    return 'git-head'
                }
            }
        }
    }
    return $null
}

function Expand-WatchDirs {
    param(
        [array]$WatchDirs,
        [string]$RepoRoot,
        [int]$MaxTotal = 2500
    )
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($entry in $WatchDirs) {
        $root = [string]$entry.root
        if (-not $root -and $entry.subdir) {
            $root = Join-Path $RepoRoot ([string]$entry.subdir)
        }
        if (-not $root) { continue }
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $patterns = @($entry.patterns | Where-Object { $_ })
        if ($patterns.Count -eq 0) { $patterns = @('*.py', '*.toml', '*.json', '*.jsonl', '*.md', '*.ts') }
        $perDirMax = [int]$entry.maxFiles
        if ($perDirMax -le 0) { $perDirMax = 800 }
        foreach ($pattern in $patterns) {
            $items = Get-ChildItem -LiteralPath $root -Filter $pattern -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object {
                    $full = $_.FullName
                    if ($full -match '[\\/]node_modules[\\/]') { return $false }
                    if ($full -match '[\\/]\.git[\\/]') { return $false }
                    if ($full -match '[\\/]dist-electron[\\/]') { return $false }
                    if ($full -match '[\\/]build[\\/]') { return $false }
                    return $true
                } |
                Select-Object -First $perDirMax
            foreach ($item in $items) {
                $found.Add($item.FullName)
                if ($found.Count -ge $MaxTotal) { return $found }
            }
        }
    }
    return $found
}

function Emit-Change {
    param(
        [string]$TargetPath,
        [hashtable]$Before,
        [hashtable]$After,
        [string]$Reason,
        [string]$LogPath,
        [string]$SnapshotDir,
        [bool]$SnapshotOnChange,
        [bool]$AutoRestore,
        [string]$RepoRoot
    )

    $severity = 'info'
    if (-not $After.exists) { $severity = 'critical' }
    elseif ($After.length -le $script:EmptyBytesThreshold) { $severity = 'critical' }
    elseif ($Before.exists -and $Before.length -gt $script:EmptyBytesThreshold -and $After.length -lt $Before.length) {
        $severity = 'warn'
    }

    $event = [ordered]@{
        atUtc = (Get-Date).ToUniversalTime().ToString('o')
        severity = $severity
        reason = $Reason
        path = $TargetPath
        before = $Before
        after = $After
        suspects = Get-SuspectProcesses
    }
    Write-EventLog -LogPath $LogPath -Event $event

  $color = switch ($severity) {
        'critical' { 'Red' }
        'warn' { 'Yellow' }
        default { 'Green' }
    }
    Write-Host ("[{0}] {1} :: {2} bytes -> {3} bytes ({4})" -f $event.atUtc, $TargetPath, $Before.length, $After.length, $Reason) -ForegroundColor $color

    if ($SnapshotOnChange) {
        Save-Snapshot -SnapshotDir $SnapshotDir -TargetPath $TargetPath -Before $Before -After $After -Kind $Reason
    }

    if ($AutoRestore -and $severity -eq 'critical') {
        $restored = Try-AutoRestore -TargetPath $TargetPath -Before $Before -RepoRoot $RepoRoot -SnapshotDir $SnapshotDir -LogPath $LogPath
        if ($restored) {
            Write-Host ("  -> auto-restored from {0}" -f $restored) -ForegroundColor Magenta
        }
    }
}

$config = Read-Config -Path $ConfigPath
$root = Get-SentinelRoot
$logPath = Join-Path $root 'events.jsonl'
$snapshotDir = Join-Path $root 'snapshots'
Ensure-Directory $root
Ensure-Directory $snapshotDir

$script:EmptyBytesThreshold = [int]$config.emptyBytesThreshold
$script:MaxSnapshotsPerFile = [int]$config.maxSnapshotsPerFile
$pollSeconds = [Math]::Max(1, [int]$config.pollSeconds)
$snapshotOnChange = [bool]$config.snapshotOnEveryChange
$autoRestore = [bool]$config.autoRestore
$repoRoot = [string]$config.repoRoot
if (-not $repoRoot) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $repoRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
}
$targets = @($config.targets | Where-Object { $_ -and ($_ -is [string]) })
foreach ($i in 0..($targets.Count - 1)) {
    $t = $targets[$i]
    if ($t -notmatch '^[A-Za-z]:[\\/]') {
        $targets[$i] = Join-Path $repoRoot $t
    }
}
if ($config.watchDirs) {
    $expanded = Expand-WatchDirs -WatchDirs @($config.watchDirs) -RepoRoot $repoRoot
    foreach ($path in $expanded) {
        if ($path -and ($targets -notcontains $path)) {
            $targets += $path
        }
    }
}

if ($targets.Count -eq 0) {
    throw 'No watch targets configured.'
}

$state = @{}
foreach ($target in $targets) {
    $state[$target] = Get-FileFingerprint -Path $target
}

$banner = [ordered]@{
    atUtc = (Get-Date).ToUniversalTime().ToString('o')
    type = 'sentinel.start'
    configPath = $ConfigPath
    pollSeconds = $pollSeconds
    targets = $targets
    initial = $state
}
Write-EventLog -LogPath $logPath -Event $banner
Write-Host "file-sentinel started. Logging to $logPath" -ForegroundColor Cyan
Write-Host ("Watching {0} file(s), poll={1}s" -f $targets.Count, $pollSeconds) -ForegroundColor Cyan

$watchers = @()
foreach ($target in $targets) {
    $parent = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $parent)) { continue }
    $watcher = New-Object System.IO.FileSystemWatcher
    $watcher.Path = $parent
    $watcher.Filter = Split-Path -Leaf $target
    $watcher.IncludeSubdirectories = $false
    $watcher.NotifyFilter = [IO.NotifyFilters]'FileName, LastWrite, Size'
    $watcher.EnableRaisingEvents = $true
    Register-ObjectEvent -InputObject $watcher -EventName Changed -SourceIdentifier ("fs-$target") -Action {
        $script:fsSignal = $true
    } | Out-Null
    Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier ("fs-create-$target") -Action {
        $script:fsSignal = $true
    } | Out-Null
    Register-ObjectEvent -InputObject $watcher -EventName Deleted -SourceIdentifier ("fs-delete-$target") -Action {
        $script:fsSignal = $true
    } | Out-Null
    Register-ObjectEvent -InputObject $watcher -EventName Renamed -SourceIdentifier ("fs-rename-$target") -Action {
        $script:fsSignal = $true
    } | Out-Null
    $watchers += $watcher
}

function Invoke-Scan([string]$Reason) {
    foreach ($target in $targets) {
        Start-Sleep -Milliseconds 120
        $after = Get-FileFingerprint -Path $target
        $before = $state[$target]
        $changed = $false
        if ($before.exists -ne $after.exists) { $changed = $true }
        elseif ($before.length -ne $after.length) { $changed = $true }
        elseif ($before.sha256 -ne $after.sha256) { $changed = $true }

        if ($changed) {
            Emit-Change -TargetPath $target -Before $before -After $after -Reason $Reason -LogPath $logPath -SnapshotDir $snapshotDir -SnapshotOnChange $snapshotOnChange -AutoRestore $autoRestore -RepoRoot $repoRoot
            $state[$target] = $after
        }
    }
}

$script:DirRefreshPolls = 0
try {
    do {
        $script:DirRefreshPolls += 1
        if ($config.watchDirs -and ($script:DirRefreshPolls % 30 -eq 0)) {
            $expanded = Expand-WatchDirs -WatchDirs @($config.watchDirs) -RepoRoot $repoRoot
            foreach ($path in $expanded) {
                if ($path -and -not $state.ContainsKey($path)) {
                    $targets += $path
                    $state[$path] = Get-FileFingerprint -Path $path
                }
            }
        }
        Invoke-Scan -Reason 'poll'
        if ($Once) { break }
        $deadline = (Get-Date).AddSeconds($pollSeconds)
        while ((Get-Date) -lt $deadline) {
            if ($script:fsSignal) {
                $script:fsSignal = $false
                Invoke-Scan -Reason 'filesystem-event'
            }
            Start-Sleep -Milliseconds 200
        }
    } while ($true)
}
finally {
    foreach ($watcher in $watchers) {
        $watcher.EnableRaisingEvents = $false
        $watcher.Dispose()
    }
    Get-EventSubscriber | Where-Object { $_.SourceIdentifier -like 'fs-*' } | Unregister-Event -Force
}
