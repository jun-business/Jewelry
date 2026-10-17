# Jewelry OS R1 — 상세설계서: 핵심 모듈 (DD, 스테이지 9) r3

- **RTM rev.6 기준** · r2: 검토 r1 차단 15건 반영(§11) — 코드도 함께 정정
- 입력: `docs/07_BD.md` r3, `docs/03_SRS.md`(§3-1), `docs/07b_Product_Design_Specification.md`(§5)
- 조정 계획: 핵심 모듈만. 핵심 모듈의 단위 시험 판정 기준(TC-U)은 이 문서가 원본(#10 통합).
- 저작: PM(C·claude-opus-5-5·high) · 검토: separate(C)

## DD-001 공유 데이터구조: DB 스키마 🔒(FROZEN) — `jewelry_os/core/schema.sql`
- 시각: TEXT ISO-8601 UTC. **외부 시각의 원문 보존 범위**: Google Forms 시각(`createTime`·`lastSubmittedTime`)은 RFC3339 `Z`(=UTC)라 받은 문자열 그대로 저장한다(원문 = 저장값). **사람이 KST로 입력한 시각**은 UTC로 바꿔 저장하고 입력 원문을 `*_src`에 둔다 — `payment_events.looked_up_at_src`, `customer_acceptances.accepted_at_src`, `cs_messages.event_at_src`(SRS-111). 변환은 `clock.from_kst_input`(오프셋 없는 입력 = KST).
- 금액 INTEGER(원), 불리언 INTEGER 0/1, `PRAGMA foreign_keys=ON; journal_mode=WAL; busy_timeout=5000`.
- UNIQUE(멱등): `intake_responses(form_id,response_id)` · `orders(order_no)` · `orders(intake_id)` · `ai_tasks(order_id,input_revision,task_type,policy_version)` · `payment_requests(order_id,phase,quote_version)` · `payment_requests(seller_code)` · `payment_events(naver_product_order_id,event_type)` · `cs_messages(provider,sender_key,message_key)` · `quotes(order_id,version)` · `design_versions(order_id,version)` · `concepts(order_id,version)` · `policies(kind,version)` · `funnel_events(order_id,kind)` · `notifications(dedupe_key)` · 활성 보류 `holds(order_id,reason) WHERE cleared_at IS NULL`.
- **불변 트리거**: `audit_events`·`payment_events`·`customer_acceptances` UPDATE/DELETE 거부. `quotes`: `OLD.status<>'DRAFT'`이면 거부하되, **`APPROVED→SUPERSEDED`로 `status` 한 컬럼만 바뀌는 UPDATE만 허용**(나머지 전 컬럼 `IS` 비교). `quote_items`: 부모 견적이 DRAFT가 아니면 **INSERT·UPDATE·DELETE 모두 거부**. `orders.lifecycle_state` 변경은 `transition_ctx`에 행이 있을 때만.
- 낙관적 잠금: `orders.version` — 폼 숨김 필드, 불일치면 `ConflictError` → 409 G-CONFLICT(BD-004).

## DD-002 전이 서비스 🔒(FROZEN) — `domain/transitions.py`
**시그니처(BD-002와 동일)**: `check(ctx, order_id, action, actor, payload=None, submitting=False, c=None) -> list[Unmet]` · `perform(ctx, order_id, action, actor, payload, expected_version=None, effect=None)` · `revert_check/revert_last(ctx, order_id, actor, reason)` · `system_perform(ctx, order_id, action)` · `block_unmet(c, order_id, action)`.

```
ACTIONS (action → from, to, actors)
ingest_brief        INQUIRY_RECEIVED → AI_BRIEF_READY            {system}
to_review           AI_BRIEF_READY → OWNER_REVIEW                {system}
accept              OWNER_REVIEW → FREE_CONCEPT                  {maker}
decline             OWNER_REVIEW → DECLINED                      {maker}
approve_quote       FREE_CONCEPT → QUOTE_APPROVED                {maker}
record_acceptance   QUOTE_APPROVED → CUSTOMER_ACCEPTED           {maker, operator}
create_payment_req  CUSTOMER_ACCEPTED → PAYMENT_PENDING          {operator}
verify_initial      PAYMENT_PENDING → INITIAL_PAYMENT_VERIFIED   {operator}   # record_payment가 같은 트랜잭션에서 호출
start_cad           INITIAL_PAYMENT_VERIFIED → DETAIL_CAD        {maker}
record_design_ok    DETAIL_CAD → CUSTOMER_DESIGN_APPROVED        {maker}
send_factory        CUSTOMER_DESIGN_APPROVED → FACTORY_SENT      {maker}
receive             FACTORY_SENT → FACTORY_RECEIVED              {maker}
pass_qc             FACTORY_RECEIVED → QC_PASSED                 {maker}      # record_qc가 전부 통과일 때 호출
verify_final        QC_PASSED → FINAL_PAYMENT_VERIFIED           {operator}   # BALANCE 결제 입력 또는 verify_final_full
ship                FINAL_PAYMENT_VERIFIED → SHIPPED             {maker, operator}
close               SHIPPED → CLOSED                             {operator}
cancel              (활성 단계 전부) → CANCELLED                  {operator}
revalidate_design   CUSTOMER_DESIGN_APPROVED → DETAIL_CAD        {maker}      # 새 디자인 업로드의 부수효과
build_packet        (비전이, CUSTOMER_DESIGN_APPROVED)            {maker}
```
- system 전이는 `ingest_brief`·`to_review` 둘뿐. `system_perform`은 `actors == {system}`이 아닌 모든 액션을 `Forbidden`(SRS-116).

**차단 표 대응(SRS §3-1 → action)** — 표의 한국어 열 대응: 견적 승인=`approve_quote` · 고객 동의 기록=`record_acceptance` · 결제 요청 만들기=`create_payment_req`·`create_balance_req`·`recreate_payment_req` · 결제 확인=`verify_initial` · 잔금 확인=`verify_final` · 상세 CAD 시작=`start_cad` · 고객 최종 시안 동의 기록=`record_design_ok` · 발주=`send_factory`·`build_packet` · 검수 통과=`pass_qc` · 발송=`ship` · 주문 완료=`close` · 완제품 수령=`receive`.
- “모든 (사람) 진행 전이”(HOLD·DISPUTED·NO_CONSENT) = PROGRESS 집합 = 위 액션 전부(**`receive` 포함**). 진행이 아닌 것 = `decline`·`cancel`·`revert`·`revalidate_design`·system 전이. REFUND_REVIEW만 `receive`를 허용(표의 명시 예외).

| 보류/이슈 | 막는 action |
|---|---|
| NEEDS_INFO | approve_quote, record_acceptance, create_payment_req, create_balance_req, recreate_payment_req, verify_initial, start_cad, send_factory, build_packet |
| WAITLISTED | — |
| HOLD · DISPUTED · NO_CONSENT | PROGRESS 전부 |
| PAYMENT_MISMATCH | verify_initial, start_cad, send_factory, build_packet, verify_final, ship |
| REFUND_REVIEW | start_cad, send_factory, build_packet, pass_qc, verify_final, ship |
| DELIVERY_DELAY | ship, close |
| CHANGE_REQUEST | create_payment_req, create_balance_req, recreate_payment_req, verify_initial, start_cad, record_design_ok, send_factory, build_packet |
| CANCEL_REQUEST | send_factory, build_packet, pass_qc, ship |
| REWORK | pass_qc, ship |

- 보류 Unmet 코드 = `G-HOLD-{reason}`(문구는 G-HOLD 템플릿), NO_CONSENT = `G-NO-CONSENT`, 이슈 = `G-ISSUE-OPEN`.

**check 평가 순서**: ① 종료 G-ENDED ② 단계 불일치 G-WRONG-STATE ③ 역할 G-ROLE-*·G-SYSTEM-ONLY ④ 보류 ⑤ 이슈 ⑥ 액션 고유 조건 ⑦ 제출 시: 사유 G-REASON·확인 G-CONFIRM.

| 액션 | 고유 조건(Unmet) | 확인(G-CONFIRM) |
|---|---|---|
| accept | 휴무일 G-HOLIDAY · 상한 G-CAPACITY(`reason` 있으면 충족) | 필요 |
| decline | 거절 분류 G-DECLINE-CATEGORY · 사유 G-REASON | 필요 |
| approve_quote | 시안 ≥1 G-NO-CONCEPT · DRAFT 견적 G-QUOTE-MISSING · 필수 G-QUOTE-FIELDS(품목 ≥1·합>0, 약속 납기, 유효기간, 세금 포함 여부, 배송비 포함 여부, 제작 사양, **적용 정책 버전 = 승인된 수정·환불 정책 존재**) · 합계 G-QUOTE-TOTAL | 필요 |
| record_acceptance | 증빙 G-ACCEPT-EVIDENCE | 필요 |
| create_payment_req | 승인 견적 G-QUOTE-MISSING · 시험 견적 실거래 불가 G-TEST-QUOTE | — (화면 대화상자의 확인은 UI 편의) |
| verify_initial | 요청 존재 G-PAYMENT-REQUEST · (PAID 합 − REFUND 합) = 기대 G-PAYMENT-UNVERIFIED | (결제 입력 폼의 확인) |
| verify_final | BALANCE 요청 존재 G-PAYMENT-REQUEST · (PAID − REFUND) = 기대 **G-BALANCE** · FULL 정책은 조건 없이 충족(verify_final_full, 확인 필요) | (결제 입력 폼 / full 확인) |
| start_cad | (보류·이슈 외 없음) | 필요 |
| record_design_ok | 디자인 버전 G-DESIGN-MISSING · 증빙 G-ACCEPT-EVIDENCE | 필요 |
| build_packet | 최신 디자인의 고객 동의 G-DESIGN-UNAPPROVED · 사양 완비 G-SPEC-INCOMPLETE · 견적 사양 일치 G-SPEC-DIFF | — |
| send_factory | 유효 패킷 G-PACKET-MISSING · 최신 디자인 G-PACKET-STALE · 해시 재계산 G-PACKET-HASH · 견적 사양 재대조 G-SPEC-DIFF | 필요 |
| receive | 수령일·실원가 G-RECEIVE-INFO | — |
| pass_qc | 6항목 기록 G-QC-INCOMPLETE · 불통과 0 G-QC-FAIL | 필요(검수 폼의 확인 체크) |
| ship | 배송 정보 G-SHIP-INFO | 필요 |
| close | 열린 이슈 0 + 배달 근거 G-DELIVERY | 필요 |
| cancel | 사유 G-REASON · 귀책 G-FAULT · 증빙·환불 결정 G-ISSUE-RESOLVE(필드명) · (확인된 결제 > 0이면 “환불할 결제 없음” 불가) | 필요 |
| revert | 아래 되돌리기 규칙 G-REVERT · 사유 G-REASON | — |

**perform 트랜잭션**: `BEGIN IMMEDIATE`(바깥 트랜잭션이 있으면 합류) → 버전 확인 → check(submitting=True) → `transition_ctx` 토큰 삽입 → 상태 UPDATE(version+1, state_entered_at) → 토큰 삭제 → `effect(c, order)` → 감사 이벤트 → 퍼널 이벤트 → 종료 단계면 closed_at → 커밋. 거부 중 G-WRONG-STATE·G-ROLE-*·G-SYSTEM-ONLY·G-ENDED는 롤백 뒤 감사 `transition_rejected`(요청 액션·코드) 기록(SRS-080).
- **퍼널 이벤트**: accept→accepted · approve_quote→quoted · verify_initial→paid · ship→shipped · close→closed · decline→declined · cancel→cancelled(+ 접수 시 inquiry).

**결제 입력과 확인(BD §6.4)**: `record_payment`는 **한 `db.tx()` 안에서** 이벤트 INSERT → 해당 요청의 (PAID − REFUND) 누적 합 계산 → ≠ 기대면 요청 MISMATCH + PAYMENT_MISMATCH 이슈 / = 기대면 요청 `PAID`(확인 대기) 후 같은 트랜잭션에서 `verify_*` `perform`(중첩 tx 합류). verify가 보류·이슈로 막히면 이벤트와 `PAID`(결제 확인 대기 — BD-001 결제 요청 상태에 추가) 상태는 커밋되고 단계는 그대로이며, **차단을 푸는 동작(보류 해제·이슈 해결·동의 기록)이 끝난 뒤 `try_verify`가 한 번 다시 시도**한다. 재시도의 주체는 **그 요청에 마지막 결제완료를 입력한 operator**다(그 입력에서 이미 확인 체크를 했으므로). 감사 이벤트에는 그 operator가 기록되고, 해제한 사람(maker 가능)은 해제 이벤트에 남는다. 취소·반품 상태 입력은 REFUND_REVIEW 이슈. 결제 입력 폼은 확인 체크 필수.

**되돌리기 규칙(SRS-156, IMPLICIT-12)**
- 대상: 직전 사람 전이 1건(감사 이벤트 기준). 불가 목록: system 전이, `decline`·`cancel`·`close`(종료), `create_payment_req`(결제 요청 생성 — 링크가 나갔을 수 있음), `verify_initial`·`verify_final`(결제 확인), `record_acceptance`·`record_design_ok`(고객 동의), `send_factory`(발주), `ship`, `revalidate_design`.
- 그 뒤 생긴 기록이 있으면 불가: 결제 요청·발주 패킷·고객 동의·QC 기록. **시안 업로드는 막지 않는다**(자산은 보존).
- 부수효과: `accept` 되돌림 → `accepted_at` NULL, 퍼널 `accepted` 삭제 · `approve_quote` 되돌림 → 승인본 SUPERSEDED + 같은 내용의 DRAFT 새 버전 생성(승인본 불변 유지), 퍼널 `quoted` 삭제 · `start_cad`·`receive` 되돌림 → 단계만.
- 상한 계산: `active = count(lifecycle_state ∈ FREE_CONCEPT..FINAL_PAYMENT_VERIFIED)`, 시험 주문 포함.

- **확인(G-CONFIRM) 액션 집합** = PDS §6.1 승인 동작: accept, decline, approve_quote, record_acceptance, start_cad, record_design_ok, send_factory, pass_qc, ship, close, cancel (+ 결제 입력 폼·verify_final_full·Gate 기록·정책 승인·CS 승인은 각 서비스가 검사).

**TC-U-001~039 (판정 기준)**
- 001–018: TRANSITIONS 18개 각각 정상 from/actor에서 perform 성공, 다른 단계 → G-WRONG-STATE, 다른 역할 → G-ROLE-*.
- 019: `system_perform`을 actors에 system이 없는 모든 액션에 호출 → 전부 Forbidden, 상태 불변.
- 020–030: 차단 표 11행 × (막는 action 전부 → 해당 코드, 표에 없는 action → 그 보류/이슈 코드 없음).
- 031: CONFIRM_ACTIONS 각각 confirm 없이 → G-CONFIRM, 있으면 G-CONFIRM 없음.
- 032: approve_quote — 승인된 수정·환불 정책 없음 → G-QUOTE-FIELDS(“적용 정책 버전”).
- 033: 결제 누적: 기대 400,000에 350,000 → MISMATCH, +50,000 → verified / 400,000 후 REFUND 400,000 → verify 조건 불충족.
- 034: HOLD 보류 중 완납 입력 → 단계 PAYMENT_PENDING·요청 PAID → 보류 해제 → 자동 확인 1회(단계 INITIAL_PAYMENT_VERIFIED).
- 035: 결제 입력 중 verify 전이 단계에서 예외 → 이벤트·단계 모두 롤백(TC-I-003).
- 036: 되돌리기 — accept 성공, approve_quote 성공(승인본 SUPERSEDED·DRAFT 생성), create_payment_req 거부, 시안 업로드 뒤 accept 되돌림 성공·시안 보존, 고객 동의 기록 뒤 거부.
- 037: 표 밖 요청 → 409 + 감사 `transition_rejected` 1건.
- 038: 낙관적 잠금 — 같은 version으로 두 번 → 두 번째 ConflictError.
- 039: send_factory — 패킷 후 재견적으로 견적 사양 변경 → G-SPEC-DIFF.

## DD-003 Google 동기화 — `services/intake.py`
```
sync_once(ctx):
  form_id = settings.google_form_id;  is_test_form = (mode == mock) or (purpose == test)
  since = (설정 sync_incremental ? cursor.last_success_at − 1일 : None)     # 기본 None = 전체 페이지
  stats = {new, revised, total}
  loop pages(page_token):                                  # SourceError → 커서 오류 기록·백오프, 성공 커서 불변, 반환
    for r in page:                                          # 응답 하나 = 트랜잭션 하나
      row = intake_responses[(form_id, r.responseId)]; digest = sha256(canonical(answers))
      if row is None:
        tx: response(rev 1) + revision + customer(연락처 암호화) + order(INQUIRY_RECEIVED, is_test = is_test_form)
            + 감사 + 퍼널(inquiry)
            + 상한 도달이면 보류 WAITLISTED
            + 동의 답 없으면 보류 NO_CONSENT
            + 필수(표시명·회신수단·제품유형·설명) 비면 보류 NEEDS_INFO
            + 파일 가져오기 작업 등록
        after tx: analysis.process_new(order)               # NO_CONSENT면 AI 없이 ingest_brief·to_review만
      elif lastSubmittedTime 다름 or digest 다름:
        tx: revision+1, orders.input_revision, 감사 intake_revised, 보류 재평가(동의·필수)
        after tx: process_new(order)                         # NO_CONSENT면 AI 미실행, 단계는 이미 OWNER_REVIEW
      else: no-op
  tx: cursor.last_success_at = now, last_count = total, status OK, attempts 0      # 모든 응답 커밋 뒤에만
```
- 동시성: UNIQUE + `IntegrityError` → no-op(다른 워커가 이미 넣음).
- 오류: 401 → refresh 1회 → 실패 `AUTH_EXPIRED` · 403 `FORBIDDEN` · 404 `NOT_FOUND` · 429 `RATE_LIMITED`(Retry-After 또는 2^n분, ≤60) · 연결/5xx `NETWORK`(2^n분, ≤30).
- **TC-U-040~059**: 040 신규 1건 → 주문 1·revision 1·OWNER_REVIEW · 041 같은 응답 10회 → 주문 1 · 042 lastSubmittedTime 같고 digest만 다름 → revision 2 · 043 lastSubmittedTime만 다름 → revision 2 · 044 3페이지(50·50·7) → 107건 · 045 2번째 응답 처리 중 예외 → 1번째 커밋 유지·커서 = 이전 값 · 046 재실행 → 누락 0·중복 0 · 047 동의 없음 → NO_CONSENT·`ai_tasks` 0·OWNER_REVIEW · 048 필수 누락 → NEEDS_INFO · 049 상한 도달 → WAITLISTED · 050 IntegrityError 경합 → no-op · 051 401/403/429/NETWORK 각각 커서 상태 ERROR·성공 시각 불변 · 052 429 Retry-After 30 → next_attempt = now+30초 · 053 매핑 필수 필드 누락 → MappingError · 054 매핑에 주소 필드 → MappingError · 055 파일 실패 → 자리표시 자산 FAILED + 작업 FAILED, 재시도 성공 → OK.

## DD-004 견적 계산 — `domain/money.py`
- `total = Σ items.amount`(입력받지 않음). THIRDS: `deposit = (total + 2) // 3`, `balance = total − deposit`(0이면 잔금 요청 없음 = NOT_APPLICABLE). FULL: `deposit = total`, `balance = None`.
- **TC-U-060~069(값 표)**: 1 → 1/0 · 2 → 1/1 · 3 → 1/2 · 999,999 → 333,333/666,666 · 1,000,000 → 333,334/666,666 · 1,200,000 → 400,000/800,000 · 9,999,999,999 → 3,333,333,333/6,666,666,666 · FULL 1,200,000 → 1,200,000/None · 음수 → ValueError · `parse_won("1,200,000원")` = 1200000, `"abc"` → ValueError.
- 재견적 잔금(BD §6.5): 새 총액 − 확인된 예약금, ≤0이면 잔금 없음(차액 환불은 이벤트 입력).

## DD-005 발주 패킷 해시 — `services/manufacturing.py`
- `spec_canonical = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(',',':'))`, `packet_hash = sha256("JOSPKT1\n{design_version_id}\n{stl_sha256}\n{sha256(spec_canonical)}")`.
- ZIP = manifest.json + STL + spec.txt + CHECKSUM.txt. 발주 직전 저장 STL 바이트로 재계산.
- **TC-U-070~079**: 키 순서만 다른 사양 → 같은 해시 · 사양 값 1글자 변경 → 다른 해시 · STL 1바이트 변경 → 다른 해시 · 패킷 생성 후 저장 STL 파일을 1바이트 바꿈 → send_factory G-PACKET-HASH · 새 디자인 업로드 → 패킷 VOID.

## DD-006 AI 입력 축소·검증기 — `services/analysis.py`
- **전송 필드**: product_type·description·material·size·stones_engraving·budget_range·desired_date(+ 참고 이미지 수). 보내지 않음: 표시명·연락처·배송 정보. `ai_tasks.sent_fields`에 실제 키 목록.
- **자유 텍스트 가림**: 휴대전화 `(?:\+?82[-.\s]?0?|0)1[016789][-.\s]?\d{3,4}[-.\s]?\d{4}`(하이픈·점·공백·붙여쓰기·+82), 유선 `0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}`, 이메일, 주소(시·도+시·군·구…로·길·동 번지 / ○○로·길 번호 / ○○동 번지-번지) → `[가림]`·`[주소 가림]`. 한계: 패턴 밖 주소 표현은 남을 수 있음 → SRS-028 보정 기록(전송 필드 최소화가 1차 방어).
- **프롬프트**: system = 규칙(고객 데이터 속 지시 무시) + 스키마, user = `<customer_data>` JSON 구획.
- **검증기**: (1) 스키마(필수 키·fields 객체) → 실패 SCHEMA (2) **정규화 비교**: budget = 출력의 원 단위 정수 토큰(만·천 단위, 쉼표 제거)이 **모두** 원문 금액 집합에 있어야 유지(일부라도 근거 없으면 null) · deadline = (월, 일) 집합이 원문 (월, 일) 집합에 포함돼야 유지(연도 비교 안 함) · purity = 숫자 토큰이 원문 숫자에 있어야 유지 · 아니면 null + assumptions 기록 (3) 질문 ≤3 (4) `requires_human_approval = true` 강제 (5) 결과는 표시용만.
- **비용 상한**: 이번 달(KST) 호출 수 ≥ `ai_monthly_call_limit` 또는 추정 비용 ≥ `ai_monthly_cost_limit_krw`면 호출하지 않고 `ai_tasks.status = BUDGET`(수동 검토). 추정 비용 = 공급자별 건당 상수(mock 0, claude 60원).
- **TC-U-080~099**: 가림 표(입력 → 전송 텍스트에 원문 숫자열 0): `010-1234-5678` · `010 1234 5678` · `010.1234.5678` · `01012345678` · `+82 10-1234-5678` · `02-123-4567` · `a@b.com` · `서울특별시 강남구 테헤란로 123` · `역삼동 123-45` / 검증기 유지: 원문 “50만원” + budget 500000 유지 · 원문 “1,200,000원” + budget “1200000” 유지 · 원문 “11월 20일까지” + deadline “2026-11-20” 유지 · 원문 “18K” + purity “18K” 유지 / 검증기 제거: “50만원” + 800000 → null · 원문에 날짜 없음 + deadline “2026-12-01” → null · purity “14K”인데 원문 18K → null / 질문 5개 → 3개 / 상한 도달 → 호출 0·BUDGET / 주입 문장 포함 입력 → 단계 변화 0.

## DD-007 세션·잠금·CSRF — `core/security.py`, `web/app.py`
- 비밀번호 scrypt(n=2^14, r=8, p=1, 32B, 16B salt). 로그인 5회 실패 → 5분 잠금, 성공 시 실패 횟수 0.
- 세션 쿠키 `jos_session`(HttpOnly·SameSite=Strict). 활동 = 화면 요청·폼 제출·입력 활동 핑(`GET /api/session/ping`, 입력 있을 때 ≤60초 간격 + **마지막 입력 뒤 한 번 더 보내는 후행 핑** — 서버의 `last_seen`이 항상 마지막 입력 이후가 되어 서버가 화면보다 먼저 잠그지 않는다; “계속 사용”은 즉시 핑). 대시보드 숫자 갱신(`/api/dashboard/summary`)·알림 조회(`/api/notifications`)는 활동이 아니다. 잠금 해제·로그인 시 `last_seen_at = now`.
- **서버 잠금 판정**: `locked = 1` 또는 `now − last_seen_at ≥ lock_minutes`. 응답(BD-006): **화면 200 + 잠금 화면 템플릿**, JSON·파일 401 `{locked:true}`. 해제 = 비밀번호(`POST /api/session/unlock` 또는 `/locked/unlock`).
- 절대 만료: `now − created_at > 12h` → 세션 삭제 → 화면 303 `/login?next=…&expired=1`, JSON 401.
- CSRF: 세션별 토큰. 폼 POST = 숨김 필드 `csrf`(라우트 `form_of`가 검사), JSON POST = 헤더 `X-CSRF-Token`(미들웨어). 불일치 403.
- **TC-U-100~119**: last_seen 14:59 전 → ok · 15:00 → locked · 15:01 → locked · 잠금 중 `/api/notifications` 401 · 잠금 중 화면 200 + 잠금 템플릿 · 해제 후 ok · 12h 초과 → 화면 303·API 401 · CSRF 없는 폼 POST 403 · 해시 형식 `scrypt$…`·검증 · 5회 실패 후 정답 거부, 5분 경과 후 허용 · 성공 로그인 후 실패 횟수 0.

## DD-008 백업 — `services/backup.py`
- DB 온라인 백업 + `files/` → ZIP → Fernet(키 `JOS_BACKUP_KEY`) → `backups/jos-YYYYMMDD-HHMMSS.josbak`. 매일 03:00 KST 또는 마지막 성공 26h 초과 시. 보관 30일(설정). 복원은 앱 정지 후 CLI: 복호화 → 무결성 검사 → 첨부 해시 대조 → 기존 폴더를 `*.before-restore-*`로 옮기고 교체.
- 한계(이월): ZIP 전체를 메모리에서 암호화 — 데이터가 수 GB가 되면 스트리밍 암호화 필요(TBD-008).
- **TC-U-120~129**: 잘못된 키 → RestoreError·원본 무변경 · 파일 앞 4바이트 ≠ `PK\x03\x04` · 복원 후 주문·첨부·감사·동의 건수 일치 · 첨부 1개 변조 백업 → RestoreError.

## DD-009 알림·임계 — `services/alerts.py`, `services/capacity.py`
- 60초 주기 평가 → `notifications` upsert(dedupe_key), 조건이 사라지면 resolved. 고객 응답 지연·납기 위험·결제 문제·시스템(동기화·백업·디스크) — 기준은 해석서 EXP-003·007.
- **release_at**: 생성 시각(KST)이 휴지 구간 [시작, 끝)에 있으면 **다음에 오는 끝 시각**, 아니면 생성 시각. 자정을 넘는 구간(시작 > 끝)은 시작 이후면 다음 날 끝, 끝 이전이면 같은 날 끝.
- **TC-U-130~149**: (00:00–07:00) 00:00:00 → 07:00 · 01:30 → 07:00 · 06:59:59 → 07:00 · 07:00:00 → 즉시 · 23:59 → 즉시 / (22:00–06:00) 21:59:59 → 즉시 · 22:00 → 다음 날 06:00 · 23:00 → 다음 날 06:00 · 05:00 → 같은 날 06:00 · 06:00 → 즉시 / 고객 응답 지연 71:59 없음·72:01 있음 / 납기 약속일−오늘 4 없음·3 있음 / 공장 예상일 당일 없음·다음 날 있음.

## DD-010 TC-U 범위
| 모듈 | 범위 | 기준 |
|---|---|---|
| domain/transitions | 001~039 | DD-002 |
| services/intake | 040~059 | DD-003 |
| domain/money | 060~069 | DD-004 |
| services/manufacturing(해시) | 070~079 | DD-005 |
| services/analysis | 080~099 | DD-006 |
| core/security | 100~119 | DD-007 |
| services/backup | 120~129 | DD-008 |
| services/alerts·capacity | 130~149 | DD-009 |
| 그 외 | — | TC-S로 판정(별도 TC-U 없음) |

## §11-2 검토 r2 반영(r3, PM 마감)
B-03 잔여→CHANGE_REQUEST에 recreate_payment_req · B-15 잔여→BD PBS 정정(BD r3 재터치) · R-01→재시도 주체 = 결제 입력 operator · R-02→`PAID` 상태 BD-001·PDS 라벨 추가 · R-03→REFUND_REVIEW에서 create_balance_req 제거 · R-04→budget 부분집합 규칙 · R-05→후행 핑 · R-06→확인 집합 = PDS §6.1.

## §11 검토 r1 반영
B-01→DD-001 불변 트리거(스키마 수정) · B-02→DD-001 원문 범위 + `*_src` 3컬럼(스키마) · B-03→DD-002 대응표·`receive` 포함·비전이 결제 요청 차단(코드) · B-04→결제 입력·확인 한 트랜잭션 + 차단 후 재시도(코드) · B-05→되돌리기 불가 목록·부수효과 · B-06→정책 버전·확인 열·cancel 코드(코드) · B-07→시그니처·G-BALANCE·PAID−REFUND·G-HOLD-{reason}·퍼널 7종(코드) · B-08→DD-003 분기·TC-U · B-09→값 표 · B-10→정규화 비교·유지 케이스(코드) · B-11→가림 규칙 확대(코드) · B-12→잠금 화면 200 · B-13→`≥ lock_minutes`(코드) · B-14→release_at 규칙·값 표 · B-15→WBS 표 갱신. 비차단 N-05(send_factory 사양 재대조) 코드 반영, N-08 TBD-008.
