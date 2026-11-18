# Prints ONE line of JSON about a folder: its owner and any reparse points (junctions, symlinks) in it.
#   {"owner":"BUILTIN\\Administrators","reparse":[]}
# The agent calls this for its own data folder at every start and refuses to act when the answer is unsafe.
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
try {
    $root = Get-Item -LiteralPath $Path -Force
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.Owner
    $toSid = { param($n) try { (New-Object Security.Principal.NTAccount([string]$n)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { 'unknown' } }
    $ownerSid = & $toSid $owner
    $aces = @()
    foreach ($a in $acl.Access) {
        if ($a.AccessControlType -ne 'Allow') { continue }
        $sid = if ($a.IdentityReference -is [Security.Principal.SecurityIdentifier]) { $a.IdentityReference.Value } else { & $toSid $a.IdentityReference.Value }
        $aces += [ordered]@{ sid = $sid; rights = [int64]$a.FileSystemRights }
    }
    $re = New-Object System.Collections.ArrayList
    if ($root.Attributes -band [IO.FileAttributes]::ReparsePoint) { [void]$re.Add($root.FullName) }
    Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) { [void]$re.Add($_.FullName) }
    }
    $o = [ordered]@{ owner = [string]$owner; ownerSid = $ownerSid; aces = @($aces); reparse = @($re) }
    [Console]::Out.Write(($o | ConvertTo-Json -Compress -Depth 3))
} catch {
    [Console]::Out.Write('{"owner":"unknown","reparse":["error"]}')
}
