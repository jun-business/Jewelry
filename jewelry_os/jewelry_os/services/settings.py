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
    "google_mode": "auto",               # auto | mock | live — auto: Google 계정이 연결돼 있으면 live, 아니면 mock(D-20)
    "google_form_purpose": "test",       # test | public — H3: 실고객 운영 승인 전까지 시험 폼
    "google_form_id": "1xE5XV0lHA9URgOdTYFvcZhM5T-ZYbecH5bXDsSGxF_o",  # 틴틴골드 의뢰 폼(D-20)
    "contract_form_id": "17H2mNNKU0s2RayLkau2Px-IP2TOHsviHRWbp1UXloZ8",  # Form B JOS_B2C_Contract_v1(D-24) — Form A와 별도
    "intake_question_binding": {         # 실제 폼 질문 ID → intake 필드(SRS-004 r2, 설정 > 폼 매핑) — 틴틴골드 의뢰 폼(D-22)
        "702b0596": "product_type", "219660f9": "description", "2388b48b": "reference_link", "7fb11e99": "material",
        "536a57ff": "size", "5d016376": "stones", "42b53d66": "engraving", "2d039a95": "budget_range",
        "3cbf2d37": "desired_date", "70b5dc7c": "display_name", "4c6af8a8": "contact_value",
        "18976f60": "contact_kind", "683c3173": "source_channel", "72899728": "consent",
    },  # 이메일(6fd6cc59)은 받지 않음 — 회신은 휴대전화 번호로. 사진 문항(5b503c28)은 자동 인식
    "ai_provider": "openai",             # mock | claude | openai — D-32: OpenAI. 키가 없으면 규칙 기반(Mock)으로 동작
    "ai_model_legal": "gpt-6-sol",       # D-39 손해 사유 법률 검토만 추론이 강한 상위 모델(견적당 1회)
    "ai_model": "gpt-6-luna",           # D-32: OpenAI GPT-6 Luna
    "ai_monthly_call_limit": 300,
    "ai_monthly_cost_limit_krw": 30000,
    "ai_send_images": False,
    "inquiry_retention_months": 3,       # 계약 안 된 문의 개인정보 보관(최종 상담일로부터, Form A 안내, D-40)
    "shop_name": "틴틴골드",
    "quote_revision_scope_default": "상세 CAD 단계에서 디자인 수정은 2회까지 포함돼요. 최종 CAD를 승인한 뒤에는 사양을 바꾸기 "
                                    "어렵고, 꼭 필요하면 추가 견적과 재동의를 거쳐요.",  # D-34 기본 문구(2026-10-25 확정)
    "quote_delivery_default": "검수를 통과하고 잔금이 확인되면 택배로 발송해요. 보통 발송 다음 날부터 1~3일 안에 도착해요"
                              "(도서산간 제외).",
    # 2026-10-25 시뮬레이션 S-21: 결제처가 정해지기 전(PAY-01·02)에는 결제처 이름을 쓰지 않는다
    # S-26: 결제 요청마다 고객에게 보낼 ‘어디서·어떻게’ 안내(예: 스마트스토어 상품 주소). 비어 있으면 화면에서 채우라고 알린다
    "payment_how": "",
    "quote_payment_guide": "결제 방법은 계약 동의 후 카카오톡으로 따로 안내해 드려요. 예약금이 확인되면 상세 CAD를 시작하고, "
                           "잔금이 확인되면 제작을 발주해요.",
    "kakao_channel_url": "https://pf.kakao.com/_dxhAWX",  # 틴틴골드 카카오톡 채널 — 견적서·카톡용 텍스트 문의처(D-27)               # D-19 (TBD-001 확정)
    "consent_version": "consent_v1",
    "tax_type": "unset",                 # H1 r1 Q9: unset | general | simplified | exempt (TBD-009)
    "simplified_vat_rate_pct": 20,       # 간이과세 업종별 부가가치율(%) — 업종 확인 필요(TBD-009)
}


def get(ctx, key: str):
    row = ctx.db.one("SELECT value_json FROM settings WHERE key=?", (key,))
    return jload(row["value_json"]) if row else DEFAULTS.get(key)


def normalize_kakao_channel(v: str | None) -> str:
    """카카오톡 채널 주소 검사·정리 → 채널 URL(https). 빈 값은 '표시 안 함'. 채팅 URL(/chat)을 넣어도 채널 URL로 저장."""
    import re
    v = (v or "").strip()
    if not v:
        return ""
    v = re.sub(r"^(https?://)?", "https://", v, count=1)
    m = re.fullmatch(r"https://pf\.kakao\.com/(_[A-Za-z0-9]{3,32})(/chat)?/?", v)
    if not m:
        raise ValueError("카카오톡 채널 주소는 https://pf.kakao.com/_로 시작해요(채널 관리자센터의 채널 URL).")
    return f"https://pf.kakao.com/{m.group(1)}"


def kakao_chat_url(channel_url: str | None) -> str:
    return f"{channel_url}/chat" if channel_url else ""


def effective_google_mode(ctx) -> str:
    """실제로 쓸 Google 모드. auto는 Google 계정 연결(refresh token) 여부로 정한다(D-20)."""
    mode = get(ctx, "google_mode")
    if mode == "auto":
        return "live" if ctx.cfg.secret("JOS_GOOGLE_REFRESH_TOKEN") else "mock"
    return mode


LEGACY_D20 = {"google_mode": ("mock", "auto"), "google_form_id": ("mock-form", DEFAULTS["google_form_id"])}
D20_MARK = "_migrated_d20"


def migrate_legacy(ctx) -> None:
    """D-20 이전에 설치된 PC는 첫 설정에서 google_mode=mock, 폼 ID=mock-form이 저장돼 있다.
    그대로 두면 Google 계정을 연결해도 시험용으로 남으므로 한 번만 새 기본값으로 바꾼다."""
    if ctx.db.one("SELECT 1 FROM settings WHERE key=?", (D20_MARK,)):
        return
    for key, (old, new) in LEGACY_D20.items():
        if ctx.db.one("SELECT 1 FROM settings WHERE key=?", (key,)) and get(ctx, key) == old:
            put(ctx, key, new, None)
    with ctx.db.tx() as c:
        c.execute("INSERT INTO settings(key, value_json, updated_by, updated_at) VALUES (?,?,?,?)",
                  (D20_MARK, jdump(True), None, clock.now_iso()))


def all_settings(ctx) -> dict:
    out = dict(DEFAULTS)
    for r in ctx.db.all("SELECT key, value_json FROM settings WHERE key NOT LIKE '\\_%' ESCAPE '\\'"):
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
