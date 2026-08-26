# Stop AISignX (Waitress) and Caddy — works for foreground and -Background starts.
<#
.SYNOPSIS
    Stop AISignX and Caddy started by start_server.ps1.

.EXAMPLE
    .\scripts\stop_server.ps1
#>
[CmdletBinding()]
param(
    # Internal: second pass after UAC elevation (do not use manually).
    [switch]$ElevatedPass
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\caddy_lib.ps1"
Stop-AisignxServer -ElevatedPass:$ElevatedPass
if (Test-PortListening -Port 5000) { exit 1 }
if (Test-PortListening -Port 443) { exit 1 }
exit 0
