"""계약 안 된 문의 보관기간 정리(D-40, Form A 개인정보 안내 ‘최종 상담일로부터 3개월 보관 후 파기’).

- 대상: 고객 묶음(같은 고객으로 연결 포함)의 모든 주문이 계약으로 이어지지 않았고, 마지막 활동이 보관기간보다 오래된 경우.
  계약(고객 동의·Form B 계약 유효·결제)이 하나라도 있으면 전자상거래법 보존 대상이라 건드리지 않는다.
- 파기 전에 ‘왜 계약이 안 됐는지’ 분석 기록(lost_inquiries)을 개인정보 없이 남긴다 — 데이터로 쌓아 실적 화면에서 본다.
- 파기: 이름·연락처·폼 원문 개인정보(기존 개인정보 삭제와 같은 범위) + 고객이 보낸 참고 사진 파일.
- Google 설문지에 남은 원 응답은 앱이 지울 수 없다(읽기 전용) — 화면에서 안내한다. 다시 가져와도 개인정보는 되살아나지 않는다."""
from __future__ import annotations

from datetime import date, timedelta

from ..core import clock
from ..core.db import jdump, jload
from ..domain.states import PHASE_LABEL, PHASE_OF, S
from . import audit, customers, settings

REASON_LABEL = {
    "SHOP_DECLINED": "틴틴골드가 거절", "CANCELLED": "진행 중 취소", "CUSTOMER_REFUSED_CONTRACT": "고객이 계약 동의 거부",
    "CONTRACT_NOT_COMPLETED": "계약 링크 보냈으나 미완료", "NO_DECISION_AFTER_QUOTE": "견적 후 고객 결정 없음",
    "NO_PROGRESS_AFTER_CONCEPT": "시안 후 견적 전 중단", "NO_REPLY_TO_INFO_REQUEST": "정보 요청에 고객 답 없음",
    "WAITLISTED_NOT_STARTED": "대기 중 시작 못 함", "NO_PRIVACY_CONSENT": "개인정보 동의 없음",
    "NOT_REVIEWED": "검토 전 방치", "OTHER": "기타",
}


def months(ctx) -> int:
    return int(settings.get(ctx, "inquiry_retention_months") or 3)


def _cutoff(ctx) -> str:
    d = clock.today_kst()
    m = d.month - months(ctx)
    y, m = d.year + (m - 1) // 12, (m - 1) % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return clock.iso(clock.parse(date(y, m, day).isoformat() + "T00:00:00+09:00"))


def last_activity(c, order) -> str:
    """최종 상담일 = 주문·이력·상담 기록·폼 응답 중 가장 늦은 시각."""
    vals = [order["created_at"], order["updated_at"],
            c.execute("SELECT MAX(created_at) FROM audit_events WHERE order_id=?", (order["id"],)).fetchone()[0],
            c.execute("SELECT MAX(m.event_at) FROM cs_messages m JOIN cs_threads t ON t.id=m.thread_id WHERE t.order_id=?",
                      (order["id"],)).fetchone()[0]]
    if order["intake_id"]:
        vals.append(c.execute("SELECT last_submitted_at FROM intake_responses WHERE id=?", (order["intake_id"],)).fetchone()[0])
    return max(clock.iso(clock.parse(v)) for v in vals if v)


def contracted(c, order_id) -> bool:
    return bool(c.execute("SELECT 1 FROM customer_acceptances WHERE order_id=? AND kind='quote'", (order_id,)).fetchone()
                or c.execute("SELECT 1 FROM contract_requests WHERE order_id=? AND status='ACCEPTED'", (order_id,)).fetchone()
                or c.execute("SELECT 1 FROM payment_events e JOIN payment_requests r ON r.id=e.request_id WHERE r.order_id=? "
                             "AND e.event_type='PAID'", (order_id,)).fetchone())


def classify(c, o) -> tuple[str, dict]:
    """계약이 안 된 이유(규칙 분류)와 근거. 개인정보는 담지 않는다."""
    st = S(o["lifecycle_state"])
    holds = {r["reason"] for r in c.execute("SELECT reason FROM holds WHERE order_id=? AND cleared_at IS NULL", (o["id"],))}
    k = c.execute("SELECT * FROM contract_requests WHERE order_id=? ORDER BY id DESC LIMIT 1", (o["id"],)).fetchone()
    q_appr = c.execute("SELECT COUNT(*) FROM quotes WHERE order_id=? AND status<>'DRAFT'", (o["id"],)).fetchone()[0]
    concepts = c.execute("SELECT COUNT(*) FROM concepts WHERE order_id=?", (o["id"],)).fetchone()[0]
    detail = {}
    if st == S.DECLINED:
        return "SHOP_DECLINED", {"decline_category": o["decline_category"]}
    if st == S.CANCELLED:
        return "CANCELLED", {}
    if k is not None and k["status"] == "REJECTED":
        refused = [r["consent_key"] for r in c.execute(
            "SELECT consent_key FROM contract_consents WHERE request_id=? AND result='REFUSED'", (k["id"],))]
        return "CUSTOMER_REFUSED_CONTRACT", {"refused": sorted(set(refused))}
    if k is not None and k["status"] in ("ISSUED", "RESPONSE_RECEIVED", "REVIEW_REQUIRED"):
        return "CONTRACT_NOT_COMPLETED", {"contract_status": k["status"],
                                          "problems": [p["code"] for p in jload(k["problems_json"], [])]}
    if q_appr:
        return "NO_DECISION_AFTER_QUOTE", detail
    if concepts:
        return "NO_PROGRESS_AFTER_CONCEPT", detail
    if "NO_CONSENT" in holds:
        return "NO_PRIVACY_CONSENT", detail
    if "NEEDS_INFO" in holds:
        return "NO_REPLY_TO_INFO_REQUEST", detail
    if "WAITLISTED" in holds:
        return "WAITLISTED_NOT_STARTED", detail
    if st in (S.INQUIRY_RECEIVED, S.AI_BRIEF_READY, S.OWNER_REVIEW):
        return "NOT_REVIEWED", detail
    return "OTHER", detail


def _record(c, o, last, reason, detail) -> None:
    """파기 전 분석 기록 — 개인정보 없이(제품·소재·예산 구간·경로·진행 정도·이유)."""
    a = {}
    if o["intake_id"]:
        r = c.execute("SELECT answers_json FROM intake_revisions WHERE intake_id=? ORDER BY revision DESC LIMIT 1",
                      (o["intake_id"],)).fetchone()
        a = jload(r["answers_json"], {}) if r else {}
    q = c.execute("SELECT * FROM quotes WHERE order_id=? AND status<>'DRAFT' ORDER BY version DESC LIMIT 1", (o["id"],)).fetchone()
    lead = None
    if a.get("desired_date"):
        try:
            lead = (date.fromisoformat(a["desired_date"]) - clock.kst(clock.parse(o["created_at"])).date()).days
        except ValueError:
            lead = None
    c.execute(
        "INSERT OR IGNORE INTO lost_inquiries(order_id, order_no, created_at, last_activity_at, recorded_at, final_state, "
        "phase_reached, reason_code, reason_detail_json, product_type, material, budget_range, source_channel, "
        "desired_lead_days, concept_count, quote_versions, last_quote_total, is_test) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (o["id"], o["order_no"], o["created_at"], last, clock.now_iso(), o["lifecycle_state"],
         PHASE_LABEL[PHASE_OF[S(o["lifecycle_state"])]], reason, jdump(detail), o["product_type"], a.get("material"),
         a.get("budget_range"), o["source_channel"], lead,
         c.execute("SELECT COUNT(*) FROM concepts WHERE order_id=?", (o["id"],)).fetchone()[0],
         c.execute("SELECT COUNT(*) FROM quotes WHERE order_id=? AND status<>'DRAFT'", (o["id"],)).fetchone()[0],
         q["total_krw"] if q else None, o["is_test"]))


def candidates(ctx) -> list[dict]:
    """보관기간이 지난 계약 안 된 고객 묶음. [{root, orders:[...], last}]"""
    cutoff = _cutoff(ctx)
    c = ctx.db.conn
    out = []
    for r in c.execute("SELECT id FROM customers WHERE merged_into IS NULL AND deleted_at IS NULL ORDER BY id").fetchall():
        ids = customers.group_ids(c, r["id"])
        marks = ",".join("?" * len(ids))
        os_ = c.execute(f"SELECT * FROM orders WHERE customer_id IN ({marks})", ids).fetchall()
        if not os_ or any(contracted(c, o["id"]) for o in os_):
            continue
        last = max(last_activity(c, o) for o in os_)
        if last < cutoff:
            out.append({"root": r["id"], "orders": os_, "last": last})
    return out


def purge(ctx) -> int:
    """보관기간이 지난 계약 안 된 문의를 분석 기록으로 남긴 뒤 개인정보 파기. 반환: 파기한 고객 묶음 수."""
    n = 0
    for g in candidates(ctx):
        with ctx.db.tx() as c:
            for o in g["orders"]:
                reason, detail = classify(c, o)
                _record(c, o, last_activity(c, o), reason, detail)
                for a in c.execute("SELECT * FROM assets WHERE order_id=? AND kind='reference' AND storage_ref IS NOT NULL",
                                   (o["id"],)).fetchall():  # 고객이 보낸 참고 사진
                    p = ctx.cfg.files_dir / a["storage_ref"]
                    if p.exists():
                        p.unlink()
                    c.execute("UPDATE assets SET storage_ref=NULL, fetch_status='PURGED', filename='(보관기간 만료로 삭제)' "
                              "WHERE id=?", (a["id"],))
            customers.erase_in_tx(ctx, c, g["root"], None,
                                  f"보관기간 만료: 계약 없이 최종 상담일로부터 {months(ctx)}개월 경과(Form A 개인정보 안내)")
            audit.record(c, "retention", g["root"], None, "inquiry_purged", detail={"orders": [o["order_no"] for o in g["orders"]],
                                                                                   "last_activity": g["last"]})
        n += 1
    with ctx.db.tx() as c:
        c.execute("INSERT INTO meta(key, value) VALUES ('retention_last_run', ?) ON CONFLICT(key) DO UPDATE SET "
                  "value=excluded.value", (clock.now_iso(),))
    return n


def due(ctx) -> bool:
    r = ctx.db.one("SELECT value FROM meta WHERE key='retention_last_run'")
    return r is None or clock.now() - clock.parse(r["value"]) >= timedelta(hours=23)


def summary(ctx, include_test=False) -> list[dict]:
    w = "" if include_test else "WHERE is_test=0"
    rows = ctx.db.all(f"SELECT reason_code, COUNT(*) n, ROUND(AVG(last_quote_total)) avg_quote FROM lost_inquiries {w} "
                      f"GROUP BY reason_code ORDER BY n DESC")
    return [{"code": r["reason_code"], "label": REASON_LABEL.get(r["reason_code"], r["reason_code"]), "count": r["n"],
             "avg_quote": r["avg_quote"]} for r in rows]
