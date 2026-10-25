"""주문·보류·이슈·큐·다음 조치(FUN-003, EXP-002·012·013, PDS §5.3)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core import clock
from ..core.db import jdump, jload
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, Unmet, unmet
from ..domain.states import (FAULT_LABEL, ENDED, HOLD_LABEL, ISSUE_LABEL, LABEL, PHASE_LABEL, PHASE_OF, PHASES5, Phase,
                             QUEUE_STATES, QUEUES, S)
from . import change_orders, audit, capacity, settings


# ---------------- 생성 ----------------
def next_order_no(c) -> str:
    year = clock.kst(clock.now()).year
    n = c.execute("SELECT COUNT(*) FROM orders WHERE order_no LIKE ?", (f"JOS-{year}-%",)).fetchone()[0] + 1
    return f"JOS-{year}-{n:04d}"


def create_order(ctx, c, *, customer_id: int, source: str, intake_id=None, product_type=None, summary=None,
                 source_channel=None, is_test=False, actor=None) -> int:
    now = clock.now_iso()
    no = next_order_no(c)
    cur = c.execute(
        "INSERT INTO orders(order_no, customer_id, intake_id, lifecycle_state, source, source_channel, product_type, "
        "summary, is_test, payment_policy, state_entered_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (no, customer_id, intake_id, S.INQUIRY_RECEIVED.value, source, source_channel, product_type, summary,
         int(is_test), settings.get(ctx, "payment_policy"), now, now, now))
    oid = cur.lastrowid
    audit.record(c, "order", no, actor, "order_created", order_id=oid, next=S.INQUIRY_RECEIVED.value,
                 detail={"source": source})
    c.execute("INSERT OR IGNORE INTO funnel_events(order_id, kind, is_test, at) VALUES (?,?,?,?)",
              (oid, "inquiry", int(is_test), now))
    if capacity.at_capacity(ctx):  # EXP-005: 상한 도달 중 새 문의 → 대기 표시
        set_hold(c, oid, "WAITLISTED", None, "동시 진행 상한 도달 중 접수")
    return oid


def create_customer(ctx, c, display_name, contact_kind, contact_value, consent_version) -> int:
    cur = c.execute("INSERT INTO customers(display_name, contact_kind, contact_enc, consent_version, created_at) "
                    "VALUES (?,?,?,?,?)", (display_name, contact_kind, ctx.enc(contact_value), consent_version,
                                          clock.now_iso()))
    return cur.lastrowid


# ---------------- 보류·이슈 ----------------
def set_hold(c, order_id, reason, actor, note=None) -> bool:
    if c.execute("SELECT 1 FROM holds WHERE order_id=? AND reason=? AND cleared_at IS NULL", (order_id, reason)).fetchone():
        return False
    c.execute("INSERT INTO holds(order_id, reason, note, set_by, set_at) VALUES (?,?,?,?,?)",
              (order_id, reason, note, getattr(actor, "id", None), clock.now_iso()))
    no = c.execute("SELECT order_no FROM orders WHERE id=?", (order_id,)).fetchone()[0]
    audit.record(c, "order", no, actor, "hold_set", order_id=order_id, next=reason, reason=note)
    return True


def clear_hold(c, order_id, reason, actor, note=None) -> bool:
    n = c.execute("UPDATE holds SET cleared_by=?, cleared_at=? WHERE order_id=? AND reason=? AND cleared_at IS NULL",
                  (getattr(actor, "id", None), clock.now_iso(), order_id, reason)).rowcount
    if n:
        no = c.execute("SELECT order_no FROM orders WHERE id=?", (order_id,)).fetchone()[0]
        audit.record(c, "order", no, actor, "hold_cleared", order_id=order_id, prev=reason, reason=note)
    return bool(n)


def open_issue(c, order_id, type_, actor, note=None, detail=None, fault=None) -> int:
    ex = c.execute("SELECT id FROM issues WHERE order_id=? AND type=? AND state='OPEN'", (order_id, type_)).fetchone()
    if ex:
        return ex["id"]
    cur = c.execute("INSERT INTO issues(order_id, type, fault, detail_json, note, opened_by, opened_at) "
                    "VALUES (?,?,?,?,?,?,?)", (order_id, type_, fault, jdump(detail or {}), note,
                                              getattr(actor, "id", None), clock.now_iso()))
    no = c.execute("SELECT order_no FROM orders WHERE id=?", (order_id,)).fetchone()[0]
    audit.record(c, "issue", cur.lastrowid, actor, "issue_opened", order_id=order_id, next=type_, reason=note,
                 detail={"fault": fault})
    return cur.lastrowid


OPERATOR_ISSUES = {"PAYMENT_MISMATCH", "REFUND_REVIEW", "DISPUTED", "CANCEL_REQUEST"}
FAULT_REQUIRED = {"REWORK", "CHANGE_REQUEST", "CANCEL_REQUEST"}
MANUAL_OPEN = {"maker": {"CHANGE_REQUEST", "REWORK", "DELIVERY_DELAY", "CANCEL_REQUEST"},
               "operator": {"CHANGE_REQUEST", "REWORK", "DELIVERY_DELAY", "CANCEL_REQUEST", "DISPUTED"}}
CHANGE_OPEN_STATES = {S.FREE_CONCEPT, S.QUOTE_APPROVED, S.CUSTOMER_ACCEPTED, S.PAYMENT_PENDING,
                      S.INITIAL_PAYMENT_VERIFIED, S.DETAIL_CAD, S.CUSTOMER_DESIGN_APPROVED}


def open_issue_action(ctx, o, actor, type_, note, fault=None) -> int:
    """사람이 여는 이슈(BD §6.3, BD-008)."""
    if actor is None or type_ not in MANUAL_OPEN.get(actor.role, set()):
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR" if type_ == "DISPUTED" else "G-ROLE-MAKER")])
    st = S(o["lifecycle_state"])
    if st in ENDED:
        raise TransitionBlocked([unmet("G-ENDED")])
    um = []
    if type_ == "CHANGE_REQUEST" and st not in CHANGE_OPEN_STATES:
        um.append(unmet("G-CHANGE-AFTER-FACTORY"))
    if not (note or "").strip():
        um.append(unmet("G-REASON"))
    if type_ in FAULT_REQUIRED and fault not in FAULT_LABEL:
        um.append(unmet("G-FAULT"))
    if um:
        raise TransitionBlocked(um)
    with ctx.db.tx() as c:
        iid = open_issue(c, o["id"], type_, actor, note, fault=fault)
        if type_ == "REWORK" and fault == "CUSTOMER_CHANGE" and not change_orders.for_issue(c, iid):
            change_orders.create(c, iid, o["id"])  # 고객 변심 재작업 = 변경 주문 절차(H1 r1 Q10)
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
        return iid


def resolve_issue(ctx, issue_id, actor, resolution: str, outcome: str | None = None, new_eta: str | None = None,
                  notice: str | None = None) -> dict:
    """이슈 해결(BD §6.3). 반환 {open_cancel: bool}."""
    i = ctx.db.one("SELECT * FROM issues WHERE id=?", (issue_id,))
    if i is None or i["state"] != "OPEN":
        raise ValueError("열린 문제가 아니에요")
    if actor is None or (i["type"] in OPERATOR_ISSUES and actor.role != "operator"):
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    if i["type"] == "CHANGE_REQUEST" and actor.role != "operator":
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])  # 수동 해결(변경 철회)은 operator, 보통은 재동의로 자동
    miss = []
    if not (resolution or "").strip():
        miss.append("해결 내용")
    if i["type"] == "DELIVERY_DELAY":
        if not new_eta:
            miss.append("새 예상일")
        if not (notice or "").strip():
            miss.append("고객 지연 안내 기록")
    if i["type"] in ("REFUND_REVIEW", "CANCEL_REQUEST") and outcome not in ("continue", "cancel"):
        miss.append("결과")
    if miss:
        raise TransitionBlocked([unmet("G-ISSUE-RESOLVE", fields="·".join(miss))])
    co = change_orders.for_issue(ctx.db.conn, issue_id)
    if co is not None:
        left = change_orders.missing(ctx.db.conn, co)
        if left:
            raise TransitionBlocked([unmet("G-CHANGE-ORDER", fields="·".join(left))])
    with ctx.db.tx() as c:
        detail = jload(i["detail_json"], {})
        if new_eta:
            detail["new_eta"] = new_eta
            c.execute("UPDATE orders SET internal_eta=? WHERE id=?", (new_eta, i["order_id"]))
        if notice:
            detail["notice"] = notice
        c.execute("UPDATE issues SET state='RESOLVED', resolved_by=?, resolved_at=?, resolution=?, detail_json=? "
                  "WHERE id=?", (actor.id, clock.now_iso(), resolution, jdump(detail), issue_id))
        audit.record(c, "issue", issue_id, actor, "issue_resolved", order_id=i["order_id"], prev=i["type"],
                     reason=resolution, detail={"outcome": outcome})
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), i["order_id"]))
    from . import payments  # 차단이 풀렸으면 결제 확인을 이어서 시도(DD-002 B-04)
    payments.try_verify(ctx, i["order_id"], actor)
    return {"open_cancel": outcome == "cancel"}


REFUND_DECISIONS = {"FULL": "전액 환불", "PARTIAL": "일부 환불", "NONE_PAID": "환불할 결제 없음"}


def cancel_order(ctx, o, actor, payload):
    """주문 취소(BD §6.7, SRS-163·164). 몰수 선택지 없음."""
    from . import payments
    um = []
    if payload.get("fault") not in FAULT_LABEL:
        um.append(unmet("G-FAULT"))
    dec = payload.get("refund_decision")
    paid = 0
    with ctx.db.tx() as c:
        for r in c.execute("SELECT id FROM payment_requests WHERE order_id=?", (o["id"],)).fetchall():
            paid += tr.paid_sum(c, r["id"])
    if dec not in REFUND_DECISIONS or (dec == "NONE_PAID" and paid > 0):
        um.append(unmet("G-ISSUE-RESOLVE", fields="환불 결정"))
    if dec == "PARTIAL" and not ((payload.get("refund_amount") or "").strip() and (payload.get("refund_basis") or "").strip()):
        um.append(unmet("G-ISSUE-RESOLVE", fields="일부 환불 금액·정책 근거"))
    if not (payload.get("evidence") or "").strip():
        um.append(unmet("G-ISSUE-RESOLVE", fields="증빙"))
    if um:
        raise TransitionBlocked(um)

    def effect(c, cur):
        c.execute("UPDATE payment_requests SET status='CANCELLED' WHERE order_id=? AND status IN "
                  "('DRAFT','LINK_READY','MISMATCH')", (o["id"],))
        c.execute("UPDATE issues SET state='RESOLVED', resolved_by=?, resolved_at=?, resolution='주문 취소로 종료' "
                  "WHERE order_id=? AND state='OPEN'", (actor.id, clock.now_iso(), o["id"]))
        c.execute("UPDATE factory_packets SET status='VOID', void_reason='주문 취소' WHERE order_id=? AND status='VALID'",
                  (o["id"],))
        q = tr.latest_quote(c, o["id"], "APPROVED")
        audit.record(c, "order", o["order_no"], actor, "cancel_detail", order_id=o["id"],
                     detail={"fault": payload["fault"], "refund_decision": dec, "refund_amount": payload.get("refund_amount"),
                             "refund_basis": payload.get("refund_basis"),
                             "terms_version": q["terms_version"] if q else None, "paid_total": paid})
    payload = dict(payload, reason=payload.get("reason"))
    return tr.perform(ctx, o["id"], "cancel", actor, payload, expected_version=payload.get("version"), effect=effect)


# ---------------- 조회 ----------------
def approve_capacity_exception(ctx, o, actor, reason: str, expected_eta: str) -> int:
    """동시 진행 상한 초과 예외 승인(H1 r1 Q2, SRS-134 r2) — 운영자. 수락은 아버님이 제작 가능 확인과 함께 한다."""
    from datetime import date
    if actor is None or actor.role != "operator":
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    if S(o["lifecycle_state"]) != S.OWNER_REVIEW:
        raise TransitionBlocked([unmet("G-WRONG-STATE")])
    n, lim = capacity.active_count(ctx), capacity.limit(ctx)
    if n < lim:
        raise ValueError("동시 진행 상한에 닿지 않아 예외 승인이 필요 없어요.")
    um = []
    if not (reason or "").strip():
        um.append(unmet("G-REASON"))
    try:
        date.fromisoformat(expected_eta or "")
    except ValueError:
        um.append(unmet("G-ETA"))
    if um:
        raise TransitionBlocked(um)
    with ctx.db.tx() as c:
        xid = c.execute("INSERT INTO capacity_exceptions(order_id, approved_by, reason, expected_eta, active_count, "
                        "limit_count, approved_at) VALUES (?,?,?,?,?,?,?)",
                        (o["id"], actor.id, reason.strip(), expected_eta, n, lim, clock.now_iso())).lastrowid
        audit.record(c, "order", o["order_no"], actor, "capacity_exception_approved", order_id=o["id"],
                     reason=reason.strip(), detail={"expected_eta": expected_eta, "active": n, "limit": lim})
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
        return xid


def pending_capacity_exception(ctx, order_id):
    return ctx.db.one("SELECT x.*, u.display_name AS approver FROM capacity_exceptions x LEFT JOIN users u "
                      "ON u.id=x.approved_by WHERE x.order_id=? AND x.maker_confirmed_at IS NULL ORDER BY x.id DESC",
                      (order_id,))


def accept_order(ctx, o, actor, payload):
    """수락. 상한 초과면 운영자 예외 승인 + 아버님 제작 가능 확인이 필요하고, 승인자·사유·예상 납기를 남긴다."""
    exc = pending_capacity_exception(ctx, o["id"])
    over = capacity.at_capacity(ctx)
    p = dict(payload)
    if over and exc:
        p["reason"] = f"상한 초과 예외 — {exc['reason']}"

    def effect(c, cur):
        c.execute("UPDATE orders SET accepted_at=? WHERE id=?", (clock.now_iso(), o["id"]))
        clear_hold(c, o["id"], "WAITLISTED", actor, "수락으로 해제")
        if over and exc:
            c.execute("UPDATE capacity_exceptions SET maker_confirmed_by=?, maker_confirmed_at=? WHERE id=?",
                      (actor.id, clock.now_iso(), exc["id"]))
            c.execute("UPDATE orders SET internal_eta=? WHERE id=?", (exc["expected_eta"], o["id"]))
            audit.record(c, "order", o["order_no"], actor, "capacity_exception_confirmed", order_id=o["id"],
                         reason=exc["reason"], detail={"approved_by": exc["approved_by"],
                                                       "expected_eta": exc["expected_eta"], "maker_feasible": True})
    return tr.perform(ctx, o["id"], "accept", actor, p, expected_version=p.get("version"), effect=effect)


def get(ctx, order_no: str):
    return ctx.db.one("SELECT o.*, c.display_name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id "
                      "WHERE o.order_no=?", (order_no,))


def holds_of(ctx, order_id):
    return ctx.db.all("SELECT * FROM holds WHERE order_id=? AND cleared_at IS NULL ORDER BY id", (order_id,))


def issues_of(ctx, order_id, only_open=True):
    q = "SELECT * FROM issues WHERE order_id=?" + (" AND state='OPEN'" if only_open else "") + " ORDER BY id"
    return ctx.db.all(q, (order_id,))


def _queue_where(queue: str) -> tuple[str, list]:
    if queue in QUEUE_STATES:
        sts = [s.value for s in QUEUE_STATES[queue]]
        w = f"o.lifecycle_state IN ({','.join('?' * len(sts))})"
        if queue == "review":
            w += (" AND NOT EXISTS (SELECT 1 FROM holds h WHERE h.order_id=o.id AND h.cleared_at IS NULL "
                  "AND h.reason<>'NO_CONSENT')")
        return w, sts
    if queue == "needs_info":
        return "EXISTS (SELECT 1 FROM holds h WHERE h.order_id=o.id AND h.cleared_at IS NULL AND h.reason='NEEDS_INFO')", []
    if queue == "waitlisted":
        return "EXISTS (SELECT 1 FROM holds h WHERE h.order_id=o.id AND h.cleared_at IS NULL AND h.reason='WAITLISTED')", []
    if queue == "issues":
        return ("(EXISTS (SELECT 1 FROM issues i WHERE i.order_id=o.id AND i.state='OPEN') OR EXISTS "
                "(SELECT 1 FROM notifications n WHERE n.order_id=o.id AND n.resolved_at IS NULL "
                "AND n.kind IN ('customer_wait','delivery_risk')))"), []
    if queue == "all":
        return "o.lifecycle_state NOT IN ('CLOSED','DECLINED','CANCELLED')", []
    raise KeyError(queue)


def queue_counts(ctx) -> dict[str, int]:
    out = {}
    for key, _ in QUEUES:
        w, a = _queue_where(key)
        out[key] = ctx.db.scalar(f"SELECT COUNT(*) FROM orders o WHERE {w}", a)
    return out


SORTS = {"updated": "o.updated_at DESC", "created": "o.created_at ASC", "promised": "o.promised_date IS NULL, o.promised_date ASC"}


def list_orders(ctx, queue="all", q=None, sort=None, page=1, per_page=50):
    w, a = _queue_where(queue)
    if q:
        from .customers import ids_by_contact_tail, tail_digits
        tail = ids_by_contact_tail(ctx, q.strip()) if tail_digits(q) else []  # 연락처 뒷자리 4자리
        w += (" AND (o.order_no LIKE ? OR c.display_name LIKE ? OR o.summary LIKE ? OR o.product_type LIKE ?"
              f" OR o.customer_id IN ({','.join('?' * len(tail)) or 'NULL'}))")
        a = a + [f"%{q}%"] * 4 + tail
    if sort not in SORTS:
        sort = "created" if queue in ("review", "needs_info", "waitlisted") else "updated"
    total = ctx.db.scalar(f"SELECT COUNT(*) FROM orders o JOIN customers c ON c.id=o.customer_id WHERE {w}", a)
    rows = ctx.db.all(f"SELECT o.*, c.display_name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id "
                      f"WHERE {w} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?", a + [per_page, (page - 1) * per_page])
    return rows, total, sort


# ---------------- 다음 조치(PDS §5.3) ----------------
NEXT = {
    S.OWNER_REVIEW: ("accept", "이 의뢰를 맡을지 정하세요", ["request_info", "waitlist", "hold", "decline"]),
    S.FREE_CONCEPT: ("approve_quote", "시안을 올리고 견적을 승인하세요", []),
    S.QUOTE_APPROVED: ("record_acceptance", "계약 동의 링크(Form B)를 보내고 응답을 검증하세요", []),
    S.CUSTOMER_ACCEPTED: ("create_payment_req", "결제 요청을 준비하세요", []),
    S.PAYMENT_PENDING: ("record_payment", "네이버 결제를 확인해 입력하세요", []),
    S.INITIAL_PAYMENT_VERIFIED: ("start_cad", "상세 설계를 시작하세요", []),
    S.DETAIL_CAD: ("record_design_ok", "최종 시안을 고객에게 확인받으세요", []),
    S.CUSTOMER_DESIGN_APPROVED: ("send_factory", "잔금 결제를 확인한 뒤 사양서와 패킷으로 발주하세요", []),
    S.FACTORY_SENT: ("receive", "공장에서 받으면 기록하세요", []),
    S.FACTORY_RECEIVED: ("pass_qc", "검수표를 채우세요", []),
    S.QC_PASSED: ("verify_final", "잔금 결제를 확인하세요", []),
    S.FINAL_PAYMENT_VERIFIED: ("ship", "배송 정보를 입력하고 발송하세요", []),
    S.SHIPPED: ("close", "배달을 확인하면 완료하세요", []),
}
ACTION_LABEL = {**{k: v.label for k, v in tr.TRANSITIONS.items()}, "record_payment": "결제 확인 입력",
                "record_consent": "동의 증빙 기록",
                "request_info": "정보 요청", "waitlist": "대기로 표시", "hold": "보류", "verify_final_full": "결제 조건 확인"}
ACTION_TAB = {"accept": "intake", "decline": "intake", "approve_quote": "quote", "record_acceptance": "quote",
              "create_payment_req": "payment", "record_payment": "payment", "verify_final": "payment",
              "start_cad": "making", "record_design_ok": "making", "send_factory": "making", "build_packet": "making",
              "receive": "making", "pass_qc": "qc", "ship": "qc", "close": "qc"}


@dataclass
class NextAction:
    action: str | None
    label: str
    sentence: str
    roles: list[str]
    unmet: list[Unmet] = field(default_factory=list)
    secondary: list[str] = field(default_factory=list)
    tab: str | None = None
    system: bool = False


def next_action(ctx, o, actor) -> NextAction:
    st = S(o["lifecycle_state"])
    if st in ENDED:
        return NextAction(None, "", "이 주문은 끝났어요", [])
    if st in (S.INQUIRY_RECEIVED, S.AI_BRIEF_READY):
        return NextAction(None, "", "AI가 의뢰를 정리하고 있어요", ["system"], system=True)
    action, sentence, secondary = NEXT[st]
    if st == S.OWNER_REVIEW and any(h["reason"] == "NO_CONSENT" for h in holds_of(ctx, o["id"])):
        return NextAction("record_consent", "동의 증빙 기록", "개인정보 동의를 먼저 받아 기록하세요",
                          ["maker", "operator"], [], ["decline"], "intake")
    label = ACTION_LABEL[action]
    check_action = action
    if action == "record_payment":
        check_action = "verify_initial"
    if action == "verify_final" and o["payment_policy"] == "FULL":
        label = ACTION_LABEL["verify_final_full"]
        sentence = "전액 결제 정책 — 결제 조건을 확인하세요"
    t = tr.TRANSITIONS[check_action]
    um = tr.check(ctx, o["id"], check_action, actor)
    if action == "record_payment":  # 입력 자체는 조건이 아니라 결과로 판정되므로 역할·보류·이슈만 표시
        um = [u for u in um if u.code not in ("G-PAYMENT-UNVERIFIED",)]
    return NextAction(action, label, sentence, sorted(t.actors), um, secondary, ACTION_TAB.get(action))


def approval_state(ctx, o, actor=None) -> str:
    st = S(o["lifecycle_state"])
    if st in ENDED or st in (S.INQUIRY_RECEIVED, S.AI_BRIEF_READY):
        return "—"
    na = next_action(ctx, o, actor)
    if na.action == "record_consent":
        return "조건 미충족"
    blocking = [u for u in tr.check(ctx, o["id"], "verify_initial" if na.action == "record_payment" else na.action, None)
                if not u.code.startswith("G-ROLE") and u.code != "G-SYSTEM-ONLY"]
    if blocking and not all(u.code in ("G-ACCEPT-EVIDENCE", "G-CONFIRM", "G-PAYMENT-UNVERIFIED", "G-BALANCE")
                            for u in blocking):
        return "조건 미충족"
    if st in (S.QUOTE_APPROVED, S.DETAIL_CAD):
        return "고객 동의 대기"
    roles = tr.TRANSITIONS["verify_initial" if na.action == "record_payment" else na.action].actors
    return "아버님 승인 대기" if "maker" in roles else "운영 확인 대기"


def phase_steps(o) -> list[dict]:
    st = S(o["lifecycle_state"])
    ph = PHASE_OF[st]
    if ph == Phase.ENDED:
        return []
    cur = PHASES5.index(ph)
    out = []
    for i, p in enumerate(PHASES5):
        state = "done" if i < cur or (st == S.CLOSED) else ("current" if i == cur else "todo")
        out.append({"label": PHASE_LABEL[p], "state": state})
    return out


def apply_hold_action(ctx, o, action, actor, note=None) -> None:
    """수락 검토의 보조 조치: 정보 요청·대기·보류(maker), 해제."""
    if actor is None or actor.role != "maker":
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])
    reason = {"request_info": "NEEDS_INFO", "waitlist": "WAITLISTED", "hold": "HOLD"}[action]
    if action == "hold" and not (note or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    with ctx.db.tx() as c:
        set_hold(c, o["id"], reason, actor, note)
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))


def release_hold(ctx, o, reason, actor, note=None) -> None:
    if reason == "NO_CONSENT":
        raise ValueError("동의 증빙 기록으로만 해제돼요")
    if actor is None or actor.role != "maker":
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])
    with ctx.db.tx() as c:
        clear_hold(c, o["id"], reason, actor, note)
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
    from . import payments
    payments.try_verify(ctx, o["id"], actor)


def record_consent(ctx, o, actor, channel, evidence) -> None:
    """동의 없음 보류 해제(S-03a) — 증빙 필수. 해제 뒤 AI 분석 대상이 된다."""
    if not ((channel or "").strip() and (evidence or "").strip()):
        raise TransitionBlocked([unmet("G-ACCEPT-EVIDENCE")])
    with ctx.db.tx() as c:
        c.execute("INSERT INTO customer_acceptances(order_id, kind, channel, evidence_text, accepted_at, recorded_by, "
                  "recorded_at) VALUES (?,?,?,?,?,?,?)", (o["id"], "consent", channel, evidence, clock.now_iso(),
                                                        actor.id, clock.now_iso()))
        c.execute("UPDATE customers SET consent_version=? WHERE id=?", (settings.get(ctx, "consent_version"),
                                                                        o["customer_id"]))
        clear_hold(c, o["id"], "NO_CONSENT", actor, "동의 증빙 기록")


def correct_spec(ctx, o, actor, spec: dict, reason: str) -> int:
    """확정 사양 정정(FR-ADM-007) — 새 revision, 이전 값 보존."""
    if not (reason or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    with ctx.db.tx() as c:
        before = current_spec(ctx, o["id"])  # 정정 전 확정 사양(처음 정정이면 승인 견적 사양)
        prev = c.execute("SELECT * FROM order_specs WHERE order_id=? ORDER BY revision DESC LIMIT 1", (o["id"],)).fetchone()
        rev = (prev["revision"] + 1) if prev else 1
        c.execute("INSERT INTO order_specs(order_id, revision, spec_json, change_reason, created_by, created_at) "
                  "VALUES (?,?,?,?,?,?)", (o["id"], rev, jdump(spec), reason, actor.id, clock.now_iso()))
        audit.record(c, "order_spec", f"{o['order_no']}:r{rev}", actor, "spec_corrected", order_id=o["id"],
                     prev=jdump(before), next=jdump(spec),
                     reason=reason, ref_version=rev)
        return rev


def current_spec(ctx, order_id) -> dict:
    """확정 사양: 정정 기록이 있으면 최신 정정, 없으면 승인 견적 사양."""
    r = ctx.db.one("SELECT spec_json FROM order_specs WHERE order_id=? AND revision>0 ORDER BY revision DESC LIMIT 1",
                   (order_id,))
    if r:
        return jload(r["spec_json"], {})
    q = ctx.db.one("SELECT spec_json FROM quotes WHERE order_id=? AND status='APPROVED' ORDER BY version DESC LIMIT 1",
                   (order_id,))
    return jload(q["spec_json"], {}) if q else {}


def timeline(ctx, order_id, kind="all"):
    rows = ctx.db.all("SELECT a.*, u.display_name AS actor_name FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id "
                      "WHERE a.order_id=? ORDER BY a.id DESC", (order_id,))
    if kind == "approvals":
        rows = [r for r in rows if r["object_type"] == "order" and r["prev"] != r["next"]]
    elif kind == "corrections":
        rows = [r for r in rows if r["action"] in ("spec_corrected", "revert")]
    elif kind == "system":
        rows = [r for r in rows if r["actor_role"] == "system"]
    return rows


def log_work(ctx, o, actor, kind, minutes, work_date, note=None) -> None:
    if int(minutes) <= 0:
        raise ValueError("시간을 1분 이상 입력하세요")
    with ctx.db.tx() as c:
        c.execute("INSERT INTO work_logs(order_id, kind, minutes, work_date, note, created_by, created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (o["id"], kind, int(minutes), work_date, note, actor.id, clock.now_iso()))


def work_summary(ctx, order_id) -> dict:
    rows = ctx.db.all("SELECT kind, SUM(minutes) m FROM work_logs WHERE order_id=? GROUP BY kind", (order_id,))
    d = {r["kind"]: r["m"] for r in rows}
    d["_total"] = sum(d.values())
    return d
