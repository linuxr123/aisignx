# Stop AISignX / Electron processes that lock dist\win-unpacked\app.asar, then clear build output.
# Builds use dist-build/ (see package.json). Legacy dist/ is cleaned when possible.

param(
    [string]$ElectronProjectDir = (Split-Path $PSScriptRoot -Parent),
    [switch]$KillAllElectron
)

$ErrorActionPreference = 'Continue'
$BuildOutputDir = 'dist-build'

function Write-PrebuildMsg([string]$msg, [string]$color = 'Gray') {
    Write-Host $msg -ForegroundColor $color
}

function Test-ProcessRelatesToAisignx {
    param([int]$ProcessId, [string]$ProjectDirNorm)
    try {
        $wmi = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        $cmd = ($wmi.CommandLine + '').ToLowerInvariant()
        $exe = ($wmi.ExecutablePath + '').ToLowerInvariant()
        if ($exe -like '*\aisignx-player\*' -or $exe -like '*\electron-client\*' -or
            $exe -like '*\win-unpacked\*') { return $true }
        if ($cmd -match 'aisignx' -or $cmd -match 'electron-client' -or
            $cmd -match [regex]::Escape($ProjectDirNorm)) { return $true }
    } catch { }
    return $false
}

function Stop-ProcessForce {
    param([int]$ProcessId, [string]$Label)
    Write-PrebuildMsg "[*] Stopping $Label (PID $ProcessId)..." 'Yellow'
    try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch { }
    try { & taskkill.exe /F /PID $ProcessId /T 2>$null | Out-Null } catch { }
}

function Stop-ElectronBuildBlockers {
    param([string]$ProjectDir, [bool]$KillAllElectron)
    $projectNorm = (Resolve-Path -LiteralPath $ProjectDir).Path.ToLowerInvariant()
    $seen = @{}

    & taskkill.exe /F /IM 'AISignX Player.exe' /T 2>$null | Out-Null
    if ($KillAllElectron) {
        Write-PrebuildMsg '[*] -KillAllElectron: ending all electron.exe.' 'Yellow'
        & taskkill.exe /F /IM 'electron.exe' /T 2>$null | Out-Null
    }

    foreach ($procName in @('AISignX Player', 'electron', 'node')) {
        Get-Process -Name $procName -ErrorAction SilentlyContinue | ForEach-Object {
            if ($seen.ContainsKey($_.Id)) { return }
            $kill = ($procName -eq 'AISignX Player') -or
                ($procName -eq 'electron' -and $KillAllElectron) -or
                (($procName -in @('electron', 'node')) -and
                    (Test-ProcessRelatesToAisignx -ProcessId $_.Id -ProjectDirNorm $projectNorm))
            if ($kill) {
                $seen[$_.Id] = $true
                Stop-ProcessForce -ProcessId $_.Id -Label $_.ProcessName
            }
        }
    }
    foreach ($name in @('app-builder', 'app-builder-bin')) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            if ($seen.ContainsKey($_.Id)) { return }
            $seen[$_.Id] = $true
            Stop-ProcessForce -ProcessId $_.Id -Label $name
        }
    }
    Start-Sleep -Milliseconds 500
}

function Invoke-RobocopyPurge {
    param([string]$TargetDir)
    $empty = Join-Path ([System.IO.Path]::GetTempPath()) ("aisignx-empty-" + [guid]::NewGuid().ToString('n'))
    try {
        New-Item -ItemType Directory -Path $empty -Force | Out-Null
        $p = Start-Process -FilePath 'robocopy.exe' -ArgumentList @(
            "`"$empty`"", "`"$TargetDir`"", '/MIR', '/R:0', '/W:0', '/NFL', '/NDL', '/NJH', '/NJS', '/nc', '/ns', '/np'
        ) -PassThru -WindowStyle Hidden -Wait
        return ($p.ExitCode -le 7)
    } catch { return $false }
    finally { Remove-Item -LiteralPath $empty -Force -ErrorAction SilentlyContinue }
}

function Remove-DirectoryForce {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        return $true
    } catch { }
    & cmd.exe /c "rmdir /s /q `"$Path`"" 2>$null | Out-Null
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    if (Invoke-RobocopyPurge -TargetDir $Path) {
        & cmd.exe /c "rmdir /s /q `"$Path`"" 2>$null | Out-Null
        if (-not (Test-Path -LiteralPath $Path)) { return $true }
    }
    return $false
}

function Move-DirAside {
    param([string]$DirPath, [string]$Label)
    $parent = Split-Path -Parent $DirPath
    $dest = Join-Path $parent ("${Label}._delete_" + (Get-Date -Format 'yyyyMMddHHmmss'))
    if (Test-Path -LiteralPath $dest) { Remove-DirectoryForce -Path $dest | Out-Null }
    try {
        Move-Item -LiteralPath $DirPath -Destination $dest -ErrorAction Stop
        Write-PrebuildMsg "[OK] Moved locked $Label -> $(Split-Path -Leaf $dest)" 'Green'
        Remove-DirectoryForce -Path $dest | Out-Null
        return $true
    } catch { return $false }
}

function Clear-OneOutputDir {
    param(
        [string]$DirPath,
        [string]$Label,
        [bool]$Required,
        [bool]$KillAllElectron
    )
    if (-not (Test-Path -LiteralPath $DirPath)) { return $true }

    Stop-ElectronBuildBlockers -ProjectDir (Split-Path $DirPath -Parent) -KillAllElectron:$KillAllElectron

    if (Remove-DirectoryForce -Path $DirPath) {
        Write-PrebuildMsg "[OK] Removed $Label." 'Green'
        return $true
    }

    if (Move-DirAside -DirPath $DirPath -Label $Label) { return $true }

    if ($Required) { return $false }

    Write-PrebuildMsg "[WARN] Legacy $Label is locked (app.asar in use). Builds use dist-build/ now - you can ignore it or delete after reboot." 'Yellow'
    Write-PrebuildMsg "       Path: $DirPath" 'DarkGray'
    return $true
}

function Invoke-PrebuildElectronClean {
    param([string]$ElectronProjectDir, [bool]$KillAllElectron = $false)
    Write-PrebuildMsg '[*] Preparing Electron build (stop players, clear output folders)...' 'Yellow'

    Get-ChildItem -LiteralPath $ElectronProjectDir -Directory -Filter '*._delete_*' -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-PrebuildMsg "[*] Removing $($_.Name) ..." 'Yellow'
            Remove-DirectoryForce -Path $_.FullName | Out-Null
        }

    $buildDir = Join-Path $ElectronProjectDir $BuildOutputDir
    $legacyDir = Join-Path $ElectronProjectDir 'dist'

    $okBuild = Clear-OneOutputDir -DirPath $buildDir -Label $BuildOutputDir -Required $true -KillAllElectron:$KillAllElectron
    if (-not $okBuild) {
        Stop-ElectronBuildBlockers -ProjectDir $ElectronProjectDir -KillAllElectron:$true
        Start-Sleep -Seconds 1
        $okBuild = Clear-OneOutputDir -DirPath $buildDir -Label $BuildOutputDir -Required $true -KillAllElectron:$true
    }
    if (-not $okBuild) {
        Write-PrebuildMsg '[FAIL] Could not clear dist-build/.' 'Red'
        Write-PrebuildMsg '  Close AISignX Player, end electron.exe for this project, close Explorer on dist-build\.' 'Yellow'
        Write-PrebuildMsg '  Try: powershell -File scripts\prebuild_clean.ps1 -KillAllElectron' 'Yellow'
        Write-PrebuildMsg '  Or reboot, then build again.' 'Yellow'
        return $false
    }

    Clear-OneOutputDir -DirPath $legacyDir -Label 'dist' -Required $false -KillAllElectron:$KillAllElectron | Out-Null
    return $true
}

if ($MyInvocation.InvocationName -ne '.') {
    $ok = Invoke-PrebuildElectronClean -ElectronProjectDir $ElectronProjectDir `
        -KillAllElectron:($KillAllElectron.IsPresent)
    if (-not $ok) { exit 1 }
}
