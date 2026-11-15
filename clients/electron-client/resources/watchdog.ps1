# AiSignX Player watchdog. Run every minute by a current-user Scheduled Task (no elevation).
# Restarts the player when it is not running or its heartbeat file is stale. The rules mirror
# decide() in src/agent/logic.js: keep the two in step.
#   flag.json      {enabled, exe, args}   written by the app
#   heartbeat.json {t, pid, state}        written by the app while the page is alive
#   stopped        marker: the user quit the app on purpose
#   state.json     {restarts: [ms], gave_up}   read by the app for its report
param([string]$Dir = (Join-Path $env:LOCALAPPDATA 'AiSignX\agent'))

$ErrorActionPreference = 'Stop'
$StaleMs = 90000; $GraceMs = 150000; $MaxPerHour = 6; $HourMs = 3600000

function Now-Ms { [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
function Read-Json($name) {
    $p = Join-Path $Dir $name
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    try { return (Get-Content -LiteralPath $p -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}
function Write-State($restarts, $gaveUp) {
    $obj = [ordered]@{ restarts = @($restarts); gave_up = [bool]$gaveUp }
    $tmp = Join-Path $Dir 'state.json.tmp'
    ($obj | ConvertTo-Json -Compress) | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination (Join-Path $Dir 'state.json') -Force
}
function Log($msg) {
    try { Add-Content -LiteralPath (Join-Path $Dir 'watchdog.log') -Value ((Get-Date -Format s) + ' ' + $msg) -Encoding UTF8 } catch {}
}

try {
    $flag = Read-Json 'flag.json'
    if (-not $flag -or $flag.enabled -ne $true -or -not $flag.exe) { exit 0 }
    if (Test-Path -LiteralPath (Join-Path $Dir 'stopped')) { exit 0 }
    if (-not (Test-Path -LiteralPath $flag.exe)) { exit 0 }   # uninstalled

    $now = Now-Ms
    $exe = (Resolve-Path -LiteralPath $flag.exe).Path
    $name = [IO.Path]::GetFileNameWithoutExtension($exe)
    $procs = @(Get-Process -Name $name -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe })
    $state = Read-Json 'state.json'
    $restarts = @(); if ($state -and $state.restarts) { $restarts = @($state.restarts | Where-Object { $now - $_ -lt $HourMs }) }
    $gaveUp = [bool]($state -and $state.gave_up)
    $hb = Read-Json 'heartbeat.json'

    $need = $null
    if ($procs.Count -eq 0) {
        $need = 'not running'
    } else {
        $match = $hb -and ($procs | Where-Object { $_.Id -eq $hb.pid })
        if ($match) {
            if ($now - [int64]$hb.t -gt $StaleMs) { $need = 'heartbeat stale' }
        } else {
            $newest = ($procs | ForEach-Object { ([DateTimeOffset]$_.StartTime).ToUnixTimeMilliseconds() } | Measure-Object -Maximum).Maximum
            if ($now - $newest -gt $GraceMs) { $need = 'no heartbeat' }
        }
    }

    if (-not $need) {
        if ($gaveUp) { Write-State $restarts $false }   # healthy again: the limit is lifted
        exit 0
    }
    if ($gaveUp -or $restarts.Count -ge $MaxPerHour) {
        if (-not $gaveUp) { Log "gave up: $need, $($restarts.Count) restarts in the last hour" }
        Write-State $restarts $true
        exit 0
    }

    Log "restarting ($need)"
    $restarts = @($restarts) + @($now)
    Write-State $restarts $false      # record first, so a crash here still counts
    foreach ($p in $procs) { try { Stop-Process -Id $p.Id -Force } catch {} }
    if ($procs.Count) { Start-Sleep -Seconds 2 }
    if ($flag.args) { Start-Process -FilePath $exe -ArgumentList @($flag.args) -WorkingDirectory (Split-Path $exe) }
    else { Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) }
} catch {
    Log ('error: ' + $_.Exception.Message)
    exit 1
}
