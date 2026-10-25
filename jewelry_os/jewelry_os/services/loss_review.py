"""견적서 청약철회 예외 고지 검토(D-36): 규칙 검사(D-35) + AI 검토 + 사람의 최종 판단.

- AI는 개별 제작 특성·손해 사유가 전자상거래법 시행령 제21조 요건에 비추어 충분히 구체적인지 기준별로 의견과 고친 문구를 낸다.
- 결정은 사람(아버님·운영자)이 한다: AI가 ‘충분’이면 확인, 아니면 근거를 적고 그대로 진행(덮어쓰기)하거나 문구를 고친다.
- 문구가 바뀌면(해시가 달라지면) 다시 검토·확인해야 한다. 계약 동의 링크는 사람이 확인한 뒤에만 만든다.
- 보내는 값: 개별 제작 특성·손해 사유·제품·사양(사이즈·소재·각인·스톤). 고객 이름·연락처는 보내지 않는다."""
from __future__ import annotations

import hashlib
import logging

from ..adapters.ai import LOSS_CRITERIA, LOSS_PROMPT_VERSION, AnalyzerError
from ..core import clock
from ..core.db import jdump, jload
from ..domain.guards import TransitionBlocked, unmet
from ..domain.states import SPEC_FIELDS
from . import analysis, audit, settings

log = logging.getLogger("jos.ai")
VERDICT_LABEL = {"sufficient": "충분해 보여요", "needs_work": "보완하면 좋아요", "insufficient": "부족해 보여요"}
CRITERIA_LABEL = {"individual_production": "이 고객만을 위한 개별 생산임이 드러나는가",
                  "specific_loss": "되돌릴 수 없는 손해가 구체적인가",
                  "this_transaction": "일반 문구가 아니라 이 거래의 사정인가",
                  "no_blanket_ban": "모든 환불을 일괄로 막는 표현이 없는가",
                  "consistent_with_spec": "견적 사양과 맞는가"}


def _admin(actor):
    if actor is None or actor.role not in ("maker", "operator"):
        raise TransitionBlocked([unmet("G-ROLE-MAKER")])


def payload(q, product_type) -> dict:
    spec = jload(q["spec_json"], {})
    return {"product_type": product_type, "spec": {k: spec.get(k) or None for k, _ in SPEC_FIELDS},
            "custom_made": (spec.get("custom_made") or "").strip(),
            "loss_reasons": analysis.redact_text((spec.get("loss_reasons") or "").strip())}


def text_sha(q) -> str:
    spec = jload(q["spec_json"], {})
    return hashlib.sha256(jdump({k: spec.get(k) for k in ("custom_made", "loss_reasons", "size", "engraving", "stones",
                                                          "material", "purity")}).encode()).hexdigest()


def latest(ctx, quote_id):
    return ctx.db.one("SELECT * FROM quote_loss_reviews WHERE quote_id=? ORDER BY id DESC LIMIT 1", (quote_id,))


def run(ctx, quote_id, actor) -> str:
    """AI 검토 실행. 반환: OK·BUDGET·SCHEMA·TIMEOUT 등."""
    _admin(actor)
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (quote_id,))
    o = ctx.db.one("SELECT product_type FROM orders WHERE id=?", (q["order_id"],))
    provider = settings.get(ctx, "ai_provider")
    status, result = "OK", None
    if analysis.over_budget(ctx):
        status = "BUDGET"
    else:
        try:
            raw = ctx.analyzer.review_loss(payload(q, o["product_type"]))
            result = _validate(raw)
            status = "OK" if result else "SCHEMA"
        except AnalyzerError as e:
            status = e.kind
        with ctx.db.tx() as c:
            c.execute("INSERT INTO ai_usage(month, calls, est_cost_krw) VALUES (?,1,?) ON CONFLICT(month) DO UPDATE SET "
                      "calls=calls+1, est_cost_krw=est_cost_krw+excluded.est_cost_krw",
                      (analysis._month(), analysis.EST_COST_KRW.get(provider, 60)))
    with ctx.db.tx() as c:
        rid = c.execute("INSERT INTO quote_loss_reviews(quote_id, text_sha256, status, result_json, model_id, prompt_version, "
                        "created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (quote_id, text_sha(q), status, jdump(result) if result else None,
                         getattr(ctx.analyzer, "model_id", provider), LOSS_PROMPT_VERSION, actor.id,
                         clock.now_iso())).lastrowid
        audit.record(c, "quote_loss_review", rid, actor, "loss_review_run", order_id=q["order_id"], next=status,
                     detail={"verdict": (result or {}).get("verdict")})
    if status != "OK":
        log.warning("loss review failed quote=%s kind=%s", quote_id, status)
    return status


def _validate(raw) -> dict | None:
    if not isinstance(raw, dict) or raw.get("verdict") not in VERDICT_LABEL:
        return None
    crit = [{"key": c["key"], "met": bool(c.get("met")), "comment": str(c.get("comment") or "")[:200]}
            for c in raw.get("criteria") or [] if isinstance(c, dict) and c.get("key") in LOSS_CRITERIA]
    return {"verdict": raw["verdict"], "criteria": crit,
            "suggestions": [str(x)[:300] for x in raw.get("suggestions") or []][:5],
            "improved_custom_made": (raw.get("improved_custom_made") or None),
            "improved_loss_reasons": (raw.get("improved_loss_reasons") or None)}


def decide(ctx, quote_id, actor, note: str = "") -> str:
    """사람의 최종 판단. AI가 ‘충분’이면 확인(ACCEPTED), 그 밖(부족·보완·AI 실패)은 근거를 적어 진행(OVERRIDDEN)."""
    _admin(actor)
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (quote_id,))
    r = latest(ctx, quote_id)
    if r is None or r["text_sha256"] != text_sha(q):
        raise TransitionBlocked([unmet("G-LOSS-REVIEW-FIRST")])
    verdict = (jload(r["result_json"], {}) or {}).get("verdict") if r["status"] == "OK" else None
    decision = "ACCEPTED" if verdict == "sufficient" else "OVERRIDDEN"
    if decision == "OVERRIDDEN" and not (note or "").strip():
        raise TransitionBlocked([unmet("G-LOSS-OVERRIDE-NOTE")])
    with ctx.db.tx() as c:
        c.execute("UPDATE quote_loss_reviews SET decision=?, decision_note=?, decided_by=?, decided_at=? WHERE id=?",
                  (decision, (note or "").strip() or None, actor.id, clock.now_iso(), r["id"]))
        audit.record(c, "quote_loss_review", r["id"], actor, "loss_review_decided", order_id=q["order_id"],
                     next=decision, reason=(note or "").strip() or None, detail={"verdict": verdict})
    return decision


def confirmed(ctx, q) -> bool:
    """지금 문구에 대해 사람이 최종 판단했는가."""
    return bool(ctx.db.one("SELECT 1 FROM quote_loss_reviews WHERE quote_id=? AND text_sha256=? AND decision IS NOT NULL",
                           (q["id"], text_sha(q))))


def view(ctx, q) -> dict:
    r = latest(ctx, q["id"])
    res = jload(r["result_json"], None) if r else None
    return {"r": r, "res": res, "current": bool(r and r["text_sha256"] == text_sha(q)), "confirmed": confirmed(ctx, q),
            "verdict_label": VERDICT_LABEL.get((res or {}).get("verdict"), None)}
