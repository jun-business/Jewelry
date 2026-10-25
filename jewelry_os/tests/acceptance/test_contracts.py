"""D-24·D-25 Form B 계약 동의 + 단계별 취소·환불 — 로컬 Mock 시험(실제 Google 연결 불필요).

지시서 번호: [B#] = Form B 자동화 지시 10번 항목, [V#] = v1.1 추가 지시 9번 항목."""
import copy
import json
import sqlite3
import urllib.parse

import pytest
from helpers import check, get, new_order, order_at

from jewelry_os.bootstrap import build_ctx
from jewelry_os.devtools import accept_contract, form_b_response, pay_balance
from jewelry_os.domain import transitions as tr
from jewelry_os.services import contracts, customers, orders, quotes, refunds, system
from webutil import web

C = {"confirm": "on"}
NOOP = lambda s: None  # noqa: E731 — 재시도 대기 없이


def _sync(ctx):
    return contracts.sync_once(ctx, sleep=NOOP)


def _issue(ctx, o, operator, deliver=True):
    rid = contracts.issue(ctx, get(ctx, o["order_no"]), operator, sleep=NOOP)
    if deliver:  # S-47: 견적서를 보낸 기록이 있어야 계약이 유효
        contracts.record_delivery(ctx, rid, operator, "kakao", "카카오톡 상담방에 견적서·안내문 전송",
                                  items=["quote", "notice", "link"])
    return rid


def _review(ctx, rid, operator, method="consult_channel"):
    return contracts.review(ctx, rid, operator, method, "카카오톡 상담방에서 본인 제출 확인")


def _status(ctx, rid):
    return contracts.get(ctx, rid)["status"]


def _codes(ctx, rid):
    return {p["code"] for p in contracts.problems_of(contracts.get(ctx, rid))}


def _qa(ctx, maker, operator, rid="r1", **kw):
    return order_at(ctx, maker, operator, "QUOTE_APPROVED", rid=rid, **kw)


# ---------------- 설정·매핑 ----------------
def test_mapping_matches_real_form_b_v11():
    """실제 Form B(forms.get 원본)의 질문 ID·선택지와 매핑이 일치. entry 번호 = questionId(16진수)의 10진수."""
    body = contracts.bundled_form()
    assert body["info"]["documentTitle"] == "JOS_B2C_Contract_v1" and body["revisionId"] == "0000009a"
    assert contracts.verify_mapping(body) == []
    qs = [i for i in body["items"] if "questionItem" in i]
    sections = [i for i in body["items"] if "pageBreakItem" in i]
    assert len(qs) == 9 and len(sections) + 1 == 6 and all(i["questionItem"]["question"].get("required") for i in qs)
    m = contracts.mapping()
    assert m["version"] == "contract_v1.3" and len(m["consents"]) == 6
    q61 = next(c for c in m["consents"] if c["key"] == "q6_1_refund_policy")
    assert q61["question_id"] == "46ab9313" and q61["accept"] == "내용을 확인했습니다."
    bad = copy.deepcopy(body)
    for it in bad["items"]:
        if it.get("itemId") == "41100339":
            it["questionItem"]["question"]["choiceQuestion"]["options"][0]["value"] = "확인함"
    assert any("Q6-1" in p for p in contracts.verify_mapping(bad))  # 선택지가 바뀌면 링크를 만들지 않음


def test_settings_wait_when_form_b_missing(ctx, maker, operator):
    """폼 ID가 없으면 임의 값을 만들지 않고 '설정 대기'."""
    from jewelry_os.services import settings
    o = _qa(ctx, maker, operator)
    settings.put(ctx, "contract_form_id", "", operator)
    r = contracts.readiness(ctx)
    assert not r["ready"] and "폼 ID" in r["reason"]
    with pytest.raises(tr.TransitionBlocked) as e:
        _issue(ctx, o, operator)
    assert e.value.unmet[0].code == "G-CONTRACT-FORM"
    assert "Form B 설정 대기" in web(ctx, "operator").get(f"/orders/{o['order_no']}/quote").text


# ---------------- [B1][B2] 링크 발행 ----------------
def test_b1_same_form_two_customers(ctx, maker, operator):
    o1 = _qa(ctx, maker, operator, "r1", name="김하늘", contact="010-1111-2222")
    o2 = _qa(ctx, maker, operator, "r2", name="박민수", contact="010-3333-4444")
    r1, r2 = _issue(ctx, o1, operator), _issue(ctx, o2, operator)
    k1, k2 = contracts.get(ctx, r1), contracts.get(ctx, r2)
    assert k1["form_id"] == k2["form_id"] == "17H2mNNKU0s2RayLkau2Px-IP2TOHsviHRWbp1UXloZ8"  # 같은 폼 하나
    assert contracts.code_of(ctx, k1) != contracts.code_of(ctx, k2)
    u1 = urllib.parse.urlparse(contracts.prefill_url(ctx, k1))
    q1 = dict(urllib.parse.parse_qsl(u1.query))
    assert u1.netloc == "docs.google.com" and q1["usp"] == "pp_url"
    assert q1["entry.1083445884"] == o1["order_no"] and q1["entry.548127108"] == k1["quote_ref"]
    assert q1["entry.528017888"] == contracts.code_of(ctx, k1)
    form_b_response(ctx, r1, email="sky@example.com")
    form_b_response(ctx, r2, email="minsu@example.com")
    assert _sync(ctx)["new"] == 2
    assert {r["request_id"] for r in ctx.db.all("SELECT request_id FROM contract_responses")} == {r1, r2}
    for rid, o in ((r1, o1), (r2, o2)):
        assert _status(ctx, rid) == "REVIEW_REQUIRED" and _review(ctx, rid, operator) == "ACCEPTED"
        assert get(ctx, o["order_no"])["lifecycle_state"] == "CUSTOMER_ACCEPTED"


def test_b2_same_customer_two_orders_independent(ctx, maker, operator):
    o1 = _qa(ctx, maker, operator, "r1")
    o2 = _qa(ctx, maker, operator, "r2")
    with ctx.db.tx() as c:  # 같은 고객의 두 번째 주문
        c.execute("UPDATE orders SET customer_id=? WHERE id=?", (o1["customer_id"], o2["id"]))
    r1 = _issue(ctx, o1, operator)
    form_b_response(ctx, r1, email="same@example.com")
    _sync(ctx)
    _review(ctx, r1, operator)  # 이 이메일을 고객의 확인된 계약 이메일로 기억
    r2 = _issue(ctx, get(ctx, o2["order_no"]), operator)
    assert contracts.get(ctx, r2)["quote_id"] != contracts.get(ctx, r1)["quote_id"]
    form_b_response(ctx, r2, email="same@example.com")
    _sync(ctx)
    k2 = contracts.get(ctx, r2)
    assert k2["identity_method"] == "verified_email" and _codes(ctx, r2) == {"OPERATOR_REVIEW_PENDING"}  # 운영자 검증은 여전히
    assert _review(ctx, r2, operator, "verified_email") == "ACCEPTED"
    assert [k["order_no"] for k in contracts.of_customer(ctx, o1["customer_id"])] == [o2["order_no"], o1["order_no"]]
    page = web(ctx, "operator").get(f"/customers/{o1['customer_id']}").text
    assert "계약 동의 기록" in page and o1["order_no"] in page and o2["order_no"] in page


def test_issue_is_idempotent_and_requires_loss_reasons(ctx, maker, operator):
    o = _qa(ctx, maker, operator)
    w = web(ctx, "operator")
    for _ in range(3):  # 새로고침·반복 클릭
        assert w.post(f"/orders/{o['order_no']}/contract/issue", data={}).status_code == 303
    assert ctx.db.scalar("SELECT COUNT(*) FROM contract_requests") == 1
    k = ctx.db.one("SELECT * FROM contract_requests")
    assert k["quote_html_asset_id"] and k["contract_form_schema_version"] == "contract_v1.3"
    html = ctx.db.one("SELECT * FROM assets WHERE id=?", (k["quote_html_asset_id"],))
    from jewelry_os.services import files
    doc = files.read(ctx, html).decode("utf-8")
    assert k["quote_ref"] in doc and "12호 맞춤 사이즈로 만들어 다른 고객에게 판매하기 어렵고" in doc and "<style>" in doc
    # 고지 사유가 없는 견적은 링크를 만들지 않는다
    o2 = new_order(ctx, "r2")
    o2 = get(ctx, o2)
    from jewelry_os.devtools import advance
    advance(ctx, o2["order_no"], "FREE_CONCEPT", maker, operator)
    quotes.upload_concept(ctx, get(ctx, o2["order_no"]), maker, "c.png", b"\x89PNG x", "시안", 10)
    qid = quotes.draft(ctx, get(ctx, o2["order_no"]), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(items=[{"kind": "metal", "label": "금", "amount": "900000"}],
                                                  spec={"material": "골드"}, tax_included=True, shipping_included=True,
                                                  promised_date="2026-12-15", valid_until="2026-11-30"))
    quotes.approve(ctx, get(ctx, o2["order_no"]), maker, C)
    with pytest.raises(tr.TransitionBlocked) as e:
        _issue(ctx, get(ctx, o2["order_no"]), operator)
    assert e.value.unmet[0].code == "G-LOSS-CONFIRM"


# ---------------- [B3][V5] 중복 수집 ----------------
def test_b3_same_response_synced_10_times_is_one_record(ctx, maker, operator):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    form_b_response(ctx, rid)
    stats = [_sync(ctx) for _ in range(10)]
    assert stats[0]["new"] == 1 and all(s["new"] == 0 and s["revised"] == 0 for s in stats[1:])
    assert ctx.db.scalar("SELECT COUNT(*) FROM contract_responses") == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM contract_response_revisions") == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM contract_consents") == 6
    assert ctx.db.scalar("SELECT COUNT(*) FROM contract_events WHERE event='evaluated'") == 1
    _review(ctx, rid, operator)
    for _ in range(3):
        _sync(ctx)
    assert ctx.db.scalar("SELECT COUNT(*) FROM customer_acceptances WHERE kind='quote'") == 1


def test_paging_and_edited_response_revision(ctx, maker, operator):
    """모든 페이지를 읽고, 고객이 응답을 고치면 새 revision + 다시 검증."""
    ctx.forms.page_size = 1
    o1, o2 = _qa(ctx, maker, operator, "r1"), _qa(ctx, maker, operator, "r2")
    r1, r2 = _issue(ctx, o1, operator), _issue(ctx, o2, operator)
    form_b_response(ctx, r1, response_id="A1")
    form_b_response(ctx, r2, response_id="A2")
    assert _sync(ctx)["new"] == 2
    _review(ctx, r1, operator)
    assert _status(ctx, r1) == "ACCEPTED"
    fid = contracts.form_id(ctx)
    resp = ctx.forms.by_form[fid][0]
    resp["lastSubmittedTime"] = "2026-10-20T03:00:00Z"
    resp["answers"]["2968b885"]["textAnswers"]["answers"][0]["value"] = "계약 진행에 동의하지 않습니다."
    assert _sync(ctx)["revised"] == 1
    assert ctx.db.scalar("SELECT revision FROM contract_responses WHERE response_id='A1'") == 2
    assert _status(ctx, r1) == "REJECTED" and not tr.contract_ok(ctx.db.conn, o1["id"])


# ---------------- [B4][B5] 사전입력 값 변조 ----------------
@pytest.mark.parametrize("field,code", [("order_no", "ORDER_MISMATCH"), ("quote_ref", "QUOTE_MISMATCH")])
def test_b4_b5_tampered_prefill_blocks(ctx, maker, operator, field, code):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    form_b_response(ctx, rid, **{field: "JOS-2026-9999" if field == "order_no" else "Q00001-v9"})
    _sync(ctx)
    assert code in _codes(ctx, rid) and _status(ctx, rid) == "REVIEW_REQUIRED"
    assert _review(ctx, rid, operator) == "REVIEW_REQUIRED"  # 운영자도 풀 수 없음 → 재발행
    assert "G-CONTRACT-REQUIRED" in check(ctx, get(ctx, o["order_no"]), "record_acceptance", operator)
    assert ctx.db.scalar("SELECT COUNT(*) FROM contract_responses") == 1  # 실패 응답도 증거로 보관
    new = contracts.issue(ctx, get(ctx, o["order_no"]), operator, reissue=True, sleep=NOOP)
    contracts.record_delivery(ctx, new, operator, "kakao", "새 확인코드 안내문 전송", items=["quote", "notice", "link"])
    assert new != rid and _status(ctx, rid) == "SUPERSEDED" and _status(ctx, new) == "ISSUED"


def test_changed_code_is_kept_unmatched(ctx, maker, operator):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    form_b_response(ctx, rid, code="TG-AAAA-BBBB")
    _sync(ctx)
    r = ctx.db.one("SELECT * FROM contract_responses")
    assert r["match_status"] == "CODE_UNKNOWN" and r["request_id"] is None and r["candidate_order_id"] == o["id"]
    assert _status(ctx, rid) == "ISSUED"


# ---------------- [B6][B7][V1~V4] 동의 문항 ----------------
REFUSE = {"q4_quote": "동의하지 않습니다.", "q5_payment": "동의하지 않습니다.", "q6_withdrawal": "동의하지 않습니다.",
          "q6_1_refund_policy": "추가 설명이 필요합니다.", "q7_aftercare": "추가 설명이 필요합니다.",
          "q8_final": "계약 진행에 동의하지 않습니다."}


@pytest.mark.parametrize("key", list(REFUSE))
def test_v4_each_consent_must_be_agreed(ctx, maker, operator, key):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    form_b_response(ctx, rid, consents={key: REFUSE[key]})
    _sync(ctx)
    expect = "REVIEW_REQUIRED" if key in ("q6_1_refund_policy", "q7_aftercare") else "REJECTED"
    assert _status(ctx, rid) == expect
    _review(ctx, rid, operator) if expect == "REVIEW_REQUIRED" else None
    assert _status(ctx, rid) == expect and not tr.contract_ok(ctx.db.conn, o["id"])
    rec = ctx.db.one("SELECT * FROM contract_consents WHERE consent_key=?", (key,))
    assert rec["result"] in ("REFUSED", "NEEDS_EXPLANATION", "UNKNOWN") and rec["answer"] == REFUSE[key]


@pytest.mark.parametrize("key", list(REFUSE))
def test_b7_v3_missing_consent_blocks(ctx, maker, operator, key):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    qid = next(c["question_id"] for c in contracts.mapping()["consents"] if c["key"] == key)
    form_b_response(ctx, rid, drop=(qid,))
    _sync(ctx)
    _review(ctx, rid, operator)
    assert "CONSENT_MISSING" in _codes(ctx, rid) and _status(ctx, rid) == "REVIEW_REQUIRED"
    assert "G-CONTRACT-REQUIRED" in check(ctx, get(ctx, o["order_no"]), "record_acceptance", operator)


def test_b6_withdrawal_refusal_blocks_contract(ctx, maker, operator):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    form_b_response(ctx, rid, consents={"q6_withdrawal": "동의하지 않습니다."})
    _sync(ctx)
    assert _status(ctx, rid) == "REJECTED" and get(ctx, o["order_no"])["lifecycle_state"] == "QUOTE_APPROVED"


def test_v1_v2_q6_1_collected_and_explain_blocks_deposit(ctx, maker, operator):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    k = contracts.get(ctx, rid)
    assert "예약금 전액을 환불합니다" in k["refund_policy_text_snapshot"] and len(k["refund_policy_sha256"]) == 64
    form_b_response(ctx, rid)
    _sync(ctx)
    _review(ctx, rid, operator)
    k = contracts.get(ctx, rid)
    rec = ctx.db.one("SELECT * FROM contract_consents WHERE consent_key='q6_1_refund_policy'")
    assert rec["question_id"] == "46ab9313" and rec["result"] == "AGREED" and rec["notice_section_sha256"]
    assert k["refund_policy_acknowledged"] == 1 and k["refund_policy_acknowledged_at"] and k["status"] == "ACCEPTED"
    w6 = ctx.db.one("SELECT * FROM contract_consents WHERE consent_key='q6_withdrawal'")  # 청약철회 별도 동의 독립 기록
    assert w6["result"] == "AGREED" and w6["notice_section_sha256"] != rec["notice_section_sha256"]
    # 다른 주문: Q6-1에 폼에 없는 답(옛 '추가 설명' 등) → 검토 대기 + 예약금 청구 차단(v1.2는 확인 선택지 하나)
    o2 = _qa(ctx, maker, operator, "r2")
    r2 = _issue(ctx, o2, operator)
    form_b_response(ctx, r2, consents={"q6_1_refund_policy": "추가 설명이 필요합니다."})
    _sync(ctx)
    _review(ctx, r2, operator)
    k2 = contracts.get(ctx, r2)
    assert k2["status"] == "REVIEW_REQUIRED" and k2["refund_policy_review_required"] == 1
    assert "G-CONTRACT-REQUIRED" in check(ctx, get(ctx, o2["order_no"]), "record_acceptance", operator)
    with ctx.db.tx() as c:  # 주문을 강제로 다음 단계에 둬도 서버가 예약금 청구를 막는다
        c.execute("INSERT INTO transition_ctx(token) VALUES ('t')")
        c.execute("UPDATE orders SET lifecycle_state='CUSTOMER_ACCEPTED' WHERE id=?", (o2["id"],))
        c.execute("DELETE FROM transition_ctx")
    assert "G-CONTRACT-REQUIRED" in check(ctx, get(ctx, o2["order_no"]), "create_payment_req", operator)


# ---------------- [B8][V6] 견적 버전 변경 ----------------
def test_b8_new_quote_version_requires_reconsent(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "CUSTOMER_ACCEPTED")
    r1 = ctx.db.scalar("SELECT id FROM contract_requests")
    assert _status(ctx, r1) == "ACCEPTED"
    orders.open_issue_action(ctx, get(ctx, o["order_no"]), maker, "CHANGE_REQUEST", "각인 문구 변경", "CUSTOMER_CHANGE")
    qid = quotes.draft(ctx, get(ctx, o["order_no"]), maker)
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,))
    quotes.save(ctx, qid, maker, quotes.QuoteForm(
        items=[{"kind": "metal", "label": "18K 금", "amount": "1000000"}, {"kind": "design", "label": "설계비", "amount": "300000"}],
        spec={**{k: v for k, v in __import__("json").loads(q["spec_json"]).items()}, "engraving": "J&K",
              "custom_made": "12호 맞춤 사이즈 18K 골드 반지에 각인 J&K를 넣어 1점 제작"},
        tax_included=True, shipping_included=True, promised_date="2026-12-20", valid_until="2026-11-30"))
    quotes.approve_requote(ctx, get(ctx, o["order_no"]), maker, C)
    from jewelry_os.devtools import confirm_loss
    confirm_loss(ctx, qid, maker)
    assert _status(ctx, r1) == "SUPERSEDED" and not tr.contract_ok(ctx.db.conn, o["id"])  # v1 동의 재사용 안 함
    assert "G-CONTRACT-REQUIRED" in check(ctx, get(ctx, o["order_no"]), "create_payment_req", operator)
    r2 = _issue(ctx, o, operator)
    k2 = contracts.get(ctx, r2)
    assert k2["quote_version"] == 2 and k2["quote_ref"].endswith("-v2")
    form_b_response(ctx, r2)
    _sync(ctx)
    assert _review(ctx, r2, operator) == "ACCEPTED" and tr.contract_ok(ctx.db.conn, o["id"])
    assert ctx.db.scalar("SELECT state FROM issues WHERE type='CHANGE_REQUEST'") == "RESOLVED"  # 재동의 자동 기록
    # 옛 v1 링크로 다시 제출해도 대체된 요청 — 새 견적 계약에 영향 없음
    form_b_response(ctx, r1, response_id="late-v1")
    _sync(ctx)
    assert _status(ctx, r1) == "SUPERSEDED" and _status(ctx, r2) == "ACCEPTED"


# ---------------- [B9] 다른 사람이 제출 ----------------
def test_b9_other_customer_submits_link(ctx, maker, operator):
    oa = _qa(ctx, maker, operator, "ra", name="김하늘", contact="010-1111-2222")
    ob = _qa(ctx, maker, operator, "rb", name="박민수", contact="010-3333-4444")
    rb = _issue(ctx, ob, operator)
    form_b_response(ctx, rb, email="minsu@example.com")
    _sync(ctx)
    _review(ctx, rb, operator)  # minsu@ = B의 확인된 계약 이메일
    ra = _issue(ctx, oa, operator)
    form_b_response(ctx, ra, email="minsu@example.com")  # B가 A의 링크로 제출
    _sync(ctx)
    assert "IDENTITY_OTHER_CUSTOMER" in _codes(ctx, ra) and _status(ctx, ra) == "REVIEW_REQUIRED"
    assert get(ctx, oa["order_no"])["lifecycle_state"] == "QUOTE_APPROVED"
    oc = _qa(ctx, maker, operator, "rc", name="이서연", contact="010-5555-6666")
    rc = _issue(ctx, oc, operator)
    form_b_response(ctx, rc, email="stranger@example.com")
    _sync(ctx)
    assert "IDENTITY_UNVERIFIED" in _codes(ctx, rc) and _status(ctx, rc) == "REVIEW_REQUIRED"
    with pytest.raises(tr.TransitionBlocked):
        contracts.review(ctx, rc, operator, "consult_channel", "")  # 근거 없이 확인 불가
    with pytest.raises(tr.TransitionBlocked):
        contracts.review(ctx, rc, operator, "verified_email", "x")  # 시스템 일치가 없으면 고를 수 없음


# ---------------- [B10][V12] 재시작·장애 후 재동기화 ----------------
def test_b10_v12_restart_and_network_failure_resync(ctx, maker, operator):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    form_b_response(ctx, rid)  # PC가 꺼져 있는 동안 고객 제출
    ctx.forms.errors = ["NETWORK", "NETWORK", "NETWORK"]
    st = _sync(ctx)
    assert not st["ok"] and st["error"] == "NETWORK"
    cur = ctx.db.one("SELECT * FROM sync_cursors WHERE scope=?", (contracts.form_id(ctx),))
    assert cur["status"] == "ERROR" and cur["next_attempt_at"]
    ctx.forms.errors = ["RATE_LIMITED"]  # 한 번 막혀도 같은 실행 안에서 다시 시도
    ctx2 = build_ctx(ctx.cfg, forms=ctx.forms, files=ctx.files, analyzer=ctx.analyzer)  # 재시작
    sid = system.on_startup(ctx2)
    stats = system.first_sync_after_startup(ctx2, sid)
    assert stats["contracts"]["ok"] and stats["contracts"]["new"] == 1
    system.first_sync_after_startup(ctx2, system.on_startup(ctx2))
    assert ctx2.db.scalar("SELECT COUNT(*) FROM contract_responses") == 1
    assert ctx2.db.scalar("SELECT status FROM sync_cursors WHERE scope=?", (contracts.form_id(ctx2),)) == "OK"
    ctx2.db.close()


# ---------------- [B11][B12] 업무 진행 조건(Gate) ----------------
def test_b11_cad_start_needs_valid_contract_and_deposit(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    assert "G-CONTRACT-REQUIRED" not in check(ctx, o, "start_cad", maker)
    rid = ctx.db.scalar("SELECT id FROM contract_requests")
    form_b_response(ctx, rid, response_id="second", consents={"q8_final": "계약 진행에 동의하지 않습니다."})
    _sync(ctx)  # 같은 요청에 상충하는 응답 → 계약 무효(검토)
    assert _status(ctx, rid) == "REVIEW_REQUIRED" and "CONFLICTING_RESPONSES" in _codes(ctx, rid)
    assert "G-CONTRACT-REQUIRED" in check(ctx, get(ctx, o["order_no"]), "start_cad", maker)
    with pytest.raises(tr.TransitionBlocked):
        tr.perform(ctx, o["id"], "start_cad", maker, C)


def test_b12_factory_needs_design_ok_balance_and_maker(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    assert "G-DESIGN-UNAPPROVED" in check(ctx, o, "build_packet", maker)
    o = order_at(ctx, maker, operator, "CUSTOMER_DESIGN_APPROVED", rid="r2")
    for a in ("build_packet", "send_factory"):
        assert "G-BALANCE-BEFORE-FACTORY" in check(ctx, o, a, maker)
    assert "G-ROLE-MAKER" in check(ctx, o, "send_factory", operator)  # 아버님 발주 승인
    pay_balance(ctx, o["order_no"], operator)
    assert "G-BALANCE-BEFORE-FACTORY" not in check(ctx, get(ctx, o["order_no"]), "build_packet", maker)


def test_gate3_ship_needs_qc_pass(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "FINAL_PAYMENT_VERIFIED")
    assert "G-QC-INCOMPLETE" not in check(ctx, o, "ship", maker)
    with ctx.db.tx() as c:
        c.execute("INSERT INTO quality_checks(order_id, checklist_json, result, inspected_at) VALUES (?, '{}', 'FAIL', 'x')",
                  (o["id"],))
    assert "G-QC-FAIL" in check(ctx, o, "ship", maker)


# ---------------- [B13][V11] 원문 복원 ----------------
def test_b13_v11_notice_and_quote_restorable_after_form_change(ctx, maker, operator):
    o = _qa(ctx, maker, operator)
    w = web(ctx, "operator")
    w.post(f"/orders/{o['order_no']}/contract/issue", data={})
    rid = ctx.db.scalar("SELECT id FROM contract_requests")
    original = contracts.get(ctx, rid)["refund_policy_text_snapshot"]
    changed = copy.deepcopy(contracts.bundled_form())
    changed["revisionId"] = "00000099"
    for it in changed["items"]:
        if it.get("itemId") == "1ab70d71":
            it["description"] = it["description"].replace("예약금 전액을 환불합니다", "예약금의 50%를 환불합니다")
    ctx.forms.bodies[contracts.form_id(ctx)] = changed  # 나중에 Form B 문구 수정
    form_b_response(ctx, rid)
    _sync(ctx)
    _review(ctx, rid, operator)
    assert "NOTICE_CHANGED" in _codes(ctx, rid) and _status(ctx, rid) == "REVIEW_REQUIRED"  # 바뀐 정책에 자동 동의 간주 안 함
    ev = contracts.evidence(ctx, rid)
    text = "\n".join((x.get("desc") or x.get("text") or "") for x in ev["notice_text"])
    assert "예약금 전액을 환불합니다" in text and "50%" not in text  # 발행 당시 원문
    assert ev["req"]["refund_policy_text_snapshot"] == original
    assert ev["checks"] == {"quote_sha256_ok": True, "notice_sha256_ok": True, "quote_html_ok": True, "quote_pdf_ok": None}
    assert ev["quote"]["public_quote"]["spec"]["loss_reasons"]
    assert ctx.db.scalar("SELECT COUNT(*) FROM contract_notices") == 2  # 두 버전 모두 보관
    with pytest.raises(sqlite3.IntegrityError):
        with ctx.db.tx() as c:
            c.execute("UPDATE contract_requests SET refund_policy_text_snapshot='x' WHERE id=?", (rid,))
    with pytest.raises(sqlite3.IntegrityError):
        with ctx.db.tx() as c:
            c.execute("UPDATE contract_events SET event='x'")
    with pytest.raises(sqlite3.IntegrityError):
        with ctx.db.tx() as c:
            c.execute("DELETE FROM contract_responses")


def test_evidence_page_export_and_pii_controls(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "CUSTOMER_ACCEPTED")
    rid = ctx.db.scalar("SELECT id FROM contract_requests")
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    page = wm.get(f"/contracts/{rid}").text
    assert "고객이 본 Form B 고지 문구" in page and "cu***@example.com" in page and "customer1@example.com" not in page
    assert wm.get(f"/contracts/{rid}/export").status_code == 200  # D-43: 아버님 계정도 내보낸다(기록 남음)
    assert "customer1@example.com" in wo.get(f"/contracts/{rid}?reveal=1").text
    r = wo.get(f"/contracts/{rid}/export")
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"contract.json", "quote_snapshot.json", "form_b_notice.json", "events.json"} <= set(z.namelist())
    assert ctx.db.scalar("SELECT COUNT(*) FROM audit_events WHERE action IN ('contract_exported','contract_pii_viewed')") == 3  # 아버님 내보내기 포함


def test_code_and_link_not_in_logs_or_audit(ctx, maker, operator, caplog):
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    k = contracts.get(ctx, rid)
    code = contracts.code_of(ctx, k)
    form_b_response(ctx, rid)
    _sync(ctx)
    _review(ctx, rid, operator)
    con = ctx.db.conn
    for t in ("audit_events", "contract_events", "contract_requests", "contract_responses", "contract_consents"):
        for row in con.execute(f"SELECT * FROM {t}"):
            s = " ".join(str(v) for v in tuple(row))
            assert code not in s and code.replace("-", "") not in s, t
    assert code not in caplog.text


def test_ai_modules_do_not_touch_contracts_or_refunds():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "jewelry_os"
    for f in ("services/analysis.py", "adapters/ai.py"):
        src = (root / f).read_text(encoding="utf-8")
        assert "contracts" not in src and "refunds" not in src


# ---------------- [V7~V10] 단계별 취소·환불 ----------------
def test_v7_cancel_after_deposit_before_cad_full_refund(ctx, maker, operator, fclock):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    fclock.advance(hours=1)
    rid = refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "제작 취소 원해요", "카카오톡")
    r = refunds.get(ctx, rid)
    dep = ctx.db.scalar("SELECT expected_krw FROM payment_requests WHERE phase='DEPOSIT'")
    assert r["production_stage_at_request"] == "R2" and r["refund_eligible_amount"] == dep == r["paid_amount"]
    assert r["legal_review_status"] == "NOT_REQUIRED" and r["cad_started_at"] is None and r["deposit_paid_at"]
    # 취소 요청 뒤 CAD 착수 기록(소급) 방지 — 환불 검토 중에는 착수 불가
    assert "G-ISSUE-OPEN" in check(ctx, get(ctx, o["order_no"]), "start_cad", maker)
    with pytest.raises(sqlite3.IntegrityError):
        with ctx.db.tx() as c:
            c.execute("UPDATE refund_requests SET cad_started_at='2026-10-19T00:00:00+00:00' WHERE id=?", (rid,))
    refunds.approve(ctx, rid, operator, dep, "R2 자체 정책: 예약금 전액")
    r = refunds.get(ctx, rid)
    assert r["refund_due_at"] == "2026-10-23"  # 화(10/20) 접수 + 3영업일
    refunds.complete(ctx, rid, operator, dep, "네이버 결제 취소", "취소번호 C-1")
    assert refunds.get(ctx, rid)["status"] == "COMPLETED"
    assert ctx.db.scalar("SELECT state FROM issues WHERE type='REFUND_REVIEW'") == "RESOLVED"
    assert ctx.db.scalar("SELECT COUNT(*) FROM refund_events WHERE refund_id=?", (rid,)) == 3


def test_r1_before_deposit_nothing_to_return(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "CUSTOMER_ACCEPTED")
    r = refunds.get(ctx, refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "취소", "카카오톡"))
    assert r["production_stage_at_request"] == "R1" and r["refund_eligible_amount"] == 0 and r["paid_amount"] == 0


def test_v8_after_cad_start_no_automatic_deduction(ctx, maker, operator, fclock):
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    fclock.advance(hours=1)
    rid = refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "마음이 바뀌었어요", "카카오톡")
    r = refunds.get(ctx, rid)
    assert r["production_stage_at_request"] == "R3" and r["refund_eligible_amount"] is None  # 자동 공제·자동 금액 없음
    assert r["legal_review_status"] == "REQUIRED" and r["cad_started_at"]
    with pytest.raises(tr.TransitionBlocked) as e:
        refunds.approve(ctx, rid, operator, 0, "x")
    assert e.value.unmet[0].code == "G-REFUND-LEGAL"
    page = web(ctx, "operator").get(f"/refunds/{rid}").text
    assert "작업 진행 상황" in page and "법적 검토" in page and "자동 계산 안 함" in page


def test_cad_start_before_request_is_server_time(ctx, maker, operator, fclock):
    """요청 시각을 CAD 착수보다 앞으로 적으면 R2 — 착수 시각은 감사로그(서버 시각)라 소급되지 않는다."""
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")  # 11:00 KST 결제 확인
    fclock.advance(hours=1)
    tr.perform(ctx, o["id"], "start_cad", maker, C)  # 12:00 KST 착수(서버 시각)
    fclock.advance(hours=2)
    cad = ctx.db.scalar("SELECT created_at FROM audit_events WHERE action='start_cad'")
    rid = refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "카톡으로 착수 전 취소 요청", "카카오톡", "2026-10-20T11:30")
    r = refunds.get(ctx, rid)
    assert r["cad_started_at"] == cad and r["production_stage_at_request"] == "R2"
    with pytest.raises(tr.TransitionBlocked):
        refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "x", "카카오톡", "2030-01-01T00:00")  # 미래 시각 거부


def test_v9_after_factory_change_of_mind_needs_legal_review(ctx, maker, operator, fclock):
    o = order_at(ctx, maker, operator, "FACTORY_SENT")
    fclock.advance(hours=1)
    r = refunds.get(ctx, refunds.record(ctx, o, operator, "CHANGE_OF_MIND", "취소하고 싶어요", "카카오톡"))
    assert r["production_stage_at_request"] == "R4" and r["legal_review_status"] == "REQUIRED"
    assert r["refund_eligible_amount"] is None and r["factory_ordered_at"]


def test_v10_defect_mismatch_separate_from_withdrawal_consent(ctx, maker, operator, fclock):
    o = order_at(ctx, maker, operator, "SHIPPED")
    fclock.advance(days=2)
    q6 = ctx.db.one("SELECT * FROM contract_consents WHERE consent_key='q6_withdrawal'")
    assert q6["result"] == "AGREED"  # 청약철회 제한 별도 동의가 있어도
    for kind in ("DEFECT", "MISMATCH"):
        rid = refunds.record(ctx, get(ctx, o["order_no"]), operator, kind, "각인이 계약과 달라요", "카카오톡")
        r = refunds.get(ctx, rid)
        assert r["production_stage_at_request"] == "R6" and r["legal_review_status"] == "REQUIRED"
        assert r["shipping_fee_bearer"] == "SELLER" and r["refund_eligible_amount"] is None
        refunds.withdraw(ctx, rid, operator, "시험 정리")
    r5 = refunds.get(ctx, refunds.record(ctx, get(ctx, o["order_no"]), operator, "CHANGE_OF_MIND", "변심", "카카오톡"))
    assert r5["production_stage_at_request"] == "R5" and r5["legal_review_status"] == "REQUIRED"
    refunds.legal_review(ctx, r5["id"], operator, "주문제작 예외 요건 검토 — 법정 청약철회 해당", True)
    with pytest.raises(tr.TransitionBlocked):
        refunds.approve(ctx, r5["id"], operator, 100, "반품 접수")  # 공급된 상품: 반환받은 날(기산일) 필요
    refunds.approve(ctx, r5["id"], operator, 100, "반품 접수", "CUSTOMER", "4000", "2026-10-23")
    r5 = refunds.get(ctx, r5["id"])
    assert r5["refund_due_at"] == "2026-10-28" and r5["shipping_fee_bearer"] == "CUSTOMER"  # 금(10/23) + 3영업일


def test_refund_ui_flow(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    w = web(ctx, "operator")
    assert "취소·환불 요청" in w.get(f"/orders/{o['order_no']}/payment").text
    r = w.post(f"/orders/{o['order_no']}/refunds", data={"reason_kind": "CHANGE_OF_MIND", "reason": "취소", "via": "카카오톡"})
    assert r.status_code == 303 and r.headers["location"].startswith("/refunds/")
    rid = ctx.db.scalar("SELECT id FROM refund_requests")
    assert w.post(f"/refunds/{rid}/approve", data={"amount": "400,000", "reason": "R2 정책"}).status_code == 303
    assert refunds.get(ctx, rid)["refund_approved_amount"] == 400000


def test_unpublished_form_b_blocks_link_in_live_mode(ctx, maker, operator):
    """실제 Form B가 '게시' 전이면 고객이 링크를 열 수 없다(2026-10-24 실연동 확인) — 링크를 만들지 않고 이유를 보여 준다."""
    o = _qa(ctx, maker, operator)
    body = copy.deepcopy(contracts.bundled_form())
    body["publishSettings"] = {"publishState": {}}  # 게시 전 상태(코드에 포함된 원본은 이제 게시된 상태)

    class LiveLike:  # Mock이 아닌 실제 어댑터처럼 게시 여부를 따진다
        def get_form(self, fid):
            return body

        def list_responses(self, fid, page_token=None, since_rfc3339=None):
            from jewelry_os.adapters.google import Page
            return Page([])
    ctx.forms = LiveLike()
    assert not contracts.is_published(body)
    with pytest.raises(tr.TransitionBlocked) as e:
        _issue(ctx, o, operator)
    assert "게시" in e.value.unmet[0].message and ctx.db.scalar("SELECT COUNT(*) FROM contract_requests") == 0
    page = web(ctx, "operator").get("/settings/google").text
    assert "계약 동의 폼(Form B)" in page and "게시되지 않았어요" in page
    body["publishSettings"] = {"publishState": {"isPublished": True, "isAcceptingResponses": True}}
    assert contracts.is_published(body) and _issue(ctx, o, operator)


def test_publishing_form_is_not_a_notice_change(ctx, maker, operator):
    """2026-10-24 실연동에서 발견: 링크 발행 뒤 폼을 '게시'만 했는데 고지 변경으로 막혔다 — 고객이 읽는 내용만 비교한다."""
    o = _qa(ctx, maker, operator)
    rid = _issue(ctx, o, operator)
    published = copy.deepcopy(contracts.bundled_form())
    published["publishSettings"] = {"publishState": {"isPublished": True, "isAcceptingResponses": True}}
    ctx.forms.bodies[contracts.form_id(ctx)] = published
    form_b_response(ctx, rid)
    _sync(ctx)
    assert "NOTICE_CHANGED" not in _codes(ctx, rid)
    assert _review(ctx, rid, operator) == "ACCEPTED"
    assert contracts.evidence(ctx, rid)["checks"]["notice_sha256_ok"]


# ---------------- 고객 묶음(‘같은 고객으로 연결’) ----------------
def test_linked_customers_share_contract_history_and_identity(ctx, maker, operator):
    """Form A 응답마다 고객이 새로 생긴다 — 운영자가 연결하면 계약 기록·확인된 이메일·검증을 묶음으로 본다.
    계약 증거의 고객 ID는 그대로(불변), 연결 사실은 계약 이력에 남는다."""
    o1 = order_at(ctx, maker, operator, "CUSTOMER_ACCEPTED", rid="r1", name="김하늘", contact="010-1111-2222")
    r1 = ctx.db.scalar("SELECT id FROM contract_requests")  # 고객1 · 확인된 이메일 customer{id}@example.com
    o2 = _qa(ctx, maker, operator, "r2", name="김하늘", contact="010-1111-9999")  # 같은 사람이 다른 번호로 — 수동 연결
    r2 = _issue(ctx, o2, operator)
    c1, c2 = o1["customer_id"], o2["customer_id"]
    assert c1 != c2 and [k["id"] for k in contracts.of_customer(ctx, c2)] == [r2]
    customers.link(ctx, c2, c1, operator, "같은 전화번호·카톡 확인")  # 고객1 → 고객2로 연결
    assert contracts.get(ctx, r1)["customer_id"] == c1  # 증거는 그대로
    assert {k["id"] for k in contracts.of_customer(ctx, c2)} == {r1, r2} == {k["id"] for k in contracts.of_customer(ctx, c1)}
    ev = ctx.db.one("SELECT * FROM contract_events WHERE request_id=? AND event='customer_linked'", (r1,))
    assert ev and '"to":' in ev["detail_json"]
    page = web(ctx, "operator").get(f"/customers/{c2}").text
    assert o1["order_no"] in page and o2["order_no"] in page
    # 연결 전 고객1의 확인된 이메일로 고객2의 새 주문에 제출 → 같은 고객으로 대조
    form_b_response(ctx, r2, email=f"customer{c1}@example.com")
    _sync(ctx)
    k2 = contracts.get(ctx, r2)
    assert k2["identity_method"] == "verified_email" and _codes(ctx, r2) == {"OPERATOR_REVIEW_PENDING"}
    assert _review(ctx, r2, operator, "verified_email") == "ACCEPTED"
    # 이미 유효한 고객1 계약에 새 응답이 와도 '고객 없음'으로 빠지지 않는다(주문 고객이 묶음 안)
    form_b_response(ctx, r1, response_id="again", email=f"customer{c1}@example.com")
    _sync(ctx)
    assert "CUSTOMER_MISSING" not in _codes(ctx, r1) and "IDENTITY_OTHER_CUSTOMER" not in _codes(ctx, r1)
    # 묶음 밖 고객의 이메일은 여전히 다른 고객으로 표시
    o3 = _qa(ctx, maker, operator, "r3", name="박민수", contact="010-3333-4444")
    r3 = _issue(ctx, o3, operator)
    form_b_response(ctx, r3, email=f"customer{c1}@example.com")
    _sync(ctx)
    assert "IDENTITY_OTHER_CUSTOMER" in _codes(ctx, r3)


def test_kakao_channel_on_quote_document_and_text(ctx, maker, operator):
    """D-27 카카오톡 채널 주소 — 코드 기본값, 견적서·카톡용 텍스트·계약 증거 견적서에 문의처로 표시."""
    from jewelry_os.services import settings
    assert settings.get(ctx, "kakao_channel_url") == "https://pf.kakao.com/_dxhAWX"
    for raw in ("http://pf.kakao.com/_dxhAWX", "pf.kakao.com/_dxhAWX/chat", " https://pf.kakao.com/_dxhAWX/ "):
        assert settings.normalize_kakao_channel(raw) == "https://pf.kakao.com/_dxhAWX"
    for bad in ("https://example.com/_dxhAWX", "https://pf.kakao.com/dxhAWX", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            settings.normalize_kakao_channel(bad)
    o = _qa(ctx, maker, operator)
    qid = ctx.db.scalar("SELECT id FROM quotes WHERE order_id=? AND status='APPROVED'", (o["id"],))
    w = web(ctx, "operator")
    doc = w.get(f"/docs/quote/{qid}").text
    assert 'href="https://pf.kakao.com/_dxhAWX"' in doc
    assert "문의: 카카오톡 채널 틴틴골드 https://pf.kakao.com/_dxhAWX/chat" in w.get(f"/docs/quote/{qid}.txt").text
    w.post(f"/orders/{o['order_no']}/contract/issue", data={})
    from jewelry_os.services import files
    k = ctx.db.one("SELECT * FROM contract_requests")
    assert "pf.kakao.com/_dxhAWX" in files.read(ctx, ctx.db.one("SELECT * FROM assets WHERE id=?",
                                                                    (k["quote_html_asset_id"],))).decode("utf-8")
    assert w.post("/settings/policy", data={"kakao_channel_url": "https://evil.example/_x"}).status_code == 422
    assert w.post("/settings/policy", data={"kakao_channel_url": ""}).status_code == 303  # 비우면 표시 안 함
    assert "카카오톡 채널 틴틴골드" not in w.get(f"/docs/quote/{qid}.txt").text
    assert 'value="' in w.get("/settings/policy").text and web(ctx, "maker").post(
        "/settings/policy", data={"kakao_channel_url": ""}).status_code == 303  # D-43


def test_quote_documents_state_balance_before_factory(ctx, maker, operator):
    """2026-10-25 시뮬레이션에서 발견: 견적서가 잔금을 '완성·검수 후'로 안내 — Form B Q5·D-24와 맞춘다."""
    o = _qa(ctx, maker, operator)
    qid = ctx.db.scalar("SELECT id FROM quotes WHERE order_id=? AND status='APPROVED'", (o["id"],))
    w = web(ctx, "operator")
    doc, txt = w.get(f"/docs/quote/{qid}").text, w.get(f"/docs/quote/{qid}.txt").text
    for t in (doc, txt):
        assert "최종 CAD 승인 후" in t and "제작 발주 전" in t and "완성·검수 후" not in t
    snap = __import__("json").loads(contracts.quote_snapshot(ctx, ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,))))
    assert "최종 CAD 승인 후" in snap["text"]


def test_send_bundle_quote_notice_link(ctx, maker, operator):
    """D-33 고객에게 ① 확정 견적서 ② 사전 고지(주문번호·견적번호·확인코드) ③ 링크를 보내고, 보낸 항목을 기록."""
    o = _qa(ctx, maker, operator)
    w = web(ctx, "operator")
    w.post(f"/orders/{o['order_no']}/contract/issue", data={})
    k = ctx.db.one("SELECT * FROM contract_requests")
    code = contracts.code_of(ctx, k)
    msg = contracts.notice_message(ctx, k)
    for part in (o["order_no"], k["quote_ref"], code, "entry.528017888=", "Google 계정 로그인", "1,200,000원",
                 "pf.kakao.com/_dxhAWX/chat"):
        assert part in msg
    page = w.get(f"/orders/{o['order_no']}/quote").text
    assert "고객에게 보낼 것(순서대로)" in page and "견적서 파일 받기" in page and "안내문 복사(고지+링크)" in page
    f = w.get(f"/contracts/{k['id']}/quote-file")
    from jewelry_os.services import files
    stored = files.read(ctx, ctx.db.one("SELECT * FROM assets WHERE id=?", (k["quote_html_asset_id"],)))
    assert f.status_code == 200 and f.content == stored and "attachment" in f.headers["content-disposition"]  # 보관 원본과 같은 파일
    r = w.post(f"/contracts/{k['id']}/delivered", data={"channel": "kakao", "delivery_note": "상담방", "items": ["link"]})
    assert r.status_code == 303 and contracts.delivered_items(ctx, contracts.get(ctx, k["id"])) == ["link"]
    assert "보낸 것: 계약 동의" in contracts.get(ctx, k["id"])["delivered_note"]
    assert "고객에게 보냄 기록" in w.get(f"/orders/{o['order_no']}/quote").text  # 빠진 것이 있으면 다시 기록 가능
    with pytest.raises(tr.TransitionBlocked):
        contracts.record_delivery(ctx, k["id"], operator, "kakao", "메모", items=[])
    assert contracts.record_delivery(ctx, k["id"], operator, "kakao", "견적서 PDF·안내문 전송",
                                     items=["quote", "notice", "link"]) == []
    assert "고객에게 보냄 기록</button>" not in w.get(f"/orders/{o['order_no']}/quote").text
    ev = ctx.db.one("SELECT detail_json FROM contract_events WHERE event='delivered' ORDER BY id DESC LIMIT 1")
    assert '"items":["quote","notice","link"]' in ev["detail_json"] and code not in ev["detail_json"]


def test_quote_has_design_revision_delivery_refund_terms(ctx, maker, operator):
    """D-34 Form B Q4가 말하는 견적서 항목(디자인·일정·수정 범위·취소·환불 조건)이 실제 견적서·증거 원본에 들어간다."""
    o = _qa(ctx, maker, operator)
    from jewelry_os.services import files, settings
    qid = ctx.db.scalar("SELECT id FROM quotes WHERE order_id=? AND status='APPROVED'", (o["id"],))
    pq = quotes.public_quote(ctx, qid)
    assert pq.design_asset_id and pq.design_label == "시안 v1"
    assert pq.revision_scope == settings.get(ctx, "quote_revision_scope_default")
    assert "예약금 전액을 환불합니다" in pq.refund_terms and pq.delivery_plan
    w = web(ctx, "operator")
    doc = w.get(f"/docs/quote/{qid}").text
    for h in ("디자인", "제작·배송 일정", "수정 가능 범위", "취소·환불 조건"):
        assert f"<h2>{h}</h2>" in doc
    txt = w.get(f"/docs/quote/{qid}.txt").text
    assert "수정 가능 범위:" in txt and "취소·환불 조건:" in txt and "배송:" in txt
    w.post(f"/orders/{o['order_no']}/contract/issue", data={})
    k = ctx.db.one("SELECT * FROM contract_requests")
    html = files.read(ctx, ctx.db.one("SELECT * FROM assets WHERE id=?", (k["quote_html_asset_id"],))).decode("utf-8")
    assert 'src="data:image/png;base64,' in html and "/assets/" not in html  # 시안 이미지도 원본 안에
    snap = json.loads(contracts.snapshot_of(ctx, k))["public_quote"]
    assert snap["refund_terms"] and snap["revision_scope"] and snap["design_label"] == "시안 v1"
    assert w.post("/settings/policy", data={"quote_delivery_default": "택배 발송, 2일 내 도착"}).status_code == 303
    assert settings.get(ctx, "quote_delivery_default") == "택배 발송, 2일 내 도착"



def test_loss_review_ai_then_human_decides(ctx, maker, operator):
    """D-36 규칙 검사 + AI 검토 + 사람의 최종 판단 — 확인 전에는 계약 링크를 만들지 않는다."""
    from jewelry_os.services import loss_review
    o = order_at(ctx, maker, operator, "FREE_CONCEPT")
    quotes.upload_concept(ctx, get(ctx, o["order_no"]), maker, "c.png", bytes([0x89]) + b"PNG lr", "시안", 10)
    qid = quotes.draft(ctx, get(ctx, o["order_no"]), maker)
    spec = {"material": "골드", "purity": "18K", "size": "12호", "engraving": "J&K",
            "custom_made": "12호 맞춤 사이즈와 각인 J&K로 이 고객님만을 위해 1점 제작",
            "loss_reasons": "12호 맞춤 사이즈와 각인 J&K 때문에 다른 고객에게 판매하기 어렵고, 상세 CAD 설계와 발주 비용을 회수하기 어려워요."}
    form = dict(items=[{"kind": "metal", "label": "금", "amount": "900000"}], tax_included=True, shipping_included=True,
                promised_date="2026-12-15", valid_until="2026-11-30")
    quotes.save(ctx, qid, maker, quotes.QuoteForm(spec=spec, **form))
    with pytest.raises(tr.TransitionBlocked):
        loss_review.decide(ctx, qid, maker, "")  # 검토 전 판단 불가
    assert loss_review.run(ctx, qid, maker) == "OK"
    sent = ctx.analyzer.calls[-1]
    assert "김하늘" not in sent and "J&K" in sent  # 고객 이름은 보내지 않음
    v = loss_review.view(ctx, ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,)))
    assert v["res"]["verdict"] == "sufficient" and v["current"] and not v["confirmed"]
    assert v["res"]["risk_level"] == "low" and v["res"]["evidence_to_keep"] and v["res"]["limitations"]
    assert {c["key"] for c in v["res"]["criteria"]} >= {"irreparable_harm", "separate_notice", "rights_preserved"}
    assert "시행령 제21조" in sent and "소비자분쟁" in sent  # 법률 검토자 관점 프롬프트
    assert loss_review.decide(ctx, qid, maker) == "ACCEPTED"
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,))
    assert loss_review.confirmed(ctx, q)
    # 문구가 바뀌면 다시 검토·판단
    quotes.save(ctx, qid, maker, quotes.QuoteForm(spec={**spec, "loss_reasons": spec["loss_reasons"] + " 추가 설명."}, **form))
    assert not loss_review.confirmed(ctx, ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,)))
    # AI가 부족하다고 하면 근거 없이 진행 불가, 근거를 적으면 사람 판단으로 진행
    ctx.analyzer.review_loss = lambda p: {"verdict": "insufficient", "criteria": [{"key": "specific_loss", "met": False,
                                                                                 "comment": "비용 근거 부족"}],
                                          "suggestions": ["발주 비용 구체화"], "improved_custom_made": None,
                                          "improved_loss_reasons": "제안 문구"}
    loss_review.run(ctx, qid, maker)
    with pytest.raises(tr.TransitionBlocked) as e:
        loss_review.decide(ctx, qid, maker, "")
    assert e.value.unmet[0].code == "G-LOSS-OVERRIDE-NOTE"
    assert loss_review.decide(ctx, qid, maker, "공장 견적서로 발주 비용 확인") == "OVERRIDDEN"
    page = web(ctx, "maker").get(f"/orders/{o['order_no']}/quote").text
    assert "추천 문구" in page and "제안 문구" in page and "분쟁 위험" in page
    assert "청약철회 고지 검토" in page and "사람 확인 완료" in page and "공장 견적서로 발주 비용 확인" in page
    quotes.approve(ctx, get(ctx, o["order_no"]), maker, C)
    assert _issue(ctx, o, operator)  # 확인했으니 링크 발행
    # AI 오류여도 사람이 근거를 적고 판단 가능, 확인 없으면 링크 차단
    o2 = order_at(ctx, maker, operator, "FREE_CONCEPT", rid="r2")
    quotes.upload_concept(ctx, get(ctx, o2["order_no"]), maker, "c.png", bytes([0x89]) + b"PNG lr2", "시안", 10)
    q2 = quotes.draft(ctx, get(ctx, o2["order_no"]), maker)
    quotes.save(ctx, q2, maker, quotes.QuoteForm(spec=spec, **form))
    quotes.approve(ctx, get(ctx, o2["order_no"]), maker, C)
    with pytest.raises(tr.TransitionBlocked) as e:
        _issue(ctx, o2, operator)
    assert e.value.unmet[0].code == "G-LOSS-CONFIRM"
    def boom(p):
        raise __import__("jewelry_os.adapters.ai", fromlist=["x"]).AnalyzerError("TIMEOUT", "x")
    ctx.analyzer.review_loss = boom
    assert loss_review.run(ctx, q2, operator) == "TIMEOUT"
    assert loss_review.decide(ctx, q2, operator, "AI 없이 사양 대조 확인") == "OVERRIDDEN"


def test_first_contact_sms_copy_box(ctx, maker, operator):
    """채널에 말을 걸지 않은 고객에게 보낼 첫 연락 문자 — 견적 승인 뒤 시안·견적 탭에 복사용으로."""
    o = _qa(ctx, maker, operator)
    page = web(ctx, "operator").get(f"/orders/{o['order_no']}/quote").text
    assert "첫 연락 문자" in page and 'data-copy="sms-text"' in page
    assert f"[틴틴골드] {get(ctx, o['order_no'])['customer_name']} 고객님" in page
    assert "https://pf.kakao.com/_dxhAWX/chat" in page and "시안 받을게요" in page
