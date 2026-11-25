<#
    Builds SentryView for Windows.

    Produces, in dist\ :
        SentryView\                              the application folder
        SentryView-<version>-Windows-portable.zip   unzip-and-run build
        SentryView-<version>-Windows-Setup.exe      installer (needs Inno Setup)

    Run from anywhere:
        powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Find-Python {
    if ($Python) { return $Python }
    foreach ($candidate in @("py -3", "python", "python3")) {
        $parts = $candidate.Split(" ")
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if ($exe) {
            $version = & $parts[0] $parts[1..($parts.Length - 1)] --version 2>&1
            if ($version -match "Python 3\.(9|1[0-9])") { return $candidate }
        }
    }
    throw "Python 3.9 or newer was not found. Install it from python.org and re-run."
}

$PyCmd = Find-Python
Write-Host "==> Using $PyCmd" -ForegroundColor Cyan

$VenvPath = Join-Path $Root ".venv-win"
if (-not (Test-Path $VenvPath)) {
    Write-Host "==> Creating build virtual environment"
    $parts = $PyCmd.Split(" ")
    & $parts[0] $parts[1..($parts.Length - 1)] -m venv $VenvPath
}
$VenvPy = Join-Path $VenvPath "Scripts\python.exe"

Write-Host "==> Installing build dependencies"
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install --upgrade PySide6 imageio-ffmpeg pyinstaller --quiet

$Version = & $VenvPy -c "import re,pathlib;print(re.search(r'__version__ = \""([^\""]+)\""', pathlib.Path('sentryview/__init__.py').read_text()).group(1))"
$Version = $Version.Trim()
Write-Host "==> Building SentryView $Version" -ForegroundColor Cyan

Write-Host "==> Generating icons"
& $VenvPy packaging\make_icon.py

Write-Host "==> Running PyInstaller"
Remove-Item -Recurse -Force (Join-Path $Root "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $Root "dist\SentryView") -ErrorAction SilentlyContinue
$env:SENTRYVIEW_ROOT = $Root
& $VenvPy -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $Root "dist") `
    --workpath (Join-Path $Root "build") `
    packaging\SentryView.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$AppDir = Join-Path $Root "dist\SentryView"
$Exe = Join-Path $AppDir "SentryView.exe"
if (-not (Test-Path $Exe)) { throw "Build produced no SentryView.exe" }

$Ffmpeg = Join-Path $AppDir "_internal\bin\ffmpeg.exe"
if (-not (Test-Path $Ffmpeg)) { $Ffmpeg = Join-Path $AppDir "bin\ffmpeg.exe" }
if (-not (Test-Path $Ffmpeg)) { throw "ffmpeg.exe is missing from the build" }
Write-Host "==> Bundled ffmpeg:"
& $Ffmpeg -hide_banner -version | Select-Object -First 1

Write-Host "==> Creating portable zip"
$Zip = Join-Path $Root "dist\SentryView-$Version-Windows-portable.zip"
Remove-Item $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path $AppDir -DestinationPath $Zip -CompressionLevel Optimal

if (-not $SkipInstaller) {
    $Iscc = $null
    foreach ($path in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $path) { $Iscc = $path; break }
    }
    if (-not $Iscc) {
        $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($cmd) { $Iscc = $cmd.Source }
    }
    if ($Iscc) {
        Write-Host "==> Building installer with Inno Setup"
        & $Iscc /Q "packaging\installer_windows.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    } else {
        Write-Warning "Inno Setup 6 not found - skipping the installer."
        Write-Warning "Install it from https://jrsoftware.org/isdl.php and re-run,"
        Write-Warning "or just ship the portable zip."
    }
}

Write-Host ""
Write-Host "==> Done" -ForegroundColor Green
Get-ChildItem (Join-Path $Root "dist") -File |
    Where-Object { $_.Name -like "SentryView-*" } |
    ForEach-Object { "    {0,-52} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB) }
