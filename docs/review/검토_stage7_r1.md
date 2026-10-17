# 검토 — 스테이지 7 기본설계(BD) + Product Design Specification(PDS) r1

- 검토 대상: `docs/07_BD.md`(RTM rev.4 기준), `docs/07b_Product_Design_Specification.md`(JOS-PDS-001 v1.0), 보조 `docs/design/design-tokens.json`·`contrast_report.txt`
- 대조 원본(상류): `docs/03_SRS.md`, `docs/01_PRD_해석서.md`, `Jewelry_OS_PRD_v1.0.md` §8·§9·§10·§11·§13. 문구·판정 기대값 대조용으로 `docs/04_TC-S.md` 참고
- 검토자: separate reviewer · 모델 `claude-opus-5-5`
- 방식: 적대적 검토(통과하면 안 되는 이유 찾기). SRS `구현`/`구조` 146행 ↔ BD §2 FUN 배정 ↔ PDS §15 추적은 스크립트로 기계 대조했고, 토큰 대비는 `check_contrast.py`와 같은 공식으로 PDS가 실제로 쓰는 쌍 28개를 추가로 계산했다.
- 차단 기준: (1) 상류와 모순 (2) 상류 누락(SRS `구현`/`구조` 행이 FUN 배정과 PDS 화면·패턴 어디에도 없음) (3) 이 단계가 정해야 할 것(모듈 경계·인터페이스·화면 구성·상태별 UI·문구·예외 상태)이 빠져서 구현 worker가 추측해야 함. BD↔PDS 불일치는 (1)이나 (3)으로 본다.

## 판정: **반려 — 차단 13건**

---

## 1. 차단 지적

| ID | 위치 | 내용 | 수정 제안 |
|---|---|---|---|
| **B-01** | BD §2 FUN 표, §8 | **FUN 배정에서 빠진 SRS `구현`/`구조` 행이 22건이다.** 031, 035, 086, 091, 093, 094, 095, 096, 097, 105, 111, 133, 142, 144, 160, 161, 162, 163, 164, 165, 166, 167. 그런데 BD §8은 “SRS 전체의 FUN·PBS 배정은 §2 표가 원본”이라고 선언한다. 그래서 RTM 7부의 FUN 열이 비고, FUN 단위로 업무지시서를 내면 서버 규칙인 161(사양 차이 패킷 거부), 163(귀책 구분), 164(몰수 없음), 166(거절 분류·유효 문의), 167(Google 실제 어댑터)을 아무 worker도 맡지 않는다. 몇몇은 본문(원칙 2 = 142, BD-004 = 086)이나 PDS에서 다루지만, 배정 원본에는 없다. 반대 방향 오류도 있다. FUN-001의 범위 표기 `002~019`에 `운영` 행인 005가 들어가 있고, §8 `운영` 목록에는 SRS-159가 빠져 있다. | §2 표에 22건을 배정한다. 제안: 031·133·142·144 → FUN-003/012 · 035 → FUN-010 · 086·097 → FUN-001/003(경합·반복 멱등) · 091·105 → FUN-007(구조: 송신 함수·Webhook 없음) · 093~096 → FUN-001/010 · 111 → FUN-012(+PBS-001 `clock.py`) · 160 → FUN-009/010 · 161·162·163 → FUN-008(161은 FUN-005와 공동) · 164 → FUN-006 · 165 → FUN-005/007 · 166 → FUN-003/011 · 167 → FUN-001. FUN-001 범위를 `002~004, 006~019`로 고치고, §8 `운영` 목록에 159를 넣는다. |
| **B-02** | BD §1 아키텍처, PBS-003, BD-005 `FormsSource`/`FileSource` / PDS S-00a 3단계 | **상류와 모순: Google 실제 어댑터를 R1에서 구현하지 않는 설계다.** SRS §0(검토 r1 B-01 반영)과 SRS-167은 “Google Forms·Drive 실제 어댑터(OAuth 설치형 앱 흐름 + REST)를 R1에서 구현”하라고 하고, SRS-145(b)·EXP-016은 실계정 “시험 폼” 연동을 허용한다. 그런데 BD의 R1 구현은 `MockGoogleForms`·`MockDrive`뿐이다. PDS S-00a는 “실제 계정(R2 준비 중 — 비활성)”이라고 적어서, 같은 PDS의 S-09(“Google 계정 연결(OAuth)”, 실계정+시험 폼)과도 맞지 않는다. OAuth 콜백 라우트, refresh token 저장 위치, 토큰 갱신·`Retry-After` 처리 계약이 없어서 TC-S-052를 설계 근거 없이 구현해야 한다. | BD-005에 `GoogleFormsHttp`·`GoogleDriveHttp`(R1 구현)를 Mock과 나란히 둔다. 계약: 루프백 리디렉션 OAuth, refresh token은 사용자 전용 ACL 비밀 파일(B-03 경로), 401이면 갱신 1회 후 `AUTH_EXPIRED`, 429는 `Retry-After`를 `retry_after`로 매핑, `forms.responses.list` 페이지 토큰, Drive `files.get?alt=media`. PBS-003에 `google_oauth.py`를 넣고, BD-006에 OAuth 시작·콜백 라우트를 넣고, TC-I(HTTP MockTransport 계약)를 추가한다. PDS S-00a 3단계는 “시험용 Mock(기본) / 실제 계정 — Gate 통과 전에는 시험 폼으로만”으로 고친다. |
| **B-03** | BD 전반(PBS-001 `config.py`), PDS 해당 없음 | **상류 누락: SRS-160(구현, T+I)이 BD와 PDS 어디에도 없다.** 앱 데이터 폴더 `%LOCALAPPDATA%\JewelryOS`, 비밀 파일의 사용자 전용 ACL, “DB·고객 파일은 앱 인증 없이 웹으로 제공되지 않음”이 설계에 없다. TC-S-049(prod 기본 경로, `/data`·DB 직접 URL 404)와 SRS-107(test/prod 별도 DB 경로)이 기댈 설계가 없다. | BD에 “데이터 위치·비밀 저장” 절을 둔다. 루트 `%LOCALAPPDATA%\JewelryOS\{prod|test}\`(db/, files/, backups/, secrets/), secrets는 생성 시 현재 사용자만 읽게 ACL을 걸고, 정적 마운트는 `web/static`만 허용하며, 파일은 B-05의 인증 라우트로만 내보낸다. 이 절을 PBS-001 `config.py`·`security.py`에 배정한다. |
| **B-04** | PDS 전체, BD-002 `Actor` | **SRS-143이 가리키는 “권한 표(PDS §4.3)”가 없다.** PDS §4.3은 타이포그래피다. 역할 정보는 §3.2 역할 열(“공통”, “operator 편집” 같은 화면 단위 표기), §5.3 주체 열, 화면별 산문에 흩어져 있고, BD도 전이 액션의 `actors`만 다룬다. 전이가 아닌 쓰기 동작의 역할이 정해지지 않았다. 예: 시안 올리기, 견적 작성·임시 저장, 사양서·패킷 만들기, CAD 날짜 정하기, 고객 메시지 기록·답변 초안 만들기, 이슈 열기·해결, 확정 사양 정정, 정보 요청·대기·보류 붙이기·풀기, 수기 접수, 고객 연결, 연락처 “보기”, 작업시간, 불일치 해결. TC-S-026(“허용 표 그대로, 불허=403”)의 판정 기준이 문서에 없어서 worker가 권한을 추측해야 한다. | PDS에 “역할 권한 표”(동작 × maker/operator, 근거 EXP-014)를 새 절로 넣는다. BD-002/BD-006에는 그 표를 코드로 옮긴 `PERMISSIONS: dict[action, set[Role]]`과 서버 강제 지점(라우트 의존성)을 정한다. SRS-143의 “PDS §4.3” 참조는 RTM 변경으로 새 절 번호로 고친다. |
| **B-05** | BD-006 HTTP 계약 ↔ PDS §3.2·§7·§13.1, TC-S | **인터페이스가 빠지거나 불일치해서 worker가 추측해야 한다.** (a) PDS에는 있는데 BD-006에 없는 라우트: `/orders/new`(S-12), `/customers` 목록(S-07), `GET /api/dashboard/summary`(S-01), 알림 목록 패널(C-26), 로그아웃, OAuth 콜백(B-02). (b) 둘 다에 없는 라우트: 인증 파일 `/assets/{id}`(SRS-009, TC-S-022, EXP-006의 “파일 URL 401”), 썸네일, 패킷 ZIP 내려받기. (c) BD-006의 쓰기 동작은 `POST /orders/{no}/actions/{action}` 하나뿐이다. 설정 각 절, Gate 기록, 정책 승인·폐기, CS 메시지·초안 승인·발송 기록, 결제 확인 입력, 파일 업로드, 고객 연결·개인정보 삭제, 지금 백업·지금 가져오기·AI 다시 시도, 작업시간, 광고비의 계약이 없다. (d) `{action}` 코드 목록이 없다. BD-002 `TRANSITIONS`의 action 이름은 `verify_initial_payment`·`verify_final_payment`·`system_revalidate`만 나오고, PDS 버튼 라벨(§5.3)과 연결하는 표가 없다. (e) 상태 코드 규칙이 충돌한다. BD는 Unmet이 있으면 409를 주는데, G-ROLE-*도 Unmet이다. 한편 TC-S-026은 역할 거부를 403으로, TC-S-029는 증빙 누락을 422로 기대하는데, PDS 카탈로그는 G-PAYMENT-EVIDENCE·G-ACCEPT-EVIDENCE·G-CONFIRM·G-REASON을 Unmet으로 둔다. (f) BD-006은 409 응답을 “Unmet 목록 HTML 조각”으로 정의하지만, PDS §13.1은 JS 없이 form POST만으로도 동작해야 한다고 한다. JS가 없을 때 409에 무엇을 렌더하는지 정해지지 않았다. | BD-006을 표로 완성한다(메서드·경로·역할·ViewModel/폼·성공·실패). action 카탈로그 표를 추가한다(action 코드 · from→to 또는 비전이 · actors · PDS 버튼 라벨 · guard 코드). 상태 코드 규칙을 하나로 정한다. 제안: 401 = 비로그인·잠금, 403 = G-ROLE-*(본문 문구는 카탈로그 문구), 409 = 상태·조건 guard, 422 = 입력·증빙 필드 오류(필드별 메시지도 카탈로그 문구). 어느 코드든 문구 원본은 §5.4로 한다. JS가 없을 때의 409·422는 같은 화면을 C-14 목록과 함께 전체 렌더하고, JS가 있을 때만 조각을 쓴다. |
| **B-06** | BD §6 결제 요청 상태 ↔ PDS §5.2·S-03c·S-03f | **BD와 PDS의 상태 불일치, 그리고 잔금 흐름이 정해지지 않았다.** BD의 결제 요청 상태는 `DRAFT → LINK_READY → PAID_VERIFIED | MISMATCH | CANCELLED`인데, PDS 라벨 사전에는 `NOT_APPLICABLE`(해당 없음)이 있다. PRD §8.1과 SRS-047·114는 FULL 정책일 때 잔금을 `NOT_APPLICABLE`로 **기록**하라고 한다. 그 기록이 결제 요청 상태인지 별도 플래그인지 BD에 없다. 잔금(BALANCE) 결제 요청을 언제 누가 만드는지도 없다. CUSTOMER_ACCEPTED의 “결제 요청 만들기” 때 둘 다 만드는지, QC_PASSED 이후에 만드는지에 따라 SRS-157 받을 돈 합계, S-04 “잔금 대기”, G-BALANCE 판정이 달라진다. `DRAFT→LINK_READY` 전이의 주체와 입력(구매 링크 URL 수동 입력)도 상태도에 없다. | BD §6 결제 요청 상태도에 `NOT_APPLICABLE`(FULL의 BALANCE 자리)을 넣고, 생성 규칙을 정한다. 제안: CUSTOMER_ACCEPTED에서 정책에 따라 DEPOSIT+BALANCE 또는 FULL+BALANCE(NOT_APPLICABLE)를 한 번에 만들고, BALANCE는 QC_PASSED 전에 확인할 수 없게 한다. 상태별 주체(operator)도 적는다. PDS S-03c 카드 목록과 S-03f 섹션 3을 이 규칙에 맞추고, G-BALANCE 판정 근거를 적는다. |
| **B-07** | BD §6, PDS S-03b, §5.3 공통 보조 조치 | **변경 요청(CHANGE_REQUEST) 흐름의 상태·화면이 정해지지 않았다**(SRS-046, SRS-161(b), TC-S-011). 수락 후 새 견적 버전 승인과 고객 재동의는 라이프사이클 전이 `FREE_CONCEPT→QUOTE_APPROVED`와 `QUOTE_APPROVED→CUSTOMER_ACCEPTED`의 from 단계와 맞지 않는다. 그런데 CUSTOMER_ACCEPTED, DETAIL_CAD, CUSTOMER_DESIGN_APPROVED 단계에서 이 동작을 무엇으로 수행하는지(비전이 액션인지 되돌림인지) 없다. 이미 확인된 예약금(옛 quote_version)과 새 총액의 차액, `UNIQUE(order,phase,quote_version)`에 따른 새 결제 요청 생성, 이슈 자동 해결 시점, 패킷 무효화도 정해지지 않았다. PDS에는 경고 문장 한 줄(“새 견적 버전을 만들고 고객 동의를 다시 받으세요”)만 있다. | BD §6에 “변경 요청 처리”를 둔다. 제안: 단계는 그대로 두고 quotes 서비스의 비전이 액션 `approve_quote_revision`·`record_reacceptance`를 쓴다(maker / maker·operator). 재동의가 기록되면 CHANGE_REQUEST가 자동으로 해결되고, 확인되지 않은 결제 요청은 CANCELLED 후 새 버전으로 다시 만든다. 확인된 예약금은 유지하고 차액은 BALANCE에 반영한다. 디자인·패킷 영향은 §6의 무효화 규칙을 따른다. PDS S-03b에 “변경 요청 모드” 화면(버전 비교 DiffView, 새 버전 승인·재동의 버튼, 차단 문구)을 추가한다. |
| **B-08** | PDS §5.3 공통 보조 조치 “(운영자) 주문 취소”, §6.1·§6.5 / BD-002 | **주문 취소의 화면·필드·결과가 정해지지 않았다**(EXP-014 “사유·증빙 필수, 결제 이력 보존”, SRS-163 귀책 구분 필수, SRS-164 “환불 여부는 정책 버전 근거와 함께 기록, 몰수 선택지 없음”, TC-S-010·046). PDS에는 버튼 이름만 있고, 대화상자 구성(사유, C-22 증빙, 귀책 구분, 환불 여부 + 근거 정책 버전, 몰수 선택지 없음), 열린 결제 요청 처리, 환불 역분개 입력(C-03이 말하는 “별도 역분개 화면”은 명세가 없다), CANCEL_REQUEST·REFUND_REVIEW 이슈와의 관계가 없다. | PDS에 “취소 대화상자” 명세를 추가한다(필드, 기본 미체크, 사유·증빙·귀책 필수, 환불 여부 Radio “환불 / 환불 없음(정책 vN §조항)” — 몰수 없음). 환불 역분개 입력 화면(S-03c 안)과, 취소 후 열린 결제 요청 → CANCELLED, 관련 이슈 자동 종료 규칙을 적는다. BD-002 전이표에 `cancel`(활성 단계 → CANCELLED, operator)을 명시한다. |
| **B-09** | BD §6 이슈 소유, PDS §5.2 이슈·§8 | **이슈 상태값과 열기·해결 흐름이 정해지지 않았다.** PRD §8.1은 `issue.status`를 독립 상태로 요구하는데, BD는 결제·CS 상태도는 주면서 이슈 상태값(예: OPEN/RESOLVED)과 해결 주체를 정하지 않았다. PDS의 해결 UI는 PAYMENT_MISMATCH(불일치 해결 패널)와 REWORK 열기뿐이다. 다음이 없다. DELIVERY_DELAY를 누가 여는지(수동인지, 납기 위험 알림에서 자동으로 여는지)와 해결 조건 UI 및 차단 문구(SRS §3-1: “새 예상일 기록 + 고객 지연 안내 발송 기록”). DISPUTED·REFUND_REVIEW·CANCEL_REQUEST의 해결 방법. REWORK 해결(재검수와의 관계). “문제 기록(이슈 열기)” 대화상자 필드(유형·증빙, CANCEL·REWORK·CHANGE의 귀책 구분 — SRS-163). 이슈 배너의 [해결하기]가 어디로 가는지도 정해지지 않았다. | BD §6에 이슈 상태도(OPEN → RESOLVED, 해결 주체·근거 필수)와 유형별 해결 조건 표를 둔다. DELIVERY_DELAY 행은 SRS §3-1 해결 조건을 그대로 옮긴다. PDS에 “이슈 열기 대화상자”와 유형별 해결 패널을 명세하고, 해결 조건 미충족 문구(예: `G-DELAY-NOTICE` “고객에게 지연 안내를 보낸 기록이 없어요.”)를 §5.4에 추가한다. |
| **B-10** | BD-002·BD-003 / PDS C-09, S-03a, S-03 기본 탭, S-01·S-02 | **NO_CONSENT 주문의 단계와 상태별 UI가 정해지지 않았다.** SRS §3-1에서 HOLD는 “모든 진행 전이(시스템 전이 제외)”, NO_CONSENT는 “모든 진행 전이 + AI 분석”이다. 시스템 전이(INQUIRY_RECEIVED→AI_BRIEF_READY→OWNER_REVIEW)가 일어나는지 BD-003이 정하지 않았다. 일어나지 않으면 PDS C-09는 INQUIRY_RECEIVED에서 “AI가 정리하고 있어요” 스피너 뒤에 “수동 검토로 넘어갔어요”를 보여 주는데, 둘 다 사실이 아니다(원칙 P4·SRS-087 위반). 또 EXP-002 큐에서 ‘검토 필요’는 “보류 사유 없음”이 조건이라서, NO_CONSENT 주문은 ‘전체’ 탭에만 나오고 대시보드 “검토할 새 의뢰”에도 세지 않는다. 결국 화면에서 보이지 않게 된다. S-03 기본 탭 규칙에도 INQUIRY_RECEIVED·AI_BRIEF_READY가 없다. | BD-003에 NO_CONSENT와 시스템 전이의 관계를 정한다. 제안: 시스템 전이는 AI 없이 OWNER_REVIEW까지 진행하고, 브리프는 “AI 미실행(동의 없음)”으로 둔다. PDS에서는 NO_CONSENT의 NextActionCard 대표 조치를 “동의 증빙 기록”으로 정하고, 대시보드에 별도 카운트(또는 ‘검토 필요’ 카드에 “동의 없음 n” 병기)를 두며, S-03 기본 탭을 ‘의뢰’로 명시한다. |
| **B-11** | BD-002(🔒 동결) ↔ BD §6, TC-I-005 | **BD 안의 모순: 동결 인터페이스와 상태 전이 절이 충돌한다.** BD-002는 “system 주체는 2개 전이만 가능, 그 외는 `TransitionForbidden`”이라고 하는데, §6은 `system_revalidate`(CUSTOMER_DESIGN_APPROVED→DETAIL_CAD, actor=system)를 추가하고 TC-I-005가 actor=system을 기대한다. BD-002를 문자 그대로 구현하면 TC-I-005가 실패한다. 이 되돌림은 사람(새 디자인 업로드자)이 일으키는데 감사 actor를 system으로 남기면 “누가”가 사라진다(SRS-081). FACTORY_SENT 이후 새 디자인이 올라오는 경우(업로드 차단인지 REWORK 경로인지)도 정해지지 않았다. | BD-002 본문에 예외를 명시한다(허용 system 전이 3개). 또는 actor를 업로드한 사람으로 하고 `revalidate_on_new_design` 액션으로 바꾼다(권장, 감사에 원인 이벤트 링크). FACTORY_SENT 이후 디자인 업로드 규칙을 정하고, PDS §6.3 확인 대화상자 문장과 TC-I-005 기대를 맞춘다. |
| **B-12** | PDS E-15 ↔ BD-004·§4 데이터 | **BD와 PDS의 불일치: 동시 편집 충돌 계약이 없다.** PDS E-15는 “낙관적 잠금 `version`, 덮어쓰기 없음, 내 입력 보존”을 약속하지만, BD-004는 “동시성은 SQLite 잠금 + UNIQUE”뿐이고, 데이터 설계에 version 열이 없으며, 폼이 기대 버전을 싣는 방식, 응답 코드, 카탈로그 문구 코드가 없다. 두 사용자가 같은 주문을 고칠 때(예: 견적 초안, 사양서) 무엇을 거부할지 worker가 정해야 한다. | BD-004에 낙관적 잠금 계약을 넣는다. 편집 대상 엔티티의 `row_version`, 폼 hidden `expected_version`, 불일치 시 409 + 코드 `G-STALE`(PDS §5.4에 문구 추가: E-15 제목/설명), 적용 대상 목록(견적 초안·사양서·확정 사양·설정). 전이 액션은 기존 guard 재검사로 충분하다고 명시한다. |
| **B-13** | PDS C-01·§5.4 ↔ TC-S-005·006·009·010 | **문구·속성이 기존 판정 기대와 다르다(검증가능성).** PDS가 문구의 원본이라고 선언했지만 TC-S 기대값과 다르다. TC-S-006은 수락 버튼 `disabled`를 기대하는데, PDS C-01은 `disabled` 대신 `aria-disabled`를 쓰라고 한다. TC-S-009는 “결제 금액이 맞지 않는 문제가 열려 있어요”를 기대하는데, G-ISSUE-OPEN 문구는 “결제 불일치 문제가 열려 있어요.”다. TC-S-005는 “동시 진행 3/3 — 사유를 적어야 수락할 수 있어요”를 기대하는데, G-CAPACITY 문구는 “동시 진행 3/3건이에요. 사유를 적어야 수락할 수 있어요.”다. TC-S-010은 차단 근거를 “PDS §5.2 이슈 표”로 가리키는데, PDS는 SRS-115가 원본이라고 하고 차단 표를 다시 적지 않는다. 지금 상태로는 PDS대로 구현하면 시험이 실패하고, 시험대로 구현하면 PDS 위반이다. | PDS를 원본으로 확정하고 TC-S 정정을 RTM 변경으로 등록한다. 판정은 문자열 일치 대신 `data-unmet-code="G-…"`와 카탈로그 문구 대조로 바꾸고, `disabled` 기대는 `aria-disabled="true"`로, TC-S-010 근거는 SRS §3-1로 고친다. 또는 PDS 문구를 TC-S에 맞춘다. 어느 쪽이든 한 쪽으로 통일한다. |

---

## 2. 비차단 지적 (이월 가능)

| ID | 위치 | 내용 | 제안 |
|---|---|---|---|
| N-01 | PDS §13.1 ↔ BD-001·PBS-002 | 라벨 사전 파일이 PDS는 `domain/labels.py`, BD는 `domain/states.py`의 `LABEL_KO`다. | 한 곳으로 통일한다(BD 기준 권장). |
| N-02 | PDS §5.3 FACTORY_RECEIVED 행 | 대표 버튼이 “검수 기록”인데 §6.1·SRS §3-1·차단 문구는 “검수 통과”다. 검수표 저장과 통과 전이가 한 버튼인지 두 버튼인지 헷갈린다. | 대표 = “검수 통과”, 저장 = 보조 “검수표 저장”. |
| N-03 | PDS S-03 기본 진입 탭 | INQUIRY_RECEIVED·AI_BRIEF_READY·CUSTOMER_ACCEPTED·INITIAL_PAYMENT_VERIFIED·QC_PASSED·FINAL_PAYMENT_VERIFIED의 기본 탭이 없다. | 18개 단계 전수 표로 만든다. |
| N-04 | PDS S-01 할 일 카드 2, S-02 | “고객 답을 기다리는 주문”이 `queue=needs_info`로 연결되는데, EXP-007의 고객 응답 대기는 QUOTE_APPROVED·PAYMENT_PENDING도 포함한다. 큐 URL 코드(`review`·`needs_info`·`payment`·`making` 외)가 전부 정의되지 않았다. | 큐 코드 표(10개)와 카드 정의를 맞춘다. |
| N-05 | PDS C-26 ↔ §6.10 | 알림 종류는 4개(payment_issue 포함)인데 패널 탭은 “고객 응답 지연/납기 위험/시스템”만 있다. | “결제 문제” 탭을 추가하거나 ‘전체’에만 둔다고 명시한다. |
| N-06 | PDS S-03a 수락 대화상자 | EXP-016 GATE-CAP-01의 “수용량 정책 미확정” 안내(W-GATE-CAP)가 카탈로그에만 있고, 어느 화면에 붙는지는 적혀 있지 않다. | 수락 대화상자 구성에 W-GATE-CAP을 넣는다. |
| N-07 | PDS S-03a | SRS-010(구조, T) “운영자가 주문 상세에서 대체 이미지를 수동 업로드·연결”할 버튼이 의뢰 탭에 없다(FUN-001에는 배정됨). | 참고 이미지 섹션에 “이미지 추가(수동)” 버튼을 둔다. |
| N-08 | PDS §15 | 백엔드 전용 SRS(007~029, 080~083 등 60건)가 추적표에 없다. 화면이 없는 것인지 누락인지 구분되지 않는다. | “UI 없음(BD FUN-xxx)” 행으로 명시한다. |
| N-09 | SRS-152 | “PDS §6”을 접근성 절로 가리키지만 실제는 §9다(SRS-143의 §4.3 오참조는 B-04). | RTM 참조를 정정한다. |
| N-10 | PDS §12.1·S-12·C-22 | 회신수단 선택지가 폼(카카오톡/전화/이메일), 수기 접수(카카오톡/전화/기타), 증빙 채널(카카오톡/전화/방문/기타)로 각각 다르다. | `contact_kind` 코드 사전을 §5.2에 하나로 둔다. |
| N-11 | PDS G-SHIP-INFO ↔ S-03f 섹션 4 | 차단 문구의 필수 항목(수취인·배송 방법·플랫폼 상태)과 화면 필드(연락처·주소·택배사·송장)가 다르다. “직접 전달”일 때 송장이 필수인지 정해지지 않았다. | 배송 방법별 필수 필드 표를 둔다. |
| N-12 | design-tokens.json `pairs` | PDS가 실제로 쓰는 쌍 28개를 추가로 계산했다. 모두 통과했다(예: text-muted/surface-sunken 6.99·9.40, text/warning-soft 15.25·13.41, danger-text/bg 7.42·8.06). focus/primary는 1.00이지만 링이 2px 간격으로 떨어져 바깥 배경과 대비하므로 문제없다. 다만 검증 목록에는 빠져 있다. | 진행 종료 칩, 표 머리, 배너 본문 쌍을 `pairs`에 추가해 CI가 지키게 한다. |
| N-13 | BD §4 | PRD §9.2에 없는 L3 운영자 판단 기록, PC 보안 점검표, 광고비(월·채널)의 저장 위치가 요약에 없다. | DD-001로 이월하되 테이블 이름만 BD에 예약한다. |

---

## 3. 사용자 요청 품질 축

| 축 | 판정 | 근거 한 줄 |
|---|---|---|
| 사용성 | PASS | 대표 조치 1개(P1), 막힌 이유를 문장으로 보여 줌(P2), 단계별 다음 조치 표(§5.3), 아버님 5과제 경로가 화면에 모두 있다(N-02 라벨 혼선은 비차단). |
| 시각적 완성도 | PASS | 토큰(색 2테마·간격·반경·그림자·모션), 타입 스케일 9단, 컴포넌트 30종의 상태·크기·아이콘 의미가 고정돼 있다. |
| 일관성 | **FAIL** | BD↔PDS 불일치: 라우트(B-05), 결제 상태 NOT_APPLICABLE(B-06), 동시 편집(B-12), 라벨 파일 위치(N-01). PDS 안에서도 S-00a와 S-09의 Google 실계정 처리가 다르다(B-02). |
| 접근성(WCAG 2.2 AA) | PASS | 52쌍 + 추가 28쌍 대비 통과, `aria-disabled`+`describedby`, 포커스 트랩·복귀, 2.4.11 `scroll-margin`, 2.5.8 목표 크기, 3.3.7·3.3.8까지 명세했다(참조 번호 오기 N-09뿐). |
| 반응형 | PASS | 5구간 그리드, 요소별 전환 표(§10), 960 필수 폭, 하단 고정 조치 바·시트·`dvh`, 6개 검증 폭이 있다. |
| 예외 상태 | **FAIL** | E-01~34는 촘촘하지만 취소(B-08), 이슈 해결(B-09), NO_CONSENT 단계(B-10), 동시 편집 계약(B-12)이 비어 있다. |
| 개발 구현 가능성 | **FAIL** | HTTP·action 카탈로그·상태 코드 규칙(B-05), 권한 표(B-04), Google 실제 어댑터 계약(B-02)이 없어서 worker가 추측해야 한다. |

## 4. 7기준 판정

| 기준 | 판정 | 근거 |
|---|---|---|
| 완전성 | **FAIL** | FUN 미배정 22건(B-01), SRS-160·167 설계 부재(B-02·B-03), 취소·이슈·변경 요청 흐름 부재(B-07~B-09). |
| 명확성 | **FAIL** | 상태 코드 규칙(B-05e), NO_CONSENT와 시스템 전이(B-10), 잔금 생성 시점(B-06)이 둘 이상으로 읽힌다. |
| 근거성 | PASS | 거의 모든 결정에 SRS·EXP·ASM 근거가 달려 있다. 예외는 S-00a의 “R2 준비 중”으로, 근거 없이 상류와 모순된다(B-02로 처리). |
| 검증가능성 | **FAIL** | TC-I 10건에 Pass 조건은 있지만, PDS 문구·속성이 TC-S 기대와 어긋나서(B-13) 같은 구현이 PDS와 TC-S를 동시에 만족할 수 없다. |
| 추적성 | **FAIL** | §8이 원본이라고 한 FUN 표에 빈칸 22건(B-01), SRS-143이 가리키는 PDS 절이 없음(B-04), PDS §15에서 백엔드 SRS 구분이 없음(N-08). |
| 실행가능성 | **FAIL** | B-04·B-05·B-11이 남아 있는 동안 BD-002·BD-006(🔒 동결 대상)을 동결할 수 없다. |
| 비중복성 | PASS | 차단 표는 SRS §3-1, 라벨·문구는 PDS를 원본으로 하나씩 지정하고 다시 적지 않았다(BD §6 보류 라벨 병기는 경미). |

## 5. 잘된 점 (유지)
- 원칙 2(화면·서버 같은 문구)를 `guards.check()` 단일 함수, `data-unmet-code`, TC-I-006으로 시험 가능한 형태로 만들었다.
- 결제 확인과 라이프사이클 전이를 한 트랜잭션에 묶고 경계 불변식을 두었으며 TC-I-003으로 검증한다.
- PublicQuote를 별도 타입으로 둬서 고객 문서에 내부값이 섞이는 것을 원천 차단했다(SRS-137).
- 사양 차이가 있으면 해소 경로를 두 개만 주는 UI(S-03e 섹션 5)가 SRS-161 r3 보정과 정확히 일치한다.
