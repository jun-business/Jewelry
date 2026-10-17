# Jewelry OS R1

자택 PC에서 동작하는 주문제작 주얼리 운영 소프트웨어 — Google 폼 의뢰 접수부터 견적·결제 확인·CAD·공장 발주·검수·발송까지, 아버님(제작 판단)과 운영자(결제·운영)가 함께 쓰는 로컬 웹앱(`http://127.0.0.1:8770`).

- **상태**: rev.7 · 시험 172건 PASS · **H3 조건부 인수 — 로컬 PC 시험 운영 모드만 승인**(실고객 운영·실결제는 인수 조건 C-1~C-5 이후)
- **설치·실행·GitHub·아버님 PC 설치**: [docs/INSTALL_운영가이드.md](docs/INSTALL_운영가이드.md)
- 스택: Python 3.13 · FastAPI · SQLite(WAL) · Jinja2 서버 렌더링 · 바닐라 CSS/JS(빌드·CDN 없음)

## 문서
| 문서 | 내용 |
|---|---|
| [PROJECT_REGISTER.md](PROJECT_REGISTER.md) | 요구사항 추적 매트릭스(RTM) — 진행 상태의 원본 |
| [Jewelry_OS_PRD_v1.0.md](Jewelry_OS_PRD_v1.0.md) | PRD 원문 |
| [docs/DECISION_LOG.md](docs/DECISION_LOG.md) | 결정 기록(H1/H3 답 포함) |
| [docs/03_SRS.md](docs/03_SRS.md) · [docs/07_BD.md](docs/07_BD.md) · [docs/09_DD_core.md](docs/09_DD_core.md) | 요구사항 · 기본설계 · 상세설계 |
| [docs/07b_Product_Design_Specification.md](docs/07b_Product_Design_Specification.md) | 화면·디자인 명세 |
| [docs/04_TC-S.md](docs/04_TC-S.md) · [docs/14_TR.md](docs/14_TR.md) · [docs/15_TRP.md](docs/15_TRP.md) | 인수 시험 · 시험 결과 · 인수 판정 |

## 빠른 실행(개발 PC)
```powershell
cd jewelry_os
.venv\Scripts\python -m jewelry_os --env dev --data-dir $env:TEMP\jos-dev serve
```
