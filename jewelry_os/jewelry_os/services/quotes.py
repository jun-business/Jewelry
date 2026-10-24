"""시안·견적·고객 동의·재견적(FUN-005, BD §6.5, DD-004)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..core import clock
from ..core.db import jdump, jload
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, unmet
from ..domain.money import parse_won, split
from ..domain.states import CONTRACT_SPEC_FIELDS, PHASE_LABEL, PHASE_OF, QUOTE_TERMS_FIELDS, S, SPEC_FIELDS
from . import audit, files, orders, settings

ITEM_KINDS = {"metal": "금속", "stone": "스톤", "engraving": "각인", "design": "설계비", "factory": "외주 제작비",
              "other": "기타"}


def _maker(actor):
    if actor is None or actor.role != "maker":
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])


# ---------------- 시안 ----------------
def upload_concept(ctx, o, actor, filename, data, notes, minutes) -> int:
    _maker(actor)
    with ctx.db.tx() as c:
        v = (c.execute("SELECT COALESCE(MAX(version),0) FROM concepts WHERE order_id=?", (o["id"],)).fetchone()[0]) + 1
        aid = files.store(ctx, c, order_id=o["id"], order_no=o["order_no"], kind="concept", filename=filename, data=data,
                          actor=actor, version=v)
        c.execute("INSERT INTO concepts(order_id, version, asset_id, notes, work_minutes, created_by, created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (o["id"], v, aid, notes, int(minutes or 0), actor.id, clock.now_iso()))
        if int(minutes or 0) > 0:
            c.execute("INSERT INTO work_logs(order_id, kind, minutes, work_date, note, created_by, created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (o["id"], "concept", int(minutes), clock.today_kst().isoformat(),
                                                 f"시안 v{v}", actor.id, clock.now_iso()))
        audit.record(c, "concept", f"{o['order_no']}:v{v}", actor, "concept_uploaded", order_id=o["id"], ref_version=v)
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))
        return v


def concepts_of(ctx, order_id):
    return ctx.db.all("SELECT c.*, a.filename, a.file_hash FROM concepts c LEFT JOIN assets a ON a.id=c.asset_id "
                      "WHERE c.order_id=? ORDER BY c.version DESC", (order_id,))


# ---------------- 견적 ----------------
def draft(ctx, o, actor) -> int:
    """새 초안. 승인본이 있으면 그 내용을 복제(새 버전)."""
    _maker(actor)
    with ctx.db.tx() as c:
        d = c.execute("SELECT id FROM quotes WHERE order_id=? AND status='DRAFT'", (o["id"],)).fetchone()
        if d:
            return d["id"]
        last = tr.latest_quote(c, o["id"])
        if last is not None:
            return clone_as_draft(c, last, actor)
        spec = orders.current_spec(ctx, o["id"])
        v = 1
        valid = (clock.today_kst() + timedelta(days=int(settings.get(ctx, "quote_valid_days")))).isoformat()
        cur = c.execute("INSERT INTO quotes(order_id, version, status, spec_json, payment_policy, valid_until, created_by, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (o["id"], v, "DRAFT", jdump({k: spec.get(k) or "" for k, _ in SPEC_FIELDS}),
                         o["payment_policy"], valid, actor.id, clock.now_iso()))
        return cur.lastrowid


def clone_as_draft(c, q, actor) -> int:
    v = (c.execute("SELECT MAX(version) FROM quotes WHERE order_id=?", (q["order_id"],)).fetchone()[0]) + 1
    cols = ("spec_json, spec_summary, tax_included, shipping_included, total_krw, payment_policy, promised_date, "
            "valid_until, cost_estimate_krw, variable_cost_krw, design_minutes_est, internal_note")
    cur = c.execute(f"INSERT INTO quotes(order_id, version, status, {cols}, created_by, created_at) "
                    f"SELECT order_id, ?, 'DRAFT', {cols}, ?, ? FROM quotes WHERE id=?",
                    (v, getattr(actor, "id", None), clock.now_iso(), q["id"]))
    nid = cur.lastrowid
    c.execute("INSERT INTO quote_items(quote_id, seq, kind, label_public, amount_krw, internal_cost_krw, note_internal) "
              "SELECT ?, seq, kind, label_public, amount_krw, internal_cost_krw, note_internal FROM quote_items "
              "WHERE quote_id=?", (nid, q["id"]))
    return nid


@dataclass
class QuoteForm:
    items: list[dict] = field(default_factory=list)  # kind,label,amount,cost,note
    spec: dict = field(default_factory=dict)
    spec_summary: str = ""
    tax_included: bool | None = None
    shipping_included: bool | None = None
    promised_date: str | None = None
    valid_until: str | None = None
    variable_cost: int = 0
    design_minutes: int = 0
    internal_note: str = ""


def save(ctx, quote_id, actor, f: QuoteForm) -> None:
    _maker(actor)
    with ctx.db.tx() as c:
        q = c.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if q is None or q["status"] != "DRAFT":
            raise ImmutableQuote()
        c.execute("DELETE FROM quote_items WHERE quote_id=?", (quote_id,))
        total = cost = 0
        for i, it in enumerate(f.items):
            if not (it.get("label") or "").strip() and not it.get("amount"):
                continue
            amt = parse_won(it.get("amount")) or 0
            ic = parse_won(it.get("cost")) or 0
            total += amt
            cost += ic
            c.execute("INSERT INTO quote_items(quote_id, seq, kind, label_public, amount_krw, internal_cost_krw, "
                      "note_internal) VALUES (?,?,?,?,?,?,?)", (quote_id, i, it.get("kind") or "other",
                                                                (it.get("label") or ITEM_KINDS.get(it.get("kind"), "기타")).strip(),
                                                                amt, ic, it.get("note") or None))
        dep, bal = split(total, q["payment_policy"])
        c.execute("UPDATE quotes SET spec_json=?, spec_summary=?, tax_included=?, shipping_included=?, total_krw=?, "
                  "deposit_krw=?, balance_krw=?, promised_date=?, valid_until=?, cost_estimate_krw=?, variable_cost_krw=?, "
                  "design_minutes_est=?, internal_note=? WHERE id=?",
                  (jdump({k: (f.spec.get(k) or "").strip() for k, _ in SPEC_FIELDS + CONTRACT_SPEC_FIELDS
                          + QUOTE_TERMS_FIELDS}),
                   f.spec_summary or None,
                   None if f.tax_included is None else int(f.tax_included),
                   None if f.shipping_included is None else int(f.shipping_included), total, dep, bal,
                   f.promised_date or None, f.valid_until or None, cost, int(f.variable_cost or 0),
                   int(f.design_minutes or 0), f.internal_note or None, quote_id))


# ---------------- D-35 청약철회 예외 고지의 구체성 ----------------
# 전자상거래법 시행령 제21조: ‘이 거래’에 대해 개별 생산·회복할 수 없는 중대한 피해를 별도로 고지해야 한다.
# 그래서 주문마다 이 주문만의 사정(사이즈·각인·디자인·소재·설계·발주 비용)이 들어가야 한다.
SPECIFIC_WORDS = ("호", "사이즈", "각인", "이니셜", "문구", "디자인", "시안", "CAD", "설계", "스톤", "보석", "다이아",
                  "소재", "금", "K", "함량", "가공", "주조", "공장", "발주", "원가", "형상", "모양", "맞춤")
BLANKET_WORDS = ("환불 불가", "환불불가", "반품 불가", "교환 불가", "취소 불가", "일체 환불", "절대")
MIN_LOSS_LEN = 25


def loss_reason_problems(spec: dict) -> list[str]:
    """손해 사유·개별 제작 특성이 구체적인지(규칙 검사). 반환: 고칠 점(없으면 [])."""
    out = []
    custom = (spec.get("custom_made") or "").strip()
    loss = (spec.get("loss_reasons") or "").strip()
    if not custom:
        out.append("개별 주문제작 특성이 비어 있어요")
    if not loss:
        out.append("청약철회 시 예상 손해 사유가 비어 있어요")
        return out
    if len(loss) < MIN_LOSS_LEN:
        out.append(f"손해 사유가 너무 짧아요({len(loss)}자) — 이 주문만의 사정을 {MIN_LOSS_LEN}자 이상으로 적어 주세요")
    if not any(w in loss for w in SPECIFIC_WORDS):
        out.append("손해 사유에 이 주문의 구체적인 사정(사이즈·각인·디자인·소재·설계·발주 비용 등)이 없어요")
    if any(w in loss + custom for w in BLANKET_WORDS):
        out.append("‘환불 불가’ 같은 일괄 제한 표현은 쓰지 마세요 — 법정 요건을 충족할 때만 제한될 수 있어요")
    mentioned = [v for k, v in spec.items() if k in ("size", "engraving", "stones") and (v or "").strip()
                 and v.strip() not in ("없음", "해당 없음", "-")]
    missing = [v.strip() for v in mentioned if v.strip() not in loss + custom]
    if missing:
        out.append("사양에 적은 사이즈·각인·스톤 값(" + ", ".join(missing) + ")이 개별 제작 특성이나 손해 사유에 들어 있지 않아요")
    return out


def suggest_loss_text(spec: dict, product_type: str | None) -> dict:
    """이 주문 사양으로 만든 예시 문구(그대로 쓰지 말고 고쳐 쓰도록 안내)."""
    v = lambda k: (spec.get(k) or "").strip()  # noqa: E731
    bits = [x for x in (v("size") and f"{v('size')} 맞춤 사이즈",
                        v("engraving") not in ("", "없음") and f"각인 ‘{v('engraving')}’",
                        v("stones") not in ("", "없음") and f"스톤 {v('stones')}",
                        (v("material") or v("purity")) and f"{v('material')} {v('purity')}".strip() + " 소재") if x]
    item = product_type or "주얼리"
    custom = f"고객님이 요청한 디자인({', '.join(bits) if bits else '세부 사양'})으로 이 고객님만을 위해 1점 제작하는 {item}이에요."
    loss = (f"{', '.join(bits) if bits else '고객님 맞춤 사양'}이 들어가 다른 고객에게 판매하거나 되돌려 쓰기 어렵고, "
            "상세 CAD 설계 작업과 공장 발주 뒤의 금속·가공 비용은 회수하기 어려워요.")
    return {"custom_made": custom, "loss_reasons": loss}


class ImmutableQuote(Exception):
    """승인된 견적 수정 시도(405)."""


def items_of(ctx, quote_id):
    return ctx.db.all("SELECT * FROM quote_items WHERE quote_id=? ORDER BY seq", (quote_id,))


def _approve_effect(ctx, actor, requote=False):
    def effect(c, o):
        q = tr.latest_quote(c, o["id"], "DRAFT")
        terms = settings.approved_policy(ctx, "terms")
        is_test = int(not settings.quote_gate_passed(ctx))
        prev = tr.latest_quote(c, o["id"], "APPROVED")
        if prev is not None:
            c.execute("UPDATE quotes SET status='SUPERSEDED' WHERE id=?", (prev["id"],))
        from . import contracts  # 새 견적 버전 = 새 계약 동의(이전 동의 재사용 금지, D-24)
        contracts.supersede_for_quote(c, o["id"], q["id"], actor, f"견적 v{q['version']} 승인")
        c.execute("UPDATE quotes SET status='APPROVED', approved_by=?, approved_at=?, is_test=?, terms_policy_id=?, "
                  "terms_version=? WHERE id=?", (actor.id, clock.now_iso(), is_test, terms["id"] if terms else None,
                                                  terms["version"] if terms else None, q["id"]))
        c.execute("UPDATE orders SET promised_date=? WHERE id=?", (q["promised_date"], o["id"]))
        if requote and o["lifecycle_state"] == S.PAYMENT_PENDING.value:
            c.execute("UPDATE payment_requests SET status='CANCELLED' WHERE order_id=? AND status IN ('DRAFT','LINK_READY')"
                      " AND quote_version<>?", (o["id"], q["version"]))
            # 미결제 요청이 취소되면 단계는 결제 대기로 남고, operator가 새 버전 기준 요청을 다시 만든다(BD §6.5)
        audit.record(c, "quote", f"{o['order_no']}:v{q['version']}", actor, "requote_approved" if requote else
                     "quote_approved", order_id=o["id"], ref_version=q["version"],
                     detail={"total": q["total_krw"], "is_test": is_test})
    return effect


def approve(ctx, o, actor, payload):
    return tr.perform(ctx, o["id"], "approve_quote", actor, payload, expected_version=payload.get("version"),
                      effect=_approve_effect(ctx, actor))


def change_request_open(c, order_id):
    return c.execute("SELECT * FROM issues WHERE order_id=? AND type='CHANGE_REQUEST' AND state='OPEN'",
                     (order_id,)).fetchone()


def approve_requote(ctx, o, actor, payload):
    """변경 요청 중 새 견적 버전 승인(비전이 — 단계 유지)."""
    _maker(actor)
    with ctx.db.tx() as c:
        if not change_request_open(c, o["id"]):
            raise TransitionBlocked([unmet("G-NOT-OPEN-CHANGE")])
        q = tr.latest_quote(c, o["id"], "DRAFT")
        if q is None:
            raise TransitionBlocked([unmet("G-QUOTE-MISSING")])
        um = []
        miss = tr.quote_missing_fields(c, q)
        if miss:
            um.append(unmet("G-QUOTE-FIELDS", fields="·".join(miss)))
        if not payload.get("confirm"):
            um.append(unmet("G-CONFIRM"))
        if um:
            raise TransitionBlocked(um)
        cur = c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        _approve_effect(ctx, actor, requote=True)(c, cur)
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))


def _record_accept(c, o, actor, payload, kind="quote", quote=None, design_version_id=None):
    at, src = clock.from_kst_input(payload.get("accepted_at"))
    c.execute("INSERT INTO customer_acceptances(order_id, kind, quote_id, quote_version, design_version_id, terms_version, "
              "channel, evidence_text, accepted_at, accepted_at_src, recorded_by, recorded_at) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (o["id"], kind, quote["id"] if quote else None, quote["version"] if quote else None, design_version_id,
               quote["terms_version"] if quote else None, payload["channel"], payload["evidence"],
               at, src, actor.id, clock.now_iso()))


def record_acceptance(ctx, o, actor, payload):
    def effect(c, cur):
        q = tr.latest_quote(c, o["id"], "APPROVED")
        _record_accept(c, cur, actor, payload, "quote", q)
    return tr.perform(ctx, o["id"], "record_acceptance", actor, payload, expected_version=payload.get("version"),
                      effect=effect)


def record_reacceptance(ctx, o, actor, payload):
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])
    if not ((payload.get("channel") or "").strip() and (payload.get("evidence") or "").strip()):
        raise TransitionBlocked([unmet("G-ACCEPT-EVIDENCE")])
    with ctx.db.tx() as c:
        cr = change_request_open(c, o["id"])
        if not cr:
            raise TransitionBlocked([unmet("G-NOT-OPEN-CHANGE")])
        q = tr.latest_quote(c, o["id"], "APPROVED")
        prior = c.execute("SELECT 1 FROM customer_acceptances WHERE order_id=? AND kind='quote' AND quote_id=?",
                          (o["id"], q["id"])).fetchone()
        if prior or q["approved_at"] < cr["opened_at"]:
            raise TransitionBlocked([unmet("G-QUOTE-MISSING")])  # 변경 요청 뒤 새로 승인된 견적이 있어야 함
        _record_accept(c, o, actor, payload, "quote", q)
        c.execute("UPDATE issues SET state='RESOLVED', resolved_by=?, resolved_at=?, resolution=? WHERE id=?",
                  (actor.id, clock.now_iso(), f"새 견적 v{q['version']} 고객 재동의", cr["id"]))
        audit.record(c, "issue", cr["id"], actor, "issue_resolved", order_id=o["id"], prev="CHANGE_REQUEST",
                     reason=f"견적 v{q['version']} 재동의")
        c.execute("UPDATE orders SET version=version+1, updated_at=? WHERE id=?", (clock.now_iso(), o["id"]))


# ---------------- 고객 문서(BD-007) ----------------
@dataclass(frozen=True)
class PublicItem:
    label: str
    amount: int


@dataclass(frozen=True)
class PublicQuote:
    """고객에게 나가는 견적 — 허용 목록 필드만(SRS-137). 내부 필드를 담을 수 없다."""
    shop_name: str
    order_no: str
    customer_name: str
    version: int
    issued_at: str
    valid_until: str | None
    product_type: str | None
    spec: dict
    spec_summary: str | None
    items: tuple
    total: int
    tax_included: bool
    shipping_included: bool
    deposit: int | None
    balance: int | None
    payment_policy: str
    promised_date: str | None
    terms_label: str | None
    phase_label: str
    is_test: bool
    design_asset_id: int | None = None   # D-34 최신 시안 이미지(고객 문서에 표시)
    design_label: str | None = None
    revision_scope: str | None = None
    delivery_plan: str | None = None
    refund_terms: str | None = None      # Form B ‘제작 단계별 취소·환불 안내’와 같은 원문
    terms_body: str | None = None        # 승인된 수정·환불 정책 본문

    ALLOWED = ("shop_name", "order_no", "customer_name", "version", "issued_at", "valid_until", "product_type", "spec",
               "spec_summary", "items", "total", "tax_included", "shipping_included", "deposit", "balance",
               "payment_policy", "promised_date", "terms_label", "phase_label", "is_test", "design_asset_id",
               "design_label", "revision_scope", "delivery_plan", "refund_terms", "terms_body")


def public_text(pq: PublicQuote) -> str:
    """고객 견적 원문(날짜는 절대 표기) — 계약 증거 스냅숏에 남긴다."""
    from ..domain.money import won
    lines = [f"[{pq.shop_name}] 견적서 v{pq.version}", f"주문번호 {pq.order_no} · {pq.customer_name} 님",
             f"발행 {pq.issued_at} · 유효기간 {pq.valid_until or '-'}까지"]
    if pq.is_test:
        lines.insert(0, "※ 시험 견적 — 실제 결제용 아님")
    lines.append(f"제품: {pq.product_type or '-'}")
    labels = dict(SPEC_FIELDS + CONTRACT_SPEC_FIELDS)
    lines += [f"{labels.get(k, k)}: {v}" for k, v in pq.spec.items() if k in labels]
    if pq.spec_summary:
        lines.append(pq.spec_summary)
    lines += [f"· {it.label}: {won(it.amount)}" for it in pq.items]
    lines.append(f"총액: {won(pq.total)} ({'부가세 포함' if pq.tax_included else '부가세 별도'}, "
                 f"{'배송비 포함' if pq.shipping_included else '배송비 별도'})")
    lines.append(f"결제: 전액 {won(pq.total)}" if pq.payment_policy == "FULL" else
                 f"결제: 예약금 {won(pq.deposit)}(계약 동의 후·상세 CAD 시작 전) · "
                 f"잔금 {won(pq.balance)}(최종 CAD 승인 후·제작 발주 전)")
    lines.append(f"완성 예정: {pq.promised_date or '-'}")
    if pq.design_label:
        lines.append(f"디자인: {pq.design_label}" + ("" if pq.design_asset_id else "(파일 별도 전달)"))
    if pq.delivery_plan:
        lines.append(f"배송 일정: {pq.delivery_plan}")
    if pq.revision_scope:
        lines.append(f"수정 가능 범위: {pq.revision_scope}")
    if pq.refund_terms:
        lines += ["[취소·환불 조건]", pq.refund_terms]
    if pq.terms_label:
        lines.append(f"수정·환불 기준: {pq.terms_label}")
    return "\n".join(lines)


def public_quote(ctx, quote_id) -> PublicQuote | None:
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (quote_id,))
    if q is None or q["status"] == "DRAFT":
        return None
    o = ctx.db.one("SELECT o.*, c.display_name AS customer_name FROM orders o JOIN customers c ON c.id=o.customer_id "
                   "WHERE o.id=?", (q["order_id"],))
    terms = ctx.db.one("SELECT * FROM policies WHERE id=?", (q["terms_policy_id"],)) if q["terms_policy_id"] else None
    items = tuple(PublicItem(i["label_public"], i["amount_krw"]) for i in items_of(ctx, quote_id))
    spec = jload(q["spec_json"], {})
    concept = ctx.db.one("SELECT c.version, a.id, a.mime FROM concepts c JOIN assets a ON a.id=c.asset_id WHERE c.order_id=? "
                         "AND c.created_at<=? ORDER BY c.version DESC LIMIT 1", (q["order_id"], q["approved_at"]))
    from . import contracts  # 지연 import(순환 방지)
    refund = contracts.policy_section(contracts.current_form_body(ctx), "refund")
    return PublicQuote(
        shop_name=settings.get(ctx, "shop_name"), order_no=o["order_no"], customer_name=o["customer_name"] or "고객",
        version=q["version"], issued_at=q["approved_at"], valid_until=q["valid_until"], product_type=o["product_type"],
        spec={k: v for k, v in jload(q["spec_json"], {}).items() if v}, spec_summary=q["spec_summary"], items=items,
        total=q["total_krw"], tax_included=bool(q["tax_included"]), shipping_included=bool(q["shipping_included"]),
        deposit=q["deposit_krw"], balance=q["balance_krw"], payment_policy=q["payment_policy"],
        promised_date=q["promised_date"], terms_label=f"{terms['title']} v{terms['version']}" if terms else None,
        phase_label=PHASE_LABEL[PHASE_OF[S(o["lifecycle_state"])]], is_test=bool(q["is_test"]),
        design_asset_id=concept["id"] if concept and (concept["mime"] or "").startswith("image/") else None,
        design_label=f"시안 v{concept['version']}" if concept else None,
        revision_scope=(spec.get("revision_scope") or "").strip() or settings.get(ctx, "quote_revision_scope_default"),
        delivery_plan=(spec.get("delivery_plan") or "").strip() or settings.get(ctx, "quote_delivery_default"),
        refund_terms=refund["text"] or None, terms_body=terms["body"] if terms else None)
