# Builds the privileged agent's install kit in dist-build\agent\:
#   AiSignX-Agent-<version>.zip   the bundle (Electron binary renamed aisignx-agent.exe, the agent's JavaScript, ps\*.ps1)
#   Install-Agent.ps1             with the bundle's SHA-256 and the signer thumbprint written in, then SIGNED
# Run on the build PC after `npm run dist:win` (or pass -BuildUnpacked to make dist-build\win-unpacked first).
#   .\Build-Agent.ps1 [-Thumbprint <40 hex>] [-TimestampUrl <url>] [-BuildUnpacked]
# The thumbprint defaults to AISIGNX_SIGN_THUMBPRINT. Without one the script is left unsigned (fine for testing).
param(
    [string]$Thumbprint = $env:AISIGNX_SIGN_THUMBPRINT,
    [string]$TimestampUrl = $env:AISIGNX_SIGN_TIMESTAMP,
    [switch]$BuildUnpacked
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pkg = Get-Content -LiteralPath (Join-Path $root 'package.json') -Raw | ConvertFrom-Json
$unpacked = Join-Path $root 'dist-build\win-unpacked'
if ($Thumbprint -and $Thumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'The thumbprint must be 40 hex characters.' }
if ($BuildUnpacked -or -not (Test-Path -LiteralPath $unpacked)) {
    Push-Location $root
    try { & npx electron-builder --win dir --x64; if ($LASTEXITCODE -ne 0) { throw 'electron-builder failed' } } finally { Pop-Location }
}
$exeSrc = Join-Path $unpacked ($pkg.build.productName + '.exe')
if (-not (Test-Path -LiteralPath $exeSrc)) { throw "Not found: $exeSrc" }
$out = Join-Path $root 'dist-build\agent'
$stage = Join-Path $root 'dist-build\agent-stage'
foreach ($d in $out, $stage) { if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force }; New-Item -ItemType Directory -Path $d | Out-Null }

# The Electron runtime without the player's own app code: the agent runs its own small JavaScript in node mode.
Get-ChildItem -LiteralPath $unpacked -Force | Where-Object { $_.Name -ne 'resources' -and $_.Name -ne ($pkg.build.productName + '.exe') } | Copy-Item -Destination $stage -Recurse -Force
Copy-Item -LiteralPath $exeSrc -Destination (Join-Path $stage 'aisignx-agent.exe')
foreach ($d in 'agent', 'agent-svc') { Copy-Item -LiteralPath (Join-Path $root "src\$d") -Destination (Join-Path $stage "src\$d") -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $root 'package.json') -Destination $stage
$ps = Join-Path $stage 'ps'
New-Item -ItemType Directory -Path $ps | Out-Null
Copy-Item -Path (Join-Path $root 'resources\agent\ps\*.ps1') -Destination $ps
Copy-Item -LiteralPath (Join-Path $root 'resources\shell-launcher.ps1') -Destination $ps

$zip = Join-Path $out ('AiSignX-Agent-' + $pkg.version + '.zip')
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
Remove-Item -LiteralPath $stage -Recurse -Force
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()

$script = Join-Path $out 'Install-Agent.ps1'
$text = Get-Content -LiteralPath (Join-Path $root 'resources\Install-Agent.ps1') -Raw
$text = $text -replace "(?m)^\`$EmbeddedBundleSha256 = ''", ('$EmbeddedBundleSha256 = ''' + $hash + '''')
if ($Thumbprint) { $text = $text -replace "(?m)^\`$EmbeddedThumbprint = ''", ('$EmbeddedThumbprint = ''' + $Thumbprint.ToUpperInvariant() + '''') }
[IO.File]::WriteAllText($script, $text, (New-Object Text.UTF8Encoding($true)))
if ($Thumbprint) { & (Join-Path $PSScriptRoot 'Sign-File.ps1') -Path $script -Thumbprint $Thumbprint -TimestampUrl $TimestampUrl }
else { Write-Host 'No thumbprint: Install-Agent.ps1 is NOT signed.' }
Write-Host ('Bundle  ' + $zip)
Write-Host ('SHA-256 ' + $hash)
Write-Host ('Script  ' + $script)
Write-Host 'Copy BOTH files to the PC and run Install-Agent.ps1 elevated (see docs/windows-agent.md).'
