# Startup settings for the player on this PC, changed by the AiSignX privileged agent (as SYSTEM).
# Everything comes from the environment set by the agent out of its protected config (never from the server's text):
#   AISIGNX_AUTOSTART  on | off | (empty: leave)
#   AISIGNX_MODE       app | shell | (empty: leave)
#   AISIGNX_EXE        the player's exe (autostart value / launcher config)
#   AISIGNX_SID        the kiosk account's SID (the only account whose shell is ever touched)
#   AISIGNX_LAUNCHER   path of shell-launcher.ps1 (protected program folder)
#   AISIGNX_CFGDIR     protected folder holding the launcher's shell.json
# Prints {"autostart":bool,"mode":"app|shell"}.
#   -DryRun  checks the environment exactly as a real run does, prints {"dryRun":true,"actions":[...]} and changes
#            nothing (no registry read or write). The tests use it to run the argument handling for real.
# An EMPTY environment variable reads as $null in PowerShell: every variable is cast to [string] first, so empty and
# missing mean the same thing ("leave as is"). Do not compare the raw $env: value with -notin / -eq.
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$autostart = [string]$env:AISIGNX_AUTOSTART
$mode = [string]$env:AISIGNX_MODE
$exe = [string]$env:AISIGNX_EXE
$sid = [string]$env:AISIGNX_SID
$launcher = [string]$env:AISIGNX_LAUNCHER
$cfgDir = [string]$env:AISIGNX_CFGDIR
if ($autostart -notin @('', 'on', 'off') -or $mode -notin @('', 'app', 'shell')) { throw 'bad value' }
if ($sid -notmatch '^S-1-5-21(-\d+){3,4}$') { throw 'bad sid' }
if ($autostart -eq 'on' -and -not $exe) { throw 'no player exe' }
if ($DryRun) {
    $acts = @()
    if ($autostart -eq 'on') { $acts += 'run-key:set' } elseif ($autostart -eq 'off') { $acts += 'run-key:remove' }
    if ($mode -eq 'shell') { $acts += 'shell:set' } elseif ($mode -eq 'app') { $acts += 'shell:remove-ours' }
    $d = [ordered]@{ dryRun = $true; autostart = $autostart; mode = $mode; actions = @($acts) }
    [Console]::Out.Write(($d | ConvertTo-Json -Compress))
    exit 0
}
$runKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
$runName = 'AiSignXPlayer'
$sub = 'Software\Microsoft\Windows NT\CurrentVersion\Winlogon'

function Get-UserShell {
    $loaded = Test-Path -LiteralPath "Registry::HKEY_USERS\$sid"
    $prof = (Get-ItemProperty -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid" -ErrorAction Stop).ProfileImagePath
    if (-not $loaded) {
        & reg.exe load "HKU\$sid" "$prof\NTUSER.DAT" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'cannot load the profile' }
    }
    return @{ Loaded = $loaded; Profile = $prof }
}
function Release-UserHive($info) {
    if (-not $info.Loaded) {
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        & reg.exe unload "HKU\$sid" | Out-Null
    }
}

if ($autostart -eq 'on') {
    if (-not $exe) { throw 'no player exe' }
    Set-ItemProperty -LiteralPath $runKey -Name $runName -Value ('"' + $exe + '"') -Type String
} elseif ($autostart -eq 'off') {
    Remove-ItemProperty -LiteralPath $runKey -Name $runName -ErrorAction SilentlyContinue
}

$info = Get-UserShell
try {
    $key = "Registry::HKEY_USERS\$sid\$sub"
    $cur = $null
    if (Test-Path -LiteralPath $key) { $cur = (Get-ItemProperty -LiteralPath $key -Name Shell -ErrorAction SilentlyContinue).Shell }
    if ($mode -eq 'shell') {
        if (-not (Test-Path -LiteralPath $key)) { New-Item -Path $key -Force | Out-Null }
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $markers = Join-Path $info.Profile 'AppData\Local\AiSignX\agent'
        $value = '"' + $ps + '" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $launcher + '" -Dir "' + $markers + '" -ConfigDir "' + $cfgDir + '"'
        Set-ItemProperty -LiteralPath $key -Name Shell -Value $value -Type String
        $cur = $value
    } elseif ($mode -eq 'app') {
        # Only a shell we set is removed; somebody else's choice stays.
        if ($cur -and $cur -match 'powershell\.exe' -and $cur.ToLowerInvariant().Contains('shell-launcher.ps1')) {
            Remove-ItemProperty -LiteralPath $key -Name Shell -ErrorAction Stop
            $cur = $null
        }
    }
    $isShell = [bool]($cur -and $cur.ToLowerInvariant().Contains('shell-launcher.ps1'))
} finally { Release-UserHive $info }
$auto = $null -ne (Get-ItemProperty -LiteralPath $runKey -Name $runName -ErrorAction SilentlyContinue).$runName
$o = [ordered]@{ autostart = $auto; mode = $(if ($isShell) { 'shell' } else { 'app' }) }
[Console]::Out.Write(($o | ConvertTo-Json -Compress))
