# 검토 — 스테이지 9 DD(핵심 모듈) r2 + 스테이지 11·12 WBS·AID 재검토

- 대상: `docs/09_DD_core.md` r2(§11 반영 기록), `docs/11_WBS_AID.md` r2
- 기준: 검토 r1 `docs/review/검토_stage9_11_r1.md`의 B-01~B-15. 상류는 BD r3·SRS(§3-1)·TC-S r3이고, 문구·화면 원본 대조용으로 PDS §5.4·§6.1을 함께 봤다.
- 코드 대조(동결 계약 확인용, 판정 근거 아님): `core/schema.sql`(quotes·quote_items 트리거, `*_src`), `domain/transitions.py`(PROGRESS·BLOCKS·CONFIRM_ACTIONS·ALIASES·block_unmet·NOT_REVERTIBLE), `services/payments.py`(record_payment·_verify_in_tx·try_verify), `services/orders.py`(release_hold·resolve_issue·cancel_order), `services/analysis.py`(가림·정규화), `core/security.py`(session_state), `domain/states.py`(PAY_STATUS_LABEL)
- 검토자: separate reviewer · 모델 `claude-opus-5-5`
- 범위: (1) r1 차단 15건 해소 여부 (2) **이번 수정이 새로 만든** 모순·누락·판정 결함만 차단으로 본다(처음부터 다시 탐색하지 않음).

## 판정: **반려 — 미해소(부분) 2건 + 신규 차단 6건**

---

## 1. r1 차단 해소 여부

| r1 ID | 판정 | 근거 |
|---|---|---|
| B-01 견적 불변 | **해소** | DD-001에 “APPROVED→SUPERSEDED로 status 한 컬럼만 바뀌는 UPDATE만 허용”이 들어갔다. 트리거는 `quotes`의 status 외 24개 컬럼 전부를 `IS`로 비교한다(스키마 컬럼과 하나씩 대조함). `quote_items_immutable_ins`(INSERT) 트리거도 추가됐다. 되돌리기의 `clone_as_draft`는 DRAFT 부모에 품목을 넣으므로 이 트리거와 충돌하지 않는다. |
| B-02 `*_src` | **해소** | 원문 보존 범위를 정했다(Google = RFC3339 Z 원문 그대로, 사람의 KST 입력 = UTC + `*_src`). 스키마 3컬럼과 `clock.from_kst_input` 사용처(payments·cs·quotes·manufacturing)가 DD와 일치한다. |
| B-03 진행 액션 집합 | **부분 미해소** | ①(`receive` 포함)과 ③(§3-1 열 → action 대응표)은 해소됐다. 남은 것은 ②다. DD 대응표는 “결제 요청 만들기 = `create_payment_req`·`create_balance_req`·`recreate_payment_req`”라고 정했는데, DD 차단 표의 **CHANGE_REQUEST 행에는 `recreate_payment_req`가 없다.** 코드 BLOCKS도 같고, 대응을 담으려던 `ALIASES`는 정의만 있고 `block_unmet`에서 쓰이지 않는다. 그래서 PAYMENT_PENDING에서 `approve_requote`가 v1 요청을 취소한 뒤 **고객 재동의 전에도** operator가 v2 기준 결제 요청(고객에게 갈 링크)을 만들 수 있다. 이는 §3-1 CHANGE_REQUEST “결제 요청 만들기” 차단 및 DD 자신의 대응표와 모순된다. → CHANGE_REQUEST 행에 `recreate_payment_req`를 추가한다(또는 `ALIASES`를 `block_unmet`에 적용하고, 행별 예외는 표에 명시한다). TC-U-020~030 기대값도 이에 맞춘다. |
| B-04 결제 확인 트랜잭션 | **해소(트랜잭션) — 신규 결함 2건** | 이벤트 INSERT → 누적 합 → `verify_*` `perform`이 한 `db.tx()` 안에서 일어나고(중첩 합류), TC-U-035가 TC-I-003을 받친다. 다만 새로 넣은 “차단 후 재시도”와 `PAID` 상태에서 결함이 생겼다 → R-01·R-02. |
| B-05 되돌리기 | **해소** | 불가 목록에 `create_payment_req`가 들어갔고(DD·코드 NOT_REVERTIBLE 일치), accept·approve_quote·start_cad·receive 되돌림의 부수효과를 정했다. TC-U-036이 이를 판정한다. |
| B-06 승인 조건 | **해소(①·③) — 신규 결함 1건** | ① G-QUOTE-FIELDS에 “적용 정책 버전(승인된 수정·환불 정책)”이 들어갔고 코드도 검사한다. ③ cancel에 G-REASON·G-FAULT·G-ISSUE-RESOLVE 코드가 생겼다. ② 확인 열은 생겼지만 그 집합이 PDS §6.1과 다르다 → R-06. |
| B-07 DD-002 동결 본문 | **해소** | 시그니처가 BD-002와 같아졌다. verify_final은 G-BALANCE, 판정은 PAID−REFUND 누적, 보류 코드는 `G-HOLD-{reason}`(코드 `block_unmet`가 같은 형식으로 만듦), 퍼널은 7종으로 맞췄다. `transition_ctx` 삭제 시점도 코드 순서(UPDATE 직후)로 고쳐 r1 N-01까지 해소됐다. |
| B-08 동기화 분기·TC-U | **해소** | 신규 분기에 WAITLISTED·NO_CONSENT·NEEDS_INFO가 들어갔다. AI는 `process_new`에서 NO_CONSENT면 실행하지 않고 시스템 전이만 한다(BD §6.2와 일치). 개정 분기에서도 AI를 실행하지 않는다. TC-U-040~055는 정량이다. |
| B-09 1/3 배분 | **해소** | 값 표 7개 + FULL + 음수·파싱이 정해졌다. `(total+2)//3` 계산값을 모두 검산했다(9,999,999,999 → 3,333,333,333/6,666,666,666 포함). |
| B-10 날조 검사 | **해소(판정 대칭·정규화 정의) — 신규 결함 1건** | 유지 케이스 4개가 들어갔고, 금액(만·천·쉼표)과 날짜((월, 일), 연도 제외) 정규화가 정해졌다. 다만 새 금액 규칙 “교집합이 있으면 유지”가 SRS-022와 어긋난다 → R-04. |
| B-11 연락처 가림 | **해소** | 휴대전화(하이픈·점·공백·붙여쓰기·+82), 유선, 이메일, 주소 3패턴이 정해졌다. TC-U 가림 표는 형식별 입력과 “원문 숫자열 0”으로 정량이다. 표의 주소 2건이 코드 정규식에 맞는지 손으로 대조했다. 잔여(보정 미등록)는 N-r2-03. |
| B-12 잠금 HTTP | **해소** | 화면 200 + 잠금 템플릿, JSON·파일 401이 BD-006·`web/app.py`와 일치한다. TC-U 경로는 `/api/notifications`로 바뀌었다. |
| B-13 서버 잠금 시점 | **해소(TC-S-007⑨ 정합) — 신규 결함 1건** | `≥ lock_minutes`와 TC-U 14:59/15:00/15:01이 맞고, TC-S-007⑨를 만족한다. 하지만 1분 여유를 없애면서 60초 간격 핑과 충돌한다 → R-05. (r1이 제안한 수정 방향의 부작용이라 검토자 쪽 누락도 있었다.) |
| B-14 휴지 시간 | **해소** | `[시작, 끝)`과 “다음에 오는 끝”으로 규칙을 정했고, 자정을 넘는 구간과 경계(00:00·07:00·06:00·22:00)의 값 표를 넣었다. |
| B-15 WBS 커버리지 | **부분 미해소** | ① TC-S-032·039·046·050 배정과 settings·audit의 WBS-001 편입은 해소됐다. ② `adapters/manual.py`·`services/scheduler.py`는 배정하지 않고 **WBS 쪽에서 “별도 파일 없음”·“`system.py`가 scheduler 역할”이라고 다시 정의했다.** 그런데 BD는 PBS-003·PBS-004와 §5 BD-005(🔒 동결 대상 절)에서 `PaymentLedger`/`ManualNaver`·`MessageChannel`/`ManualKakao` 어댑터 인터페이스를 R1 구현으로 정하고 있다. 이 어댑터는 R2에서 네이버·카카오 실제 연동을 끼울 자리다. WBS가 BD 변경 등록 없이 상류 구성요소와 인터페이스를 없앤 것이다. → BD PBS-003·004·BD-005를 RTM 변경으로 고치거나(인터페이스는 `services/payments`·`cs` 안의 Protocol로 둔다 등), WBS-007·009 산출물에 `adapters/manual.py`를 둔다. |

---

## 2. 신규 차단 (이번 수정이 만든 모순·누락·판정 결함)

| ID | 유형 | 위치 | 내용 | 수정 제안 |
|---|---|---|---|---|
| **R-01** | (1)(3) | DD-002 “차단을 푸는 동작(보류 해제·이슈 해결)이 끝난 뒤 `try_verify`가 한 번 다시 시도” · TC-U-034 “HOLD 보류 중 완납 입력 → 보류 해제 → 자동 확인 1회” ↔ BD-008(보류·해제 = **maker**, 결제 확인 = **operator**) ↔ DD-002 verify_initial actors `{operator}` | **재시도하는 주체를 정하지 않아, 보류를 해제했을 때의 자동 확인이 권한 표상 불가능하다.** 코드는 해제한 사람을 그대로 actor로 넘긴다(`release_hold` → `try_verify(ctx, id, actor)`). 보류(HOLD·NEEDS_INFO·WAITLISTED) 해제는 maker만 할 수 있으므로 `check`가 G-ROLE-OPERATOR를 돌려주고, 재시도는 아무 말 없이 끝난다. 그래서 TC-U-034는 BD-008을 지키는 한 통과할 수 없다. B-04에서 고치려던 “완납했는데 PAYMENT_PENDING에 머무는” 상태가 보류 경로에 그대로 남는다. 이슈 해결 경로도 해결한 사람이 maker이면 같은 문제가 생긴다. | 재시도의 주체와 감사 표기를 DD-002에 정한다. 예: “자동 확인의 actor = 마지막 PAID 이벤트를 기록한 operator(감사 detail에 `trigger: hold_released by <maker>`)”, 또는 “차단이 풀리면 결제 탭 대표 버튼이 ‘결제 확인’(operator, 확인 체크)으로 바뀐다”. TC-U-034는 maker 해제를 기준으로 다시 쓴다. |
| **R-02** | (1) 동결 | DD-002 “= 기대면 요청 **`PAID`(확인 대기)**” ↔ BD-001 🔒 결제 요청 상태 `DRAFT·LINK_READY·PAID_VERIFIED·MISMATCH·CANCELLED·NOT_APPLICABLE` ↔ PDS §5 결제 요청 상태 라벨 표(“DRAFT · LINK_READY · PAID_VERIFIED · …”) | **동결 BD-001에 없는 결제 요청 상태를 새로 만들었다.** PDS 라벨 표와 코드 `PAY_STATUS_LABEL`에 `PAID`가 없어서, 결제 탭·받을 돈 화면에 영문 코드가 나오거나 라벨 조회가 실패한다(SRS-130 “1차 문구에 영문 코드 0”, TC-S-043 “라벨 누락 0”). BD §6.5-4 재견적은 “미결제(DRAFT·LINK_READY) 요청 취소”만 정해서 `PAID` 대기 요청은 어떻게 하는지도 비어 있다. (r1의 수정 제안에 예시로 적은 이름이지만, 상류 등록까지 포함해 제안했어야 했다.) | RTM 변경으로 BD-001에 `PAID`(“입금 확인 대기” — 금액은 맞고 보류·이슈로 확인 전)를 추가하고, PDS §5 상태 라벨 표와 `PAY_STATUS_LABEL`에 라벨을, BD §6.5·6.7에 `PAID` 요청 처리(재견적 시 유지·환불 경로, 취소 시 상태)를 넣는다. 아니면 상태를 늘리지 말고 “확인 대기” 여부를 (누적 합 = 기대 ∧ 단계 미전이)로 계산해 표시한다. |
| **R-03** | (1) | DD-002 차단 표 REFUND_REVIEW 행 “…, ship, **create_balance_req**” ↔ SRS §3-1 REFUND_REVIEW “상세 CAD 시작, 발주, 검수 통과, 잔금 확인, 발송” · SRS-115 “표에 없는 전이는 막지 않음” ↔ TC-S-053 “그 외 미차단” | **대응표를 만들면서 §3-1에 없는 차단을 추가했다.** DD 대응표상 `create_balance_req` = “결제 요청 만들기”인데, §3-1 REFUND_REVIEW 행에는 “결제 요청 만들기”가 없다. 코드 BLOCKS도 같다. 그래서 TC-S-053(차단 표 전수, 표에 없는 액션은 미차단)과 TC-U-020~030 기대값이 서로 다르게 정해진다. 취지(환불 검토 중 잔금 요청 보류)는 타당할 수 있으나 상류 변경 등록이 없다. | 행에서 `create_balance_req`를 빼거나, RTM 변경으로 SRS §3-1 REFUND_REVIEW 행에 “결제 요청 만들기(잔금)”를 추가하고 TC-S-053에 반영한다. |
| **R-04** | (1)(3) | DD-006 검증기 “budget = 원 단위 정수 집합이 원문 금액 집합과 **교집합이 있어야 유지**” ↔ SRS-022 “고객 원문에 없는 금액 … 숫자가 브리프에 있으면 그 필드를 null” | **금액 규칙이 ‘하나라도 맞으면 유지’여서, 일부를 지어낸 값이 통과한다.** 원문 “예산 50만원”, 출력 budget “50만~80만원”은 집합 {500000, 800000, …} ∩ {500000, …} ≠ ∅이므로 유지되어, 원문에 없는 80만원이 브리프에 남는다. 같은 DD 안에서 deadline은 ⊆(포함), purity는 전부 일치를 쓰는데 budget만 다르다. TC-U에는 범위 출력 케이스가 없어 이 결함을 판정할 수 없다. 코드 `amounts()`는 정규화 값과 함께 모든 숫자 토큰(“50만”의 50 등)도 집합에 넣어서 교집합이 더 쉽게 생긴다. | budget을 “출력의 금액 집합 ⊆ 원문 금액 집합”으로 바꾼다. 범위 표기(“50~100만원”: 뒤 단위를 앞 숫자에도 적용) 정규화를 정한다. TC-U에 “50만원 → ‘50만~80만원’ → null”, “50~100만원 → 1,000,000 유지”를 추가한다. |
| **R-05** | (1) | DD-007 “서버 잠금 = `now − last_seen_at ≥ lock_minutes`” + “활동 = … 입력 활동 핑(입력 있을 때 **≤60초 간격**)” ↔ EXP-006 “**15분 비활성** 시 잠금 … 잠금 60초 전 경고와 ‘계속 사용’ 버튼” ↔ TC-S-007① | **두 규칙이 함께 적용되면 경고 중에 ‘계속 사용’을 눌러도 이미 서버가 잠근 상태일 수 있다.** 핑은 입력이 이어지는 동안 60초에 한 번만 나가므로 `last_seen_at`은 실제 마지막 입력보다 최대 60초 앞선다(L ∈ [0, 60s)). 서버는 `마지막 입력 − L + 15:00`에 잠그고, JS 경고는 `마지막 입력 + 14:00`에 뜬다. L이 60초에 가까우면 경고가 뜨는 순간 서버는 이미 잠긴 상태다. 그러면 “계속 사용” 핑이 401 `{locked:true}`를 받아 비밀번호 화면이 나온다. 또 실제 비활성이 15분이 되기 전(최대 1분 일찍)에 요청이 401이 되어 EXP-006 “15분 비활성” 정의와도 다르다. r1의 1분 여유는 이 오차를 흡수하던 장치였다. | 핑을 trailing-edge(입력이 멈춘 뒤 1~2초 안에 한 번 더 보냄)로 정해 `last_seen_at` ≈ 마지막 입력이 되게 한다. 또는 서버 판정은 `≥ lock_minutes`로 두고 핑 간격을 “입력 시 즉시 + 최대 N초 묶음(N ≤ 5)”으로 줄인다. TC-U에 “입력 59초 지속 후 비활성 → 14:30에 계속 사용 → ok”를 추가한다. |
| **R-06** | (1) | DD-002 액션 표 “확인(G-CONFIRM)” 열 · 코드 `CONFIRM_ACTIONS` ↔ PDS §6.1 “승인 대상 동작: 수락·거절·견적 승인·고객 동의 기록·결제 확인·상세 CAD 시작·고객 최종 시안 동의 기록·공장 발주·**검수 통과**·잔금 확인·발송·완료·취소…” · PDS S-03e 섹션 7 완제품 수령(확인 체크 없음) | **확인 체크가 필요한 액션 집합이 화면 원본(PDS)과 두 방향으로 다르다.** ① DD는 `receive`·`create_payment_req`에도 G-CONFIRM을 요구한다. PDS에는 두 동작의 ApprovalDialog·확인 체크가 없으므로, PDS대로 만든 화면에서 제출하면 항상 409 G-CONFIRM이 된다(TC-S-002 E2E의 수령·결제 요청 단계가 실패한다). ② PDS가 승인 대화상자로 정한 **검수 통과**는 DD에서 “(검수 폼)”이고 서버 검사(CONFIRM_ACTIONS)에서 빠졌다. 그래서 TC-S-027 “체크 없이 → 409 G-CONFIRM”이 검수 통과에서는 성립하지 않는다. | 확인 열을 PDS §6.1 목록과 같게 맞춘다(검수 통과 = `record_qc`→`pass_qc` 제출의 confirm 필수). `receive`·`create_payment_req`에 확인을 두려면 PDS §6.1·해당 화면에 ApprovalDialog를 추가하는 PDS 변경을 함께 등록한다. TC-U-031은 PDS 목록을 기준으로 정한다. |

---

## 3. 비차단 (이월 가능)

| ID | 위치 | 내용 | 제안 |
|---|---|---|---|
| N-r2-01 | DD-002 cancel 행 | 증빙·환불 결정 누락을 G-ISSUE-RESOLVE로 돌려준다. 문구가 “이 문제를 해결하려면 {fields}을(를) …”이라 취소 대화상자에서는 맞지 않는다. | PDS §5.4에 취소용 코드(예: G-CANCEL-FIELDS “취소하려면 {fields}을(를) 입력해야 해요.”)를 추가한다. |
| N-r2-02 | DD-003 오류 분류 | `404 NOT_FOUND` 종류를 새로 넣었다. BD-005 실패 계약은 4종(AUTH_EXPIRED·FORBIDDEN·RATE_LIMITED·NETWORK)이고 TC-S-021은 “4종 다른 문구”다. 5번째 종류의 화면 문구가 PDS에 없다. | BD-005·PDS에 NOT_FOUND(“폼을 찾을 수 없어요 — 폼 ID를 확인하세요”)를 등록하거나, FORBIDDEN에 합친다. |
| N-r2-03 | DD-006 가림 한계 | “→ SRS-028 보정 기록”이라고 적었지만 SRS-028 행은 바뀌지 않았다(작업 폴더에서 RTM 기록도 찾지 못함). | SRS-028 보정 열에 “패턴 밖 주소 표현은 남을 수 있음 — 전송 필드 최소화가 1차 방어”를 실제로 등록한다. |
| N-r2-04 | DD-002 send_factory G-SPEC-DIFF | 발주 시 재대조로 막혔을 때 필요한 조치는 “패킷 다시 만들기”인데, G-SPEC-DIFF 문구는 “공장 사양을 견적에 맞추거나 변경 요청…”이라 해결 경로를 안내하지 못한다. | 발주용 코드를 따로 두거나(G-PACKET-SPEC-STALE “견적이 바뀌어 패킷을 다시 만들어야 해요”), 재견적 승인 시 VALID 패킷을 VOID로 만든다. |
| N-r2-05 | `schema.sql` 트리거 | `CREATE TRIGGER IF NOT EXISTS`라서 이미 만들어진 DB에는 옛 트리거가 남는다. | 스키마 버전(`meta`)을 올리고 `DROP TRIGGER` 후 다시 만드는 이전 절차를 둔다(R1 운영 DB가 생기기 전에 끝낸다). |
| N-r2-06 | DD-002 확인 열 | `decline`은 PDS 거절 대화상자(S-03a) 구성에 확인 체크가 명시되지 않았다(§6.1 목록에는 있음). R-06을 고칠 때 함께 맞춘다. | 거절 대화상자 구성에 확인 체크를 적는다. |

---

## 4. 7기준 판정 (r2)

| 기준 | 판정 | 근거 |
|---|---|---|
| 완전성 | **FAIL** | 재시도 주체(R-01), `PAID` 상태의 상류 처리(R-02), PBS 어댑터·스케줄러의 BD 정합(B-15 잔여). |
| 명확성 | **FAIL** | 자동 확인의 actor가 정해지지 않았고(R-01), CHANGE_REQUEST 행과 대응표가 서로 다르게 읽힌다(B-03 잔여). |
| 근거성 | **FAIL** | 상류 등록 없이 상태를 추가하고(R-02), 차단을 추가했으며(R-03), 구성요소를 다시 정의했다(B-15 잔여). |
| 검증가능성 | **FAIL** | BD-008을 지키면 TC-U-034가 성립할 수 없다(R-01). 범위 금액 케이스가 없어 R-04를 판정하지 못한다. TC-S-027이 검수 통과에서 성립하지 않는다(R-06). 나머지 TC-U(040~055·060~069·080~099·100~119·130~149)는 정량으로 고쳐졌다. |
| 추적성 | PASS | TC-S AGENT 시험이 모두 WBS DoD에 배정됐고, TC-U 범위가 DD-010·WBS와 일치하며, §11 반영 기록이 r1 ID와 1:1로 대응한다. |
| 실행가능성 | **FAIL** | 보류 해제 뒤 결제 확인이 진행되지 않는다(R-01). PDS대로 만든 화면에서는 수령·결제 요청이 항상 409가 된다(R-06). |
| 비중복성 | PASS | 차단 표 원본(SRS §3-1)과 DD 대응표의 관계가 명시됐다. 어긋난 2곳(B-03 잔여·R-03)은 모순으로 처리했다. |

## 5. 요약
- 해소 13건: B-01·02·04·05·06·07·08·09·10·11·12·13·14. 이 중 B-04·06·10·13의 수정 방식에서 신규 결함이 나왔다(R-01·02·04·05·06).
- 부분 미해소 2건: **B-03**(CHANGE_REQUEST가 `recreate_payment_req`를 막지 않음 — 재동의 전 v2 결제 요청 가능), **B-15**(manual.py·scheduler.py를 BD 변경 없이 WBS에서 재정의).
- 신규 차단 6건: **R-01**(재시도 actor 미정 → 보류 해제 후 자동 확인 불가) · **R-02**(동결 BD-001에 없는 `PAID` 상태) · **R-03**(REFUND_REVIEW에 §3-1 밖 차단 추가) · **R-04**(budget 교집합 규칙이 SRS-022와 모순) · **R-05**(`≥15분` + 60초 핑 → 경고 중 ‘계속 사용’ 실패 가능) · **R-06**(확인 대상 집합 ≠ PDS §6.1).
