# 검토 — 스테이지 7 기본설계(BD) + PDS 재검토 r2

- 대상: `docs/07_BD.md` r2(§11 반영 기록), `docs/07b_Product_Design_Specification.md`(S-00a, §2.1-1, S-03 공통 보조 조치·이슈·취소 대화상자, S-03b 변경 요청, S-03c 잔금·환불, 동의 없음, E-15, S-09 데이터 위치), `docs/04_TC-S.md` r2, `docs/03_SRS.md`(SRS-143 → BD-008)
- 기준: 검토 r1 `docs/review/검토_stage7_r1.md`의 B-01~B-13
- 검토자: separate reviewer · 모델 `claude-opus-5-5`
- 범위: (1) r1 차단 13건의 해소 여부 (2) **이번 수정이 새로 만든** 모순·누락만 차단으로 본다(처음부터 다시 탐색하지 않음). BD↔PDS↔TC-S 사이 불일치를 중점으로 봤다.
- 방법: SRS↔FUN 배정을 스크립트로 다시 대조했고(구현·구조 147행, 미배정 0), BD r2 전문, PDS 수정 절, TC-S r2 전문을 BD-006·BD-008·§6과 하나씩 맞대어 봤다.

## 판정: **반려 — 미해소(부분) 3건 + 신규 차단 7건**

---

## 1. r1 차단 해소 여부

| r1 ID | 판정 | 근거 |
|---|---|---|
| B-01 FUN 미배정 22건 | **해소** | 스크립트 대조 결과 구현·구조 147행이 모두 FUN에 배정됐고, FUN에 들어간 운영 행은 0이다. §2 주석에 159가 들어갔다. |
| B-02 Google 실제 어댑터 | **해소** | BD-005에 `GoogleForms`·`GoogleDrive`·`GoogleOAuth`(루프백·refresh·읽기 전용 범위)가 생겼고, PDS S-00a를 “실제 Google 계정 — 시험 폼”으로 고쳤다. 다만 새 인터페이스가 TC-S-052와 어긋난다 → R2-05. |
| B-03 데이터 폴더·ACL | **해소** | BD §1 배치, PDS S-09 데이터 위치, TC-S-049(`icacls` 판정)가 서로 맞는다. |
| B-04 권한 표 | **해소** | BD-008을 새로 만들었고, SRS-143 참조와 PDS §2.1-1 위임, TC-S-026이 서로 맞는다. 빠진 동작 하나는 R2-06. |
| B-05 HTTP 계약 | **부분 미해소** | 라우트 목록, 응답 규칙 표, JS 없는 409 처리는 해소됐다. **남은 것은 두 가지다.** (d) 전이 action 코드 목록이 아직 없다. BD-006은 “전이 액션(BD-002 이름)”이라고만 하는데 BD-002는 `ingest_brief`·`to_review`·`revalidate_design`만 이름을 적고, TC-S-002는 BD 어디에도 없는 `accept`·`approve_quote`·`record_acceptance`·`send_factory`·`record_design_ok`·`ship`을 직접 호출한다. (e) 증빙 누락의 상태 코드가 서로 다르다. TC-S-002⑦은 409 G-ACCEPT-EVIDENCE, TC-S-029는 **422 G-PAYMENT-EVIDENCE**, TC-S-017은 동의 증빙 누락 422다. BD-006은 카탈로그 Unmet이면 409, 필드 오류면 422(`{errors:{field:msg}}`, 코드 없음)로 정했다. 그래서 같은 카탈로그 코드가 422로 나가는 규칙이 없다. → 수정: BD-002에 action 카탈로그 표를 둔다(action · from→to · actors · PDS §5.3 버튼 라벨 · 주요 guard 코드). BD-006에는 “전이 액션의 증빙·확인·사유 누락 = 409 Unmet / 비전이 기록 폼(결제 입력·수기 접수)의 필수 누락 = 422 + 해당 카탈로그 코드를 필드 오류로” 같은 분류 규칙을 명시하고, TC-S-002·017·029를 그 규칙에 맞춘다. |
| B-06 결제 요청 상태·잔금 | **해소** | BD-001에 `NOT_APPLICABLE`(가상 행)을 넣었고, BD §6.4에 잔금 생성 시점(QC_PASSED, operator, 비전이)과 `verify_final_full`을 정했다. PDS S-03c와 TC-S-002·028이 이와 맞는다. 결제 비교 방식의 잔여 불일치는 R2-04. |
| B-07 변경 요청 흐름 | **해소(흐름 정의됨)** | BD §6.5 1~5단계, PDS S-03b 3단계 표시, TC-I-012, TC-S-011로 흐름이 정해졌다. 다만 새 흐름에 결함 2건이 생겼다 → R2-02·R2-03. |
| B-08 주문 취소 | **부분 미해소** | 대화상자 필드(사유·증빙·귀책·환불 결정, 몰수 없음)와 TC-S-010은 해소됐다. **남은 것:** 취소할 때 ① **미결제 결제 요청**(DRAFT·LINK_READY)을 어떻게 하는지 BD §6.7·PDS 어디에도 없다. 그대로 두면 SRS-157 “받을 돈”에 끝난 주문의 금액이 계속 잡힌다. ② CANCELLED 주문에 **남은 OPEN 이슈**(CHANGE_REQUEST·DELIVERY_DELAY·DISPUTED 등)를 어떻게 하는지 없다. 남으면 EXP-002 ‘지연·이슈’ 큐와 대시보드 위험 묶음에 계속 나온다. → 수정: §6.7 `cancel` 효과에 “미결제 요청 → CANCELLED, 열린 이슈 → RESOLVED(해결 근거 = 주문 취소, 감사 기록)”를 같은 트랜잭션으로 넣는다. |
| B-09 이슈 상태·해결 | **부분 미해소** | 상태값(BD-001), 유형별 여는 경로·해결 조건(BD §6.3), 열기·해결 대화상자(PDS S-03)는 해소됐다. **남은 것:** 해결 조건이 안 됐을 때 쓸 **차단 문구 코드가 PDS §5.4에 없다.** TC-S-053은 “DELIVERY_DELAY 예상일만 → 해결 409”를 기대하는데, 그 409의 코드·문구를 정한 곳이 없다. 그래서 worker가 문구를 만들어야 하고, 이는 “문구 원본 = PDS”(BD 머리말·TC-I-006)를 어기게 된다. 결제 불일치 해결 방법·환불 검토 결과 등 Radio 미선택도 마찬가지다. → 수정: §5.4에 `G-DELAY-DATE`·`G-DELAY-NOTICE`(“고객에게 지연 안내를 보낸 기록이 없어요.”)·`G-RESOLUTION`(“해결 방법을 골라야 해요.”) 같은 코드를 추가한다. |
| B-10 동의 없음 | **해소(설계) — 상류 정합은 신규 차단** | 단계, 큐, 다음 조치, AI 미실행이 BD §6.2·PDS S-03·TC-I-011에서 일관된다. 다만 해소 방식이 상류 문장과 BD-002를 어긋나게 만들었다 → R2-01. |
| B-11 system_revalidate | **해소** | `revalidate_design`의 주체를 업로드한 maker로 바꿨고, BD-002·§6.6·TC-I-005·TC-S-030이 서로 맞는다. 잔여 사항은 비차단 N-r2-04. |
| B-12 동시 편집 | **해소(계약)** | `orders.version`, 숨김 필드, 409 G-CONFLICT, TC-I-013이 생겼다. 문구 원본 위치 문제는 R2-05에 함께 적었다. |
| B-13 PDS↔TC-S 판정 | **해소** | TC-S-006은 `aria-disabled`로, TC-S-005·009의 기대 문장은 카탈로그 문구로 바뀌었고, TC-S-010의 “PDS §5.2” 참조는 제거됐다. |

---

## 2. 신규 차단 (이번 수정이 만든 모순·누락)

| ID | 위치 | 내용 | 수정 제안 |
|---|---|---|---|
| **R2-01** | BD §6.2·BD-001 QUEUES ↔ BD-002 “차단 표 = SRS §3-1을 BLOCKS로 그대로” ↔ SRS §3-1 NO_CONSENT 행·해석서 EXP-002 | **B-10을 해소하면서 상류를 RTM 변경 없이 바꿨고, BD 안에서도 서로 충돌한다.** ① SRS §3-1을 보면 HOLD 행은 “모든 진행 전이(**시스템 전이 제외**)”인데, NO_CONSENT 행은 “모든 진행 전이 + AI 분석”이라 예외가 없다. 그런데 BD §6.2는 동의 없는 주문에서 시스템 전이 `ingest_brief`·`to_review`를 수행한다. BD-002가 BLOCKS를 §3-1 “그대로” 옮기면 이 시스템 전이는 막혀서 §6.2가 동작하지 않는다. ② EXP-002의 ‘검토 필요’ 조건은 “보류 사유 없음”인데, BD-001은 “`동의 없음` 외의 보류가 없음”으로 바꿨다. TC-S-003①은 “DB에서 큐 조건으로 센 값”을 기대하므로 어느 조건이 원본인지 갈린다. (r1에서 이 방향을 제안했지만, 상류를 고치는 등록 없이 BD만 바뀌었다.) | RTM 변경으로 SRS §3-1 NO_CONSENT 행에 “(시스템 전이 `ingest_brief`·`to_review`는 AI 없이 허용)”을, EXP-002 ‘검토 필요’ 조건에 “`동의 없음` 보류는 포함”을 보정 등록한다(ASM 연결). BD-002에는 “BLOCKS의 NO_CONSENT 행은 system 전이 2개에 적용하지 않음”을 명시한다. |
| **R2-02** | BD §6.5-4 ↔ BD §6.4·BD-006 `create_payment_req`(전이) ↔ TC-S-011⑥·TC-I-012 | **변경 요청 뒤 결제 요청을 다시 만들 경로가 없다.** §6.5-4는 “PAYMENT_PENDING에서 기존 미결제 예약금 요청을 CANCELLED로 바꾸고 operator가 새 버전 기준 요청을 다시 만든다”고 한다. 그런데 결제 요청을 만드는 유일한 동작 `create_payment_req`는 CUSTOMER_ACCEPTED→PAYMENT_PENDING **전이**다. 이미 PAYMENT_PENDING인 주문에서는 G-WRONG-STATE가 되어 TC-S-011⑥ “새 결제 요청 = v2 기준 금액”과 TC-I-012 “새 요청 생성 가능”을 만족할 수 없다. | 비전이 액션 `recreate_payment_req`(operator, PAYMENT_PENDING, 해당 phase의 활성 요청이 없을 때만)를 BD-002·BD-006·BD-008에 추가한다. 또는 `approve_requote`/`record_reacceptance`의 효과로 새 요청을 자동 생성하도록 정한다. PDS S-03c에 그 버튼과 조건을 적는다. |
| **R2-03** | BD §6.5-2 “`approve_requote` … 단계 CUSTOMER_ACCEPTED~FACTORY_SENT 이전” ↔ BD-008 “CHANGE_REQUEST 해결 = 재동의 기록 시 자동”뿐 ↔ PDS §5.3·S-03 “변경 요청 기록: 활성 단계 전부” ↔ PDS S-03f·SRS-163 “고객 요청 변경 → 재견적 경로” | **CHANGE_REQUEST가 해결될 수 없는 교착이 생긴다.** 변경 요청 이슈는 모든 활성 단계에서 열 수 있다. QC 불통과에서 귀책 “고객 요청 변경”(FACTORY_RECEIVED)도 SRS-163에 따라 재견적 경로로 간다. 그런데 재견적 승인은 FACTORY_SENT 이전 단계에서만 허용되고, 이슈를 수동으로 해결하는 경로는 없다. 그 결과 FACTORY_SENT 이후에 열린 CHANGE_REQUEST는 영원히 OPEN이다. 결제 확인(TC-S-011 “재견적 없이 결제 확인 불가”) → 잔금 확인과, G-DELIVERY(열린 문제 0) → 주문 완료가 영구히 막힌다. | 방법은 두 가지다. (a) FACTORY_SENT 이후 단계에서는 이슈 열기 대화상자의 CHANGE_REQUEST를 비활성으로 하고, REWORK 귀책 “고객 요청 변경”의 처리를 별도로 정한다(예: 재견적 허용 단계에 FACTORY_RECEIVED·QC 포함). (b) 단계 제한을 없애고 그 단계들의 결제·디자인 영향을 §6.5에 적는다. 어느 쪽이든 operator가 수동으로 해결하는 경로(사유 필수, 예: “고객 철회”)를 BD-008·§6.3에 추가하고, 막히는 경우의 문구를 §5.4에 넣는다. |
| **R2-04** | BD §6.4 `record_payment` “해당 phase의 **PAID 합 − REFUND 합** = 기대 → 검증 전이” ↔ PDS S-03c “**금액 = 기대 금액** & 상태 = 결제완료 → 전이 / 금액 ≠ 기대 → PAYMENT_MISMATCH” ↔ TC-S-009 ⑤(350,000 + 차액 50,000 = 400,000 → 검증) | **결제 금액을 무엇과 비교하는지 BD와 PDS가 다르다.** BD와 TC-S는 누적 합을 비교하고, PDS는 이번 입력 한 건의 금액을 비교한다. PDS대로 구현하면 TC-S-009의 차액 50,000 입력이 또 하나의 불일치가 되어 시험이 실패한다. 전이를 대화상자에서 확인한 뒤 일으키는지(PDS)와 저장 트랜잭션에서 자동으로 일으키는지(BD)도 다르다. | PDS S-03c를 BD §6.4 기준으로 고친다(“이 phase의 결제 합계가 기대 금액과 같아지면 결제 확인 — 대화상자에 누적 합·이번 입력 표시”). 결제 확인 ApprovalDialog를 저장 전 확인 단계로 둔다고 BD-006에 명시한다. |
| **R2-05** | BD-005 `FormsSource.list_responses(form_id, page_token)`(URL `?pageToken=`만) ↔ TC-S-052 “기본 모드 `filter` 없음, **증분 모드 `filter=timestamp >= {커서−1일}`**” · BD-003 추가 코드 ↔ PDS §5.4·E-15 | **새로 만든 인터페이스·문구가 짝 문서와 어긋난다(2건).** ① 실제 어댑터의 시그니처와 요청 URL에 증분 필터 인자가 없다. 그래서 TC-S-052의 증분 모드 판정(경로·쿼리)을 BD 계약으로는 구현할 수 없다. 언제 기본 모드(전체)이고 언제 증분 모드인지도 BD에 없다. ② BD 머리말은 “라벨·문구는 PDS가 원본”이라고 하면서 BD-003이 `G-WRONG-STATE`(문구 없음)·`G-CONFLICT`(“다른 사람이 먼저 바꿨어요. 새 내용을 확인한 뒤 다시 시도하세요.”)를 직접 정의한다. 두 코드는 PDS §5.4에 없고, G-CONFLICT 문구는 PDS E-15 문구(“…{누가} {시각}에 이 주문을 바꿨어요. 내 입력은 아래에 남겨 둘게요.”)와 다르다. TC-S-002는 409 G-WRONG-STATE를 기대하는데 그 본문 문구가 정해지지 않았다. | ① `list_responses(form_id, page_token, since: datetime|None)`로 바꾸고 `since`가 있으면 `filter=timestamp >= since−1일`을 붙인다. 모드 결정 규칙(첫 동기화·재연결 직후 = 전체, 그 외 = 커서 기준 증분)을 BD-005에 적는다. ② 두 코드와 문구를 PDS §5.4로 옮기고(G-WRONG-STATE 예: “지금 단계에서는 할 수 없는 동작이에요.”) BD-003은 참조만 하게 한다. G-CONFLICT와 E-15 문구는 하나로 맞춘다. |
| **R2-06** | TC-S-022 “**operator** 수동 업로드 후 그 주문 `reference` +1”(SRS-010) ↔ BD-006 동작 표·BD-008 권한 표 ↔ PDS S-03a | **TC-S r2가 요구하는 동작에 라우트·권한·화면이 없다.** BD-006의 업로드 라우트는 `/concepts`(시안)와 `/designs`(디자인)뿐이고, BD-008은 “시안·디자인 업로드 = maker 전용”이며, 고객 참고 이미지(`reference`) 수동 업로드 행이 없다. PDS S-03a 참고 이미지 섹션에도 업로드 버튼이 없다(r1 N-07이 시험 요구로 격상됨). TC-S-026은 “BD-008 표 전부 × 두 역할”이라 이 동작의 허용 여부를 판정할 근거가 없다. | BD-006에 `POST /orders/{no}/references`(`upload_reference`)를 추가하고, BD-008에 maker·operator 행(또는 operator)을 넣는다. PDS S-03a에 “이미지 추가(수동 — 고객이 카카오로 보낸 사진)” 버튼과 출처 표기(“수동 추가 · 김OO”)를 넣는다. |
| **R2-07** | BD §6.3 CANCEL_REQUEST “결과(철회됨-계속 진행 / 취소 처리)” ↔ BD-008 “이슈 해결: … CANCEL_REQUEST = operator” ↔ PDS S-03 이슈 열기 “CANCEL_REQUEST: maker·operator” | **이슈 해결 결과 “취소 처리”의 연결이 BD와 PDS에서 다르다.** PDS 해결 대화상자는 REFUND_REVIEW “취소로 전환” → 취소 대화상자 열림만 정하고, CANCEL_REQUEST “취소 처리”가 같은 취소 대화상자로 이어지는지, 이슈만 닫고 취소는 따로 하는지 없다. BD §6.3도 REFUND_REVIEW에만 “→ 주문 취소 화면으로”를 적었다. 순서에 따라 B-08 잔여(취소 시 열린 이슈 처리)와 겹쳐, 이슈는 RESOLVED인데 주문은 활성이거나 그 반대가 생긴다. | §6.3 CANCEL_REQUEST 행에 REFUND_REVIEW와 같은 규칙(“취소 처리 → 취소 대화상자, 취소가 커밋될 때 이슈 RESOLVED — 같은 트랜잭션”)을 넣고, PDS 해결 대화상자 문장도 같게 한다. |

---

## 3. 비차단 (이월 가능)

| ID | 위치 | 내용 | 제안 |
|---|---|---|---|
| N-r2-01 | PDS §5.3 QC_PASSED 행 | 대표 버튼이 “잔금 확인 입력”인데, r2 흐름(BD §6.4·S-03c·TC-S-002)에서는 먼저 “잔금 결제 요청 만들기”를 해야 한다. | 잔금 요청이 없으면 대표 = “잔금 결제 요청 만들기”, 있으면 “잔금 확인 입력”으로 조건부 행을 둔다. |
| N-r2-02 | TC-S-003 `queue=concept` ↔ PDS S-01(`review`·`needs_info`·`payment`·`making`) | 큐 URL 코드가 시험에만 나오고 BD/PDS에 표가 없다(r1 N-04 잔여). | BD-001 `QUEUES`에 10개 코드를 표로 둔다. |
| N-r2-03 | TC-S-014⑨ ↔ PDS S-03a 수락 대화상자 | W-GATE-CAP을 시험은 요구하는데 화면 구성 목록에는 아직 없다(r1 N-06 잔여). | 수락 ApprovalDialog 구성에 W-GATE-CAP을 추가한다. |
| N-r2-04 | BD §6.6 | “새 디자인 업로드 → 유효 패킷 VOID”가 단계와 무관하게 적혀 있어서, FACTORY_SENT 이후에 올리면 이미 보낸 패킷이 VOID가 될 수 있다(SRS-073 해시 고정 기록). | FACTORY_SENT 이후 업로드는 패킷을 건드리지 않거나(REWORK 경로), 업로드를 막는다고 명시한다. |
| N-r2-05 | BD-001 `NOT_APPLICABLE` 가상 행 | PRD §8.1은 잔금을 NOT_APPLICABLE로 “**기록**”하라고 한다. DB 행이 없으면 `verify_final_full` 감사 이벤트가 유일한 기록이 된다. | 감사 payload에 `balance: NOT_APPLICABLE`을 남긴다고 명시한다. |
| N-r2-06 | PDS S-00a “백업 키 1회 표시” ↔ BD | 백업 암호화 키를 만들고 저장하는 위치(secrets.env?)와 `restore`가 키를 받는 방법이 BD에 없다. | BD §1·PBS-006에 키 위치와 `jos restore --key`를 적는다. |
| N-r2-07 | BD-006 `/system/*` 역할 칸 | 경로 5개에 역할 3개가 “/”로 나열돼 있어서 짝이 모호하다. | 행을 경로별로 나눈다. |
| N-r2-08 | SRS-152 “PDS §6” | 접근성 절은 §9다(r1 N-09 잔여). | RTM에서 참조를 정정한다. |

---

## 4. 요약
- 해소 10건: B-01·02·03·04·06·07(흐름)·10(설계)·11·12·13. 이 중 B-02·07·10은 해소 방식에서 신규 결함이 나왔다(R2-01·02·03·05).
- 부분 미해소 3건: **B-05**(action 카탈로그, 증빙 누락 상태 코드), **B-08**(취소 시 미결제 요청·열린 이슈), **B-09**(해결 조건 차단 문구 코드 없음).
- 신규 차단 7건: **R2-01**(동의 없음 방식이 SRS §3-1·EXP-002·BD-002 BLOCKS와 모순) · **R2-02**(PAYMENT_PENDING에서 결제 요청 재생성 경로 없음) · **R2-03**(FACTORY_SENT 이후 CHANGE_REQUEST 교착) · **R2-04**(결제 비교: 누적 합 vs 단건) · **R2-05**(FormsSource 증분 filter 없음, BD-003 문구 원본 이탈) · **R2-06**(참고 이미지 수동 업로드 라우트·권한·화면 없음) · **R2-07**(CANCEL_REQUEST “취소 처리” 연결 불일치).
