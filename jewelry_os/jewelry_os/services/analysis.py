"""AI 의뢰 정리(FUN-002, DD-006). AI 결과는 표시용 — 상태·금액을 바꾸지 않는다(SRS-119)."""
from __future__ import annotations

import logging
import re

from ..adapters.ai import PROMPT_VERSION, AnalyzerError
from ..core import clock
from ..core.db import jdump, jload
from ..domain import transitions as tr
from ..domain.states import S
from . import settings

log = logging.getLogger("jos.ai")

SENT_FIELDS = ["product_type", "description", "material", "size", "stones_engraving", "budget_range", "desired_date"]
_PHONE = re.compile(r"(?:\+?82[-.\s]?0?|0)1[016789][-.\s]?\d{3,4}[-.\s]?\d{4}")
_LANDLINE = re.compile(r"0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# 주소: “○○시/도/구/군 … ○○로/길 123” 또는 “○○동 123-4” 형태(DD-006 B-11)
_ADDRESS = re.compile(r"[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|시|도)\s*[가-힣]+(?:시|군|구)[^\n,.]*?"
                      r"(?:로|길|동|읍|면|리)\s*\d+(?:-\d+)?(?:\s*\d+\s*(?:동|호|층))*"
                      r"|[가-힣0-9]+(?:로|길)\s*\d+(?:-\d+)?(?:\s*\d+\s*(?:동|호|층))*"
                      r"|[가-힣]+동\s*\d+-\d+")
EST_COST_KRW = {"mock": 0, "claude": 60, "openai": 5}  # 호출당 어림값(원) — 월 비용 상한 계산용


def redact_text(s):
    if not isinstance(s, str):
        return s
    s = _EMAIL.sub("[가림]", s)
    s = _PHONE.sub("[가림]", s)
    s = _LANDLINE.sub("[가림]", s)
    return _ADDRESS.sub("[주소 가림]", s)


def build_payload(parsed: dict) -> dict:
    p = {k: redact_text(parsed.get(k)) for k in SENT_FIELDS if parsed.get(k) not in (None, "")}
    p["reference_count"] = len(parsed.get("reference_files") or [])
    return p


def amounts(text: str) -> set[int]:
    """금액 정규화(원 단위 정수): '50만원'→500000, '1,200,000원'→1200000, '120만 5천원'→1205000, '3천원'→3000."""
    t = str(text).replace(",", "")
    out: set[int] = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*만(?:\s*(\d+)\s*천)?", t):
        out.add(int(round(float(m.group(1)) * 10000)) + (int(m.group(2)) * 1000 if m.group(2) else 0))
    for m in re.finditer(r"(\d+)\s*천", t):
        out.add(int(m.group(1)) * 1000)
    for m in re.finditer(r"\d+", t):
        out.add(int(m.group()))
    return out


def month_days(text: str) -> set[tuple[int, int]]:
    """날짜 정규화((월, 일)) — 연도는 비교하지 않는다."""
    t = str(text)
    out = set()
    for m in re.finditer(r"(?:\d{4}[-./년]\s*)?(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?", t):
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.add((mo, d))
    return out


def validate(raw: dict, source_text: str) -> tuple[dict | None, str | None]:
    """스키마 검사 + 숫자 날조 제거(DD-006). 원문에 근거가 있는 값은 남긴다. (brief, error_kind)."""
    need = {"product_type", "fields", "required_missing_fields", "risk_flags", "customer_questions", "assumptions"}
    if not isinstance(raw, dict) or not need <= raw.keys() or not isinstance(raw.get("fields"), dict):
        return None, "SCHEMA"
    b = dict(raw)
    b["fields"] = dict(raw["fields"])
    b["assumptions"] = list(raw.get("assumptions") or [])
    src_amounts = amounts(source_text)
    src_dates = month_days(source_text)
    src_digits = set(re.findall(r"\d+", source_text))
    for k in ("budget", "purity", "deadline"):
        v = b["fields"].get(k)
        if v is None:
            continue
        sv = str(v)
        if k == "budget":
            got = amounts(sv)
            ok = bool(got) and got <= src_amounts  # 출력 금액 토큰이 모두 원문 근거가 있어야 유지(SRS-022)
        elif k == "deadline":
            got = month_days(sv)
            ok = bool(got) and got <= src_dates if got else not re.search(r"\d", sv)
        else:  # purity(18K, 14K, PT950 …)
            toks = re.findall(r"\d+", sv)
            ok = all(tk in src_digits for tk in toks)
        if not ok:
            b["fields"][k] = None
            b["assumptions"].append(f"원문에 없는 값이라 비웠어요: {k}={v}")
    b["customer_questions"] = list(raw.get("customer_questions") or [])[:3]
    b["requires_human_approval"] = True
    b.setdefault("policy_refs", [])
    b.setdefault("draft_reply", None)
    return b, None


def _month() -> str:
    return clock.kst(clock.now()).strftime("%Y-%m")


def usage(ctx) -> dict:
    r = ctx.db.one("SELECT * FROM ai_usage WHERE month=?", (_month(),))
    return {"calls": r["calls"] if r else 0, "cost": r["est_cost_krw"] if r else 0,
            "call_limit": int(settings.get(ctx, "ai_monthly_call_limit")),
            "cost_limit": int(settings.get(ctx, "ai_monthly_cost_limit_krw"))}


def over_budget(ctx) -> bool:
    u = usage(ctx)
    return u["calls"] >= u["call_limit"] or u["cost"] >= u["cost_limit"]


def _advance_system(ctx, order_id):
    o = ctx.db.one("SELECT lifecycle_state FROM orders WHERE id=?", (order_id,))
    if o["lifecycle_state"] == S.INQUIRY_RECEIVED.value:
        tr.system_perform(ctx, order_id, "ingest_brief")
        o = ctx.db.one("SELECT lifecycle_state FROM orders WHERE id=?", (order_id,))
    if o["lifecycle_state"] == S.AI_BRIEF_READY.value:
        tr.system_perform(ctx, order_id, "to_review")


def process_new(ctx, order_id) -> str:
    """새 주문/새 revision: 분석 후 시스템 전이. 동의 없음이면 AI 미실행(SRS-006, B-10)."""
    holds = {r["reason"] for r in ctx.db.all("SELECT reason FROM holds WHERE order_id=? AND cleared_at IS NULL",
                                              (order_id,))}
    if "NO_CONSENT" in holds:
        _advance_system(ctx, order_id)
        return "no_consent"
    status = run_brief(ctx, order_id)
    _advance_system(ctx, order_id)
    return status


def run_brief(ctx, order_id, force=False) -> str:
    from . import intake
    o = ctx.db.one("SELECT * FROM orders WHERE id=?", (order_id,))
    parsed, rev = intake.answers_of(ctx, o)
    pol = settings.approved_policy(ctx, "cs")
    pol_ver = f"cs:v{pol['version']}" if pol else "none"
    existing = ctx.db.one("SELECT * FROM ai_tasks WHERE order_id=? AND input_revision=? AND task_type='brief' "
                          "AND policy_version=?", (order_id, o["input_revision"], pol_ver))
    if existing and not force and existing["status"] == "OK":
        return "duplicate"
    provider = settings.get(ctx, "ai_provider")
    analyzer = ctx.analyzer
    payload = build_payload(parsed)
    status, result, err = "OK", None, None
    if over_budget(ctx):
        status, err = "BUDGET", "BUDGET"
    else:
        try:
            raw = analyzer.analyze(payload, pol["body"] if pol else None)
            result, err = validate(raw, " ".join(str(v) for v in payload.values() if isinstance(v, str)))
            if err:
                status = "SCHEMA"
        except AnalyzerError as e:
            status, err = e.kind, e.kind
        with ctx.db.tx() as c:
            c.execute("INSERT INTO ai_usage(month, calls, est_cost_krw) VALUES (?,1,?) ON CONFLICT(month) DO UPDATE SET "
                      "calls=calls+1, est_cost_krw=est_cost_krw+excluded.est_cost_krw",
                      (_month(), EST_COST_KRW.get(provider, 60)))
    with ctx.db.tx() as c:
        if existing:
            c.execute("DELETE FROM ai_tasks WHERE id=?", (existing["id"],))  # 재시도: 같은 키의 실패 결과를 교체
        c.execute("INSERT INTO ai_tasks(order_id, input_revision, task_type, policy_version, prompt_version, model_id, "
                  "status, result_json, error_kind, sent_fields, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (order_id, o["input_revision"], "brief", pol_ver, PROMPT_VERSION, getattr(analyzer, "model_id", provider),
                   status, jdump(result) if result else None, err, jdump(sorted(k for k in payload if k != "reference_count")),
                   clock.now_iso()))
    if status != "OK":
        log.warning("ai brief failed order=%s kind=%s", order_id, err)
    return status


def latest_brief(ctx, order_id):
    r = ctx.db.one("SELECT * FROM ai_tasks WHERE order_id=? AND task_type='brief' ORDER BY id DESC LIMIT 1", (order_id,))
    if r is None:
        return None
    d = dict(r)
    d["result"] = jload(r["result_json"], None)
    return d


def pending_count(ctx) -> tuple[int, str | None]:
    """AI 대기(분석 결과 없는 활성 접수 주문) 수와 가장 오래된 시각(SRS-110)."""
    rows = ctx.db.all("SELECT o.created_at FROM orders o WHERE o.lifecycle_state IN ('INQUIRY_RECEIVED','AI_BRIEF_READY') "
                      "ORDER BY o.created_at")
    return len(rows), rows[0]["created_at"] if rows else None
