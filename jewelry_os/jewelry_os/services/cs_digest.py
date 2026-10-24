"""카카오톡 대화 AI 정리(D-31) — 붙여넣은 대화를 정해진 JSON(DIGEST_SCHEMA)으로 정리해 화면·답변 초안에 쓴다.

- 보내기 전에 전화번호·이메일·주소를 가린다(의뢰 정리와 같은 규칙). 고객 이름도 보내지 않는다(customer/shop만).
- AI 결과는 표시·제안용이다. 상태·금액을 바꾸지 않고, 최종 시안 동의·환불 접수는 사람이 버튼으로 기록한다.
- 근거 메시지 ID가 대화에 없는 항목은 버린다. 형식이 틀리면 ‘정리 실패’로 남기고 앱은 계속 동작한다.
- AI 월 호출·비용 한도는 의뢰 정리와 함께 센다."""
from __future__ import annotations

import logging

from ..adapters.ai import (DECISION_KINDS, DIGEST_PROMPT_VERSION, QUESTION_TOPICS, REQUEST_KINDS, SPEC_KEYS,
                           AnalyzerError)
from ..core import clock
from ..core.db import jdump, jload
from . import analysis, audit, settings

log = logging.getLogger("jos.ai")
MAX_MESSAGES = 120  # 최근 메시지만(토큰·비용)
TOPIC_LABEL = {"price": "가격", "deadline": "납기", "size": "사이즈", "design": "디자인", "material": "소재",
               "stones": "스톤", "engraving": "각인", "payment": "결제", "shipping": "배송", "other": "기타"}
KIND_LABEL = {"design_approval": "최종 시안 승인", "quote_agreement": "견적 동의", "spec_change": "사양 변경",
              "cancel_refund": "취소·환불 요청", "defect_as": "하자·A/S", "change_request": "변경 요청", "other": "기타"}
STATUS_LABEL = {"OK": "AI 정리됨", "BUDGET": "AI 월 한도 초과 — 정리 안 됨", "SCHEMA": "AI 결과 형식 오류 — 정리 안 됨",
                "TIMEOUT": "AI 응답 지연 — 정리 안 됨", "RATE": "AI 요청 제한 — 정리 안 됨", "AUTH": "AI 키 오류 — 정리 안 됨",
                "REFUSAL": "AI가 정리를 거절 — 직접 확인", "ERROR": "AI 오류 — 정리 안 됨"}


def thread_messages(ctx, thread_id=None, order_id=None):
    if order_id is not None:
        return ctx.db.all("SELECT m.* FROM cs_messages m JOIN cs_threads t ON t.id=m.thread_id WHERE t.order_id=? "
                          "ORDER BY m.event_at, m.id", (order_id,))
    return ctx.db.all("SELECT * FROM cs_messages WHERE thread_id=? ORDER BY event_at, id", (thread_id,))


def payload(msgs) -> list[dict]:
    """AI에 보낼 메시지(가림 처리, 이름 없음)."""
    out = []
    for m in msgs[-MAX_MESSAGES:]:
        text = m["content_redacted"] or m["summary"] or ""
        if m["direction"] == "in" and ": " in (m["summary"] or "") and not m["content_redacted"]:
            text = m["summary"].split(": ", 1)[1]  # 요지 앞 ‘이름: ’ 떼기
        out.append({"id": m["id"], "from": "customer" if m["direction"] == "in" else "shop",
                    "at": m["event_at"], "text": analysis.redact_text(text)})
    return out


def validate(raw, ids: set[int]) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict) or not isinstance(raw.get("summary"), str):
        return None, "SCHEMA"
    try:
        def refs(x):
            return [i for i in (x.get("message_ids") or []) if isinstance(i, int) and i in ids]
        qs = [{"question": str(q["question"])[:300], "needs": q["needs"] if q.get("needs") in ("maker", "operator") else "maker",
               "topic": q["topic"] if q.get("topic") in QUESTION_TOPICS else "other", "message_ids": refs(q)}
              for q in raw.get("open_questions") or []]
        dec = [{"kind": d["kind"], "text": str(d["text"])[:300], "message_ids": refs(d)}
               for d in raw.get("decisions") or [] if d.get("kind") in DECISION_KINDS]
        req = [{"kind": r["kind"], "text": str(r["text"])[:300], "message_ids": refs(r)}
               for r in raw.get("requests") or [] if r.get("kind") in REQUEST_KINDS]
        spec = {k: (str(v)[:80] if v not in (None, "") else None) for k, v in (raw.get("spec_mentions") or {}).items()
                if k in SPEC_KEYS}
    except (KeyError, TypeError, AttributeError):
        return None, "SCHEMA"
    # 근거 없는 결정·요청은 버린다(버튼 후보로 쓰이므로)
    dec = [d for d in dec if d["message_ids"]]
    req = [r for r in req if r["message_ids"]]
    return {"summary": raw["summary"][:600], "open_questions": qs[:8], "decisions": dec, "requests": req,
            "spec_mentions": {k: spec.get(k) for k in SPEC_KEYS},
            "urgency": raw.get("urgency") if raw.get("urgency") in ("normal", "urgent") else "normal",
            "unclear": [str(u)[:200] for u in (raw.get("unclear") or [])][:8]}, None


def run(ctx, actor=None, thread_id=None, order=None, force=False) -> str:
    """대화 정리. order가 있으면 주문의 모든 대화, 없으면 그 문의 스레드. 반환: 상태(OK·BUDGET·SCHEMA…·SAME·EMPTY)."""
    msgs = thread_messages(ctx, thread_id=thread_id, order_id=order["id"] if order else None)
    if not msgs:
        return "EMPTY"
    if order is not None:
        from . import cs
        with ctx.db.tx() as c:
            thread_id = cs.thread_for(ctx, c, order["id"])
    upto = max(m["id"] for m in msgs)
    last = latest(ctx, thread_id=thread_id, order=order)
    if last and not force and last["upto_message_id"] >= upto and last["status"] == "OK":
        return "SAME"
    sent = payload(msgs)
    provider = settings.get(ctx, "ai_provider")
    status, result, err = "OK", None, None
    if analysis.over_budget(ctx):
        status, err = "BUDGET", "BUDGET"
    else:
        try:
            result, err = validate(ctx.analyzer.digest_chat(sent), {m["id"] for m in sent})
            if err:
                status = err
        except AnalyzerError as e:
            status, err = e.kind, e.kind
        with ctx.db.tx() as c:
            c.execute("INSERT INTO ai_usage(month, calls, est_cost_krw) VALUES (?,1,?) ON CONFLICT(month) DO UPDATE SET "
                      "calls=calls+1, est_cost_krw=est_cost_krw+excluded.est_cost_krw",
                      (analysis._month(), analysis.EST_COST_KRW.get(provider, 60)))
    with ctx.db.tx() as c:
        did = c.execute("INSERT INTO cs_digests(thread_id, order_id, upto_message_id, message_count, status, result_json, "
                        "error_kind, model_id, prompt_version, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (thread_id, order["id"] if order else None, upto, len(msgs), status,
                         jdump(result) if result else None, err, getattr(ctx.analyzer, "model_id", provider),
                         DIGEST_PROMPT_VERSION, getattr(actor, "id", None), clock.now_iso())).lastrowid
        audit.record(c, "cs_digest", did, actor, "cs_digest_created", order_id=order["id"] if order else None,
                     next=status, detail={"messages": len(msgs)})
    if status != "OK":
        log.warning("chat digest failed thread=%s kind=%s", thread_id, err)
    return status


def latest(ctx, thread_id=None, order=None):
    if order is not None:
        return ctx.db.one("SELECT * FROM cs_digests WHERE order_id=? ORDER BY id DESC LIMIT 1", (order["id"],))
    return ctx.db.one("SELECT * FROM cs_digests WHERE thread_id=? AND order_id IS NULL ORDER BY id DESC LIMIT 1",
                      (thread_id,))


def view(ctx, thread_id=None, order=None) -> dict | None:
    """화면용: 정리 결과 + 근거 메시지 원문 + 새 메시지가 붙었는지."""
    d = latest(ctx, thread_id=thread_id, order=order)
    if d is None:
        return None
    msgs = {m["id"]: m for m in thread_messages(ctx, thread_id=thread_id, order_id=order["id"] if order else None)}
    r = jload(d["result_json"], None)
    if r:
        for group in ("open_questions", "decisions", "requests"):
            for item in r[group]:
                item["quotes"] = [{"id": i, "text": (msgs[i]["content_redacted"] or msgs[i]["summary"]),
                                   "at": msgs[i]["event_at"]} for i in item["message_ids"] if i in msgs]
    return {"d": d, "r": r, "status_label": STATUS_LABEL.get(d["status"], d["status"]),
            "stale": bool(msgs) and max(msgs) > d["upto_message_id"]}


def for_draft(ctx, order) -> dict | None:
    """답변 초안 근거로 넘길 정리(최신·성공한 것만, 가림 처리된 값)."""
    d = ctx.db.one("SELECT * FROM cs_digests WHERE order_id=? AND status='OK' ORDER BY id DESC LIMIT 1", (order["id"],))
    if d is None:
        return None
    r = jload(d["result_json"], {})
    return {"summary": r.get("summary"), "open_questions": [q["question"] for q in r.get("open_questions", [])],
            "spec_mentions": {k: v for k, v in (r.get("spec_mentions") or {}).items() if v},
            "requests": [KIND_LABEL.get(x["kind"], x["kind"]) for x in r.get("requests", [])]}
