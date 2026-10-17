# Jewelry OS — 업데이트(새 버전 받기). 실행: jewelry_os 폴더에서
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1
# 하는 일: 백업 → 앱 종료 → git pull → 패키지 갱신 → 앱 다시 시작(DB 구조 변경은 기동 시 자동 이행)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$vpy = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "1) 업데이트 전 백업..." -ForegroundColor Cyan
& $vpy -m jewelry_os backup
if ($LASTEXITCODE -ne 0) { Write-Host "백업에 실패해 업데이트를 멈췄어요." -ForegroundColor Red; exit 1 }

Write-Host "2) 실행 중인 앱 종료..."
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*jewelry_os*serve*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 2

Write-Host "3) 새 버전 받기..."
Push-Location (Split-Path -Parent $root)
git pull --ff-only
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Write-Host "git pull에 실패했어요(로컬에서 파일을 고쳤는지 확인)." -ForegroundColor Red; exit 1 }

Write-Host "4) 패키지 갱신..."
& $vpy -m pip install -r requirements.txt -q

Write-Host "5) 다시 시작..."
& (Join-Path $root "scripts\start.bat")
Write-Host "업데이트 완료." -ForegroundColor Green
