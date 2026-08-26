# Restart AISignX (Waitress) + Caddy with the same options as start_server.ps1.
<#
.SYNOPSIS
    Stop then start AISignX and Caddy.

.PARAMETER Hostname
    Passed to start_server.ps1 (Caddyfile host).

.PARAMETER AppOnly
    Restart app only (no Caddy).

.PARAMETER Background
    Ignored (default start already returns to the prompt).

.PARAMETER Foreground
    Passed to start_server.ps1 (blocks in the current window).

.PARAMETER SkipHttpsSetup
    Passed to start_server.ps1.

.EXAMPLE
    .\scripts\restart_server.ps1 -Hostname 192.168.1.22
    .\scripts\restart_server.ps1 -AppOnly
#>
[CmdletBinding()]
param(
    [string]$Hostname = $env:AISIGNX_HOSTNAME,
    [switch]$AppOnly,
    [switch]$Background,
    [switch]$Foreground,
    [switch]$SkipHttpsSetup
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\caddy_lib.ps1"

Write-Host ''
Write-Host 'AISignX - restart server' -ForegroundColor Cyan
Write-Host ''

Stop-AisignxServer
Start-Sleep -Seconds 1

$startArgs = @{}
if ($Hostname) { $startArgs['Hostname'] = $Hostname }
if ($AppOnly) { $startArgs['AppOnly'] = $true }
if ($Foreground) { $startArgs['Foreground'] = $true }
if ($SkipHttpsSetup) { $startArgs['SkipHttpsSetup'] = $true }

& (Join-Path $PSScriptRoot 'start_server.ps1') @startArgs
