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
    assert db.scalar("SELECT value FROM meta WHERE key='schema_version'") == "3"
    assert db.scalar("SELECT expected_krw FROM payment_requests") == 100
    assert db.all("PRAGMA foreign_key_check") == []
    db.close()


def test_migrate_v2_adds_contract_tables_without_touching_data(tmp_path):
    """v2 → v3(D-24): Form B 계약 표는 추가만. 기존 주문·견적·동의 기록은 그대로, 이전 동의 주문은 소급 제외."""
    db_path = tmp_path / "v2.db"
    v2 = SCHEMA[:SCHEMA.index("-- D-24 Form B")] + SCHEMA[SCHEMA.index("CREATE INDEX IF NOT EXISTS ix_orders_state"):]
    con = sqlite3.connect(db_path)
    con.executescript(v2)
    con.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '2')")
    con.execute("INSERT INTO customers(id, display_name, created_at) VALUES (1, 'x', 'now')")
    con.execute("INSERT INTO orders(id, order_no, customer_id, lifecycle_state, source, payment_policy, state_entered_at, "
                "created_at, updated_at) VALUES (1, 'JOS-1', 1, 'CUSTOMER_ACCEPTED', 'form', 'THIRDS', 'n', 'n', 'n')")
    con.execute("INSERT INTO quotes(id, order_id, version, status, payment_policy, created_at) VALUES (1,1,1,'DRAFT','THIRDS','n')")
    con.execute("UPDATE quotes SET status='APPROVED' WHERE id=1")
    con.execute("INSERT INTO customer_acceptances(order_id, kind, quote_id, quote_version, channel, evidence_text, "
                "accepted_at, recorded_by, recorded_at) VALUES (1,'quote',1,1,'카카오톡','동의','2026-10-01T00:00:00+00:00',1,"
                "'2026-10-01T00:00:00+00:00')")
    con.commit()
    con.close()
    db = Database(db_path)
    for t in ("contract_requests", "contract_responses", "contract_response_revisions", "contract_events",
              "contract_notices", "customer_identities"):
        assert db.scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (t,)) == 1
    assert db.scalar("SELECT value FROM meta WHERE key='schema_version'") == "3"
    assert db.scalar("SELECT order_no FROM orders") == "JOS-1" and db.scalar("SELECT COUNT(*) FROM customer_acceptances") == 1
    from jewelry_os.domain import transitions as tr
    assert tr.contract_exempt(db.conn, 1) and tr.contract_ok(db.conn, 1)  # 이전에 동의한 주문은 소급 적용하지 않음
    db.close()
