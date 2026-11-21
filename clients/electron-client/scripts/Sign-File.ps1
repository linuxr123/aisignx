# Signs ONE file with the code-signing certificate in the current user's store and checks the result.
#   .\Sign-File.ps1 -Path <file> [-Thumbprint <40 hex>] [-TimestampUrl http://timestamp.digicert.com]
# The thumbprint defaults to the environment variable AISIGNX_SIGN_THUMBPRINT (never put it in a tracked file).
# Refuses a certificate that is missing, has no private key, or has no code-signing purpose. After signing, the file is
# read back: the signer's thumbprint must equal the one asked for and the signature must not be NotSigned/HashMismatch.
# (A self-signed certificate reads back as UnknownError until it is trusted on this PC; that is expected here, the
# agent checks it with the pinned thumbprint on the PC that installs.)
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$Thumbprint = $env:AISIGNX_SIGN_THUMBPRINT,
    [string]$TimestampUrl = $env:AISIGNX_SIGN_TIMESTAMP
)
$ErrorActionPreference = 'Stop'
if ($Thumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'Give -Thumbprint (40 hex) or set AISIGNX_SIGN_THUMBPRINT.' }
$Thumbprint = $Thumbprint.ToUpperInvariant()
if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File not found: $Path" }
$cert = Get-Item -LiteralPath ('Cert:\CurrentUser\My\' + $Thumbprint) -ErrorAction SilentlyContinue
if (-not $cert) { throw 'The certificate is not in Cert:\CurrentUser\My on this PC.' }
if (-not $cert.HasPrivateKey) { throw 'The certificate has no private key here.' }
if (($cert.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId }) -notcontains '1.3.6.1.5.5.7.3.3') { throw 'The certificate is not a code-signing certificate.' }
# Already signed with this certificate (electron-builder can ask for the same helper file twice at the same moment): nothing to do.
$existing = Get-AuthenticodeSignature -LiteralPath $Path
if ($existing.Status -eq 'Valid' -and $existing.SignerCertificate -and $existing.SignerCertificate.Thumbprint -eq $Thumbprint -and $existing.TimeStamperCertificate) {
    Write-Host ('Signed ' + (Split-Path -Leaf $Path) + ' (already signed and timestamped)')
    return
}
$before = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
$args2 = @{ FilePath = $Path; Certificate = $cert; HashAlgorithm = 'SHA256' }
if ($TimestampUrl) { $args2.TimestampServer = $TimestampUrl }
$sig = Set-AuthenticodeSignature @args2
if ($sig.Status -in @('NotSigned', 'HashMismatch', 'NotSupportedFileFormat', 'Incompatible')) { throw ('Signing failed: ' + $sig.Status + ' ' + $sig.StatusMessage) }
$check = Get-AuthenticodeSignature -LiteralPath $Path
if ($check.Status -in @('NotSigned', 'HashMismatch', 'NotSupportedFileFormat', 'Incompatible')) { throw ('Read-back failed: ' + $check.Status) }
if (-not $check.SignerCertificate -or $check.SignerCertificate.Thumbprint -ne $Thumbprint) { throw 'The file is signed by a different certificate than asked for.' }
if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $before) { throw 'The file was not changed by signing.' }
Write-Host ('Signed ' + (Split-Path -Leaf $Path) + ' (' + $check.Status + ', ' + $(if ($TimestampUrl) { 'timestamped' } else { 'no timestamp: re-sign before the certificate expires' }) + ')')
