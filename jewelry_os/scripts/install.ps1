# Jewelry OS — 아버님 PC 설치 스크립트 (Windows 10/11)
# 실행: jewelry_os 폴더에서  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# 하는 일: Python 확인 → 가상환경(.venv) → 패키지 설치 → 로그온 자동 시작 등록 → 바탕화면 바로가기 → 앱 실행
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # ...\jewelry_os
Set-Location $root
Write-Host "== Jewelry OS 설치 ==" -ForegroundColor Cyan
Write-Host "설치 위치: $root"

# 1) Python 3.13
$py = $null
try { & py -3.13 --version *> $null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-3.13") } } catch {}
if (-not $py) {
    try { $v = & python --version 2>&1; if ("$v" -match "Python 3\.1[3-9]") { $py = @("python") } } catch {}
}
if (-not $py) {
    Write-Host "Python 3.13이 없어요. 아래 명령으로 설치한 뒤 이 스크립트를 다시 실행하세요:" -ForegroundColor Yellow
    Write-Host "  winget install -e --id Python.Python.3.13"
    exit 1
}
Write-Host "Python 확인: $(& $py[0] $py[1..9] --version)"

# 2) 가상환경 + 패키지
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "가상환경을 만드는 중..."
    & $py[0] $py[1..9] -m venv .venv
}
$vpy = Join-Path $root ".venv\Scripts\python.exe"
Write-Host "패키지를 설치하는 중(1~3분)..."
& $vpy -m pip install --upgrade pip -q
& $vpy -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) { Write-Host "패키지 설치에 실패했어요. 인터넷 연결을 확인하세요." -ForegroundColor Red; exit 1 }
& $vpy -c "import jewelry_os.web.app" ; if ($LASTEXITCODE -ne 0) { Write-Host "앱을 불러오지 못했어요." -ForegroundColor Red; exit 1 }

# 3) 로그온 시 자동 시작(작업 스케줄러, 검은 창 없음)
Write-Host "로그온 자동 시작을 등록하는 중..."
& $vpy -m jewelry_os install-task
if ($LASTEXITCODE -ne 0) { Write-Host "자동 시작 등록에 실패했어요. 바로가기로 실행할 수 있어요." -ForegroundColor Yellow }

# 4) 바탕화면 바로가기
$desk = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $desk "Jewelry OS.lnk"))
$lnk.TargetPath = Join-Path $root "scripts\start.bat"
$lnk.WorkingDirectory = $root
$lnk.WindowStyle = 7
$lnk.Description = "Jewelry OS 열기"
$lnk.Save()
Write-Host "바탕화면에 'Jewelry OS' 바로가기를 만들었어요."

# 5) 실행
& (Join-Path $root "scripts\start.bat")
Write-Host ""
Write-Host "완료! 브라우저에 첫 실행 마법사가 열려요(http://127.0.0.1:8770)." -ForegroundColor Green
Write-Host "마법사에서 아버님·운영자 계정을 만든 뒤, 꼭 키를 USB에 따로 보관하세요:"
Write-Host "  .venv\Scripts\python -m jewelry_os export-keys E:\JewelryOS-keys.txt"
