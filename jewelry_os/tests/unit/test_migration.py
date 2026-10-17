"""스키마 v1 → v2 이행(H1 r1 Q10: payment_requests CHANGE 단계)."""
import sqlite3
from pathlib import Path

from jewelry_os.core.db import Database

SCHEMA = (Path(__file__).resolve().parents[2] / "jewelry_os" / "core" / "schema.sql").read_text(encoding="utf-8")


def test_migrate_v1_payment_requests(tmp_path):
    db_path = tmp_path / "v1.db"
    old = SCHEMA.replace("'DEPOSIT','BALANCE','FULL','CHANGE'", "'DEPOSIT','BALANCE','FULL'")
    old = old.replace("  change_order_id INTEGER NOT NULL DEFAULT 0,   -- CHANGE(발주 후 변경 추가 결제)일 때 change_orders.id\n", "")
    old = old.replace("UNIQUE (order_id, phase, quote_version, change_order_id)", "UNIQUE (order_id, phase, quote_version)")
    con = sqlite3.connect(db_path)
    con.executescript(old)
    con.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    con.execute("INSERT INTO customers(id, display_name, created_at) VALUES (1, 'x', 'now')")
    con.execute("INSERT INTO orders(id, order_no, customer_id, lifecycle_state, source, payment_policy, state_entered_at, "
                "created_at, updated_at) VALUES (1, 'JOS-1', 1, 'INQUIRY_RECEIVED', 'form', 'THIRDS', 'n', 'n', 'n')")
    con.execute("INSERT INTO quotes(id, order_id, version, status, payment_policy, created_at) VALUES (1,1,1,'DRAFT','THIRDS','n')")
    con.execute("INSERT INTO payment_requests(order_id, quote_id, quote_version, phase, expected_krw, seller_code, created_at) "
                "VALUES (1, 1, 1, 'DEPOSIT', 100, 'S1', 'n')")
    con.commit()
    con.close()
    db = Database(db_path)
    sql = db.scalar("SELECT sql FROM sqlite_master WHERE name='payment_requests'")
    assert "'CHANGE'" in sql and "change_order_id" in sql
    assert db.scalar("SELECT value FROM meta WHERE key='schema_version'") == "2"
    assert db.scalar("SELECT expected_krw FROM payment_requests") == 100
    assert db.all("PRAGMA foreign_key_check") == []
    db.close()
