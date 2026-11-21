<#
Connects this PC's AiSignX player to the privileged agent. Used by the setup .exe (silent: the values arrive in
AISIGNX_SETUP_* environment variables, never on a command line) and by the Start menu entry "Connect AiSignX agent"
(asks for them). Everything it installs came inside the signed setup .exe: Install-Agent.ps1 (signed, with the bundle's
SHA-256 inside), the agent bundle, and the public signing certificate that the agent pins.
Exit codes: 0 done, 2 nothing to do (no code given), anything else: Install-Agent.ps1 failed (its message is shown).
#>
[CmdletBinding()]
param([string]$InstallDir = (Split-Path $PSScriptRoot -Parent | Split-Path -Parent))
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$player = Join-Path $InstallDir 'AiSignX Player.exe'

function Test-Admin {
    (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
$server = $env:AISIGNX_SETUP_SERVER; $code = $env:AISIGNX_SETUP_CODE; $key = $env:AISIGNX_SETUP_KEY
$kiosk = $env:AISIGNX_SETUP_KIOSKUSER; $allow = $env:AISIGNX_SETUP_ALLOW
$silent = [bool]$code
if (-not $silent) {
    if (-not (Test-Admin)) {   # started from the Start menu: ask Windows for permission
        Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") -Verb RunAs -Wait
        exit 0
    }
    Write-Host 'Connect the AiSignX agent to this PC.' -ForegroundColor Cyan
    Write-Host 'In the admin: Displays > this screen > Device agent > Enrollment code (the screen must be paired first).'
    $server = Read-Host 'Server address, for example https://signs.example.com or http://192.168.1.19:5000'
    $code = Read-Host 'Enrollment code (ABCD-EFGH-JKLM-NPQR)'
    $key = Read-Host 'Server key fingerprint shown next to the code'
    $kiosk = Read-Host 'Windows account the player runs as (empty = none: no startup changes)'
    $allow = Read-Host 'What may the web page do? Words from: reboot, startup, update (empty = update)'
    if (-not $allow) { $allow = 'update' }
}
if (-not $code) { Write-Host 'No enrollment code: the agent files are in place but it is not connected.'; exit 2 }

# Every value ends up on a command line of a privileged script: only plain characters are accepted.
if ($server -notmatch '^https?://[A-Za-z0-9._:\[\]-]{1,200}$') { throw 'The server address must look like https://host or http://192.168.1.19:5000.' }
if ($code -notmatch '^[A-Za-z0-9-]{8,40}$') { throw 'The enrollment code has characters that are not allowed.' }
if ($key -notmatch '^[0-9A-Fa-f ]{16,80}$') { throw 'The key fingerprint has characters that are not allowed.' }
if ($kiosk -and $kiosk -notmatch '^[A-Za-z0-9 ._\\-]{1,64}$') { throw 'The Windows account name has characters that are not allowed.' }
$words = @(($allow -split '[,; ]+') | Where-Object { $_ })
foreach ($w in $words) { if ($w -notin 'reboot', 'startup', 'update') { throw "'$w' is not one of: reboot, startup, update." } }

$zip = Get-ChildItem -LiteralPath $here -Filter 'AiSignX-Agent-*.zip' | Select-Object -First 1
if (-not $zip) { throw 'The agent bundle is missing from this installation. Run the setup .exe again.' }
$a = @{
    Server = $server; Code = $code; KeyFingerprint = $key
    Thumbprint = ((New-Object Security.Cryptography.X509Certificates.X509Certificate2 (Join-Path $here 'signing.cer')).Thumbprint)
    CertFile = (Join-Path $here 'signing.cer'); Bundle = $zip.FullName; PlayerExe = $player
    SkipSelfCheck = $true; Yes = $true   # the files came inside the signed setup .exe, and the bundle hash is checked inside
}
if ($kiosk) { $a.KioskUser = $kiosk }
if ($words -contains 'reboot') { $a.AllowReboot = $true }
if ($words -contains 'startup') { $a.AllowStartup = $true }
if ($words -contains 'update') { $a.AllowUpdate = $true }
if ($server -like 'http://*') { $a.AllowInsecureHttp = $true }
# Install-Agent.ps1 trusts the bundle only through the SHA-256 written into it by the build; it must be the signed copy.
& (Join-Path $here 'Install-Agent.ps1') @a
exit $LASTEXITCODE
