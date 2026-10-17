"""감사로그(SRS-081). 고객 개인정보 값은 쓰지 않는다 — PII 필드 변경은 `[개인정보]`로 기록."""
from __future__ import annotations

from ..core import clock
from ..core.db import jdump

PII = "[개인정보]"


def record(c, object_type: str, object_id, actor, action: str, *, order_id=None, prev=None, next=None,
           reason=None, ref_version=None, detail=None) -> None:
    """c = 열린 트랜잭션 커서. actor = User | None(시스템)."""
    c.execute(
        "INSERT INTO audit_events(object_type, object_id, order_id, actor_id, actor_role, action, prev, next, reason, "
        "ref_version, detail_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (object_type, str(object_id), order_id, getattr(actor, "id", None), getattr(actor, "role", "system"),
         action, prev, next, reason, None if ref_version is None else str(ref_version), jdump(detail or {}),
         clock.now_iso()))
