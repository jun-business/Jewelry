"""고객 이력·연결·개인정보 삭제(IMPLICIT-14, SRS-084·158)."""
from __future__ import annotations

import re

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


def tail_digits(q) -> str | None:
    """검색어가 연락처 뒷자리 4자리면 그 숫자(채널 ‘진행 중인 주문’ 안내: 성함 + 뒷자리 4자리)."""
    q = (q or "").strip()
    return q if re.fullmatch(r"\d{4}", q) else None


def ids_by_contact_tail(ctx, digits: str) -> list[int]:
    """연락처 뒷자리로 고객 찾기 — 연락처는 암호화돼 있어 풀어서 비교한다(파기된 고객 제외)."""
    out = []
    for r in ctx.db.all("SELECT id, contact_enc FROM customers WHERE contact_enc IS NOT NULL AND deleted_at IS NULL"):
        if re.sub(r"\D", "", ctx.dec(r["contact_enc"]) or "").endswith(digits):
            out.append(r["id"])
    return out


def list_customers(ctx, q=None):
    w, a = "c.deleted_at IS NULL AND c.merged_into IS NULL", []
    if tail_digits(q):
        roots = sorted({root_id(ctx.db.conn, i) for i in ids_by_contact_tail(ctx, q.strip())})
        w += f" AND (c.display_name LIKE ? OR c.id IN ({','.join('?' * len(roots)) or 'NULL'}))"
        a += [f"%{q.strip()}%", *roots]
    elif q:
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


AUTO_LINK_REASON = "이름·전화번호 일치 — 자동 연결"


def norm_name(v: str | None) -> str:
    return re.sub(r"\s+", "", v or "")


def norm_phone(v: str | None) -> str | None:
    """전화번호만 비교(숫자만, +82 → 0). 전화번호 모양이 아니면(카카오 ID 등) None — 자동 연결하지 않는다."""
    d = re.sub(r"\D", "", v or "")
    if d.startswith("82") and len(d) >= 11:
        d = "0" + d[2:]
    return d if d.startswith("0") and 9 <= len(d) <= 11 else None


def same_person(ctx, c, cid) -> int | None:
    """이름·전화번호가 같은 다른 고객 묶음의 대표 ID(없으면 None). 삭제된 고객은 대상이 아니다."""
    me = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    name, phone = norm_name(me["display_name"]), norm_phone(ctx.dec(me["contact_enc"]))
    if not name or name == "(이름없음)" or not phone or me["deleted_at"]:
        return None
    mine = root_id(c, cid)
    for r in c.execute("SELECT * FROM customers WHERE id<>? AND deleted_at IS NULL AND REPLACE(display_name, ' ', '')=? "
                       "ORDER BY id", (cid, name)).fetchall():
        if norm_phone(ctx.dec(r["contact_enc"])) == phone and root_id(c, r["id"]) != mine:
            return root_id(c, r["id"])
    return None


def auto_link(ctx, c, cid) -> int:
    """새 고객이 기존 고객과 이름·전화번호가 같으면 그 고객 묶음에 자동 연결. 반환: 주문에 쓸 고객 ID."""
    target = same_person(ctx, c, cid)
    if target is None:
        return cid
    _link(c, target, root_id(c, cid), None, AUTO_LINK_REASON)
    return target


def auto_link_existing(ctx) -> int:
    """이미 있는 중복 고객을 한 번 정리(오래된 고객 쪽으로). 반환: 연결한 수."""
    n = 0
    with ctx.db.tx() as c:
        for r in c.execute("SELECT id FROM customers WHERE merged_into IS NULL AND deleted_at IS NULL ORDER BY id").fetchall():
            if root_id(c, r["id"]) == r["id"] and auto_link(ctx, c, r["id"]) != r["id"]:
                n += 1
    return n


def link(ctx, cid, other_id, actor, reason):
    """수동 “같은 고객으로 연결”. other → cid 로 합침(이름·전화번호가 같으면 접수 때 자동으로도 연결)."""
    if not (reason or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    if cid == other_id:
        raise ValueError("같은 고객이에요")
    with ctx.db.tx() as c:
        _link(c, cid, other_id, actor, reason)


def _link(c, cid, other_id, actor, reason):
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
    if actor is None or actor.role not in ("maker", "operator"):  # D-43
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    if not (reason or "").strip():
        raise TransitionBlocked([unmet("G-REASON")])
    with ctx.db.tx() as c:
        erase_in_tx(ctx, c, cid, actor, reason)


def erase_in_tx(ctx, c, cid, actor, reason):
    """개인정보 파기 본체 — 운영자 요청(erase)과 보관기간 만료 정리(retention, actor=None)가 함께 쓴다."""
    from ..core.db import jdump
    ids = group_ids(c, cid)  # 연결된 고객 기록 모두(자동 연결된 같은 사람의 이전 기록 포함)
    marks = ",".join("?" * len(ids))
    c.execute(f"UPDATE customers SET display_name='삭제된 고객', contact_enc=NULL, deleted_at=? WHERE id IN ({marks})",
              (clock.now_iso(), *ids))
    c.execute(f"DELETE FROM customer_identities WHERE customer_id IN ({marks})", ids)  # 확인된 계약 이메일
    for o in c.execute(f"SELECT id, intake_id FROM orders WHERE customer_id IN ({marks})", ids).fetchall():
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
