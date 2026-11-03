# Keeps the parked account alive between pilot sessions: a cancelling movement pair through
# the pilot (left, then right) every few minutes, so the game's idle logout never fires while
# Claude works on something else. Stops when the stop file appears or the switch goes off.
#   .venv\Scripts\python -m jamcrammer pilot serve      (in its own terminal, first)
#   powershell -File scripts\pilot_keepalive.ps1 [-IntervalS 240]
param([int]$IntervalS = 240)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$stop = Join-Path $root "data\pilot\keepalive.stop"
Remove-Item $stop -ErrorAction SilentlyContinue
while (-not (Test-Path $stop)) {
    $a = & $py -m jamcrammer pilot key left 0.15 2>&1 | Select-String '"reason"|"refused"' | Select-Object -First 1
    Start-Sleep -Milliseconds 600
    $b = & $py -m jamcrammer pilot key right 0.15 2>&1 | Select-String '"reason"|"refused"' | Select-Object -First 1
    Write-Output ("{0}  {1} / {2}" -f (Get-Date -Format "HH:mm:ss"), ($a -replace '\s+', ' '), ($b -replace '\s+', ' '))
    Start-Sleep -Seconds $IntervalS
}
Write-Output "keepalive stopped"
