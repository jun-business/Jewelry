"""전이 서비스(BD-002 🔒FROZEN, DD-002). 라이프사이클 상태는 이 모듈로만 바뀐다(SRS-080·116)."""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from ..core import clock
from ..core.db import ConflictError, jdump, jload
from ..domain.guards import Forbidden, TransitionBlocked, Unmet, unmet
from ..domain.money import won
from ..domain.states import (ENDED, HOLD_DESC, HOLD_LABEL, ISSUE_LABEL, LABEL, ORDERED, QC_ITEMS, S, SPEC_FIELDS,
                             FACTORY_SPEC_FIELDS)


@dataclass(frozen=True)
class T:
    action: str
    frm: tuple[S, ...]
    to: S | None          # None = 비전이 액션
    actors: frozenset
    label: str


_ACTIVE = tuple(s for s in ORDERED if s not in ENDED)

TRANSITIONS: dict[str, T] = {t.action: t for t in [
    T("ingest_brief", (S.INQUIRY_RECEIVED,), S.AI_BRIEF_READY, frozenset({"system"}), "AI 정리 완료"),
    T("to_review", (S.AI_BRIEF_READY,), S.OWNER_REVIEW, frozenset({"system"}), "수락 검토로"),
    T("accept", (S.OWNER_REVIEW,), S.FREE_CONCEPT, frozenset({"maker"}), "수락"),
    T("decline", (S.OWNER_REVIEW,), S.DECLINED, frozenset({"maker"}), "거절"),
    T("approve_quote", (S.FREE_CONCEPT,), S.QUOTE_APPROVED, frozenset({"maker"}), "견적 승인"),
    # D-24: Form B 계약이 유효해지면 시스템이 기록(확인된 계약 이메일 자동 확정)할 수도 있다
    T("record_acceptance", (S.QUOTE_APPROVED,), S.CUSTOMER_ACCEPTED, frozenset({"maker", "operator", "system"}),
      "고객 동의 기록"),
    T("create_payment_req", (S.CUSTOMER_ACCEPTED,), S.PAYMENT_PENDING, frozenset({"operator"}), "결제 요청 만들기"),
    T("verify_initial", (S.PAYMENT_PENDING,), S.INITIAL_PAYMENT_VERIFIED, frozenset({"operator"}), "첫 결제 확인"),
    T("start_cad", (S.INITIAL_PAYMENT_VERIFIED,), S.DETAIL_CAD, frozenset({"maker"}), "상세 CAD 시작"),
    T("record_design_ok", (S.DETAIL_CAD,), S.CUSTOMER_DESIGN_APPROVED, frozenset({"maker"}), "고객 최종 시안 동의 기록"),
    T("send_factory", (S.CUSTOMER_DESIGN_APPROVED,), S.FACTORY_SENT, frozenset({"maker"}), "공장 발주"),
    T("receive", (S.FACTORY_SENT,), S.FACTORY_RECEIVED, frozenset({"maker"}), "완제품 수령 기록"),
    T("pass_qc", (S.FACTORY_RECEIVED,), S.QC_PASSED, frozenset({"maker"}), "검수 통과"),
    T("verify_final", (S.QC_PASSED,), S.FINAL_PAYMENT_VERIFIED, frozenset({"operator"}), "잔금 확인"),
    T("ship", (S.FINAL_PAYMENT_VERIFIED,), S.SHIPPED, frozenset({"maker", "operator"}), "발송 기록"),
    T("close", (S.SHIPPED,), S.CLOSED, frozenset({"operator"}), "주문 완료 처리"),
    T("cancel", _ACTIVE, S.CANCELLED, frozenset({"operator"}), "주문 취소"),
    T("revalidate_design", (S.CUSTOMER_DESIGN_APPROVED,), S.DETAIL_CAD, frozenset({"maker"}), "새 디자인 재확인"),
    # 비전이 액션(차단 표 대상)
    T("build_packet", (S.CUSTOMER_DESIGN_APPROVED,), None, frozenset({"maker"}), "발주 패킷 만들기"),
]}

PROGRESS = {"accept", "approve_quote", "record_acceptance", "create_payment_req", "verify_initial", "start_cad",
            "record_design_ok", "send_factory", "receive", "pass_qc", "verify_final", "ship", "close", "build_packet",
            "create_balance_req", "recreate_payment_req"}
# DD-002: 명시적 확인 체크(G-CONFIRM)가 필요한 사람 전이(EXP-004)
CONFIRM_ACTIONS = {"accept", "decline", "approve_quote", "record_acceptance", "start_cad", "record_design_ok",
                   "send_factory", "pass_qc", "ship", "close", "cancel"}  # = PDS §6.1 승인 동작(결제 확인은 결제 입력 폼의 확인)
# 비전이 결제 요청 액션은 차단 표에서 “결제 요청 만들기”와 같이 취급(DD-002 대응표)
ALIASES = {"create_balance_req": "create_payment_req", "recreate_payment_req": "create_payment_req"}

# SRS §3-1 차단 표 — 키: 보류 사유 또는 이슈 유형
BLOCKS: dict[str, set[str]] = {
    "NEEDS_INFO": {"approve_quote", "record_acceptance", "create_payment_req", "verify_initial", "start_cad",
                   "send_factory", "build_packet", "create_balance_req", "recreate_payment_req"},
    "WAITLISTED": set(),
    "HOLD": set(PROGRESS),
    "NO_CONSENT": set(PROGRESS),
    "PAYMENT_MISMATCH": {"verify_initial", "start_cad", "send_factory", "build_packet", "verify_final", "ship"},
    "REFUND_REVIEW": {"start_cad", "send_factory", "build_packet", "pass_qc", "verify_final", "ship"},
    "DISPUTED": set(PROGRESS),
    "DELIVERY_DELAY": {"ship", "close"},
    "CHANGE_REQUEST": {"create_payment_req", "verify_initial", "start_cad", "record_design_ok", "send_factory",
                       "build_packet", "create_balance_req", "recreate_payment_req"},
    "CANCEL_REQUEST": {"send_factory", "build_packet", "pass_qc", "ship"},
    "REWORK": {"pass_qc", "ship"},
}

# 퍼널 이벤트(SRS-089)
FUNNEL = {"accept": "accepted", "approve_quote": "quoted", "verify_initial": "paid", "ship": "shipped",
          "close": "closed", "decline": "declined", "cancel": "cancelled"}

# 되돌리기 불가: 시스템 전이·종료 전이·외부 사실이 생긴 전이
NOT_REVERTIBLE = {"ingest_brief", "to_review", "decline", "cancel", "close", "verify_initial", "verify_final",
                  "create_payment_req",
                  "send_factory", "revalidate_design", "record_acceptance", "record_design_ok", "ship"}


def actor_role(actor) -> str:
    return "system" if actor is None else actor.role


# ---------------- 조건 평가 ----------------
def _order(c, order_id):
    o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if o is None:
        raise KeyError(order_id)
    return o


def active_holds(c, order_id) -> list[str]:
    return [r["reason"] for r in c.execute("SELECT reason FROM holds WHERE order_id=? AND cleared_at IS NULL "
                                           "ORDER BY id", (order_id,))]


def open_issues(c, order_id) -> list[str]:
    return [r["type"] for r in c.execute("SELECT type FROM issues WHERE order_id=? AND state='OPEN' ORDER BY id",
                                         (order_id,))]


def latest_quote(c, order_id, status=None):
    if status:
        return c.execute("SELECT * FROM quotes WHERE order_id=? AND status=? ORDER BY version DESC LIMIT 1",
                         (order_id, status)).fetchone()
    return c.execute("SELECT * FROM quotes WHERE order_id=? ORDER BY version DESC LIMIT 1", (order_id,)).fetchone()


def quote_missing_fields(c, q) -> list[str]:
    miss = []
    items = c.execute("SELECT * FROM quote_items WHERE quote_id=?", (q["id"],)).fetchall()
    if not items or sum(i["amount_krw"] for i in items) <= 0:
        miss.append("품목")
    if not q["promised_date"]:
        miss.append("약속 납기")
    if not q["valid_until"]:
        miss.append("유효기간")
    if q["tax_included"] is None:
        miss.append("세금 포함 여부")
    if q["shipping_included"] is None:
        miss.append("배송비 포함 여부")
    if not c.execute("SELECT 1 FROM policies WHERE kind='terms' AND status='APPROVED'").fetchone():
        miss.append("적용 정책 버전(승인된 수정·환불 정책)")
    spec = jload(q["spec_json"], {})
    if not any((spec.get(k) or "").strip() for k, _ in SPEC_FIELDS) and not (q["spec_summary"] or "").strip():
        miss.append("제작 사양")
    return miss


def paid_sum(c, request_id) -> int:
    r = c.execute("SELECT COALESCE(SUM(CASE WHEN event_type='PAID' THEN amount_krw "
                  "WHEN event_type='REFUND' THEN -amount_krw ELSE 0 END),0) FROM payment_events WHERE request_id=?",
                  (request_id,)).fetchone()
    return r[0]


def payment_request(c, order_id, phases):
    marks = ",".join("?" * len(phases))
    return c.execute(f"SELECT * FROM payment_requests WHERE order_id=? AND phase IN ({marks}) AND status<>'CANCELLED' "
                     "ORDER BY id DESC LIMIT 1", (order_id, *phases)).fetchone()


def latest_design(c, order_id):
    return c.execute("SELECT * FROM design_versions WHERE order_id=? ORDER BY version DESC LIMIT 1",
                     (order_id,)).fetchone()


def design_acceptance(c, order_id, design_version_id):
    return c.execute("SELECT * FROM customer_acceptances WHERE order_id=? AND kind='design' AND design_version_id=? "
                     "ORDER BY id DESC LIMIT 1", (order_id, design_version_id)).fetchone()


def latest_factory_spec(c, order_id):
    return c.execute("SELECT * FROM factory_specs WHERE order_id=? ORDER BY revision DESC LIMIT 1",
                     (order_id,)).fetchone()


def spec_missing(spec: dict) -> list[str]:
    miss = []
    for k, lab in FACTORY_SPEC_FIELDS:
        v = spec.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            miss.append(lab)
    if not spec.get("stl_asset_id"):
        miss.append("연결 파일(STL)")
    return miss


def spec_diff(quote_spec: dict, factory_spec: dict) -> list[str]:
    diff = []
    for k, lab in SPEC_FIELDS:
        a = (quote_spec.get(k) or "").strip()
        b = (factory_spec.get(k) or "").strip()
        if a and a != b:
            diff.append(lab)
    return diff


def valid_packet(c, order_id):
    return c.execute("SELECT * FROM factory_packets WHERE order_id=? AND status='VALID' ORDER BY id DESC LIMIT 1",
                     (order_id,)).fetchone()


def _meta(c, key):
    r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def contract_exempt(c, order_id) -> bool:
    """D-24 이전에 고객 동의가 기록된 주문은 Form B 계약·발주 전 잔금 조건을 소급 적용하지 않는다."""
    since = _meta(c, "contract_gate_since")
    return bool(since and c.execute("SELECT 1 FROM customer_acceptances WHERE order_id=? AND kind='quote' AND "
                                    "recorded_at<?", (order_id, since)).fetchone())


def contract_ok(c, order_id) -> bool:
    """현재 확정 견적 버전에 유효한(ACCEPTED) 계약 동의가 있는가(D-24)."""
    q = latest_quote(c, order_id, "APPROVED")
    if q is None:
        return False
    if c.execute("SELECT 1 FROM contract_requests WHERE order_id=? AND quote_id=? AND status='ACCEPTED'",
                 (order_id, q["id"])).fetchone():
        return True
    return contract_exempt(c, order_id)


def deposit_verified(c, order_id) -> bool:
    r = payment_request(c, order_id, ("DEPOSIT", "FULL"))
    return r is not None and r["status"] == "PAID_VERIFIED" and paid_sum(c, r["id"]) == r["expected_krw"]


def balance_paid(c, o) -> bool:
    """공장 발주 전 잔금 2/3 실제 결제 확인(D-24 Gate 2). 전액 정책·소급 제외 주문은 해당 없음."""
    if o["payment_policy"] == "FULL" or contract_exempt(c, o["id"]):
        return True
    r = payment_request(c, o["id"], ("BALANCE",))
    return r is not None and paid_sum(c, r["id"]) == r["expected_krw"]


def _check_specific(ctx, c, o, action, payload, submitting) -> list[Unmet]:
    from ..services import capacity, settings  # 지연 import
    p = payload or {}
    out: list[Unmet] = []
    oid = o["id"]
    if action == "accept":
        if capacity.is_holiday_today(ctx):
            out.append(unmet("G-HOLIDAY"))
        n, lim = capacity.active_count(ctx), capacity.limit(ctx)
        if n >= lim:  # H1 r1 Q2: 운영자 예외 승인 + 아버님 제작 가능 확인
            exc = c.execute("SELECT id FROM capacity_exceptions WHERE order_id=? AND maker_confirmed_at IS NULL",
                            (oid,)).fetchone()
            if exc is None:
                out.append(unmet("G-CAPACITY", n=n, limit=lim))
            elif submitting and p.get("feasible_confirm") != "on":
                out.append(unmet("G-CAPACITY-FEASIBLE"))
    elif action == "decline":
        if submitting and not p.get("category"):
            out.append(unmet("G-DECLINE-CATEGORY"))
        if submitting and not (p.get("reason") or "").strip():
            out.append(unmet("G-REASON"))
    elif action == "approve_quote":
        if not c.execute("SELECT 1 FROM concepts WHERE order_id=?", (oid,)).fetchone():
            out.append(unmet("G-NO-CONCEPT"))
        q = latest_quote(c, oid, "DRAFT")
        if q is None:
            out.append(unmet("G-QUOTE-MISSING"))
        else:
            miss = quote_missing_fields(c, q)
            if miss:
                out.append(unmet("G-QUOTE-FIELDS", fields="·".join(miss)))
            s = c.execute("SELECT COALESCE(SUM(amount_krw),0) FROM quote_items WHERE quote_id=?", (q["id"],)).fetchone()[0]
            if s != q["total_krw"]:
                out.append(unmet("G-QUOTE-TOTAL", sum=won(s), total=won(q["total_krw"])))
    elif action == "record_acceptance":
        if not contract_ok(c, oid):  # D-24: 고객 동의 = 유효한 Form B 계약 동의
            out.append(unmet("G-CONTRACT-REQUIRED"))
        if submitting and not ((p.get("channel") or "").strip() and (p.get("evidence") or "").strip()):
            out.append(unmet("G-ACCEPT-EVIDENCE"))
    elif action == "create_payment_req":
        q = latest_quote(c, oid, "APPROVED")
        if q is None:
            out.append(unmet("G-QUOTE-MISSING"))
        elif q["is_test"] and not settings.test_mode(ctx):
            out.append(unmet("G-TEST-QUOTE"))
        if q is not None and not contract_ok(c, oid):  # 계약 동의 없이 예약금 청구 금지(D-24)
            out.append(unmet("G-CONTRACT-REQUIRED"))
    elif action == "start_cad":  # D-24 Gate 1: 유효한 계약 동의 + 예약금(또는 전액) 실제 결제 확인
        if not contract_ok(c, oid):
            out.append(unmet("G-CONTRACT-REQUIRED"))
        if not deposit_verified(c, oid):
            out.append(unmet("G-PAYMENT-UNVERIFIED"))
    elif action in ("verify_initial", "verify_final"):
        if action == "verify_initial" and not contract_ok(c, oid):
            out.append(unmet("G-CONTRACT-REQUIRED"))
        phases = ("DEPOSIT", "FULL") if action == "verify_initial" else ("BALANCE",)
        if action == "verify_final" and o["payment_policy"] == "FULL":
            pass  # 전액 정책: 첫 결제로 조건 충족(SRS-114)
        else:
            r = payment_request(c, oid, phases)
            if r is None:
                out.append(unmet("G-PAYMENT-REQUEST"))
            elif paid_sum(c, r["id"]) != r["expected_krw"] and not payload_skip_amount(p):
                out.append(unmet("G-PAYMENT-UNVERIFIED" if action == "verify_initial" else "G-BALANCE"))
    elif action == "record_design_ok":
        d = latest_design(c, oid)
        if d is None or not (d["stl_asset_id"] or d["cad_asset_id"]):
            out.append(unmet("G-DESIGN-MISSING"))
        if submitting and not ((p.get("channel") or "").strip() and (p.get("evidence") or "").strip()):
            out.append(unmet("G-ACCEPT-EVIDENCE"))
    elif action in ("build_packet", "send_factory"):
        d = latest_design(c, oid)
        if not balance_paid(c, o):  # D-24 Gate 2: 잔금 2/3 실제 결제 확인 후 발주
            out.append(unmet("G-BALANCE-BEFORE-FACTORY"))
        if action == "build_packet":
            if d is None or not design_acceptance(c, oid, d["id"]):
                out.append(unmet("G-DESIGN-UNAPPROVED"))
            fs = latest_factory_spec(c, oid)
            spec = jload(fs["spec_json"], {}) if fs else {}
            miss = spec_missing(spec)
            if miss:
                out.append(unmet("G-SPEC-INCOMPLETE", fields="·".join(miss)))
            q = latest_quote(c, oid, "APPROVED")
            if q is not None and fs is not None:
                diff = spec_diff(jload(q["spec_json"], {}), spec)
                if diff:
                    out.append(unmet("G-SPEC-DIFF", fields="·".join(diff)))
        else:
            pk = valid_packet(c, oid)
            if pk is None:
                out.append(unmet("G-PACKET-MISSING"))
            else:
                if d is None or pk["design_version_id"] != d["id"]:
                    out.append(unmet("G-PACKET-STALE"))
                q = latest_quote(c, oid, "APPROVED")
                fs = c.execute("SELECT spec_json FROM factory_specs WHERE id=?", (pk["spec_id"],)).fetchone()
                if q is not None and fs is not None:
                    diff = spec_diff(jload(q["spec_json"], {}), jload(fs["spec_json"], {}))
                    if diff:  # 재견적으로 사양이 바뀐 뒤 옛 패킷 발주 방지(검토 N-05)
                        out.append(unmet("G-SPEC-DIFF", fields="·".join(diff)))
                from ..services import manufacturing
                if not manufacturing.packet_hash_ok(ctx, c, pk):
                    out.append(unmet("G-PACKET-HASH"))
    elif action == "receive":
        if submitting and not (p.get("received_at") and p.get("actual_cost") not in (None, "")):
            out.append(unmet("G-RECEIVE-INFO"))
    elif action == "pass_qc":
        qc = c.execute("SELECT * FROM quality_checks WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
        if qc is None or qc["result"] == "INCOMPLETE":
            out.append(unmet("G-QC-INCOMPLETE"))
        elif qc["result"] == "FAIL":
            out.append(unmet("G-QC-FAIL"))
    elif action == "ship":  # D-24 Gate 3: 검수 통과 기록이 있어야 발송
        qc = c.execute("SELECT result FROM quality_checks WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
        if qc is None or qc["result"] != "PASS":
            out.append(unmet("G-QC-INCOMPLETE" if qc is None or qc["result"] == "INCOMPLETE" else "G-QC-FAIL"))
        if submitting and not (p.get("recipient") and p.get("method") and p.get("platform_status")):
            out.append(unmet("G-SHIP-INFO"))
    elif action == "close":
        if open_issues(c, oid) or (submitting and not (p.get("evidence") or "").strip()):
            out.append(unmet("G-DELIVERY"))
    elif action == "cancel":
        if submitting and not (p.get("reason") or "").strip():
            out.append(unmet("G-REASON"))
    if submitting and action in CONFIRM_ACTIONS and not p.get("confirm") and not p.get("_system_confirm"):
        out.append(unmet("G-CONFIRM"))
    return out


def payload_skip_amount(p) -> bool:
    return False


def check(ctx, order_id, action: str, actor, payload=None, submitting=False, c=None) -> list[Unmet]:
    c = c or ctx.db.conn
    t = TRANSITIONS[action]
    o = _order(c, order_id)
    state = S(o["lifecycle_state"])
    if state in ENDED:
        return [unmet("G-ENDED")]
    if state not in t.frm:
        if action == "build_packet" and state == S.DETAIL_CAD:  # 이 단계에서 막히는 실제 이유를 말한다(PDS §5.4)
            return [unmet("G-DESIGN-UNAPPROVED")]
        return [unmet("G-WRONG-STATE")]
    role = actor_role(actor)
    if role not in t.actors:
        if t.actors == {"system"}:
            return [unmet("G-SYSTEM-ONLY")]
        return [unmet("G-ROLE-MAKER" if "maker" in t.actors else "G-ROLE-OPERATOR")]
    out: list[Unmet] = []
    out.extend(block_unmet(c, order_id, action))
    out.extend(_check_specific(ctx, c, o, action, payload, submitting))
    return out


def block_unmet(c, order_id, action) -> list[Unmet]:
    """SRS §3-1 차단 표 적용(비전이 액션 포함). 보류 코드 = G-HOLD-{reason}(PDS §5.4)."""
    out: list[Unmet] = []
    for h in active_holds(c, order_id):
        if action in BLOCKS.get(h, set()):
            if h == "NO_CONSENT":
                out.append(unmet("G-NO-CONSENT"))
            else:
                u = unmet("G-HOLD", hold_label=HOLD_LABEL[h], hold_desc=HOLD_DESC[h])
                out.append(Unmet(f"G-HOLD-{h}", u.message, u.fix))
    seen = set()
    for i in open_issues(c, order_id):
        if action in BLOCKS.get(i, set()) and i not in seen:
            seen.add(i)
            out.append(unmet("G-ISSUE-OPEN", issue_label=ISSUE_LABEL[i]))
    if action in PROGRESS - {"close"} and fully_refunded(c, order_id):
        out.append(unmet("G-REFUNDED"))  # 환급 완료로 검토 문제가 닫혀도 제작이 다시 열리지 않게(S-32)
    return out


def fully_refunded(c, order_id) -> bool:
    """받은 결제를 환급 완료로 모두 돌려준 주문인가(일부 환불은 해당 없음)."""
    refunded = c.execute("SELECT COALESCE(SUM(refund_completed_amount),0) FROM refund_requests WHERE order_id=? "
                         "AND status='COMPLETED'", (order_id,)).fetchone()[0]
    if refunded <= 0:
        return False
    paid = c.execute("SELECT COALESCE(SUM(e.amount_krw),0) FROM payment_events e JOIN payment_requests r ON r.id=e.request_id "
                     "WHERE r.order_id=? AND e.event_type='PAID'", (order_id,)).fetchone()[0]
    return refunded >= paid


# ---------------- 실행 ----------------
def perform(ctx, order_id, action: str, actor, payload=None, expected_version=None, effect=None):
    """한 트랜잭션: 조건 → 상태 변경 → 부수효과(effect) → 감사 → 퍼널. 실패 시 전체 롤백."""
    payload = payload or {}
    t = TRANSITIONS[action]
    try:
        return _perform(ctx, order_id, action, actor, payload, expected_version, effect, t)
    except TransitionBlocked as e:
        if any(u.code in ("G-WRONG-STATE", "G-SYSTEM-ONLY", "G-ENDED") or u.code.startswith("G-ROLE") for u in e.unmet):
            _audit_rejected(ctx, order_id, action, actor, e.unmet)  # SRS-080: 표 밖 요청은 오류 + 감사
        raise


def _audit_rejected(ctx, order_id, action, actor, unmet_list):
    from ..services import audit
    with ctx.db.tx() as c:
        no = c.execute("SELECT order_no FROM orders WHERE id=?", (order_id,)).fetchone()[0]
        audit.record(c, "order", no, actor, "transition_rejected", order_id=order_id,
                     detail={"requested": action, "codes": [u.code for u in unmet_list]})


def _perform(ctx, order_id, action, actor, payload, expected_version, effect, t):
    with ctx.db.tx() as c:
        o = _order(c, order_id)
        if expected_version not in (None, "") and int(expected_version) != o["version"]:
            raise ConflictError(o["version"])
        problems = check(ctx, order_id, action, actor, payload, submitting=True, c=c)
        if problems:
            raise TransitionBlocked(problems)
        prev = o["lifecycle_state"]
        if t.to is not None:
            token = secrets.token_hex(8)
            c.execute("INSERT INTO transition_ctx(token) VALUES (?)", (token,))
            c.execute("UPDATE orders SET lifecycle_state=?, version=version+1, state_entered_at=?, updated_at=? "
                      "WHERE id=?", (t.to.value, clock.now_iso(), clock.now_iso(), order_id))
            c.execute("DELETE FROM transition_ctx WHERE token=?", (token,))
        else:
            c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), order_id))
        if effect:
            effect(c, o)
        from ..services import audit
        audit.record(c, "order", o["order_no"], actor, action, order_id=order_id, prev=prev,
                     next=t.to.value if t.to else prev, reason=(payload.get("reason") or None),
                     ref_version=payload.get("ref_version"), detail={k: v for k, v in payload.items()
                                                                     if k in ("category", "capacity_override",
                                                                              "channel", "is_test")})
        if action in FUNNEL:
            c.execute("INSERT OR IGNORE INTO funnel_events(order_id, kind, amount_krw, is_test, at) VALUES (?,?,?,?,?)",
                      (order_id, FUNNEL[action], payload.get("amount"), o["is_test"] or int(bool(payload.get("is_test"))),
                       clock.now_iso()))
        if t.to in ENDED:
            c.execute("UPDATE orders SET closed_at=? WHERE id=?", (clock.now_iso(), order_id))
        return c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def last_human_transition(c, order_id):
    return c.execute("SELECT * FROM audit_events WHERE order_id=? AND object_type='order' AND actor_role<>'system' "
                     "AND prev IS NOT NULL AND next IS NOT NULL AND prev<>next ORDER BY id DESC LIMIT 1",
                     (order_id,)).fetchone()


def revert_check(ctx, order_id, actor, c=None) -> tuple[list[Unmet], object]:
    c = c or ctx.db.conn
    o = _order(c, order_id)
    last = c.execute("SELECT * FROM audit_events WHERE order_id=? AND object_type='order' AND prev IS NOT NULL "
                     "AND next IS NOT NULL AND prev<>next ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
    if last is None or last["actor_role"] == "system" or last["action"] in NOT_REVERTIBLE \
            or last["next"] != o["lifecycle_state"]:
        return [unmet("G-REVERT")], last
    t = TRANSITIONS.get(last["action"])
    if t is None or actor_role(actor) not in t.actors:
        return [unmet("G-ROLE-MAKER" if t and "maker" in t.actors else "G-ROLE-OPERATOR")], last
    since = last["created_at"]
    later = any(c.execute(q, (order_id, since)).fetchone() for q in (
        "SELECT 1 FROM payment_requests WHERE order_id=? AND created_at>?",
        "SELECT 1 FROM factory_packets WHERE order_id=? AND created_at>?",
        "SELECT 1 FROM customer_acceptances WHERE order_id=? AND recorded_at>?",
        "SELECT 1 FROM quality_checks WHERE order_id=? AND inspected_at>?",
    ))
    if later:
        return [unmet("G-REVERT")], last
    return [], last


def revert_last(ctx, order_id, actor, reason: str):
    if not (reason or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    with ctx.db.tx() as c:
        problems, last = revert_check(ctx, order_id, actor, c)
        if problems:
            raise TransitionBlocked(problems)
        token = secrets.token_hex(8)
        c.execute("INSERT INTO transition_ctx(token) VALUES (?)", (token,))
        c.execute("UPDATE orders SET lifecycle_state=?, version=version+1, state_entered_at=?, updated_at=?, "
                  "accepted_at=CASE WHEN ?='accept' THEN NULL ELSE accepted_at END WHERE id=?",
                  (last["prev"], clock.now_iso(), clock.now_iso(), last["action"], order_id))
        c.execute("DELETE FROM transition_ctx WHERE token=?", (token,))
        if last["action"] == "approve_quote":
            q = latest_quote(c, order_id, "APPROVED")
            if q is not None:  # 승인 견적은 불변 — 대체 처리하고 같은 내용의 새 초안을 만든다
                from ..services import contracts, quotes
                if latest_quote(c, order_id, "DRAFT") is None:  # 이미 고치던 초안이 있으면 그걸 쓴다(S-24)
                    quotes.clone_as_draft(c, q, actor)
                c.execute("UPDATE quotes SET status='SUPERSEDED' WHERE id=?", (q["id"],))
                contracts.supersede_for_quote(c, order_id, None, actor, "견적 승인 되돌림")
        o = _order(c, order_id)
        from ..services import audit
        audit.record(c, "order", o["order_no"], actor, "revert", order_id=order_id, prev=last["next"],
                     next=last["prev"], reason=reason, detail={"reverted_action": last["action"]})
        c.execute("DELETE FROM funnel_events WHERE order_id=? AND kind=?", (order_id, FUNNEL.get(last["action"], "-")))
        return o


def system_perform(ctx, order_id, action, effect=None):
    """시스템 전이(동기화·AI). 사람 전이를 호출하면 Forbidden(SRS-116)."""
    if TRANSITIONS[action].actors != {"system"}:
        raise Forbidden(action)
    return perform(ctx, order_id, action, None, {}, effect=effect)
