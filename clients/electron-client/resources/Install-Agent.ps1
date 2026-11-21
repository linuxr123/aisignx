# Installs (or removes) the AiSignX privileged agent on this PC. Design: docs/windows-agent-service.md.
# MUST run elevated (Run as administrator). The agent runs as SYSTEM from a scheduled task, obeys only commands signed
# by the server's pinned command key, and installs only installers signed with the pinned code-signing certificate.
#
#   .\Install-Agent.ps1 -Server https://signage.example -Code ABCD-EFGH-JKLM-NPQR -KeyFingerprint "ab12 cd34 ef56 7890" `
#        -KioskUser kiosk -AllowUpdate [-AllowReboot] [-AllowStartup] [-AllowAutologin] [-Thumbprint <40 hex>] [-CertFile owner.cer]
#   .\Install-Agent.ps1 -Uninstall [-KeepData] [-Yes]
#   Plain http to another machine is refused unless -AllowInsecureHttp (the enrollment code and the public key would travel in clear; auto-login is refused over http anyway).
#   -Uninstall: -Force deletes the folders even when the agent's settings cannot be read (leftover startup/certificate changes are then NOT undone).
#   Add -DryRun to check every parameter and print the plan without changing anything (no elevation needed).
#
# Build-Agent.ps1 writes the two constants below (the bundle's SHA-256 and the default certificate thumbprint) and then
# signs this script. Never paste values you did not build yourself.
[CmdletBinding()]
param(
    [string]$Server,
    [string]$Code,
    [string]$KeyFingerprint,
    [string]$Thumbprint,
    [string]$CertFile,
    [string]$KioskUser,
    [string]$PlayerExe,
    [string]$Bundle,
    [string]$BundleSha256,
    [switch]$AllowReboot,
    [switch]$AllowStartup,
    [switch]$AllowUpdate,
    [switch]$AllowAutologin,
    [ValidateSet('SystemOnly', 'Machine')][string]$TrustScope = 'SystemOnly',
    [switch]$AllowAdminKiosk,
    [switch]$AllowCaCertificate,
    [switch]$AllowInsecureHttp,
    [switch]$Force,
    [switch]$SkipSelfCheck,
    # Install WITHOUT a code: the agent asks this server for approval when it starts (the player tells the server which PC it
    # is on; the admin approves, or ticked the box when pairing). Needs only -Server; -Code and -KeyFingerprint are not used.
    [switch]$Pending,
    [switch]$Uninstall,
    [switch]$KeepData,
    [switch]$Yes,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

# --- written by Build-Agent.ps1 ---
$EmbeddedBundleSha256 = ''
$EmbeddedThumbprint = ''
# ----------------------------------

$TaskName = 'AiSignX Agent'
$LaunchTaskName = 'AiSignX Player Launch'
$ProgramDir = Join-Path $env:ProgramFiles 'AiSignX\Agent'
$DataDir = Join-Path $env:ProgramData 'AiSignXAgent'
$SidSystem = '*S-1-5-18'
$SidAdmins = '*S-1-5-32-544'
$SidUsers = '*S-1-5-32-545'

function Say([string]$m) { Write-Host $m }
function Test-Elevated {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---- Parameter checks (pure; also used by the dry run) ----
function Test-ServerUrl([string]$s) { return $s -match '^https?://([A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:]+\])(:\d{1,5})?/?$' }
function Test-LoopbackServer([string]$s) { return $s -match '^http://(localhost|127(\.\d{1,3}){3}|\[::1\])(:\d{1,5})?/?$' }
function Test-Thumbprint([string]$s) { return $s -match '^[0-9A-Fa-f]{40}$' }
function Test-EnrollCode([string]$s) { return $s -match '^[A-HJ-NP-Za-hj-np-z2-9-]{16,24}$' }
function Test-Fingerprint([string]$s) { return $s -match '^([0-9A-Fa-f]{4}[ :-]?){3}[0-9A-Fa-f]{4}$' }
function Test-AccountName([string]$s) { return $s -match '^[A-Za-z0-9._ -]{1,64}$' }
function Test-WinPath([string]$s) { return $s -match '^[A-Za-z]:\\[^"<>|*?\r\n]{1,240}$' }

function Resolve-KioskSid([string]$name) {
    try { $sid = (New-Object System.Security.Principal.NTAccount($name)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
    catch { throw "The kiosk account '$name' does not exist on this PC." }
    if ($sid -notmatch '^S-1-5-21(-\d+){3,4}$') { throw "'$name' is not a local or domain user account." }
    return $sid
}
function Test-IsAdminMember([string]$sid) {
    try { return [bool](Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop | Where-Object { $_.SID.Value -eq $sid }) } catch { return $false }
}

function Get-Config {
    $thumb = if ($Thumbprint) { $Thumbprint } else { $EmbeddedThumbprint }
    $errors = New-Object System.Collections.ArrayList
    if (-not $Uninstall) {
        if (-not (Test-ServerUrl $Server)) { [void]$errors.Add('-Server must look like https://host[:port] (a literal host, no path).') }
        if (-not $Pending -and -not (Test-EnrollCode $Code)) { [void]$errors.Add('-Code is the 16-character one-time code from the display page.') }
        if (-not $Pending -and -not (Test-Fingerprint $KeyFingerprint)) { [void]$errors.Add('-KeyFingerprint is the 16 hex characters shown on the display page (groups of four).') }
        if (-not (Test-Thumbprint $thumb)) { [void]$errors.Add('-Thumbprint: 40 hex characters of the code-signing certificate (or build the agent with one).') }
        if ($Server -match '^http://' -and -not (Test-LoopbackServer $Server) -and -not $AllowInsecureHttp) { [void]$errors.Add('-Server is plain http: use https, or pass -AllowInsecureHttp to accept that the one-time code travels unencrypted.') }
        if ($KioskUser -and -not (Test-AccountName $KioskUser)) { [void]$errors.Add('-KioskUser has characters that are not allowed in an account name.') }
        if (($AllowStartup -or $AllowAutologin) -and -not $KioskUser) { [void]$errors.Add('-AllowStartup and -AllowAutologin need -KioskUser (the one account they may change).') }
        if ($PlayerExe -and -not (Test-WinPath $PlayerExe)) { [void]$errors.Add('-PlayerExe must be a full Windows path without quotes.') }
    }
    return @{ Thumbprint = $thumb.ToUpperInvariant(); Errors = $errors }
}

# ---- Folders ----
function Find-ReparsePoints([string]$dir) {
    if (-not (Test-Path -LiteralPath $dir)) { return @() }
    $bad = @()
    $root = Get-Item -LiteralPath $dir -Force
    if ($root.Attributes -band [IO.FileAttributes]::ReparsePoint) { $bad += $root.FullName }
    Get-ChildItem -LiteralPath $dir -Force -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) { $bad += $_.FullName }
    }
    return $bad
}
# A folder that already exists must belong to SYSTEM or Administrators: ProgramData lets any user create folders, and a
# pre-created one could hold planted keys, hard links or open handles.
function Assert-FolderNotPlanted([string]$dir) {
    if (-not (Test-Path -LiteralPath $dir)) { return }
    $o = (New-Object System.Security.Principal.NTAccount((Get-Acl -LiteralPath $dir).Owner)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($o -ne 'S-1-5-18' -and $o -ne 'S-1-5-32-544') { throw ('Refusing: ' + $dir + ' already exists and is not owned by SYSTEM or Administrators (owner ' + $o + '). Delete it yourself first (as administrator), then run this again.') }
}
function Protect-Folder([string]$dir, [bool]$usersRead) {
    $re = Find-ReparsePoints $dir
    if ($re.Count -gt 0) { throw ('Refusing: junction or symbolic link inside ' + $dir + ': ' + ($re -join ', ')) }
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    # Order matters: owner first, then cut the inheritance from ProgramData (users can read there) and grant, and only THEN
    # reset the children (they now inherit from the protected folder). The folder is never readable by users in between.
    & icacls.exe $dir /setowner $SidAdmins /T /C /Q | Out-Null
    & icacls.exe $dir /inheritance:r | Out-Null
    $args2 = @($dir, '/grant:r', ($SidSystem + ':(OI)(CI)F'), ($SidAdmins + ':(OI)(CI)F'))
    if ($usersRead) { $args2 += ($SidUsers + ':(OI)(CI)RX') }
    & icacls.exe @args2 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed on $dir" }
    if (@(Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue).Count -gt 0) {
        & icacls.exe ($dir + '\*') /reset /T /C /Q | Out-Null
    }
}

# ---- Zip extraction with a zip-slip check ----
function Expand-Safe([string]$zip, [string]$target) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $full = [IO.Path]::GetFullPath($target).TrimEnd('\') + '\'
    $za = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        foreach ($e in $za.Entries) {
            $dest = [IO.Path]::GetFullPath((Join-Path $target $e.FullName))
            if (-not $dest.StartsWith($full, [StringComparison]::OrdinalIgnoreCase)) { throw ('Unsafe path in the bundle: ' + $e.FullName) }
            if ($e.FullName.EndsWith('/') -or $e.FullName.EndsWith([string][char]92)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null; continue }
            New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dest, $true)
        }
    } finally { $za.Dispose() }
}

# ---- Tasks (constant XML, the only variable text is our own paths and SIDs) ----
function Esc([string]$s) { return [Security.SecurityElement]::Escape($s) }
function New-AgentTaskXml([string]$cmdPath) {
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>AiSignX privileged agent (signed commands only)</Description></RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled></BootTrigger>
    <TimeTrigger><Repetition><Interval>PT5M</Interval></Repetition><StartBoundary>2020-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Hidden>true</Hidden>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>$(Esc $cmdPath)</Command></Exec></Actions>
</Task>
"@
}
function New-LaunchTaskXml([string]$sid, [string]$exe) {
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Starts the AiSignX player in the kiosk account's session (run by the agent after an update)</Description></RegistrationInfo>
  <Triggers/>
  <Principals><Principal id="Author"><UserId>$(Esc $sid)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>$(Esc $exe)</Command></Exec></Actions>
</Task>
"@
}
# Constant launcher: Task Scheduler runs this file, so no quoting of paths with spaces is ever needed.
function New-RunCmd {
    "@echo off`r`nset ELECTRON_RUN_AS_NODE=1`r`nset NODE_OPTIONS=`r`nstart `"`" /wait `"%~dp0aisignx-agent.exe`" `"%~dp0src\agent-svc\main.js`"`r`nexit /b %errorlevel%`r`n"
}

# Runs one of the agent's helper scripts as SYSTEM through a throw-away task (used to edit SYSTEM's own certificate store).
function Invoke-AsSystem([string]$script, [string[]]$scriptArgs) {
    $name = 'AiSignX Agent OneShot'
    $argLine = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '" ' + ($scriptArgs -join ' ')
    $a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argLine
    $p = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -RunLevel Highest
    Register-ScheduledTask -TaskName $name -Action $a -Principal $p -Force | Out-Null
    try {
        Start-ScheduledTask -TaskName $name
        for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Seconds 1; if ((Get-ScheduledTask -TaskName $name).State -ne 'Running') { break } }
        return (Get-ScheduledTaskInfo -TaskName $name).LastTaskResult
    } finally { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue }
}

function Invoke-Uninstall {
    Say 'Removing the AiSignX agent.'
    $cfgFile = Join-Path $DataDir 'agent.json'
    $cfg = $null
    if (Test-Path -LiteralPath $cfgFile) { try { $cfg = Get-Content -LiteralPath $cfgFile -Raw | ConvertFrom-Json } catch { $cfg = $null } }
    if (-not $cfg -and -not $Force -and ((Test-Path -LiteralPath $DataDir) -or (Test-Path -LiteralPath $ProgramDir))) {
        throw 'The agent settings (agent.json) are missing or unreadable, so the startup, auto-login and certificate changes cannot be undone. Nothing was removed. Pass -Force to delete the folders anyway.'
    }
    foreach ($d in @($DataDir, $ProgramDir)) {
        $rp = Find-ReparsePoints $d
        if ($rp.Count -gt 0) { throw ('Refusing to delete: junction or symbolic link in ' + $d + ': ' + ($rp -join ', ')) }
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $LaunchTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Get-Process -Name 'aisignx-agent' -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ($ProgramDir + '\*') } | Stop-Process -Force
    $ps = Join-Path $ProgramDir 'ps'
    # Auto-login is cleared only when the agent itself set it (state.json says so): the owner's own setup stays.
    $agentSetAutologin = $false
    $stateFile = Join-Path $DataDir 'state.json'
    if (Test-Path -LiteralPath $stateFile) { try { $agentSetAutologin = ((Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json).autologin.enabled -eq $true) } catch { $agentSetAutologin = $false } }
    if ($agentSetAutologin -and (Test-Path -LiteralPath (Join-Path $ps 'Set-Autologon.ps1'))) {
        if ($Yes -or (Read-Host 'The agent set automatic Windows sign-in. Remove it? (y/N)') -match '^[yY]') {
            & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $ps 'Set-Autologon.ps1') -Mode Clear | Out-Null
        }
    }
    if ($cfg -and $cfg.kioskSid -and (Test-Path -LiteralPath (Join-Path $ps 'Set-Startup.ps1'))) {
        # Takes the Winlogon shell override and the Run entry that the agent created back out.
        $env:AISIGNX_AUTOSTART = 'off'; $env:AISIGNX_MODE = 'app'; $env:AISIGNX_SID = [string]$cfg.kioskSid
        $env:AISIGNX_EXE = [string]$cfg.playerExe; $env:AISIGNX_LAUNCHER = (Join-Path $ps 'shell-launcher.ps1'); $env:AISIGNX_CFGDIR = (Join-Path $ProgramDir 'launcher')
        try { & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $ps 'Set-Startup.ps1') | Out-Null } catch { Say 'Could not undo the startup settings (continuing).' }
    }
    if ($cfg -and $cfg.thumbprint -and (Test-Path -LiteralPath (Join-Path $ps 'Import-Cert.ps1')) -and (Test-Path -LiteralPath (Join-Path $DataDir 'owner.cer'))) {
        $scope = if ($cfg.trustScope -eq 'Machine') { 'Machine' } else { 'SystemOnly' }
        $cargs = @('-CertFile', ('"' + (Join-Path $DataDir 'owner.cer') + '"'), '-Thumbprint', [string]$cfg.thumbprint, '-TrustScope', $scope, '-Remove')
        if ($scope -eq 'Machine') { & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $ps 'Import-Cert.ps1') @cargs | Out-Null }
        else {
            $rc = Invoke-AsSystem (Join-Path $ps 'Import-Cert.ps1') $cargs
            if ($rc -ne 0) { Say ('WARNING: the certificate could not be removed from the agent''s store (task result ' + $rc + '). Remove it by hand: certmgr / certutil.') }
        }
    }
    if ($cfg -and $cfg.token -and $cfg.server) {
        try { Invoke-RestMethod -Method Post -Uri (([string]$cfg.server).TrimEnd('/') + '/api/player/v1/agent/unenroll') -Headers @{ Authorization = ('Bearer ' + $cfg.token) } -TimeoutSec 10 | Out-Null } catch { Say 'The server could not be told (press "Remove agent" on the display page).' }
    }
    if (Test-Path -LiteralPath $ProgramDir) { Remove-Item -LiteralPath $ProgramDir -Recurse -Force }
    if (-not $KeepData -and (Test-Path -LiteralPath $DataDir)) { Remove-Item -LiteralPath $DataDir -Recurse -Force }
    Say 'Done. If the display page still shows the agent, press "Remove agent" there.'
}

function Invoke-Install($conf) {
    $thumb = $conf.Thumbprint
    if ($KioskUser) {
        $kioskSid = Resolve-KioskSid $KioskUser
        if ((Test-IsAdminMember $kioskSid) -and -not $AllowAdminKiosk) { throw "The kiosk account '$KioskUser' is an administrator. Use a standard account, or pass -AllowAdminKiosk." }
    } else { $kioskSid = '' }
    $player = if ($PlayerExe) { $PlayerExe } else { Join-Path $env:ProgramFiles 'AiSignX Player\AiSignX Player.exe' }
    $bundlePath = $Bundle
    if (-not $bundlePath) { $bundlePath = (Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'AiSignX-Agent-*.zip' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName) }
    $wantHash = if ($BundleSha256) { $BundleSha256 } else { $EmbeddedBundleSha256 }
    if ($wantHash -notmatch '^[0-9A-Fa-f]{64}$') { throw 'No expected bundle hash: use the Install-Agent.ps1 that Build-Agent.ps1 made, or pass -BundleSha256.' }

    Say ('Server        : ' + $Server)
    Say ('Pinned signer : ' + $thumb)
    Say ('Kiosk account : ' + $(if ($KioskUser) { $KioskUser + ' (' + $kioskSid + ')' } else { '(none: startup and auto-login are unavailable)' }))
    Say ('Allowed here  : reboot=' + [bool]$AllowReboot + ' startup=' + [bool]$AllowStartup + ' update=' + [bool]$AllowUpdate + ' autologin=' + [bool]$AllowAutologin)
    Say ('Trust scope   : ' + $TrustScope + $(if ($TrustScope -eq 'Machine') { ' (the WHOLE PC will trust this signing key)' } else { ' (only the agent trusts this signing key)' }))
    if ($Server -like 'http://*') { Say 'WARNING: plain http. Signed commands still protect Tier B, but the enrollment code and the installer download travel unencrypted.' }
    if ($DryRun) { Say ('Bundle        : ' + $bundlePath); Say 'Dry run: nothing was changed.'; return }

    if (-not (Test-Elevated)) { throw 'Run this script elevated (right-click PowerShell > Run as administrator).' }
    if (-not $bundlePath -or -not (Test-Path -LiteralPath $bundlePath)) { throw 'The agent bundle (AiSignX-Agent-*.zip) was not found: pass -Bundle.' }
    if ($EmbeddedBundleSha256 -and -not $SkipSelfCheck) {
        $sig = Get-AuthenticodeSignature -LiteralPath $PSCommandPath
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Thumbprint -ne $thumb) { throw ('This script is not signed by the pinned certificate (status ' + $sig.Status + '). Trust the certificate for your own account first, or pass -SkipSelfCheck after checking the files yourself.') }
    }
    if ($TrustScope -eq 'Machine' -and -not $Yes -and (Read-Host 'Machine scope makes the WHOLE PC trust this signing key. Continue? (y/N)') -notmatch '^[yY]') { throw 'Cancelled.' }

    # 1. Protected folders (the data folder first: everything staged goes inside it, never into %TEMP%).
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Assert-FolderNotPlanted $DataDir
    Protect-Folder $DataDir $false
    Remove-Item -LiteralPath (Join-Path $DataDir 'keys') -Recurse -Force -ErrorAction SilentlyContinue      # a key is always made fresh at install
    foreach ($d in 'keys', 'staging', 'previous', 'logs', 'work') { New-Item -ItemType Directory -Path (Join-Path $DataDir $d) -Force | Out-Null }
    New-Item -ItemType Directory -Path (Join-Path $DataDir 'public') -Force | Out-Null
    # Users get nothing on the data folder except read access to public\ (status.json: output only, never read as input).
    & icacls.exe (Join-Path $DataDir 'public') /grant:r ($SidUsers + ':(OI)(CI)RX') | Out-Null
    # 2. The bundle: copy inside the protected folder, check the hash on that copy, extract with the zip-slip check.
    $work = Join-Path $DataDir 'work'
    $copy = Join-Path $work 'bundle.zip'
    Copy-Item -LiteralPath $bundlePath -Destination $copy -Force
    if ((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -ne $wantHash.ToUpperInvariant()) { throw 'The agent bundle does not match its expected SHA-256. Nothing was installed.' }
    $ex = Join-Path $work 'extract'
    if (Test-Path -LiteralPath $ex) { Remove-Item -LiteralPath $ex -Recurse -Force }
    Expand-Safe $copy $ex
    Get-Process -Name 'aisignx-agent' -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ($ProgramDir + '\*') } | Stop-Process -Force
    if (Test-Path -LiteralPath $ProgramDir) { Remove-Item -LiteralPath $ProgramDir -Recurse -Force }
    Protect-Folder $ProgramDir $true
    Copy-Item -Path (Join-Path $ex '*') -Destination $ProgramDir -Recurse -Force
    [IO.File]::WriteAllText((Join-Path $ProgramDir 'Run-Agent.cmd'), (New-RunCmd), [Text.Encoding]::ASCII)
    & icacls.exe $ProgramDir /setowner $SidAdmins /T /C /Q | Out-Null     # new files already inherited the folder's ACL
    Remove-Item -LiteralPath $ex -Recurse -Force
    # 3. The certificate: public part only, a leaf, the pinned thumbprint.
    $cer = Join-Path $DataDir 'owner.cer'
    if ($CertFile) { Copy-Item -LiteralPath $CertFile -Destination $cer -Force }
    else {
        $c0 = Get-Item -LiteralPath ('Cert:\CurrentUser\My\' + $thumb) -ErrorAction SilentlyContinue
        if (-not $c0) { $c0 = Get-Item -LiteralPath ('Cert:\LocalMachine\My\' + $thumb) -ErrorAction SilentlyContinue }
        if (-not $c0) { throw 'The certificate was not found in your certificate store: pass -CertFile (the public .cer).' }
        [void](Export-Certificate -Cert $c0 -FilePath $cer)
    }
    $x = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $cer
    if ($x.Thumbprint -ne $thumb) { throw 'The certificate file does not have the pinned thumbprint.' }
    $bc = $x.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' }
    if ($bc -and $bc.CertificateAuthority -and -not $AllowCaCertificate) { throw 'This certificate is a CA. Use a code-signing-only leaf certificate (or -AllowCaCertificate).' }
    $eku = $x.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId }
    if ($eku -notcontains '1.3.6.1.5.5.7.3.3') { throw 'This certificate is not a code-signing certificate.' }
    # 4. Key pair and enrollment (the one-time code goes through a file in the protected folder, never the command line).
    $exe = Join-Path $ProgramDir 'aisignx-agent.exe'
    $main = Join-Path $ProgramDir 'src\agent-svc\main.js'
    $env:ELECTRON_RUN_AS_NODE = '1'
    try {
        $cfgExists = Test-Path -LiteralPath (Join-Path $DataDir 'agent.json')
        [void](& $exe $main --init)
        $params = [ordered]@{ server = $Server.TrimEnd('/'); code = $Code; fingerprint = $KeyFingerprint; thumbprint = $thumb
            allow = [ordered]@{ reboot = [bool]$AllowReboot; startup = [bool]$AllowStartup; update = [bool]$AllowUpdate; autologin = [bool]$AllowAutologin }
            allowInsecureHttp = [bool]$AllowInsecureHttp; trustScope = $TrustScope; kioskUser = $KioskUser; kioskSid = $kioskSid; playerExe = $player }
        $pf = Join-Path $work 'enroll.json'
        [IO.File]::WriteAllText($pf, ($params | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
        $out = (& $exe $main $(if ($Pending) { '--prepare' } else { '--enroll' }) $pf | Select-Object -Last 1)
        $r = $null; try { $r = $out | ConvertFrom-Json } catch { $r = $null }
        if (-not $r -or -not $r.ok) { throw ('Enrollment' + $(if ($Pending) { ' set-up' }) + ' failed: ' + $(if ($r) { $r.reason } else { 'no answer from the agent' })) }
    } finally { Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue; Remove-Item -LiteralPath (Join-Path $work 'enroll.json') -Force -ErrorAction SilentlyContinue }
    # 5. Tasks.
    $xmlA = New-AgentTaskXml (Join-Path $ProgramDir 'Run-Agent.cmd')
    Register-ScheduledTask -TaskName $TaskName -Xml $xmlA -Force | Out-Null
    if ($kioskSid) { Register-ScheduledTask -TaskName $LaunchTaskName -Xml (New-LaunchTaskXml $kioskSid $player) -Force | Out-Null }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    $status = Join-Path $DataDir 'public\status.json'
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if ((Test-Path -LiteralPath $status) -and ((Get-Content -LiteralPath $status -Raw) -match '"state":"running"')) { Say 'The agent is running.'; return }
        if ($Pending -and (Test-Path -LiteralPath $status) -and ((Get-Content -LiteralPath $status -Raw) -match '"state":"waiting"')) {
            Say 'The agent is installed and waits for approval: pair the screen on the server (tick "approve the agent"), or approve it on the display page.'; return
        }
    }
    throw ('The agent did not report "running" in 30 seconds. See ' + (Join-Path $DataDir 'logs\agent.log'))
}

# Dot-sourcing loads only the functions (used by the tests).
if ($MyInvocation.InvocationName -ne '.') {
    $conf = Get-Config
    if ($conf.Errors.Count -gt 0) { $conf.Errors | ForEach-Object { Write-Host ('Problem: ' + $_) }; exit 2 }
    if ($Uninstall) {
        if ($DryRun) { Say 'Dry run: would stop and remove the tasks, undo auto-login and startup changes, remove the certificate and delete both folders.' }
        else { if (-not (Test-Elevated)) { throw 'Run this script elevated.' }; Invoke-Uninstall }
    } else { Invoke-Install $conf }
}
