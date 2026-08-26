# Run Caddy for AISignX HTTPS.
# Usage (Administrator PowerShell, from server/):  .\scripts\run_caddy.ps1
# Detached (new window, writes logs\caddy.pid):  .\scripts\run_caddy.ps1 -Detached
# Hidden window:                                .\scripts\run_caddy.ps1 -Detached -Hidden

param([switch]$Detached, [switch]$Hidden)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\caddy_lib.ps1"

$CaddyExe = Find-CaddyExe
if (-not $CaddyExe) {
    throw "Caddy not found. Run: .\scripts\setup_https.ps1 -Hostname YOUR_HOST -NonInteractive"
}
if (-not (Test-Path $global:AisignxCaddyfile)) {
    throw "Missing $($global:AisignxCaddyfile) - run setup_https.ps1 first."
}

$caddyArgs = @('run', '--config', $global:AisignxCaddyfile)

if ($Detached) {
    New-Item -ItemType Directory -Force -Path $global:AisignxLogsDir | Out-Null
    $ws = if ($Hidden) { [System.Diagnostics.ProcessWindowStyle]::Hidden } else { [System.Diagnostics.ProcessWindowStyle]::Normal }
    $proc = Start-Process -FilePath $CaddyExe -ArgumentList $caddyArgs -WorkingDirectory $global:AisignxServerDir `
        -WindowStyle $ws -PassThru
    $proc.Id | Set-Content -Path $global:AisignxCaddyPidFile -Encoding ASCII
    Write-Host "[OK] Caddy started (PID $($proc.Id))" -ForegroundColor Green
    Write-Host "  HTTPS: https://$(Get-PublicHostFromCaddyfile)/" -ForegroundColor Green
    exit 0
}

Write-Host "Starting Caddy: $CaddyExe" -ForegroundColor Cyan
Write-Host "Config: $($global:AisignxCaddyfile)" -ForegroundColor Gray
& $CaddyExe @caddyArgs
