# PROJECT REGISTER — Jewelry OS R1

## 0. 인수인계 (항상 파일 맨 위)
- **직전에 한 일**: (2026-10-18) 실제 Google 폼 질문 ID 연결(D-18, SRS-004 r2, TC-S-057, TC-U-056~058) — 회귀 184 PASS. 그 전: H1/H3 PM Review r1 반영, H3 조건부 인수(시험 운영 모드만) — rev.7
- **진행 중 작업**: 없음
- **다음에 할 일**: 사용자 몫 — H3 추가 검증 조건 5개(아래 「인수 조건」), TBD-009 과세 유형 입력, 실 PC 설치(`docs/INSTALL_운영가이드.md`) · 미결정 사항 한눈에: `docs/미결정_사항.md`
- **사용자 대기**: 인수 조건 C-1~C-5 · TC-S-015·045·047 AWAITING_ACTUAL · 섹션 D 가정(ASM-002·015·017·018) 재확인 · TBD-001~006·008·009
- **재개 프롬프트**: 「lifecycle-pipeline으로 이 폴더의 Jewelry OS 프로젝트를 이어서 진행해줘 — 인수 조건 C-n 결과: (여기에)」

## 1. 헤더
- 프로젝트: Jewelry OS R1 (로컬 주문제작 운영 소프트웨어) + Product Design Specification
- 모드: Light (단독 수행·되돌리기 쉬움·외부 납품 없음. 단, worker 문서는 독립 문서)
- 단계 계획: 소프트웨어 조정 계획 — 결정 근거: 산출물이 소프트웨어뿐(자택 PC 로컬 웹앱). rev.1 PM 결정(사용자 확인 생략 지시)
- 진행 방식: 자동 진행 — 진행 중 H1·H3 생략(사용자 지시) 후 **사후 확인 r1 답변 반영(2026-10-17, docs/DECISION_LOG.md)**, 예외 정지도 하지 않고 해당 항목만 TBD/ASSUMED. H2(사람 시험)는 `AWAITING_ACTUAL`로 남긴다.
- 리비전: rev.7
- PRD 원본: Jewelry_OS_PRD_v1.0.md · 원본 버전 v1.0 Draft (2026-10-16 사용자 제공)
- 현재 게이트: 전 스테이지 마감 · **H3 조건부 인수 승인(2026-10-17) — 로컬 PC 시험 운영 모드만, 실고객 운영·실결제 비승인**. 인수 조건 C-1~C-5 대기
- 최종 갱신: 2026-10-18
- **모델 카탈로그**: 확인 2026-10-16 · C=claude-opus-5-5 · T1=claude-opus-5-5 · T2=claude-sonnet-5-5 · T3=claude-haiku-5-5 · UNRANKED=claude-fable-5-1(서열 미확인 — 사용자 확인 불가로 미사용) · 출처: 가용=실행 환경 모델 목록 / 서열=PM 판단(공식 서열 미확인 → ASM 아님, 기록만)
- **PM 세션**: claude-opus-5-5 · high · Doc Lock 충족(T1 이상). 한계: C와 T1이 같은 모델이라 “작성자와 다른 모델 검토”는 같은 모델의 독립 인스턴스로 대체(변경 로그 기록)
- 스테이지 완료 체크리스트:
  [x] 1 PRD해석 [x] 2 TRD(RTM 요약) [x] 3 SRS [x] 4 TC-S  `5 FUN MERGED→#7` `6 PBS MERGED→#7`
  [x] 7 BD+PDS  `8 TC-I MERGED→#7` [x] 9 DD 핵심 모듈만 `10 TC-U MERGED→#13` [x] 11·12 WBS·AID 묶음
  [x] 13 IMP    [x] 14 TR   [x] 15 TRP(조건부 — H2·H3 대기)

### 진척 대시보드
- **PRD 인수 현황(R1)**: 충족 30 / 조건부 충족 11 / 미충족 0 / 미판정 0 (PRD 요구 26 + IMPLICIT 14 + PDS 요청 1 = 41) · **분리 추적 미완료 1(REMOTE-ADMIN, R2)** · H3 = 조건부 인수 승인
- **해석 확인 현황**: EXP CONFIRMED 8 / ASSUMED 8 / UNCONFIRMED 0 / REJECTED 0 (H1 r1 답 반영)
- **SRS 상태 분포**: 167건 — PASS 159(그중 HUMAN 확인도 남은 10) · AWAITING_ACTUAL 7 · 미완료(R2) 1 · FAIL 0
- **시험(rev.7 + D-18 회귀)**: TC-U 105 · TC-I 13 · TC-S AGENT 60(+browser 6) = 184 PASS / TC-S HUMAN 3 AWAITING_ACTUAL
- **사람 대기 큐**: 인수 조건 5 (C-1~C-5)
- **리스크 요약**: 미해결 고아 0 / UNVERIFIED 가정 9 / 미해결 결함 0 / 최대 재터치 2 (EXP-016, ASM-005·019·020 각 1)

## 2. 추적 매트릭스

### 2-1. PRD 커버리지 (주 추적 기준)
| PRD ID | 원문 인용(요약 금지) | 분류 | 해석(EXP) | SRS | 경험 시나리오 TC | 인수 판정 | 근거 |
|--------|--------------------|------|-----------|-----|-----------------|----------|------|
| PRD-001 | 「`확정`은 … `Gate`는 해당 기능의 실사용을 막는 선행조건을 뜻한다.」 | 배경 | — (해석 규칙) | — | — | 비범위 | 문서 규칙 |
| PRD-002 | 「Jewelry OS는 … 주문형 제조 운영 소프트웨어다.」 | 배경 | — (요약) | — | — | 비범위 | §5~§15 재진술 |
| PRD-003 | 「아버님은 제한된 작업시간 안에 '승인이 필요한 주문'만 관리하고 CAD와 제조 품질에 집중한다.」 | 요구 | EXP-001, EXP-003, EXP-013 | 3건 (130, 132, 142) | 009, 015, 019, 036, 042, 043 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 015(HUMAN) — SRS 130, 142 |
| PRD-004 | 「KPI-01 데이터 완전성 … KPI-08 재작업·클레임 비율」 | 요구 | — (SRS 직접) | 4건 (124, 166, 168~169) | 037, 054 | 충족 | 14_TR·15_TRP |
| PRD-005 | 「독자 결제대행(PG) 또는 네이버페이 결제 세션을 직접 구축하지 않는다.」 | 배경 | — (비목표) | — | — | 비범위 | 설계 제약 |
| PRD-006 | 「최초 제작 가능성 … 아버님 최종 판단」 | 요구 | EXP-004, EXP-014 | 2건 (133, 143) | 001, 015, 026, 027 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 015(HUMAN) — SRS 133 |
| PRD-007 | 「작업시간: 자정 이전만 직접 업무, 공휴일 신규 수락 중단」 | 요구 | EXP-010 | 1건 (139) | 006 | 충족 | 14_TR·15_TRP |
| PRD-008 | 「UI 및 정책기록은 Asia/Seoul 시간대를 사용한다.」 | 요구 | — (SRS 직접) | 1건 (111) | 050 | 충족 | 14_TR·15_TRP |
| PRD-009 | 「네이버의 결제·취소·반품 원본 기록이 금전 거래 사실의 근거다.」 | 요구 | — (SRS 직접) | 1건 (112) | 029 | 충족 | 14_TR·15_TRP |
| PRD-010 | 「고객은 … Google Forms 제출. 폼 접수는 구매 계약이 아님.」~「10.」 | 요구 | EXP-012 | 1건 (141) | 002, 015 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 015(HUMAN) — SRS 141 |
| PRD-011 | 「자료 부족: `NEEDS_INFO`로 표시하고 추가 질문.」 외 | 요구 | EXP-007, EXP-009 | 3건 (125, 136, 138) | 004, 008, 039 | 충족 | 14_TR·15_TRP |
| PRD-012 | 「R1 E2E MVP … 결제·출시는 Gate 통과 시만 활성화」 | 배경 | — (릴리스 범위) | — | — | 비범위 | WBS 범위 |
| PRD-013 | 「API의 존재만으로 DONE 처리하지 않는다.」 | 요구 | — (SRS 규칙) | 규칙(SRS §0) | 전 TC-S | 충족 | 14_TR·15_TRP — 판정은 인수 시험 결과로만(14_TR) |
| PRD-014 | `FR-F-001`~`FR-F-010` | 요구 | — (SRS 직접) | 10건 (001~010) | 004, 022, 023, 046, 047 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 047(HUMAN) — SRS 001~003, 005~006 |
| PRD-015 | `FR-SYNC-001`~`010` | 요구 | — (SRS 직접) | 11건 (011~020, 167) | 008, 020, 021, 022, 044, 045, 046, 048, 052 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 045(HUMAN) — SRS 011 |
| PRD-016 | `FR-AI-001`~`009` | 요구 | — (SRS 직접) | 9건 (021~029) | 004, 020, 024, 046 | 충족 | 14_TR·15_TRP |
| PRD-017 | `FR-ADM-001`~`009` | 요구 | EXP-002, EXP-005, EXP-006 | 14건 (030~038, 131, 134~135, 153~154) | 003, 004, 005, 006, 007, 010, 025, 027, 032, 039, 041, 043 | 충족 | 14_TR·15_TRP |
| PRD-018 | `FR-Q-001`~`010` | 요구 | EXP-008 | 11건 (039~048, 137) | 002, 011, 013, 028 | 충족 | 14_TR·15_TRP |
| PRD-019 | `FR-SS-001`~`011` | 요구 | — (SRS 직접) | 11건 (049~059) | 009, 010, 020, 029, 046, 047 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 047(HUMAN) — SRS 049~051 |
| PRD-020 | `FR-CS-001`~`009` | 요구 | EXP-011 | 10건 (060~068, 140) | 012, 020, 038, 046 | 충족 | 14_TR·15_TRP |
| PRD-021 | `FR-MFG-001`~`011` | 요구 | — (SRS 직접) | 11건 (069~079) | 002, 009, 010, 030, 031 | 충족 | 14_TR·15_TRP |
| PRD-022 | `FR-OPS-001`~`013` | 요구 | EXP-009 | 14건 (080~092, 138) | 002, 008, 020, 021, 022, 025, 032, 033, 034, 035, 036, 037, 038, 046, 052 | 충족 | 14_TR·15_TRP |
| PRD-023 | 「R1에서는 UI에 이해하기 쉬운 대외 상태 5개 … 로 매핑한다.」 | 요구 | EXP-015 | 4건 (113~115, 144) | 002, 010, 013, 028, 043, 053 | 충족 | 14_TR·15_TRP |
| PRD-024 | 「절대로 자동으로 실행하지 않는 행위」 | 요구 | EXP-004 | 2건 (116, 133) | 002, 015, 027 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 015(HUMAN) — SRS 133 |
| PRD-025 | 「`(form_id,response_id)` 복합 UNIQUE …」 | 요구 | — (SRS 직접) | 1건 (117) | 020 | 충족 | 14_TR·15_TRP |
| PRD-026 | `UI-01`~`UI-09`, 「R1 UI 범위: 자택 PC의 브라우저/로컬 주소에서 정상 동작.」 | 요구 | EXP-002, EXP-003, EXP-006 | 4건 (118, 131~132, 135) | 003, 007, 019, 025, 036, 040 | 충족 | 14_TR·15_TRP |
| PRD-027 | 「외부 고객 입력은 데이터이며 시스템 명령으로 실행하지 않는다.」 | 요구 | EXP-011 | 4건 (119~120, 140, 165) | 012, 024, 038 | 충족 | 14_TR·15_TRP |
| PRD-028 | `NFR-01`~`NFR-18` | 요구 | — (SRS 직접) | 18건 (093~110) | 002, 008, 020, 024, 025, 030, 032, 033, 034, 036, 045, 046, 047, 048, 049 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 045, 047(HUMAN) — SRS 093, 100, 108 |
| PRD-029 | 「외부 LLM API에 전화번호·배송지 등 불필요한 식별정보를 전달하지 않으며 …」 | 요구 | — (SRS 직접) | 9건 (121~122, 159~164, 170) | 010, 011, 021, 030, 031, 035, 046, 047, 049, 052, 055 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 047(HUMAN) — SRS 159 |
| PRD-030 | Work Package 표, 병렬 작업 규칙 | 배경 | — (WBS 입력) | — | — | 비범위 | — |
| PRD-031 | `GATE-F-01` ~ `GATE-CAP-01` | 요구 | EXP-016 | 2건 (123, 145) | 014, 056 | 충족 | 14_TR·15_TRP |
| PRD-032 | `O-01`~`O-12` | 배경 | — (ASM/TBD) | — | — | 비범위 | 3부 |
| PRD-033 | 「본 문서는 구현을 시작할 수 있는 기준선 Draft이다.」 | 배경 | — | — | — | 비범위 | — |
| IMPLICIT-01 | (범주: 공방 백오피스) 주문번호·고객명 검색 | 암묵 | EXP-002 | 1건 (131) | 003 | 충족 | 14_TR·15_TRP |
| IMPLICIT-02 | 저장 안 된 변경 경고 | 암묵 | — | 1건 (146) | 007, 051 | 충족 | 14_TR·15_TRP |
| IMPLICIT-03 | 주문별 전체 이력 타임라인 | 암묵 | EXP-012 | 1건 (141) | 002, 015 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 015(HUMAN) — SRS 141 |
| IMPLICIT-04 | 목록 복귀 시 필터 유지 | 암묵 | EXP-002 | 1건 (131) | 003 | 충족 | 14_TR·15_TRP |
| IMPLICIT-05 | 비활성 버튼의 이유 표시 | 암묵 | EXP-013 | 1건 (142) | 009, 015 | 조건부 충족 | 14_TR·15_TRP — 남은 증거 015(HUMAN) — SRS 142 |
| IMPLICIT-06 | 참고 이미지 크게 보기 | 암묵 | — | 1건 (147) | 019, 051 | 충족 | 14_TR·15_TRP |
| IMPLICIT-07 | 처리 결과 즉시 피드백 | 암묵 | — | 1건 (148) | 019, 051 | 충족 | 14_TR·15_TRP |
| IMPLICIT-08 | 첫 실행 안내 | 암묵 | — | 1건 (149) | 001 | 충족 | 14_TR·15_TRP |
| IMPLICIT-09 | 한국식 날짜·금액 표기 | 암묵 | — | 1건 (150) | 013, 043 | 충족 | 14_TR·15_TRP |
| IMPLICIT-10 | 고객용 견적 문서(인쇄/복사) | 암묵 | EXP-008 | 2건 (137, 151) | 013 | 충족 | 14_TR·15_TRP |
| IMPLICIT-11 | (역번역 r1) 카카오·전화로 온 문의의 **수기 접수**(동의 증빙 필수, 출처 `수기`) | 암묵 | — | 1건 (155) | 017 | 충족 | 14_TR·15_TRP |
| IMPLICIT-12 | (역번역 r1) 잘못 누른 **직전 전이 되돌리기** — 사람이 수행한 직전 전이 1건만, 그 뒤 생긴 기록(결제·발주·파일 승인)이 없을 때, 사유 필수, 되돌림도 새 감사 이벤트 | 암묵 | — | 1건 (156) | 016 | 충족 | 14_TR·15_TRP |
| IMPLICIT-13 | (역번역 r1) **받을 돈**(잔금 미수) 목록과 월별 매출·공헌이익 요약 | 암묵 | — | 1건 (157) | 018, 037 | 충족 | 14_TR·15_TRP |
| IMPLICIT-14 | (역번역 r1) **고객별 이력**(과거 주문·사양·호수) 조회 — 자동 병합 없이 같은 `customer_id`로 묶인 주문만 | 암묵 | — | 1건 (158) | 018 | 충족 | 14_TR·15_TRP |
| PDS-REQ | (사용자 요청) production-ready Product Design Specification — 사용성·시각 완성도·일관성·접근성·반응형·예외 상태·구현 가능성 | 요청 | — | 2건 (152~153) | 041, 042 | 충족 | docs/07b_Product_Design_Specification.md, axe 0·대비 0 실패 |
| REMOTE-ADMIN | (H3 r1 분리 지시) 모바일 원격 관리자 사용 — 집 밖 휴대전화로 관리 화면 접속(HTTPS·인증·권한·PC 꺼짐 대응). PRD FR-ADM-009(P1·R2)·GATE-REMOTE-01 | 요구(R2) | — | 1건 (171) | TC-S-056(로컬 전용 확인만) | **미완료(R2)** | 로컬 전용(SRS-145, 127.0.0.1)과 분리 추적(D-12). R1 인수 판정 밖 |

### 1-2. TRD 요약 (스테이지 2 — Light, RTM 기록)
| TRD ID | 항목 | 내용 | 출처 |
|---|---|---|---|
| TRD-001 | 플랫폼 | Windows 10/11 자택 PC, 로컬 단일 프로세스, 127.0.0.1:8770 바인딩 | PRD §3·§10, ASM-013 |
| TRD-002 | 런타임·스택 | Python 3.13, FastAPI, SQLite(WAL, 외래키 ON), Jinja2 SSR, 바닐라 CSS/JS. 외부 CDN·빌드 단계 없음(오프라인 동작) | ASM-013 |
| TRD-003 | 브라우저 | Chrome·Edge 최신 2개 메이저 | PRD §10 |
| TRD-004 | 자동 기동·단일 인스턴스 | Windows 작업 스케줄러(로그온 트리거) 등록 스크립트 + 포트·잠금 파일 단일 인스턴스 | FR-SYNC-001, NFR-01 |
| TRD-005 | 외부 연동 | 어댑터 인터페이스(PRD §9.3) + R1 구현은 Mock/Manual. 네트워크 실패·401/403/429 모의 가능 | ASM-015 |
| TRD-006 | 비밀정보 | 환경변수 또는 `%APPDATA%\JewelryOS\secrets.env`(ACL 사용자 한정). 로그 마스킹 필터 | FR-OPS-003, NFR-07 |
| TRD-007 | 백업 | 일 1회(기동 시 26h 경과면 즉시) DB 온라인 백업 + 파일 → ZIP → Fernet 암호화, 30일 보관(설정), 복원 CLI | FR-OPS-004, NFR-08 |
| TRD-008 | 성능 | 로컬 기준 목록·상세 서버 응답 p95 300ms 이하(주문 1,000건 시험 데이터) | PM 제안 |
| TRD-009 | 시간 | 저장 UTC ISO-8601 + 원본 오프셋, 표시 Asia/Seoul | PRD §3 |
| TRD-010 | 환경 분리 | `JOS_ENV=test|dev|prod`, 환경별 데이터 폴더 | NFR-15 |

### 2-2. SRS 추적 (SRS → TC-S → 결과, 문서에서 계산)
| SRS | 출처 | 구분 · 검증 | TC-S | 결과 |
|---|---|---|---|---|
| SRS-001 | FR-F-001 | 운영 · D | TC-S-047 | AWAITING_ACTUAL |
| SRS-002 | FR-F-002 | 구현 + 운영 · T + D | TC-S-004, TC-S-023, TC-S-047 | PASS (+HUMAN 대기) |
| SRS-003 | FR-F-003 | 구현 · T | TC-S-023, TC-S-047 | PASS (+HUMAN 대기) |
| SRS-004 | FR-F-004 | 구현 · T | TC-S-023, TC-S-057 | PASS (r2 실제 폼 질문 연결 — 실계정 확인은 C-1) |
| SRS-005 | FR-F-005 | 운영 · D | TC-S-023, TC-S-047 | PASS (+HUMAN 대기) |
| SRS-006 | FR-F-006 | 구현 + 운영 · T + D | TC-S-023, TC-S-047 | PASS (+HUMAN 대기) |
| SRS-007 | FR-F-007 | 구조 · I | TC-S-023, TC-S-046 | PASS |
| SRS-008 | FR-F-008 | 구현 · T | TC-S-023 | PASS |
| SRS-009 | FR-F-009 | 구현 · T | TC-S-022 | PASS |
| SRS-010 | FR-F-010 | 구조 · T | TC-S-022 | PASS |
| SRS-011 | FR-SYNC-001 | 구현 · T+D | TC-S-044, TC-S-045 | PASS (+HUMAN 대기) |
| SRS-012 | FR-SYNC-002 | 구현 · T | TC-S-008, TC-S-048, TC-S-052 | PASS |
| SRS-013 | FR-SYNC-003 | 구현 · T | TC-S-048, TC-S-052 | PASS |
| SRS-014 | FR-SYNC-004 | 구현 · T | TC-S-020, TC-S-052 | PASS |
| SRS-015 | FR-SYNC-005 | 구현 · T | TC-S-020, TC-S-052 | PASS |
| SRS-016 | FR-SYNC-006 | 구현 · T | TC-S-020, TC-S-052 | PASS |
| SRS-017 | FR-SYNC-007 | 구현 · T | TC-S-008, TC-S-052 | PASS |
| SRS-018 | FR-SYNC-008 | 구현 · T | TC-S-022, TC-S-052 | PASS |
| SRS-019 | FR-SYNC-009 | 구현 · T | TC-S-021, TC-S-052 | PASS |
| SRS-020 | FR-SYNC-010 | R2 · — | TC-S-046 | PASS |
| SRS-021 | FR-AI-001 | 구현 · T | TC-S-024 | PASS |
| SRS-022 | FR-AI-002 | 구현 · T | TC-S-024 | PASS |
| SRS-023 | FR-AI-003 | 구현 · T | TC-S-004, TC-S-024 | PASS |
| SRS-024 | FR-AI-004 | 구현 · T | TC-S-024 | PASS |
| SRS-025 | FR-AI-005 | 구현 · T | TC-S-024 | PASS |
| SRS-026 | FR-AI-006 | 구현 · T | TC-S-020, TC-S-024 | PASS |
| SRS-027 | FR-AI-007 | 구현 · T | TC-S-024 | PASS |
| SRS-028 | FR-AI-008 | 구현 · T | TC-S-024 | PASS |
| SRS-029 | FR-AI-009 | 구조 · — | TC-S-046 | PASS |
| SRS-030 | FR-ADM-001 | 구현 · T | TC-S-007, TC-S-025 | PASS |
| SRS-031 | FR-ADM-002 | 구현 · T | TC-S-003 | PASS |
| SRS-032 | FR-ADM-003 | 구현 · T | TC-S-027 | PASS |
| SRS-033 | FR-ADM-004 | 구현 · T | TC-S-005 | PASS |
| SRS-034 | FR-ADM-005 | 구현 · T | TC-S-006 | PASS |
| SRS-035 | FR-ADM-006 | 구현 · T | TC-S-004 | PASS |
| SRS-036 | FR-ADM-007 | 구현 · T | TC-S-032 | PASS |
| SRS-037 | FR-ADM-008 | 구현 · T | TC-S-039 | PASS |
| SRS-038 | FR-ADM-009 | 구현 · T | TC-S-041 | PASS |
| SRS-039 | FR-Q-001 | 구현 · T | TC-S-002 | PASS |
| SRS-040 | FR-Q-002 | 구현 · T | TC-S-028 | PASS |
| SRS-041 | FR-Q-003 | 구현 · T | TC-S-028 | PASS |
| SRS-042 | FR-Q-004 | 구현 · T | TC-S-013 | PASS |
| SRS-043 | FR-Q-005 | 구현 · T | TC-S-011, TC-S-028 | PASS |
| SRS-044 | FR-Q-006 | 구현 · T | TC-S-028 | PASS |
| SRS-045 | FR-Q-007 | 구현 · T | TC-S-002 | PASS |
| SRS-046 | FR-Q-008 | 구현 · T | TC-S-011 | PASS |
| SRS-047 | FR-Q-009 | 구현 · T | TC-S-028 | PASS |
| SRS-048 | FR-Q-010 | 구현 · T | TC-S-013 | PASS |
| SRS-049 | FR-SS-001 | 운영 · D | TC-S-047 | AWAITING_ACTUAL |
| SRS-050 | FR-SS-002 | 운영 · D | TC-S-047 | AWAITING_ACTUAL |
| SRS-051 | FR-SS-003 | 운영 · D | TC-S-047 | AWAITING_ACTUAL |
| SRS-052 | FR-SS-004 | 구현 · T | TC-S-009, TC-S-029 | PASS |
| SRS-053 | FR-SS-005 | 구조 · I | TC-S-046 | PASS |
| SRS-054 | FR-SS-006 | R2 · — | TC-S-046 | PASS |
| SRS-055 | FR-SS-007 | R2 · — | TC-S-046 | PASS |
| SRS-056 | FR-SS-008 | R2 · — | TC-S-046 | PASS |
| SRS-057 | FR-SS-009 | 구현 · T | TC-S-009, TC-S-010 | PASS |
| SRS-058 | FR-SS-010 | 구현 · T | TC-S-020, TC-S-029 | PASS |
| SRS-059 | FR-SS-011 | 구조 · T | TC-S-046 | PASS |
| SRS-060 | FR-CS-001 | 구현 · T | TC-S-012 | PASS |
| SRS-061 | FR-CS-002 | 구현 · T | TC-S-012, TC-S-038 | PASS |
| SRS-062 | FR-CS-003 | 구현 · T | TC-S-012 | PASS |
| SRS-063 | FR-CS-004 | 구현 · T | TC-S-012 | PASS |
| SRS-064 | FR-CS-005 | R2 · — | TC-S-046 | PASS |
| SRS-065 | FR-CS-006 | R2 · — | TC-S-046 | PASS |
| SRS-066 | FR-CS-007 | R2 · — | TC-S-046 | PASS |
| SRS-067 | FR-CS-008 | R2 · — | TC-S-046 | PASS |
| SRS-068 | FR-CS-009 | 구조 · T | TC-S-020 | PASS |
| SRS-069 | FR-MFG-001 | 구현 · T | TC-S-002, TC-S-009, TC-S-030 | PASS |
| SRS-070 | FR-MFG-002 | 구현 · T | TC-S-030 | PASS |
| SRS-071 | FR-MFG-003 | 구현 · T | TC-S-002, TC-S-030 | PASS |
| SRS-072 | FR-MFG-004 | 구현 · T | TC-S-030 | PASS |
| SRS-073 | FR-MFG-005 | 구현 · T | TC-S-002, TC-S-030 | PASS |
| SRS-074 | FR-MFG-006 | 구현 · T | TC-S-002, TC-S-030 | PASS |
| SRS-075 | FR-MFG-007 | 구현 · T | TC-S-031 | PASS |
| SRS-076 | FR-MFG-008 | 구현 · T | TC-S-002, TC-S-031 | PASS |
| SRS-077 | FR-MFG-009 | 구현 · T | TC-S-031 | PASS |
| SRS-078 | FR-MFG-010 | 구현 · T | TC-S-002, TC-S-031 | PASS |
| SRS-079 | FR-MFG-011 | 구현 · T | TC-S-010, TC-S-031 | PASS |
| SRS-080 | FR-OPS-001 | 구현 · T+I | TC-S-002 | PASS |
| SRS-081 | FR-OPS-002 | 구현 · T | TC-S-032, TC-S-035 | PASS |
| SRS-082 | FR-OPS-003 | 구현 · T | TC-S-033 | PASS |
| SRS-083 | FR-OPS-004 | 구현 · T | TC-S-034 | PASS |
| SRS-084 | FR-OPS-005 | 구현 · T | TC-S-035, TC-S-057 | PASS |
| SRS-085 | FR-OPS-006 | 구현 · T | TC-S-021, TC-S-052 | PASS |
| SRS-086 | FR-OPS-007 | 구현 · T | TC-S-020 | PASS |
| SRS-087 | FR-OPS-008 | 구현 · T | TC-S-008 | PASS |
| SRS-088 | FR-OPS-009 | 구현 · T | TC-S-022, TC-S-025 | PASS |
| SRS-089 | FR-OPS-010 | 구현 · T | TC-S-037 | PASS |
| SRS-090 | FR-OPS-011 | 구현 · T | TC-S-038 | PASS |
| SRS-091 | FR-OPS-012 | 구조 · I | TC-S-046 | PASS |
| SRS-092 | FR-OPS-013 | 구현 · T | TC-S-036 | PASS |
| SRS-093 | NFR-01 | 구현 · D | TC-S-045 | AWAITING_ACTUAL |
| SRS-094 | NFR-02 | 구현 · T | TC-S-048 | PASS |
| SRS-095 | NFR-03 | 구현 · T | TC-S-008 | PASS |
| SRS-096 | NFR-04 | 구현 · T | TC-S-020 | PASS |
| SRS-097 | NFR-05 | 구현 · T | TC-S-020 | PASS |
| SRS-098 | NFR-06 | 구현 · T | TC-S-025 | PASS |
| SRS-099 | NFR-07 | 구현 · T | TC-S-033 | PASS |
| SRS-100 | NFR-08 | 구현 · T+D | TC-S-034, TC-S-045 | PASS (+HUMAN 대기) |
| SRS-101 | NFR-09 | 구현 · T | TC-S-030 | PASS |
| SRS-102 | NFR-10 | 구현 · T | TC-S-002, TC-S-032 | PASS |
| SRS-103 | NFR-11 | 구현 · T | TC-S-025 | PASS |
| SRS-104 | NFR-12 | R2 · — | TC-S-046 | PASS |
| SRS-105 | NFR-13 | 구조 · I | TC-S-046 | PASS |
| SRS-106 | NFR-14 | 구현 · T | TC-S-024 | PASS |
| SRS-107 | NFR-15 | 구현 · T | TC-S-049 | PASS |
| SRS-108 | NFR-16 | 운영 · D | TC-S-047 | AWAITING_ACTUAL |
| SRS-109 | NFR-17 | 구현 · T | TC-S-034 | PASS |
| SRS-110 | NFR-18 | 구현 · T | TC-S-036 | PASS |
| SRS-111 | PRD-008 §3 | 구현 · T | TC-S-050 | PASS |
| SRS-112 | PRD-009 §4 | 구현 · T+I | TC-S-029 | PASS |
| SRS-113 | PRD-023 §8.1 | 구현 · T | TC-S-002 | PASS |
| SRS-114 | PRD-023 §8.1 | 구현 · T | TC-S-002, TC-S-028 | PASS |
| SRS-115 | PRD-023 §8.2 | 구현 · T | TC-S-010, TC-S-053 | PASS |
| SRS-116 | PRD-024 §8.3 | 구현 · T | TC-S-002, TC-S-027 | PASS |
| SRS-117 | PRD-025 §9.1 | 구현 · T | TC-S-020 | PASS |
| SRS-118 | PRD-026 §10 | 구현 · T | TC-S-040 | PASS |
| SRS-119 | PRD-027 §11.1 | 구현 · T | TC-S-024 | PASS |
| SRS-120 | PRD-027 §11.2 | 구현 · T | TC-S-012 | PASS |
| SRS-121 | PRD-029 §13.1 | 구현 · T | TC-S-035 | PASS |
| SRS-122 | PRD-029 §13.3 | 구현 · T | TC-S-021, TC-S-052 | PASS |
| SRS-123 | PRD-031 §15 | 구현 · T | TC-S-014 | PASS |
| SRS-124 | PRD-004 §1.2 | 구현 · T | TC-S-037 | PASS |
| SRS-125 | PRD-011 §5.2 | 구현 · T | TC-S-039 | PASS |
| SRS-130 | EXP-001 | 구현 · T | TC-S-015, TC-S-042, TC-S-043 | PASS (+HUMAN 대기) |
| SRS-131 | EXP-002, IMPLICIT-01, IMPLICIT-04 | 구현 · T | TC-S-003 | PASS |
| SRS-132 | EXP-003 | 구현 · T | TC-S-019, TC-S-036 | PASS |
| SRS-133 | EXP-004 | 구현 · T | TC-S-015, TC-S-027 | PASS (+HUMAN 대기) |
| SRS-134 | EXP-005, H1 r1 Q2 | 구현 · T | TC-S-005 | PASS |
| SRS-135 | EXP-006 | 구현 · T | TC-S-007, TC-S-025 | PASS |
| SRS-136 | EXP-007 | 구현 · T | TC-S-004 | PASS |
| SRS-137 | EXP-008 | 구현 · T | TC-S-013 | PASS |
| SRS-138 | EXP-009 | 구현 · T | TC-S-008 | PASS |
| SRS-139 | EXP-010 | 구현 · T | TC-S-006 | PASS |
| SRS-140 | EXP-011 | 구현 · T | TC-S-012 | PASS |
| SRS-141 | EXP-012, IMPLICIT-03 | 구현 · T | TC-S-002, TC-S-015 | PASS (+HUMAN 대기) |
| SRS-142 | EXP-013, IMPLICIT-05 | 구현 · T | TC-S-009, TC-S-015 | PASS (+HUMAN 대기) |
| SRS-143 | EXP-014 | 구현 · T | TC-S-001, TC-S-026 | PASS |
| SRS-144 | EXP-015 | 구현 · T | TC-S-002, TC-S-013, TC-S-043 | PASS |
| SRS-145 | EXP-016 | 구현 · T | TC-S-014, TC-S-056 | PASS |
| SRS-146 | IMPLICIT-02 | 구현 · T | TC-S-007, TC-S-051 | PASS |
| SRS-147 | IMPLICIT-06 | 구현 · T | TC-S-019, TC-S-051 | PASS |
| SRS-148 | IMPLICIT-07 | 구현 · T | TC-S-019, TC-S-051 | PASS |
| SRS-149 | IMPLICIT-08 | 구현 · T | TC-S-001 | PASS |
| SRS-150 | IMPLICIT-09 | 구현 · T | TC-S-013, TC-S-043 | PASS |
| SRS-151 | IMPLICIT-10 | 구현 · T | TC-S-013 | PASS |
| SRS-152 | 사용자 요청(2026-10-16) | 구현 · T | TC-S-042 | PASS |
| SRS-153 | FR-ADM-009, 사용자 요청 | 구현 · T | TC-S-041 | PASS |
| SRS-154 | FR-ADM-003, ASM-014 | 구현 · T | TC-S-010, TC-S-043 | PASS |
| SRS-155 | IMPLICIT-11 | 구현 · T | TC-S-017 | PASS |
| SRS-156 | IMPLICIT-12 | 구현 · T | TC-S-016 | PASS |
| SRS-157 | IMPLICIT-13 | 구현 · T | TC-S-018, TC-S-037 | PASS |
| SRS-158 | IMPLICIT-14 | 구현 · T | TC-S-018 | PASS |
| SRS-159 | PRD-029 §13.1 | 운영 · D | TC-S-047 | AWAITING_ACTUAL |
| SRS-160 | PRD-029 §13.1 | 구현 · T+I | TC-S-049 | PASS |
| SRS-161 | PRD-029 §13.2 | 구현 · T | TC-S-030 | PASS |
| SRS-162 | PRD-029 §13.2 | 구현 · T | TC-S-031 | PASS |
| SRS-163 | PRD-029 §13.2, H1 r1 Q10 | 구현 · T | TC-S-010, TC-S-011, TC-S-031, TC-S-055 | PASS |
| SRS-164 | PRD-029 §13.2 | 구현 · T+I | TC-S-010, TC-S-046 | PASS |
| SRS-165 | PRD-027 §11.1 | 구현 · T | TC-S-012, TC-S-038 | PASS |
| SRS-166 | PRD-004 KPI-04 | 구현 · T | TC-S-037 | PASS |
| SRS-167 | FR-SYNC-002~009 공통 | 구현 · T | TC-S-052 | PASS |
| SRS-168 | PRD-004 KPI-04·06, ASM-019, H1 r1 Q9 | 구현 · T | TC-S-037, TC-S-054 | PASS |
| SRS-169 | PRD-004 KPI, H1 r1 Q9 | 구현 · T | TC-S-054 | PASS |
| SRS-170 | H1 r1 Q10, PRD-029 §13.2 | 구현 · T | TC-S-055 | PASS |
| SRS-171 | FR-ADM-009, GATE-REMOTE-01, H3 r1 | 구현(R2) · T(R2) | TC-S-056 | 미완료(R2) |
- 집계(표에서 셈): 167건 — PASS 149 · PASS (+HUMAN 대기) 10 · AWAITING_ACTUAL 7 · 미완료(R2) 1. TC-U·TC-I 판정은 14_TR §3·§8.

### 2-3. 작업 추적 (WBS → 구현 → 시험)
| WBS | 작업 | 산출물(코드) | 시험 증거 | 상태 |
|---|---|---|---|---|
| WBS-001 | 기반: 설정·DB·시계·보안·마스킹·Gate·감사 | core/*, services/settings·audit | TC-U-100~111·120~123, TC-S-025·033·049 | DONE |
| WBS-002 | 상태·전이·차단·문구 🔒 | domain/* | TC-U-001~039, TC-I-004·006·007, TC-S-053 | DONE |
| WBS-003 | 접수: 매핑·동기화·Google 어댑터·파일·수기 | services/intake, adapters/google | TC-U-040~055, TC-I-001·011, TC-S-020~023·052 | DONE |
| WBS-004 | AI 정리 | adapters/ai, services/analysis | TC-U-080~098, TC-I-002, TC-S-024 | DONE (실 API 미검증 — TBD-004) |
| WBS-005 | 주문 서비스 | services/orders·capacity | TC-S-004~006·016·032·039 | DONE |
| WBS-006 | 시안·견적·동의·재견적·고객 문서 | services/quotes | TC-U-060~069, TC-I-012, TC-S-011·013·028 | DONE |
| WBS-007 | 결제 | services/payments | TC-U-033~035, TC-I-003, TC-S-009·010·029 | DONE |
| WBS-008 | 제조 | services/manufacturing | TC-U-070~074, TC-I-005, TC-S-030·031 | DONE |
| WBS-009 | CS | services/cs | TC-I-010, TC-S-012·038 | DONE |
| WBS-010 | 운영: 알림·백업·기동·스케줄러·고객·실적 | services/alerts·backup·system·customers·metrics | TC-U-130~145, TC-I-008·009, TC-S-008·018·034~037·048 | DONE |
| WBS-011 | 웹: 라우트·템플릿·토큰·JS | web/* | TC-S-001~003·007·019·040~043·051 | DONE |
| WBS-012 | CLI·배포·구조 검사 | cli.py, tools/* | TC-S-044·046 (재부팅 실기 TC-S-045 대기) | DONE (실기 대기) |

### 2-4. 설계·시험 ID 색인 (문서에서 추출 — 내용은 각 문서가 원본)
| ID 범위 | 개수 | 문서 | 상태 |
|---|---|---|---|
| BD-001, BD-002, BD-003, BD-004, BD-005, BD-006, BD-007, BD-008 | 8 | docs/07_BD.md | 게이트 PASS(6절) · 시험 증거 14_TR |
| FUN-001, FUN-002, FUN-003, FUN-004, FUN-005, FUN-006, FUN-007, FUN-008, FUN-009, FUN-010, FUN-011, FUN-012 | 12 | docs/07_BD.md | 게이트 PASS(6절) · 시험 증거 14_TR |
| PBS-001, PBS-002, PBS-003, PBS-004, PBS-005, PBS-006, PBS-007 | 7 | docs/07_BD.md | 게이트 PASS(6절) · 시험 증거 14_TR |
| TC-I-001, TC-I-002, TC-I-003, TC-I-004, TC-I-005, TC-I-006, TC-I-007, TC-I-008, TC-I-009, TC-I-010, TC-I-011, TC-I-012, TC-I-013 | 13 | docs/07_BD.md | 게이트 PASS(6절) · 시험 증거 14_TR |
| DD-001, DD-002, DD-003, DD-004, DD-005, DD-006, DD-007, DD-008, DD-009, DD-010 | 10 | docs/09_DD_core.md | 게이트 PASS(6절) · 시험 증거 14_TR |
| WBS-001, WBS-002, WBS-003, WBS-004, WBS-005, WBS-006, WBS-007, WBS-008, WBS-009, WBS-010, WBS-011, WBS-012 | 12 | docs/11_WBS_AID.md | 게이트 PASS(6절) · 시험 증거 14_TR |

### 2-5. H3 인수 조건 (2026-10-17 조건부 인수 승인 — 실고객 운영·실결제 비승인)
| 조건 | 내용 | 확인 방법 | 상태 |
|---|---|---|---|
| C-1 | 실제 Google Forms 및 AI API 연동 시험 | TBD-004 · 시험 응답 1건 → 설정 > 폼 매핑에서 질문 연결(INSTALL §5, D-18) → TC-S-024·052·057을 실계정으로 재실행(14_TR §6) | OPEN |
| C-2 | 아버님 사용성 시험 | TC-S-015(14_TR 부록 A) | OPEN |
| C-3 | 실제 자택 PC 재부팅·복구 시험 | TC-S-045(14_TR 부록 B, INSTALL 가이드 §6) | OPEN |
| C-4 | 조건부 충족 11개 항목의 시험 증빙 확인 | 15_TRP §2 조건부 행의 남은 증거(TC-S-015·045·047) | OPEN |
| C-5 | 스마트스토어 실결제 및 소비자 계약 정책 검증 | TC-S-047(e)·GATE-SS-01·SS-02·LEGAL-01, TBD-002 | OPEN |
- 운영 규칙(D-11): C-1~C-5 완료 전에는 GATE-OPS-01·LEGAL-01·SS-02를 통과로 기록하지 않는다 — 앱은 시험 운영 모드(실결제 요청 차단)를 유지한다.

## 3. 가정 레지스터
| ASM ID | 가정 내용 | 근거 | 검증 방법 | 검증 시점 | 상태 | 매달린 하류 |
|--------|----------|------|----------|----------|------|------------|
| ASM-001 | 사용자 확인(H1·H3) 없이 모든 해석을 PM 제안값으로 진행해도 된다 | 사용자 지시(2026-10-16) | 사후 확인요청 파일 응답 | 프로젝트 종료 후 | CONFIRMED(사후 확인 r1) | EXP-001~016, IMPLICIT-01~10 |
| ASM-002 | 아버님은 노안·중간 수준 디지털 숙련도 — 본문 16px·한국어 라벨 기본 | “아버님” 호칭, 직장 병행 | 사용성 시험(HUMAN) | #14 | UNVERIFIED | EXP-001, PDS 타이포 |
| ASM-003 | 명시적 승인 = 요약+확인 대화상자, 기본 미체크, 일괄 승인 없음 | FR-ADM-003·§8.3 | 사후 확인 | 종료 후 | UNVERIFIED | EXP-004 |
| ASM-004 | 계정 역할 2종 maker/operator, 권한 분리 | §2 RACI | 사후 확인 | 종료 후 | CONFIRMED(Q1) | EXP-014 |
| ASM-005 | 상한 초과 시 ~~사유 입력 후 수동 수락~~ → 운영자 예외 승인(사유·예상 납기) + 아버님 제작 가능 확인 후 수락 | FR-ADM-004 “자동 수락 차단” | 사후 확인 | 종료 후 | CONFIRMED-수정(Q2 → D-2) | EXP-005 |
| ASM-006 | 세션 비활성 잠금 15분(설정 가능), 60초 전 경고 | 업계 관행 | 사후 확인 | 종료 후 | CONFIRMED(Q3) | EXP-006 |
| ASM-007 | 알림 휴지 시간 00:00–07:00, 공휴일 목록은 설정에서 수동 관리(2026–27 기본값 제공) | §3 “자정 이전만” | 사후 확인 | 종료 후 | CONFIRMED(Q4) | EXP-010 |
| ASM-008 | 경고 임계: 동기화 15분·백업 26h·디스크 10%·고객응답 3일·납기 3일 | PM 제안 | 사후 확인 | 종료 후 | CONFIRMED(Q5) | EXP-003, EXP-007 |
| ASM-009 | CS 초안은 승인 후에만 복사 가능 | FR-CS-003 | 사후 확인 | 종료 후 | UNVERIFIED | EXP-011 |
| ASM-010 | 대외 5단계 매핑(해석서 EXP-015) | §8.1 | 사후 확인 | 종료 후 | CONFIRMED(Q6) | EXP-015 |
| ASM-011 | Gate 상태는 운영자가 설정 화면에서 기록, 미통과 시 실거래 기능을 시험 모드로 제한 | §15 | 사후 확인 | 종료 후 | UNVERIFIED | EXP-016 |
| ASM-012 | R1 고객 견적 문서 = 인쇄용 HTML(브라우저 PDF 저장)+복사 텍스트, 정식 PDF는 R2 | UI-03 vs FR-Q-010(P1·R2) | 사후 확인 | 종료 후 | CONFIRMED(Q7) | IMPLICIT-10 |
| ASM-013 | 자택 PC는 Windows 10/11, 스택 = Python 3.13 + FastAPI + SQLite(WAL) + Jinja2 서버 렌더링 + 바닐라 CSS/JS(빌드·CDN 없음) | O-08 미결, 개발 환경 Windows | 실 PC 설치 시험 | #14 | UNVERIFIED | TRD-001~004, BD 전체, 구현 전체 |
| ASM-014 | 종료 상태 DECLINED·CANCELLED 신설 | FR-ADM-003 거절, FR-MFG-011 취소 | 사후 확인 | 종료 후 | CONFIRMED(Q6) | SRS-154, EXP-015 |
| ASM-015 | Google Forms·Drive·Claude 실제 어댑터는 구현하되 HTTP 모의 서버 계약 시험으로만 검증, 네이버·카카오는 Manual 어댑터(PRD R1 전략) | 실계정·키 없음 | 실계정 시험(TBD-004) | 키 제공 후 | UNVERIFIED | SRS-012~019, 021~028, 052, 167 |
| ASM-016 | 예약금 = 합계/3 원 단위 올림, 잔금 = 합계 − 예약금 (정책 설정으로 변경 가능) | PRD “합계/반올림 검증”만 기술 | 사업 책임자 결제 정책 승인 | GATE-SS-02 | CONFIRMED(Q8) | SRS-047 |
| ASM-017 | 수기 접수 허용(동의 증빙 필수) | 역번역 r1 | 사후 확인 | 종료 후 | UNVERIFIED | SRS-155 |
| ASM-018 | 직전 전이 되돌리기 허용(조건부) | 역번역 r1 | 사후 확인 | 종료 후 | UNVERIFIED | SRS-156 |
| ASM-019 | KPI 계산 규칙(세금 제외 = 총액÷1.1, 유입비 = 월·경로 광고비 ÷ 문의 수, KPI-04 분모 = 전체 문의) | PRD KPI 정의에 계산식 없음 | 사업 책임자 확인 | 첫 광고 전(O-11) | CONFIRMED-수정(Q9 → D-9) | SRS-168, TC-S-037 |
| ASM-020 | 변경 요청은 발주 전에만, 발주 후 고객 변경은 REWORK(귀책 고객) + 잔금 정산 | 공장 발주 후 사양 변경은 재제작 | 아버님 확인 | 종료 후 | CONFIRMED-수정(Q10 → D-10) | BD §6.3, TBD-007 |
| ASM-021 | 공헌이익의 광고비 배분은 문의당 광고비, CAC는 별도 지표(D-13) | D-9가 배분 방식을 정하지 않음 | 사업 책임자 확인 | 첫 광고 전 | UNVERIFIED | SRS-168 ③, TC-S-054 |

### 3-2. TBD 목록
| TBD ID | 내용 | 종류 | 대상 스테이지 | 출처(어디서 넘겼나) | 상태 |
|--------|------|------|-------------|------------------|------|
| TBD-001 | 브랜드명·로고(앱·고객 문서 머리) | 정보 부족 | — (사용자 입력) | PDS | CLOSED — D-19: 틴틴골드(tntngold), 로고 적용 |
| TBD-002 | 고객 고지·동의·환불 최종 문구(O-10) | 정보 부족 | — (사업 책임자, GATE-LEGAL-01) | PRD O-10 | OPEN — 자리표시 문구 + 정책 버전 관리 |
| TBD-003 | 무료 시안 범위·횟수·공수 상한(O-03) | 정보 부족 | — (아버님·사업 책임자) | PRD O-03 | OPEN — 설정값(기본 1버전) |
| TBD-004 | Google/Naver/Kakao/LLM 실계정 연동 시험 | 정보 부족 | — (계정·키 제공 후) | ASM-015 | OPEN — HTTP 모의 서버 계약 시험 PASS(TC-S-052), 실계정 재실행 대기 |
| TBD-005 | 개인정보 보존기간·백업 보관 주기(O-12) | 정보 부족 | — (사업 책임자) | PRD O-12 | OPEN — 설정값(백업 30일 보관 기본) |
| TBD-006 | 실 PC 재부팅·복원 리허설·아버님 사용성 시험·운영 증거 | 정보 부족 | #14 (HUMAN) | SRS-093·100, TC-S-015·045·047 | OPEN — 절차 14_TR 부록 A·B·C |
| TBD-007 | 발주 후 고객 요청 변경의 추가 비용 결제 방식(별도 결제 요청 vs 잔금 가산) | 정보 부족 | — (사업 책임자, GATE-SS-02) | BD r3 §6.3 | CLOSED — D-10·D-15: 변경 주문 + 별도 결제 요청(CHANGE) |
| TBD-008 | 백업 ZIP 전체를 메모리에서 암호화 — 데이터가 수 GB가 되면 스트리밍 암호화 필요 | 하류 이월 | R2 | DD-008 | OPEN — R1 데이터 규모(수백 MB)에서는 영향 없음 |
| TBD-009 | 실제 사업자 과세 유형(일반·간이·면세)과 간이과세면 업종 부가가치율 | 정보 부족 | — (사업 책임자 → 설정 → 운영 정책) | D-9·D-14 | OPEN — 기본 ‘미설정’(세금 포함 견적 공헌이익 미산정) |

## 4. 고아 리포트
| 유형 | 항목 ID | 설명 | 발견일 |
|------|---------|------|--------|
| (없음) | | | |

## 5. 변경 로그
| 일시 | 변경 항목 | 내용 | 사유 | 영향받은 하류 | 재터치 |
|------|----------|------|------|--------------|--------|
| 2026-10-16 | (진행 방식) | 휴먼 게이트 H1·H3 생략, 예외 정지 없음 | 사용자 명시 지시 | 전 EXP → ASSUMED(ASM-001) | — |
| 2026-10-16 | (범위) | 범위 = 구현·시험까지 파이프라인 끝까지 | 사용자 정정(“구현 및 시험까지”) | WBS 범위 R0+R1 | — |
| 2026-10-16 | (단계 계획) | 소프트웨어 조정 계획 채택, PRD 항목은 절 단위 PRD-NNN | PRD가 원문 ID 보유 | SRS가 원문 ID 단위 추적 | — |
| 2026-10-16 | (카탈로그) | C=T1=opus-5-5, fable-5-1 UNRANKED 미사용 | 서열 미확인·사용자 확인 불가 | 검토자는 같은 모델의 독립 인스턴스 | — |
| 2026-10-16 | 스테이지 1 | 역번역 r1 FAIL(차단 18) → 전부 반영 r2, IMPLICIT-11~14 신설 | 맹검 역번역 | SRS-155~158 | — |
| 2026-10-16 | 스테이지 3 | 검토 r1 FAIL(차단 11) → r2 FAIL(신규 4) → r3 반영 마감. Google 실제 어댑터 R1 구현으로 범위 변경(B-01), Gate 효과 강화(B-02·R-01), 차단 표 신설(B-04) | 적대적 검토 | EXP-011·015·016 재터치 1회, ASM-015 갱신, TC-S·PDS 반영 | EXP-016 2회, EXP-015 1회 |
| 2026-10-16 | 스테이지 4·7 | BATCHED — #4 TC-S 검토와 #7 BD·PDS 검토를 병렬 실행(게이트는 개별 판정) | 사용자 “중단 없이” 지시, 대기 시간 단축 | 양쪽 지적 교차 반영 | — |
| 2026-10-16 | 스테이지 4 | TC-S r1 FAIL(10) → r2 FAIL(미해소 3·신규 3) → r3 반영 PM 마감. 검토자가 자기 r1 제안 오류 1건(되돌리기 범위) 자진 정정 | 적대적 검토(C) | SRS-168 신설, ASM-019 | TC-S 3회 |
| 2026-10-16 | 스테이지 7 | BD r1 FAIL(13) → r2 FAIL(미해소 3·신규 7) → r3 반영 PM 마감. 상류 재터치: SRS §3-1 NO_CONSENT 행, EXP-002 검토 필요 조건 | 적대적 검토 | SRS 재터치 1회, EXP-002 재터치 1회, ASM-020, TBD-007 | BD 3회 |
| 2026-10-16 | 스테이지 9·11·12·13 | BATCHED — 문서 게이트 검토와 구현(#13 서비스 계층)을 병행. fan-out 없음(PM 직접 구현) | 사용자 “중단 없이” 지시 | 검토 지적은 구현에 역반영 | — |
| 2026-10-16 | (실행) | 자동 기동 상한 회차 규칙: 핵심 문서 #1·#3·#4·#7 모두 r3(상한)에서 PM 판정으로 마감, OVERRULED 0 | 회차 상한 | 남은 지적 0 | — |
| 2026-10-16 | 스테이지 9·11·12 | DD r1 FAIL(15) → r2 FAIL(미해소 2·신규 6) → r3 반영 PM 마감, WBS·AID r2 마감. 코드 동시 정정 | 적대적 검토 | BD PBS 재터치 1회, DD-002 재시도 주체 = 결제 입력 operator | DD 3회 |
| 2026-10-17 | 스테이지 13·14 | 시험 160건 작성·수행, 제품 결함 22건 발견·수정(치명 2: CSRF 빈 토큰·한국어 플래시 쿠키) | 인수·통합·브라우저 시험 | 14_TR §4 | — |
| 2026-10-17 | 동결 BD-001·BD-002 | 아이콘 이름을 스프라이트 별칭으로(BD-001), DETAIL_CAD의 build_packet 차단 문구 = G-DESIGN-UNAPPROVED(BD-002 check). 계약(이름·전이·차단 표) 불변 | 시험 결함 DEF-004·009 | PDS 아이콘 별칭 1줄 | — |
| 2026-10-17 | 문서 정정 | 해석서 분류 집계 24·9 → 26·7(표에서 다시 셈), TC-S-002 ⑪ 공헌이익 식 = SRS-168, BD-002 시험 주문 규칙에 맞게 구현 정정(DEF-008) | 요약 숫자 불일치·문서 간 불일치 | RTM 대시보드 | 해석서 1회 |
| 2026-10-17 | 스테이지 15 | PRD 인수 판정 41건 — 충족 30 / 조건부 11 / 미충족 0. 조건부 = HUMAN 증거만 남음 | 15_TRP | H1H3 사후 확인 요청 작성 | — |
| 2026-10-17 | H1/H3 사후 확인 r1 | Q1·3·4·5·6·7·8 승인, Q2·Q9·Q10 수정 승인, H3 조건부 인수(시험 운영 모드만, 실고객·실결제 비승인), 원격 관리자 분리 | PRD 작성자 답 | DECISION_LOG D-1~D-17, EXP 8건 CONFIRMED, ASM 12건 확인(3건 수정) | ASM-005·019·020 1회 |
| 2026-10-17 | 역방향 반영 | SRS-134·163·168 개정, SRS-169·170·171 신설 · BD-008 권한 4행·§6.3 REWORK · TC-S-005 r2, TC-S-054~056 신설 · DD-001 스키마 v2(이행 자동) · 코드 Q2·Q9·Q10 | H1 수정 승인 | 회귀 179 PASS(14_TR §8) | SRS 1회, BD 1회, TC-S 1회 |
| 2026-10-17 | 설치·배포 | requirements·설치/업데이트/실행 스크립트·키 관리 CLI(set-secret·export/import-keys)·자동 시작 pythonw·`.gitignore`·README·`docs/INSTALL_운영가이드.md`, anthropic 1.15.0 고정 + 어댑터 계약 시험, 깨끗한 설치 확인 | 사용자 요청(실행 방법·Git·아버님 PC 설치) | 14_TR §8, 회귀 179 PASS | — |
| 2026-10-18 | 브랜드(D-19) | 상호 틴틴골드·로고를 앱·로그인·고객 견적 문서·파비콘에 적용, TBD-001 종료, `docs/미결정_사항.md` 정리 | 사업 책임자 확정 | 회귀 185 PASS(브라우저·접근성 포함) | — |
| 2026-10-24 | 화면 결함(설정 메뉴) | 설정 탭이 줄어들어 글자 줄바꿈·글머리 기호 표시·마지막 탭 잘림 → 탭 줄어듦 방지·한 줄 표시, 설정 화면 빈 레일 열 제거, 600px 이상 줄바꿈. CSS·JS 링크에 내용 해시 `?v=` 추가(업데이트 뒤 옛 CSS 캐시 방지). TC-S-041에 설정 탭 검사 추가 | 사용자 보고 | 회귀 185 PASS | — |
| 2026-10-24 | 화면 결함(알림 패널) | 알림 항목이 타임라인 2열 격자의 28px 아이콘 칸에 들어가 글자가 한 자씩 줄바꿈 → 전용 목록(종류 아이콘·제목·“N분 전”) | 사용자 보고 | 새 브라우저 시험(1280·320px), 회귀 186 PASS | — |
| 2026-10-24 | 폼 연결(D-20) | 틴틴골드 의뢰 폼 ID 코드 기본값, Google 모드 ‘자동’ 추가(연결 시 실계정·시험 폼), 앱 안 OAuth 연결 시 즉시 전환 | 사용자 요청 | 회귀 189 PASS, C-1 실계정 확인은 대기 | — |
| 2026-10-24 | Google 클라이언트 포함(D-21) | `google_client.json`(코드 포함 클라이언트 값, PC 값 우선), 키 파일이 Google 연결 토큰도 옮김, 설치 가이드 §5·§6 | 사용자 요청 | 시험 추가(test_cli_keys) | — |
| 2026-10-24 | 기존 설치본 Google 모드 이전(D-20 보완) | 첫 설정에서 저장된 mock·mock-form을 시작 시 한 번만 자동·실제 폼 ID로 — 아버님 PC가 업데이트 후 Google 연결을 바로 쓰도록 | 연동 확인 중 발견 | 시험 추가(test_form_connect) | — |
| 2026-10-24 | 폼 질문 연결 기본값(D-22) | 실제 폼 질문 ID 14개를 설정 기본값으로, 스톤·각인 합치기·참고 링크 붙이기 보조 항목, 유입 경로 네이버 2종 추가. 실연동 확인: 시험 응답 1건 전 항목 채워짐 | 사용자 요청 | 시험 추가(test_form_connect 2건), TC-S-057 기본값 반영 | — |
| 2026-10-24 | 화면 결함(빈 기록 줄) | 타임라인 목록의 한 칸짜리 줄(“아직 기록이 없어요”·“아직 백업이 없어요”·“기록 없음”·“기록이 없어요”)이 28px 아이콘 칸에 갇혀 한 자씩 줄바꿈 → 한 칸 줄은 전체 폭. TC-S-041에 ‘글자 갇힘’ 검사(모든 화면·6폭)와 설정 Gate·백업 화면 추가 | 사용자 보고 | 회귀 191 PASS | — |
| 2026-10-24 | Form B 계약 동의 연동(D-24) | schema v3(계약 요청·응답·응답 원본·동의 기록·이력·고지 원본·확인된 계약 이메일, 추가만), 사전입력 링크·수집·검증·증거·내보내기, Gate 1·2·3 서버 검증, 잔금 발주 전으로 이동, 주문·고객·설정 화면 | 사용자 지시 | 시험 추가(test_contracts 42건·live 3건), 기존 시험 갱신(수기 동의 → Form B, 발주 전 잔금) | 실연동: 구조·응답 읽기 PASS, 게시 FAIL(사용자 조치) |
| 2026-10-24 | Form B v1.1·환불 정책(D-25) | Q6-1·운영자 검증 필수·문항별 동의 기록·환불 정책 스냅숏, 견적서 개별 제작 고지, 취소·환불 사건(R1~R7)·이벤트·화면 | 사용자 지시 | 시험 추가(test_contracts 내 V1~V12) | — |
| 2026-10-18 | SRS-004 r2 · 코드 | 실제 Google 폼 질문 ID 연결: 설정 > 폼 매핑(미인식 질문 ID + 가린 예시 답변) → `intake_question_binding`(operator, 감사), `load_mapping` 덧붙임(자리표시 ID 유지), 저장 후 전체 재해석(해석 변경분만 revision · 시스템 보류 해제 · 빈 값 채움 · AI 1회). d29e3d1(파일 답 모양 인식) 유지 + 첫 단위 시험 | 아버님 PC 실폼 시험에서 글 답 전부 None → NEEDS_INFO·NO_CONSENT, AI 미실행(인수 조건 C-1 선행 결함). OAuth 범위 유지 | D-18, TC-S-057 신설, TC-U-056~058, INSTALL §5, 14_TR §9, 회귀 184 PASS | SRS-004 1회 |

## 6. 산출물 품질 게이트
| 스테이지 | 7기준 판정 | 전체 | 산출물 경로 | 저작 (티어·모델·추론 강도) | 검토 주체 (여러 값) | 검토 (모델·추론 강도) | 재작성 | 근거 |
|---------|-----------|------|------------|-------------------------|-------------------|--------------------|-------|------|
| 1 PRD해석 | 완전성 PASS / 명확성 PASS / 근거성 PASS / 검증가능성 PASS / 추적성 PASS / 실행가능성 PASS / 비중복성 PASS | PASS | docs/01_PRD_해석서.md (rev.2) | C·claude-opus-5-5·high | separate(맹검 역번역) + user(사용자 지시로 생략 — ASM-001) | claude-opus-5-5·high | r2/3 · 지적 18→0(전부 수용) | docs/review/검토_stage1_역번역_r1.md, 반영 기록 해석서 B-4. r2 반영 확인은 #3 검토자가 대조 |
| 2 TRD | Light RTM 요약 | PASS | RTM#1-2 | C·claude-opus-5-5·high | separate(#3 검토에 포함) | claude-opus-5-5·high | r1 | TRD-001~010 |
| 4 TC-S | 완전성 PASS / 명확성 PASS / 근거성 PASS / 검증가능성 PASS / 추적성 PASS / 실행가능성 PASS / 비중복성 PASS (r3 PM 판정) | PASS | docs/04_TC-S.md (rev.5) | T1·claude-opus-5-5·high | separate(C) | claude-opus-5-5·high | r3/3 · 지적 10→6→0(전부 수용) | docs/review/검토_stage4_r1.md, 검토_stage4_r2.md |
| 7 BD+PDS | 완전성 PASS / 명확성 PASS / 근거성 PASS / 검증가능성 PASS / 추적성 PASS / 실행가능성 PASS / 비중복성 PASS (r3 PM 판정) | PASS | docs/07_BD.md, docs/07b_Product_Design_Specification.md (rev.5) | C·claude-opus-5-5·high | separate | claude-opus-5-5·high | r3/3 · 지적 13→10→0(전부 수용) | docs/review/검토_stage7_r1.md, 검토_stage7_r2.md · 대비 검사 docs/design/contrast_report.txt 0 실패 |
| 3 SRS | 완전성 PASS / 명확성 PASS / 근거성 PASS / 검증가능성 PASS / 추적성 PASS / 실행가능성 PASS / 비중복성 PASS (r3 PM 판정) | PASS | docs/03_SRS.md (rev.3) | C·claude-opus-5-5·high | separate | claude-opus-5-5·high | r3/3 · 지적 11→4→0(전부 수용, OVERRULED 0) | docs/review/검토_stage3_r1.md, 검토_stage3_r2.md. r3는 상한 회차 — 남은 4건 전부 수용 반영 후 PM 마감 |
| 9 DD(핵심) | 7기준 PASS (r3 PM 판정) | PASS | docs/09_DD_core.md (rev.6) | C·claude-opus-5-5·high | separate(C) | claude-opus-5-5·high | r3/3 · 지적 15→8→0(전부 수용) | docs/review/검토_stage9_11_r1.md, r2.md, DD §11·§11-2 |
| 11·12 WBS·AID | 7기준 PASS (r2 PM 판정) | PASS | docs/11_WBS_AID.md (rev.6) | T1·claude-opus-5-5·high | separate(C) | claude-opus-5-5·high | r2/3 · B-15·N-09·N-10 반영 | docs/review/검토_stage9_11_r1.md, r2.md |
| 13 IMP | 증거 규율(시험 PASS) | PASS | jewelry_os/ (WBS-001~012) | T1·claude-opus-5-5·high (PM 직접, fan-out 없음) | self | — | 결함 22 수정 | RTM 2-3, 14_TR §4 |
| 14 TR | 7기준 PASS | PASS | docs/14_TR.md (rev.6) · jewelry_os/reports/ | T1·claude-opus-5-5·high | self(증거 규율) | — | r1 | reports/SUMMARY.txt, *.xml |
| 15 TRP | 7기준 PASS · 인수 = 조건부 | PASS(조건부) | docs/15_TRP.md (rev.6) | T1·claude-opus-5-5·high | user(H3 — 2026-10-17 조건부 인수 승인) | — | r1 | docs/review/H1H3_확인요청_r1.md |
| r2 반영(3·4·7·13·14) | 7기준 PASS | PASS | docs/03_SRS.md·04_TC-S.md·07_BD.md·DECISION_LOG.md, jewelry_os/ | T1·claude-opus-5-5·high | self(증거 규율) + user(H1 r1 답이 기준) | — | r1 | 14_TR §8, reports/SUMMARY.txt |

## 7. 동결 목록
| 항목 ID | 동결 대상 | 동결 시점 | 해제/변경 이력 |
|---------|----------|----------|---------------|
| BD-001 | 상태·라벨·대외 매핑·큐(`domain/states.py`) | rev.5 / 2026-10-16 | rev.6: 아이콘 이름 = 스프라이트 별칭(DEF-004), 라벨·매핑 불변 |
| BD-002 | 전이표·차단 표(`domain/transitions.py`) | rev.5 / 2026-10-16 | rev.6: check() 문구 특례 1건(DEF-009) · rev.7: accept 상한 초과 조건 = 예외 승인 + 제작 가능 확인(D-2). 전이표·차단 표 불변 |
| BD-003 | 문구 카탈로그(`domain/guards.py` = PDS §5.4) | rev.5 / 2026-10-16 | — |
| BD-006 | HTTP 응답 규칙·액션 코드 | rev.5 / 2026-10-16 | — |
| BD-008 | 권한 표 | rev.5 / 2026-10-16 | rev.7: 4행 추가(상한 초과 예외 승인, 변경 주문 판정·견적 / 동의 / 결제) — D-2·D-10 |
| DD-001 | DB 스키마(`core/schema.sql`) | rev.5 / 2026-10-16 | rev.7: v2 — `capacity_exceptions`·`change_orders` 추가, payment_requests 단계 CHANGE·change_order_id(자동 이행, D-17) — 사용자 결정 D-2·D-10 |
| DD-002 | 전이 서비스 시그니처·차단 표(DD r3) | rev.6 / 2026-10-17 | — |
