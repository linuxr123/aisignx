# Builds the ONE setup .exe: the player for all users plus the privileged agent (files and installer script inside).
# Steps: unpacked app -> agent bundle (Build-Agent.ps1) -> stage the agent files -> NSIS installer with them inside -> sign.
# Needs AISIGNX_SIGN_THUMBPRINT (the agent pins that certificate); AISIGNX_SIGN_TIMESTAMP is optional but recommended.
#   $env:AISIGNX_SIGN_THUMBPRINT = '<40 hex>'; $env:AISIGNX_SIGN_TIMESTAMP = 'http://timestamp.digicert.com'; npm run dist:win
param([string]$Thumbprint = $env:AISIGNX_SIGN_THUMBPRINT)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($Thumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'Set AISIGNX_SIGN_THUMBPRINT to the 40-hex thumbprint of your code-signing certificate: the agent inside this installer pins it.' }
$env:AISIGNX_SIGN_THUMBPRINT = $Thumbprint
Push-Location $root
try {
    function Run([string]$what, [scriptblock]$cmd) { Write-Host "`n==> $what" -ForegroundColor Cyan; & $cmd; if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }
    $pack = Join-Path $root 'agent-pack'
    Run 'clean' { & powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prebuild_clean.ps1 }
    if (Test-Path $pack) { Remove-Item $pack -Recurse -Force }
    New-Item -ItemType Directory -Path $pack | Out-Null     # empty for now: the first build only needs the folder to exist
    Run 'unpacked app' { & npx electron-builder --win dir --x64 }
    Run 'agent bundle' { & powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Build-Agent.ps1 }
    Copy-Item (Join-Path $root 'dist-build\agent\*') $pack
    Copy-Item (Join-Path $root 'resources\agent-install\Setup-Agent.ps1') $pack
    $cert = Get-Item "Cert:\CurrentUser\My\$Thumbprint" -ErrorAction Stop
    Export-Certificate -Cert $cert -FilePath (Join-Path $pack 'signing.cer') -Type CERT | Out-Null    # public part only
    # The agent bundle and its hash are now fixed: the app is built once more, this time with them inside the installer.
    Run 'installer' { & npx electron-builder --win nsis --x64 }
    Get-ChildItem (Join-Path $root 'dist-build') -Filter '*Setup*.exe' | ForEach-Object {
        $s = Get-AuthenticodeSignature $_.FullName
        $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Host ("`n{0}`n  signature: {1}`n  size: {2} bytes`n  sha256: {3}" -f $_.FullName, $s.Status, $_.Length, $h)
    }
} finally { Pop-Location }
