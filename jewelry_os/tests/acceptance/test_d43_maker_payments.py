"""D-43: 아버님 계정 하나로 업무 전체 — 결제·주문 완료·취소·상한 예외·상담 L3·설정(계정 전환 없음)."""
import pytest
from helpers import get, order_at

from jewelry_os.services import cs
from webutil import web


def act(w, no, code, **data):
    return w.post(f"/orders/{no}/actions/{code}", data={"confirm": "on", **data})


def test_maker_records_payment_and_resolves_mismatch(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    no = o["order_no"]
    wm = web(ctx, "maker")
    t = wm.get(f"/orders/{no}/payment").text
    assert "운영자 계정만 할 수 있어요" not in t
    act(wm, no, "record_payment", naver_order_id="N1", naver_product_order_id="P1", amount="350000", naver_status="PAYED",
        looked_up_at="2026-10-20T12:00")
    assert get(ctx, no)["lifecycle_state"] == "PAYMENT_PENDING"
    iss = ctx.db.one("SELECT * FROM issues WHERE type='PAYMENT_MISMATCH' AND state='OPEN'")
    assert act(wm, no, "resolve_issue", issue_id=str(iss["id"]), resolution="추가 결제 예정").status_code == 303
    act(wm, no, "record_payment", naver_order_id="N1", naver_product_order_id="P1b", amount="50000", naver_status="PAYED",
        looked_up_at="2026-10-20T13:00")
    assert get(ctx, no)["lifecycle_state"] == "INITIAL_PAYMENT_VERIFIED"
    assert act(wm, no, "start_cad").status_code == 303


def test_maker_balance_request_through_close(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "CUSTOMER_DESIGN_APPROVED")
    no = o["order_no"]
    wm = web(ctx, "maker")
    assert "운영자 계정만 할 수 있어요" not in wm.get(f"/orders/{no}/payment").text
    assert act(wm, no, "create_balance_req").status_code == 303
    bal = ctx.db.scalar("SELECT expected_krw FROM payment_requests WHERE phase='BALANCE' AND order_id=?", (o["id"],))
    assert act(wm, no, "record_payment", phase="BALANCE", naver_order_id="N2", naver_product_order_id="P2",
               amount=str(bal), naver_status="PAYED", looked_up_at="2026-11-20T10:00").status_code == 303
    assert ctx.db.scalar("SELECT status FROM payment_requests WHERE phase='BALANCE' AND order_id=?",
                         (o["id"],)) == "PAID_VERIFIED"
    s = order_at(ctx, maker, operator, "SHIPPED", rid="d43-ship")
    assert "운영자 계정만 할 수 있어요" not in wm.get(f"/orders/{s['order_no']}/qc").text
    assert act(wm, s["order_no"], "close", evidence="배달 완료 조회").status_code == 303
    assert get(ctx, s["order_no"])["lifecycle_state"] == "CLOSED"


def test_maker_cancels_and_approves_capacity_exception(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    no = o["order_no"]
    wm = web(ctx, "maker")
    assert 'data-open="dlg-cancel"' in wm.get(f"/orders/{no}").text
    assert act(wm, no, "cancel", fault="CUSTOMER_CHANGE", refund_decision="PARTIAL", refund_amount="100000",
               refund_basis="환불 정책 3조", evidence="카톡 취소 요청", reason="고객 요청").status_code == 303
    assert get(ctx, no)["lifecycle_state"] == "CANCELLED"
    r = order_at(ctx, maker, operator, "OWNER_REVIEW", rid="d43-cap")
    assert "운영자 계정만 할 수 있어요" not in wm.get(f"/orders/{r['order_no']}").text
    from jewelry_os.services import orders
    with pytest.raises(ValueError, match="상한"):  # 역할 검사는 통과하고, 상한에 닿지 않아 예외가 필요 없다는 안내
        orders.approve_capacity_exception(ctx, get(ctx, r["order_no"]), maker, "단골 급한 의뢰", "2026-12-20")


def test_maker_records_l3_judgment_and_changes_settings(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    no = o["order_no"]
    wm = web(ctx, "maker")
    l3 = cs.create_draft(ctx, get(ctx, no), maker, "환불해 주세요")
    assert cs.draft(ctx, l3)["level"] == "L3"
    assert "운영자 계정만 할 수 있어요" not in wm.get(f"/orders/{no}/cs").text
    assert wm.post(f"/cs/drafts/{l3}/judgment", data={"judgment": "정책상 일부 환불"}).status_code == 303
    assert wm.post(f"/cs/drafts/{l3}/approve", data={"confirm": "on"}).status_code == 303
    page = wm.get("/settings/policy").text
    assert "운영자 계정만 바꿀 수 있어요" not in page and 'aria-disabled="true"' not in page
    assert wm.post("/settings/gates/GATE-CAP-01", data={"status": "PASSED", "confirm": "on",
                                                         "evidence": "아버님 확인"}).status_code == 303
