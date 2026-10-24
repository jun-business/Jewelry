"""고객 이력·연결·개인정보 삭제(IMPLICIT-14, SRS-084·158)."""
from __future__ import annotations

from ..core import clock
from ..core.db import jdump, jload
from ..domain.guards import TransitionBlocked, unmet
from . import audit


def masked(value: str | None) -> str:
    if not value:
        return "—"
    v = value.strip()
    if len(v) <= 4:
        return "****"
    return v[:3] + "-****-" + v[-4:] if v[:3].isdigit() else v[:2] + "****" + v[-2:]


def root_id(c, cid) -> int:
    """연결(merged_into)을 따라간 대표 고객 ID."""
    seen = set()
    while cid not in seen:
        seen.add(cid)
        r = c.execute("SELECT merged_into FROM customers WHERE id=?", (cid,)).fetchone()
        if r is None or r["merged_into"] is None:
            return cid
        cid = r["merged_into"]
    return cid


def group_ids(c, cid) -> list[int]:
    """‘같은 고객으로 연결’된 고객 묶음(대표 + 연결된 모든 고객). 계약 증거의 고객 ID는 그대로 두고 묶음으로 본다."""
    root = root_id(c, cid)
    return [r["id"] for r in c.execute(
        "WITH RECURSIVE g(id) AS (SELECT ? UNION SELECT cu.id FROM customers cu JOIN g ON cu.merged_into=g.id) "
        "SELECT id FROM g ORDER BY id", (root,)).fetchall()]


def list_customers(ctx, q=None):
    w, a = "c.deleted_at IS NULL AND c.merged_into IS NULL", []
    if q:
        w += " AND c.display_name LIKE ?"
        a.append(f"%{q}%")
    return ctx.db.all(f"SELECT c.*, COUNT(o.id) AS n_orders, MAX(o.created_at) AS last_order FROM customers c "
                      f"LEFT JOIN orders o ON o.customer_id=c.id WHERE {w} GROUP BY c.id ORDER BY last_order DESC", a)


def detail(ctx, cid):
    c = ctx.db.one("SELECT * FROM customers WHERE id=?", (cid,))
    if c is None:
        return None
    ids = group_ids(ctx.db.conn, cid)
    marks = ",".join("?" * len(ids))
    orders = ctx.db.all(f"SELECT * FROM orders WHERE customer_id IN ({marks}) ORDER BY created_at DESC", ids)
    last_spec = {}
    for o in orders:
        r = ctx.db.one("SELECT spec_json FROM order_specs WHERE order_id=? AND revision>0 ORDER BY revision DESC LIMIT 1",
                       (o["id"],))
        if r:
            last_spec = jload(r["spec_json"], {})
            break
        q = ctx.db.one("SELECT spec_json FROM quotes WHERE order_id=? AND status='APPROVED' ORDER BY version DESC LIMIT 1",
                       (o["id"],))
        if q:
            last_spec = jload(q["spec_json"], {})
            break
    contact = ctx.dec(c["contact_enc"]) if c["contact_enc"] else None
    return {"customer": c, "orders": orders, "last_spec": last_spec, "contact_masked": masked(contact)}


def reveal_contact(ctx, cid, actor) -> str | None:
    c = ctx.db.one("SELECT * FROM customers WHERE id=?", (cid,))
    with ctx.db.tx() as cur:
        audit.record(cur, "customer", cid, actor, "contact_revealed", prev=audit.PII)
    return ctx.dec(c["contact_enc"]) if c["contact_enc"] else None


def link(ctx, cid, other_id, actor, reason):
    """수동 “같은 고객으로 연결”(자동 병합 없음). other → cid 로 합침."""
    if not (reason or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    if cid == other_id:
        raise ValueError("같은 고객이에요")
    with ctx.db.tx() as c:
        c.execute("UPDATE customers SET merged_into=? WHERE id=?", (cid, other_id))
        c.execute("UPDATE orders SET customer_id=? WHERE customer_id=?", (cid, other_id))
        # 계약 증거의 고객 ID는 바꾸지 않고, 연결 사실을 각 계약 이력에 남긴다(고객 묶음으로 조회·검증)
        for r in c.execute("SELECT id, order_id FROM contract_requests WHERE customer_id=?", (other_id,)).fetchall():
            c.execute("INSERT INTO contract_events(request_id, order_id, event, actor_id, actor_role, detail_json, created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (r["id"], r["order_id"], "customer_linked", getattr(actor, "id", None),
                                                 getattr(actor, "role", "system"),
                                                 jdump({"from": other_id, "to": cid, "reason": reason}), clock.now_iso()))
        audit.record(c, "customer", cid, actor, "customer_linked", prev=str(other_id), next=str(cid), reason=reason)


def erase(ctx, cid, actor, reason):
    """개인정보 삭제 요청(SRS-084): 연락처·표시명·폼 원문 개인정보 파기, 주문·금전 기록은 법정 보존."""
    if actor is None or actor.role != "operator":
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    if not (reason or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    from ..core.db import jdump
    with ctx.db.tx() as c:
        c.execute("UPDATE customers SET display_name='삭제된 고객', contact_enc=NULL, deleted_at=? WHERE id=?",
                  (clock.now_iso(), cid))
        for o in c.execute("SELECT id, intake_id FROM orders WHERE customer_id=?", (cid,)).fetchall():
            if o["intake_id"]:
                for rv in c.execute("SELECT id, answers_json FROM intake_revisions WHERE intake_id=?",
                                    (o["intake_id"],)).fetchall():
                    a = jload(rv["answers_json"], {})
                    for k in ("display_name", "contact_value", "contact_kind"):
                        if k in a:
                            a[k] = None
                    c.execute("UPDATE intake_revisions SET answers_json=? WHERE id=?", (jdump(a), rv["id"]))
            c.execute("UPDATE shipments SET recipient_enc=NULL WHERE order_id=?", (o["id"],))
            c.execute("UPDATE cs_messages SET content_redacted=NULL, summary='(삭제된 고객의 메시지)' WHERE thread_id IN "
                      "(SELECT id FROM cs_threads WHERE order_id=?)", (o["id"],))
        from .intake import UNMAPPED_META  # 미인식 질문 예시값도 비운다(어느 응답의 값인지 남기지 않으므로 전부)
        c.execute("DELETE FROM meta WHERE key=?", (UNMAPPED_META,))
        audit.record(c, "customer", cid, actor, "customer_erased", prev=audit.PII, next=audit.PII, reason=reason)
