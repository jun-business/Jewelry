"""발주 후 고객 변경(H1 r1 Q10, SRS-170).

재작업 이슈의 귀책이 ‘고객 변심·요청 변경’이면 재작업 전에 네 단계를 거친다:
변경 가능 판정(아버님) → 추가 견적(아버님) → 고객 동의 기록 → 추가금이 있으면 결제 확인(운영자).
제품 하자·계약 사양 불일치(판매자 귀책)는 이 절차 없이 재작업 메모로 진행한다.
"""
from __future__ import annotations

from ..core import clock
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, unmet
from ..domain.money import parse_won
from . import audit

STEPS = [("decide", "변경 가능 판정"), ("quote", "추가 견적"), ("accept", "고객 동의"), ("pay", "추가 결제 확인")]


def create(c, issue_id, order_id) -> int:
    return c.execute("INSERT INTO change_orders(issue_id, order_id, created_at) VALUES (?,?,?)",
                     (issue_id, order_id, clock.now_iso())).lastrowid


def get(ctx, co_id):
    return ctx.db.one("SELECT * FROM change_orders WHERE id=?", (co_id,))


def for_issue(c, issue_id):
    return c.execute("SELECT * FROM change_orders WHERE issue_id=?", (issue_id,)).fetchone()


def payment_request(c, co):
    return c.execute("SELECT * FROM payment_requests WHERE phase='CHANGE' AND change_order_id=? AND status<>'CANCELLED' "
                     "ORDER BY id DESC LIMIT 1", (co["id"],)).fetchone()


def missing(c, co) -> list[str]:
    """해결 전에 남은 단계(라벨). 변경 불가로 판정했으면 남은 단계 없음(원래 사양으로 진행)."""
    if co["feasible"] is None:
        return ["변경 가능 판정"]
    if co["feasible"] == 0:
        return []
    out = []
    if co["amount_krw"] is None:
        out.append("추가 견적")
    if co["accepted_at"] is None:
        out.append("고객 동의")
    if (co["amount_krw"] or 0) > 0:
        req = payment_request(c, co)
        if req is None or req["status"] != "PAID_VERIFIED":
            out.append("추가 결제 확인")
    return out


def view(ctx, order_id) -> list[dict]:
    rows = ctx.db.all("SELECT co.*, i.state AS issue_state FROM change_orders co JOIN issues i ON i.id=co.issue_id "
                      "WHERE co.order_id=? ORDER BY co.id DESC", (order_id,))
    out = []
    for r in rows:
        c = ctx.db.conn
        req = payment_request(c, r)
        out.append({"co": dict(r), "missing": missing(c, r), "req": dict(req) if req else None})
    return out


def _open(ctx, co_id):
    co = get(ctx, co_id)
    if co is None:
        raise ValueError("변경 주문을 찾을 수 없어요")
    st = ctx.db.scalar("SELECT state FROM issues WHERE id=?", (co["issue_id"],))
    if st != "OPEN":
        raise TransitionBlocked([unmet("G-ENDED")])
    return co


def _maker(actor):
    if actor is None or actor.role != "maker":
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])


def _touch(c, order_id):
    c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), order_id))


def decide(ctx, co_id, actor, feasible: bool, note: str) -> None:
    _maker(actor)
    co = _open(ctx, co_id)
    if co["accepted_at"]:
        raise TransitionBlocked([unmet("G-CHANGE-ORDER-LOCKED")])
    if not (note or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    with ctx.db.tx() as c:
        c.execute("UPDATE change_orders SET feasible=?, feasibility_note=?, decided_by=?, decided_at=? WHERE id=?",
                  (int(bool(feasible)), note.strip(), actor.id, clock.now_iso(), co_id))
        audit.record(c, "change_order", co_id, actor, "change_feasibility", order_id=co["order_id"],
                     next="가능" if feasible else "불가", reason=note.strip())
        _touch(c, co["order_id"])


def quote(ctx, co_id, actor, amount, description: str) -> None:
    _maker(actor)
    co = _open(ctx, co_id)
    if co["feasible"] != 1:
        raise TransitionBlocked([unmet("G-CHANGE-ORDER-STEP", step="변경 가능 판정(가능)")])
    if co["accepted_at"]:
        raise TransitionBlocked([unmet("G-CHANGE-ORDER-LOCKED")])
    amt = parse_won(amount)
    um = []
    if amt is None or amt < 0:
        um.append(unmet("G-ISSUE-RESOLVE", fields="추가 금액(없으면 0)"))
    if not (description or "").strip():
        um.append(unmet("G-ISSUE-RESOLVE", fields="변경 내용"))
    if um:
        raise TransitionBlocked(um)
    with ctx.db.tx() as c:
        c.execute("UPDATE change_orders SET amount_krw=?, description=?, quoted_by=?, quoted_at=? WHERE id=?",
                  (amt, description.strip(), actor.id, clock.now_iso(), co_id))
        audit.record(c, "change_order", co_id, actor, "change_quoted", order_id=co["order_id"], detail={"amount": amt})
        _touch(c, co["order_id"])


def accept(ctx, co_id, actor, channel: str, evidence: str, accepted_at: str | None = None) -> None:
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])
    co = _open(ctx, co_id)
    if co["feasible"] != 1 or co["amount_krw"] is None:
        raise TransitionBlocked([unmet("G-CHANGE-ORDER-STEP", step="추가 견적")])
    if not ((channel or "").strip() and (evidence or "").strip()):
        raise TransitionBlocked([unmet("G-ACCEPT-EVIDENCE")])
    at, _ = clock.from_kst_input(accepted_at)
    with ctx.db.tx() as c:
        c.execute("UPDATE change_orders SET accept_channel=?, accept_evidence=?, accepted_at=?, accept_recorded_by=? "
                  "WHERE id=?", (channel.strip(), evidence.strip(), at, actor.id, co_id))
        audit.record(c, "change_order", co_id, actor, "change_accepted", order_id=co["order_id"],
                     detail={"channel": channel.strip(), "amount": co["amount_krw"]})
        _touch(c, co["order_id"])


def create_payment_request(ctx, co_id, actor) -> int:
    from . import payments
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    co = _open(ctx, co_id)
    if co["accepted_at"] is None:
        raise TransitionBlocked([unmet("G-CHANGE-ORDER-STEP", step="고객 동의")])
    if not co["amount_krw"]:
        raise ValueError("추가 금액이 0원이라 결제 요청이 필요 없어요")
    with ctx.db.tx() as c:
        if payment_request(c, co):
            raise ValueError("이 변경의 결제 요청이 이미 있어요")
        o = c.execute("SELECT * FROM orders WHERE id=?", (co["order_id"],)).fetchone()
        q = tr.latest_quote(c, o["id"], "APPROVED")
        rid = payments._new_request(ctx, c, o, q, "CHANGE", co["amount_krw"], actor, change_order_id=co["id"])
        _touch(c, o["id"])
        return rid


def verified_total(c, order_id) -> int:
    """확인된 변경 추가금 합(실적 매출에 더함)."""
    total = 0
    for r in c.execute("SELECT id FROM payment_requests WHERE order_id=? AND phase='CHANGE' AND status='PAID_VERIFIED'",
                       (order_id,)).fetchall():
        total += tr.paid_sum(c, r["id"])
    return total
