# JamCrammer bootstrap: creates .venv, installs pinned deps, copies config.example.yaml.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = $null
foreach ($v in @("3.12", "3.13", "3.14", "3.11")) {
  try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = "-$v"; break } } catch {}
}
if (-not $py) { throw "No Python 3.11+ found via the py launcher. Install Python 3.12 from python.org." }
Write-Host "[setup] Using Python $py"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  & py $py -m venv .venv
}
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -e ".[dev]"

if (-not (Test-Path "config.yaml")) {
  Copy-Item "config.example.yaml" "config.yaml"
  Write-Host "[setup] Created config.yaml from config.example.yaml"
}
foreach ($d in @("data\logs", "captures\sessions", "captures\unknown", "captures\unknown_popups")) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Write-Host "[setup] Done. Launch with run.bat, or: .venv\Scripts\python -m jamcrammer --help"
