# Prints ONE line of JSON about the Authenticode signature of a file and nothing else:
#   {"status":"Valid","thumbprint":"...","notAfter":"...","timestamped":true,"eku":["1.3.6.1.5.5.7.3.3"],"sigAlg":"sha256RSA"}
# Run by the AiSignX privileged agent (as SYSTEM) on the exact file it is about to execute. The decision
# (status Valid, thumbprint equals the pinned one, code signing EKU) is made by the agent in JavaScript;
# this script only reports. Constant script: the file path is its only argument.
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
try {
    $s = Get-AuthenticodeSignature -LiteralPath $Path
    $c = $s.SignerCertificate
    $eku = New-Object System.Collections.ArrayList
    if ($c) { foreach ($e in $c.EnhancedKeyUsageList) { [void]$eku.Add([string]$e.ObjectId) } }
    $o = [ordered]@{
        status      = [string]$s.Status
        thumbprint  = if ($c) { [string]$c.Thumbprint } else { '' }
        notAfter    = if ($c) { $c.NotAfter.ToUniversalTime().ToString('o') } else { '' }
        timestamped = ($null -ne $s.TimeStamperCertificate)
        eku         = @($eku)
        sigAlg      = if ($c) { [string]$c.SignatureAlgorithm.FriendlyName } else { '' }
    }
    [Console]::Out.Write(($o | ConvertTo-Json -Compress -Depth 3))
} catch {
    [Console]::Out.Write('{"status":"Error"}')
}
