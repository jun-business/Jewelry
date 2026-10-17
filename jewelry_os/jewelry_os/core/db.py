"""SQLite 접근(BD-004, DD-001). 쓰기는 tx() 트랜잭션(BEGIN IMMEDIATE)으로만."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = "2"
_SCHEMA = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")


class ConflictError(Exception):
    """낙관적 잠금 충돌(E-15)."""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._local = threading.local()
        c = self.connect()  # sqlite3의 with는 닫지 않는다 — 명시적으로 닫아 파일 잠금을 남기지 않음
        try:
            c.executescript(_SCHEMA)
            _migrate(c)
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (SCHEMA_VERSION,))
        finally:
            c.close()

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        return c

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self.connect()
            self._local.conn = c
        return c

    @contextmanager
    def tx(self):
        c = self.conn
        if c.in_transaction:  # 중첩 호출은 바깥 트랜잭션에 합류
            yield c
            return
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, args=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def scalar(self, sql: str, args=()):
        r = self.conn.execute(sql, args).fetchone()
        return None if r is None else r[0]

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None


def _migrate(c: sqlite3.Connection) -> None:
    """v1 → v2: payment_requests에 CHANGE 단계·change_order_id(H1 r1 Q10). SQLite는 CHECK를 바꿀 수 없어 표를 다시 만든다."""
    sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='payment_requests'").fetchone()[0]
    if "'CHANGE'" in sql:
        return
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("BEGIN IMMEDIATE")
    try:
        new_sql = _SCHEMA[_SCHEMA.index("CREATE TABLE IF NOT EXISTS payment_requests"):]
        new_sql = new_sql[:new_sql.index(");") + 2].replace("payment_requests", "payment_requests_v2", 1)
        c.execute(new_sql)
        cols = "id, order_id, quote_id, quote_version, phase, expected_krw, seller_code, link_url, link_status, status, " \
               "is_test, created_by, created_at"
        c.execute(f"INSERT INTO payment_requests_v2({cols}) SELECT {cols} FROM payment_requests")
        c.execute("DROP TABLE payment_requests")
        c.execute("ALTER TABLE payment_requests_v2 RENAME TO payment_requests")
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    finally:
        c.execute("PRAGMA foreign_keys=ON")


def jdump(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def jload(s, default=None):
    if s is None or s == "":
        return default
    return json.loads(s)
