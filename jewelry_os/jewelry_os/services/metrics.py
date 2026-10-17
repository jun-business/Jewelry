"""실적·KPI(UI-09, SRS-089·124·157·166·168)."""
from __future__ import annotations

from ..core import clock
from ..domain.states import INVALID_DECLINE, SOURCE_LABEL

SMALL_N = 5


def _month_of(iso: str) -> str:
    return clock.kst(clock.parse(iso)).strftime("%Y-%m")


def acquisition_cost(ctx, order) -> int:
    """SRS-168 ③: 그 달 그 경로 광고비 ÷ 그 달 그 경로 문의 수."""
    ch = order["source_channel"]
    if not ch:
        return 0
    m = _month_of(order["created_at"])
    spend = ctx.db.scalar("SELECT amount_krw FROM ad_spend WHERE month=? AND channel=?", (m, ch)) or 0
    if not spend:
        return 0
    n = sum(1 for r in ctx.db.all("SELECT created_at FROM orders WHERE source_channel=?", (ch,))
            if _month_of(r["created_at"]) == m)
    return spend // n if n else 0


def revenue_ex_tax(ctx, gross: int, tax_included) -> int | None:
    """H1 r1 Q9: 세금 제외 매출을 사업자 과세 유형으로 계산(무조건 ÷1.1 하지 않음). 미설정이면 None(미산정)."""
    from . import settings
    if not tax_included:
        return gross
    t = settings.get(ctx, "tax_type")
    if t == "general":            # 일반과세: 공급가액 = 공급대가 ÷ 1.1
        return gross * 10 // 11
    if t == "simplified":         # 간이과세: 부가세 = 공급대가 × 업종 부가가치율 × 10%
        rate = int(settings.get(ctx, "simplified_vat_rate_pct"))
        return gross - gross * rate // 1000
    if t == "exempt":             # 면세: 부가세 없음
        return gross
    return None


def contribution(ctx, order) -> dict | None:
    q = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='APPROVED' ORDER BY version DESC LIMIT 1",
                   (order["id"],))
    fj = ctx.db.one("SELECT * FROM factory_jobs WHERE order_id=? ORDER BY id DESC LIMIT 1", (order["id"],))
    if q is None:
        return None
    from . import change_orders
    gross = q["total_krw"] + change_orders.verified_total(ctx.db.conn, order["id"])  # 확인된 변경 추가금 포함
    revenue = revenue_ex_tax(ctx, gross, q["tax_included"])  # SRS-168 ② r2
    actual = None
    if fj and fj["actual_cost_krw"] is not None:
        actual = (fj["actual_cost_krw"] or 0) + (fj["defect_cost_krw"] or 0) + (fj["logistics_cost_krw"] or 0)
    acq = acquisition_cost(ctx, order)
    minutes = ctx.db.scalar("SELECT COALESCE(SUM(minutes),0) FROM work_logs WHERE order_id=?", (order["id"],))
    contrib = None if actual is None or revenue is None else revenue - actual - q["variable_cost_krw"] - acq
    return {"total": gross, "revenue_ex_tax": revenue, "tax_unset": revenue is None, "actual_cost": actual, "variable": q["variable_cost_krw"],
            "acquisition": acq, "contribution": contrib, "minutes": minutes,
            "per_hour": (contrib * 60 // minutes) if contrib is not None and minutes else None}


def kpis(ctx, include_test=False) -> dict:
    where = "" if include_test else "WHERE o.is_test=0"
    orders = ctx.db.all(f"SELECT o.* FROM orders o {where}")
    n = len(orders)
    ids = [o["id"] for o in orders]
    # KPI-01: Google이 마지막 동기화에서 보여 준 응답 수 대비 저장된 응답 수
    stored = ctx.db.scalar("SELECT COUNT(*) FROM intake_responses ir JOIN orders o ON o.intake_id=ir.id" +
                           ("" if include_test else " WHERE o.is_test=0"))
    seen = ctx.db.scalar("SELECT MAX(last_count) FROM sync_cursors WHERE provider='google_forms'") or 0
    intake_total = max(seen if include_test else stored, stored)
    form_orders = stored
    dup = ctx.db.scalar("SELECT COUNT(*) FROM (SELECT intake_id FROM orders WHERE intake_id IS NOT NULL GROUP BY intake_id "
                        "HAVING COUNT(*)>1)")
    closed = [o for o in orders if o["lifecycle_state"] == "CLOSED"]
    accepted_ids = {o["id"] for o in orders if o["accepted_at"] or o["lifecycle_state"] not in
                    ("INQUIRY_RECEIVED", "AI_BRIEF_READY", "OWNER_REVIEW", "DECLINED")}
    accepted = [o for o in orders if o["id"] in accepted_ids]
    valid = [o for o in orders if o["id"] in accepted_ids or
             (o["lifecycle_state"] == "DECLINED" and o["decline_category"] not in INVALID_DECLINE)]
    undecided = [o for o in orders if o["id"] not in accepted_ids and o["lifecycle_state"] != "DECLINED"]
    agreed = [o for o in orders if ctx.db.scalar("SELECT 1 FROM customer_acceptances WHERE order_id=? AND kind='quote'",
                                                 (o["id"],))]
    paid = [o for o in agreed if ctx.db.scalar("SELECT 1 FROM funnel_events WHERE order_id=? AND kind='paid'", (o["id"],))]
    contribs = [contribution(ctx, o) for o in closed]
    tax_unset = sum(1 for x in contribs if x and x["tax_unset"])
    contribs = [x for x in contribs if x and x["contribution"] is not None]
    tot_c = sum(x["contribution"] for x in contribs)
    tot_m = sum(x["minutes"] for x in contribs)
    on_time = [o for o in closed if o["promised_date"] and (
        ctx.db.scalar("SELECT shipped_at FROM shipments WHERE order_id=? ORDER BY id DESC LIMIT 1", (o["id"],)) or "9")[:10]
        <= o["promised_date"]]
    with_promise = [o for o in closed if o["promised_date"]]
    rework = ctx.db.scalar("SELECT COUNT(DISTINCT order_id) FROM issues WHERE type='REWORK'" +
                           ("" if include_test else " AND order_id IN (SELECT id FROM orders WHERE is_test=0)"))

    def ratio(a, b):
        # 정수 반올림(4/6 = 66.7 → 67%, §C)
        return {"num": a, "den": b, "pct": (a * 200 + b) // (2 * b) if b else None, "small": b < SMALL_N}

    return {
        "KPI-01": ratio(form_orders, intake_total) | {"label": "데이터 완전성"},
        "KPI-02": {"num": dup, "label": "중복 생성", "small": False},
        "KPI-03": {"num": len(closed), "label": "E2E 완료", "small": False},
        "KPI-04": ratio(len(valid), n) | {"label": "유효 문의 비율", "undecided": len(undecided)},
        "KPI-05": ratio(len(paid), len(agreed)) | {"label": "견적→실결제 전환율"},
        "KPI-06": {"label": "작업시간당 공헌이익", "value": (tot_c * 60 // tot_m) if tot_m else None,
                   "contribution": tot_c, "minutes": tot_m, "small": len(contribs) < SMALL_N,
                   "tax_unset": tax_unset},
        "KPI-07": ratio(len(on_time), len(with_promise)) | {"label": "납기 준수율"},
        "KPI-08": ratio(rework, len(accepted)) | {"label": "재작업·클레임 비율"},
    }


def ad_efficiency(ctx, include_test=False) -> list[dict]:
    """H1 r1 Q9: 문의당 광고비(광고비 ÷ 문의 수)와 CAC(광고비 ÷ 결제까지 간 유료 고객 수)를 따로 계산."""
    out = []
    for s in ctx.db.all("SELECT * FROM ad_spend ORDER BY month DESC, channel"):
        rows = [o for o in ctx.db.all("SELECT * FROM orders WHERE source_channel=?" +
                                      ("" if include_test else " AND is_test=0"), (s["channel"],))
                if _month_of(o["created_at"]) == s["month"]]
        paid = {o["customer_id"] for o in rows
                if ctx.db.scalar("SELECT 1 FROM funnel_events WHERE order_id=? AND kind='paid'", (o["id"],))}
        out.append({"month": s["month"], "channel": s["channel"], "label": SOURCE_LABEL.get(s["channel"], s["channel"]),
                    "spend": s["amount_krw"], "inquiries": len(rows), "per_inquiry": s["amount_krw"] // len(rows) if rows else None,
                    "paid_customers": len(paid), "cac": s["amount_krw"] // len(paid) if paid else None})
    return out


def sources(ctx, include_test=False):
    where = "" if include_test else "WHERE is_test=0"
    rows = ctx.db.all(f"SELECT source_channel, COUNT(*) n FROM orders {where} GROUP BY source_channel")
    out = []
    for r in rows:
        ch = r["source_channel"]
        kind = "미분류" if ch is None else ("광고" if ch == "ad" else "직접")
        out.append({"channel": ch, "label": SOURCE_LABEL.get(ch, ch), "kind": kind, "count": r["n"]})
    summary = {"광고": 0, "직접": 0, "미분류": 0}
    for x in out:
        summary[x["kind"]] += x["count"]
    return out, summary


def funnel(ctx, include_test=False):
    out = []
    for kind, label in (("inquiry", "문의"), ("quoted", "견적 승인"), ("paid", "결제 확인"), ("shipped", "발송")):
        q = "SELECT COUNT(*) FROM funnel_events WHERE kind=?" + ("" if include_test else " AND is_test=0")
        out.append({"kind": kind, "label": label, "count": ctx.db.scalar(q, (kind,))})
    return out


def monthly(ctx):
    rows = ctx.db.all("SELECT r.is_test, e.amount_krw, e.event_type, e.recorded_at FROM payment_events e JOIN "
                      "payment_requests r ON r.id=e.request_id WHERE e.event_type IN ('PAID','REFUND')")
    agg: dict = {}
    for r in rows:
        m = _month_of(r["recorded_at"])
        a = agg.setdefault(m, {"live": 0, "test": 0})
        amt = r["amount_krw"] if r["event_type"] == "PAID" else -r["amount_krw"]
        a["test" if r["is_test"] else "live"] += amt
    return sorted(agg.items())


def set_ad_spend(ctx, actor, month, channel, amount):
    with ctx.db.tx() as c:
        c.execute("INSERT INTO ad_spend(month, channel, amount_krw, updated_by, updated_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(month, channel) DO UPDATE SET amount_krw=excluded.amount_krw, updated_by=excluded.updated_by,"
                  " updated_at=excluded.updated_at", (month, channel, int(amount), actor.id, clock.now_iso()))
