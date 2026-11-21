<#
.SYNOPSIS
  Puts nginx in front of the AiSignX server on Windows, with HTTPS (a self-signed certificate or one you bought).

.DESCRIPTION
  Run it AFTER scripts\install-server.ps1, from an elevated PowerShell (or just run it: it asks for permission).
  What it does:
    1. Gets a certificate: your own (-Cert / -Key, PEM files) or a self-signed one made by AiSignX for -Hostname.
    2. Downloads the current STABLE nginx for Windows from nginx.org (over HTTPS) into ProgramData\AiSignX\nginx.
    3. Writes nginx.conf (made by AiSignX: `flask https config`), checks it with `nginx -t`.
    4. Turns on AISIGNX_PROXY_HOPS=1 and AISIGNX_SECURE_COOKIES=1 for the server and restarts it.
    5. Registers two Scheduled Tasks: "AiSignX nginx" (runs nginx at start-up as LOCAL SERVICE) and
       "AiSignX nginx reload" (every 5 minutes: when the certificate file changed, for example after you
       replaced it on the HTTPS page, nginx is told to reload it).
    6. Opens ports 80 and 443, and closes the plain port of the server to other computers (only nginx
       talks to it, on this machine).
  Run it again with -Update to move to a newer stable nginx (your certificate and settings are kept, the old
  nginx is kept for a way back), or with -AutoUpdate to have Windows check weekly.

    powershell -ExecutionPolicy Bypass -File scripts\setup-nginx.ps1 -Hostname signs.example.lan,192.168.1.19
    powershell -ExecutionPolicy Bypass -File scripts\setup-nginx.ps1 -Cert C:\certs\fullchain.pem -Key C:\certs\key.pem
    powershell -ExecutionPolicy Bypass -File scripts\setup-nginx.ps1 -Update
    powershell -ExecutionPolicy Bypass -File scripts\setup-nginx.ps1 -Uninstall

  Honest limits: nginx.org publishes PGP signatures but no checksums, and Windows has no PGP tool built in, so the
  download is protected by HTTPS to nginx.org only (the SHA-256 of what was installed is written to
  nginx\installed.json). This script has been tested for syntax and for the text it generates; it has not yet been run
  on a real PC (docs\testing-checklist.md).
#>
[CmdletBinding()]
param(
    # Names and IP addresses the screens and browsers use, separated by commas. Default: the names in the certificate
    # that is already stored, else this computer's name and its IPv4 addresses.
    [string[]]$Hostname = @(),
    # A certificate you bought (PEM: yours first, then the intermediate certificates) and its private key.
    [string]$Cert = '',
    [string]$Key = '',
    [string]$KeyPassphrase = '',
    [int]$Port = 5000,
    [int]$HttpPort = 80,
    [int]$HttpsPort = 443,
    [switch]$Update,
    [switch]$AutoUpdate,
    [switch]$Hsts,
    [switch]$NoRedirect,
    [switch]$NoFirewall,
    [switch]$Uninstall,
    # Used by the scheduled reload task.
    [switch]$Reload,
    # Show what would be done and change nothing.
    [switch]$DryRun,
    [switch]$Yes,
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'AiSignX'),
    [string]$DataDir = (Join-Path $env:ProgramData 'AiSignX')
)
$ErrorActionPreference = 'Stop'
$ServerTask = 'AiSignX Server'
$NginxTask = 'AiSignX nginx'
$ReloadTask = 'AiSignX nginx reload'
$UpdateTask = 'AiSignX nginx update'
$OldRule = 'AiSignX server'
$App = Join-Path $InstallDir 'app'
$Venv = Join-Path $InstallDir 'venv'
$EnvFile = Join-Path $DataDir 'aisignx.env'
$NginxRoot = Join-Path $DataDir 'nginx'
$NginxDir = Join-Path $NginxRoot 'app'
$TlsDir = Join-Path $DataDir 'tls'
$Info = Join-Path $NginxRoot 'installed.json'
$Marker = Join-Path $NginxRoot 'loaded.sha256'
$Self = Join-Path $NginxRoot 'setup-nginx.ps1'
$DownloadPage = 'https://nginx.org/en/download.html'

function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "    $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "`nProblem: $t" -ForegroundColor Red; exit 1 }
function Do-It([string]$what, [scriptblock]$action) {
    if ($DryRun) { Write-Host "    [dry run] $what" -ForegroundColor DarkGray } else { & $action }
}

foreach ($p in @($Port, $HttpPort, $HttpsPort)) { if ($p -lt 1 -or $p -gt 65535) { Fail 'Ports must be numbers from 1 to 65535.' } }
# Everything below ends up inside nginx.conf or a command line: only plain host names and addresses go in.
$Hostname = @($Hostname | ForEach-Object { $_ -split '[,;\s]+' } | Where-Object { $_ })
foreach ($h in $Hostname) { if ($h -notmatch '^[A-Za-z0-9.:*-]{1,253}$') { Fail "'$h' is not a host name or an IP address." } }

function Test-Admin {
    $pr = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-StableVersion {
    # The newest STABLE version listed on nginx.org (the page has a "Stable version" table).
    $html = (Invoke-WebRequest -Uri $DownloadPage -UseBasicParsing -TimeoutSec 60).Content
    $i = $html.IndexOf('Stable version')
    if ($i -lt 0) { throw 'nginx.org/en/download.html has changed: no "Stable version" table found.' }
    $m = [regex]::Match($html.Substring($i), 'nginx-(\d+\.\d+\.\d+)\.zip')
    if (-not $m.Success) { throw 'No Windows download found in the stable table on nginx.org.' }
    $m.Groups[1].Value
}
function Get-InstalledVersion {
    if (Test-Path $Info) { try { return (Get-Content $Info -Raw | ConvertFrom-Json).version } catch { } }
    $null
}
function Compare-Version([string]$a, [string]$b) { ([version]$a).CompareTo([version]$b) }

function Invoke-Flask([string[]]$flaskArgs) {
    $saved = @{}
    foreach ($line in Get-Content $EnvFile -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$' -and -not $line.TrimStart().StartsWith('#')) {
            $saved[$Matches[1]] = [Environment]::GetEnvironmentVariable($Matches[1], 'Process')
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process')
        }
    }
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        Push-Location $App
        $out = & (Join-Path $Venv 'Scripts\flask.exe') --app wsgi @flaskArgs 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
        $out | ForEach-Object { Write-Host "    $_" }
        if ($code -ne 0) { throw "flask $($flaskArgs -join ' ') failed (exit code $code)." }
    } finally {
        $ErrorActionPreference = $old
        Pop-Location
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
    }
}

function Test-NginxConfig([string]$dir) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $out = & (Join-Path $dir 'nginx.exe') -t -p $dir 2>&1 | ForEach-Object { "$_" }
        $ok = ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $old }
    if (-not $ok) { Write-Host ($out -join "`n") -ForegroundColor Red }
    $ok
}

function Write-NginxConfig([string]$dir) {
    $flaskArgs = @('https', 'config', '--standalone', '--out', (Join-Path $dir 'conf\nginx.conf'), '--upstream', "127.0.0.1:$Port")
    if ($Hsts) { $flaskArgs += '--hsts' }
    if ($NoRedirect) { $flaskArgs += '--no-redirect' }
    foreach ($h in $Hostname) { $flaskArgs += @('--name', $h) }
    Invoke-Flask $flaskArgs
}

function Set-EnvValue([string]$name, [string]$value) {
    $lines = @(Get-Content $EnvFile -ErrorAction SilentlyContinue)
    $done = $false
    $new = foreach ($l in $lines) {
        if ($l -match "^\s*#?\s*$name\s*=") { if (-not $done) { "$name=$value"; $done = $true } } else { $l }
    }
    if (-not $done) { $new = @($new) + "$name=$value" }
    Set-Content -Path $EnvFile -Value $new -Encoding UTF8
}

function Install-NginxFiles([string]$version, [string]$target) {
    $zip = Join-Path ([IO.Path]::GetTempPath()) "nginx-$version.zip"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("nginx-extract-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    try {
        Invoke-WebRequest -Uri "https://nginx.org/download/nginx-$version.zip" -OutFile $zip -UseBasicParsing -TimeoutSec 300
        $hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inner = Join-Path $tmp "nginx-$version"
        if (-not (Test-Path (Join-Path $inner 'nginx.exe'))) { throw 'The download does not contain nginx.exe where expected.' }
        if (Test-Path $target) { Remove-Item $target -Recurse -Force }
        Move-Item -Path $inner -Destination $target
        @{ version = $version; sha256 = $hash; installed = (Get-Date).ToUniversalTime().ToString('o'); source = "https://nginx.org/download/nginx-$version.zip" } |
            ConvertTo-Json | Set-Content -Path $Info -Encoding UTF8
        Ok "nginx $version unpacked (zip SHA-256 $hash)"
    } finally {
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Grant-Folders {
    # TLS folder: the server (LOCAL SERVICE) writes the certificate and key when an admin uses the HTTPS page; nobody else may read them.
    & icacls.exe $TlsDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' /Q | Out-Null
    # nginx folder: LOCAL SERVICE runs nginx and writes its logs and temp files.
    & icacls.exe $NginxRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' /Q | Out-Null
}

function Register-NginxTasks {
    $nginx = Join-Path $NginxDir 'nginx.exe'
    $svc = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\LOCAL SERVICE' -LogonType ServiceAccount -RunLevel Limited
    $set = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $act = New-ScheduledTaskAction -Execute $nginx -Argument "-p `"$NginxDir`"" -WorkingDirectory $NginxDir
    Register-ScheduledTask -TaskName $NginxTask -Action $act -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $set -Principal $svc `
        -Description 'nginx in front of the AiSignX server (HTTPS)' -Force | Out-Null
    # The reload watcher runs a copy of this script from the nginx folder, so it survives moving the source folder.
    Copy-Item -Path $PSCommandPath -Destination $Self -Force
    $ract = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Self`" -Reload -DataDir `"$DataDir`" -InstallDir `"$InstallDir`""
    $rtrig = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $rset = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $ReloadTask -Action $ract -Trigger $rtrig -Settings $rset -Principal $svc `
        -Description 'Tells nginx to load a new HTTPS certificate when the file changed' -Force | Out-Null
}

# --- Reload mode: used by the scheduled task, needs no administrator ------------------------------------------------
if ($Reload) {
    $crt = Join-Path $TlsDir 'server.crt'
    if (-not (Test-Path $crt) -or -not (Test-Path (Join-Path $NginxDir 'nginx.exe'))) { exit 0 }
    $now = (Get-FileHash -Path $crt -Algorithm SHA256).Hash
    $was = if (Test-Path $Marker) { (Get-Content $Marker -Raw).Trim() } else { '' }
    if ($now -eq $was) { exit 0 }
    if (-not (Test-NginxConfig $NginxDir)) { exit 1 }
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & (Join-Path $NginxDir 'nginx.exe') -s reload -p $NginxDir 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -eq 0) { Set-Content -Path $Marker -Value $now -Encoding ASCII }
    exit $code
}

# --- Administrator permission ----------------------------------------------------------------------------------------
if (-not (Test-Admin) -and -not $DryRun) {
    Write-Host 'This needs administrator permission; asking Windows for it...' -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    foreach ($k in $PSBoundParameters.Keys) {
        $v = $PSBoundParameters[$k]
        if ($v -is [switch]) { if ($v.IsPresent) { $argList += "-$k" } }
        elseif ($v -is [array]) { $argList += "-$k"; $argList += ('"' + ($v -join ',') + '"') }
        else { $argList += "-$k"; $argList += "`"$v`"" }
    }
    Start-Process -FilePath powershell.exe -ArgumentList $argList -Verb RunAs -Wait
    exit $LASTEXITCODE
}

# --- Uninstall ------------------------------------------------------------------------------------------------------
if ($Uninstall) {
    Step 'Removing nginx (your certificate and the AiSignX server stay)'
    Do-It 'stop and remove the scheduled tasks and the nginx process' {
        foreach ($t in @($NginxTask, $ReloadTask, $UpdateTask)) {
            if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $t -Confirm:$false }
        }
        Get-CimInstance Win32_Process -Filter "Name='nginx.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -like "$NginxRoot*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Get-NetFirewallRule -DisplayName 'AiSignX nginx*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        if (Test-Path $NginxRoot) { Remove-Item $NginxRoot -Recurse -Force }
    }
    Warn "AISIGNX_PROXY_HOPS and AISIGNX_SECURE_COOKIES are still on in ${EnvFile}: turn them off if you go back to plain http, and allow port $Port in the firewall (scripts\install-server.ps1 does it)."
    Ok 'nginx removed.'
    return
}

# --- Check the server is there ----------------------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $Venv 'Scripts\flask.exe')) -or -not (Test-Path $EnvFile)) {
    Fail "The AiSignX server is not installed here ($InstallDir). Run scripts\install-server.ps1 first, or pass -InstallDir and -DataDir."
}

# --- Update ---------------------------------------------------------------------------------------------------------
if ($Update) {
    Step 'Looking for a newer stable nginx'
    $have = Get-InstalledVersion
    if (-not $have) { Fail 'nginx was not installed by this script. Run it without -Update first.' }
    $latest = Get-StableVersion
    Write-Host "    installed: $have    newest stable: $latest"
    if ((Compare-Version $latest $have) -le 0) { Ok 'nginx is up to date.'; return }
    if (-not $Yes -and -not $DryRun) {
        if ((Read-Host "Install nginx $latest now? The web page is unreachable for a few seconds (y/N)") -notmatch '^[yY]') { return }
    }
    Do-It "install nginx $latest, keeping $have for a way back" {
        $old = "$NginxDir.old"
        Stop-ScheduledTask -TaskName $NginxTask -ErrorAction SilentlyContinue
        Get-CimInstance Win32_Process -Filter "Name='nginx.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -like "$NginxRoot*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        if (Test-Path $old) { Remove-Item $old -Recurse -Force }
        $infoBackup = Get-Content $Info -Raw
        Move-Item -Path $NginxDir -Destination $old
        $good = $false
        try {
            Install-NginxFiles $latest $NginxDir
            Write-NginxConfig $NginxDir
            Grant-Folders
            if (-not (Test-NginxConfig $NginxDir)) { throw "nginx $latest does not accept the configuration." }
            Start-ScheduledTask -TaskName $NginxTask
            Start-Sleep -Seconds 3
            $up = Get-NetTCPConnection -LocalPort $HttpsPort -State Listen -ErrorAction SilentlyContinue
            if (-not $up) { throw "nginx $latest did not start listening on port $HttpsPort." }
            $good = $true
        } catch {
            Warn "$($_.Exception.Message) Going back to nginx $have."
            Stop-ScheduledTask -TaskName $NginxTask -ErrorAction SilentlyContinue
            if (Test-Path $NginxDir) { Remove-Item $NginxDir -Recurse -Force }
            Move-Item -Path $old -Destination $NginxDir
            Set-Content -Path $Info -Value $infoBackup -Encoding UTF8
            Start-ScheduledTask -TaskName $NginxTask
        }
        if ($good) { Ok "nginx $latest is running (the old one is kept in $old)." } else { exit 1 }
    }
    return
}

# --- Install --------------------------------------------------------------------------------------------------------
Step "nginx in front of AiSignX (HTTPS on port $HttpsPort)"
if ($DryRun) { Write-Host '    Dry run: nothing will be changed.' }

Step 'Certificate'
Do-It "create $TlsDir" { New-Item -ItemType Directory -Force -Path $TlsDir, $NginxRoot | Out-Null; Grant-Folders }
if ($Cert -or $Key) {
    if (-not ($Cert -and $Key)) { Fail 'Give both -Cert and -Key.' }
    if (-not (Test-Path $Cert) -or -not (Test-Path $Key)) { Fail 'The certificate or key file was not found.' }
    $imp = @('https', 'import', '--cert', $Cert, '--key', $Key)
    if ($KeyPassphrase) { $imp += @('--passphrase', $KeyPassphrase) }
    Do-It 'check and store the certificate you gave' { Invoke-Flask $imp }
} elseif (-not (Test-Path (Join-Path $TlsDir 'server.crt'))) {
    if (-not $Hostname) {
        $Hostname = @($env:COMPUTERNAME.ToLowerInvariant()) + @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -ExpandProperty IPAddress)
        Write-Host "    names for the certificate: $($Hostname -join ', ')"
    }
    $ss = @('https', 'self-signed'); foreach ($h in $Hostname) { $ss += @('--name', $h) }
    Do-It 'make a self-signed certificate' { Invoke-Flask $ss }
    Warn 'A self-signed certificate: browsers warn until it is trusted. Install tls\server.crt as a trusted certificate on your PCs, or let the Android and Windows players pin it (docs\https.md).'
} else {
    Ok 'Using the certificate that is already stored (the HTTPS page shows it).'
}

Step 'nginx'
$latest = if ($DryRun) { '(newest stable)' } else { Get-StableVersion }
if (Test-Path (Join-Path $NginxDir 'nginx.exe')) { Ok "nginx $(Get-InstalledVersion) is already installed (use -Update to look for a newer one)." }
else { Do-It "download nginx $latest from nginx.org" { Install-NginxFiles $latest $NginxDir } }
Do-It 'write nginx.conf and check it' {
    Write-NginxConfig $NginxDir
    Grant-Folders
    if (-not (Test-NginxConfig $NginxDir)) { Fail 'nginx does not accept the configuration (see above). Nothing was started.' }
    Ok 'nginx -t: the configuration is valid'
}

Step 'AiSignX settings'
Do-It 'AISIGNX_PROXY_HOPS=1 and AISIGNX_SECURE_COOKIES=1' {
    Set-EnvValue 'AISIGNX_PROXY_HOPS' '1'
    Set-EnvValue 'AISIGNX_SECURE_COOKIES' '1'
    Set-EnvValue 'AISIGNX_TLS_DIR' $TlsDir
}
Do-It 'restart the AiSignX server' {
    if (Get-ScheduledTask -TaskName $ServerTask -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $ServerTask -ErrorAction SilentlyContinue
        Get-CimInstance Win32_Process -Filter "Name='flask.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -like "$Venv*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-ScheduledTask -TaskName $ServerTask
    } else { Warn "The scheduled task '$ServerTask' was not found: restart AiSignX yourself." }
}

Step 'Start-up and firewall'
Do-It "register '$NginxTask' and '$ReloadTask'" { Register-NginxTasks }
if ($AutoUpdate) {
    Do-It "register '$UpdateTask' (checks nginx.org every Sunday at 03:00 and installs a newer stable version)" {
        $uact = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Self`" -Update -Yes -DataDir `"$DataDir`" -InstallDir `"$InstallDir`""
        $utrig = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
        $uprin = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName $UpdateTask -Action $uact -Trigger $utrig -Principal $uprin -Description 'Updates nginx for AiSignX' -Force | Out-Null
    }
}
if (-not $NoFirewall) {
    Do-It "allow inbound TCP $HttpPort and $HttpsPort (private and domain networks); close plain port $Port to other computers" {
        Get-NetFirewallRule -DisplayName 'AiSignX nginx*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName 'AiSignX nginx HTTPS' -Direction Inbound -Protocol TCP -LocalPort $HttpsPort -Action Allow -Profile Private, Domain | Out-Null
        if (-not $NoRedirect) { New-NetFirewallRule -DisplayName 'AiSignX nginx HTTP' -Direction Inbound -Protocol TCP -LocalPort $HttpPort -Action Allow -Profile Private, Domain | Out-Null }
        Get-NetFirewallRule -DisplayName $OldRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
}
Do-It 'start nginx' {
    Start-ScheduledTask -TaskName $NginxTask
    Start-Sleep -Seconds 3
    if (-not (Get-NetTCPConnection -LocalPort $HttpsPort -State Listen -ErrorAction SilentlyContinue)) {
        Fail "nginx is not listening on port $HttpsPort. Look at $NginxDir\logs\error.log (another program may be using the port)."
    }
    Set-Content -Path $Marker -Value (Get-FileHash -Path (Join-Path $TlsDir 'server.crt') -Algorithm SHA256).Hash -Encoding ASCII
}

Step 'Done'
$names = @($Hostname); if (-not $names) { $names = @('localhost') }
Ok "Open https://$($names[0])$(if ($HttpsPort -ne 443) { ":$HttpsPort" })/ in a browser."
Write-Host "    Certificate: $TlsDir    nginx: $NginxDir    nginx log: $NginxDir\logs\error.log"
Write-Host '    Change the certificate on the admin page System > HTTPS: nginx loads it within 5 minutes.'
Write-Host '    Screens that were set up with an http:// address must be set up again with the https:// one.'
