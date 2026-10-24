"""결제 요청·수동 결제 확인·불일치·환불(FUN-006, BD §6.4). 금전 사실은 네이버 증빙으로만(SRS-112)."""
from __future__ import annotations

import secrets

from ..core import clock
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, unmet
from ..domain.money import parse_won
from ..domain.states import S
from . import audit, orders, settings

EVENT_OF_STATUS = {"PAYED": "PAID", "CANCEL_REQUEST": "CANCEL_REQ", "CANCELED": "CANCELLED",
                   "RETURN_REQUEST": "RETURN_REQ", "RETURNED": "RETURNED", "NOT_PAID": "UNPAID", "REFUND": "REFUND"}


def _operator(actor):
    if actor is None or actor.role != "operator":
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])


def _new_request(ctx, c, o, quote, phase, amount, actor, change_order_id=0):
    code = f"JOS{secrets.token_hex(4).upper()}"  # 개인정보 없는 판매자 관리코드(SRS-053)
    is_test = int(settings.test_mode(ctx) or bool(quote["is_test"]) or bool(o["is_test"]))
    cur = c.execute("INSERT INTO payment_requests(order_id, quote_id, quote_version, phase, expected_krw, seller_code, "
                    "status, is_test, created_by, created_at, change_order_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (o["id"], quote["id"], quote["version"], phase, amount, code, "DRAFT", is_test, actor.id,
                     clock.now_iso(), change_order_id))
    audit.record(c, "payment_request", cur.lastrowid, actor, "payment_request_created", order_id=o["id"],
                 next=phase, ref_version=quote["version"], detail={"amount": amount, "is_test": is_test})
    return cur.lastrowid


def create_initial(ctx, o, actor, payload):
    """CUSTOMER_ACCEPTED → PAYMENT_PENDING(전이)."""
    def effect(c, cur):
        q = tr.latest_quote(c, o["id"], "APPROVED")
        phase = "FULL" if cur["payment_policy"] == "FULL" else "DEPOSIT"
        _new_request(ctx, c, cur, q, phase, q["deposit_krw"] if phase == "DEPOSIT" else q["total_krw"], actor)
    return tr.perform(ctx, o["id"], "create_payment_req", actor, payload, expected_version=payload.get("version"),
                      effect=effect)


def recreate_initial(ctx, o, actor):
    """재견적으로 미결제 요청이 취소된 뒤(단계 PAYMENT_PENDING) 새 버전 기준 요청."""
    _operator(actor)
    with ctx.db.tx() as c:
        if tr.payment_request(c, o["id"], ("DEPOSIT", "FULL")) is not None:
            raise TransitionBlocked([unmet("G-WRONG-STATE")])
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        if cur["lifecycle_state"] != S.PAYMENT_PENDING.value:
            raise TransitionBlocked([unmet("G-WRONG-STATE")])
        um = tr.block_unmet(c, o["id"], "recreate_payment_req")
        if um:
            raise TransitionBlocked(um)
        q = tr.latest_quote(c, o["id"], "APPROVED")
        if q["is_test"] and not settings.test_mode(ctx):
            raise TransitionBlocked([unmet("G-TEST-QUOTE")])
        phase = "FULL" if cur["payment_policy"] == "FULL" else "DEPOSIT"
        return _new_request(ctx, c, cur, q, phase, q["deposit_krw"] if phase == "DEPOSIT" else q["total_krw"], actor)


def verified_initial_amount(c, order_id) -> int:
    r = tr.payment_request(c, order_id, ("DEPOSIT", "FULL"))
    return tr.paid_sum(c, r["id"]) if r and r["status"] == "PAID_VERIFIED" else 0


def balance_amount(c, o) -> int | None:
    q = tr.latest_quote(c, o["id"], "APPROVED")
    if o["payment_policy"] == "FULL":
        return None
    bal = q["total_krw"] - verified_initial_amount(c, o["id"])
    return bal if bal > 0 else None


def create_balance(ctx, o, actor):
    """잔금 요청(비전이). D-24: 최종 CAD 승인 뒤(발주 전) 또는 QC_PASSED. FULL·잔금 0원이면 만들지 않는다."""
    _operator(actor)
    with ctx.db.tx() as c:
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        if cur["lifecycle_state"] not in (S.CUSTOMER_DESIGN_APPROVED.value, S.QC_PASSED.value):
            raise TransitionBlocked([unmet("G-WRONG-STATE")])
        if tr.payment_request(c, o["id"], ("BALANCE",)) is not None:
            raise TransitionBlocked([unmet("G-WRONG-STATE")])
        um = tr.block_unmet(c, o["id"], "create_balance_req")
        if um:
            raise TransitionBlocked(um)
        amount = balance_amount(c, cur)
        if amount is None:
            raise TransitionBlocked([unmet("G-WRONG-STATE")])
        q = tr.latest_quote(c, o["id"], "APPROVED")
        return _new_request(ctx, c, cur, q, "BALANCE", amount, actor)


def set_link(ctx, request_id, actor, url, ready: bool):
    _operator(actor)
    with ctx.db.tx() as c:
        c.execute("UPDATE payment_requests SET link_url=?, link_status=?, status=CASE WHEN status='DRAFT' AND ?=1 THEN "
                  "'LINK_READY' ELSE status END WHERE id=?", (url or None, "LINK_READY" if ready else "DRAFT", int(ready),
                                                             request_id))


class PaymentInputError(Exception):
    def __init__(self, errors: dict):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def record_payment(ctx, o, actor, form: dict) -> dict:
    """결제 이벤트 입력(SRS-052·057·058). 반환 {result: verified|mismatch|refund_review|duplicate|recorded}."""
    _operator(actor)
    phase = form.get("phase") or "DEPOSIT"
    status = form.get("naver_status")
    need = {"naver_order_id": "네이버 주문번호", "naver_product_order_id": "상품주문번호", "amount": "금액",
            "naver_status": "상태", "looked_up_at": "조회 시각"}
    errors = {k: f"{v}을(를) 입력하세요." for k, v in need.items() if not (str(form.get(k) or "")).strip()}
    if errors:
        raise PaymentInputError({**errors, "_unmet": unmet("G-PAYMENT-EVIDENCE").message})
    try:
        amount = parse_won(form["amount"])
    except ValueError as e:
        raise PaymentInputError({"amount": str(e)})
    if status not in EVENT_OF_STATUS:
        raise PaymentInputError({"naver_status": "상태를 고르세요."})
    ev_type = EVENT_OF_STATUS[status]
    try:
        looked, looked_src = clock.from_kst_input(form["looked_up_at"])
    except ValueError:
        raise PaymentInputError({"looked_up_at": "조회 시각 형식이 올바르지 않아요."})
    if not form.get("confirm"):
        raise TransitionBlocked([unmet("G-CONFIRM")])
    with ctx.db.tx() as c:
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        req = tr.payment_request(c, o["id"], {"BALANCE": ("BALANCE",), "CHANGE": ("CHANGE",)}.get(phase,
                                                                                                ("DEPOSIT", "FULL")))
        if req is None:
            raise TransitionBlocked([unmet("G-PAYMENT-REQUEST")])
        dup = c.execute("SELECT * FROM payment_events WHERE naver_product_order_id=? AND event_type=?",
                        (form["naver_product_order_id"].strip(), ev_type)).fetchone()
        if dup:
            return {"result": "duplicate", "event": dict(dup)}
        c.execute("INSERT INTO payment_events(request_id, naver_order_id, naver_product_order_id, event_type, amount_krw, "
                  "naver_status, looked_up_at, looked_up_at_src, note, is_test, recorded_by, recorded_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (req["id"], form["naver_order_id"].strip(), form["naver_product_order_id"].strip(), ev_type, amount,
                   status, looked, looked_src, form.get("note") or None, req["is_test"], actor.id, clock.now_iso()))
        audit.record(c, "payment_event", form["naver_product_order_id"], actor, "payment_recorded", order_id=o["id"],
                     next=ev_type, detail={"amount": amount, "phase": req["phase"]})
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
        if ev_type in ("CANCEL_REQ", "CANCELLED", "RETURN_REQ", "RETURNED"):
            orders.open_issue(c, o["id"], "REFUND_REVIEW", actor, f"네이버 상태: {status}",
                              {"request_id": req["id"], "status": status})
            return {"result": "refund_review"}
        if ev_type in ("REFUND", "UNPAID"):
            return {"result": "recorded"}
        total = tr.paid_sum(c, req["id"])
        if total != req["expected_krw"]:
            c.execute("UPDATE payment_requests SET status='MISMATCH' WHERE id=?", (req["id"],))
            orders.open_issue(c, o["id"], "PAYMENT_MISMATCH", actor, "결제 금액 불일치",
                              {"request_id": req["id"], "expected": req["expected_krw"], "paid": total,
                               "diff": req["expected_krw"] - total})
            return {"result": "mismatch", "expected": req["expected_krw"], "paid": total}
        if req["phase"] == "CHANGE":  # 변경 추가금: 단계 전이 없이 요청만 확인(H1 r1 Q10)
            c.execute("UPDATE payment_requests SET status='PAID_VERIFIED' WHERE id=?", (req["id"],))
            audit.record(c, "payment_request", req["id"], actor, "change_payment_verified", order_id=o["id"],
                         detail={"amount": total})
            return {"result": "verified_change"}
        # 누적 합 = 기대: 같은 트랜잭션에서 결제 확인 전이(BD §6.4 경계 불변식, TC-I-003)
        c.execute("UPDATE payment_requests SET status='PAID' WHERE id=?", (req["id"],))
        res = _verify_in_tx(ctx, c, o["id"], actor)
        return {"result": res or "awaiting"}


def _verify_in_tx(ctx, c, order_id, actor) -> str | None:
    o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    st = S(o["lifecycle_state"])
    if st == S.CUSTOMER_DESIGN_APPROVED:  # D-24: 발주 전 잔금 — 단계 전이 없이 요청만 확인(검수 뒤 잔금 확인 단계에서 다시 씀)
        req = tr.payment_request(c, order_id, ("BALANCE",))
        if req is None or tr.paid_sum(c, req["id"]) != req["expected_krw"] or req["status"] == "PAID_VERIFIED":
            return None
        if tr.block_unmet(c, order_id, "verify_final"):
            return None
        c.execute("UPDATE payment_requests SET status='PAID_VERIFIED' WHERE id=?", (req["id"],))
        audit.record(c, "payment_request", req["id"], actor, "balance_verified_before_factory", order_id=order_id,
                     detail={"amount": req["expected_krw"]})
        return "verified"
    action = {S.PAYMENT_PENDING: "verify_initial", S.QC_PASSED: "verify_final"}.get(st)
    if action is None or (action == "verify_final" and o["payment_policy"] == "FULL"):
        return None
    phases = ("DEPOSIT", "FULL") if action == "verify_initial" else ("BALANCE",)
    req = tr.payment_request(c, order_id, phases)
    if req is None or tr.paid_sum(c, req["id"]) != req["expected_krw"]:
        return None
    if tr.check(ctx, order_id, action, actor, {}, submitting=False, c=c):
        return None  # 보류·이슈로 막힘 → 요청 상태 PAID(확인 대기), 차단이 풀리면 try_verify가 다시 시도

    def effect(cc, cur):
        cc.execute("UPDATE payment_requests SET status='PAID_VERIFIED' WHERE id=?", (req["id"],))
    tr.perform(ctx, order_id, action, actor, {"is_test": o["is_test"]}, effect=effect)
    return "verified"


def try_verify(ctx, order_id, actor=None) -> str | None:
    """차단이 풀리는 동작(보류 해제·이슈 해결·동의 기록) 뒤 결제 확인 재시도.
    주체 = 그 요청에 마지막 결제완료를 입력한 operator(확인 체크로 이미 확인한 사람, DD-002 r3)."""
    from ..core.security import User
    with ctx.db.tx() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        bal_states = (S.QC_PASSED.value, S.CUSTOMER_DESIGN_APPROVED.value)
        phases = ("BALANCE",) if o["lifecycle_state"] in bal_states else ("DEPOSIT", "FULL")
        req = tr.payment_request(c, order_id, phases)
        if req is None:
            return None
        ev = c.execute("SELECT u.* FROM payment_events e JOIN users u ON u.id=e.recorded_by WHERE e.request_id=? AND "
                       "e.event_type='PAID' ORDER BY e.id DESC LIMIT 1", (req["id"],)).fetchone()
        if ev is None or ev["role"] != "operator":
            return None
        op = User(ev["id"], ev["username"], ev["display_name"], ev["role"], {})
        return _verify_in_tx(ctx, c, order_id, op)


def verify_final_full(ctx, o, actor, payload):
    """전액 정책: QC 후 결제 조건 확인(SRS-114)."""
    if o["payment_policy"] != "FULL":
        raise TransitionBlocked([unmet("G-BALANCE")])
    if not payload.get("confirm"):
        raise TransitionBlocked([unmet("G-CONFIRM")])
    return tr.perform(ctx, o["id"], "verify_final", actor, payload, expected_version=payload.get("version"))


def requests_of(ctx, order_id):
    return ctx.db.all("SELECT * FROM payment_requests WHERE order_id=? ORDER BY id", (order_id,))


def events_of(ctx, order_id):
    return ctx.db.all("SELECT e.*, r.phase, u.display_name AS recorder FROM payment_events e JOIN payment_requests r "
                      "ON r.id=e.request_id LEFT JOIN users u ON u.id=e.recorded_by WHERE r.order_id=? ORDER BY e.id",
                      (order_id,))


def receivables(ctx) -> dict:
    """받을 돈(IMPLICIT-13): 확인되지 않은 결제 요청 기대 금액 합."""
    rows = ctx.db.all("SELECT r.*, o.order_no, c.display_name AS customer_name FROM payment_requests r "
                      "JOIN orders o ON o.id=r.order_id JOIN customers c ON c.id=o.customer_id "
                      "WHERE r.status IN ('DRAFT','LINK_READY','MISMATCH') AND o.lifecycle_state NOT IN "
                      "('CANCELLED','DECLINED') ORDER BY r.created_at")
    live = sum(r["expected_krw"] - 0 for r in rows if not r["is_test"])
    test = sum(r["expected_krw"] for r in rows if r["is_test"])
    return {"rows": rows, "live": live, "test": test}
