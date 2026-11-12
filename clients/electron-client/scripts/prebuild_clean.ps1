# Stops processes that lock the build output (the installed or unpacked AiSignX Player,
# electron.exe started from this project, app-builder), then removes dist-build\.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\prebuild_clean.ps1 [-KillAllElectron]
param(
    [string]$ProjectDir = (Split-Path $PSScriptRoot -Parent),
    [switch]$KillAllElectron
)

$ErrorActionPreference = 'Continue'
$OutputDir = Join-Path $ProjectDir 'dist-build'
$projectNorm = (Resolve-Path -LiteralPath $ProjectDir).Path.ToLowerInvariant()

function Test-ProjectProcess([int]$ProcessId) {
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        $text = (($p.CommandLine + ' ' + $p.ExecutablePath) + '').ToLowerInvariant()
        return $text.Contains($projectNorm) -or $text.Contains('\win-unpacked\')
    } catch { return $false }
}

function Stop-Blockers {
    foreach ($name in @('AiSignX Player', 'electron', 'app-builder', 'app-builder-bin')) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            $kill = ($name -ne 'electron') -or $KillAllElectron -or (Test-ProjectProcess $_.Id)
            if ($kill) {
                Write-Host "[*] Stopping $($_.ProcessName) (PID $($_.Id))" -ForegroundColor Yellow
                try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { }
            }
        }
    }
    Start-Sleep -Milliseconds 500
}

function Remove-Output {
    if (-not (Test-Path -LiteralPath $OutputDir)) { return $true }
    try {
        Remove-Item -LiteralPath $OutputDir -Recurse -Force -ErrorAction Stop
        return $true
    } catch { }
    & cmd.exe /c "rmdir /s /q `"$OutputDir`"" 2>$null | Out-Null
    return -not (Test-Path -LiteralPath $OutputDir)
}

Write-Host '[*] Preparing the Electron build (stop players, clear dist-build)...' -ForegroundColor Yellow
Stop-Blockers
if (-not (Remove-Output)) {
    Start-Sleep -Seconds 1
    Stop-Blockers
    if (-not (Remove-Output)) {
        Write-Host '[FAIL] Could not remove dist-build. Close AiSignX Player and any Explorer window on dist-build, then retry.' -ForegroundColor Red
        Write-Host '       Or run: scripts\prebuild_clean.ps1 -KillAllElectron' -ForegroundColor Yellow
        exit 1
    }
}
Write-Host '[OK] dist-build is clean.' -ForegroundColor Green
exit 0
