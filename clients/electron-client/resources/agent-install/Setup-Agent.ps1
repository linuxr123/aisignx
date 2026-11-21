<#
Installs and connects the privileged agent on this PC. Used by the setup .exe (silent: the values arrive in AISIGNX_SETUP_*
environment variables, never on a command line) and by the Start menu entry "Connect AiSignX agent" (asks for them).
There is no code to type: the agent asks the server for approval, the player tells the server which PC it is on, and an admin
approves (or ticked "approve the agent" when pairing the screen). Everything installed here came inside the signed setup .exe:
Install-Agent.ps1 (signed, with the bundle's SHA-256 inside), the agent bundle, and the public signing certificate the agent pins.
Exit codes: 0 done, 2 nothing to do (no server address), anything else: Install-Agent.ps1 failed (its message is shown).
#>
[CmdletBinding()]
param([string]$InstallDir = (Split-Path $PSScriptRoot -Parent | Split-Path -Parent))
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$player = Join-Path $InstallDir 'AiSignX Player.exe'

function Test-Admin {
    (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
# Local accounts that can run the screen: enabled, and not an administrator (the agent refuses an administrator by default).
function Get-PlayerAccounts {
    try {
        $admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop | ForEach-Object { $_.SID.Value })
        @(Get-LocalUser -ErrorAction Stop | Where-Object { $_.Enabled -and $admins -notcontains $_.SID.Value } | ForEach-Object { $_.Name })
    } catch { @() }
}
$server = $env:AISIGNX_SETUP_SERVER; $kiosk = $env:AISIGNX_SETUP_KIOSKUSER; $allow = $env:AISIGNX_SETUP_ALLOW
$silent = [bool]$server
if (-not $silent) {
    if (-not (Test-Admin)) {   # started from the Start menu: ask Windows for permission
        Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") -Verb RunAs -Wait
        exit 0
    }
    Write-Host 'Connect the AiSignX agent to this PC.' -ForegroundColor Cyan
    Write-Host 'Afterwards pair this PC''s screen on the server (Displays > Pair a display) and tick "approve the agent", or approve it on the display page.'
    $server = Read-Host 'Server address, for example https://signs.example.com or http://192.168.1.19:5000'
    $accounts = Get-PlayerAccounts
    Write-Host 'Which Windows account runs the screen? (needed only for "shell" and automatic sign-in)'
    Write-Host '  0) none: whoever signs in'
    for ($i = 0; $i -lt $accounts.Count; $i++) { Write-Host ('  {0}) {1}' -f ($i + 1), $accounts[$i]) }
    $pick = Read-Host 'Number'
    $kiosk = if ($pick -match '^\d+$' -and [int]$pick -ge 1 -and [int]$pick -le $accounts.Count) { $accounts[[int]$pick - 1] } else { '' }
    $allow = Read-Host 'What may the web page do? Words from: reboot, startup, update (empty = update)'
    if (-not $allow) { $allow = 'update' }
}
if (-not $server) { Write-Host 'No server address: the agent files are in place but it is not connected.'; exit 2 }

# Every value ends up on a command line of a privileged script: only plain characters are accepted.
if ($server -notmatch '^https?://[A-Za-z0-9._:\[\]-]{1,200}$') { throw 'The server address must look like https://host or http://192.168.1.19:5000.' }
if ($kiosk -and $kiosk -notmatch '^[A-Za-z0-9 ._\-]{1,64}$') { throw 'The Windows account name has characters that are not allowed.' }
$words = @(($allow -split '[,; ]+') | Where-Object { $_ })
foreach ($w in $words) { if ($w -notin 'reboot', 'startup', 'update') { throw "'$w' is not one of: reboot, startup, update." } }
if (-not $kiosk -and ($words -contains 'startup')) { Write-Host 'Changing how the player starts needs a Windows account: "startup" is left out.' -ForegroundColor Yellow; $words = @($words | Where-Object { $_ -ne 'startup' }) }

$zip = Get-ChildItem -LiteralPath $here -Filter 'AiSignX-Agent-*.zip' | Select-Object -First 1
if (-not $zip) { throw 'The agent bundle is missing from this installation. Run the setup .exe again.' }
$a = @{
    Server = $server; Pending = $true
    Thumbprint = ((New-Object Security.Cryptography.X509Certificates.X509Certificate2 (Join-Path $here 'signing.cer')).Thumbprint)
    CertFile = (Join-Path $here 'signing.cer'); Bundle = $zip.FullName; PlayerExe = $player
    SkipSelfCheck = $true; Yes = $true   # the files came inside the signed setup .exe, and the bundle hash is checked inside
}
if ($kiosk) { $a.KioskUser = $kiosk }
if ($words -contains 'reboot') { $a.AllowReboot = $true }
if ($words -contains 'startup') { $a.AllowStartup = $true }
if ($words -contains 'update') { $a.AllowUpdate = $true }
if ($server -like 'http://*') { $a.AllowInsecureHttp = $true }
& (Join-Path $here 'Install-Agent.ps1') @a
exit $LASTEXITCODE
