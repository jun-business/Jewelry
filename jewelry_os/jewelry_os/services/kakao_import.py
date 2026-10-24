"""카카오톡 채널 대화 가져오기(D-30) — 비용·사업자등록 없이 상담 내용을 앱에 모은다.

카카오톡 채널 1:1 대화는 무료로 읽어 올 공개 API가 없어서, 운영자가 대화를 **복사해 붙여넣거나 내보낸 .txt**를
올리면 메시지 단위로 나눠 기록한다. 같은 대화를 다시 붙여도 새 메시지만 들어간다(시각·보낸 사람·내용 해시).
보낸 사람이 우리 채널 이름이면 ‘→ 고객’, 그 밖은 ‘고객 →’. 전화번호는 기존 상담 기록처럼 가린다."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from ..core import clock
from ..domain.guards import TransitionBlocked, unmet
from . import audit, cs, settings

PROVIDER = "kakao_paste"
_AMPM = r"(오전|오후|AM|PM|am|pm)"
_DATE_HEAD = re.compile(r"^-*\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\s*\S+요일)?\s*-*$")
# 모바일 내보내기: “2026년 10월 24일 오후 3:15, 김하늘 : 내용” / “2026. 10. 24. 오후 3:15, 김하늘 : 내용”
_MOBILE = re.compile(r"^(\d{4})(?:년\s*|\.\s*)(\d{1,2})(?:월\s*|\.\s*)(\d{1,2})(?:일|\.)?\s*" + _AMPM +
                     r"\s*(\d{1,2}):(\d{2}),\s*(.+?)\s*:\s?(.*)$")
# PC 내보내기·복사: “[김하늘] [오후 3:15] 내용”
_PC = re.compile(r"^\[(.+?)\]\s*\[" + _AMPM + r"\s*(\d{1,2}):(\d{2})\]\s?(.*)$")
_SKIP = re.compile(r"^(저장한 날짜|.+님과 카카오톡 대화|.+ 님과 카카오톡 대화)")


@dataclass
class Msg:
    at: datetime | None
    sender: str
    text: str


def _hour(ampm: str, h: int) -> int:
    pm = ampm.lower() in ("오후", "pm")
    return (h % 12) + (12 if pm else 0)


def parse(text: str, default_date=None) -> list[Msg]:
    """카카오톡 대화 텍스트 → 메시지 목록. 형식을 모르는 줄은 앞 메시지에 이어 붙이고, 첫 줄부터 모르면 한 덩어리로."""
    day = default_date or clock.today_kst()
    out: list[Msg] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip() or _SKIP.match(line.strip()):
            continue
        m = _DATE_HEAD.match(line.strip())
        if m:
            day = datetime(int(m[1]), int(m[2]), int(m[3])).date()
            continue
        m = _MOBILE.match(line.strip())
        if m:
            day = datetime(int(m[1]), int(m[2]), int(m[3])).date()
            at = datetime(day.year, day.month, day.day, _hour(m[4], int(m[5])), int(m[6]))
            out.append(Msg(at, m[7].strip(), m[8]))
            continue
        m = _PC.match(line.strip())
        if m:
            at = datetime(day.year, day.month, day.day, _hour(m[2], int(m[3])), int(m[4]))
            out.append(Msg(at, m[1].strip(), m[5]))
            continue
        if out:
            out[-1].text += "\n" + line
        else:
            out.append(Msg(None, "", line))
    return [x for x in out if x.text.strip()]


def our_names(ctx) -> set[str]:
    return {n for n in {settings.get(ctx, "shop_name"), "틴틴골드", "tntngold"} if n}


def _key(m: Msg) -> str:
    return hashlib.sha256(f"{m.at.isoformat() if m.at else '-'}|{m.sender}|{m.text.strip()}".encode()).hexdigest()[:32]


def import_chat(ctx, actor, text: str, *, order=None, thread_id: int | None = None, title: str | None = None) -> dict:
    """대화 텍스트를 주문(또는 주문 없는 문의 스레드)에 기록. 반환 {thread_id, new, dup, total}."""
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    msgs = parse(text)
    if not msgs:
        raise TransitionBlocked([unmet("G-CS-PASTE")])
    ours = our_names(ctx)
    new = dup = 0
    with ctx.db.tx() as c:
        if order is not None:
            tid = cs.thread_for(ctx, c, order["id"])
        elif thread_id is not None:
            tid = thread_id
        else:
            tid = c.execute("INSERT INTO cs_threads(order_id, provider, title, created_at) VALUES (NULL, ?, ?, ?)",
                            (PROVIDER, (title or "").strip() or _guess_title(msgs, ours), clock.now_iso())).lastrowid
        for m in msgs:
            at = clock.iso(m.at.replace(tzinfo=clock.KST)) if m.at else clock.now_iso()
            direction = "out" if m.sender in ours else "in"
            body = cs.redact(m.text.strip())
            cur = c.execute("INSERT OR IGNORE INTO cs_messages(thread_id, provider, sender_key, message_key, direction, "
                            "summary, content_redacted, event_at, event_at_src, recorded_by, recorded_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (tid, PROVIDER, f"thread:{tid}", _key(m), direction,
                             (f"{m.sender}: " if m.sender and direction == "in" else "") + body[:80],
                             body if len(body) > 80 or "\n" in body else None, at, "카카오톡 대화 붙여넣기",
                             actor.id, clock.now_iso()))
            if cur.rowcount:
                new += 1
            else:
                dup += 1
        audit.record(c, "cs_thread", tid, actor, "kakao_chat_imported", order_id=order["id"] if order else None,
                     detail={"new": new, "dup": dup})
    return {"thread_id": tid, "new": new, "dup": dup, "total": len(msgs)}


def _guess_title(msgs, ours) -> str:
    who = next((m.sender for m in msgs if m.sender and m.sender not in ours), "")
    return f"{who} 카카오톡 문의" if who else "카카오톡 문의"


def open_threads(ctx):
    """주문에 아직 연결되지 않은 문의 대화."""
    return ctx.db.all("SELECT t.*, (SELECT COUNT(*) FROM cs_messages m WHERE m.thread_id=t.id) AS n, "
                      "(SELECT MAX(event_at) FROM cs_messages m WHERE m.thread_id=t.id) AS last_at FROM cs_threads t "
                      "WHERE t.order_id IS NULL ORDER BY last_at DESC")


def thread(ctx, tid):
    return ctx.db.one("SELECT * FROM cs_threads WHERE id=?", (tid,))


def thread_messages(ctx, tid):
    return ctx.db.all("SELECT m.*, u.display_name AS recorder FROM cs_messages m LEFT JOIN users u ON u.id=m.recorded_by "
                      "WHERE m.thread_id=? ORDER BY m.event_at, m.id", (tid,))


def link_order(ctx, tid, order, actor) -> None:
    """주문 없는 문의 대화를 주문에 연결 — 주문에 이미 대화가 있으면 그쪽으로 합친다."""
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])
    with ctx.db.tx() as c:
        t = c.execute("SELECT * FROM cs_threads WHERE id=?", (tid,)).fetchone()
        if t is None or t["order_id"] is not None:
            raise TransitionBlocked([unmet("G-WRONG-STATE")])
        target = c.execute("SELECT id FROM cs_threads WHERE order_id=? ORDER BY id LIMIT 1", (order["id"],)).fetchone()
        if target is None:
            c.execute("UPDATE cs_threads SET order_id=? WHERE id=?", (order["id"], tid))
        else:
            for m in c.execute("SELECT * FROM cs_messages WHERE thread_id=?", (tid,)).fetchall():
                dupe = c.execute("SELECT 1 FROM cs_messages WHERE provider=? AND thread_id=? AND message_key=?",
                                 (m["provider"], target["id"], m["message_key"])).fetchone()
                if dupe:
                    c.execute("DELETE FROM cs_messages WHERE id=?", (m["id"],))
                else:
                    c.execute("UPDATE cs_messages SET thread_id=?, sender_key=? WHERE id=?",
                              (target["id"], f"thread:{target['id']}", m["id"]))
            c.execute("UPDATE cs_threads SET order_id=?, title=COALESCE(title, '') || ' (주문 대화에 합침)' WHERE id=?",
                      (order["id"], tid))
        audit.record(c, "cs_thread", tid, actor, "cs_thread_linked", order_id=order["id"], next=order["order_no"])
