"""기동·하트비트·상태 진단·스케줄러(SRS-012·092·110·138, FR-OPS-013)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from ..core import clock
from . import alerts, analysis, backup, intake, settings

log = logging.getLogger("jos.system")


def heartbeat(ctx) -> None:
    with ctx.db.tx() as c:
        c.execute("INSERT INTO heartbeats(id, at) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET at=excluded.at",
                  (clock.now_iso(),))


def on_startup(ctx) -> int:
    """중단 추정 구간 기록(EXP-009). 반환 startup_event id."""
    hb = ctx.db.one("SELECT at FROM heartbeats WHERE id=1")
    with ctx.db.tx() as c:
        sid = c.execute("INSERT INTO startup_events(started_at, est_down_from) VALUES (?,?)",
                        (clock.now_iso(), hb["at"] if hb else None)).lastrowid
    heartbeat(ctx)
    return sid


def first_sync_after_startup(ctx, startup_id) -> dict:
    stats = intake.sync_once(ctx)
    intake.fetch_pending_files(ctx)
    with ctx.db.tx() as c:
        c.execute("UPDATE startup_events SET backfilled=? WHERE id=?", (stats.get("new", 0), startup_id))
    alerts.evaluate(ctx)
    return stats


def pending_startup_banner(ctx):
    r = ctx.db.one("SELECT * FROM startup_events WHERE acknowledged_at IS NULL ORDER BY id DESC LIMIT 1")
    if r is None or not r["est_down_from"]:
        return None
    gap = clock.parse(r["started_at"]) - clock.parse(r["est_down_from"])
    if gap < timedelta(minutes=10):
        return None
    return {"id": r["id"], "from": r["est_down_from"], "to": r["started_at"], "hours": round(gap.total_seconds() / 3600),
            "backfilled": r["backfilled"]}


def ack_startup(ctx, sid, actor) -> None:
    with ctx.db.tx() as c:
        c.execute("UPDATE startup_events SET acknowledged_by=?, acknowledged_at=? WHERE id=?",
                  (actor.id, clock.now_iso(), sid))


def status(ctx) -> dict:
    s = settings.all_settings(ctx)
    cur = ctx.db.one("SELECT * FROM sync_cursors WHERE provider='google_forms' AND scope=?", (s["google_form_id"],))
    b = ctx.db.one("SELECT * FROM backups WHERE status='OK' ORDER BY id DESC LIMIT 1")
    starts = ctx.db.all("SELECT * FROM startup_events ORDER BY id DESC LIMIT 10")
    jobs = ctx.db.all("SELECT * FROM system_jobs WHERE status IN ('FAILED','RETRY') ORDER BY id")
    ai_n, ai_oldest = analysis.pending_count(ctx)
    try:
        locked = False
        ctx.db.conn.execute("BEGIN IMMEDIATE")
        ctx.db.conn.execute("ROLLBACK")
    except Exception:
        locked = True
    disk = ctx.extra.get("disk_pct_override")
    return {
        "started_at": starts[0]["started_at"] if starts else None, "startups": starts, "sync": cur, "backup": b,
        "disk_pct": alerts.disk_free_pct(ctx) if disk is None else disk, "db_locked": ctx.extra.get("db_locked_override", locked),
        "db_size": ctx.cfg.db_path.stat().st_size if ctx.cfg.db_path.exists() else 0,
        "jobs": jobs, "ai_pending": ai_n, "ai_oldest": ai_oldest, "ai_usage": analysis.usage(ctx),
        "ai_wait": _duration(clock.now() - clock.parse(ai_oldest)) if ai_oldest else None,
        "data_dir": str(ctx.cfg.data_dir), "problems": [p for p in alerts.system_problems(ctx, ctx.db.conn, s)],
        "security": {r["item"]: dict(r) for r in ctx.db.all("SELECT * FROM security_checklist")},
    }


def _duration(d: timedelta) -> str:
    m = int(d.total_seconds() // 60)
    if m < 60:
        return f"{m}분"
    if m < 60 * 24:
        return f"{m // 60}시간"
    return f"{m // (60 * 24)}일"


def retry_job(ctx, job_id) -> bool:
    job = ctx.db.one("SELECT * FROM system_jobs WHERE id=?", (job_id,))
    if job is None:
        return False
    if job["kind"] == "fetch_files":
        ok = intake.run_file_job(ctx, job)
        return bool(ok)
    if job["kind"] == "ai_brief":
        from ..core.db import jload
        st = analysis.run_brief(ctx, jload(job["payload_json"])["order_id"], force=True)
        with ctx.db.tx() as c:
            c.execute("UPDATE system_jobs SET status=?, attempts=attempts+1, updated_at=? WHERE id=?",
                      ("DONE" if st == "OK" else "FAILED", clock.now_iso(), job_id))
        return st == "OK"
    return False


SECURITY_ITEMS = [("bitlocker", "디스크 암호화(BitLocker) 켜짐"), ("win_password", "Windows 계정 비밀번호 설정"),
                  ("screen_lock", "화면 자동 잠금"), ("offsite_backup", "백업 파일 외부 복사본 위치 확인")]


def set_security_item(ctx, item, checked, actor):
    with ctx.db.tx() as c:
        c.execute("INSERT INTO security_checklist(item, checked, checked_by, checked_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(item) DO UPDATE SET checked=excluded.checked, checked_by=excluded.checked_by, "
                  "checked_at=excluded.checked_at", (item, int(checked), actor.id, clock.now_iso()))


async def scheduler_loop(ctx, stop: asyncio.Event):
    """폴링·하트비트·알림·백업. 실패는 기록하고 계속(SRS-085)."""
    last_sync = None
    while not stop.is_set():
        try:
            heartbeat(ctx)
            interval = timedelta(minutes=int(settings.get(ctx, "sync_interval_min")))
            cur = ctx.db.one("SELECT next_attempt_at FROM sync_cursors WHERE provider='google_forms' AND scope=?",
                             (settings.get(ctx, "google_form_id"),))
            nxt = clock.parse(cur["next_attempt_at"]) if cur and cur["next_attempt_at"] else None
            if (last_sync is None or clock.now() - last_sync >= interval) and (nxt is None or clock.now() >= nxt):
                await asyncio.to_thread(intake.sync_once, ctx)
                await asyncio.to_thread(intake.fetch_pending_files, ctx)
                last_sync = clock.now()
            await asyncio.to_thread(alerts.evaluate, ctx)
            k = clock.kst(clock.now())
            if backup.due(ctx) or (k.hour == 3 and k.minute < 1):
                await asyncio.to_thread(backup.make_backup, ctx)
        except Exception:
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
