"""2026-10-25 실제 폼 다중 agent 시뮬레이션에서 나온 '높음' 문제(S-24·S-32·S-43·S-47·S-52·S-21·S-22) 회귀 시험."""
from helpers import C, get, order_at

from helpers import check

from jewelry_os.devtools import confirm_loss
from jewelry_os.domain import transitions as tr
from jewelry_os.services import contracts, orders, quotes, refunds


def _requote(ctx, o, maker, total):
    qid = quotes.draft(ctx, o, maker)
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,))
    items = quotes.items_of(ctx, qid)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(
        items=[{"kind": items[0]["kind"], "label": "줄인 사양", "amount": str(total), "cost": "100000"}],
        spec=__import__("json").loads(q["spec_json"]), tax_included=True, shipping_included=True,
        promised_date=q["promised_date"], valid_until=q["valid_until"]))
    return qid


def test_requote_before_consent_approves_the_edited_draft(ctx, maker, operator):
    """S-24: 고객 동의 전 재견적 — 되돌리기 없이, 화면에 보이는 그 초안(370,000원)이 승인된다."""
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED")
    old = tr.latest_quote(ctx.db.conn, o["id"], "APPROVED")
    rid = contracts.issue(ctx, get(ctx, o["order_no"]), operator, sleep=lambda s: None)
    qid = _requote(ctx, get(ctx, o["order_no"]), maker, 370_000)
    quotes.approve_requote(ctx, get(ctx, o["order_no"]), maker, C)
    new = tr.latest_quote(ctx.db.conn, o["id"], "APPROVED")
    assert new["id"] == qid and new["total_krw"] == 370_000
    assert ctx.db.one("SELECT status FROM quotes WHERE id=?", (old["id"],))["status"] == "SUPERSEDED"
    assert get(ctx, o["order_no"])["lifecycle_state"] == "QUOTE_APPROVED"
    assert contracts.get(ctx, rid)["status"] == "SUPERSEDED"  # 이전 견적의 계약 링크는 대체
    confirm_loss(ctx, qid, maker)
    assert contracts.issue(ctx, get(ctx, o["order_no"]), operator, sleep=lambda s: None) != rid


def test_revert_keeps_the_draft_being_edited(ctx, maker, operator):
    """S-24: 고치던 초안(v2)이 있을 때 되돌리기는 승인본을 또 복사하지 않고, 승인은 v2를 승인한다."""
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED")
    qid = _requote(ctx, get(ctx, o["order_no"]), maker, 370_000)
    tr.revert_last(ctx, o["id"], maker, "가격 협의")
    drafts = ctx.db.all("SELECT id FROM quotes WHERE order_id=? AND status='DRAFT'", (o["id"],))
    assert [d["id"] for d in drafts] == [qid]
    quotes.approve(ctx, get(ctx, o["order_no"]), maker, C)
    assert tr.latest_quote(ctx.db.conn, o["id"], "APPROVED")["total_krw"] == 370_000


def test_fully_refunded_order_cannot_start_cad(ctx, maker, operator):
    """S-32: 예약금을 전액 환급 완료해 검토 문제가 닫혀도 상세 CAD를 시작할 수 없고, 주문 취소는 된다."""
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    dep = ctx.db.scalar("SELECT expected_krw FROM payment_requests WHERE phase='DEPOSIT'")
    rid = refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "이사 때문에 취소", "카카오톡")
    refunds.approve(ctx, rid, operator, dep, "R2 자체 정책: 예약금 전액")
    refunds.complete(ctx, rid, operator, dep, "네이버 결제 취소", "취소번호 C-1")
    assert "G-REFUNDED" in check(ctx, get(ctx, o["order_no"]), "start_cad", maker)
    orders.cancel_order(ctx, get(ctx, o["order_no"]), operator, {
        **C, "fault": "CUSTOMER_CHANGE", "refund_decision": "FULL", "refund_basis": "R2", "channel": "카카오톡",
        "evidence": "취소 요청 카톡", "reason": "단순 변심"})
    assert get(ctx, o["order_no"])["lifecycle_state"] == "CANCELLED"


def test_partial_refund_does_not_block(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    rid = refunds.record(ctx, o, operator, "OTHER", "일부 조정", "카카오톡")
    refunds.approve(ctx, rid, operator, 10_000, "협의 조정")
    refunds.complete(ctx, rid, operator, 10_000, "네이버 부분 취소", "취소번호 C-2")
    assert "G-REFUNDED" not in check(ctx, get(ctx, o["order_no"]), "start_cad", maker)


def test_needs_info_can_be_released_from_the_order_screen(ctx, maker, operator):
    """S-43: 정보 요청 뒤 고객이 답하면 아버님이 화면에서 '정보 부족'을 풀 수 있고, 그러면 견적 승인이 막히지 않는다."""
    from webutil import web
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    orders.apply_hold_action(ctx, o, "request_info", maker, "소재·길이 확인 필요")
    o = order_at(ctx, maker, operator, "FREE_CONCEPT")
    assert "G-HOLD-NEEDS_INFO" in check(ctx, o, "approve_quote", maker)
    w = web(ctx, "maker")
    page = w.get(f"/orders/{o['order_no']}/quote").text
    assert "고객 답 받음 — 정보 부족 풀기" in page and 'name="reason_code" value="NEEDS_INFO"' in page
    r = w.post(f"/orders/{o['order_no']}/actions/release_hold",
               data={"reason_code": "NEEDS_INFO", "note": "문자로 답 받음", "_tab": "quote"})
    assert r.status_code == 303
    assert "G-HOLD-NEEDS_INFO" not in check(ctx, get(ctx, o["order_no"]), "approve_quote", maker)
    assert "정보 부족 풀기" not in w.get(f"/orders/{o['order_no']}/quote").text
    assert "아버님 계정에서 풀 수 있어요" not in web(ctx, "operator").get(f"/orders/{o['order_no']}").text


def test_contract_needs_quote_delivery_record(ctx, maker, operator):
    """S-47: 견적서를 보낸 기록이 없으면 Form B 제출·본인 확인을 마쳐도 계약이 유효가 아니고, 기록하면 유효가 된다."""
    from jewelry_os.devtools import form_b_response
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED")
    rid = contracts.issue(ctx, get(ctx, o["order_no"]), operator, sleep=lambda s: None)
    form_b_response(ctx, rid, email="sms-customer@example.com")
    contracts.sync_once(ctx, sleep=lambda s: None)
    contracts.review(ctx, rid, operator, "existing_contact", "문자로 본인 제출 확인", remember_email=False)
    k = contracts.get(ctx, rid)
    assert k["status"] == "REVIEW_REQUIRED"
    assert "QUOTE_NOT_DELIVERED" in {p["code"] for p in contracts.problems_of(k)}
    contracts.record_delivery(ctx, rid, operator, "sms", "문자로 사전 고지·링크만 보냄", items=["notice", "link"])
    assert contracts.get(ctx, rid)["status"] == "REVIEW_REQUIRED"  # 견적서가 빠진 전달은 안 된다
    contracts.record_delivery(ctx, rid, operator, "sms", "견적서 PDF 링크를 문자로 보냄", items=["quote"])
    assert contracts.get(ctx, rid)["status"] == "ACCEPTED"
    assert get(ctx, o["order_no"])["lifecycle_state"] == "CUSTOMER_ACCEPTED"


def test_qc_pass_needs_a_photo(ctx, maker, operator):
    """S-52: 검수 통과는 사진(각인·세팅 확대) 1장 이상이 있어야 기록된다 — 하자 분쟁 때 공방 쪽 증거."""
    import pytest
    from jewelry_os.domain.guards import TransitionBlocked
    from jewelry_os.services import manufacturing
    o = order_at(ctx, maker, operator, "FACTORY_RECEIVED")
    ok = {**{k: "pass" for k in ("spec", "purity_engraving", "size", "finish", "setting", "fit")}, **C}
    with pytest.raises(TransitionBlocked) as e:
        manufacturing.record_qc(ctx, get(ctx, o["order_no"]), maker, ok)
    assert {u.code for u in e.value.unmet} == {"G-QC-PHOTO"}
    assert manufacturing.record_qc(ctx, get(ctx, o["order_no"]), maker, ok, [("각인.jpg", b"\xff\xd8 HE")]) == "PASS"
    assert [a["filename"] for a in manufacturing.qc_photos(ctx, o["id"])] == ["각인.jpg"]
    assert get(ctx, o["order_no"])["lifecycle_state"] == "QC_PASSED"


def test_customer_quote_has_no_placeholder_or_fixed_payment_place(ctx, maker, operator):
    """S-21·S-22: 고객 견적서·카톡 텍스트에 자리표시 정책([TBD-…])과 '스마트스토어' 고정 결제처가 나가지 않는다."""
    from webutil import web
    from jewelry_os.services import settings
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED")
    pid = ctx.db.scalar("SELECT terms_policy_id FROM quotes WHERE status='APPROVED'")
    ctx.db.conn.execute("UPDATE policies SET body='[TBD-002] 사업 책임자가 확정할 문구 자리입니다.' WHERE id=?", (pid,))
    qid = ctx.db.scalar("SELECT id FROM quotes WHERE status='APPROVED'")
    w = web(ctx, "operator")
    for url in (f"/docs/quote/{qid}", f"/docs/quote/{qid}.txt"):
        t = w.get(url).text
        assert "[TBD" not in t and "(초안)" not in t and "스마트스토어" not in t
        assert "계약 동의 후 카카오톡으로 따로 안내" in t
    settings.put(ctx, "quote_payment_guide", "결제는 스마트스토어 ‘맞춤 제작 결제’ 상품에서 해 주세요.", operator)
    assert "‘맞춤 제작 결제’" in w.get(f"/docs/quote/{qid}.txt").text
    assert "[TBD" not in quotes.public_text(quotes.public_quote(ctx, qid))


def test_cs_draft_gets_intake_and_public_process_facts(ctx, maker, operator):
    """S-11·S-12: 상담 답변 초안 요청에 이미 공개된 진행 안내와 의뢰서 내용(이름·연락처 제외)이 근거로 들어간다."""
    from jewelry_os.services import cs
    o = order_at(ctx, maker, operator, "FREE_CONCEPT")
    seen = {}
    real = ctx.analyzer.draft_cs

    def spy(question, octx, policy):
        seen.update(octx)
        return real(question, octx, policy)
    ctx.analyzer.draft_cs = spy
    cs.create_draft(ctx, get(ctx, o["order_no"]), operator, "접수됐나요? 다음 절차는요?")
    assert any("1/3" in f for f in seen["process_facts"]) and any("전액 환불" in f for f in seen["process_facts"])
    assert seen["intake"] and "display_name" not in seen["intake"] and "contact_value" not in seen["intake"]
    assert seen["shop_name"]


def test_address_redaction_ignores_jewelry_specs():
    """S-10: '기념으로 18K'를 도로명 주소로 가리지 않는다(실제 주소는 그대로 가림)."""
    from jewelry_os.services.analysis import redact_text
    assert redact_text("결혼 10주년 기념으로 18K 옐로골드") == "결혼 10주년 기념으로 18K 옐로골드"
    assert redact_text("세종대로 110") == "[주소 가림]"
