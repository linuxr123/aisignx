<#
.SYNOPSIS
  One command to install the AiSignX server on Windows: downloads it from GitHub, then runs install-server.ps1.

.DESCRIPTION
  Run it from an elevated PowerShell:

    irm https://raw.githubusercontent.com/linuxr123/aisignx/v3/scripts/get-aisignx.ps1 | iex

  or save it and pass options (they go to install-server.ps1):

    powershell -ExecutionPolicy Bypass -File get-aisignx.ps1 -Https -Port 8080
    powershell -ExecutionPolicy Bypass -File get-aisignx.ps1 -Ref v3.0.0     # a release tag instead of the branch

  It downloads the zip over HTTPS, unpacks it in a temporary folder, runs the installer, and removes the folder.
  If the release has a .sha256 file next to the zip, the download is checked against it.
#>
[CmdletBinding()]
param(
    [string]$Ref = 'v3',
    [string]$Repo = 'linuxr123/aisignx',
    [int]$Port = 5000,
    [switch]$Https,
    [switch]$Yes,
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($Repo -notmatch '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$' -or $Ref -notmatch '^[A-Za-z0-9._/-]+$') { throw 'Repo or Ref has characters that are not allowed.' }

$work = Join-Path ([IO.Path]::GetTempPath()) ('aisignx-get-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $zip = Join-Path $work 'aisignx.zip'
    $url = "https://github.com/$Repo/archive/$Ref.zip"
    Write-Host "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

    try {
        $expected = ((Invoke-WebRequest -Uri "$url.sha256" -UseBasicParsing).Content -split '\s+')[0].Trim().ToLower()
        $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
        if ($expected -ne $actual) { throw 'The download does not match its published checksum. Nothing was installed.' }
        Write-Host 'Checksum matches.'
    } catch [System.Net.WebException] { Write-Host 'No published checksum for this download (normal for a branch).' }

    Expand-Archive -Path $zip -DestinationPath $work -Force
    $root = Get-ChildItem -Path $work -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'scripts\install-server.ps1') } | Select-Object -First 1
    if (-not $root) { throw 'The download does not contain scripts\install-server.ps1.' }

    $args2 = @('-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root.FullName 'scripts\install-server.ps1'), '-Port', $Port)
    if ($Https) { $args2 += '-Https' }
    if ($Yes) { $args2 += '-Yes' }
    if ($NoStart) { $args2 += '-NoStart' }
    & powershell.exe @args2
    if ($LASTEXITCODE) { throw "The installer stopped with code $LASTEXITCODE." }
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
