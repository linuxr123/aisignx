# Signs an installer that is already built (for builds made elsewhere, or when the electron-builder hook was off).
#   .\Sign-Installer.ps1 [-Path dist-build\AiSignX-Player-Setup-3.1.0.exe] [-Thumbprint <40 hex>] [-TimestampUrl <url>]
# Sign last and do not touch the file afterwards: one changed byte breaks the signature. Prints the SHA-256 and size
# of the SIGNED file, which are the values the server records when you upload it on the Downloads page.
param(
    [string]$Path,
    [string]$Thumbprint = $env:AISIGNX_SIGN_THUMBPRINT,
    [string]$TimestampUrl = $env:AISIGNX_SIGN_TIMESTAMP
)
$ErrorActionPreference = 'Stop'
if (-not $Path) {
    $Path = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot '..\dist-build') -Filter '*Setup*.exe' -ErrorAction Stop |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
& (Join-Path $PSScriptRoot 'Sign-File.ps1') -Path $Path -Thumbprint $Thumbprint -TimestampUrl $TimestampUrl
$f = Get-Item -LiteralPath $Path
Write-Host ('sha256 ' + (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant())
Write-Host ('size   ' + $f.Length)
