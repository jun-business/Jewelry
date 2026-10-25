"""서비스 계층 스모크: 동기화 → 수락 → 견적 → 결제 → … → 완료."""
from conftest import add_response, approve_policies, pass_all_gates

from jewelry_os.domain.states import S
from jewelry_os.services import intake, manufacturing, orders, payments, quotes


def test_full_flow_services(ctx, maker, operator, fclock):
    pass_all_gates(ctx, operator)
    approve_policies(ctx, operator)
    ctx.files.files["f1"] = b"\xff\xd8img1"
    add_response(ctx, "r1", files=["f1"])
    st = intake.sync_once(ctx)
    assert st["ok"] and st["new"] == 1
    intake.fetch_pending_files(ctx)
    o = ctx.db.one("SELECT * FROM orders")
    assert o["lifecycle_state"] == S.OWNER_REVIEW.value
    from jewelry_os.domain import transitions as tr
    tr.perform(ctx, o["id"], "accept", maker, {"confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    quotes.upload_concept(ctx, o, maker, "c1.png", b"\x89PNGc1", "첫 시안", 30)
    qid = quotes.draft(ctx, o, maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(
        items=[{"kind": "metal", "label": "18K 금", "amount": "900,000", "cost": "500000"},
               {"kind": "design", "label": "설계비", "amount": "300000", "cost": "0"}],
        spec={"material": "골드", "purity": "18K", "size": "12호", "stones": "없음", "engraving": "없음",
              "custom_made": "12호 맞춤 사이즈 18K 골드 반지를 이 고객님만을 위해 1점 제작",
                      "loss_reasons": "12호 맞춤 사이즈로 만들어 다른 고객에게 판매하기 어렵고, 상세 CAD 설계와 공장 발주 뒤의 금속·가공 비용은 회수하기 어려워요."},
        tax_included=True, shipping_included=True, promised_date="2026-11-30", valid_until="2026-11-03"))
    o = orders.get(ctx, o["order_no"])
    quotes.approve(ctx, o, maker, {"confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    from jewelry_os.devtools import accept_contract
    accept_contract(ctx, o, operator)  # D-24: Form B 계약 동의가 유효해지면 고객 동의 완료로 자동 반영
    o = orders.get(ctx, o["order_no"])
    assert o["lifecycle_state"] == S.CUSTOMER_ACCEPTED.value
    payments.create_initial(ctx, o, operator, {"confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    r = payments.record_payment(ctx, o, operator, {"naver_order_id": "N1", "naver_product_order_id": "P1",
                                                   "amount": "400000", "naver_status": "PAYED",
                                                   "looked_up_at": "2026-10-20T12:00", "confirm": "on"})
    assert r["result"] == "verified"
    o = orders.get(ctx, o["order_no"])
    assert o["lifecycle_state"] == S.INITIAL_PAYMENT_VERIFIED.value
    tr.perform(ctx, o["id"], "start_cad", maker, {"confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    manufacturing.upload_design(ctx, o, maker, {"cad": ("r.3dm", b"3dm"), "preview": ("p.png", b"png"),
                                                 "stl": ("r.stl", b"solid x")})
    o = orders.get(ctx, o["order_no"])
    manufacturing.record_design_ok(ctx, o, maker, {"channel": "카카오톡", "evidence": "최종 시안 좋아요", "confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    stl = ctx.db.one("SELECT id FROM assets WHERE kind='stl'")["id"]
    manufacturing.save_factory_spec(ctx, o, maker, {"material": "골드", "purity": "18K", "size": "12호", "stones": "없음",
                                                     "engraving": "없음", "finish_na": "on", "special_na": "on",
                                                     "stl_asset_id": stl})
    o = orders.get(ctx, o["order_no"])
    payments.create_balance(ctx, o, operator)  # D-24 Gate 2: 최종 CAD 승인 뒤 잔금 2/3 확인 → 발주
    o = orders.get(ctx, o["order_no"])
    r = payments.record_payment(ctx, o, operator, {"phase": "BALANCE", "naver_order_id": "N2",
                                                   "naver_product_order_id": "P2", "amount": "800000",
                                                   "naver_status": "PAYED", "looked_up_at": "2026-11-10T12:00", "confirm": "on"})
    assert r["result"] == "verified"
    o = orders.get(ctx, o["order_no"])
    manufacturing.build_packet(ctx, o, maker)
    o = orders.get(ctx, o["order_no"])
    manufacturing.send_factory(ctx, o, maker, {"confirm": "on", "expected_at": "2026-11-10"})
    o = orders.get(ctx, o["order_no"])
    manufacturing.receive(ctx, o, maker, {"received_at": "2026-11-09", "actual_cost": "520000", "confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    res = manufacturing.record_qc(ctx, o, maker, {**{k: "pass" for k in ("spec", "purity_engraving", "size", "finish",
                                                                            "setting", "fit")}, "confirm": "on"},
                                  [("qc.jpg", b"\xff\xd8 qc")])  # S-52 검수 사진
    assert res == "PASS"
    assert payments.try_verify(ctx, o["id"]) == "verified"  # 발주 전에 확인한 잔금으로 잔금 확인 단계
    o = orders.get(ctx, o["order_no"])
    assert o["lifecycle_state"] == S.FINAL_PAYMENT_VERIFIED.value
    manufacturing.ship(ctx, o, maker, {"recipient": "김하늘", "method": "택배", "platform_status": "배송중", "confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    manufacturing.close(ctx, o, operator, {"evidence": "배달 완료 조회", "confirm": "on"})
    o = orders.get(ctx, o["order_no"])
    assert o["lifecycle_state"] == S.CLOSED.value
    n = ctx.db.scalar("SELECT COUNT(*) FROM audit_events WHERE order_id=? AND object_type='order' AND prev<>next",
                      (o["id"],))
    assert n >= 15
