"""D-43: 아버님 계정 하나로 결제 요청·결제 입력·불일치 해결·잔금·잔금 확인·주문 완료까지(계정 전환 없음)."""
from helpers import get, order_at
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
