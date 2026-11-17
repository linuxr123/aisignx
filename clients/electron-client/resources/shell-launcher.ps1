# AiSignX Player shell launcher. Windows runs this INSTEAD of the desktop (Explorer) for one user
# when the admin sets "shell mode" (HKCU\...\Winlogon\Shell). It keeps the player running and has a
# safety net so the user is never left with a blank screen:
#   - the player quits on purpose (PIN exit / Ctrl+Alt+Q)  -> Explorer starts
#   - the player exits within 30 s, 3 times in a row (crash loop) -> Explorer starts
# Winlogon ends the session when its shell exits, so this script stays alive until the end.
# Rules mirror crashLoop() in src/agent/system.js: keep the two in step.
#   shell.json  {exe, args}   written by the app (or setup-kiosk.ps1)
#   stopped     marker written by the app on a deliberate quit
#   updating    marker written by the app when an update quits it: the player comes back, so wait
#               up to 10 minutes for it instead of starting Explorer
param([string]$Dir = $PSScriptRoot)

$ErrorActionPreference = 'Stop'
$QuickMs = 30000; $QuickMax = 3

function Log($msg) {
    try { Add-Content -LiteralPath (Join-Path $Dir 'shell-launcher.log') -Value ((Get-Date -Format s) + ' ' + $msg) -Encoding UTF8 } catch {}
}

function Start-Desktop($why) {
    Log "starting Explorer ($why)"
    $explorer = Join-Path $env:SystemRoot 'explorer.exe'
    try { Start-Process -FilePath $explorer } catch { Log ('explorer failed: ' + $_.Exception.Message) }
    # Stay alive while this user's desktop runs; when it ends, the session ends. Explorer may be
    # gone for a moment (a crash and restart), so exit only after 12 misses in a row (about a minute).
    Start-Sleep -Seconds 5
    $sid = (Get-Process -Id $PID).SessionId
    $misses = 0
    while ($misses -lt 12) {
        if (Get-Process -Name explorer -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $sid }) { $misses = 0 } else { $misses++ }
        Start-Sleep -Seconds 5
    }
}

try {
    $cfgFile = Join-Path $Dir 'shell.json'
    $cfg = $null
    if (Test-Path -LiteralPath $cfgFile) { $cfg = Get-Content -LiteralPath $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json }
    if (-not $cfg -or -not $cfg.exe -or -not (Test-Path -LiteralPath $cfg.exe)) { Start-Desktop 'player not installed'; exit 0 }
    $exe = (Resolve-Path -LiteralPath $cfg.exe).Path
    $stopped = Join-Path $Dir 'stopped'
    $quick = 0

    while ($true) {
        $began = Get-Date
        $p = if ($cfg.args -and @($cfg.args).Count) {
            Start-Process -FilePath $exe -ArgumentList @($cfg.args) -WorkingDirectory (Split-Path $exe) -PassThru
        } else {
            Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -PassThru
        }
        $p.WaitForExit()
        $ms = ((Get-Date) - $began).TotalMilliseconds
        # The app may relaunch itself (restart_player): give a second instance a moment to appear.
        Start-Sleep -Seconds 3
        $name = [IO.Path]::GetFileNameWithoutExtension($exe)
        # An update quit the app and the installer is starting the new version: give it time.
        $upd = Join-Path $Dir 'updating'
        if (Test-Path -LiteralPath $upd) {
            $age = ((Get-Date) - (Get-Item -LiteralPath $upd).LastWriteTime).TotalSeconds
            if ($age -lt 600) {
                Log 'update in progress, waiting for the player'
                $until = (Get-Date).AddSeconds(600 - $age)
                while ((Test-Path -LiteralPath $upd) -and -not (Get-Process -Name $name -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $until)) { Start-Sleep -Seconds 5 }
            }
        }
        if (Get-Process -Name $name -ErrorAction SilentlyContinue) {
            while (Get-Process -Name $name -ErrorAction SilentlyContinue) { Start-Sleep -Seconds 5 }
            $quick = 0
        } elseif ($ms -lt $QuickMs) { $quick++ } else { $quick = 0 }

        if (Test-Path -LiteralPath $stopped) { Start-Desktop 'player was quit on purpose'; break }
        if ($quick -ge $QuickMax) { Start-Desktop "player exits at once ($quick times)"; break }
        Log "player ended after $([int]$ms) ms, starting it again"
    }
} catch {
    Log ('error: ' + $_.Exception.Message)
    # Never a long sleep (the script ending logs the user off): same wait-on-Explorer loop.
    Start-Desktop 'launcher error'
}
