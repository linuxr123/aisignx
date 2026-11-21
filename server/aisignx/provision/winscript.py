"""The one-step setup script for a Windows PC ("Set up a Windows PC" in the admin).

One script per PC, made by the admin and run once on that PC as administrator. It does what used to be several manual
steps: download and verify the signed player installer and the privileged-agent files from this server, install the
player for all users, pair it (the pairing is claimed by the script, so nobody types a code), enroll the privileged agent
(the elevated Install-Agent.ps1 that is shipped with the agent), and leave the chosen run mode to the agent.

What is inside the script, and why it is safe to hand to the admin only:
  - the server address, a one-time pairing secret and a one-time agent enrollment code: it is a secret, like the Android
    QR code. Both codes burn on first use and expire after a couple of hours;
  - the download links (signed, time-limited) and the SHA-256 of every file: the script checks each file against its hash
    before it uses it. The script is the trust anchor (it came out of the admin's signed-in session); the hashes bind it to
    exactly the files the admin uploaded;
  - the public code-signing certificate's thumbprint (and the certificate itself is one of the downloads): the agent pins it
    and later accepts only updates signed by it.
"""
import re

SCRIPT_NAME = "Setup-AiSignX-Windows-PC.ps1"
_URL = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~/%+=?&-]*)?$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_THUMB = re.compile(r"^[0-9A-F]{40}$")
_SECRET = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_CODE = re.compile(r"^[A-HJ-NP-Z2-9-]{16,24}$")
_FINGERPRINT = re.compile(r"^([0-9a-f]{4} ){3}[0-9a-f]{4}$")
_ACCOUNT = re.compile(r"^[A-Za-z0-9._ -]{1,64}$")


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _b(value: bool) -> str:
    return "$true" if value else "$false"


_TEMPLATE = r"""<#
.SYNOPSIS
  Sets up this Windows PC as an AiSignX screen: the player, the privileged agent, pairing and the run mode, in one step.

.DESCRIPTION
  Made by your AiSignX server (@@SERVER_TEXT@@) for the screen "@@NAME_TEXT@@". Run it ONCE, on the PC that is to
  become that screen, as an administrator (it asks Windows for permission). It
    1. downloads the player installer and the agent files from your server and checks every file's SHA-256,
    2. installs the player for all users,
    3. pairs this PC with the screen "@@NAME_TEXT@@" (no code to type),
    4. installs and enrolls the privileged agent (it runs as SYSTEM and can then restart the player, reboot the PC,
       change how the player runs and install signed updates, from the web page),
    5. sends the run mode you chose ("@@RUN_TEXT@@") to the PC as soon as the agent is online.
  The player runs as the Windows account "@@KIOSK_TEXT@@" (it must already exist here; a standard account is best).

  This file holds one-time secrets (a pairing secret and an enrollment code that expire). Do not share it or post a
  picture of it. It deletes itself after a successful run.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Setup-AiSignX-Windows-PC.ps1
  powershell -ExecutionPolicy Bypass -File .\Setup-AiSignX-Windows-PC.ps1 -DryRun          # show the plan, change nothing
  powershell -ExecutionPolicy Bypass -File .\Setup-AiSignX-Windows-PC.ps1 -DownloadOnly    # download and verify, change nothing
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$DownloadOnly,
    [switch]$KeepScript
)
$ErrorActionPreference = 'Stop'

$Server       = @@SERVER@@
$PlayerUrl    = @@PLAYER_URL@@
$PlayerSha    = @@PLAYER_SHA@@
$AgentZipUrl  = @@ZIP_URL@@
$AgentZipSha  = @@ZIP_SHA@@
$AgentPsUrl   = @@PS_URL@@
$AgentPsSha   = @@PS_SHA@@
$CertUrl      = @@CERT_URL@@
$CertSha      = @@CERT_SHA@@
$Thumbprint   = @@THUMBPRINT@@
$PairSecret   = @@PAIR_SECRET@@
$EnrollCode   = @@ENROLL_CODE@@
$KeyPrint     = @@KEY_FINGERPRINT@@
$KioskUser    = @@KIOSK_USER@@
$AllowReboot  = @@ALLOW_REBOOT@@
$AllowStartup = @@ALLOW_STARTUP@@
$AllowUpdate  = @@ALLOW_UPDATE@@
$AllowAutologin = @@ALLOW_AUTOLOGIN@@
$InsecureHttp = @@INSECURE_HTTP@@

function Step($t) { Write-Host ''; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "    $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host ''; Write-Host "Problem: $t" -ForegroundColor Red; exit 1 }
function Quote([string]$s) { return '"' + $s + '"' }
function Test-Elevated {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- ask for administrator permission (not needed to look or to download-and-check) ------------------------------
if (-not $DryRun -and -not $DownloadOnly -and -not (Test-Elevated)) {
    Write-Host 'This needs administrator permission; asking Windows for it...' -ForegroundColor Yellow
    $again = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote $PSCommandPath))
    if ($KeepScript) { $again += '-KeepScript' }
    $p = Start-Process -FilePath powershell.exe -ArgumentList $again -Verb RunAs -Wait -PassThru
    exit $p.ExitCode
}

# --- what will happen --------------------------------------------------------------------------------------------
Step 'Set-up of this PC as the AiSignX screen @@NAME_TEXT@@'
Write-Host "    Server        : $Server"
Write-Host "    Run as        : @@RUN_TEXT@@ (applied once the agent is online)"
Write-Host "    Player account: $KioskUser"
Write-Host "    The agent may : reboot=$AllowReboot startup=$AllowStartup update=$AllowUpdate auto-login=$AllowAutologin"
if ($Server -like 'http://*') { Warn 'The server address is plain http: the downloads are protected by their checksums, but the pairing secret travels unencrypted on your network. Use https if the network is not yours.' }
if ($DryRun) { Ok 'Dry run: nothing was downloaded or changed.'; exit 0 }

# --- the player's account must exist ------------------------------------------------------------------------------
try { $kioskSid = (New-Object System.Security.Principal.NTAccount($KioskUser)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
catch { Fail "The Windows account '$KioskUser' does not exist on this PC. Create it (a standard account) or make the script again with another name." }

# --- a protected work folder -------------------------------------------------------------------------------------
# Elevated: a folder only SYSTEM and administrators can use (what is downloaded here is run as administrator).
# Not elevated (only -DownloadOnly gets this far): the user's own temp folder, nothing is installed from it.
$elevated = Test-Elevated
$workRoot = if ($elevated) { $env:ProgramData } else { [IO.Path]::GetTempPath() }
$work = Join-Path $workRoot ('AiSignXSetup-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $work -Force | Out-Null
if ($elevated) { & icacls.exe $work /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q 2>&1 | Out-Null }

function Get-Verified([string]$url, [string]$name, [string]$sha) {
    $dest = Join-Path $work $name
    Write-Host "    downloading $name"
    try { Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing } catch { Fail "could not download $name from your server ($($_.Exception.Message)). The link may have expired: make the setup script again." }
    $got = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $sha.ToLowerInvariant()) { Fail "$name does not match its checksum (got $got). Nothing was installed." }
    return $dest
}

try {
    Step 'Downloading and checking the files'
    $installer = Get-Verified $PlayerUrl 'AiSignX-Player-Setup.exe' $PlayerSha
    $zip       = Get-Verified $AgentZipUrl 'AiSignX-Agent.zip' $AgentZipSha
    $agentPs   = Get-Verified $AgentPsUrl 'Install-Agent.ps1' $AgentPsSha
    $cer       = Get-Verified $CertUrl 'signing.cer' $CertSha
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $cer
    if ($cert.Thumbprint -ne $Thumbprint) { Fail 'The signing certificate does not have the thumbprint this script expects.' }
    $sig = Get-AuthenticodeSignature -LiteralPath $installer
    if ($sig.Status -in 'HashMismatch', 'NotSigned', 'NotSupportedFileFormat') { Fail "The player installer is not signed properly (status $($sig.Status))." }
    if (-not $sig.SignerCertificate -or $sig.SignerCertificate.Thumbprint -ne $Thumbprint) { Fail 'The player installer was not signed by your code-signing certificate.' }
    Ok 'all files match their checksums; the installer is signed by your certificate'
    if ($DownloadOnly) { Ok 'Download only: nothing was installed.'; exit 0 }

    # --- the player ------------------------------------------------------------------------------------------------
    Step 'Installing the player for all users'
    $proc = Start-Process -FilePath $installer -ArgumentList '/S', '/allusers' -Wait -PassThru
    if ($proc.ExitCode -ne 0) { Fail "The player installer failed (exit code $($proc.ExitCode))." }
    $player = Join-Path $env:ProgramFiles 'AiSignX Player\AiSignX Player.exe'
    if (-not (Test-Path -LiteralPath $player)) {
        $found = Get-ChildItem -LiteralPath $env:ProgramFiles -Filter 'AiSignX Player.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $player = $found.FullName } else { Fail 'The player was installed but its program file was not found under Program Files.' }
    }
    Ok "installed: $player"

    # --- pairing ---------------------------------------------------------------------------------------------------
    Step 'Pairing this PC with the screen'
    $body = @{ pairing_secret = $PairSecret; credential = 'bearer' } | ConvertTo-Json
    try { $r = Invoke-RestMethod -Method Post -Uri ($Server.TrimEnd('/') + '/api/player/v1/pair/status') -ContentType 'application/json' -Body $body }
    catch { Fail "Pairing failed ($($_.Exception.Message)). This script was already used or has expired: make a new one in the admin." }
    if ($r.status -ne 'paired' -or -not $r.token) { Fail 'The server did not complete the pairing. Make a new setup script in the admin.' }
    # The player (running as the player account) takes this once, on its first start, and deletes the file.
    $dataDir = Join-Path $env:ProgramData 'AiSignX'
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    & icacls.exe $dataDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' ('*' + $kioskSid + ':(OI)(CI)M') /Q 2>&1 | Out-Null
    $setup = [ordered]@{ server = $Server.TrimEnd('/'); token = [string]$r.token; created = (Get-Date).ToUniversalTime().ToString('o') }
    [IO.File]::WriteAllText((Join-Path $dataDir 'setup.json'), ($setup | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Ok "paired as $($r.display.name)"

    # --- the privileged agent --------------------------------------------------------------------------------------
    Step 'Installing and enrolling the privileged agent'
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote $agentPs),
           '-Server', $Server.TrimEnd('/'), '-Code', $EnrollCode, '-KeyFingerprint', (Quote $KeyPrint),
           '-Thumbprint', $Thumbprint, '-CertFile', (Quote $cer), '-Bundle', (Quote $zip), '-BundleSha256', $AgentZipSha,
           '-KioskUser', (Quote $KioskUser), '-PlayerExe', (Quote $player), '-SkipSelfCheck', '-Yes')
    if ($AllowReboot) { $a += '-AllowReboot' }
    if ($AllowStartup) { $a += '-AllowStartup' }
    if ($AllowUpdate) { $a += '-AllowUpdate' }
    if ($AllowAutologin) { $a += '-AllowAutologin' }
    if ($InsecureHttp) { $a += '-AllowInsecureHttp' }
    $ag = Start-Process -FilePath powershell.exe -ArgumentList $a -Wait -PassThru -NoNewWindow
    if ($ag.ExitCode -ne 0) { Fail "The agent installer failed (exit code $($ag.ExitCode)); read what it printed above. The player is installed and paired." }
    Ok 'the agent is installed and running'

    # --- start the player now (if the player account is signed in; otherwise it starts at its next sign-in) --------
    Start-ScheduledTask -TaskName 'AiSignX Player Launch' -ErrorAction SilentlyContinue

    Write-Host ''
    Write-Host 'Done.' -ForegroundColor Green
    Write-Host '  - In the admin, open the screen: it shows the agent as running, and "How the player runs" will be set to'
    Write-Host '    @@RUN_TEXT@@ within a minute.'
    Write-Host "  - The player starts when '$KioskUser' signs in (or now, if that account is signed in)."
    if (-not $KeepScript) { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue }
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
"""


def build(*, server: str, name: str, run_text: str, kiosk_user: str, player: dict, agent_zip: dict, agent_script: dict,
          cert: dict, thumbprint: str, pair_secret: str, enroll_code: str, key_fingerprint: str, allow_reboot: bool,
          allow_startup: bool, allow_update: bool, allow_autologin: bool) -> str:
    """The script text. player/agent_zip/agent_script/cert are {"url", "sha256"}. Raises ValueError for anything unsafe."""
    if not _URL.match(server or ""):
        raise ValueError("bad server")
    for label, d in (("player", player), ("agent zip", agent_zip), ("agent script", agent_script), ("certificate", cert)):
        if not _URL.match(d.get("url", "")) or not _HEX64.match(d.get("sha256", "")):
            raise ValueError(f"bad {label}")
    if not _THUMB.match(thumbprint or ""):
        raise ValueError("bad thumbprint")
    if not _SECRET.match(pair_secret or "") or not _CODE.match(enroll_code or "") or not _FINGERPRINT.match(key_fingerprint or ""):
        raise ValueError("bad secret, code or fingerprint")
    if not _ACCOUNT.match(kiosk_user or ""):
        raise ValueError("bad account name")
    text = (_TEMPLATE
            .replace("@@SERVER_TEXT@@", re.sub(r"[^A-Za-z0-9:/._-]", "", server))
            .replace("@@NAME_TEXT@@", re.sub(r"[^A-Za-z0-9 ._-]", "", name or "this screen") or "this screen")
            .replace("@@RUN_TEXT@@", re.sub(r"[^A-Za-z0-9 :,()-]", "", run_text))
            .replace("@@KIOSK_TEXT@@", re.sub(r"[^A-Za-z0-9._ -]", "", kiosk_user))
            .replace("@@SERVER@@", _q(server))
            .replace("@@PLAYER_URL@@", _q(player["url"])).replace("@@PLAYER_SHA@@", _q(player["sha256"]))
            .replace("@@ZIP_URL@@", _q(agent_zip["url"])).replace("@@ZIP_SHA@@", _q(agent_zip["sha256"]))
            .replace("@@PS_URL@@", _q(agent_script["url"])).replace("@@PS_SHA@@", _q(agent_script["sha256"]))
            .replace("@@CERT_URL@@", _q(cert["url"])).replace("@@CERT_SHA@@", _q(cert["sha256"]))
            .replace("@@THUMBPRINT@@", _q(thumbprint)).replace("@@PAIR_SECRET@@", _q(pair_secret))
            .replace("@@ENROLL_CODE@@", _q(enroll_code)).replace("@@KEY_FINGERPRINT@@", _q(key_fingerprint))
            .replace("@@KIOSK_USER@@", _q(kiosk_user))
            .replace("@@ALLOW_REBOOT@@", _b(allow_reboot)).replace("@@ALLOW_STARTUP@@", _b(allow_startup))
            .replace("@@ALLOW_UPDATE@@", _b(allow_update)).replace("@@ALLOW_AUTOLOGIN@@", _b(allow_autologin))
            .replace("@@INSECURE_HTTP@@", _b(server.startswith("http://"))))
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def certificate_thumbprint(data: bytes) -> str | None:
    """SHA-1 thumbprint (40 upper-case hex) of a DER or PEM X.509 certificate, or None when it is not one."""
    import hashlib

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    for loader in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
        try:
            cert = loader(data)
            return hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()  # noqa: S324 (a Windows thumbprint, not security)
        except ValueError:
            continue
    return None
