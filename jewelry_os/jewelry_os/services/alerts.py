"""알림·경고 평가(DD-009, EXP-003·007·010)."""
from __future__ import annotations

import shutil
from datetime import timedelta

from ..core import clock
from ..domain.states import S
from . import capacity, settings

KIND_META = {
    "customer_wait": ("고객 응답 지연", "clock", "warning"),
    "delivery_risk": ("납기 위험", "alert", "danger"),
    "payment_issue": ("결제 문제", "octagon", "danger"),
    "system": ("시스템", "server", "warning"),
}


def _upsert(c, ctx, kind, severity, order_id, key, title):
    now = clock.now()
    ex = c.execute("SELECT * FROM notifications WHERE dedupe_key=?", (key,)).fetchone()
    if ex and ex["resolved_at"] is None:
        if ex["title"] != title or ex["severity"] != severity:
            c.execute("UPDATE notifications SET title=?, severity=? WHERE id=?", (title, severity, ex["id"]))
        return
    rel = clock.iso(capacity.release_at(ctx, now))
    if ex:
        c.execute("UPDATE notifications SET resolved_at=NULL, created_at=?, release_at=?, title=?, severity=? WHERE id=?",
                  (clock.iso(now), rel, title, severity, ex["id"]))
        c.execute("DELETE FROM notification_reads WHERE notification_id=?", (ex["id"],))
    else:
        c.execute("INSERT INTO notifications(kind, severity, order_id, dedupe_key, title, created_at, release_at) "
                  "VALUES (?,?,?,?,?,?,?)", (kind, severity, order_id, key, title, clock.iso(now), rel))


def _entered_at(c, order_id, state) -> str | None:
    r = c.execute("SELECT created_at FROM audit_events WHERE order_id=? AND object_type='order' AND next=? AND prev<>next "
                  "ORDER BY id DESC LIMIT 1", (order_id, state)).fetchone()
    return r["created_at"] if r else None


def evaluate(ctx) -> None:
    s = settings.all_settings(ctx)
    now = clock.now()
    today = clock.today_kst()
    active: set[str] = set()
    with ctx.db.tx() as c:
        # 고객 응답 지연(EXP-007)
        wait = timedelta(hours=int(s["alert_customer_wait_hours"]))
        for o in c.execute("SELECT * FROM orders WHERE lifecycle_state NOT IN ('CLOSED','DECLINED','CANCELLED')").fetchall():
            starts = []
            h = c.execute("SELECT set_at FROM holds WHERE order_id=? AND reason='NEEDS_INFO' AND cleared_at IS NULL",
                          (o["id"],)).fetchone()
            if h:
                starts.append(h["set_at"])
            if o["lifecycle_state"] in (S.QUOTE_APPROVED.value, S.PAYMENT_PENDING.value):
                starts.append(o["state_entered_at"])
            if any(now - clock.parse(t) > wait for t in starts):
                key = f"customer_wait:{o['id']}"
                active.add(key)
                _upsert(c, ctx, "customer_wait", "warning", o["id"], key, f"{o['order_no']} 고객 답을 기다린 지 3일이 넘었어요")
            # 납기 위험
            risk = None
            fj = c.execute("SELECT * FROM factory_jobs WHERE order_id=? AND status='SENT' ORDER BY id DESC LIMIT 1",
                           (o["id"],)).fetchone()
            if fj and fj["expected_at"] and fj["expected_at"] < today.isoformat():
                risk = f"{o['order_no']} 공장 예상 수령일({fj['expected_at']})이 지났어요"
            if not risk and o["promised_date"] and o["lifecycle_state"] not in (S.SHIPPED.value,):
                left = (clock.parse(o["promised_date"] + "T00:00:00+09:00").date() - today).days
                if left <= int(s["alert_delivery_days"]):
                    risk = f"{o['order_no']} 약속일까지 {left}일 남았어요"
            if risk:
                key = f"delivery_risk:{o['id']}"
                active.add(key)
                _upsert(c, ctx, "delivery_risk", "danger", o["id"], key, risk)
        for i in c.execute("SELECT i.*, o.order_no FROM issues i JOIN orders o ON o.id=i.order_id WHERE i.state='OPEN' AND "
                           "i.type IN ('PAYMENT_MISMATCH','REFUND_REVIEW')").fetchall():
            key = f"payment_issue:{i['order_id']}:{i['type']}"
            active.add(key)
            _upsert(c, ctx, "payment_issue", "danger", i["order_id"], key,
                    f"{i['order_no']} " + ("결제 금액이 맞지 않아요" if i["type"] == "PAYMENT_MISMATCH"
                                          else "제작 중 취소·환불 요청이 들어왔어요"))
        for key, title, sev in system_problems(ctx, c, s):
            active.add(key)
            _upsert(c, ctx, "system", sev, None, key, title)
        for n in c.execute("SELECT * FROM notifications WHERE resolved_at IS NULL").fetchall():
            if n["dedupe_key"] not in active:
                c.execute("UPDATE notifications SET resolved_at=? WHERE id=?", (clock.iso(now), n["id"]))


def disk_free_pct(ctx) -> float:
    try:
        u = shutil.disk_usage(ctx.cfg.data_dir)
        return u.free / u.total * 100
    except OSError:
        return 100.0


def system_problems(ctx, c, s=None):
    s = s or settings.all_settings(ctx)
    out = []
    now = clock.now()
    cur = c.execute("SELECT * FROM sync_cursors WHERE provider='google_forms' AND scope=?",
                    (s["google_form_id"],)).fetchone()
    limit = timedelta(minutes=int(s["sync_interval_min"]) * int(s["alert_sync_multiplier"]))
    if cur and cur["status"] == "ERROR" and cur["last_error_kind"] == "AUTH_EXPIRED":
        out.append(("system:sync_auth", "Google 연결이 만료됐어요 — 다시 연결이 필요해요", "danger"))
    elif cur and cur["last_success_at"] and now - clock.parse(cur["last_success_at"]) > limit:
        mins = int((now - clock.parse(cur["last_success_at"])).total_seconds() // 60)
        out.append(("system:sync_stale", f"동기화가 {mins}분째 안 됐어요", "warning"))
    b = c.execute("SELECT created_at FROM backups WHERE status='OK' ORDER BY id DESC LIMIT 1").fetchone()
    if b is None or now - clock.parse(b["created_at"]) > timedelta(hours=int(s["alert_backup_hours"])):
        hrs = "한 번도" if b is None else f"{int((now - clock.parse(b['created_at'])).total_seconds() // 3600)}시간째"
        out.append(("system:backup", f"백업이 {hrs} 안 됐어요", "warning"))
    pct = ctx.extra.get("disk_pct_override", None)
    pct = disk_free_pct(ctx) if pct is None else pct
    if pct < 5:
        out.append(("system:disk", f"저장 공간이 매우 부족해요(남은 {pct:.0f}%)", "danger"))
    elif pct < int(s["alert_disk_pct"]):
        out.append(("system:disk", f"저장 공간이 부족해요(남은 {pct:.0f}%)", "warning"))
    return out


def for_dashboard(ctx):
    rows = ctx.db.all("SELECT n.*, o.order_no FROM notifications n LEFT JOIN orders o ON o.id=n.order_id "
                      "WHERE n.resolved_at IS NULL ORDER BY CASE n.severity WHEN 'danger' THEN 0 ELSE 1 END, n.created_at")
    groups = {"payment_issue": [], "delivery_risk": [], "customer_wait": [], "system": []}
    for r in rows:
        groups[r["kind"]].append(r)
    return groups


def badge_count(ctx, user_id) -> int:
    return ctx.db.scalar("SELECT COUNT(*) FROM notifications n WHERE n.resolved_at IS NULL AND n.release_at<=? AND "
                         "NOT EXISTS (SELECT 1 FROM notification_reads r WHERE r.notification_id=n.id AND r.user_id=?)",
                         (clock.now_iso(), user_id))


def mark_read(ctx, user_id):
    with ctx.db.tx() as c:
        c.execute("INSERT OR IGNORE INTO notification_reads(notification_id, user_id) SELECT id, ? FROM notifications "
                  "WHERE resolved_at IS NULL AND release_at<=?", (user_id, clock.now_iso()))
