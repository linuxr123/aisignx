<#
.SYNOPSIS
  Builds the add-in's installer package: dist\AiSignX-PowerPoint-Addin-<version>\ and a .zip next to it.

.DESCRIPTION
  1. publishes the signed ClickOnce/VSTO package (setup.exe, .vsto, Application Files\),
  2. exports the public signing certificate (AiSignX-addin.cer),
  3. compiles "Install AiSignX for PowerPoint.exe" (installer\Setup.cs, with the AiSignX icon) and signs it,
  4. writes INSTALL.txt and zips everything.

  Needs Visual Studio with the Office/SharePoint development workload and the signing certificate made by
  tools\New-DevCert.ps1 (its thumbprint is read from AiSignXPowerPoint\signing.local.props).
  Run from any folder:  powershell -ExecutionPolicy Bypass -File clients\powerpoint-addin\tools\Build-Installer.ps1
#>
[CmdletBinding()]
param([string]$Configuration = 'Release')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$proj = Join-Path $root 'AiSignXPowerPoint\AiSignXPowerPoint.csproj'
$props = Join-Path $root 'AiSignXPowerPoint\signing.local.props'
if (-not (Test-Path $props)) { throw "No signing certificate yet. Run tools\New-DevCert.ps1 first." }
$thumb = ([xml](Get-Content $props)).Project.PropertyGroup.ManifestCertificateThumbprint
$cert = Get-Item "Cert:\CurrentUser\My\$thumb"
$version = ([xml](Get-Content $proj)).Project.PropertyGroup.ApplicationVersion | Where-Object { $_ } | Select-Object -First 1
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Workload.Office -property installationPath
if (-not $vs) { throw "Visual Studio with the 'Office/SharePoint development' workload was not found." }
$msbuild = Join-Path $vs 'MSBuild\Current\Bin\MSBuild.exe'
$csc = Join-Path $vs 'MSBuild\Current\Bin\Roslyn\csc.exe'

$name = "AiSignX-PowerPoint-Addin-$version"
$dist = Join-Path $root 'dist'
$out = Join-Path $dist $name
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "Publishing $version ..."
& $msbuild $proj '/t:Publish' "/p:Configuration=$Configuration" "/p:PublishDir=$out\" /v:quiet /nologo /clp:ErrorsOnly
if ($LASTEXITCODE -ne 0) { throw "MSBuild failed." }

Export-Certificate -Cert $cert -FilePath (Join-Path $out 'AiSignX-addin.cer') | Out-Null

Write-Host "Building the installer launcher ..."
$exe = Join-Path $out 'Install AiSignX for PowerPoint.exe'
& $csc /nologo /target:winexe "/out:$exe" "/win32icon:$(Join-Path $root 'AiSignXPowerPoint\Resources\AiSignX.ico')" `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll (Join-Path $root 'installer\Setup.cs')
if ($LASTEXITCODE -ne 0) { throw "csc failed." }
$sig = Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -HashAlgorithm SHA256
if ($sig.Status -ne 'Valid' -and $sig.Status -ne 'UnknownError') { Write-Warning "Signing the launcher: $($sig.Status)" }

@"
AiSignX for PowerPoint $version

TO INSTALL
  1. Unzip this whole folder (do not run anything from inside the zip).
  2. Close PowerPoint.
  3. Double-click "Install AiSignX for PowerPoint.exe". Click OK, then Yes when Windows asks
     about the certificate, then Install.
  4. Open PowerPoint. The AiSignX tab is on the ribbon. The first time, enter your AiSignX server
     address and your API token (AiSignX: your name > My API tokens > media:write).

If the launcher does not run: double-click AiSignX-addin.cer > Install Certificate > Current User >
"Place all certificates in the following store" > Trusted Publishers (repeat for Trusted Root
Certification Authorities), then run setup.exe.

Needs Windows with PowerPoint 2016 or newer (desktop). Full guide: docs/powerpoint-addin.md in the
AiSignX repository.
"@ | Set-Content -Path (Join-Path $out 'INSTALL.txt') -Encoding UTF8

$zip = Join-Path $dist "$name.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $out '*') -DestinationPath $zip
Write-Host ("Done: {0}  ({1:N0} KB)" -f $zip, ((Get-Item $zip).Length / 1KB))
