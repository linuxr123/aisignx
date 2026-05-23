<#
.SYNOPSIS
    Install AISignX as a Windows Service using NSSM (https://nssm.cc/).

.DESCRIPTION
    Wraps `python -m waitress ... app:app` in a Windows Service named
    "AISignX" with stdout/stderr piped to logs/service.*.log and
    auto-start on boot.

    Requires:
      - nssm.exe on PATH (or pass -NssmPath)
      - A virtualenv at .\.venv with project dependencies installed
      - A configured .env file (or pass -SecretKey)
      - Run from an elevated PowerShell

.PARAMETER ServiceName
    Service display name. Defaults to "AISignX".

.PARAMETER InstallRoot
    Project root containing app.py and .venv. Defaults to the script's parent.

.PARAMETER Port
    TCP port for Waitress to bind. Defaults to 5000.

.PARAMETER SecretKey
    Optional. Sets AISIGNX_SECRET_KEY in the service environment. If omitted,
    the service inherits whatever is in .env.

.EXAMPLE
    .\install_windows_service.ps1
    .\install_windows_service.ps1 -Port 8080
#>

[CmdletBinding()]
param(
    [string]$ServiceName = 'AISignX',
    [string]$InstallRoot = (Split-Path $PSScriptRoot -Parent),
    [int]   $Port        = 5000,
    [string]$NssmPath    = 'nssm.exe',
    [string]$SecretKey
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command $NssmPath -ErrorAction SilentlyContinue)) {
    throw "nssm.exe not found on PATH (or at -NssmPath '$NssmPath'). Download from https://nssm.cc/."
}

$python   = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$logsDir  = Join-Path $InstallRoot 'logs'

if (-not (Test-Path $python)) {
    throw "Python not found at $python. Create a venv with 'py -3.13 -m venv .venv' first."
}
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# Stop+remove any previous install so this script is idempotent
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Stopping existing service '$ServiceName'..."
    & $NssmPath stop   $ServiceName confirm | Out-Null
    & $NssmPath remove $ServiceName confirm | Out-Null
}

Write-Host "Installing service '$ServiceName' on port $Port..."
& $NssmPath install $ServiceName $python "-m waitress --host=0.0.0.0 --port=$Port app:app"
& $NssmPath set     $ServiceName AppDirectory     $InstallRoot
& $NssmPath set     $ServiceName AppStdout        (Join-Path $logsDir 'service.out.log')
& $NssmPath set     $ServiceName AppStderr        (Join-Path $logsDir 'service.err.log')
& $NssmPath set     $ServiceName AppRotateFiles   1
& $NssmPath set     $ServiceName AppRotateBytes   10485760
& $NssmPath set     $ServiceName Start            SERVICE_AUTO_START
& $NssmPath set     $ServiceName AppRestartDelay  5000

if ($SecretKey) {
    & $NssmPath set $ServiceName AppEnvironmentExtra `
        "AISIGNX_SECRET_KEY=$SecretKey" `
        'AISIGNX_PREFERRED_URL_SCHEME=https' `
        'AISIGNX_TRUST_PROXY_HOPS=1'
}

Write-Host "Starting '$ServiceName'..."
& $NssmPath start $ServiceName

Start-Sleep -Seconds 2
Get-Service -Name $ServiceName | Format-Table -AutoSize

Write-Host "`nDone. Logs:"
Write-Host "    $logsDir\service.out.log"
Write-Host "    $logsDir\service.err.log"
Write-Host "Open: http://localhost:$Port/"
