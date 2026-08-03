<#
.SYNOPSIS
    Launches the ClickTutor desktop overlay with the right interpreter.

.DESCRIPTION
    Three Python installations are typically present on this machine: global
    Python, the native Windows venv, and the WSL venv. Only the Windows venv
    can run the overlay, and typing the wrong one produces either a bare
    ModuleNotFoundError or a silent WSL failure. This picks the right one.

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Dev
    .\run.ps1 -Verify
#>
param(
    # Show the developer panel: demo dropdown, MP4 recorder, debug toggle.
    [switch]$Dev,
    # Run the geometry check instead of the app.
    [switch]$Verify,
    # Optional image to preload for OCR debugging.
    [string]$Image
)

$ErrorActionPreference = "Stop"

# Candidate interpreters, most preferred first. The D: location keeps Qt's
# ~100MB of DLLs on a local disk; loading them across the \\wsl$ share made
# startup roughly twelve times slower.
$candidates = @(
    "D:\venvs\clicktutor\Scripts\python.exe",
    "C:\venvs\clicktutor\Scripts\python.exe",
    (Join-Path $PSScriptRoot "venv-win\Scripts\python.exe")
)

$python = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $python) {
    Write-Host "No native Windows environment found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Looked in:"
    $candidates | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Create one with:"
    Write-Host "  py -3.11 -m venv D:\venvs\clicktutor"
    Write-Host "  D:\venvs\clicktutor\Scripts\pip install -r requirements.txt"
    exit 1
}

Write-Host "Using $python" -ForegroundColor DarkGray

if ($Verify) {
    & $python (Join-Path $PSScriptRoot "tools\verify_geometry.py")
    exit $LASTEXITCODE
}

$appArgs = @((Join-Path $PSScriptRoot "desktop.py"))
if ($Image) { $appArgs += $Image }
if ($Dev)   { $appArgs += "--dev" }

& $python $appArgs
exit $LASTEXITCODE
