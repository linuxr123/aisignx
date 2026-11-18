# Trusts the owner's PUBLIC code-signing certificate for the account this script runs as (SYSTEM, from the
# agent), so Get-AuthenticodeSignature can report Status Valid for files signed with it. Idempotent.
# SystemOnly (default): SYSTEM's own CurrentUser Root + TrustedPublisher stores, so nothing else on the PC trusts it.
# Machine: LocalMachine stores (the whole PC trusts the key; the install script asks before choosing this).
# The thumbprint must equal the pinned one, or nothing is imported.
param(
    [Parameter(Mandatory = $true)][string]$CertFile,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$Thumbprint,
    [ValidateSet('SystemOnly', 'Machine')][string]$TrustScope = 'SystemOnly',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $CertFile
if ($cert.Thumbprint -ne $Thumbprint.ToUpperInvariant()) { throw 'certificate does not match the pinned thumbprint' }
$loc = if ($TrustScope -eq 'Machine') { 'LocalMachine' } else { 'CurrentUser' }
foreach ($name in 'Root', 'TrustedPublisher') {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($name, $loc)
    $store.Open('ReadWrite')
    try {
        $have = $store.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
        if ($Remove) { if ($have) { $store.Remove($cert) } }
        elseif (-not $have) { $store.Add($cert) }
    } finally { $store.Close() }
}
[Console]::Out.Write('{"ok":true}')
