# AISignX - Node.js Diagnostic
# Run this if build_clients_windows.ps1 says Node.js is not found.
# It will tell you exactly where Node.js is (or isn't) on this machine.

Write-Host ""
Write-Host "=== Node.js Diagnostic ===" -ForegroundColor Cyan
Write-Host ""

# 1. Check current PATH
Write-Host "1. Checking current session PATH for node..." -ForegroundColor Yellow
$cmd = Get-Command node -ErrorAction SilentlyContinue
if ($cmd) {
	Write-Host "   FOUND on PATH: $($cmd.Source)" -ForegroundColor Green
	Write-Host "   Version: $(node --version)" -ForegroundColor Green
} else {
	Write-Host "   NOT on current PATH" -ForegroundColor Red
}

# 2. Check system/user PATH in registry
Write-Host ""
Write-Host "2. System PATH entries containing 'node' or 'nodejs':" -ForegroundColor Yellow
$machinePath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
$userPath    = [System.Environment]::GetEnvironmentVariable("PATH","User")
$allPaths    = ($machinePath + ";" + $userPath).Split(";") | Where-Object { $_ -match "node" }
if ($allPaths) { $allPaths | ForEach-Object { Write-Host "   $_" -ForegroundColor Green } }
else           { Write-Host "   None found in registry PATH" -ForegroundColor Red }

# 3. Search common install locations
Write-Host ""
Write-Host "3. Searching common install locations for node.exe..." -ForegroundColor Yellow
$candidates = @(
	"$env:ProgramFiles\nodejs",
	"${env:ProgramFiles(x86)}\nodejs",
	"$env:ProgramFiles\OpenJS\nodejs",
	"$env:LOCALAPPDATA\Programs\nodejs",
	"$env:APPDATA\nvm\current",
	"C:\nodejs"
)
foreach ($c in $candidates) {
	if (Test-Path "$c\node.exe") {
		Write-Host "   FOUND: $c\node.exe" -ForegroundColor Green
	} else {
		Write-Host "   not found: $c" -ForegroundColor DarkGray
	}
}

# 4. Broad search under Program Files
Write-Host ""
Write-Host "4. Broad search under Program Files (may take a moment)..." -ForegroundColor Yellow
foreach ($base in @($env:ProgramFiles, "${env:ProgramFiles(x86)}", $env:LOCALAPPDATA)) {
	if (-not $base) { continue }
	$hits = Get-ChildItem -Path $base -Recurse -Depth 3 -Filter "node.exe" -ErrorAction SilentlyContinue
	foreach ($h in $hits) {
		Write-Host "   FOUND: $($h.FullName)" -ForegroundColor Green
	}
}

# 5. Check winget list
Write-Host ""
Write-Host "5. Winget packages matching 'node':" -ForegroundColor Yellow
try {
	$wg = winget list 2>&1 | Select-String -Pattern "node|Node"
	if ($wg) { $wg | ForEach-Object { Write-Host "   $($_.Line)" -ForegroundColor Green } }
	else      { Write-Host "   No Node.js entry found in winget list" -ForegroundColor Red }
} catch {
	Write-Host "   winget not available or failed" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== End Diagnostic ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "If node.exe was found above but NOT on PATH:" -ForegroundColor White
Write-Host "  Copy the directory path shown and run:" -ForegroundColor White
Write-Host '  [System.Environment]::SetEnvironmentVariable("PATH", "C:\that\path;" + [System.Environment]::GetEnvironmentVariable("PATH","Machine"), "Machine")' -ForegroundColor Yellow
Write-Host "  Then open a NEW PowerShell window and re-run build_clients_windows.ps1" -ForegroundColor White
Write-Host ""
Write-Host "If node.exe was NOT found anywhere:" -ForegroundColor White
Write-Host "  Download and install Node.js 20 LTS from https://nodejs.org/en/download" -ForegroundColor Yellow
Write-Host "  Then open a NEW PowerShell window and re-run build_clients_windows.ps1" -ForegroundColor White
Write-Host ""
