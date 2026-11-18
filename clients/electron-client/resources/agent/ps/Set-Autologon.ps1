# Automatic Windows sign-in for ONE account, set or removed by the AiSignX privileged agent (as SYSTEM).
#   -Mode Set    reads ONE line from stdin: the password as base64 of its UTF-8 bytes. The account comes from the
#                environment (AISIGNX_USER, AISIGNX_DOMAIN), set by the agent from its protected config.
#   -Mode Clear  removes it again.
#   -Mode SelfTest  only compiles the helper and prints ok (no change to the machine).
#   -DryRun      (with Set or Clear) does all the checking, reads the Windows 10/11 password-less key (read only),
#                prints what it WOULD do as JSON and changes nothing: no LSA secret, no registry write.
# The password is stored as the LSA secret "DefaultPassword" (what Sysinternals Autologon does). It is NEVER written
# to the registry value Winlogon\DefaultPassword (that one is readable by every local user) and there is no fallback.
# A local administrator can still read an LSA secret; the docs say so.
param([Parameter(Mandatory = $true)][ValidateSet('Set', 'Clear', 'SelfTest')][string]$Mode, [switch]$DryRun)
$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AsxLsa {
    [StructLayout(LayoutKind.Sequential)] struct LSA_UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
    [StructLayout(LayoutKind.Sequential)] struct LSA_OBJECT_ATTRIBUTES { public int Length; public IntPtr RootDirectory; public IntPtr ObjectName; public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService; }
    [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr systemName, ref LSA_OBJECT_ATTRIBUTES attrs, uint access, out IntPtr policy);
    [DllImport("advapi32.dll", EntryPoint = "LsaStorePrivateData")] static extern uint LsaStore(IntPtr policy, ref LSA_UNICODE_STRING key, ref LSA_UNICODE_STRING data);
    [DllImport("advapi32.dll", EntryPoint = "LsaStorePrivateData")] static extern uint LsaDelete(IntPtr policy, ref LSA_UNICODE_STRING key, IntPtr noData);
    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr policy);
    [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint status);
    const uint POLICY_CREATE_SECRET = 0x00000020;
    static LSA_UNICODE_STRING Make(string s, out IntPtr mem) {
        mem = Marshal.StringToHGlobalUni(s);
        LSA_UNICODE_STRING u = new LSA_UNICODE_STRING();
        u.Buffer = mem; u.Length = (ushort)(s.Length * 2); u.MaximumLength = (ushort)(s.Length * 2 + 2);
        return u;
    }
    // value == null removes the secret. Returns 0 on success, otherwise a Win32 error code.
    public static int Put(string name, string value) {
        LSA_OBJECT_ATTRIBUTES attrs = new LSA_OBJECT_ATTRIBUTES();
        attrs.Length = Marshal.SizeOf(typeof(LSA_OBJECT_ATTRIBUTES));
        IntPtr policy;
        uint st = LsaOpenPolicy(IntPtr.Zero, ref attrs, POLICY_CREATE_SECRET, out policy);
        if (st != 0) return LsaNtStatusToWinError(st);
        IntPtr kmem = IntPtr.Zero, vmem = IntPtr.Zero;
        try {
            LSA_UNICODE_STRING key = Make(name, out kmem);
            if (value == null) st = LsaDelete(policy, ref key, IntPtr.Zero);
            else { LSA_UNICODE_STRING data = Make(value, out vmem); st = LsaStore(policy, ref key, ref data); }
            return st == 0 ? 0 : LsaNtStatusToWinError(st);
        } finally {
            if (kmem != IntPtr.Zero) Marshal.FreeHGlobal(kmem);
            if (vmem != IntPtr.Zero) Marshal.ZeroFreeGlobalAllocUnicode(vmem);
            LsaClose(policy);
        }
    }
}
'@

if ($Mode -eq 'SelfTest') { [Console]::Out.Write('ok'); exit 0 }

$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$dpl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'
$oldName = 'AiSignXOldDevicePasswordLess'

if ($Mode -eq 'Set') {
    $user = [string]$env:AISIGNX_USER
    $domain = [string]$env:AISIGNX_DOMAIN
    if ($user -notmatch '^[A-Za-z0-9._ -]{1,64}$' -or $domain -notmatch '^(\.|[A-Za-z0-9._-]{1,64})$') { throw 'bad account' }
    $line = [Console]::In.ReadLine()
    if (-not $line -or $line.Length -gt 1024) { throw 'no password' }
    $bytes = [Convert]::FromBase64String($line.Trim())
    $pw = [Text.Encoding]::UTF8.GetString($bytes)
    [Array]::Clear($bytes, 0, $bytes.Length)
    if ($pw.Length -lt 1 -or $pw.Length -gt 127 -or $pw.Contains([string][char]0)) { throw 'bad password' }
    if ($DryRun) {
        $pw = $null
        $cur = $null
        if (Test-Path -LiteralPath $dpl) { $cur = (Get-ItemProperty -LiteralPath $dpl -Name DevicePasswordLessBuildVersion -ErrorAction SilentlyContinue).DevicePasswordLessBuildVersion }
        $d = [ordered]@{ ok = $true; dryRun = $true; mode = 'Set'; user = $user; domain = $domain; dplKey = $dpl; dplKeyExists = [bool](Test-Path -LiteralPath $dpl)
            wouldSwitchOffPasswordless = [bool]($null -ne $cur -and $cur -ne 0) }
        [Console]::Out.Write(($d | ConvertTo-Json -Compress))
        exit 0
    }
    $rc = [AsxLsa]::Put('DefaultPassword', $pw)
    $pw = $null
    if ($rc -ne 0) { throw "could not store the secret (error $rc)" }
    Set-ItemProperty -LiteralPath $winlogon -Name AutoAdminLogon -Value '1' -Type String
    Set-ItemProperty -LiteralPath $winlogon -Name DefaultUserName -Value $user -Type String
    Set-ItemProperty -LiteralPath $winlogon -Name DefaultDomainName -Value $domain -Type String
    Set-ItemProperty -LiteralPath $winlogon -Name ForceAutoLogon -Value '0' -Type String
    # A leftover clear-text value from anybody else is removed: the secret is the only place.
    Remove-ItemProperty -LiteralPath $winlogon -Name DefaultPassword -ErrorAction SilentlyContinue
    # Windows 10/11: password-less sign-in enforcement can switch AutoAdminLogon off. Remember the old value for undo.
    if (Test-Path -LiteralPath $dpl) {
        $cur = (Get-ItemProperty -LiteralPath $dpl -Name DevicePasswordLessBuildVersion -ErrorAction SilentlyContinue).DevicePasswordLessBuildVersion
        if ($null -ne $cur -and $cur -ne 0) {
            Set-ItemProperty -LiteralPath $dpl -Name $oldName -Value $cur -Type DWord
            Set-ItemProperty -LiteralPath $dpl -Name DevicePasswordLessBuildVersion -Value 0 -Type DWord
        }
    }
    [Console]::Out.Write('{"ok":true}')
    exit 0
}

# Clear
if ($DryRun) {
    $old = $null
    if (Test-Path -LiteralPath $dpl) { $old = (Get-ItemProperty -LiteralPath $dpl -Name $oldName -ErrorAction SilentlyContinue).$oldName }
    $d = [ordered]@{ ok = $true; dryRun = $true; mode = 'Clear'; dplKey = $dpl; dplKeyExists = [bool](Test-Path -LiteralPath $dpl); wouldRestorePasswordless = [bool]($null -ne $old) }
    [Console]::Out.Write(($d | ConvertTo-Json -Compress))
    exit 0
}
Set-ItemProperty -LiteralPath $winlogon -Name AutoAdminLogon -Value '0' -Type String
Remove-ItemProperty -LiteralPath $winlogon -Name DefaultPassword -ErrorAction SilentlyContinue
$rc = [AsxLsa]::Put('DefaultPassword', $null)
# 2 (file not found) and the NTSTATUS for a missing secret both mean there was nothing to remove.
if ($rc -ne 0 -and $rc -ne 2) { throw "could not remove the secret (error $rc)" }
if (Test-Path -LiteralPath $dpl) {
    $old = (Get-ItemProperty -LiteralPath $dpl -Name $oldName -ErrorAction SilentlyContinue).$oldName
    if ($null -ne $old) {
        Set-ItemProperty -LiteralPath $dpl -Name DevicePasswordLessBuildVersion -Value $old -Type DWord
        Remove-ItemProperty -LiteralPath $dpl -Name $oldName -ErrorAction SilentlyContinue
    }
}
[Console]::Out.Write('{"ok":true}')
