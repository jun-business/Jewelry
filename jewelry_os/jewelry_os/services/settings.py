"""업무 설정·Gate 상태·정책(UI-08). 업무 설정 쓰기는 operator만."""
from __future__ import annotations

from ..core import clock
from ..core.db import jdump, jload
from ..domain.states import GATES, LIVE_INTAKE_GATES, TEST_MODE_GATES
from . import audit

DEFAULTS = {
    "capacity_limit": 3,
    "payment_policy": "THIRDS",          # THIRDS | FULL (ASM-016)
    "free_concept_limit": 1,             # TBD-003
    "lock_minutes": 15,                  # ASM-006
    "quiet_start": "00:00",              # ASM-007
    "quiet_end": "07:00",
    "cad_weekdays": [5, 6],              # 토·일
    "cad_on_holidays": True,
    "sync_interval_min": 5,
    "sync_incremental": False,           # DD-003: 기본은 전체 페이지 조회
    "alert_sync_multiplier": 3,          # ASM-008
    "alert_backup_hours": 26,
    "alert_disk_pct": 10,
    "alert_customer_wait_hours": 72,
    "alert_delivery_days": 3,
    "quote_valid_days": 14,
    "factory_days": 14,
    "backup_keep_days": 30,
    "google_mode": "mock",               # mock | live
    "google_form_purpose": "test",       # test | public
    "google_form_id": "mock-form",
    "ai_provider": "mock",               # mock | claude
    "ai_model": "claude-opus-5-5",
    "ai_monthly_call_limit": 300,
    "ai_monthly_cost_limit_krw": 30000,
    "ai_send_images": False,
    "shop_name": "Jewelry OS",           # TBD-001
    "consent_version": "consent_v1",
    "tax_type": "unset",                 # H1 r1 Q9: unset | general | simplified | exempt (TBD-009)
    "simplified_vat_rate_pct": 20,       # 간이과세 업종별 부가가치율(%) — 업종 확인 필요(TBD-009)
}


def get(ctx, key: str):
    row = ctx.db.one("SELECT value_json FROM settings WHERE key=?", (key,))
    return jload(row["value_json"]) if row else DEFAULTS.get(key)


def all_settings(ctx) -> dict:
    out = dict(DEFAULTS)
    for r in ctx.db.all("SELECT key, value_json FROM settings"):
        out[r["key"]] = jload(r["value_json"])
    return out


def put(ctx, key: str, value, actor) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    old = get(ctx, key)
    with ctx.db.tx() as c:
        c.execute("INSERT INTO settings(key, value_json, updated_by, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_by=excluded.updated_by, "
                  "updated_at=excluded.updated_at", (key, jdump(value), actor.id if actor else None, clock.now_iso()))
        audit.record(c, "setting", key, actor, "setting_changed", prev=jdump(old), next=jdump(value))


# ---- Gate ----
def gate_status(ctx) -> dict[str, dict]:
    out = {}
    for gid, name, criteria in GATES:
        r = ctx.db.one("SELECT * FROM gate_records WHERE gate_id=? ORDER BY id DESC LIMIT 1", (gid,))
        out[gid] = {"id": gid, "name": name, "criteria": criteria,
                    "passed": bool(r and r["status"] == "PASSED"), "record": dict(r) if r else None}
    return out


def test_mode(ctx) -> bool:
    g = gate_status(ctx)
    return any(not g[x]["passed"] for x in TEST_MODE_GATES)


def unpassed_test_gates(ctx) -> list[str]:
    g = gate_status(ctx)
    return [x for x in TEST_MODE_GATES if not g[x]["passed"]]


def live_intake_allowed(ctx) -> bool:
    g = gate_status(ctx)
    return all(g[x]["passed"] for x in LIVE_INTAKE_GATES)


def quote_gate_passed(ctx) -> bool:
    return gate_status(ctx)["GATE-Q-01"]["passed"]


def record_gate(ctx, gate_id: str, passed: bool, evidence: str, actor) -> None:
    if gate_id not in {g[0] for g in GATES}:
        raise KeyError(gate_id)
    with ctx.db.tx() as c:
        c.execute("INSERT INTO gate_records(gate_id, status, evidence, recorded_by, recorded_at) VALUES (?,?,?,?,?)",
                  (gate_id, "PASSED" if passed else "NOT_PASSED", evidence, actor.id, clock.now_iso()))
        audit.record(c, "gate", gate_id, actor, "gate_recorded", next="PASSED" if passed else "NOT_PASSED",
                     reason=evidence)


# ---- 정책 문서(상담·약관·동의·가격) ----
def approved_policy(ctx, kind: str):
    return ctx.db.one("SELECT * FROM policies WHERE kind=? AND status='APPROVED' ORDER BY version DESC LIMIT 1", (kind,))


def create_policy(ctx, kind: str, title: str, body: str, actor) -> int:
    with ctx.db.tx() as c:
        v = (c.execute("SELECT COALESCE(MAX(version),0) FROM policies WHERE kind=?", (kind,)).fetchone()[0]) + 1
        cur = c.execute("INSERT INTO policies(kind, version, status, title, body, created_by, created_at) "
                        "VALUES (?,?,?,?,?,?,?)", (kind, v, "DRAFT", title, body, actor.id if actor else None,
                                                    clock.now_iso()))
        audit.record(c, "policy", f"{kind}:v{v}", actor, "policy_drafted")
        return cur.lastrowid


def approve_policy(ctx, policy_id: int, actor) -> None:
    with ctx.db.tx() as c:
        p = c.execute("SELECT * FROM policies WHERE id=?", (policy_id,)).fetchone()
        if p is None or p["status"] != "DRAFT":
            raise ValueError("초안 정책만 승인할 수 있어요")
        c.execute("UPDATE policies SET status='RETIRED' WHERE kind=? AND status='APPROVED'", (p["kind"],))
        c.execute("UPDATE policies SET status='APPROVED', approved_by=?, approved_at=? WHERE id=?",
                  (actor.id, clock.now_iso(), policy_id))
        audit.record(c, "policy", f"{p['kind']}:v{p['version']}", actor, "policy_approved")


def retire_policy(ctx, policy_id: int, actor) -> None:
    with ctx.db.tx() as c:
        p = c.execute("SELECT * FROM policies WHERE id=?", (policy_id,)).fetchone()
        c.execute("UPDATE policies SET status='RETIRED' WHERE id=?", (policy_id,))
        audit.record(c, "policy", f"{p['kind']}:v{p['version']}", actor, "policy_retired")
