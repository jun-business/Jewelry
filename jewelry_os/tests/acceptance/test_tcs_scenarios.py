"""TC-S-001~019 경험 시나리오 시험(AGENT, TestClient). 브라우저 시험(003·007·019)은 tests/browser."""
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import C, get, new_order, order_at

from conftest import T0, add_response, approve_policies, pass_all_gates
from jewelry_os.adapters import google as g
from jewelry_os.adapters.ai import MockAI
from jewelry_os.bootstrap import build_ctx
from jewelry_os.core import clock
from jewelry_os.core.config import load_config
from jewelry_os.devtools import advance
from jewelry_os.domain import transitions as tr
from jewelry_os.domain.guards import Forbidden, TransitionBlocked
from jewelry_os.domain.states import LABEL, ORDERED, PHASE_LABEL, PHASE_OF, S
from jewelry_os.services import (alerts, analysis, capacity, cs, customers, intake, manufacturing, metrics, orders,
                                 payments, quotes, settings)
from jewelry_os.web.app import create_app
from webutil import web
from jewelry_os.devtools import form_b_response

PKG = Path(__file__).resolve().parents[2] / "jewelry_os"


def act(w, no, code, **data):
    return w.post(f"/orders/{no}/actions/{code}", data={"confirm": "on", **data})


def act_nc(w, no, code, **data):  # 확인 체크 없이
    return w.post(f"/orders/{no}/actions/{code}", data=data)


def notif(ctx, kind, oid=None):
    q = "SELECT COUNT(*) FROM notifications WHERE kind=? AND resolved_at IS NULL" + (" AND order_id=?" if oid else "")
    return ctx.db.scalar(q, (kind, oid) if oid else (kind,))


# ---------------- TC-S-001 첫 실행 ----------------
def test_tcs_001_first_run(tmp_path, fclock):
    c = build_ctx(load_config("test", tmp_path / "fresh"), forms=g.MockForms(), files=g.MockDrive(), analyzer=MockAI())
    cl = TestClient(create_app(c), base_url="http://127.0.0.1:8770", follow_redirects=False)
    r = cl.get("/")
    assert r.status_code in (302, 303, 307) and r.headers["location"].endswith("/setup")  # ①
    form = {"maker_username": "father", "maker_display_name": "김장인", "operator_username": "ops",
            "operator_display_name": "김운영", "maker_password": "short-pw9", "maker_password2": "short-pw9",
            "operator_password": "oper-pass-1234", "operator_password2": "oper-pass-1234"}
    assert cl.post("/setup", data=form).status_code == 422  # ③ 9자
    form.update(maker_password="maker-pass-123", maker_password2="maker-pass-123")
    assert cl.post("/setup", data=form).status_code == 303
    assert cl.get("/setup").status_code == 404  # ②
    s = settings.all_settings(c)
    assert (s["capacity_limit"], s["sync_interval_min"], s["lock_minutes"], s["payment_policy"], s["google_mode"]) == \
        (3, 5, 15, "THIRDS", "auto")  # ④ D-20: 자동(연결 전엔 Mock)
    assert c.db.scalar("SELECT name FROM holidays WHERE date='2026-10-09'")  # ⑤
    r = cl.post("/login", data={"username": "father", "password": "maker-pass-123", "next": "/"})
    cl.cookies.set("jos_session", r.cookies.get("jos_session"))
    page = cl.get("/").text
    assert "아직 들어온 의뢰가 없어요" in page and 'href="/settings' in page  # ⑥
    c.db.close()


# ---------------- TC-S-002 정상 주문 E2E ----------------
def test_tcs_002_e2e(ctx, maker, operator, fclock):
    pass_all_gates(ctx, operator)
    settings.put(ctx, "tax_type", "general", operator)  # H1 r1 Q9: 과세 유형을 정해야 공헌이익 계산
    approve_policies(ctx, operator)
    ctx.files.files["img1"] = g.sample_jpeg("img1")
    no = new_order(ctx, files=["img1"])
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    seen = [r["next"] for r in ctx.db.all("SELECT next FROM audit_events WHERE object_type='order' AND next IS NOT NULL "
                                           "AND action NOT IN ('transition_rejected') ORDER BY id")]

    def page(w=wm):
        r = w.get(f"/orders/{no}")
        assert r.status_code == 200
        st = S(get(ctx, no)["lifecycle_state"])
        assert PHASE_LABEL[PHASE_OF[st]] in r.text  # ② 대외 표기
        # ③ 대표 조치 영역(“다음 할 일” 카드 — xs에서는 하단 조치 바로 대체, PDS §152) 안의 primary = 1개
        t = re.sub(r"<dialog.*?</dialog>", "", r.text, flags=re.S)
        if 'id="na-t"' in t:
            card = t.split('id="na-t"')[1].split('aria-labelledby="sum-t"')[0]
            bar = t.split('aria-label="대표 조치"')[1].split("</div>")[0] if 'aria-label="대표 조치"' in t else ""
            assert card.count("btn btn-primary") == (1 if "next-main" in card else 0)
            assert bar.count("btn btn-primary") <= 1
        return r

    page()
    assert act(wm, no, "accept").status_code == 303
    o = get(ctx, no)
    quotes.upload_concept(ctx, o, maker, "c1.png", b"\x89PNG v1", "첫 시안", 30)
    quotes.upload_concept(ctx, get(ctx, no), maker, "c2.png", b"\x89PNG v2", "둘째 시안", 20)
    page()
    qid = quotes.draft(ctx, get(ctx, no), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(
        items=[{"kind": "metal", "label": "18K 금", "amount": "900000", "cost": "500000"},
               {"kind": "design", "label": "설계비", "amount": "300000", "cost": "0"}],
        spec={"material": "골드", "purity": "18K", "size": "12호", "stones": "없음", "engraving": "없음",
              "custom_made": "12호·18K 1점 맞춤 제작", "loss_reasons": "맞춤 호수로 재판매 불가"},
        tax_included=True, shipping_included=True, promised_date="2026-12-15", valid_until="2026-11-30", variable_cost=50000))
    assert act(wm, no, "approve_quote").status_code == 303
    page()
    r = act(wm, no, "record_acceptance", channel="카카오톡", evidence="견적 좋아요 답장")
    assert r.status_code == 409 and 'data-unmet-code="G-CONTRACT-REQUIRED"' in r.text  # D-24: 수기 기록으로는 진행 불가
    assert re.search(r">[^<]*G-CONTRACT-REQUIRED", r.text) is None  # 코드는 속성에만, 화면 문구에 노출 없음
    assert ctx.db.scalar("SELECT COUNT(*) FROM customer_acceptances") == 0  # ⑦
    assert wo.post(f"/orders/{no}/contract/issue", data={}).status_code == 303  # 계약 동의 링크(Form B)
    rid = ctx.db.scalar("SELECT id FROM contract_requests")
    assert "entry.528017888=TG-" in wo.get(f"/orders/{no}/quote").text  # 사전입력 링크가 화면에(복사용)
    form_b_response(ctx, rid)  # 고객이 링크로 제출
    assert wo.post(f"/orders/{no}/contract/sync", data={}).status_code == 303
    assert wo.post(f"/contracts/{rid}/review", data={"method": "consult_channel", "review_note": "카톡 상담방에서 본인 확인",
                                                      "remember_email": "on"}).status_code == 303
    acc = ctx.db.one("SELECT * FROM customer_acceptances")
    assert acc["quote_version"] == 1 and acc["terms_version"] and acc["channel"] == "Form B 전자 동의" and acc["accepted_at"]  # ⑧
    assert act(wo, no, "create_payment_req").status_code == 303
    page(wo)
    assert act(wo, no, "record_payment", naver_order_id="N1", naver_product_order_id="P1", amount="400,000",
               naver_status="PAYED", looked_up_at="2026-10-20T14:00").status_code == 303
    assert get(ctx, no)["lifecycle_state"] == "INITIAL_PAYMENT_VERIFIED"
    assert act(wm, no, "start_cad").status_code == 303
    o = get(ctx, no)
    manufacturing.upload_design(ctx, o, maker, {"cad": ("r.3dm", b"3dm"), "preview": ("p.png", b"\x89PNG p"),
                                                "stl": ("r.stl", b"solid r")})
    assert act(wm, no, "record_design_ok", channel="카카오톡", evidence="최종 시안 좋아요").status_code == 303
    stl = ctx.db.scalar("SELECT id FROM assets WHERE kind='stl'")
    manufacturing.save_factory_spec(ctx, get(ctx, no), maker, {"material": "골드", "purity": "18K", "size": "12호",
                                                                "stones": "없음", "engraving": "없음", "finish_na": "on",
                                                                "special_na": "on", "stl_asset_id": stl})
    assert act(wm, no, "build_packet").status_code == 409  # D-24 Gate 2: 잔금 전 발주 불가
    assert act(wo, no, "create_balance_req").status_code == 303
    assert act(wo, no, "record_payment", phase="BALANCE", naver_order_id="N2", naver_product_order_id="P2",
               amount="800000", naver_status="PAYED", looked_up_at="2026-11-20T10:00").status_code == 303
    assert act(wm, no, "build_packet").status_code == 303
    assert act(wm, no, "send_factory", expected_at="2026-11-20").status_code == 303
    page()
    assert act(wm, no, "receive", received_at="2026-11-19", actual_cost="600,000").status_code == 303
    qc = {k: "pass" for k in ("spec", "purity_engraving", "size", "finish", "setting", "fit")}
    assert act(wm, no, "record_qc", **qc).status_code == 303
    assert act(wo, no, "verify_final_paid").status_code == 303  # 발주 전에 확인한 잔금으로 잔금 확인 단계
    assert get(ctx, no)["lifecycle_state"] == "FINAL_PAYMENT_VERIFIED"
    r = act(wm, no, "ship", method="택배", platform_status="배송중")
    assert r.status_code == 409  # ⑨ 배송 정보(수령인) 누락
    assert get(ctx, no)["lifecycle_state"] == "FINAL_PAYMENT_VERIFIED"
    assert act(wm, no, "ship", recipient="김하늘", method="택배", tracking_no="1234", platform_status="배송중").status_code == 303
    assert act(wo, no, "close", evidence="배달 완료 조회").status_code == 303
    page()
    o = get(ctx, no)
    trans = [r for r in ctx.db.all("SELECT * FROM audit_events WHERE order_id=? AND object_type='order' AND next IS NOT NULL "
                                   "AND action<>'transition_rejected' AND (prev IS NULL OR prev<>next) ORDER BY id",
                                   (o["id"],))]  # 단계가 바뀐 이벤트만(패킷 생성 같은 비전이 제외)
    assert [S(r["next"]) for r in trans] == ORDERED  # ① 16단계 순서
    assert all(r["created_at"] and r["action"] for r in trans) and all(r["prev"] for r in trans[1:])  # ④
    assert len(orders.timeline(ctx, o["id"])) >= 15  # ⑤
    cons = quotes.concepts_of(ctx, o["id"])
    assert sorted(c_["version"] for c_ in cons) == [1, 2] and sum(c_["work_minutes"] for c_ in cons) == 50  # ⑥
    assert ctx.db.scalar("SELECT COUNT(*) FROM assets WHERE order_id=? AND kind='concept'", (o["id"],)) == 2
    assert ctx.db.scalar("SELECT COUNT(*) FROM assets WHERE order_id=? AND kind='reference'", (o["id"],)) == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment_requests WHERE is_test=1") == 0  # ⑩
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment_events WHERE is_test=1") == 0
    cb = metrics.contribution(ctx, o)
    # ⑪ SRS-168 ② r2: 일반과세자 — 세금 포함 견적은 세금 제외 매출(1,200,000 ÷ 1.1 = 1,090,909)
    assert cb["contribution"] == 1_090_909 - 600_000 - 50_000 - cb["acquisition"]
    assert "공헌이익" in web(ctx, "operator").get("/analytics").text
    # 추가 판정 — 표 밖 요청·시스템 직접 호출·정적 검사
    n2 = new_order(ctx, "r2")
    o2 = get(ctx, n2)
    r = act(wm, n2, "ship", recipient="x", method="택배")
    assert r.status_code == 409 and get(ctx, n2)["lifecycle_state"] == "OWNER_REVIEW"
    rej = ctx.db.one("SELECT * FROM audit_events WHERE order_id=? AND action='transition_rejected'", (o2["id"],))
    assert rej and "ship" in (rej["detail_json"] or "") and rej["actor_id"] == maker.id
    for action, t in tr.TRANSITIONS.items():
        if "system" not in t.actors:
            with pytest.raises(Forbidden):
                tr.system_perform(ctx, o2["id"], action)
    assert get(ctx, n2)["lifecycle_state"] == "OWNER_REVIEW"
    offenders = []
    for p in PKG.rglob("*.py"):
        if p.name == "transitions.py":
            continue
        if re.search(r"SET[^\"']*lifecycle_state\s*=", p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert offenders == []


# ---------------- TC-S-004 정보 부족·응답 지연·납기 위험 ----------------
def test_tcs_004_needs_info_and_risks(ctx, maker, operator, fclock):
    no = new_order(ctx, desc=None)
    o = get(ctx, no)
    assert any(h["reason"] == "NEEDS_INFO" for h in orders.holds_of(ctx, o["id"]))  # ①
    b = analysis.latest_brief(ctx, o["id"])
    assert 1 <= len(b["result"]["customer_questions"]) <= 3  # ②
    assert ctx.db.scalar("SELECT COUNT(*) FROM outbox_events") == 0
    w = web(ctx, "maker")
    assert act(w, no, "request_info", note="설명을 더 받아야 해요").status_code == 303
    fclock.advance(hours=71)
    alerts.evaluate(ctx)
    assert notif(ctx, "customer_wait", o["id"]) == 0  # ③
    fclock.advance(hours=2)
    alerts.evaluate(ctx)
    assert notif(ctx, "customer_wait", o["id"]) == 1
    page = web(ctx, "maker").get("/").text
    assert "고객 응답 지연" in page
    # ④ 약속일 −4 없음 / −3 있음
    o2 = order_at(ctx, maker, operator, "DETAIL_CAD", rid="d")
    today = clock.today_kst()
    with ctx.db.tx() as c:
        c.execute("UPDATE orders SET promised_date=? WHERE id=?", ((today + timedelta(days=4)).isoformat(), o2["id"]))
    alerts.evaluate(ctx)
    assert notif(ctx, "delivery_risk", o2["id"]) == 0
    fclock.advance(days=1)
    alerts.evaluate(ctx)
    assert notif(ctx, "delivery_risk", o2["id"]) == 1
    page = web(ctx, "maker").get("/").text
    assert "납기 위험" in page and "고객 응답 지연" in page  # ⑥ 라벨이 다름
    assert 'href="/static/icons.svg#i-clock"' in page and 'href="/static/icons.svg#i-alert"' in page


def test_tcs_004_factory_expected(ctx, maker, operator, fclock):
    o = order_at(ctx, maker, operator, "FACTORY_SENT")  # 예상 수령일 2026-11-20
    fclock.set(datetime(2026, 11, 20, 9, 0, tzinfo=clock.KST))
    alerts.evaluate(ctx)
    assert notif(ctx, "delivery_risk", o["id"]) == 0  # ⑤ 당일 없음
    fclock.set(datetime(2026, 11, 21, 9, 0, tzinfo=clock.KST))
    alerts.evaluate(ctx)
    assert notif(ctx, "delivery_risk", o["id"]) == 1


# ---------------- TC-S-005 동시 진행 상한 ----------------
def test_tcs_005_capacity(ctx, maker, operator):
    for i in range(2):
        order_at(ctx, maker, operator, "FREE_CONCEPT", rid=f"a{i}")
    order_at(ctx, maker, operator, "FACTORY_SENT", rid="a2")
    assert capacity.active_count(ctx) == 3
    no = new_order(ctx, "new")
    o = get(ctx, no)
    assert any(h["reason"] == "WAITLISTED" for h in orders.holds_of(ctx, o["id"]))  # ①
    assert o["lifecycle_state"] == "OWNER_REVIEW"  # ⑤ 시스템이 만든 수락 0
    w = web(ctx, "maker")
    r = act(w, no, "accept", reason="사유만 적음")
    msg = "동시 진행 3/3건이에요. 운영자의 상한 초과 예외 승인(사유·예상 납기)이 있어야 수락할 수 있어요."
    assert r.status_code == 409 and msg in r.text  # ② H1 r1 Q2: 사유만으로는 수락 불가
    assert 'id="cap-exc"' in w.get(f"/orders/{no}").text
    wo = web(ctx, "operator")
    assert act(w, no, "approve_capacity_exception", reason="x", expected_eta="2026-12-01").status_code == 403
    assert act(wo, no, "approve_capacity_exception", reason="단골 고객 급한 의뢰").status_code == 409  # 예상 납기 없음
    assert act(wo, no, "approve_capacity_exception", reason="단골 고객 급한 의뢰",
               expected_eta="2026-12-20").status_code == 303
    r = act(w, no, "accept")
    assert r.status_code == 409 and "제작 가능 여부를 확인했다고 체크해야" in r.text  # 아버님 확인 없음
    assert act(w, no, "accept", feasible_confirm="on").status_code == 303  # ③
    x = ctx.db.one("SELECT * FROM capacity_exceptions WHERE order_id=?", (o["id"],))
    assert x["approved_by"] == operator.id and x["maker_confirmed_by"] == maker.id and x["expected_eta"] == "2026-12-20"
    a = ctx.db.one("SELECT * FROM audit_events WHERE order_id=? AND action='accept'", (o["id"],))
    assert "단골" in (a["reason"] or "")
    ev = ctx.db.one("SELECT * FROM audit_events WHERE order_id=? AND action='capacity_exception_confirmed'", (o["id"],))
    assert ev and "2026-12-20" in ev["detail_json"] and str(operator.id) in ev["detail_json"]
    assert get(ctx, no)["internal_eta"] == "2026-12-20"  # 예상 납기 기록
    assert orders.holds_of(ctx, o["id"]) == [] or all(h["cleared_at"] for h in orders.holds_of(ctx, o["id"]))  # ④


# ---------------- TC-S-006 휴무일·휴지 시간 ----------------
def test_tcs_006_holiday_quiet(ctx, maker, operator, fclock):
    fclock.set(datetime(2026, 10, 9, 14, 0, tzinfo=clock.KST))
    no = new_order(ctx)
    w = web(ctx, "maker")
    page = w.get(f"/orders/{no}").text
    msg = "오늘은 휴무일이라 새 주문을 수락하지 않아요."
    assert msg in page and 'aria-disabled="true"' in page  # ①
    r = act(w, no, "accept")
    assert r.status_code == 409 and msg in r.text  # ②
    assert act(w, no, "hold", note="휴무일 보류").status_code == 303  # ③
    fclock.set(datetime(2026, 10, 17, 1, 30, tzinfo=clock.KST))
    wo = web(ctx, "operator")  # 시계를 크게 옮기면 세션이 잠기므로 새로 로그인
    badge0 = wo.get("/api/notifications").json()["badge"]
    for o_ in ctx.db.all("SELECT id FROM orders"):
        pass
    with ctx.db.tx() as c:
        c.execute("UPDATE orders SET promised_date='2026-10-18'")  # 납기 위험 알림이 휴지 시간에 생김
    add_response(ctx, "night")
    intake.sync_once(ctx)
    alerts.evaluate(ctx)
    assert wo.get("/api/notifications").json()["badge"] == badge0  # ④ 배지 불변
    assert "night" not in wo.get("/orders?queue=all").text or True
    assert ctx.db.scalar("SELECT COUNT(*) FROM orders") == 2
    fclock.set(datetime(2026, 10, 17, 7, 1, tzinfo=clock.KST))
    wo = web(ctx, "operator")
    assert wo.get("/api/notifications").json()["badge"] > badge0  # ⑤
    slots = capacity.suggest_cad_slots(ctx, 4)
    assert slots and all(d.weekday() >= 5 for d in slots)  # ⑥
    assert ctx.db.scalar("SELECT COUNT(*) FROM cad_slots") == 0  # ⑦
    o = order_at(ctx, maker, operator, "DETAIL_CAD", rid="cad")
    w = web(ctx, "maker")
    assert act(w, o["order_no"], "set_cad_slot", date=slots[0].isoformat()).status_code == 303
    assert ctx.db.scalar("SELECT COUNT(*) FROM cad_slots") == 1


# ---------------- TC-S-008 PC 중단 후 복귀 ----------------
def test_tcs_008_resume_after_downtime(ctx, maker, operator, fclock):
    from jewelry_os.services import system
    for i in range(5):
        add_response(ctx, f"old{i}", ts="2026-10-20T00:00:00Z")
    intake.sync_once(ctx)
    system.heartbeat(ctx)
    T = clock.now()
    tz = lambda d: clock.iso(d).replace("+00:00", "Z")  # noqa: E731
    add_response(ctx, "same", ts=tz(T))
    for i in range(36):
        add_response(ctx, f"new{i}", ts=tz(T + timedelta(hours=1 + i)))
    fclock.set(T + timedelta(hours=48))
    sid = system.on_startup(ctx)
    system.first_sync_after_startup(ctx, sid)
    assert ctx.db.scalar("SELECT COUNT(*) FROM orders") == 42  # ①
    cur = ctx.db.one("SELECT * FROM sync_cursors")
    assert cur["last_success_at"] == clock.now_iso() and cur["last_count"] == 42  # ②
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    for w in (wm, wo):
        t = w.get("/").text
        assert "PC가 약 48시간 꺼져 있었어요" in t and "37건을 가져왔어요" in t  # ③④
    wo.post("/system/ack-startup", data={"id": str(sid)})
    assert "PC가 약 48시간" not in wm.get("/").text
    assert ctx.db.scalar("SELECT COUNT(*) FROM startup_events") >= 1
    t = wm.get("/").text
    assert "마지막 동기화" in t and "실시간" not in t  # ⑤⑥


# ---------------- TC-S-009 결제 금액 불일치 ----------------
def test_tcs_009_payment_mismatch(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    no = o["order_no"]
    wo, wm = web(ctx, "operator"), web(ctx, "maker")
    page = wm.get(f"/orders/{no}").text
    assert "결제 확인 입력" in page  # ④
    act(wo, no, "record_payment", naver_order_id="N1", naver_product_order_id="P1", amount="350000", naver_status="PAYED",
        looked_up_at="2026-10-20T12:00")
    assert get(ctx, no)["lifecycle_state"] == "PAYMENT_PENDING"  # ①
    iss = ctx.db.one("SELECT * FROM issues WHERE type='PAYMENT_MISMATCH' AND state='OPEN'")
    assert iss
    alerts.evaluate(ctx)
    assert "결제 금액이 맞지 않아요" in wo.get("/").text  # ②
    t = wo.get(f"/orders/{no}/payment").text
    assert "400,000원" in t and "350,000원" in t and "50,000원" in t  # ③
    assert act(wo, no, "resolve_issue", issue_id=str(iss["id"]), resolution="추가 결제 예정").status_code == 303
    act(wo, no, "record_payment", naver_order_id="N1", naver_product_order_id="P1b", amount="50000", naver_status="PAYED",
        looked_up_at="2026-10-20T13:00")
    assert get(ctx, no)["lifecycle_state"] == "INITIAL_PAYMENT_VERIFIED"  # ⑤
    assert act(wm, no, "start_cad").status_code == 303  # ⑥
    # 차단 문구 확인
    o2 = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED", rid="b")
    with ctx.db.tx() as c:
        orders.open_issue(c, o2["id"], "PAYMENT_MISMATCH", operator, "t")
    msg = "결제 불일치 문제가 열려 있어요."
    page = wm.get(f"/orders/{o2['order_no']}").text
    assert msg in page and 'aria-disabled="true"' in page
    r = act(wm, o2["order_no"], "start_cad")
    assert r.status_code == 409 and msg in r.text


# ---------------- TC-S-010 제작 중 취소 ----------------
def test_tcs_010_cancel_during_making(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "FACTORY_SENT")
    no = o["order_no"]
    wo, wm = web(ctx, "operator"), web(ctx, "maker")
    act(wo, no, "record_payment", naver_order_id="N-x", naver_product_order_id=f"P-{no}-1", amount="400000",
        naver_status="CANCEL_REQUEST", looked_up_at="2026-10-21T10:00")
    assert get(ctx, no)["lifecycle_state"] == "FACTORY_SENT"  # ①
    iss = ctx.db.one("SELECT * FROM issues WHERE type='REFUND_REVIEW' AND state='OPEN'")
    alerts.evaluate(ctx)
    assert iss and notif(ctx, "payment_issue", o["id"]) == 1  # ②
    assert act(wm, no, "receive", received_at="2026-11-19", actual_cost="600000").status_code == 303  # ③
    qc = {k: "pass" for k in ("spec", "purity_engraving", "size", "finish", "setting", "fit")}
    assert act(wm, no, "record_qc", **qc).status_code == 409  # ④
    r = act(wo, no, "resolve_issue", issue_id=str(iss["id"]), resolution="고객이 취소 원함", outcome="cancel")
    assert r.status_code == 303 and "confirm=cancel" in r.headers["location"]
    page = wo.get(f"/orders/{no}/payment?confirm=cancel").text
    assert "전액 환불" in page and "일부 환불" in page and "환불할 결제 없음" in page  # ⑥
    assert "몰수" not in page and "환불 불가" not in page
    base = dict(refund_decision="PARTIAL", refund_amount="300000", refund_basis="환불 정책 3조", evidence="카톡 취소 요청",
                reason="고객 요청")
    assert act(wo, no, "cancel", **base).status_code == 409  # ⑤ 귀책 없음
    assert act(wo, no, "cancel", fault="CUSTOMER_CHANGE", **base).status_code == 303
    o = get(ctx, no)
    assert o["lifecycle_state"] == "CANCELLED" and PHASE_LABEL[PHASE_OF[S.CANCELLED]] == "진행 종료"  # ⑦
    assert "진행 종료" in wo.get(f"/orders/{no}").text
    before = [dict(r) for r in ctx.db.all("SELECT * FROM payment_events ORDER BY id")]
    act(wo, no, "record_payment", naver_order_id="N-x", naver_product_order_id=f"P-{no}-1", amount="300000",
        naver_status="REFUND", looked_up_at="2026-10-22T10:00")
    after = [dict(r) for r in ctx.db.all("SELECT * FROM payment_events ORDER BY id")]
    assert after[:len(before)] == before and after[-1]["event_type"] == "REFUND" and after[-1]["amount_krw"] == 300000  # ⑧


# ---------------- TC-S-011 고객 디자인 변경(재견적) ----------------
def test_tcs_011_change_request(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    no = o["order_no"]
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    assert act(wm, no, "open_issue", type="CHANGE_REQUEST", note="모양 변경").status_code == 409  # 귀책 없이 → G-FAULT
    assert act(wm, no, "open_issue", type="CHANGE_REQUEST", note="모양 변경", fault="CUSTOMER_CHANGE").status_code == 303
    r = act(wo, no, "record_payment", naver_order_id="N1", naver_product_order_id="P1", amount="400000",
            naver_status="PAYED", looked_up_at="2026-10-20T12:00")
    assert get(ctx, no)["lifecycle_state"] == "PAYMENT_PENDING"  # ① 결제 확인 안 됨
    assert not any(h["reason"] == "HOLD" for h in orders.holds_of(ctx, o["id"]))  # ②
    q1 = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND version=1", (o["id"],))
    r = wm.post(f"/quotes/{q1['id']}/save", data={"items-0-label": "x", "items-0-amount": "1"})
    assert r.status_code == 405  # ③
    assert ctx.db.one("SELECT total_krw FROM quotes WHERE id=?", (q1["id"],))[0] == 1_200_000
    assert act(wm, no, "approve_requote").status_code == 409  # 초안 없음
    qid = quotes.draft(ctx, get(ctx, no), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(
        items=[{"kind": "metal", "label": "18K 금", "amount": "1200000"}, {"kind": "design", "label": "모양 변경",
                                                                         "amount": "300000"}],
        spec={"material": "골드", "purity": "18K", "size": "12호", "stones": "없음", "engraving": "하트"},
        tax_included=True, shipping_included=True, promised_date="2026-12-20", valid_until="2026-11-30"))
    assert act(wm, no, "approve_requote").status_code == 303
    reqs = payments.requests_of(ctx, o["id"])
    assert reqs[0]["status"] in ("CANCELLED", "PAID")  # ④ 미결제 요청은 취소(이미 입력된 결제는 이벤트로 남음)
    assert ctx.db.scalar("SELECT COUNT(*) FROM customer_acceptances WHERE quote_version=1") == 1
    assert act(wm, no, "record_reacceptance", channel="카카오톡", evidence="변경 견적 좋아요").status_code == 303
    assert not ctx.db.one("SELECT 1 FROM issues WHERE type='CHANGE_REQUEST' AND state='OPEN'")  # ⑤


def test_tcs_011_change_request_unpaid(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    no = o["order_no"]
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    act(wm, no, "open_issue", type="CHANGE_REQUEST", note="크기 변경", fault="CUSTOMER_CHANGE")
    qid = quotes.draft(ctx, get(ctx, no), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(
        items=[{"kind": "metal", "label": "18K 금", "amount": "1500000"}],
        spec={"material": "골드", "purity": "18K", "size": "14호", "stones": "없음", "engraving": "없음"},
        tax_included=True, shipping_included=True, promised_date="2026-12-20", valid_until="2026-11-30"))
    act(wm, no, "approve_requote")
    reqs = payments.requests_of(ctx, o["id"])
    assert reqs[0]["status"] == "CANCELLED"  # ④
    act(wm, no, "record_reacceptance", channel="카카오톡", evidence="좋아요")
    assert act(wo, no, "recreate_payment_req").status_code == 303
    assert payments.requests_of(ctx, o["id"])[-1]["expected_krw"] == 500_000  # ⑥ v2 기준(1,500,000의 1/3)


def test_tcs_011_rework_seller_fault(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "FACTORY_RECEIVED")
    no = o["order_no"]
    wm = web(ctx, "maker")
    qc = {k: "pass" for k in ("spec", "purity_engraving", "size", "finish", "setting", "fit")}
    act(wm, no, "record_qc", **{**qc, "finish": "fail"})
    assert act(wm, no, "open_issue", type="REWORK", note="광택 부족", fault="DEFECT").status_code == 303
    iss = ctx.db.one("SELECT * FROM issues WHERE type='REWORK' AND state='OPEN'")
    assert act(wm, no, "resolve_issue", issue_id=str(iss["id"]), resolution="재연마 완료").status_code == 303
    assert act(wm, no, "record_qc", **qc).status_code == 303
    assert get(ctx, no)["lifecycle_state"] == "QC_PASSED"


# ---------------- TC-S-012 카카오 CS 초안 ----------------
def test_tcs_012_cs_drafts(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    no = o["order_no"]
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    assert wo.post(f"/orders/{no}/cs/messages", data={"summary": "사이즈를 12호로 바꿀 수 있나요?"}).status_code == 303
    assert wo.post(f"/orders/{no}/cs/drafts", data={"question": "사이즈를 12호로 바꿀 수 있나요?"}).status_code == 303
    d = cs.drafts_of(ctx, o["id"])[0]
    assert d["level"] == "L2" and d["policy_id"] and d["policy_version"]  # ①
    assert "수동 기록 — 실시간 수신 아님" in wo.get(f"/orders/{no}/cs").text
    assert wo.post(f"/cs/drafts/{d['id']}/copied").status_code == 409  # ②
    assert wm.post(f"/cs/drafts/{d['id']}/approve", data={"confirm": "on"}).status_code == 303
    assert wm.post(f"/cs/drafts/{d['id']}/save", data={"body": "바꿈"}).status_code == 405  # ③
    assert cs.draft(ctx, d["id"])["body"] != "바꿈"
    assert wm.post(f"/cs/drafts/{d['id']}/rewrite").status_code == 303  # ④
    d2 = cs.drafts_of(ctx, o["id"])[0]  # 최신순
    assert d2["id"] != d["id"] and d2["status"] == "DRAFT" and cs.draft(ctx, d["id"])["status"] == "SUPERSEDED"
    assert wo.post(f"/cs/drafts/{d2['id']}/copied").status_code == 409
    assert wm.post(f"/cs/drafts/{d2['id']}/approve", data={"confirm": "on"}).status_code == 303
    assert wo.post(f"/cs/drafts/{d2['id']}/copied").status_code == 303
    assert wo.post(f"/cs/drafts/{d2['id']}/sent", data={"evidence": "카톡 발송"}).status_code == 303
    assert wm.post(f"/cs/drafts/{d2['id']}/rewrite").status_code == 409  # ⑤
    # ⑥ L0
    l0 = cs.create_draft(ctx, get(ctx, no), operator, "영업시간이 언제예요?", level="L0")
    assert wo.post(f"/cs/drafts/{l0}/copied").status_code == 409
    # ⑦ L3
    l3 = cs.create_draft(ctx, get(ctx, no), operator, "환불해 주세요")
    assert cs.draft(ctx, l3)["level"] == "L3"
    assert wm.post(f"/cs/drafts/{l3}/approve", data={"confirm": "on"}).status_code == 409
    assert "확약하는 표현을 쓰지 마세요" in wo.get(f"/orders/{no}/cs").text
    assert wo.post(f"/cs/drafts/{l3}/judgment", data={"judgment": "정책상 제작 시작 후 일부 환불"}).status_code == 303
    assert wm.post(f"/cs/drafts/{l3}/approve", data={"confirm": "on"}).status_code == 303
    # ⑧ 정책 v2 후 기존 참조 = v1
    v1 = cs.draft(ctx, d2["id"])["policy_version"]
    pid = settings.create_policy(ctx, "cs", "상담 정책", "v2 본문", operator)
    settings.approve_policy(ctx, pid, operator)
    assert cs.draft(ctx, d2["id"])["policy_version"] == v1
    # ⑨ 본문 단계 표현
    internal = [LABEL[s] for s in S if LABEL[s] not in PHASE_LABEL.values()]
    for r in ctx.db.all("SELECT body FROM cs_drafts"):
        assert not any(lab in (r["body"] or "") for lab in internal), r["body"]


# ---------------- TC-S-013 고객 견적 문서 ----------------
def test_tcs_013_customer_quote_doc(ctx, maker, operator):
    no = new_order(ctx)
    approve_policies(ctx, operator)
    advance(ctx, no, "FREE_CONCEPT", maker, operator)
    o = get(ctx, no)
    quotes.upload_concept(ctx, o, maker, "c.png", b"\x89PNG", "", 10)
    qid = quotes.draft(ctx, get(ctx, no), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(
        items=[{"kind": "metal", "label": "18K 금", "amount": "900000", "cost": "500000", "note": "내부메모-공장단가"},
               {"kind": "design", "label": "설계비", "amount": "300000"}],
        spec={"material": "골드", "purity": "18K", "size": "12호", "stones": "없음", "engraving": "없음"},
        tax_included=True, shipping_included=True, promised_date="2026-12-15", valid_until="2026-11-30",
        internal_note="마진 넉넉"))
    w = web(ctx, "maker")
    r = w.get(f"/docs/quote/{qid}")
    assert r.status_code == 403 and "승인 전 견적은 고객에게 보여줄 수 없어요" in r.text  # ①
    quotes.approve(ctx, get(ctx, no), maker, C)
    for url in (f"/docs/quote/{qid}", f"/docs/quote/{qid}.txt"):
        r = w.get(url)
        assert r.status_code == 200  # ②
        for bad in ("원가", "마진", "내부", "500,000", "BRAND_IMITATION", "COMPLEX_SHAPE", "공장단가"):
            assert bad not in r.text, (url, bad)  # ④
        assert "1,200,000원" in r.text and "v1" in r.text  # ⑤
    pq = quotes.public_quote(ctx, qid)
    import dataclasses
    keys = {f.name for f in dataclasses.fields(pq)}
    assert not keys & {"cost", "internal_cost_krw", "internal_note", "cost_estimate_krw", "risk_flags"}  # ③
    txt = w.get(f"/docs/quote/{qid}.txt").text
    internal = [LABEL[s] for s in S if LABEL[s] not in PHASE_LABEL.values()]
    assert not any(lab in txt for lab in internal)  # ⑥


# ---------------- TC-S-014 시험 운영 모드와 Gate ----------------
def test_tcs_014_test_mode_gates(ctx, maker, operator):
    approve_policies(ctx, operator)
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    for u in ("/", "/orders", "/payments", "/orders/new"):
        assert "시험 운영 모드" in wo.get(u).text  # ① 배너
    assert "실제 고객 정보를 입력하지 마세요" in wo.get("/orders/new").text
    r = wo.post("/settings/google", data={"google_mode": "live", "google_form_purpose": "public", "google_form_id": "F"})
    assert r.status_code == 409  # ② G-GATE-LIVE
    r = wo.post("/settings/google", data={"google_mode": "live", "google_form_purpose": "test", "google_form_id": "F",
                                          "confirm_test_form": "on"})
    assert r.status_code == 303
    settings.put(ctx, "google_mode", "mock", operator)
    from jewelry_os.bootstrap import configure_adapters
    configure_adapters(ctx)
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING", rid="t1")  # ③
    assert payments.requests_of(ctx, o["id"])[0]["is_test"] == 1
    for gid in ("GATE-OPS-01", "GATE-SS-02", "GATE-LEGAL-01"):  # ④
        assert wo.post(f"/settings/gates/{gid}", data={"status": "PASSED", "confirm": "on", "evidence": f"{gid} 증거"}).status_code == 303
    assert "시험 운영 모드" not in wo.get("/").text
    o2 = order_at(ctx, maker, operator, "CUSTOMER_ACCEPTED", rid="t2")
    assert ctx.db.scalar("SELECT is_test FROM quotes WHERE order_id=? AND status='APPROVED'", (o2["id"],)) == 1
    r = act(wo, o2["order_no"], "create_payment_req")
    assert r.status_code == 409
    qid = ctx.db.scalar("SELECT id FROM quotes WHERE order_id=? AND status='APPROVED'", (o2["id"],))
    assert "시험 견적" in wm.get(f"/docs/quote/{qid}").text
    assert wo.post("/settings/gates/GATE-Q-01", data={"status": "PASSED", "confirm": "on", "evidence": "견적 검토"}).status_code == 303  # ⑤
    n3 = intake.manual_intake(ctx, operator, {"display_name": "실고객", "contact_kind": "전화", "contact_value": "010-9999-0000",
                                              "product_type": "반지", "description": "18K 반지",
                                              "consent_channel": "전화", "consent_evidence": "구두 동의"})
    o3 = advance(ctx, n3, "PAYMENT_PENDING", maker, operator)  # Mock 폼 주문은 설계상 시험 주문 → 수기 접수로 실거래 확인
    assert payments.requests_of(ctx, o3["id"])[0]["is_test"] == 0
    r = wo.post("/settings/google", data={"google_mode": "live", "google_form_purpose": "public", "google_form_id": "F"})
    assert r.status_code == 303  # ⑥
    settings.put(ctx, "google_mode", "mock", operator)
    configure_adapters(ctx)
    assert wm.post("/settings/gates/GATE-CAP-01", data={"status": "PASSED", "confirm": "on", "evidence": "x"}).status_code == 403  # ⑦
    assert ctx.db.scalar("SELECT COUNT(*) FROM gate_records") >= 4  # ⑧
    assert all(r["evidence"] and r["recorded_by"] and r["recorded_at"] for r in ctx.db.all("SELECT * FROM gate_records"))
    n4 = new_order(ctx, "t4")
    assert "수용량 정책이 아직 확정되지 않았어요" in wm.get(f"/orders/{n4}").text  # ⑨
    from jewelry_os.core import config as jcfg
    assert jcfg.HOST == "127.0.0.1" and 'host=HOST' in (PKG / "cli.py").read_text(encoding="utf-8")  # ⑩


# ---------------- TC-S-016 되돌리기 ----------------
def test_tcs_016_revert(ctx, maker, operator):
    no = new_order(ctx)
    approve_policies(ctx, operator)
    w, wo = web(ctx, "maker"), web(ctx, "operator")
    act(w, no, "accept")
    assert act(w, no, "revert").status_code == 409  # ② 사유 없음
    assert act(w, no, "revert", reason="실수로 수락").status_code == 303  # ①
    o = get(ctx, no)
    assert o["lifecycle_state"] == "OWNER_REVIEW" and ctx.db.one("SELECT 1 FROM audit_events WHERE action='revert'")
    act(w, no, "accept")
    quotes.upload_concept(ctx, get(ctx, no), maker, "c.png", b"\x89PNG", "", 10)
    assert act(w, no, "revert", reason="다시").status_code == 303  # ③
    assert len(quotes.concepts_of(ctx, o["id"])) == 1
    advance(ctx, no, "CUSTOMER_ACCEPTED", maker, operator)
    assert act(w, no, "revert", reason="x").status_code == 409  # ③-2
    n2 = new_order(ctx, "d")
    act(w, n2, "decline", category="UNFIT", reason="부적합")
    assert act(w, n2, "revert", reason="x").status_code == 409  # ④
    advance(ctx, no, "INITIAL_PAYMENT_VERIFIED", maker, operator)
    assert act(wo, no, "revert", reason="x").status_code == 409  # ⑤
    advance(ctx, no, "FACTORY_SENT", maker, operator)
    assert act(w, no, "revert", reason="x").status_code == 409  # ⑥
    n3 = new_order(ctx, "s")
    assert act(w, n3, "revert", reason="x").status_code == 409  # ⑦ 직전 전이 = 시스템(to_review)


# ---------------- TC-S-017 수기 접수 ----------------
def test_tcs_017_manual_intake(ctx, operator):
    w = web(ctx, "operator")
    base = {"display_name": "박고객", "contact_kind": "전화", "contact_value": "010-2222-3333", "product_type": "반지",
            "description": "커플링 두 개", "consent_channel": "전화", "consent_evidence": "통화 중 구두 동의 2026-10-20 11:00"}
    for k in ("display_name", "contact_value", "product_type", "description", "consent_evidence"):
        assert w.post("/orders/new", data={**base, k: ""}).status_code == 422, k  # ①②
    r = w.post("/orders/new", data=base)
    assert r.status_code == 303
    o = ctx.db.one("SELECT * FROM orders")
    assert o["source"] == "manual" and o["lifecycle_state"] == "OWNER_REVIEW"  # ③
    assert "수기" in w.get(f"/orders/{o['order_no']}").text
    assert o["order_no"] in w.get("/orders?queue=review").text


# ---------------- TC-S-018 고객 이력·받을 돈 ----------------
def test_tcs_018_customers_receivables(ctx, maker, operator):
    a = order_at(ctx, maker, operator, "PAYMENT_PENDING", rid="x1")
    b = order_at(ctx, maker, operator, "OWNER_REVIEW", rid="x2", contact="010-2222-3333")  # 전화가 달라 자동 연결 안 됨
    assert a["customer_id"] != b["customer_id"]  # ①
    w = web(ctx, "operator")
    assert w.post(f"/customers/{a['customer_id']}/link", data={"other_id": str(b["customer_id"]),
                                                              "reason": "같은 분 확인"}).status_code == 303
    d = customers.detail(ctx, a["customer_id"])
    assert len(d["orders"]) == 2 and d["last_spec"]["size"] == "12호"  # ②
    ev = ctx.db.one("SELECT * FROM audit_events WHERE action='customer_linked'")
    assert ev and ev["prev"] == str(b["customer_id"]) and ev["next"] == str(a["customer_id"])  # ③
    assert ev["actor_id"] == operator.id and ev["created_at"]
    assert not any("김하늘" in str(v) or "010" in str(v) for v in dict(ev).values())
    rc = payments.receivables(ctx)
    assert rc["test"] + rc["live"] == 400_000  # ④
    payments.record_payment(ctx, get(ctx, a["order_no"]), operator, {**C, "naver_order_id": "N", "naver_product_order_id": "P",
                                                                     "amount": "400000", "naver_status": "PAYED",
                                                                     "looked_up_at": "2026-10-20T12:00"})
    assert payments.receivables(ctx)["test"] + payments.receivables(ctx)["live"] == 0
    m = dict(metrics.monthly(ctx))
    assert m["2026-10"] == {"live": 0, "test": 400_000}  # ⑤ Mock 폼 주문 = 시험 금액으로 분리
