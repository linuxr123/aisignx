<#
.SYNOPSIS
  Creates a self-signed code-signing certificate for the add-in's ClickOnce/VSTO manifests and
  writes AiSignXPowerPoint\signing.local.props (gitignored) so the project signs with it.

.DESCRIPTION
  Run once per developer PC. The certificate lives in Cert:\CurrentUser\My; nothing secret is
  written to the repository. A self-signed certificate is fine for your own PCs, but every PC that
  installs the add-in must trust it (see the README, "Install"). For wider distribution use a
  certificate from a real code-signing CA instead and put its thumbprint in signing.local.props.
#>
[CmdletBinding()]
param(
    [string]$Subject = 'CN=AiSignX for PowerPoint',
    [int]$Years = 3
)
$ErrorActionPreference = 'Stop'
$cert = New-SelfSignedCertificate -Subject $Subject -Type CodeSigningCert -KeyUsage DigitalSignature `
    -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears($Years)
$props = Join-Path $PSScriptRoot '..\AiSignXPowerPoint\signing.local.props'
@"
<?xml version="1.0" encoding="utf-8"?>
<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <ManifestCertificateThumbprint>$($cert.Thumbprint)</ManifestCertificateThumbprint>
  </PropertyGroup>
</Project>
"@ | Set-Content -Path $props -Encoding UTF8
Write-Host "Created certificate $($cert.Thumbprint) and wrote $props"
Write-Host "To let another PC install the add-in, export the public part and trust it there:"
Write-Host "  Export-Certificate -Cert Cert:\CurrentUser\My\$($cert.Thumbprint) -FilePath AiSignX-addin.cer"
