# Build all distributables in dependency order.
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\build_agent.ps1"
python -m PyInstaller --clean --noconfirm NetPulse.spec
Write-Host "Main GUI and server utilities built under dist/"
