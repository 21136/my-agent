[CmdletBinding()]
param(
    [string]$StableRoot = 'F:\my-agent-main',
    [string]$SandboxRoot = 'F:\my-agent-main\.worktrees\sandbox',
    [string]$OutputDir = 'F:\my-agent-main\.worktrees\sandbox\artifacts\project-local-storage-20261024'
)
$ErrorActionPreference = 'Stop'

# Native volume geometry: no WMI/Get-Volume dependency.
if (-not ('StorageNative' -as [type])) {
    $native = 'using System; using System.Runtime.InteropServices; using System.Text;' + [Environment]::NewLine +
      'public static class StorageNative {' + [Environment]::NewLine +
      '[DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool GetVolumeInformation(string root, StringBuilder name, int nameSize, out uint serial, out uint maxComponent, out uint flags, StringBuilder fs, int fsSize);' + [Environment]::NewLine +
      '[DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool GetDiskFreeSpace(string root, out uint sectorsPerCluster, out uint bytesPerSector, out uint freeClusters, out uint totalClusters); }'
    Add-Type $native
}

function Get-Category([string]$RelativePath) {
    $parts = @($RelativePath -split '[\\/]+' | Where-Object { $_ })
    if ($parts.Count -eq 0) { return 'other' }
    $first = $parts[0].ToLowerInvariant()
    if ($first -eq '.managed-runtime') { return 'other' }
    # High-level ownership wins over filename extensions and nested build names.
    if ($first -eq 'artifacts' -or $first -match '^(backup|backups|\.cache|cache|\.pytest_cache|\.ruff_cache)$') { return 'artifacts/backups/cache' }
    if ($first -in @('data','workspace')) { return 'data (metadata scan)' }
    if ($parts -contains 'node_modules') { return 'node_modules' }
    if ($parts -contains '.venv') { return '.venv' }
    if ($first -eq '.git') { return 'Git' }
    if ($parts | Where-Object { $_ -in @('dist','build','out','release','target','__pycache__','.next') }) { return 'builds' }
    if ($first -in @('agent-core','desktop','terminal-ui','evolve','scripts','tests','docs','skills','assets','src') -or $RelativePath -match '\.(py|pyi|ts|tsx|js|jsx|vue|html|css|scss|json|toml|yaml|yml|md|ps1|bat|cmd|sh|sql|txt|lock)$') { return 'source' }
    return 'other'
}

function Get-ArtifactKind([string]$RelativePath) {
    if ($RelativePath -match '(?i)(^|[\\/])(pytest|test|vitest|junit|coverage|playwright|screenshot|trace|receipt|verification)([\\/._-]|$)') { return 'test receipts / verification' }
    if ($RelativePath -match '(?i)(^|[\\/])(control-build|dist|build|compiled|release)([\\/._-]|$)') { return 'build/code payloads' }
    return 'other artifacts'
}

function New-Bucket { return [ordered]@{ Files = [long]0; LogicalBytes = [long]0; AllocationEstimateBytes = [long]0 } }
function Add-FileToBucket($Bucket, [long]$Length, [long]$Unit) {
    $Bucket.Files++
    $Bucket.LogicalBytes += $Length
    if ($Length -gt 0) { $Bucket.AllocationEstimateBytes += [long]([math]::Ceiling($Length / [double]$Unit) * $Unit) }
}

function Measure-Tree([string]$Root, [long]$Unit, [string[]]$SkipTop = @(), [string[]]$ExcludedFiles = @()) {
    $categories = [ordered]@{}
    foreach ($name in @('source','node_modules','.venv','Git','builds','data (metadata scan)','artifacts/backups/cache','other')) { $categories[$name] = New-Bucket }
    $artifactKinds = [ordered]@{}
    foreach ($name in @('test receipts / verification','build/code payloads','other artifacts')) { $artifactKinds[$name] = New-Bucket }
    $errors = [Collections.Generic.List[string]]::new()
    $excludedReceiptBytes = [long]0; $excludedReceiptFiles = 0
    $reparse = [Collections.Generic.List[string]]::new()
    $stack = [Collections.Generic.Stack[string]]::new()
    $stack.Push($Root)
    $fileCount = [long]0; $directoryCount = [long]0
    while ($stack.Count -gt 0) {
        $directory = $stack.Pop()
        try { $entries = [IO.Directory]::EnumerateFileSystemEntries($directory) }
        catch { $errors.Add("DIR $directory :: $($_.Exception.Message)"); continue }
        foreach ($entry in $entries) {
            try {
                $item = Get-Item -LiteralPath $entry -Force -ErrorAction Stop
                $relative = [IO.Path]::GetRelativePath($Root, $entry)
                if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $reparse.Add($entry); continue }
                if (-not $item.PSIsContainer -and $ExcludedFiles -contains ([IO.Path]::GetFullPath($entry))) { $excludedReceiptBytes += [long]$item.Length; $excludedReceiptFiles++; continue }
                if ($item.PSIsContainer) {
                    if ($directory -eq $Root -and $SkipTop -contains $item.Name) { continue }
                    $directoryCount++; $stack.Push($entry); continue
                }
                $length = [long]$item.Length
                Add-FileToBucket $categories[(Get-Category $relative)] $length $Unit
                if ((Split-Path -Path $entry -Parent) -match '(^|[\\/])artifacts([\\/]|$)') {
                    Add-FileToBucket $artifactKinds[(Get-ArtifactKind $relative)] $length $Unit
                }
                $fileCount++
            } catch { $errors.Add("ENTRY $entry :: $($_.Exception.Message)") }
        }
    }
    return [pscustomobject]@{ Root=$Root; Categories=$categories; ArtifactKinds=$artifactKinds; Files=$fileCount; Directories=$directoryCount; Errors=$errors; SkippedReparse=$reparse; ExcludedReceiptBytes=$excludedReceiptBytes; ExcludedReceiptFiles=$excludedReceiptFiles }
}

function Get-Totals($Scan) {
    $logical = [long]0; $allocated = [long]0
    foreach ($bucket in $Scan.Categories.Values) { $logical += $bucket.LogicalBytes; $allocated += $bucket.AllocationEstimateBytes }
    return [ordered]@{ Files=$Scan.Files; LogicalBytes=$logical; AllocationEstimateBytes=$allocated }
}
function Format-Size([long]$Bytes) { '{0:N3} GiB ({1:N1} MiB)' -f ($Bytes / 1GB), ($Bytes / 1MB) }

$stable = [IO.Path]::GetFullPath($StableRoot).TrimEnd('\')
$sandbox = [IO.Path]::GetFullPath($SandboxRoot).TrimEnd('\')
$output = [IO.Path]::GetFullPath($OutputDir).TrimEnd('\')
$reportPath = Join-Path $output 'report.md'
$jsonPath = Join-Path $output 'summary.json'
$receiptFiles = @([IO.Path]::GetFullPath($reportPath), [IO.Path]::GetFullPath($jsonPath))
if (-not (Test-Path -LiteralPath $stable -PathType Container)) { throw "Stable root missing: $stable" }
if (-not (Test-Path -LiteralPath $sandbox -PathType Container)) { throw "Sandbox root missing: $sandbox" }
if ((Test-Path -LiteralPath $reportPath) -or (Test-Path -LiteralPath $jsonPath)) { throw "Refusing to overwrite existing measurement receipts in $output" }
$drive = [IO.Path]::GetPathRoot($stable)
$volumeName = [Text.StringBuilder]::new(260); $fileSystem = [Text.StringBuilder]::new(64)
[uint32]$serial=0; [uint32]$maxComponent=0; [uint32]$flags=0
[uint32]$sectorsPerCluster=0; [uint32]$bytesPerSector=0; [uint32]$freeClusters=0; [uint32]$totalClusters=0
if (-not [StorageNative]::GetVolumeInformation($drive,$volumeName,260,[ref]$serial,[ref]$maxComponent,[ref]$flags,$fileSystem,64)) { throw "GetVolumeInformation failed (Win32 $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" }
if (-not [StorageNative]::GetDiskFreeSpace($drive,[ref]$sectorsPerCluster,[ref]$bytesPerSector,[ref]$freeClusters,[ref]$totalClusters)) { throw "GetDiskFreeSpace failed (Win32 $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" }
$unit = [long]$sectorsPerCluster * [long]$bytesPerSector
$sandboxDrive = [IO.Path]::GetPathRoot($sandbox)
if (-not [string]::Equals($drive,$sandboxDrive,[StringComparison]::OrdinalIgnoreCase)) { throw "Stable and sandbox roots are on different drives ($drive vs $sandboxDrive); refusing to apply one drive's allocation geometry to both." }
if ($unit -le 0) { throw 'Native allocation-unit size was zero.' }

# Stable intentionally omits all linked worktrees. Its .git store is counted once here.
# Sandbox scan omits only report.md and summary.json, not other measurement-directory artifacts.
$stableScan = Measure-Tree $stable $unit @('.worktrees')
$sandboxScan = Measure-Tree $sandbox $unit @() $receiptFiles
$stableTotals = Get-Totals $stableScan
$sandboxTotals = Get-Totals $sandboxScan
$combined = [ordered]@{
    Files = $stableTotals.Files + $sandboxTotals.Files
    LogicalBytes = $stableTotals.LogicalBytes + $sandboxTotals.LogicalBytes
    AllocationEstimateBytes = $stableTotals.AllocationEstimateBytes + $sandboxTotals.AllocationEstimateBytes
}

# Read only the exact generated E0 manifest; never serialize its runtime references into report.
$e0Dir = Join-Path $sandbox 'artifacts/fixed-control-e0/run-551bc58a-4bde-48e3-a0bc-325f3fd289d8/releases/a8856c257b0d63436271349648319f1d6ee4162fd55af34fe1370bc29ff34797'
$e0 = [ordered]@{ Path=$e0Dir; ManifestFound=$false; FileCount=0; DeclaredAppPayloadBytes=$null; ManifestBytes=$null; TreeFileCount=$null; TreeLogicalBytes=$null; TreeAllocationEstimateBytes=$null; Errors=@() }
if (Test-Path -LiteralPath $e0Dir -PathType Container) {
    $manifestFile = Get-ChildItem -LiteralPath $e0Dir -File -Force | Where-Object Name -Match 'manifest' | Select-Object -First 1
    if ($manifestFile) {
        try {
            $manifest = Get-Content -LiteralPath $manifestFile.FullName -Raw | ConvertFrom-Json
            $e0.ManifestFound = $true; $e0.ManifestBytes = [long]$manifestFile.Length
            $e0.FileCount = [int](@($manifest.files).Count)
            $sum = [long]0; foreach ($file in @($manifest.files)) { if ($null -ne $file.byte_length) { $sum += [long]$file.byte_length } }
            $e0.DeclaredAppPayloadBytes = $sum
            $treeLogical = [long]0; $treeAllocated = [long]0; $treeFileCount = 0
            $treeStack = [Collections.Generic.Stack[string]]::new(); $treeStack.Push($e0Dir)
            while ($treeStack.Count -gt 0) {
                $treeDir = $treeStack.Pop()
                foreach ($treeEntry in [IO.Directory]::EnumerateFileSystemEntries($treeDir)) {
                    $treeItem = Get-Item -LiteralPath $treeEntry -Force -ErrorAction Stop
                    if ($treeItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                    if ($treeItem.PSIsContainer) { $treeStack.Push($treeEntry); continue }
                    $treeFileCount++; $treeLogical += [long]$treeItem.Length
                    if ($treeItem.Length -gt 0) { $treeAllocated += [long]([math]::Ceiling($treeItem.Length/[double]$unit)*$unit) }
                }
            }            $e0.TreeFileCount=$treeFileCount; $e0.TreeLogicalBytes=$treeLogical; $e0.TreeAllocationEstimateBytes=$treeAllocated
        } catch { $e0.Errors = @($_.Exception.Message) }
    } else { $e0.Errors = @('No manifest file found in expected sealed-release directory.') }
} else { $e0.Errors = @('Expected E0 sealed-release directory missing or inaccessible.') }

$report = [Collections.Generic.List[string]]::new()
$report.Add('# Project-local storage measurement — 2026-10-24'); $report.Add('')
$report.Add("- Stable root: ``$stable`` (top-level .worktrees excluded). Sandbox root: ``$sandbox``; only exact report.md/summary.json receipt files excluded, not other measurement-directory artifacts.")
$report.Add("- Native volume: $drive; filesystem $($fileSystem.ToString()); allocation unit $unit bytes; sector $bytesPerSector bytes; free $([long]$freeClusters*$unit) bytes; total $([long]$totalClusters*$unit) bytes.")
$report.Add("- Exact measurement receipt files omitted: $($sandboxScan.ExcludedReceiptFiles) files / $($sandboxScan.ExcludedReceiptBytes) bytes (only report.md and summary.json); other output-directory artifacts remain counted.")
$report.Add('- Logical bytes are file lengths. Allocation estimate rounds every non-empty file up to one native allocation unit (including tiny files); this is not measured physical allocation. Reparse points are skipped, never followed.')
$report.Add(''); $report.Add('| Disjoint root/category | Files | Logical | Allocation estimate |'); $report.Add('|---|---:|---:|---:|')
foreach ($scan in @($stableScan,$sandboxScan)) {
    foreach ($name in $scan.Categories.Keys) { $bucket=$scan.Categories[$name]; $report.Add("| $($scan.Root) — $name | $($bucket.Files) | $(Format-Size $bucket.LogicalBytes) | $(Format-Size $bucket.AllocationEstimateBytes) |") }
    $t=if ($scan.Root -eq $stable) {$stableTotals} else {$sandboxTotals}
    $report.Add("| $($scan.Root) — TOTAL | $($t.Files) | $(Format-Size $t.LogicalBytes) | $(Format-Size $t.AllocationEstimateBytes) |")
}
$report.Add("| Combined distinct roots | $($combined.Files) | $(Format-Size $combined.LogicalBytes) | $(Format-Size $combined.AllocationEstimateBytes) |")
$report.Add(''); $report.Add('## Fixed/shared vs incremental (numeric)')
$report.Add("- Fixed stable baseline (includes shared Git object store exactly once): $($stableTotals.LogicalBytes) logical bytes ($(Format-Size $stableTotals.LogicalBytes)); allocation estimate $($stableTotals.AllocationEstimateBytes) bytes ($(Format-Size $stableTotals.AllocationEstimateBytes)).")
$report.Add("- Incremental sandbox tree: $($sandboxTotals.LogicalBytes) logical bytes ($(Format-Size $sandboxTotals.LogicalBytes)); allocation estimate $($sandboxTotals.AllocationEstimateBytes) bytes ($(Format-Size $sandboxTotals.AllocationEstimateBytes)).")
$report.Add("- Combined distinct-root cost: $($combined.LogicalBytes) logical bytes; allocation estimate $($combined.AllocationEstimateBytes) bytes. No hardlink savings assumed.")
$report.Add(''); $report.Add('## Release sizing evidence')
$report.Add('- `desktop/launcher/release.mjs` copies app payload and inventories/reference-verifies external Node/Electron/Python/python_base trees. These references are excluded from per-release duplicate cost and are not an implemented immutable shared store.')
$report.Add("- E0 sealed kit path: ``$([IO.Path]::GetRelativePath($sandbox,$e0Dir))``. Manifest file count: $($e0.FileCount); manifest-declared app payload byte_length sum: $($e0.DeclaredAppPayloadBytes) bytes; manifest file: $($e0.ManifestBytes) bytes; actual sealed tree files: $($e0.TreeFileCount); logical: $($e0.TreeLogicalBytes) bytes; allocation estimate: $($e0.TreeAllocationEstimateBytes) bytes.")
$report.Add('- E0 runtime references are deliberately omitted from duplicate-copy totals. Production per-release remains unimplemented; this observed E0 kit is an estimate, not a hard production guarantee.')
$report.Add('- Source-only release estimate uses the source category only (conservative, not exact manifest selection); data (metadata scan) is separate and excluded from source estimates. Full dependency-copy estimate adds relevant node_modules and/or .venv categories. Lockfile sizes are not installed dependency sizes.')
$report.Add(''); $report.Add('## Artifacts: repeated test receipts vs app/build code')
foreach ($kind in $sandboxScan.ArtifactKinds.Keys) { $x=$sandboxScan.ArtifactKinds[$kind]; $report.Add("- $kind — $($x.Files) files; $(Format-Size $x.LogicalBytes) logical; $(Format-Size $x.AllocationEstimateBytes) allocation estimate.") }
$report.Add(''); $report.Add('## Coverage / errors')
$report.Add("- Stable: $($stableScan.Directories) directories, $($stableScan.Files) files, $($stableScan.Errors.Count) errors, $($stableScan.SkippedReparse.Count) skipped reparse points. Sandbox: $($sandboxScan.Directories) directories, $($sandboxScan.Files) files, $($sandboxScan.Errors.Count) errors, $($sandboxScan.SkippedReparse.Count) skipped reparse points.")
$coverage = if (($stableScan.Errors.Count+$sandboxScan.Errors.Count) -eq 0) {'COMPLETE'} else {'PARTIAL — one or more paths could not be scanned; totals exclude those paths.'}
$report.Add("- Overall coverage status: $coverage")
foreach ($err in $stableScan.Errors) { $report.Add("- ERROR stable: ``$err``") }; foreach ($err in $sandboxScan.Errors) { $report.Add("- ERROR sandbox: ``$err``") }
foreach ($path in $stableScan.SkippedReparse) { $report.Add("- SKIPPED REPARSE stable: ``$path``") }; foreach ($path in $sandboxScan.SkippedReparse) { $report.Add("- SKIPPED REPARSE sandbox: ``$path``") }
foreach ($err in $e0.Errors) { $report.Add("- E0 manifest/tree note: $err") }
$report.Add(''); $report.Add('## Reproduce'); $report.Add('```powershell'); $report.Add("& '$($MyInvocation.MyCommand.Path)' -StableRoot '$stable' -SandboxRoot '$sandbox' -OutputDir '$output'"); $report.Add('```'); $report.Add('')
$report.Add('The tree scan uses metadata only and opens no file contents. Release sizing reads only the generated E0 manifest and the stated release implementation source; it does not inspect credential stores, real keys/config, user chat contents, or other data contents.')

$summary = [ordered]@{
    measuredAt = (Get-Date).ToString('o')
    volume = [ordered]@{ root=$drive; fileSystem=$fileSystem.ToString(); allocationUnitBytes=$unit; bytesPerSector=$bytesPerSector; freeBytes=[long]$freeClusters*$unit; totalBytes=[long]$totalClusters*$unit }
    roots = [ordered]@{
        stable = [ordered]@{ path=$stable; worktreesExcluded=$true; totals=$stableTotals; categories=$stableScan.Categories }
        sandbox = [ordered]@{ path=$sandbox; excludedReceiptFiles=$receiptFiles; excludedReceiptBytes=$sandboxScan.ExcludedReceiptBytes; totals=$sandboxTotals; categories=$sandboxScan.Categories }
    }
    combinedDistinctRoots = $combined
    fixedShared = $stableTotals
    incrementalSandbox = $sandboxTotals
    e0SealedKit = $e0
    artifactKinds = $sandboxScan.ArtifactKinds
    coverage = [ordered]@{ status=$coverage; stableErrors=@($stableScan.Errors); sandboxErrors=@($sandboxScan.Errors); stableSkippedReparse=@($stableScan.SkippedReparse); sandboxSkippedReparse=@($sandboxScan.SkippedReparse) }
    assumptions = @('No reparse traversal','Stable excludes top-level .worktrees','Only report.md and summary.json receipt files are excluded from sandbox scan','Allocation estimate is per-file cluster rounding, not physical allocation','Runtime references are not per-release copies')
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
$reportPath = Join-Path $output 'report.md'; $jsonPath = Join-Path $output 'summary.json'
if ((Test-Path -LiteralPath $reportPath) -or (Test-Path -LiteralPath $jsonPath)) { throw "Refusing to overwrite an existing measurement receipt in $output" }
[IO.File]::WriteAllLines($reportPath,$report,[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($jsonPath,($summary | ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
$report | Select-Object -First 30
"REPORT=$reportPath"; "JSON=$jsonPath"; "COVERAGE=$coverage"


