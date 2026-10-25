"""2026-10-25 시뮬레이션 '중간' 문제 회귀 시험."""
from helpers import C, check, get, order_at

from jewelry_os.services import contracts, cs, cs_digest, kakao_import, orders, quotes


def test_ai_unclear_items_and_risk_codes_are_korean():
    """S-7·S-8: 불명확한 점의 영어 필드명, 모르는 위험 코드가 화면에 그대로 나오지 않는다."""
    from jewelry_os.web.filters import ai_item, ai_risk
    assert ai_item("size") == "호수·사이즈 확인 필요"
    assert ai_item("size (최종 크기 확인)") == "호수·사이즈 — 최종 크기 확인"
    assert ai_item("animal_motif 앞면에 동물 발바닥") == "확인 필요 — 앞면에 동물 발바닥"
    assert ai_item("정확한 손목 둘레") == "정확한 손목 둘레"
    assert ai_risk("animal_motif") == "확인 필요" and ai_risk("BRAND_IMITATION") == "상표·디자인 모방 의심"


def test_chat_digest_gets_form_a_context(ctx, maker, operator):
    """S-12: 대화 정리 AI에 의뢰서 내용(이름·연락처 제외)이 함께 간다."""
    o = order_at(ctx, maker, operator, "OWNER_REVIEW", desc="14K 꽃잎 팔찌. 예산 30만원")
    seen = {}
    real = ctx.analyzer.digest_chat

    def spy(messages, intake=None):
        seen["intake"] = intake
        return real(messages, intake)
    ctx.analyzer.digest_chat = spy
    kakao_import.import_chat(ctx, operator, "2026년 10월 25일 일요일\n[이미정] [오후 1:30] 견적 언제쯤 알 수 있을까요?",
                             order=get(ctx, o["order_no"]))
    assert "예산 30만원" in seen["intake"]["description"]
    assert "display_name" not in seen["intake"] and "contact_value" not in seen["intake"]


def test_first_contact_text_follows_preferred_channel(ctx, maker, operator):
    """S-14·S-45·S-48: 첫 문자는 가게·고객 이름으로 시작하고, 문자를 고른 고객에게는 견적 요약을 문자에 담는다."""
    from webutil import web
    from jewelry_os.services import intake
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED", product="목걸이 / 펜던트")
    o = get(ctx, o["order_no"])
    answers, _ = intake.answers_of(ctx, o)
    q = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='APPROVED'", (o["id"],))
    assert cs.first_contact_sms(ctx, o, answers).startswith("[틴틴골드] 김하늘 고객님, 보내 주신 목걸이·펜던트 의뢰서")
    kakao = cs.first_contact_sms(ctx, o, {**answers, "contact_kind": "카카오톡 채널"}, q)
    sms = cs.first_contact_sms(ctx, o, {**answers, "contact_kind": "문자메시지"}, q)
    assert "pf.kakao.com" in kakao and "pf.kakao.com" not in sms
    assert "1,200,000원" in sms and "견적서 파일" in sms and "목걸이 / 펜던트" not in sms
    assert "첫 연락 문자" in web(ctx, "maker").get(f"/orders/{o['order_no']}/quote").text


def test_new_quote_prefills_spec_and_keeps_extra_terms(ctx, maker, operator):
    """S-17: 첫 견적 초안에 AI 정리·의뢰서의 사양이 미리 채워지고, S-30: 추가 약속이 고객 견적서에 나온다."""
    from webutil import web
    o = order_at(ctx, maker, operator, "FREE_CONCEPT", desc="18K 금으로 12호 반지를 만들고 싶어요. 예산 50만원")
    qid = quotes.draft(ctx, get(ctx, o["order_no"]), maker)
    spec = __import__("json").loads(ctx.db.scalar("SELECT spec_json FROM quotes WHERE id=?", (qid,)))
    assert any(spec.get(k) for k in ("material", "purity", "size"))
    page = web(ctx, "maker").get(f"/orders/{o['order_no']}/quote").text
    assert page.count('name="item_') >= 6 * 5 and 'id="q-total"' in page and 'name="spec_extra_terms"' in page
    o2 = order_at(ctx, maker, operator, "QUOTE_APPROVED", rid="r2")
    q2 = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='APPROVED'", (o2["id"],))
    nid = quotes.draft(ctx, get(ctx, o2["order_no"]), maker)
    f = quotes.QuoteForm(items=[{"kind": "metal", "label": "18K", "amount": "900000"}],
                         spec={**__import__("json").loads(q2["spec_json"]), "extra_terms": "11/8까지 동의하면 금값이 올라도 그대로"},
                         tax_included=True, shipping_included=True, promised_date=q2["promised_date"], valid_until=q2["valid_until"])
    quotes.save(ctx, nid, maker, f)
    quotes.approve_requote(ctx, get(ctx, o2["order_no"]), maker, C)
    assert "추가 약속: 11/8까지 동의하면 금값이 올라도 그대로" in quotes.public_text(quotes.public_quote(ctx, nid))


def test_approval_panel_warns_when_notice_review_unconfirmed(ctx, maker, operator):
    """S-20: 고지 문구를 사람이 확인하지 않은 초안을 승인하려 하면 승인 패널에서 미리 알려 준다."""
    from webutil import web
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED")
    quotes.draft(ctx, get(ctx, o["order_no"]), maker)
    page = web(ctx, "maker").get(f"/orders/{o['order_no']}/quote?confirm=approve_quote").text
    assert "아직 사람이 확인하지 않았어요" in page


def test_payment_request_has_customer_guide(ctx, maker, operator):
    """S-26·S-36: 결제 요청에 고객에게 보낼 안내(금액 그대로·관리코드만 메모·결제 방법)가 복사 칸으로 나온다."""
    from webutil import web
    from jewelry_os.services import payments, settings
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    r = payments.requests_of(ctx, o["id"])[-1]
    w = web(ctx, "operator")
    page = w.get(f"/orders/{o['order_no']}/payment").text
    assert "고객에게 보낼 결제 안내" in page and r["seller_code"] in page and "결제 방법(결제처)이 아직 설정되지 않았어요" in page
    settings.put(ctx, "payment_how", "스마트스토어 ‘맞춤 제작 결제’ 상품, 금액 직접 입력", operator)
    g = payments.customer_guide(ctx, get(ctx, o["order_no"]), r)
    assert "‘맞춤 제작 결제’" in g and "이 금액 그대로" in g and "계약 확인코드와는 다른 번호" in g


def test_remembering_shared_email_reevaluates_waiting_requests(ctx, maker, operator):
    """S-31: 공용 이메일을 한 고객 것으로 기억하면, 이미 들어와 확인을 기다리던 다른 고객의 요청에 경고가 붙는다.
    S-27: 화면의 '이 계정 기억'은 기본으로 꺼져 있다."""
    from webutil import web
    from jewelry_os.devtools import form_b_response
    a = order_at(ctx, maker, operator, "QUOTE_APPROVED", rid="ra", contact="010-1111-1111", name="김서연")
    b = order_at(ctx, maker, operator, "QUOTE_APPROVED", rid="rb", contact="010-2222-2222", name="이미정")
    ids = []
    for o in (a, b):
        rid = contracts.issue(ctx, get(ctx, o["order_no"]), operator, sleep=lambda s: None)
        contracts.record_delivery(ctx, rid, operator, "kakao", "견적서·안내문", items=["quote", "notice", "link"])
        form_b_response(ctx, rid, email="family@example.com")
        ids.append(rid)
    contracts.sync_once(ctx, sleep=lambda s: None)
    codes_b = {p["code"] for p in contracts.problems_of(contracts.get(ctx, ids[1]))}
    assert "IDENTITY_OTHER_CUSTOMER" not in codes_b
    contracts.review(ctx, ids[0], operator, "consult_channel", "카톡으로 본인 확인", remember_email=True)
    codes_b = {p["code"] for p in contracts.problems_of(contracts.get(ctx, ids[1]))}
    assert "IDENTITY_OTHER_CUSTOMER" in codes_b
    page = web(ctx, "operator").get(f"/orders/{b['order_no']}/quote").text
    assert 'name="remember_email" value="on">' in page and "공용 계정" in page


def test_factory_spec_may_add_detail_after_quote_wording():
    """S-39: 견적 문구 뒤에 상세를 덧붙인 공장 사양은 같은 사양, 문구를 바꾸면 다른 사양."""
    from jewelry_os.domain.transitions import spec_diff
    q = {"stones": "한쪽 별 가운데 큐빅 1.5mm 1개", "size": "별 지름 7mm"}
    assert spec_diff(q, {"stones": "한쪽 별 가운데 큐빅 1.5mm 1개 (오른쪽 귀)", "size": "별 지름 7mm"}) == []
    assert spec_diff(q, {"stones": "양쪽 별 큐빅 2개", "size": "별 지름 7mm"}) == ["스톤"]


def test_stage_notices_for_cad_design_and_shipping(ctx, maker, operator):
    """S-37·S-53·S-41: CAD 시작 알림, 최종 시안 확인 체크리스트, 배송지 요청·발송 안내 문구가 단계별로 나온다."""
    from webutil import web
    from jewelry_os.devtools import advance
    from jewelry_os.services import manufacturing
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    w = web(ctx, "maker")
    page = w.get(f"/orders/{o['order_no']}/making").text
    assert "상세 설계(CAD)를 시작합니다" in page and "‘상세 CAD 작업 착수 후’" in page
    manufacturing.upload_design(ctx, get(ctx, o["order_no"]), maker, {"cad": ("r.3dm", b"3dm x"), "preview": ("p.png", b"png x"),
                                                                     "stl": ("r.stl", b"solid x")})
    page = w.get(f"/orders/{o['order_no']}/making").text
    assert "최종 시안(디자인 v1)" in page and "· 호수·규격: 12호" in page and "철자" in page
    advance(ctx, o["order_no"], "QC_PASSED", maker, operator)
    assert "보내 드릴 곳을 알려 주세요" in w.get(f"/orders/{o['order_no']}/qc").text


def test_refund_close_out_resolution_and_deadline(ctx, maker, operator):
    """S-33: 전액 환급 뒤 주문을 한 번에 '취소'로 정리. S-34: 결정 전에도 참고 기한. S-51: 하자 요청을 수선·재제작으로 해결."""
    from webutil import web
    from jewelry_os.services import refunds
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    dep = ctx.db.scalar("SELECT expected_krw FROM payment_requests WHERE phase='DEPOSIT'")
    rid = refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "이사 때문에 취소", "카카오톡")
    assert refunds.view(ctx, refunds.get(ctx, rid))["provisional_due"]
    refunds.approve(ctx, rid, operator, dep, "R2 예약금 전액")
    refunds.complete(ctx, rid, operator, dep, "결제 취소", "취소번호 C-1")
    assert "주문 취소로 정리" in web(ctx, "operator").get(f"/refunds/{rid}").text
    assert refunds.close_order_after_refund(ctx, rid, operator)
    assert get(ctx, o["order_no"])["lifecycle_state"] == "CANCELLED"
    assert "환급 완료" in web(ctx, "operator").get(f"/orders/{o['order_no']}/payment").text

    s = order_at(ctx, maker, operator, "SHIPPED", rid="r9")
    rid2 = refunds.record(ctx, s, operator, "DEFECT", "각인이 H.F로 보여요", "카카오톡")
    refunds.legal_review(ctx, rid2, operator, "회수 후 확인", None)
    assert refunds.view(ctx, refunds.get(ctx, rid2))["legal_pending_judgment"]
    refunds.resolve_without_refund(ctx, rid2, operator, "REMAKE", "회수 확인 결과 각인 F로 새김 — 재제작")
    r2 = refunds.get(ctx, rid2)
    assert r2["status"] == "COMPLETED" and r2["refund_completed_amount"] == 0
    assert ctx.db.scalar("SELECT COUNT(*) FROM issues WHERE order_id=? AND type='REWORK' AND state='OPEN'", (s["id"],)) == 1
