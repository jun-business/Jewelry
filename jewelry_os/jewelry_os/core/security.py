"""비밀번호·세션·CSRF(DD-007)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from . import clock

N, R, P, DKLEN = 2 ** 14, 8, 1, 32
MAX_FAILS = 5
LOCKOUT = timedelta(minutes=5)
ABSOLUTE = timedelta(hours=12)


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(h)))
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


@dataclass
class User:
    id: int
    username: str
    display_name: str
    role: str
    prefs: dict

    @property
    def role_label(self) -> str:
        return "아버님" if self.role == "maker" else "운영자"


SYSTEM = None  # 시스템 주체는 actor=None


class AuthError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def create_user(db, username: str, display_name: str, role: str, password: str) -> int:
    if len(password) < 10:
        raise AuthError("비밀번호는 10자 이상이어야 해요.")
    if role not in ("maker", "operator"):
        raise AuthError("역할이 올바르지 않아요.")
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise AuthError("이미 있는 아이디예요.")
        cur = c.execute("INSERT INTO users(username, display_name, role, password_hash, created_at) VALUES (?,?,?,?,?)",
                        (username, display_name, role, hash_password(password), clock.now_iso()))
        return cur.lastrowid


def login(db, username: str, password: str) -> tuple[str, str]:
    """성공 시 (session_id, csrf). 실패 시 AuthError."""
    now = clock.now()
    u = db.one("SELECT * FROM users WHERE username=?", (username,))
    if u is None:
        raise AuthError("아이디 또는 비밀번호가 맞지 않아요.")
    lu = clock.parse(u["locked_until"])
    if lu and lu > now:
        raise AuthError(f"비밀번호를 {MAX_FAILS}번 틀려 5분 동안 로그인할 수 없어요({clock.kst(lu):%H:%M}까지).")
    if not verify_password(password, u["password_hash"]):
        fails = u["failed_count"] + 1
        with db.tx() as c:
            c.execute("UPDATE users SET failed_count=?, locked_until=? WHERE id=?",
                      (0 if fails >= MAX_FAILS else fails, clock.iso(now + LOCKOUT) if fails >= MAX_FAILS else None,
                       u["id"]))
            c.execute("INSERT INTO audit_events(object_type, object_id, actor_id, actor_role, action, detail_json, created_at)"
                      " VALUES ('user', ?, ?, ?, 'login_failed', ?, ?)",
                      (u["id"], u["id"], u["role"], f'{{"fails":{fails}}}', clock.now_iso()))
        if fails >= MAX_FAILS:
            raise AuthError(f"비밀번호를 {MAX_FAILS}번 틀려 5분 동안 로그인할 수 없어요"
                            f"({clock.kst(now + LOCKOUT):%H:%M}까지).")
        raise AuthError("아이디 또는 비밀번호가 맞지 않아요.")
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with db.tx() as c:
        c.execute("UPDATE users SET failed_count=0, locked_until=NULL, last_login_at=? WHERE id=?",
                  (clock.now_iso(), u["id"]))
        c.execute("INSERT INTO sessions(id, user_id, csrf, created_at, last_seen_at, locked) VALUES (?,?,?,?,?,0)",
                  (sid, u["id"], csrf, clock.now_iso(), clock.now_iso()))
        c.execute("INSERT INTO audit_events(object_type, object_id, actor_id, actor_role, action, detail_json, created_at)"
                  " VALUES ('user', ?, ?, ?, 'login', '{}', ?)", (u["id"], u["id"], u["role"], clock.now_iso()))
    return sid, csrf


def session_state(db, sid: str | None, lock_minutes: int) -> tuple[str, dict | None]:
    """('none'|'expired'|'locked'|'ok', session_row_with_user)."""
    if not sid:
        return "none", None
    s = db.one("SELECT s.*, u.username, u.display_name, u.role, u.prefs_json FROM sessions s "
               "JOIN users u ON u.id=s.user_id WHERE s.id=?", (sid,))
    if s is None:
        return "none", None
    now = clock.now()
    if now - clock.parse(s["created_at"]) > ABSOLUTE:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE id=?", (sid,))
        return "expired", None
    if s["locked"] or now - clock.parse(s["last_seen_at"]) >= timedelta(minutes=lock_minutes):
        if not s["locked"]:
            with db.tx() as c:
                c.execute("UPDATE sessions SET locked=1 WHERE id=?", (sid,))
        return "locked", dict(s)
    return "ok", dict(s)


def touch(db, sid: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE sessions SET last_seen_at=? WHERE id=? AND locked=0", (clock.now_iso(), sid))


def lock(db, sid: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE sessions SET locked=1 WHERE id=?", (sid,))


def unlock(db, sid: str, password: str) -> bool:
    s = db.one("SELECT s.user_id, u.password_hash FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=?", (sid,))
    if s is None or not verify_password(password, s["password_hash"]):
        return False
    with db.tx() as c:
        c.execute("UPDATE sessions SET locked=0, last_seen_at=? WHERE id=?", (clock.now_iso(), sid))
    return True


def logout(db, sid: str) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE id=?", (sid,))


def to_user(row) -> User:
    import json
    return User(id=row["user_id"] if "user_id" in row.keys() else row["id"], username=row["username"],
                display_name=row["display_name"], role=row["role"], prefs=json.loads(row["prefs_json"] or "{}"))
