<#
.SYNOPSIS
  Installs the AiSignX server on Windows so that it starts with the computer.

.DESCRIPTION
  Copies the server to Program Files, makes a Python environment, keeps your data separate in
  ProgramData, and registers a Scheduled Task that runs the server at start-up as the low-privilege
  "LOCAL SERVICE" account and restarts it if it stops. It also opens the port in the Windows Firewall
  for private and domain networks. Run it again to upgrade (the database is backed up first). Your data
  is never touched by an upgrade.

  Run it from the AiSignX folder (the one that contains server\), from an elevated PowerShell, or just
  run it: it asks for administrator permission.

    powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1 -Port 8080
    powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1 -Uninstall            # keeps your data
    powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1 -Uninstall -Purge     # deletes it too

  Docker users do not need this: see docs\docker.md.
#>
[CmdletBinding()]
param(
    [int]$Port = 5000,
    [string]$Source = '',
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'AiSignX'),
    [string]$DataDir = (Join-Path $env:ProgramData 'AiSignX'),
    [switch]$Uninstall,
    [switch]$Purge,
    [switch]$NoStart,
    [switch]$NoFirewall,
    # Copy, build the environment and migrate the database, but register nothing (no task, no firewall,
    # no permission changes). Used to test the installer; also handy to prepare a machine.
    [switch]$NoService,
    # Show what would be done and change nothing.
    [switch]$DryRun,
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'
$TaskName = 'AiSignX Server'
$RuleName = 'AiSignX server'
$App = Join-Path $InstallDir 'app'
$Venv = Join-Path $InstallDir 'venv'
$EnvFile = Join-Path $DataDir 'aisignx.env'
$LogDir = Join-Path $DataDir 'logs'

function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "    $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    $t" -ForegroundColor Yellow }
function Do-It([string]$what, [scriptblock]$action) {
    if ($DryRun) { Write-Host "    [dry run] $what" -ForegroundColor DarkGray } else { & $action }
}

if ($Port -lt 1 -or $Port -gt 65535) { throw '-Port must be a number from 1 to 65535.' }

# --- Administrator permission (only when we change the system) ----------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $DryRun -and -not $NoService) {
    Write-Host 'This needs administrator permission; asking Windows for it...' -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") + $MyInvocation.UnboundArguments
    foreach ($k in $PSBoundParameters.Keys) {
        $v = $PSBoundParameters[$k]
        if ($v -is [switch]) { if ($v.IsPresent) { $argList += "-$k" } } else { $argList += "-$k"; $argList += "`"$v`"" }
    }
    Start-Process -FilePath powershell.exe -ArgumentList $argList -Verb RunAs -Wait
    exit $LASTEXITCODE
}

# --- Uninstall -----------------------------------------------------------------------------------
if ($Uninstall) {
    Step 'Removing the AiSignX service'
    Do-It 'stop and remove the scheduled task' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Get-CimInstance Win32_Process -Filter "Name='flask.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -like "$Venv*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    Do-It "remove $InstallDir" { if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force } }
    if ($Purge) {
        Do-It "delete the data in $DataDir" { if (Test-Path $DataDir) { Remove-Item $DataDir -Recurse -Force } }
        Ok 'Removed the program, the service and all data.'
    } else {
        Ok "Removed the program and the service. Your data is still in $DataDir (use -Purge to delete it)."
    }
    return
}

# --- Source ----------------------------------------------------------------------------------------
if (-not $Source) { $Source = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$serverSrc = Join-Path $Source 'server'
if (-not (Test-Path (Join-Path $serverSrc 'wsgi.py')) -or -not (Test-Path (Join-Path $serverSrc 'requirements.txt'))) {
    throw "$Source does not look like an AiSignX folder (no server\wsgi.py)."
}
$version = ''
$m = Select-String -Path (Join-Path $serverSrc 'aisignx\__init__.py') -Pattern '^__version__\s*=\s*"(.*)"' | Select-Object -First 1
if ($m) { $version = $m.Matches[0].Groups[1].Value }
$upgrade = Test-Path $App
Step ("AiSignX ${version}: " + $(if ($upgrade) { 'upgrading' } else { 'installing' }) + " into $InstallDir (data in $DataDir)")

# --- Python 3.10 or newer ----------------------------------------------------------------------------
function Find-Python {
    # Native commands that print to stderr (py.exe: "No suitable Python runtime found") must not abort us.
    $oldPref = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { return Find-PythonInner } finally { $ErrorActionPreference = $oldPref }
}
function Find-PythonInner {
    $candidates = @()
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            $p = & py.exe "-$v" -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p.Trim() }
        }
    }
    foreach ($n in 'python.exe', 'python3.exe') {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { $candidates += $c.Source }
    }
    foreach ($p in $candidates | Select-Object -Unique) {
        if ($p -like '*\WindowsApps\*') { continue }   # the Microsoft Store stub is not a real Python
        $ok = & $p -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)' 2>$null
        if ($ok -eq '1') { return $p }
    }
    return $null
}
function Test-Machine-Python([string]$py) {
    $oldPref = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { return Test-Machine-PythonInner $py } finally { $ErrorActionPreference = $oldPref }
}
function Test-Machine-PythonInner([string]$py) {
    # The service runs as LOCAL SERVICE, which cannot read another user's profile folder, so the base
    # Python must live outside C:\Users (Program Files, C:\Python312, ...).
    $base = & $py -c 'import sys; print(sys.base_prefix)'
    return -not ($base -like "$env:SystemDrive\Users\*")
}
Step 'Python 3.10 or newer'
$py = Find-Python
if ($py -and -not $NoService -and -not (Test-Machine-Python $py)) {
    Warn "Found $py, but it is installed for one user only. The service cannot use it."
    $py = $null
}
if (-not $py) {
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        $go = $Yes -or $DryRun -or ((Read-Host '    Install Python 3.12 for all users with winget? [Y/n]') -match '^(|y|yes)$')
        if ($go) {
            Do-It 'winget install Python.Python.3.12 --scope machine' {
                & winget.exe install -e --id Python.Python.3.12 --scope machine --accept-package-agreements --accept-source-agreements
                $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
            }
            $py = Find-Python
        }
    }
    if (-not $py -and -not $DryRun) { throw 'Python 3.10 or newer (installed for all users) was not found. Install it from https://www.python.org/downloads/ (tick "Install for all users" and "Add to PATH") and run this again.' }
}
if ($py) { Ok "Using $py" }

# --- ffmpeg and a browser (optional) --------------------------------------------------------------------
Step 'ffmpeg (video length and thumbnails) and a browser (web page previews)'
if (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) { Ok 'ffmpeg found' } else {
    Warn 'ffmpeg is missing: videos upload without a length or thumbnail.'
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        if ($Yes -or ((Read-Host '    Install ffmpeg with winget? [Y/n]') -match '^(|y|yes)$')) {
            Do-It 'winget install Gyan.FFmpeg' { & winget.exe install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements }
        }
    }
}
$haveBrowser = (Test-Path "$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe") -or (Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe")
if ($haveBrowser) { Ok 'Edge or Chrome found (web page previews work)' } else { Warn 'No Edge or Chrome found: web pages get a title card instead of a screenshot.' }

# --- Stop the old service, back up, copy ---------------------------------------------------------------------
Step 'Program files'
if (-not $NoService) {
    Do-It 'stop the running server' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
        Get-CimInstance Win32_Process -Filter "Name='flask.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -like "$Venv*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
}
Do-It "create $InstallDir and $DataDir" {
    New-Item -ItemType Directory -Force -Path $InstallDir, (Join-Path $DataDir 'instance'), (Join-Path $DataDir 'uploads'), (Join-Path $DataDir 'backups'), $LogDir | Out-Null
}
function Invoke-Flask([string[]]$flaskArgs) {
    # Run flask with the server's settings from the env file.
    $saved = @{}
    foreach ($line in Get-Content $EnvFile -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$' -and -not $line.TrimStart().StartsWith('#')) {
            $saved[$Matches[1]] = [Environment]::GetEnvironmentVariable($Matches[1], 'Process')
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process')
        }
    }
    # flask/alembic write progress to stderr; that is not an error. Judge by the exit code.
    $oldPref = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        Push-Location $App
        $out = & (Join-Path $Venv 'Scripts\flask.exe') --app wsgi @flaskArgs 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
        $out
        if ($code -ne 0) { throw "flask $($flaskArgs -join ' ') failed (exit code $code)." }
    } finally {
        $ErrorActionPreference = $oldPref
        Pop-Location
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
    }
}
if ($upgrade -and -not $DryRun -and (Test-Path (Join-Path $Venv 'Scripts\flask.exe')) -and (Test-Path (Join-Path $DataDir 'instance\aisignx.db'))) {
    Step 'Backing up the database before upgrading'
    try { Invoke-Flask @('backup', 'create') | ForEach-Object { Write-Host "    $_" } } catch { Warn "Could not make a backup ($($_.Exception.Message)); continuing. Copy $DataDir\instance by hand if unsure." }
}
Do-It "copy the server to $App" {
    $new = "$App.new"
    if (Test-Path $new) { Remove-Item $new -Recurse -Force }
    # robocopy exit codes below 8 are success.
    & robocopy.exe $serverSrc $new /E /NFL /NDL /NJH /NJS /NP /XD .venv instance uploads backups logs plugin-cache __pycache__ tests .pytest_cache /XF *.pyc *.db *.db-* .env | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the program failed (robocopy exit code $LASTEXITCODE)." }
    if (Test-Path "$App.prev") { Remove-Item "$App.prev" -Recurse -Force }
    if (Test-Path $App) { Rename-Item $App "$App.prev" }
    Rename-Item $new $App
    $global:LASTEXITCODE = 0
}
Ok "Copied the server to $App"

Step 'Python environment'
Do-It "create $Venv and install the packages" {
    if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) { & $py -m venv $Venv; if ($LASTEXITCODE) { throw 'Could not create the Python environment.' } }
    & (Join-Path $Venv 'Scripts\python.exe') -m pip install --quiet --upgrade pip
    & (Join-Path $Venv 'Scripts\python.exe') -m pip install --quiet -r (Join-Path $App 'requirements.txt')
    if ($LASTEXITCODE) { throw 'Installing the Python packages failed (is the internet reachable?).' }
}
Ok 'Packages installed'

# --- Settings and data permissions ------------------------------------------------------------------------------
Step "Settings file $EnvFile"
Do-It 'write the settings file if it does not exist' {
    if (-not (Test-Path $EnvFile)) {
        @"
# AiSignX server settings (see docs\deployment.md for every option). Restart the service after editing:
#   Restart-ScheduledTask 'AiSignX Server'   (or stop and start it in Task Scheduler)
AISIGNX_INSTANCE_DIR=$DataDir\instance
AISIGNX_UPLOAD_DIR=$DataDir\uploads
AISIGNX_BACKUP_DIR=$DataDir\backups
AISIGNX_LOG_DIR=$LogDir
# AISIGNX_SECURE_COOKIES=1        # turn on when you serve over HTTPS (docs\https.md)
# AISIGNX_MAX_UPLOAD_MB=2048
"@ | Set-Content -Path $EnvFile -Encoding ASCII
        Write-Host '    Created' -ForegroundColor Green
    } else { Write-Host '    Kept your existing settings' -ForegroundColor Green }
}
if (-not $NoService) {
    Do-It 'let LOCAL SERVICE read the program and write the data' {
        & icacls.exe $InstallDir /grant '*S-1-5-19:(OI)(CI)RX' /T /Q | Out-Null
        & icacls.exe $DataDir /grant '*S-1-5-19:(OI)(CI)M' /T /Q | Out-Null
        & icacls.exe $DataDir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' /Q | Out-Null
    }
}

Step 'Database'
Do-It 'apply database migrations' { Invoke-Flask @('db', 'upgrade') 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" } }
Ok 'Database is up to date'

if ($NoService) {
    Step 'Done (no service registered, as asked)'
    Ok "Start it by hand: cd `"$App`"; & `"$Venv\Scripts\flask.exe`" --app wsgi serve --port $Port"
    return
}

# --- The service: a Scheduled Task at start-up ----------------------------------------------------------------------------
Step 'Starting with the system'
$runner = Join-Path $InstallDir 'run-server.ps1'
Do-It "write $runner" {
    @"
# Started by the Scheduled Task '$TaskName' at boot. Loads the settings, migrates the database, runs the server.
`$ErrorActionPreference = 'Stop'
Get-Content '$EnvFile' | ForEach-Object {
    if (`$_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$' -and -not `$_.TrimStart().StartsWith('#')) {
        [Environment]::SetEnvironmentVariable(`$Matches[1], `$Matches[2].Trim('"'), 'Process')
    }
}
`$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine')
Set-Location '$App'
& '$Venv\Scripts\flask.exe' --app wsgi db upgrade *>> '$LogDir\service.log'
& '$Venv\Scripts\flask.exe' --app wsgi serve --host 0.0.0.0 --port $Port *>> '$LogDir\service.log'
exit `$LASTEXITCODE
"@ | Set-Content -Path $runner -Encoding UTF8
}
Do-It "register the scheduled task '$TaskName' (runs at start-up as LOCAL SERVICE, restarts on failure)" {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\LOCAL SERVICE' -LogonType ServiceAccount -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal `
        -Description 'AiSignX digital signage server' -Force | Out-Null
}
if (-not $NoFirewall) {
    Do-It "allow inbound TCP $Port on private and domain networks" {
        Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private, Domain | Out-Null
    }
}
if (-not $NoStart) {
    Do-It 'start the server' { Start-ScheduledTask -TaskName $TaskName }
    if (-not $DryRun) {
        Write-Host -NoNewline '    waiting for the server'
        $up = $false
        for ($i = 0; $i -lt 90 -and -not $up; $i++) {
            try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/login" -UseBasicParsing -TimeoutSec 3; $up = ($r.StatusCode -eq 200) } catch { Start-Sleep -Seconds 1; Write-Host -NoNewline '.' }
        }
        Write-Host
        if (-not $up) { Warn "The server did not answer within 90 seconds. Look at: $LogDir\service.log"; exit 1 }
    }
}

Step 'Done'
Ok 'AiSignX is installed and will start with this computer.'
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 3 -ExpandProperty IPAddress
foreach ($ip in $ips) { Ok "Open http://${ip}:$Port in a browser to finish setup (create the admin account)." }
if (-not $ips) { Ok "Open http://localhost:$Port in a browser to finish setup (create the admin account)." }
Write-Host "    Settings: $EnvFile    Data: $DataDir    Log: $LogDir\service.log"
if ($upgrade) { Write-Host "    The previous version is kept in $App.prev (delete it when you are happy)." }
