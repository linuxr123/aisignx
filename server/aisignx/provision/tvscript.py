"""The PC install script for Android TV boxes (and any Android device with network debugging).

Typing an address with a TV remote, and the "Downloader" apps that go with it, are the weak part of putting the app on
a TV box. This script does it from a PC instead: it finds or fetches Google's own `adb` tool, installs the app from this
server over the network, and starts it with the server address filled in. The TV then only shows its pairing code.

The script is generated for one server and one app file. It holds the server address, the (time-limited) download link
for the app and the app's SHA-256, which the script checks before it installs anything. No password, pairing secret or
token of the person who made it is inside. Windows PowerShell 5.1 and PowerShell 7 can run it.
"""
import re

SCRIPT_NAME = "Install-AiSignX-on-Android-TV.ps1"
SETUP_ACTIVITY = "com.aisignx.player.ui.SetupActivity"
ADMIN_RECEIVER = "com.aisignx.player.AisignxDeviceAdminReceiver"
EXTRA_SERVER = "com.aisignx.player.SERVER"
_SAFE = re.compile(r"^[A-Za-z0-9:/._~?&=%+#@\[\]-]{1,600}$")
_PACKAGES = ("com.aisignx.player", "com.aisignx.player.debug")


def _q(value: str) -> str:
    """A value as a PowerShell single-quoted string. Everything inserted is also checked against _SAFE first."""
    return "'" + value.replace("'", "''") + "'"


_TEMPLATE = r"""<#
.SYNOPSIS
  Installs AiSignX on an Android TV box (or another Android device) from this PC, over the network.

.DESCRIPTION
  Made by your AiSignX server (@@SERVER_TEXT@@) for app version @@VERSION_TEXT@@.
  It needs no account and no password. What it does:
    1. finds Google's adb tool, or downloads it from Google (platform-tools, a few MB)
    2. downloads the app from your server and checks its SHA-256 against the value inside this script
    3. connects to the device, installs the app and opens it with the server address filled in
  On the device, press Save when it shows the address; it then shows a pairing code: type that code in the admin
  (Displays > Pair a display).

  BEFORE you run it, once, on the device: Settings > Device Preferences > About > press Build 7 times, then
  Settings > Developer options > turn ON "USB debugging" / "Network debugging" (on an NVIDIA Shield: "Network debugging").
  The device asks "Allow this computer?" the first time: choose Always allow.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Install-AiSignX-on-Android-TV.ps1
  powershell -ExecutionPolicy Bypass -File .\Install-AiSignX-on-Android-TV.ps1 -Address 192.168.1.40
#>
[CmdletBinding()]
param(
    # The device's address on your network (Settings > Network on the device). Asked for when left out.
    [string]$Address = '',
    # Also make the app the Device Owner (full control: silent updates, reboot). Only works on a device with NO
    # Google or other accounts signed in. Without it the app is installed as a normal app.
    [switch]$DeviceOwner
)
$ErrorActionPreference = 'Stop'

$Server   = @@SERVER@@
$ApkUrl   = @@APK_URL@@
$ApkSha   = @@APK_SHA@@
$Package  = @@PACKAGE@@
$Activity = @@ACTIVITY@@
$Receiver = @@RECEIVER@@
$ExtraKey = @@EXTRA@@
$ToolsUrl = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'

function Step($t) { Write-Host ''; Write-Host "==> $t" -ForegroundColor Cyan }
function Fail($t) { Write-Host ''; Write-Host "Problem: $t" -ForegroundColor Red; exit 1 }

# --- the device address -------------------------------------------------------------------------------------------
if (-not $Address) { $Address = (Read-Host "The device's IP address, for example 192.168.1.40").Trim() }
if ($Address -notmatch '^\d{1,3}(\.\d{1,3}){3}(:\d{1,5})?$') { Fail "'$Address' is not an IP address like 192.168.1.40" }
$Serial = if ($Address.Contains(':')) { $Address } else { "$($Address):5555" }

# --- adb -----------------------------------------------------------------------------------------------------------
Step 'Looking for adb (Google''s Android tool)'
$adb = $null
$cmd = Get-Command adb.exe -ErrorAction SilentlyContinue
if ($cmd) { $adb = $cmd.Source }
$local = Join-Path $env:LOCALAPPDATA 'AiSignX\platform-tools'
if (-not $adb -and (Test-Path (Join-Path $local 'adb.exe'))) { $adb = Join-Path $local 'adb.exe' }
if (-not $adb) {
    Write-Host '    not found: downloading it from Google'
    $zip = Join-Path ([IO.Path]::GetTempPath()) 'aisignx-platform-tools.zip'
    $parent = Split-Path $local -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    try { Invoke-WebRequest -Uri $ToolsUrl -OutFile $zip -UseBasicParsing } catch { Fail "could not download adb from Google ($($_.Exception.Message)). Install 'Android platform-tools' yourself and run this again." }
    if (Test-Path $local) { Remove-Item $local -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $parent -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    $adb = Join-Path $local 'adb.exe'
    if (-not (Test-Path $adb)) { Fail 'adb was not found after unpacking the download.' }
}
Write-Host "    using $adb"

# --- the app -------------------------------------------------------------------------------------------------------
Step 'Downloading the app from your server'
$apk = Join-Path ([IO.Path]::GetTempPath()) 'aisignx-player.apk'
try { Invoke-WebRequest -Uri $ApkUrl -OutFile $apk -UseBasicParsing } catch { Fail "could not download the app from $Server ($($_.Exception.Message)). The link may have expired: make a new install link in the admin." }
$got = (Get-FileHash -Path $apk -Algorithm SHA256).Hash.ToLowerInvariant()
if ($got -ne $ApkSha.ToLowerInvariant()) { Remove-Item $apk -Force; Fail "the downloaded app does not match its checksum (got $got). Nothing was installed." }
Write-Host "    ok, $([math]::Round((Get-Item $apk).Length / 1MB, 1)) MB, checksum matches"

# --- connect -------------------------------------------------------------------------------------------------------
Step "Connecting to the device at $Serial"
& $adb start-server 2>&1 | Out-Null
$out = (& $adb connect $Serial 2>&1 | Out-String).Trim()
Write-Host "    $out"
if ($out -match 'refused|failed|cannot|unable|timed out') { Fail "could not connect. Is 'Network debugging' (or 'USB debugging') switched on, and is the address right? The device and this PC must be on the same network." }
Start-Sleep -Seconds 1
$state = (& $adb -s $Serial get-state 2>&1 | Out-String).Trim()
if ($state -match 'unauthorized') { Fail "the device has not allowed this computer yet. Look at the TV: choose 'Always allow' on the 'Allow USB debugging?' question, then run this again." }
if ($state -ne 'device') { Fail "the device answered '$state', not 'device'." }

# --- install -------------------------------------------------------------------------------------------------------
Step 'Installing the app'
$res = (& $adb -s $Serial install -r -g $apk 2>&1 | Out-String).Trim()
Write-Host "    $res"
if ($res -notmatch 'Success') { Fail 'the install did not succeed (see the line above). A different version signed with another key must be uninstalled first.' }

if ($DeviceOwner) {
    Step 'Making the app the Device Owner'
    $o = (& $adb -s $Serial shell dpm set-device-owner "$Package/$Receiver" 2>&1 | Out-String).Trim()
    Write-Host "    $o"
    if ($o -match 'Success') { Write-Host '    done: the app now has full control of this device' -ForegroundColor Green }
    else { Write-Host "    not possible: $o" -ForegroundColor Yellow; Write-Host '    (It needs a device with no accounts signed in. The app works without it, with fewer rights.)' -ForegroundColor Yellow }
}

# --- start it with the server filled in ----------------------------------------------------------------------------
Step 'Opening the app with your server address'
& $adb -s $Serial shell am start -n "$Package/$Activity" --es $ExtraKey $Server 2>&1 | Out-Null
Remove-Item $apk -Force -ErrorAction SilentlyContinue
& $adb disconnect $Serial 2>&1 | Out-Null

Write-Host ''
Write-Host 'Done. On the device:' -ForegroundColor Green
Write-Host "  1. The app shows $Server. Wait for the green 'connected' line, then press Save."
Write-Host '  2. It shows a short pairing code.'
Write-Host '  3. In the admin: Displays > Pair a display, and type the code.'
"""


def build(*, server: str, apk_url: str, apk_sha256: str, package: str, version: str) -> str:
    """The script text for this server and app. Raises ValueError if anything inserted looks unsafe."""
    for name, value in (("server", server), ("apk_url", apk_url)):
        if not _SAFE.match(value or ""):
            raise ValueError(f"unsafe {name}")
    if not re.fullmatch(r"[0-9a-f]{64}", apk_sha256 or ""):
        raise ValueError("bad checksum")
    if package not in _PACKAGES:
        raise ValueError("unknown package")
    text = (_TEMPLATE
            .replace("@@SERVER_TEXT@@", re.sub(r"[^A-Za-z0-9:/._-]", "", server))
            .replace("@@VERSION_TEXT@@", re.sub(r"[^A-Za-z0-9._-]", "", version or "latest") or "latest")
            .replace("@@SERVER@@", _q(server)).replace("@@APK_URL@@", _q(apk_url)).replace("@@APK_SHA@@", _q(apk_sha256))
            .replace("@@PACKAGE@@", _q(package)).replace("@@ACTIVITY@@", _q(SETUP_ACTIVITY))
            .replace("@@RECEIVER@@", _q(ADMIN_RECEIVER)).replace("@@EXTRA@@", _q(EXTRA_SERVER)))
    return text.replace("\r\n", "\n").replace("\n", "\r\n")  # Windows PowerShell likes CRLF
