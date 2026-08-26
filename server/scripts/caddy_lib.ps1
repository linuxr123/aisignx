# Shared Caddy paths/helpers for AISignX server scripts.
# Dot-source from scripts/:  . "$PSScriptRoot\caddy_lib.ps1"

# Use $global: so dot-sourced callers (start_server.ps1, etc.) see the same paths.
$global:AisignxServerDir = Split-Path -Parent $PSScriptRoot
$global:AisignxDeployDir   = Join-Path $global:AisignxServerDir 'deploy'
$global:AisignxCaddyfile    = Join-Path $global:AisignxDeployDir 'Caddyfile'
$global:AisignxToolsCaddy   = Join-Path $global:AisignxServerDir 'tools\caddy'
$global:AisignxPortableCaddy = Join-Path $global:AisignxToolsCaddy 'caddy.exe'
$global:AisignxLogsDir      = Join-Path $global:AisignxServerDir 'logs'
$global:AisignxAppPidFile   = Join-Path $global:AisignxLogsDir 'aisignx-app.pid'
$global:AisignxCaddyPidFile = Join-Path $global:AisignxLogsDir 'caddy.pid'

function Refresh-SessionPath {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Find-CaddyExe {
    Refresh-SessionPath
    $cmd = Get-Command caddy -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }

    $candidates = @(
        $global:AisignxPortableCaddy,
        'C:\Program Files\Caddy\caddy.exe',
        "$env:ProgramFiles\Caddy\caddy.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return (Resolve-Path $path).Path }
    }
    $wingetGlob = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $wingetGlob) {
        $found = Get-ChildItem -Path $wingetGlob -Filter 'caddy.exe' -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

function Test-AisignxInternalTlsHost {
    param([string]$Name)
    if (-not $Name) { return $false }
    if ($Name -match '^(localhost|127\.0\.0\.1|::1)$') { return $true }
    if ($Name -like '*.local') { return $true }
    if ($Name -match '^\d{1,3}(\.\d{1,3}){3}$') { return $true }
    return $false
}

function Get-PublicHostFromCaddyfile {
    if (-not (Test-Path $global:AisignxCaddyfile)) { return $null }
    foreach ($line in Get-Content -LiteralPath $global:AisignxCaddyfile -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*([^\s#\{]+)\s*\{') {
            return ($Matches[1].Trim() -split ',')[0].Trim()
        }
    }
    return $null
}

function Test-CaddyfileMatchesHost {
    param([string]$ExpectedHost)
    if (-not $ExpectedHost) { return $true }
    if (-not (Test-Path $global:AisignxCaddyfile)) { return $false }
    $current = Get-PublicHostFromCaddyfile
    if (-not $current -or ($current -ne $ExpectedHost)) { return $false }
    if (Test-AisignxInternalTlsHost $ExpectedHost) {
        $raw = Get-Content -LiteralPath $global:AisignxCaddyfile -Raw -ErrorAction SilentlyContinue
        if ($raw -notmatch '(?m)^\s*tls\s+internal\s*;?\s*$') { return $false }
    }
    return $true
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal $id
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-AisignxPython {
    foreach ($name in @('.venv', 'venv')) {
        $py = Join-Path $global:AisignxServerDir "$name\Scripts\python.exe"
        if (Test-Path $py) { return $py }
    }
    return $null
}

function Start-ElevatedCaddy {
    param([switch]$Detached, [switch]$Hidden)
    $caddyScript = Join-Path $PSScriptRoot 'run_caddy.ps1'
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $caddyScript)
    if ($Detached) { $argList += '-Detached' }
    if ($Hidden) { $argList += '-Hidden' }
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -WorkingDirectory $global:AisignxServerDir `
        -ArgumentList $argList | Out-Null
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

function Stop-FromPidFile {
    param([string]$PidFilePath, [string]$Label)
    if (-not $PidFilePath) { return }
    if (-not (Test-Path -LiteralPath $PidFilePath)) { return }
    $pidVal = (Get-Content -LiteralPath $PidFilePath -Raw -ErrorAction SilentlyContinue).Trim()
    if ($pidVal -match '^\d+$') {
        Stop-ProcessTree ([int]$pidVal)
        Write-Host "[OK] Stopped $Label (PID $pidVal)" -ForegroundColor Green
    }
    Remove-Item -LiteralPath $PidFilePath -Force -ErrorAction SilentlyContinue
}

function Test-PortListening {
    param([int]$Port)
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    } catch {
        return $false
    }
}

function Test-IsAisignxCaddyPid {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if ($proc.ProcessName -ne 'caddy') { return $false }
    $ourCaddy = Find-CaddyExe
    if ($ourCaddy -and $proc.Path -and ($proc.Path -ieq $ourCaddy)) { return $true }
    $w = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $w) { return $false }
    $cmd = ($w.CommandLine + '').ToLowerInvariant()
    if (-not $cmd) { return $false }
    $deployNorm = $global:AisignxDeployDir.ToLowerInvariant()
    $caddyfileNorm = $global:AisignxCaddyfile.ToLowerInvariant()
    return ($cmd -match [regex]::Escape($deployNorm) -or
            $cmd -match [regex]::Escape($caddyfileNorm) -or
            $cmd -match 'aisignx')
}

function Test-IsAisignxPythonPid {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.ProcessName -notmatch '^python') { return $false }
    $w = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $w) { return $false }
    $cmd = ($w.CommandLine + '').ToLowerInvariant()
    $serverNorm = $global:AisignxServerDir.ToLowerInvariant()
    return ($cmd -match 'waitress' -and $cmd -match [regex]::Escape($serverNorm))
}

function Stop-ListenersOnPort {
    param(
        [int]$Port,
        [scriptblock]$ShouldStop = $null
    )
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    } catch {
        return
    }
    $seen = @{}
    foreach ($c in $conns) {
        $ownerPid = $c.OwningProcess
        if (-not $ownerPid -or $ownerPid -le 0 -or $seen.ContainsKey($ownerPid)) { continue }
        $seen[$ownerPid] = $true
        if ($ShouldStop -and -not (& $ShouldStop $ownerPid)) { continue }
        Stop-ProcessTree $ownerPid
        Write-Host "[OK] Stopped listener on port $Port (PID $ownerPid)" -ForegroundColor Green
    }
}

function Test-AisignxStopNeedsElevation {
    if (Test-IsAdmin) { return $false }
    # Non-admin often cannot read elevated process command lines, so do not
    # require identifying Caddy first — port 443 or our pid file is enough.
    if (Test-Path -LiteralPath $global:AisignxCaddyPidFile) { return $true }
    return (Test-PortListening -Port 443)
}

function Invoke-StopAisignxElevated {
    $stopScript = Join-Path $PSScriptRoot 'stop_server.ps1'
    Write-Host ''
    Write-Host '[*] Administrator permission is required to stop Caddy (port 443).' -ForegroundColor Yellow
    Write-Host '    Click Yes on the UAC prompt...' -ForegroundColor Yellow
    Write-Host ''
    try {
        $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru `
            -ArgumentList @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', $stopScript,
                '-ElevatedPass'
            )
        if ($null -eq $proc) {
            Write-Host '[!] Elevated stop did not run (UAC may have been cancelled).' -ForegroundColor Yellow
            return $false
        }
        return ($proc.ExitCode -eq 0)
    } catch {
        Write-Host '[!] Could not elevate: UAC was cancelled or denied.' -ForegroundColor Yellow
        return $false
    }
}

function Stop-AisignxCaddyForce {
    # Elevated pass: kill our Caddy by pid file, process scan, and port 443 listeners.
    Stop-FromPidFile $global:AisignxCaddyPidFile 'Caddy'
    foreach ($proc in Get-CimInstance Win32_Process -Filter "Name='caddy.exe'" -ErrorAction SilentlyContinue) {
        if (Test-IsAisignxCaddyPid $proc.ProcessId) {
            Stop-ProcessTree $proc.ProcessId
            Write-Host "[OK] Stopped caddy.exe (PID $($proc.ProcessId))" -ForegroundColor Green
        }
    }
    Stop-ListenersOnPort -Port 443 -ShouldStop {
        param($procId)
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $p -and $p.ProcessName -eq 'caddy'
    }
}

function Stop-AisignxServer {
    param([switch]$ElevatedPass)

    Write-Host 'Stopping AISignX server processes...' -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $global:AisignxLogsDir | Out-Null

    Stop-FromPidFile $global:AisignxAppPidFile 'AISignX'
    Stop-FromPidFile $global:AisignxCaddyPidFile 'Caddy'

    $serverNorm = $global:AisignxServerDir.ToLowerInvariant()
    $caddyfileNorm = $global:AisignxCaddyfile.ToLowerInvariant()
    $deployNorm = $global:AisignxDeployDir.ToLowerInvariant()

    foreach ($proc in Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) {
        $name = $proc.Name
        $cmd = ($proc.CommandLine + '')
        $cmdLower = if ($cmd) { $cmd.ToLowerInvariant() } else { '' }
        $stop = $false
        if ($name -match '^python') {
            if ($cmdLower -and $cmdLower -match 'waitress' -and $cmdLower -match [regex]::Escape($serverNorm)) {
                $stop = $true
            } elseif (-not $cmdLower -and (Test-IsAisignxPythonPid $proc.ProcessId)) {
                $stop = $true
            }
        } elseif ($name -eq 'caddy.exe') {
            if (Test-IsAisignxCaddyPid $proc.ProcessId) { $stop = $true }
        } elseif ($name -eq 'powershell.exe' -or $name -eq 'pwsh.exe') {
            if ($cmd -match 'run_caddy\.ps1' -or $cmd -match 'start_server\.ps1') {
                $stop = $true
            }
        }
        if ($stop) {
            Stop-ProcessTree $proc.ProcessId
            Write-Host "[OK] Stopped $name (PID $($proc.ProcessId))" -ForegroundColor Green
        }
    }

    Stop-ListenersOnPort -Port 5000 -ShouldStop { param($procId) Test-IsAisignxPythonPid $procId }
    if ($ElevatedPass -or (Test-IsAdmin)) {
        Stop-AisignxCaddyForce
    } else {
        Stop-ListenersOnPort -Port 443 -ShouldStop { param($procId) Test-IsAisignxCaddyPid $procId }
    }

    Start-Sleep -Milliseconds 500

    $elevatedOk = $true
    if (-not $ElevatedPass -and (Test-AisignxStopNeedsElevation)) {
        $elevatedOk = Invoke-StopAisignxElevated
        Start-Sleep -Milliseconds 300
    }

    $left5000 = Test-PortListening -Port 5000
    $left443 = Test-PortListening -Port 443
    if ($left5000 -or $left443) {
        $ports = @()
        if ($left5000) { $ports += '5000' }
        if ($left443) { $ports += '443' }
        Write-Host "[!] Port(s) still listening: $($ports -join ', ')." -ForegroundColor Yellow
        if ($left443 -and -not (Test-IsAdmin)) {
            if (-not $elevatedOk) {
                Write-Host '    Run stop again and approve the UAC prompt, or close the Caddy console window.' -ForegroundColor Yellow
            } else {
                Write-Host '    Close the elevated Caddy/PowerShell window, then run stop again.' -ForegroundColor Yellow
            }
        } else {
            Write-Host '    Close any AISignX or Caddy console windows, then run stop again.' -ForegroundColor Yellow
        }
    } else {
        Write-Host '[OK] Stop complete (ports 5000 and 443 are free).' -ForegroundColor Green
    }
}
