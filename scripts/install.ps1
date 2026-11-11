<#
.SYNOPSIS
    Install AiSignX on Windows: Python environment, database, ffmpeg and a web browser.

.DESCRIPTION
    Safe to run again; it only installs what is missing.
    Uses winget (built into Windows 10 1809+ and Windows 11) for ffmpeg and the browser.

.PARAMETER Yes
    Don't ask before installing missing programs.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
param([switch]$Yes)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root 'server'
$Venv = Join-Path $Server '.venv'

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

function Confirm-Install($what) {
    if ($Yes) { return $true }
    $answer = Read-Host "    $what is missing. Install it now? [Y/n]"
    return ($answer -eq '' -or $answer -match '^[Yy]')
}

function Update-SessionPath {
    # Pick up PATH changes made by installers without opening a new window.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Install-WithWinget($id, $what) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Warn "winget is not available. Please install $what manually."
        return $false
    }
    if (-not (Confirm-Install $what)) { return $false }
    winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    return $LASTEXITCODE -eq 0
}

function Find-Browser {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    return $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

function Find-Python {
    foreach ($cmd in @('py -3', 'python', 'python3')) {
        $parts = $cmd.Split(' ')
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        # No quotes in the code: Windows PowerShell 5.1 mangles quotes passed to native programs.
        $probeArgs = @($parts | Select-Object -Skip 1) + @('-c', 'import sys; print(*sys.version_info[:2], sep=chr(46))')
        try { $version = & $exe.Source @probeArgs 2>$null } catch { continue }
        if ($version -and [version]$version -ge [version]'3.10') { return @($exe.Source) + @($parts | Select-Object -Skip 1) }
    }
    return $null
}

# --- Python -----------------------------------------------------------------
Write-Step 'Checking Python 3.10+'
$python = Find-Python
if (-not $python) {
    if (Install-WithWinget 'Python.Python.3.12' 'Python 3.12') { $python = Find-Python }
}
if (-not $python) { throw 'Python 3.10 or newer is required. Install it from https://www.python.org/ and run this script again.' }
Write-Ok "Using $($python -join ' ')"

# --- ffmpeg -----------------------------------------------------------------
Write-Step 'Checking ffmpeg (video length and thumbnails)'
if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Ok 'ffmpeg found'
} elseif (Install-WithWinget 'Gyan.FFmpeg' 'ffmpeg') {
    Write-Ok 'ffmpeg installed'
} else {
    Write-Warn 'Continuing without ffmpeg. Videos will upload without length or thumbnails.'
}

# --- Browser ----------------------------------------------------------------
Write-Step 'Checking web browser (web page previews)'
$browser = Find-Browser
if ($browser) {
    Write-Ok "Found $browser"
} elseif (Install-WithWinget 'Microsoft.Edge' 'Microsoft Edge') {
    Write-Ok 'Microsoft Edge installed'
} else {
    Write-Warn 'Continuing without a browser. Web pages will get title cards instead of screenshots.'
}

# --- Python environment -----------------------------------------------------
Write-Step 'Setting up the Python environment'
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    $pyExe = $python[0]; $pyArgs = @($python | Select-Object -Skip 1) + @('-m', 'venv', $Venv)
    & $pyExe @pyArgs
}
$vpy = Join-Path $Venv 'Scripts\python.exe'
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r (Join-Path $Server 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing Python packages failed.' }
Write-Ok 'Python packages installed'

# --- Database ---------------------------------------------------------------
Write-Step 'Creating / updating the database'
Push-Location $Server
try {
    & $vpy -m flask --app wsgi db upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }

    Write-Step 'Final check'
    & $vpy -m flask --app wsgi check
    $checkOk = $LASTEXITCODE -eq 0
} finally {
    Pop-Location
}

Write-Host ''
if ($checkOk) {
    Write-Host 'AiSignX is installed.' -ForegroundColor Green
} else {
    Write-Host 'AiSignX is installed, but some optional programs are missing (see above).' -ForegroundColor Yellow
}
Write-Host "Start it with:  cd `"$Server`"; .venv\Scripts\flask --app wsgi run"
Write-Host 'Then open http://127.0.0.1:5000 to run the setup wizard.'
