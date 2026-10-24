# Jewelry OS — 실행·설치·운영 가이드

- **RTM rev.7 기준** · 대상: 개발 PC(지금 이 PC), GitHub 저장소, 아버님 PC(최종 설치)
- 앱은 **그 PC 안에서만** 동작합니다(주소 `http://127.0.0.1:8770`, 다른 기기·집 밖 접속 없음 — 원격 관리자는 미완료 기능 SRS-171).
- 인수 상태: **시험 운영 모드만 승인**(H3, 2026-10-17). 실제 고객 접수·실결제는 인수 조건 C-1~C-5를 마친 뒤 Gate를 기록해야 열립니다(§8).

## 0. 한눈에 보기

| 무엇 | 어디 |
|---|---|
| 코드 | `jewelry_os/` (실행 패키지 `jewelry_os/jewelry_os/`) |
| 실행에 필요한 패키지 | `jewelry_os/requirements.txt` (개발·시험: `requirements-dev.txt`, 전체 잠금: `requirements-lock.txt`) |
| 설치·업데이트·실행 스크립트 | `jewelry_os/scripts/install.ps1` · `update.ps1` · `start.bat` |
| 운영 데이터(DB·첨부·백업·로그) | `%LOCALAPPDATA%\JewelryOS\prod\` — 코드 폴더와 분리, Git에 올라가지 않음 |
| 암호화 키 | 같은 폴더의 `secrets.env` — **백업과 따로 USB 등에 보관(§6)** |

명령은 모두 `jewelry_os` 폴더에서 실행합니다(PowerShell). `python`은 그 폴더의 `.venv\Scripts\python.exe`를 뜻합니다.

## 1. 개발 PC에서 실행해 보기

```powershell
cd "C:\Users\TEST OS\Desktop\창업\Jewelry\jewelry_os"
```
시험 데이터가 든 개발용 폴더를 만들고(계정: `tools/dev_seed.py`에 적힌 시험용 아이디·비밀번호):
```powershell
.venv\Scripts\python tools\dev_seed.py $env:TEMP\jos-dev
```
실행(브라우저가 자동으로 열림):
```powershell
.venv\Scripts\python -m jewelry_os --env dev --data-dir $env:TEMP\jos-dev serve
```
- 종료: 실행한 창에서 `Ctrl+C`. 이미 켜져 있으면 “이미 실행 중이에요”와 함께 종료코드 3으로 끝납니다.
- 전체 시험(약 3분, 개발 PC만):
```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
```
```powershell
.venv\Scripts\python -m pytest tests -q
```
(브라우저 시험은 Microsoft Edge와 `tools/node_modules/axe-core`가 필요 — `cd tools; npm install axe-core@4.10.2`)

## 2. GitHub에 올리기

1. GitHub에서 **비공개(Private) 저장소**를 만듭니다(예: `jewelry-os`). 고객 정보는 저장소에 없지만 사업 문서가 들어가므로 비공개를 권장합니다.
2. 이 PC에서(처음 한 번):
```powershell
cd "C:\Users\TEST OS\Desktop\창업\Jewelry"
```
```powershell
git init -b main
```
```powershell
git add .
```
```powershell
git status
```
   → 목록에 `secrets.env`, `.venv`, `*.db`, `*.josbak`, `구글 폼/`, `스마트스토어/`가 **없는지** 확인합니다(`.gitignore`가 막음). 사업 자료도 올리려면 `.gitignore` 맨 아래 줄을 지우세요.
```powershell
git commit -m "Jewelry OS R1 — 시험 운영 버전(rev.7)"
```
```powershell
git remote add origin https://github.com/<계정>/jewelry-os.git
```
```powershell
git push -u origin main
```
3. 이후 수정할 때마다: `git add .` → `git commit -m "내용"` → `git push`.
- 올라가는 것: 코드·시험·문서(`docs/`, RTM)·시험 증거 로그(`jewelry_os/reports/*.log·*.xml`). 올라가지 않는 것: 운영 데이터·키·가상환경·스크린샷.

## 3. 아버님 PC에 설치하기 (최종 설치)

**준비물**: Windows 10/11, 인터넷, 관리자 권한은 필요 없음.

1. **Python 3.13 설치** — PowerShell에서:
```powershell
winget install -e --id Python.Python.3.13
```
   (winget이 없으면 python.org에서 3.13 설치, “Add python.exe to PATH” 체크)
2. **코드 받기** — 둘 중 하나:
   - Git이 있으면:
```powershell
git clone https://github.com/<계정>/jewelry-os.git "$env:USERPROFILE\JewelryOS"
```
   - 없으면 GitHub의 **Code → Download ZIP**을 받아 `C:\Users\<아버님>\JewelryOS`에 풀기.
3. **설치 스크립트 실행**:
```powershell
cd "$env:USERPROFILE\JewelryOS\jewelry_os"
```
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```
   스크립트가 하는 일: Python 확인 → `.venv` 생성 → 패키지 설치 → **로그온 시 자동 시작 등록**(작업 스케줄러 `JewelryOS`, 검은 창 없음) → **바탕화면 “Jewelry OS” 바로가기** → 앱 실행.
4. **첫 실행 마법사**(브라우저가 자동으로 열림, `http://127.0.0.1:8770/setup`):
   - 아버님 계정(제작 판단)과 운영자 계정(결제·설정)을 만듭니다. 비밀번호 10자 이상, 서로 다른 아이디.
   - 운영 기본값: 동시 진행 상한 3, 결제 정책 예약금 1/3, 화면 잠금 15분, 휴지 시간 00:00–07:00(D-2~D-8 확정값).
5. **설치 직후 꼭 할 일**(운영자 계정으로):
   - **키 보관**: USB를 꽂고(예: E:) —
```powershell
.venv\Scripts\python -m jewelry_os export-keys E:\JewelryOS-keys.txt
```
     이 파일이 없으면 PC 고장 시 백업을 복원할 수 없습니다. USB나 비밀번호 관리자에 보관하고 **백업 파일과 같은 곳에 두지 마세요.**
   - **설정 → 운영 정책 → 사업자 과세 유형** 선택(일반·간이·면세, 간이면 업종 부가가치율) — TBD-009. 미설정이면 실적의 공헌이익이 일부 계산되지 않습니다.
   - **시스템 상태 → PC 보안 점검표**: BitLocker·Windows 비밀번호·화면 자동 잠금·백업 외부 복사본 체크.
6. **확인**: PC를 재부팅 → 로그온 후 1분 안에 브라우저가 열리는지, 바탕화면 바로가기로도 열리는지 확인합니다(인수 조건 C-3, 14_TR 부록 B).

## 4. 매일 사용

- PC를 켜고 로그온하면 자동으로 켜지고 브라우저가 열립니다. 창을 닫았으면 바탕화면 **Jewelry OS** 바로가기를 누르세요(이미 켜져 있으면 화면만 다시 엽니다).
- 주소: `http://127.0.0.1:8770` — 북마크해 두면 편합니다. 15분 쓰지 않으면 화면이 잠기고, 12시간마다 다시 로그인합니다.
- 앱이 PC가 꺼져 있던 동안의 Google 폼 의뢰를 켜질 때 모두 가져옵니다(“PC가 약 N시간 꺼져 있었어요”).
- 백업은 매일 새벽 3시 또는 마지막 백업 26시간 경과 시 자동으로 `%LOCALAPPDATA%\JewelryOS\prod\backups`에 만들어집니다. 주 1회 USB나 클라우드 폴더로 복사해 두세요.

## 5. 실제 계정 연결 (인수 조건 C-1 — 승인 전에는 시험 폼으로만)

- **Anthropic(Claude) API 키** — 화면에 보이지 않게 입력됩니다:
```powershell
.venv\Scripts\python -m jewelry_os set-secret ANTHROPIC_API_KEY
```
  그다음 앱의 설정 → AI에서 제공자를 `claude`로, 월 호출·비용 상한을 정하고 앱을 다시 시작합니다.
- **Google Forms·Drive**: OAuth 클라이언트(“데스크톱 앱”) 값은 **코드에 들어 있습니다**(`jewelry_os/jewelry_os/google_client.json`, D-21) — PC마다 입력하지 않습니다. 처음 한 번만 Google Cloud 콘솔에서 만들어 그 파일에 붙여 넣고 GitHub에 올립니다(API: Google Forms·Drive 사용 설정, 동의 화면 ‘외부’, 테스트 사용자에 폼 주인 계정). PC마다 하는 일은 **Google 로그인 1번**뿐입니다:
  운영자 계정으로 `http://127.0.0.1:8770` → 설정 → Google → **“Google 계정 연결”** → Google 화면에서 폼 주인 계정으로 로그인·허용(“확인되지 않은 앱”이면 고급 → 계속). 명령으로 하려면:
```powershell
.venv\Scripts\python -m jewelry_os google-auth
```
  동의 화면이 ‘테스트’ 상태면 Google이 7일마다 연결을 끊으니, 시험이 끝나면 **‘프로덕션으로 게시’**로 바꿉니다. PC별로 다른 클라이언트를 쓰려면 `set-secret JOS_GOOGLE_CLIENT_ID`/`JOS_GOOGLE_CLIENT_SECRET`(PC 값이 코드 값보다 우선).
  브라우저에서 Google 계정으로 허용한 뒤 앱을 다시 시작하면 끝입니다. **틴틴골드 의뢰 폼 ID는 코드에 들어 있고**(D-20), 새로 설치한 앱은 Google 모드가 **자동**이라 계정이 연결되는 순간 그 폼(시험 폼)에서 가져옵니다. 이미 설치된 PC는 설정 → Google에서 모드를 **자동**으로 한 번 저장하세요(예전 설정값이 남아 있을 수 있음). 앱 화면의 **Google 계정 연결** 버튼으로 연결하면 다시 시작하지 않아도 바로 바뀝니다. 고객 공개 폼은 GATE-OPS-01·LEGAL-01 통과 전 저장이 거부됩니다.
- **폼 질문 연결(처음 한 번, 운영자 계정)** — 실제 폼은 질문마다 Google이 정한 고유 ID를 쓰므로, 연결하기 전에는 이름·연락처·설명·동의를 읽지 못해 주문에 “정보 부족”·“동의 없음”이 붙습니다.
  1. 시험 폼에 **시험 응답 1건**을 직접 제출합니다(모든 질문에 알아보기 쉬운 값, 사진 1장, 동의 체크).
  2. 설정 → Google → **지금 가져오기**. 화면 위에 “연결하지 않은 폼 질문 N개가 있어요”가 나오면 **질문 연결하기**를 누릅니다(설정 → 폼 매핑).
  3. 질문 ID마다 예시 답변을 보고 항목을 고릅니다. **(필수)** 표시 6개(고객 표시명·회신수단 종류·회신수단·제품 유형·의뢰 설명·개인정보 동의)는 모두 연결해야 저장됩니다. 사진 문항은 자동 인식이라 목록에 없습니다. 쓰지 않는 질문은 “(연결 안 함)”.
  4. **연결 저장** → 이미 받은 응답을 다시 읽어 빈 칸을 채우고, 시스템이 붙인 “정보 부족”·“동의 없음”을 풀고, AI 정리를 1번 실행합니다. 주문 상세에서 이름·연락처·설명과 AI 정리를 확인합니다.
  - 폼에 질문을 새로 추가하거나 다른 폼으로 바꾸면 같은 화면에 새 질문 ID가 다시 나타납니다 — 같은 방법으로 연결합니다. 다른 폼으로 바꿀 때는 예전 폼의 질문 ID를 “(연결 안 함)”으로 바꿔야 저장됩니다(한 항목에 질문 하나). 질문 제목만 고친 것은 ID가 그대로라 다시 할 필요 없음).
- 연결 후 14_TR §6대로 TC-S-024·052를 실계정으로 확인하고 결과를 RTM 인수 조건 C-1에 기록합니다.

## 6. 백업 복원 · PC 교체

**같은 PC에서 복원**(앱을 먼저 종료 — 작업 관리자에서 `pythonw.exe` 종료 또는 재부팅 직후):
```powershell
.venv\Scripts\python -m jewelry_os restore "$env:LOCALAPPDATA\JewelryOS\prod\backups\jos-YYYYMMDD-HHMMSS.josbak"
```
기존 데이터는 `*.before-restore-*`로 옆에 남습니다.

**새 PC로 옮기기**: ① 새 PC에 §3의 1~3 설치(마법사는 **진행하지 않음**) ② 키 가져오기 ③ 백업 복원 ④ 바로가기로 실행 → 기존 계정으로 로그인. 키 파일에는 **Google 연결도 함께** 들어 있어(연결된 PC에서 내보냈을 때) 새 PC에서 Google 로그인을 다시 하지 않아도 됩니다.
```powershell
.venv\Scripts\python -m jewelry_os import-keys E:\JewelryOS-keys.txt
```
```powershell
.venv\Scripts\python -m jewelry_os restore E:\jos-YYYYMMDD-HHMMSS.josbak
```

## 7. 업데이트 (새 버전 받기)

GitHub에 새 버전을 올린 뒤 아버님 PC에서:
```powershell
cd "$env:USERPROFILE\JewelryOS\jewelry_os"
```
```powershell
powershell -ExecutionPolicy Bypass -File scripts\update.ps1
```
백업 → 앱 종료 → `git pull` → 패키지 갱신 → 재시작을 자동으로 합니다. DB 구조가 바뀐 버전이면 앱이 켜질 때 자동으로 옮깁니다(예: rev.7의 스키마 v2). ZIP으로 설치했다면 새 ZIP을 같은 폴더에 덮어쓴 뒤 `scripts\install.ps1`을 다시 실행하세요(데이터는 지워지지 않습니다).

## 8. 시험 운영 모드와 실운영 전환 (H3 결정)

- 지금은 **시험 운영 모드**입니다: 모든 화면 상단에 노란 배너, 견적·결제에 “시험” 표지, 실거래 결제 요청 차단.
- **인수 조건 C-1~C-5를 마치기 전에는 설정 → Gate에서 GATE-OPS-01·LEGAL-01·SS-02를 “통과”로 기록하지 마세요.** 기록하면 시험 운영 모드가 풀립니다(D-11).
  - C-1 실제 Google·AI 연동 시험(§5) · C-2 아버님 사용성 시험(14_TR 부록 A) · C-3 실 PC 재부팅·복구(§3-6, §6) · C-4 조건부 11개 항목 증빙 · C-5 스마트스토어 실결제·소비자 계약 정책 검증.
- 모든 조건의 결과는 RTM 2-5 표에 기록합니다.

## 9. 문제 해결

| 증상 | 확인 |
|---|---|
| 브라우저가 안 열림 | 바탕화면 바로가기 실행 → 그래도 안 되면 `http://127.0.0.1:8770` 직접 입력 |
| “이미 실행 중이에요”(종료코드 3) | 정상 — 이미 켜져 있음. 브라우저 주소만 여세요 |
| 화면이 안 뜨고 8770 포트 오류 | 다른 프로그램이 8770을 쓰는지 확인(재부팅이 가장 간단) |
| Google 동기화 오류 | 시스템 상태 화면의 문구대로(“다시 연결 필요”면 §5의 `google-auth` 다시) |
| 로그 | `%LOCALAPPDATA%\JewelryOS\prod\logs\jos.log`(연락처·키는 가려져 기록) |
| 자동 시작 해제 | `schtasks /Delete /TN JewelryOS /F` |
| 자동 시작 다시 등록 | `.venv\Scripts\python -m jewelry_os install-task` |
