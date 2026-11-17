<#
.SYNOPSIS
  Local, interactive kiosk helper for AiSignX Player. Run it in an ELEVATED PowerShell on the device.

.DESCRIPTION
  Two things must never be done remotely, so they live here and not in the app:
    AutoLogin  Windows signs a chosen user in at power-on (needs administrator rights and that user's password).
    Shell      The chosen user's Windows shell becomes the AiSignX Player (with a safe fallback to Explorer).
  The player app itself is not elevated and must not be.

.PARAMETER Do        AutoLogin, Shell or Both (asked if left out).
.PARAMETER User      The Windows user (default: the user who started this elevated window).
.PARAMETER ExePath   The player program (default: the per-user install in %LOCALAPPDATA%\Programs).
.PARAMETER Undo      Remove what this script set (AutoLogin: turn it off and delete the stored password).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File setup-kiosk.ps1 -Do Both
  powershell -ExecutionPolicy Bypass -File setup-kiosk.ps1 -Do Both -Undo
#>
param(
    [ValidateSet('AutoLogin', 'Shell', 'Both')][string]$Do,
    [string]$User,
    [string]$ExePath,
    [switch]$Undo
)
$ErrorActionPreference = 'Stop'

$Winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$ShellKeyPath = 'Software\Microsoft\Windows NT\CurrentVersion\Winlogon'
$Launcher = 'shell-launcher.ps1'

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-Admin)) {
    Write-Host 'This helper needs administrator rights (auto-login and other users'' settings are protected).' -ForegroundColor Red
    Write-Host 'Start PowerShell with "Run as administrator" and run it again.'
    exit 1
}

if (-not $Do) {
    Write-Host 'What do you want to set up?'
    Write-Host '  1  Auto-login (Windows signs the kiosk user in at power-on)'
    Write-Host '  2  Shell (the player replaces the desktop for that user)'
    Write-Host '  3  Both'
    $c = Read-Host 'Choose 1, 2 or 3'
    $Do = @{ '1' = 'AutoLogin'; '2' = 'Shell'; '3' = 'Both' }[$c]
    if (-not $Do) { Write-Host 'Nothing chosen.'; exit 1 }
}
if (-not $User) { $User = Read-Host "Windows user for the kiosk [$env:USERNAME]"; if (-not $User) { $User = $env:USERNAME } }

# Resolve the user to a SID and a profile folder.
try {
    $nt = New-Object Security.Principal.NTAccount($User)
    $sid = $nt.Translate([Security.Principal.SecurityIdentifier]).Value
} catch { Write-Host "Unknown user '$User'." -ForegroundColor Red; exit 1 }
$userProfile = (Get-ItemProperty -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid" -ErrorAction SilentlyContinue).ProfileImagePath
if (-not $userProfile -or -not (Test-Path -LiteralPath $userProfile)) {
    Write-Host "'$User' has never signed in (no profile yet). Sign in once as that user, then run this again." -ForegroundColor Red
    exit 1
}
$short = ($User -split '\\')[-1]
$domain = if ($User -match '\\') { ($User -split '\\')[0] } else { $env:COMPUTERNAME }

function Set-AutoLogin {
    if ($Undo) {
        Set-ItemProperty -LiteralPath $Winlogon -Name AutoAdminLogon -Value '0' -Type String
        Remove-ItemProperty -LiteralPath $Winlogon -Name DefaultPassword -ErrorAction SilentlyContinue
        Write-Host 'Auto-login is off and the stored password is deleted.' -ForegroundColor Green
        return
    }
    Write-Host ''
    Write-Host 'WARNING' -ForegroundColor Yellow
    Write-Host 'Windows keeps the password in the REGISTRY (HKLM ... Winlogon\DefaultPassword) in PLAIN TEXT.'
    Write-Host 'Anyone who can read the registry on this PC (any administrator, or anyone with the disk) can read it.'
    Write-Host 'Use a dedicated, low-privilege local account that is used for nothing else, and reuse no password.'
    Write-Host 'Undo any time with -Undo.'
    if ((Read-Host 'Type YES to continue') -ne 'YES') { Write-Host 'Cancelled.'; exit 1 }
    $secure = Read-Host "Password for $short (not shown)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if (-not $plain) { Write-Host 'An empty password is not supported.' -ForegroundColor Red; exit 1 }
    Set-ItemProperty -LiteralPath $Winlogon -Name DefaultUserName -Value $short -Type String
    Set-ItemProperty -LiteralPath $Winlogon -Name DefaultDomainName -Value $domain -Type String
    Set-ItemProperty -LiteralPath $Winlogon -Name DefaultPassword -Value $plain -Type String
    Set-ItemProperty -LiteralPath $Winlogon -Name AutoAdminLogon -Value '1' -Type String
    $plain = $null
    Write-Host "Auto-login is on for $domain\$short." -ForegroundColor Green
}

# Runs $Action with the user's registry hive available as 'Registry::HKEY_USERS\<name>'.
function With-UserHive([scriptblock]$Action) {
    $loaded = Test-Path -LiteralPath "Registry::HKEY_USERS\$sid"
    if ($loaded) { & $Action $sid; return }
    $name = 'AiSignXKiosk'
    $dat = Join-Path $userProfile 'NTUSER.DAT'
    & reg.exe load "HKU\$name" $dat | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Cannot load the hive of $User (is that user signed in somewhere else?)." }
    try { & $Action $name }
    finally {
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        & reg.exe unload "HKU\$name" | Out-Null
    }
}

function Set-Shell {
    $agentDir = Join-Path $userProfile 'AppData\Local\AiSignX\agent'
    $target = Join-Path $agentDir $Launcher
    $ours = { param($v) $v -and $v -match 'powershell\.exe' -and $v.ToLower().Contains($Launcher) }
    With-UserHive {
        param($hive)
        $key = "Registry::HKEY_USERS\$hive\$ShellKeyPath"
        if ($Undo) {
            $cur = (Get-ItemProperty -LiteralPath $key -Name Shell -ErrorAction SilentlyContinue).Shell
            if (-not $cur) { Write-Host 'No shell is set for this user (already normal).' }
            elseif (-not (& $ours $cur)) { Write-Host "The shell of this user is not ours ($cur); left alone." -ForegroundColor Yellow }
            else { Remove-ItemProperty -LiteralPath $key -Name Shell; Write-Host 'The normal Windows desktop is back for this user (next sign-in).' -ForegroundColor Green }
            return
        }
        if (-not $ExePath) { $ExePath = Join-Path $userProfile 'AppData\Local\Programs\AiSignX Player\AiSignX Player.exe' }
        if (-not (Test-Path -LiteralPath $ExePath)) { throw "The player is not at '$ExePath'. Install it first (or pass -ExePath)." }
        $src = Join-Path $PSScriptRoot $Launcher
        if (-not (Test-Path -LiteralPath $src)) { throw "$Launcher must sit next to this script." }
        New-Item -ItemType Directory -Force -Path $agentDir | Out-Null
        Copy-Item -LiteralPath $src -Destination $target -Force
        (@{ exe = $ExePath; args = @() } | ConvertTo-Json -Compress) | Set-Content -LiteralPath (Join-Path $agentDir 'shell.json') -Encoding UTF8
        if (-not (Test-Path -LiteralPath $key)) { New-Item -Path $key -Force | Out-Null }
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $value = '"' + $ps + '" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $target + '"'
        Set-ItemProperty -LiteralPath $key -Name Shell -Value $value -Type String
        Write-Host "The player is the shell of $User from the next sign-in." -ForegroundColor Green
        Write-Host 'Safety net: if the player quits on purpose, or exits at once 3 times, Explorer starts instead.'
        Write-Host 'Undo: run this script with -Do Shell -Undo, or choose "Normal app" in the web admin.'
    }
}

if ($Do -eq 'AutoLogin' -or $Do -eq 'Both') { Set-AutoLogin }
if ($Do -eq 'Shell' -or $Do -eq 'Both') { Set-Shell }
