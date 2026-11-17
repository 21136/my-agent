# Read-only check for Windows dev taskbar branding (run after start-desktop / npm run dev setup).
# Does not modify icon cache or electron under node_modules.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$desktop = Join-Path $repoRoot "desktop"
$brandedDir = Join-Path $desktop ".branding\electron-dist"
$manifest = Join-Path $desktop ".branding\branded-electron.manifest.json"
$ico = Join-Path $desktop "build\app-icon.ico"

Write-Host "[verify] repo: $repoRoot"
if (-not (Test-Path $ico)) {
  Write-Warning "Missing $ico — run: cd desktop && node scripts/build-app-icon-ico.mjs"
  exit 2
}
if (-not (Test-Path $brandedDir)) {
  Write-Warning "Missing branded electron dist — run: node desktop/scripts/ensure-windows-branded-electron.mjs"
  exit 2
}
$exe = Get-ChildItem -Path $brandedDir -Filter "electron.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) {
  Write-Warning "No electron.exe under $brandedDir"
  exit 2
}
Write-Host "[verify] branded exe: $($exe.FullName) ($($exe.Length) bytes)"
if (Test-Path $manifest) {
  Write-Host "[verify] manifest:"
  Get-Content $manifest | Write-Host
}
$envLine = $env:ELECTRON_OVERRIDE_DIST_PATH
if ($envLine) {
  Write-Host "[verify] ELECTRON_OVERRIDE_DIST_PATH=$envLine"
} else {
  Write-Warning "ELECTRON_OVERRIDE_DIST_PATH not set in this shell — start-desktop.bat sets it for dev."
}
Write-Host @"

Next (manual visual):
1. Close any running my-agent Electron windows.
2. Run start-desktop.bat (not raw electron.exe from node_modules).
3. Taskbar should show my-agent icon (not default Electron).
4. If an old Electron icon is pinned, unpin and re-pin after first branded launch.
   Restart is not required; re-launch dev is enough unless Windows cached a pinned shortcut.

"@
exit 0