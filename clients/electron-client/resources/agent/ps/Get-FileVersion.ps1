# Prints the product version of an installed program (the player), or nothing. Path comes from the protected agent.json.
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
try { [Console]::Out.Write((Get-Item -LiteralPath $Path).VersionInfo.ProductVersion) } catch { }
