# Windows packaging: rename the PyInstaller one-file build to the versioned
# asset name the in-app updater looks for (DevNotifier-<version>.exe).
#
# Usage (from repo root, after the pyinstaller build):
#   $env:APP_VERSION = "1.0.0"; pwsh packaging/windows_package.ps1
#
# This is a free, unsigned build (no code-signing certificate). Windows
# SmartScreen may warn on first launch ("Windows protected your PC" ->
# "More info" -> "Run anyway"). That is expected.

$ErrorActionPreference = "Stop"

$AppVersion = if ($env:APP_VERSION) { $env:APP_VERSION } else { "0.0.0" }
$AppName = "DevNotifier"
$DistDir = "dist"
$BuiltExe = Join-Path $DistDir "$AppName.exe"
$TargetExe = Join-Path $DistDir "$AppName-$AppVersion.exe"

Write-Host "==> Verify built .exe exists"
if (-not (Test-Path $BuiltExe)) {
    Write-Error "ERROR: $BuiltExe not found. Run: pyinstaller packaging/dev-notifier-win.spec --noconfirm"
    exit 1
}

Write-Host "==> Rename to versioned asset name"
if (Test-Path $TargetExe) {
    Remove-Item $TargetExe -Force
}
Move-Item -Path $BuiltExe -Destination $TargetExe

Write-Host "==> Done: $TargetExe"
