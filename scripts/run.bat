@echo off
setlocal
set ROOT=%~dp0..
if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo [JamCrammer] venv missing. Run: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  exit /b 1
)
cd /d "%ROOT%"
"%ROOT%\.venv\Scripts\python.exe" -m jamcrammer gui %*
endlocal
