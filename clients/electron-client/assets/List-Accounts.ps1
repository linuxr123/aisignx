# Prints the local accounts that can run the screen (enabled, not an administrator), separated by '|'. Used by the setup wizard.
$ErrorActionPreference = 'Stop'
try {
    $admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { $_.SID.Value })
    $names = @(Get-LocalUser | Where-Object { $_.Enabled -and $admins -notcontains $_.SID.Value } | ForEach-Object { $_.Name })
    [Console]::Out.Write(($names -join '|'))
} catch { [Console]::Out.Write('') }
