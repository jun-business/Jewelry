"""TC-I-001~013 — BD §8 통합 시험(모듈 경계)."""
import re
import sqlite3
import threading
from datetime import datetime

import pytest
from helpers import C, get, new_order, order_at

from conftest import approve_policies
from jewelry_os.core import clock
from jewelry_os.domain import transitions as tr
from jewelry_os.services import alerts, backup, capacity, cs, intake, manufacturing, orders, payments, quotes, settings
from webutil import web
from jewelry_os.devtools import pay_balance


def act(w, no, code, **data):
    return w.post(f"/orders/{no}/actions/{code}", data={"confirm": "on", **data})


def unmet_messages(html):
    return set(re.findall(r'data-unmet-code="[^"]+"[^>]*>.*?<span class="sr-only">[^<]*</span>([^<]+)', html, flags=re.S))


def test_tci_001_intake_to_review(ctx):
    no = new_order(ctx)
    assert get(ctx, no)["lifecycle_state"] == "OWNER_REVIEW"
    assert ctx.db.scalar("SELECT COUNT(*) FROM ai_tasks") == 1
    assert ctx.db.scalar("SELECT last_success_at FROM sync_cursors") == clock.now_iso()


def test_tci_002_analysis_failure(ctx, maker):
    no = new_order(ctx, desc="[[mock:timeout]]")
    o = get(ctx, no)
    assert o["lifecycle_state"] == "OWNER_REVIEW" and ctx.db.scalar("SELECT status FROM ai_tasks") == "TIMEOUT"
    w = web(ctx, "maker")
    assert "AI 정리 다시 하기" in w.get(f"/orders/{no}").text  # 재시도 가능
    ctx.forms.responses[0]["answers"]["q04_description"]["textAnswers"]["answers"][0]["value"] = "18K 반지"
    with ctx.db.tx() as c:  # 원문은 그대로 두고 장애가 풀린 상황: Mock 장애 표지를 지운 revision을 만든다
        c.execute("UPDATE intake_revisions SET answers_json=replace(answers_json, '[[mock:timeout]]', '18K 반지')")
    assert act(w, no, "retry_ai").status_code == 303
    assert ctx.db.scalar("SELECT status FROM ai_tasks ORDER BY id DESC LIMIT 1") == "OK"
    assert get(ctx, no)["lifecycle_state"] == "OWNER_REVIEW"


def test_tci_003_payment_rollback(ctx, maker, operator, monkeypatch):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    monkeypatch.setattr(tr, "_perform", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("inject")))
    with pytest.raises(RuntimeError):
        payments.record_payment(ctx, o, operator, {**C, "naver_order_id": "N", "naver_product_order_id": "P", "amount": "400000",
                                                   "naver_status": "PAYED", "looked_up_at": "2026-10-20T12:00"})
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment_events") == 0
    assert ctx.db.scalar("SELECT status FROM payment_requests") == "DRAFT"
    assert get(ctx, o["order_no"])["lifecycle_state"] == "PAYMENT_PENDING"


def test_tci_004_issue_check_and_reverify(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    payments.record_payment(ctx, o, operator, {**C, "naver_order_id": "N", "naver_product_order_id": "P1", "amount": "400000",
                                               "naver_status": "PAYED", "looked_up_at": "2026-10-20T12:00"})
    assert get(ctx, o["order_no"])["lifecycle_state"] == "INITIAL_PAYMENT_VERIFIED"
    with ctx.db.tx() as c:
        iid = orders.open_issue(c, o["id"], "PAYMENT_MISMATCH", operator, "확인")
    assert any(u.code.startswith("G-ISSUE") or "결제 불일치" in u.message for u in tr.check(ctx, o["id"], "start_cad", maker))
    orders.resolve_issue(ctx, iid, operator, "금액 정정")
    assert tr.check(ctx, o["id"], "start_cad", maker) == []


def test_tci_005_new_design_revalidates(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "CUSTOMER_DESIGN_APPROVED")
    pay_balance(ctx, o["order_no"], operator)  # D-24 Gate 2: 발주 전 잔금
    stl = ctx.db.scalar("SELECT id FROM assets WHERE kind='stl'")
    manufacturing.save_factory_spec(ctx, o, maker, {"material": "골드", "purity": "18K", "size": "12호", "stones": "없음",
                                                     "engraving": "없음", "finish_na": "on", "special_na": "on",
                                                     "stl_asset_id": stl})
    manufacturing.build_packet(ctx, get(ctx, o["order_no"]), maker)
    manufacturing.upload_design(ctx, get(ctx, o["order_no"]), maker, {"stl": ("v2.stl", b"solid v2")})
    ev = ctx.db.one("SELECT * FROM audit_events WHERE action='revalidate_design'")
    assert ctx.db.scalar("SELECT status FROM factory_packets") == "VOID"
    assert get(ctx, o["order_no"])["lifecycle_state"] == "DETAIL_CAD" and ev["actor_id"] == maker.id


def test_tci_006_screen_and_post_same_messages(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    with ctx.db.tx() as c:
        orders.set_hold(c, o["id"], "HOLD", maker, "t")
        orders.open_issue(c, o["id"], "PAYMENT_MISMATCH", operator, "t")
    w = web(ctx, "maker")
    screen = {u.message for u in tr.check(ctx, o["id"], "start_cad", maker)}
    page = w.get(f"/orders/{o['order_no']}").text
    r = act(w, o["order_no"], "start_cad")
    assert r.status_code == 409
    assert screen and all(m in page for m in screen) and all(m in r.text for m in screen)


def test_tci_007_state_trigger_guard(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    with pytest.raises(sqlite3.DatabaseError):
        with ctx.db.tx() as c:
            c.execute("UPDATE orders SET lifecycle_state='CLOSED' WHERE id=?", (o["id"],))
    assert get(ctx, o["order_no"])["lifecycle_state"] == "OWNER_REVIEW"


def test_tci_008_quiet_hours_badge(ctx, maker, operator, fclock):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    fclock.set(datetime(2026, 10, 21, 1, 0, tzinfo=clock.KST))
    with ctx.db.tx() as c:
        c.execute("UPDATE orders SET promised_date='2026-10-22' WHERE id=?", (o["id"],))
    alerts.evaluate(ctx)
    rows = ctx.db.all("SELECT * FROM notifications")
    assert rows and all(clock.kst(clock.parse(n["release_at"])).strftime("%H:%M") == "07:00" for n in rows)
    assert alerts.badge_count(ctx, maker.id) == 0  # 휴지 시간에 생긴 알림은 배지에 아직 없음
    fclock.set(datetime(2026, 10, 21, 7, 0, tzinfo=clock.KST))
    assert alerts.badge_count(ctx, maker.id) == len(rows)


def test_tci_009_backup_during_writes(ctx, maker, operator):
    from jewelry_os.adapters import google as g
    ctx.files.files["f"] = g.sample_jpeg("f")
    order_at(ctx, maker, operator, "CUSTOMER_ACCEPTED", files=["f"])
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            with ctx.db.tx() as c:
                c.execute("INSERT INTO work_logs(order_id, kind, minutes, work_date, created_at) VALUES (1,'cs',1,'2026-10-20',?)",
                          (clock.now_iso(),))
            i += 1
            stop.wait(0.01)  # 사람이 쓰는 속도보다 훨씬 빠른 연속 쓰기(100회/초)
        ctx.db.close()  # 스레드별 연결을 닫는다(복원 전 파일 잠금 해제)
    t = threading.Thread(target=writer)
    t.start()
    try:
        p = backup.make_backup(ctx)
    finally:
        stop.set()
        t.join()
    ctx.db.close()
    s = backup.restore(ctx.cfg, p)
    assert s["bad_files"] == [] and s["orders"] == 1
    con = sqlite3.connect(ctx.cfg.db_path)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_tci_010_policy_refs(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    p1 = settings.approved_policy(ctx, "cs")
    d1 = cs.draft(ctx, cs.create_draft(ctx, get(ctx, o["order_no"]), operator, "진행 상황?"))
    assert d1["policy_id"] == p1["id"]
    settings.retire_policy(ctx, p1["id"], operator)
    d2 = cs.draft(ctx, cs.create_draft(ctx, get(ctx, o["order_no"]), operator, "진행 상황?"))
    assert d2["policy_id"] is None
    p2 = settings.create_policy(ctx, "cs", "상담 정책", "v2", operator)
    settings.approve_policy(ctx, p2, operator)
    d3 = cs.draft(ctx, cs.create_draft(ctx, get(ctx, o["order_no"]), operator, "진행 상황?"))
    assert d3["policy_id"] == p2 and cs.draft(ctx, d1["id"])["policy_id"] == p1["id"]


def test_tci_011_no_consent_flow(ctx, maker):
    no = new_order(ctx, consent=False)
    assert ctx.analyzer.calls == [] and no in web(ctx, "maker").get("/orders?queue=review").text
    orders.record_consent(ctx, get(ctx, no), maker, "카카오톡", "동의 답장")
    from jewelry_os.services import analysis
    analysis.process_new(ctx, get(ctx, no)["id"])
    assert len(ctx.analyzer.calls) == 1


def test_tci_012_change_request_payments(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    no = o["order_no"]
    orders.open_issue_action(ctx, o, maker, "CHANGE_REQUEST", "크기 변경", "CUSTOMER_CHANGE")
    qid = quotes.draft(ctx, get(ctx, no), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(items=[{"kind": "metal", "label": "금", "amount": "1500000"}],
                                                  spec={"material": "골드", "purity": "18K", "size": "14호", "stones": "없음",
                                                        "engraving": "없음"}, tax_included=True, shipping_included=True,
                                                  promised_date="2026-12-20", valid_until="2026-11-30"))
    quotes.approve_requote(ctx, get(ctx, no), maker, C)
    assert payments.requests_of(ctx, o["id"])[0]["status"] == "CANCELLED"
    quotes.record_reacceptance(ctx, get(ctx, no), maker, {**C, "channel": "카카오톡", "evidence": "좋아요"})
    assert not ctx.db.one("SELECT 1 FROM issues WHERE state='OPEN'")
    payments.recreate_initial(ctx, get(ctx, no), operator)
    assert payments.requests_of(ctx, o["id"])[-1]["expected_krw"] == 500_000


def test_tci_013_web_optimistic_lock(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    w = web(ctx, "maker")
    v = str(o["version"])
    assert act(w, o["order_no"], "hold", version=v, note="잠깐").status_code == 303
    r = act(w, o["order_no"], "accept", version=v, reason="이전 화면에서 누름")
    assert r.status_code == 409 and 'data-unmet-code="G-CONFLICT"' in r.text
    assert "이전 화면에서 누름" in r.text  # 입력값 재렌더
