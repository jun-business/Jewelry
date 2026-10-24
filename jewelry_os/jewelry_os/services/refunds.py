"""취소·환불 사건(D-25, Form B v1.1 '제작 단계별 취소 및 환불 안내' R1~R7).

- 접수 시점의 사실(결제·상세 CAD 착수·발주·배송 시각, 결제액)은 시스템 기록에서 가져오고 고칠 수 없다.
  CAD 착수 시각은 start_cad 전이의 감사로그(서버 시각) — 사람이 입력하거나 소급할 수 없다.
- 접수하면 '환불·취소 검토' 문제가 열려 상세 CAD 착수·발주·검수·발송이 막힌다(취소 요청 뒤 착수 기록 방지).
- 자동 계산은 R1(결제 없음 → 0원)·R2(CAD 착수 전 → 예약금 전액)만. 그 밖은 법적 검토와 사람의 결정.
- 환불 불가·공제액을 자동으로 정하지 않는다. AI는 이 모듈을 쓰지 않는다.
- 하자·계약 불일치(R6)는 청약철회 제한 동의와 별개 — 법정 권리 검토 대상."""
from __future__ import annotations

from datetime import date, timedelta

from ..core import clock
from ..core.db import jdump, jload
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, unmet
from . import audit, orders

REASON_LABEL = {"CHANGE_OF_MIND": "단순 변심", "DEFECT": "제품 하자", "MISMATCH": "계약·표시 내용과 다름", "OTHER": "기타"}
STAGE_LABEL = {
    "R1": "예약금 결제 전", "R2": "예약금 결제 후 · 상세 CAD 착수 전", "R3": "상세 CAD 착수 후 · 제조 발주 전",
    "R4": "제조 발주 후 · 배송 전", "R5": "배송 후(단순 변심)", "R6": "제품 하자·계약 불일치",
}
STAGE_GUIDE = {
    "R1": "결제한 금액이 없으면 돌려줄 대금이 없어요. 취소 요청만 기록해요.",
    "R2": "틴틴골드 자체 정책: 예약금 전액 환불 대상이에요. 실제 환급은 결제 취소·이체 절차로 처리하고 증거를 남기세요.",
    "R3": "계약 내용과 실제 작업 진행을 확인하세요. 법정 청약철회가 가능하면 그 기준이 우선이에요. "
          "CAD 비용이 생겼다는 이유만으로 정해진 금액을 자동 공제하지 않아요.",
    "R4": "개별 주문제작 여부·회복할 수 없는 중대한 피해·사전 별도 고지·전자문서 동의·실제 제조 진행과 손해 근거를 "
          "모두 검토하세요. Form B 동의 하나로 청약철회 제한을 판정하지 않아요.",
    "R5": "일반 청약철회 기간과 주문제작 예외 요건을 검토하세요. 요건이 맞아도 자동으로 환불 불가 처리하지 않아요.",
    "R6": "표시·광고·계약과 다른 공급이면 수령일부터 3개월·안 날부터 30일 이내 법정 청약철회권이 있어요. "
          "하자에 대한 소비자 권리도 그대로예요. 청약철회 제한 동의와 별개로 처리하세요.",
}
LEGAL_LABEL = {"NOT_REQUIRED": "법적 검토 불필요(자체 정책 적용)", "REQUIRED": "법적 검토 필요", "DONE": "법적 검토 완료"}
STATUS_LABEL = {"RECEIVED": "접수", "APPROVED": "환불액 결정됨", "COMPLETED": "환급 완료", "WITHDRAWN": "고객 철회"}
BEARER_LABEL = {"CUSTOMER": "고객 부담(단순 변심)", "SELLER": "판매자 부담(판매자 귀책)", "NONE": "해당 없음"}


def _admin(actor):
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])


def _event(c, r, event, actor, detail=None):
    c.execute("INSERT INTO refund_events(refund_id, order_id, event, actor_id, actor_role, detail_json, created_at) "
              "VALUES (?,?,?,?,?,?,?)", (r["id"], r["order_id"], event, getattr(actor, "id", None),
                                         getattr(actor, "role", "system"), jdump(detail or {}), clock.now_iso()))
    audit.record(c, "refund", r["id"], actor, f"refund_{event}", order_id=r["order_id"], detail=detail or {})


# ---------------- 사실 ----------------
def facts(c, order_id) -> dict:
    """주문의 진행 사실(시스템 기록 기준). 시각은 UTC ISO."""
    pay = c.execute("SELECT MIN(e.looked_up_at) FROM payment_events e JOIN payment_requests r ON r.id=e.request_id "
                    "WHERE r.order_id=? AND r.phase IN ('DEPOSIT','FULL') AND e.event_type='PAID'", (order_id,)).fetchone()[0]
    paid = c.execute("SELECT COALESCE(SUM(CASE WHEN e.event_type='PAID' THEN e.amount_krw WHEN e.event_type='REFUND' "
                     "THEN -e.amount_krw ELSE 0 END),0) FROM payment_events e JOIN payment_requests r ON r.id=e.request_id "
                     "WHERE r.order_id=?", (order_id,)).fetchone()[0]
    first = lambda action: c.execute("SELECT MIN(created_at) FROM audit_events WHERE order_id=? AND action=?",  # noqa: E731
                                     (order_id, action)).fetchone()[0]
    sh = c.execute("SELECT * FROM shipments WHERE order_id=? ORDER BY id LIMIT 1", (order_id,)).fetchone()
    return {"deposit_paid_at": pay, "paid_amount": paid, "cad_started_at": first("start_cad"),
            "factory_ordered_at": first("send_factory"),
            "delivered_at": (sh["delivered_confirmed_at"] or sh["shipped_at"]) if sh else None}


def classify(f: dict, reason_kind: str, requested_at: str) -> str:
    """요청 시점의 제작 단계(R1~R6). 시각 비교는 요청 접수 시각 기준 — 그 뒤에 생긴 기록은 단계를 올리지 않는다."""
    if reason_kind in ("DEFECT", "MISMATCH"):
        return "R6"
    before = lambda t: bool(t) and clock.parse(t) <= clock.parse(requested_at)  # noqa: E731
    if before(f["delivered_at"]):
        return "R5"
    if before(f["factory_ordered_at"]):
        return "R4"
    if before(f["cad_started_at"]):
        return "R3"
    if f["paid_amount"] > 0:  # 결제한 금액이 있고 상세 CAD 착수 전 → 예약금 전액 환불 대상(R2)
        return "R2"
    return "R1"


def eligible_amount(stage: str, paid: int) -> int | None:
    """자동 계산은 R1·R2만(정책상 명확). 그 밖은 사람이 결정 — 자동 공제 없음."""
    if stage == "R1":
        return 0 if paid <= 0 else None
    if stage == "R2":
        return paid
    return None


def add_business_days(d: date, n: int, holidays: set) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in holidays:
            n -= 1
    return d


# ---------------- 처리 ----------------
def record(ctx, o, actor, reason_kind: str, reason: str, via: str, requested_at: str | None = None) -> int:
    """고객 취소·환불 요청 접수. 반환 refund id."""
    _admin(actor)
    miss = [lab for k, lab in (("reason", "요청 내용"), ("via", "접수 채널")) if not ({"reason": reason, "via": via}[k] or "").strip()]
    if reason_kind not in REASON_LABEL:
        miss.insert(0, "요청 사유 구분")
    if miss:
        raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="·".join(miss))])
    at, src = clock.from_kst_input(requested_at)
    if clock.parse(at) > clock.now():
        raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="미래가 아닌 접수 시각")])
    with ctx.db.tx() as c:
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        f = facts(c, o["id"])
        stage = classify(f, reason_kind, at)
        legal = "NOT_REQUIRED" if stage in ("R1", "R2") else "REQUIRED"
        creq = c.execute("SELECT id FROM contract_requests WHERE order_id=? AND status<>'SUPERSEDED' ORDER BY "
                         "CASE status WHEN 'ACCEPTED' THEN 0 ELSE 1 END, id DESC LIMIT 1", (o["id"],)).fetchone()
        iid = orders.open_issue(c, o["id"], "REFUND_REVIEW", actor, f"고객 취소·환불 요청({REASON_LABEL[reason_kind]})",
                                {"stage": stage})
        rid = c.execute(
            "INSERT INTO refund_requests(order_id, customer_id, contract_request_id, reason_kind, refund_reason, requested_via, "
            "requested_at, requested_at_src, lifecycle_state_at_request, production_stage_at_request, paid_amount, "
            "deposit_paid_at, cad_started_at, factory_ordered_at, delivered_at, legal_review_status, refund_eligible_amount, "
            "shipping_fee_bearer, issue_id, recorded_by, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o["id"], cur["customer_id"], creq["id"] if creq else None, reason_kind, reason.strip(), via.strip(), at, src,
             cur["lifecycle_state"], stage, f["paid_amount"], f["deposit_paid_at"], f["cad_started_at"],
             f["factory_ordered_at"], f["delivered_at"], legal, eligible_amount(stage, f["paid_amount"]),
             "SELLER" if reason_kind in ("DEFECT", "MISMATCH") else None, iid if isinstance(iid, int) else None,
             actor.id, clock.now_iso())).lastrowid
        r = c.execute("SELECT * FROM refund_requests WHERE id=?", (rid,)).fetchone()
        _event(c, r, "received", actor, {"stage": stage, "reason_kind": reason_kind, "legal": legal,
                                         "eligible": r["refund_eligible_amount"], "facts": f})
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
        return rid


def get(ctx, rid):
    return ctx.db.one("SELECT * FROM refund_requests WHERE id=?", (rid,))


def of_order(ctx, order_id):
    return ctx.db.all("SELECT * FROM refund_requests WHERE order_id=? ORDER BY id DESC", (order_id,))


def events_of(ctx, rid):
    return ctx.db.all("SELECT e.*, u.display_name FROM refund_events e LEFT JOIN users u ON u.id=e.actor_id "
                      "WHERE e.refund_id=? ORDER BY e.id", (rid,))


def _load(c, rid, allowed):
    r = c.execute("SELECT * FROM refund_requests WHERE id=?", (rid,)).fetchone()
    if r is None or r["status"] not in allowed:
        raise TransitionBlocked([unmet("G-REFUND-STATE", status=STATUS_LABEL.get(r["status"] if r else "", "-"))])
    return r


def legal_review(ctx, rid, actor, note: str, legal_withdrawal: bool | None) -> None:
    """법적 검토 기록(청약철회 가능 여부, 법정 권리). 제작 진행 상황과 따로 표시한다."""
    _admin(actor)
    if not (note or "").strip():
        raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="검토 근거")])
    with ctx.db.tx() as c:
        r = _load(c, rid, ("RECEIVED",))
        c.execute("UPDATE refund_requests SET legal_review_status='DONE', legal_review_note=?, legal_review_by=?, "
                  "legal_review_at=?, legal_withdrawal=? WHERE id=?",
                  (note.strip(), actor.id, clock.now_iso(), None if legal_withdrawal is None else int(legal_withdrawal), rid))
        _event(c, r, "legal_reviewed", actor, {"note": note.strip(), "legal_withdrawal": legal_withdrawal})


def approve(ctx, rid, actor, amount, reason: str, bearer: str | None = None, shipping_fee=None,
            basis_date: str | None = None) -> None:
    """환불액 결정(사람). 법적 검토가 필요한 건은 검토 기록이 먼저. 법정 청약철회면 3영업일 환급 기한을 정한다."""
    from ..domain.money import parse_won
    _admin(actor)
    miss = []
    if amount in (None, ""):
        miss.append("환불 결정액")
    if not (reason or "").strip():
        miss.append("결정 근거")
    if bearer not in (None, "", "CUSTOMER", "SELLER", "NONE"):
        miss.append("배송비 부담 구분")
    if miss:
        raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="·".join(miss))])
    amt = parse_won(str(amount))
    fee = parse_won(str(shipping_fee)) if shipping_fee not in (None, "") else None
    with ctx.db.tx() as c:
        r = _load(c, rid, ("RECEIVED",))
        if r["legal_review_status"] == "REQUIRED":
            raise TransitionBlocked([unmet("G-REFUND-LEGAL")])
        if amt < 0 or amt > r["paid_amount"]:
            raise TransitionBlocked([unmet("G-REFUND-INPUT", fields=f"0원 이상 결제액({r['paid_amount']:,}원) 이하의 환불액")])
        due = basis = None
        if r["legal_withdrawal"] or r["production_stage_at_request"] == "R2":
            # 법정 청약철회: 공급된 상품은 반환받은 날, 미공급은 철회일부터 3영업일(R2 자체 정책도 같은 기한으로 관리)
            if r["delivered_at"] and not basis_date:
                raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="상품을 반환받은 날(환급 기산일)")])
            basis = basis_date or clock.kst(clock.parse(r["requested_at"])).date().isoformat()
            hol = {h["date"] for h in c.execute("SELECT date FROM holidays").fetchall()}
            due = add_business_days(date.fromisoformat(basis), 3, hol).isoformat()
        c.execute("UPDATE refund_requests SET status='APPROVED', refund_approved_amount=?, decision_reason=?, approved_by=?, "
                  "approved_at=?, shipping_fee_bearer=COALESCE(?, shipping_fee_bearer), shipping_fee_krw=?, "
                  "refund_basis_date=?, refund_due_at=? WHERE id=?",
                  (amt, reason.strip(), actor.id, clock.now_iso(), bearer or None, fee, basis, due, rid))
        _event(c, r, "approved", actor, {"amount": amt, "reason": reason.strip(), "bearer": bearer, "shipping_fee": fee,
                                         "basis_date": basis, "due": due})


def complete(ctx, rid, actor, amount, method: str, evidence: str, completed_at: str | None = None,
             refs: list | None = None) -> None:
    """실제 환급 완료(결제 취소·이체 증거). 열린 '환불·취소 검토' 문제를 닫는다."""
    from ..domain.money import parse_won
    _admin(actor)
    miss = [lab for v, lab in ((amount, "실제 환급액"), (method, "처리 방식"), (evidence, "결제 취소·이체 증거")) if
            v in (None, "") or not str(v).strip()]
    if miss:
        raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="·".join(miss))])
    at, _src = clock.from_kst_input(completed_at)
    with ctx.db.tx() as c:
        r = _load(c, rid, ("APPROVED",))
        c.execute("UPDATE refund_requests SET status='COMPLETED', refund_completed_at=?, refund_completed_amount=?, "
                  "refund_method=?, refund_evidence=?, evidence_refs=? WHERE id=?",
                  (at, parse_won(str(amount)), method.strip(), evidence.strip(),
                   jdump(jload(r["evidence_refs"], []) + (refs or [])), rid))
        _close_issue(c, r, actor, "환급 완료")
        _event(c, r, "completed", actor, {"amount": parse_won(str(amount)), "method": method.strip(), "at": at,
                                          "late": bool(r["refund_due_at"] and clock.kst(clock.parse(at)).date().isoformat()
                                                       > r["refund_due_at"])})


def withdraw(ctx, rid, actor, note: str) -> None:
    """고객이 취소·환불 요청을 거둬들임."""
    _admin(actor)
    if not (note or "").strip():
        raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="철회 근거")])
    with ctx.db.tx() as c:
        r = _load(c, rid, ("RECEIVED", "APPROVED"))
        c.execute("UPDATE refund_requests SET status='WITHDRAWN', decision_reason=COALESCE(decision_reason, ?) WHERE id=?",
                  (note.strip(), rid))
        _close_issue(c, r, actor, "고객 요청 철회")
        _event(c, r, "withdrawn", actor, {"note": note.strip()})


def _close_issue(c, r, actor, resolution):
    if r["issue_id"]:
        c.execute("UPDATE issues SET state='RESOLVED', resolved_by=?, resolved_at=?, resolution=? WHERE id=? AND state='OPEN'",
                  (actor.id, clock.now_iso(), resolution, r["issue_id"]))
        audit.record(c, "issue", r["issue_id"], actor, "issue_resolved", order_id=r["order_id"], prev="REFUND_REVIEW",
                     reason=resolution)


def overdue(ctx) -> list:
    """환급 기한(3영업일)이 지났는데 완료되지 않은 건."""
    today = clock.today_kst().isoformat()
    return ctx.db.all("SELECT r.*, o.order_no FROM refund_requests r JOIN orders o ON o.id=r.order_id WHERE "
                      "r.status='APPROVED' AND r.refund_due_at IS NOT NULL AND r.refund_due_at<?", (today,))


def view(ctx, r) -> dict:
    """화면용: 작업 진행(사실)과 법적 검토를 나눠 보여 준다."""
    tr_ = tr  # noqa: F841 — 단계 표기는 상태 모듈 라벨 사용
    return {"r": r, "stage_label": STAGE_LABEL[r["production_stage_at_request"]],
            "guide": STAGE_GUIDE[r["production_stage_at_request"]], "legal_label": LEGAL_LABEL[r["legal_review_status"]],
            "status_label": STATUS_LABEL[r["status"]], "reason_label": REASON_LABEL[r["reason_kind"]],
            "bearer_label": BEARER_LABEL.get(r["shipping_fee_bearer"] or "", "—"),
            "late": bool(r["status"] == "APPROVED" and r["refund_due_at"] and
                         r["refund_due_at"] < clock.today_kst().isoformat()),
            "events": events_of(ctx, r["id"])}
