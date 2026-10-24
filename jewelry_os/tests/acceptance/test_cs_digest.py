"""D-31 카카오톡 대화 AI 정리(정해진 JSON) → 화면 카드·답변 초안·처리 버튼 — 가짜 AI(규칙)로 시험."""
import json

from helpers import get, new_order, order_at

from jewelry_os.services import cs, cs_digest, kakao_import, refunds, settings
from webutil import web

CHAT = """2026년 10월 23일 오후 3:15, 김하늘 : 안녕하세요 18K로 반지 만들고 싶어요
2026년 10월 23일 오후 3:16, 김하늘 : 12호로 가능할까요? 연락은 010-1234-5678
2026년 10월 23일 오후 3:17, 김하늘 : 안쪽 각인도 넣을 수 있나요?
"""


def _digest(ctx, o):
    v = cs_digest.view(ctx, order=o)
    return v["d"]["status"], v["r"]


def test_paste_creates_structured_digest_with_redaction(ctx, maker, operator):
    no = new_order(ctx, "r1")
    o = get(ctx, no)
    st = kakao_import.import_chat(ctx, operator, CHAT, order=o)
    assert st["digest"] == "OK"
    status, r = _digest(ctx, o)
    assert status == "OK" and set(r) == {"summary", "open_questions", "decisions", "requests", "spec_mentions", "urgency",
                                         "unclear"}
    assert [q["topic"] for q in r["open_questions"]] == ["size", "engraving"]
    assert r["spec_mentions"]["purity"] == "18K" and r["spec_mentions"]["size"] == "12호"
    sent = ctx.analyzer.calls[-1]
    assert "1234-5678" not in sent and "김하늘" not in sent and '"from": "customer"' in sent  # 가림·이름 없음
    page = web(ctx, "operator").get(f"/orders/{no}/cs").text
    assert "대화 정리" in page and "답해야 할 질문" in page and "아버님 판단" in page
    assert 'value="12호로 가능할까요?' in page  # 답변 초안 질문 칸 = 첫 남은 질문
    assert kakao_import.import_chat(ctx, operator, CHAT, order=o)["digest"] is None  # 새 메시지 없으면 다시 안 함
    assert ctx.db.scalar("SELECT COUNT(*) FROM cs_digests") == 1


def test_draft_uses_digest(ctx, maker, operator):
    seen = {}
    real = ctx.analyzer.draft_cs

    def spy(question, order_ctx, policy):
        seen["ctx"] = order_ctx
        return real(question, order_ctx, policy)
    ctx.analyzer.draft_cs = spy
    no = new_order(ctx, "r1")
    o = get(ctx, no)
    kakao_import.import_chat(ctx, operator, CHAT, order=o)
    cs.create_draft(ctx, o, operator, "12호로 가능할까요?")
    dg = seen["ctx"]["chat_digest"]
    assert dg["spec_mentions"]["size"] == "12호" and "12호로 가능할까요?" in dg["open_questions"][0]


def test_invalid_refs_and_schema_errors_are_safe(ctx, maker, operator):
    no = new_order(ctx, "r1")
    o = get(ctx, no)

    def fake(messages):
        return {"summary": "요약", "open_questions": [], "urgency": "normal", "unclear": [],
                "decisions": [{"kind": "design_approval", "text": "지어낸 승인", "message_ids": [99999]}],
                "requests": [{"kind": "cancel_refund", "text": "취소", "message_ids": [messages[0]["id"]]}],
                "spec_mentions": {"material": None, "purity": None, "size": None, "stones": None, "engraving": None,
                                  "budget": None, "desired_date": None}}
    ctx.analyzer.digest_chat = fake
    kakao_import.import_chat(ctx, operator, CHAT, order=o)
    status, r = _digest(ctx, o)
    assert status == "OK" and r["decisions"] == [] and len(r["requests"]) == 1  # 근거 없는 결정은 버림
    ctx.analyzer.digest_chat = lambda m: {"oops": True}
    assert cs_digest.run(ctx, operator, order=o, force=True) == "SCHEMA"
    assert "형식 오류" in web(ctx, "operator").get(f"/orders/{no}/cs").text


def test_budget_limit_skips_ai(ctx, maker, operator):
    settings.put(ctx, "ai_monthly_call_limit", 0, operator)
    no = new_order(ctx, "r1")
    n = len(ctx.analyzer.calls)
    st = kakao_import.import_chat(ctx, operator, CHAT, order=get(ctx, no))
    assert st["new"] == 3 and st["digest"] == "BUDGET" and len(ctx.analyzer.calls) == n


def test_design_approval_button_records_with_quote(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    from jewelry_os.services import manufacturing
    manufacturing.upload_design(ctx, get(ctx, o["order_no"]), maker, {"stl": ("r.stl", b"solid v1"), "cad": ("r.3dm", b"3dm")})
    kakao_import.import_chat(ctx, operator, "2026년 10월 24일 오전 10:00, 김하늘 : 시안 좋아요 이대로 진행해 주세요", order=o)
    _, r = _digest(ctx, get(ctx, o["order_no"]))
    assert r["decisions"][0]["kind"] == "design_approval"
    wm = web(ctx, "maker")
    page = wm.get(f"/orders/{o['order_no']}/cs").text
    assert "최종 시안 동의로 기록" in page
    ev = r["decisions"][0]["text"]
    resp = wm.post(f"/orders/{o['order_no']}/actions/record_design_ok",
                   data={"confirm": "on", "channel": "카카오톡", "evidence": ev + " (카카오톡 대화 원문)", "_tab": "cs"})
    assert resp.status_code == 303 and get(ctx, o["order_no"])["lifecycle_state"] == "CUSTOMER_DESIGN_APPROVED"
    acc = ctx.db.one("SELECT * FROM customer_acceptances WHERE kind='design'")
    assert "시안 좋아요" in acc["evidence_text"]


def test_refund_request_button(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    kakao_import.import_chat(ctx, operator, "2026년 10월 20일 오전 10:30, 김하늘 : 죄송한데 주문 취소하고 환불받고 싶어요", order=o)
    page = web(ctx, "operator").get(f"/orders/{o['order_no']}/cs").text
    assert "취소·환불 요청으로 접수" in page and 'name="requested_at" value="2026-10-20T10:30"' in page
    r = web(ctx, "operator").post(f"/orders/{o['order_no']}/refunds", data={
        "reason_kind": "CHANGE_OF_MIND", "via": "카카오톡", "reason": "죄송한데 주문 취소하고 환불받고 싶어요",
        "requested_at": "2026-10-20T10:30"})
    assert r.status_code == 303
    rf = refunds.get(ctx, ctx.db.scalar("SELECT id FROM refund_requests"))
    assert rf["requested_at"] == "2026-10-20T01:30:00+00:00" and rf["production_stage_at_request"] == "R2"


def test_pre_order_thread_digest_and_link(ctx, maker, operator):
    w = web(ctx, "operator")
    r = w.post("/cs/import", data={"chat": CHAT})
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    v = cs_digest.view(ctx, thread_id=tid)
    assert v["d"]["status"] == "OK" and v["d"]["order_id"] is None
    assert "대화 정리" in w.get(f"/cs/threads/{tid}").text
    assert w.post(f"/cs/threads/{tid}/digest", data={}).status_code == 303
    no = new_order(ctx, "r1")
    w.post(f"/cs/threads/{tid}/link", data={"order_no": no})
    v2 = cs_digest.view(ctx, order=get(ctx, no))
    assert v2 and v2["d"]["message_count"] == 3 and json.loads(v2["d"]["result_json"])["open_questions"]
