# Build additive server-side utilities for NetPulse.
$ErrorActionPreference = "Stop"

# Agent is onedir because Qt's native DLL/plugin layout is more reliable for a
# long-running server process. Copy the entire dist\NetPulse-Agent directory.
python -m PyInstaller --clean --noconfirm Agent.spec
python -m PyInstaller --clean --noconfirm Hub.spec
python -m PyInstaller --clean --noconfirm AgentCtl.spec

Write-Host "Built server utilities under dist/"
