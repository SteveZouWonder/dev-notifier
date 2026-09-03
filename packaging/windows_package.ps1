# Windows packaging: turn the PyInstaller one-file build into the two release
# assets the in-app updater and the README expect:
#
#   dist/DevNotifier-<version>-setup.exe     Inno Setup installer (recommended;
#                                            what the updater downloads + runs)
#   dist/DevNotifier-<version>-portable.exe  bare one-file exe, no install
#
# Usage (from repo root, after the pyinstaller build):
#   $env:APP_VERSION = "1.0.0"; pwsh packaging/windows_package.ps1
#
# Requires Inno Setup 6 (ISCC.exe) - preinstalled on GitHub's windows-latest
# runners; locally: winget install JRSoftware.InnoSetup. Set
# $env:DEVNOTIFIER_SKIP_INSTALLER = "1" to only produce the portable exe.
#
# This is a free, unsigned build (no code-signing certificate). Windows
# SmartScreen may warn on first launch ("Windows protected your PC" ->
# "More info" -> "Run anyway"). That is expected.

$ErrorActionPreference = "Stop"

$AppVersion = if ($env:APP_VERSION) { $env:APP_VERSION } else { "0.0.0" }
$AppName = "DevNotifier"
$DistDir = "dist"
$BuildDir = "build"
$BuiltExe = Join-Path $DistDir "$AppName.exe"
$PortableExe = Join-Path $DistDir "$AppName-$AppVersion-portable.exe"
$SetupExe = Join-Path $DistDir "$AppName-$AppVersion-setup.exe"
$IssFile = Join-Path "packaging" "windows_installer.iss"
$IconFile = Join-Path $BuildDir "app-icon.ico"

# Numeric core for the installer's VERSIONINFO (1.6.0-rc.1 -> 1.6.0).
$NumericVersion = if ($AppVersion -match '^v?(\d+\.\d+\.\d+)') { $Matches[1] } else { "0.0.0" }

Write-Host "==> Verify built .exe exists"
if (-not (Test-Path $BuiltExe)) {
    Write-Error "ERROR: $BuiltExe not found. Run: pyinstaller packaging/dev-notifier-win.spec --noconfirm"
    exit 1
}

Write-Host "==> Rename to versioned portable asset"
if (Test-Path $PortableExe) {
    Remove-Item $PortableExe -Force
}
Move-Item -Path $BuiltExe -Destination $PortableExe
Write-Host "    $PortableExe"

if ($env:DEVNOTIFIER_SKIP_INSTALLER -eq "1") {
    Write-Host "==> DEVNOTIFIER_SKIP_INSTALLER=1: skipping Inno Setup installer"
    exit 0
}

Write-Host "==> Locate Inno Setup compiler (ISCC.exe)"
$Iscc = $null
$cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($cmd) { $Iscc = $cmd.Source }
if (-not $Iscc) {
    $roots = @(${env:ProgramFiles(x86)}, $env:ProgramFiles)
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA "Programs") }
    foreach ($root in $roots) {
        if (-not $root) { continue }
        $candidate = Join-Path $root "Inno Setup 6\ISCC.exe"
        if (Test-Path $candidate) { $Iscc = $candidate; break }
    }
}
if (-not $Iscc) {
    Write-Error "ERROR: ISCC.exe (Inno Setup 6) not found. Install it (winget install JRSoftware.InnoSetup) or set DEVNOTIFIER_SKIP_INSTALLER=1."
    exit 1
}
Write-Host "    $Iscc"

Write-Host "==> Build installer with Inno Setup"
if (Test-Path $SetupExe) {
    Remove-Item $SetupExe -Force
}
# ISCC resolves relative paths against the .iss file's directory (packaging\),
# not the current directory, so hand it absolute paths.
$PortableExeAbs = (Resolve-Path $PortableExe).Path
$DistDirAbs = (Resolve-Path $DistDir).Path
$isccArgs = @(
    "/DAppVersion=$AppVersion",
    "/DAppVersionNumeric=$NumericVersion",
    "/DSourceExe=$PortableExeAbs",
    "/DOutputDir=$DistDirAbs"
)
if (Test-Path $IconFile) {
    $isccArgs += "/DAppIcon=$((Resolve-Path $IconFile).Path)"
} else {
    Write-Host "    (no $IconFile; installer uses the default icon)"
}
$isccArgs += (Resolve-Path $IssFile).Path
& $Iscc @isccArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "ERROR: ISCC failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
if (-not (Test-Path $SetupExe)) {
    Write-Error "ERROR: expected installer $SetupExe was not produced"
    exit 1
}

Write-Host "==> Done:"
Write-Host "    $SetupExe"
Write-Host "    $PortableExe"
