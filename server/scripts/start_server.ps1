<#
.SYNOPSIS
    Start AISignX (Waitress) and Caddy HTTPS in one step.

.DESCRIPTION
    By default, starts Waitress and Caddy in separate console windows and returns
    to the prompt so you can run stop_server.ps1 from the same shell.

    - AISignX listens on http://127.0.0.1:5000
    - Caddy serves https://HOST on port 443 (needs Administrator on Windows)

.PARAMETER Hostname
    Used to generate deploy\Caddyfile via setup_https.ps1 if missing.

.PARAMETER AppOnly
    Start only the AISignX app (no Caddy). For debugging behind an existing proxy.

.PARAMETER Foreground
    Run in this window (blocks until Ctrl+C). For debugging only.

.PARAMETER Background
    Same as default (kept for scripts). Processes run in new windows, not hidden.

.PARAMETER SkipHttpsSetup
    Do not auto-run setup_https.ps1 when Caddyfile is missing.

.EXAMPLE
    .\scripts\start_server.ps1
    .\scripts\start_server.ps1 -Hostname 192.168.1.22
    .\scripts\stop_server.ps1
    .\scripts\restart_server.ps1 -Hostname 192.168.1.22
#>

[CmdletBinding()]
param(
    [string]$Hostname = $env:AISIGNX_HOSTNAME,
    [switch]$AppOnly,
    [switch]$Foreground,
    [switch]$Background,
    [switch]$SkipHttpsSetup
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\caddy_lib.ps1"

function Write-Step($m) { Write-Host "[*] $m" -ForegroundColor Yellow }
function Write-Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }

function Ensure-Venv {
    $py = Resolve-AisignxPython
    if (-not $py) {
        throw "Virtualenv not found under $($global:AisignxServerDir) - create .venv or run install_windows.ps1 first."
    }
    return $py
}

function Ensure-Config {
    $cfg = Join-Path $global:AisignxServerDir 'config.py'
    if (Test-Path $cfg) { return }
    $hostForCfg = if ($Hostname) { $Hostname } else { 'localhost' }
    Write-Step "Creating config.py for https hostname $hostForCfg ..."
    & (Ensure-Venv) (Join-Path $global:AisignxServerDir 'generate_config.py') '--mode', 'https', '--hostname', $hostForCfg | Out-Host
}

function Ensure-Waitress {
    $py = Ensure-Venv
    $prevEa = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $py -m pip install -q waitress 2>&1 | Out-Null
        & $py -c 'import waitress' 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Step 'Installing waitress...'
            & $py -m pip install waitress 2>&1 | Out-Host
        }
    } finally {
        $ErrorActionPreference = $prevEa
    }
}

function Ensure-Caddyfile {
    if ($AppOnly) { return }
    $hostArg = $Hostname
    if (-not $hostArg) { $hostArg = $env:AISIGNX_HOSTNAME }
    if (Test-Path $global:AisignxCaddyfile) {
        if (-not $hostArg) { return }
        if (Test-CaddyfileMatchesHost $hostArg) { return }
        $current = Get-PublicHostFromCaddyfile
        Write-Warn "Caddyfile site '$current' does not match -Hostname $hostArg; regenerating..."
    } elseif (-not $hostArg) {
        if ($SkipHttpsSetup) {
            throw "Missing $($global:AisignxCaddyfile). Run: .\scripts\setup_https.ps1 -Hostname YOUR_HOST"
        }
        $hostArg = 'localhost'
    }
    if ($SkipHttpsSetup) {
        throw "Caddyfile must be updated for host $hostArg. Run: .\scripts\setup_https.ps1 -Hostname $hostArg"
    }
    Write-Step "Writing Caddyfile for host $hostArg ..."
    & (Join-Path $PScriptRoot 'setup_https.ps1') -Hostname $hostArg -NonInteractive
}

function Start-AisignxApp {
    param(
        [bool]$Detached,
        [System.Diagnostics.ProcessWindowStyle]$WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Normal
    )
    Stop-FromPidFile $global:AisignxAppPidFile 'AISignX'
    New-Item -ItemType Directory -Force -Path $global:AisignxLogsDir | Out-Null
    $py = Ensure-Venv
    $waitressArgs = @('-m', 'waitress', '--host=127.0.0.1', '--port=5000', 'app:app')
    if ($Detached) {
        # No log redirect — Waitress output stays in its own console window.
        $proc = Start-Process -FilePath $py -ArgumentList $waitressArgs -WorkingDirectory $global:AisignxServerDir `
            -WindowStyle $WindowStyle -PassThru
        $proc.Id | Set-Content -Path $global:AisignxAppPidFile -Encoding ASCII
        Start-Sleep -Seconds 2
        Write-Ok "AISignX started in a new window (PID $($proc.Id)) at http://127.0.0.1:5000"
        return $proc
    }
    return Start-Process -FilePath $py -ArgumentList $waitressArgs -WorkingDirectory $global:AisignxServerDir `
        -PassThru -NoNewWindow
}

function Start-CaddyProcess {
    param(
        [bool]$Detached,
        [System.Diagnostics.ProcessWindowStyle]$WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Normal
    )
    $caddy = Find-CaddyExe
    if (-not $caddy) {
        throw 'Caddy not found. Run: .\scripts\setup_https.ps1 -Hostname YOUR_HOST -NonInteractive'
    }
    Stop-FromPidFile $global:AisignxCaddyPidFile 'Caddy'
    $caddyArgs = @('run', '--config', $global:AisignxCaddyfile)
    if ($Detached) {
        $proc = Start-Process -FilePath $caddy -ArgumentList $caddyArgs -WorkingDirectory $global:AisignxServerDir `
            -WindowStyle $WindowStyle -PassThru
        $proc.Id | Set-Content -Path $global:AisignxCaddyPidFile -Encoding ASCII
        Write-Ok "Caddy started in a new window (PID $($proc.Id))"
        return $proc
    }
    return $caddy
}

function Test-AppHealthy {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/static/sw.js' -UseBasicParsing -TimeoutSec 8
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Start-AisignxDetached {
    param([string]$PublicUrl)

    Start-AisignxApp -Detached $true
    if (-not (Test-AppHealthy)) {
        Write-Warn 'AISignX may still be starting - check the AISignX console window'
    } else {
        Write-Ok 'AISignX is responding on http://127.0.0.1:5000'
    }

    if ($AppOnly) {
        Write-Host ''
        Write-Ok 'App-only mode (AISignX window is open).'
        Write-Host '  Local admin: http://127.0.0.1:5000/'
        Write-Host '  Stop:        .\scripts\stop_server.ps1'
        Write-Host ''
        return
    }

    if (-not (Test-IsAdmin)) {
        Write-Warn 'Approve UAC to start Caddy on port 443 in a new elevated window...'
        Start-ElevatedCaddy -Detached
    } else {
        Start-CaddyProcess -Detached $true
    }

    Write-Host ''
    Write-Ok 'AISignX is running in separate windows.'
    Write-Host "  Admin UI: $PublicUrl"
    Write-Host '  Stop:     .\scripts\stop_server.ps1  (from this or any shell)'
    Write-Host '  Restart:  .\scripts\restart_server.ps1'
    Write-Host ''
}

function Start-AisignxForeground {
    param([string]$PublicUrl)

    $appProc = Start-AisignxApp -Detached $true
    if (-not (Test-AppHealthy)) {
        Write-Warn 'AISignX may still be starting - check the AISignX console window'
    } else {
        Write-Ok 'AISignX is responding on http://127.0.0.1:5000'
    }

    if ($AppOnly) {
        Write-Host ''
        Write-Host 'App-only foreground mode. Press Ctrl+C to stop AISignX.' -ForegroundColor Gray
        try { Wait-Process -Id $appProc.Id } catch { }
        Stop-FromPidFile $global:AisignxAppPidFile 'AISignX'
        return
    }

    $caddy = Start-CaddyProcess -Detached $false
    if (-not (Test-IsAdmin)) {
        Write-Host ''
        Write-Warn 'Caddy needs port 443 - approve UAC (Caddy runs in an elevated window).'
        Write-Host "  Admin UI (after Caddy starts): $PublicUrl" -ForegroundColor Green
        Write-Host '  This window waits on AISignX. Use stop_server.ps1 to stop everything.' -ForegroundColor Gray
        Write-Host ''
        Start-ElevatedCaddy
        try {
            Wait-Process -Id $appProc.Id -ErrorAction Stop
        } catch {
            Write-Warn 'AISignX process ended unexpectedly.'
        } finally {
            Stop-FromPidFile $global:AisignxAppPidFile 'AISignX'
            Stop-FromPidFile $global:AisignxCaddyPidFile 'Caddy'
            if (Test-AisignxStopNeedsElevation) {
                Invoke-StopAisignxElevated
            } elseif (Test-PortListening -Port 443) {
                Stop-ListenersOnPort -Port 443 -ShouldStop { param($procId) Test-IsAisignxCaddyPid $procId }
            }
        }
        return
    }

    Write-Host ''
    Write-Ok "Serving HTTPS at $PublicUrl (Caddy logs in this window)"
    Write-Host 'Press Ctrl+C to stop Caddy; use stop_server.ps1 to stop AISignX too.' -ForegroundColor Gray
    Write-Host ''
    try {
        & $caddy run --config $global:AisignxCaddyfile
    } finally {
        Stop-FromPidFile $global:AisignxAppPidFile 'AISignX'
        Stop-FromPidFile $global:AisignxCaddyPidFile 'Caddy'
        if (Test-PortListening -Port 5000) {
            Stop-ListenersOnPort -Port 5000 -ShouldStop { param($procId) Test-IsAisignxPythonPid $procId }
        }
    }
}

Write-Host ''
Write-Host 'AISignX - start server' -ForegroundColor Cyan
Write-Host ''

Set-Location $global:AisignxServerDir
Ensure-Config
Ensure-Waitress
Ensure-Caddyfile

$publicHost = Get-PublicHostFromCaddyfile
if (-not $publicHost) { $publicHost = $Hostname }
if (-not $publicHost) { $publicHost = 'localhost' }
$publicUrl = "https://$publicHost/"

if ($Foreground) {
    Start-AisignxForeground -PublicUrl $publicUrl
} else {
    Start-AisignxDetached -PublicUrl $publicUrl
}
