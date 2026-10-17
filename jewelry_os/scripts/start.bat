@echo off
rem Jewelry OS - open the app (start it if it is not running). ASCII only: cmd code page.
cd /d "%~dp0.."
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8770/login -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  start "" http://127.0.0.1:8770/
) else (
  start "" ".venv\Scripts\pythonw.exe" -m jewelry_os serve
)
