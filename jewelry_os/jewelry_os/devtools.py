"""시험·시연 데이터(JOS_ENV=test|dev 전용). 실제 고객 정보는 쓰지 않는다(NFR-15)."""
from __future__ import annotations

from .adapters import google as g
from .core import security
from .domain import transitions as tr
from .services import intake, manufacturing, orders, payments, quotes, settings

FIRST = ["김하늘", "김하나", "박민수", "이서연", "최지우", "정다은", "한유진", "오세훈", "윤아름", "장미래", "임하준", "서보람"]


def answers(name, desc, product="반지", consent=True, source="스마트스토어", files=None, contact=None):
    a = dict([g.text_answer("q01_display_name", name), g.text_answer("q02_contact_kind", "카카오톡"),
              g.text_answer("q02_contact_value", contact or f"kakao_{abs(hash(name)) % 10000}"),
              g.text_answer("q03_product_type", product), g.text_answer("q11_source_channel", source)])
    if desc:
        a.update([g.text_answer("q04_description", desc)])
    if consent:
        a.update([g.text_answer("q12_consent", "동의합니다")])
    if files:
        a.update([g.files_answer("q10_reference_files", [(f, f"ref{i}.jpg") for i, f in enumerate(files)])])
    return a


def users(ctx):
    m = ctx.db.one("SELECT * FROM users WHERE role='maker'")
    o = ctx.db.one("SELECT * FROM users WHERE role='operator'")
    return (security.User(m["id"], m["username"], m["display_name"], "maker", {}),
            security.User(o["id"], o["username"], o["display_name"], "operator", {}))


def advance(ctx, order_no, target: str, maker, operator, total=1_200_000):
    """주문을 정상 경로로 target 단계까지 진행(시험용)."""
    order = lambda: orders.get(ctx, order_no)  # noqa: E731
    C = {"confirm": "on"}
    steps = ["FREE_CONCEPT", "QUOTE_APPROVED", "CUSTOMER_ACCEPTED", "PAYMENT_PENDING", "INITIAL_PAYMENT_VERIFIED",
             "DETAIL_CAD", "CUSTOMER_DESIGN_APPROVED", "FACTORY_SENT", "FACTORY_RECEIVED", "QC_PASSED",
             "FINAL_PAYMENT_VERIFIED", "SHIPPED", "CLOSED"]
    cur = order()["lifecycle_state"]
    start = steps.index(cur) + 1 if cur in steps else 0
    for st in steps[start: steps.index(target) + 1]:
        o = order()
        if st == "FREE_CONCEPT":
            from .services import capacity
            if capacity.at_capacity(ctx) and not orders.pending_capacity_exception(ctx, o["id"]):
                orders.approve_capacity_exception(ctx, o, operator, "시험 데이터", "2026-12-31")  # H1 r1 Q2 절차 그대로
            orders.accept_order(ctx, order(), maker, {**C, "reason": "시험", "feasible_confirm": "on"})
        elif st == "QUOTE_APPROVED":
            quotes.upload_concept(ctx, o, maker, "c1.png", b"\x89PNG concept " + order_no.encode(), "첫 시안", 30)
            qid = quotes.draft(ctx, order(), maker)
            quotes.save(ctx, qid, maker, quotes.QuoteForm(
                items=[{"kind": "metal", "label": "18K 금", "amount": str(total - 300_000), "cost": str(total // 2)},
                       {"kind": "design", "label": "설계비", "amount": "300000", "cost": "0"}],
                spec={"material": "골드", "purity": "18K", "size": "12호", "stones": "없음", "engraving": "없음"},
                tax_included=True, shipping_included=True, promised_date="2026-12-15", valid_until="2026-11-30"))
            quotes.approve(ctx, order(), maker, C)
        elif st == "CUSTOMER_ACCEPTED":
            quotes.record_acceptance(ctx, o, maker, {**C, "channel": "카카오톡", "evidence": "견적 동의 답장"})
        elif st == "PAYMENT_PENDING":
            payments.create_initial(ctx, o, operator, C)
        elif st == "INITIAL_PAYMENT_VERIFIED":
            r = payments.requests_of(ctx, o["id"])[-1]
            payments.record_payment(ctx, o, operator, {**C, "naver_order_id": f"N-{order_no}",
                                                       "naver_product_order_id": f"P-{order_no}-1",
                                                       "amount": str(r["expected_krw"]), "naver_status": "PAYED",
                                                       "looked_up_at": "2026-10-20T12:00"})
        elif st == "DETAIL_CAD":
            tr.perform(ctx, o["id"], "start_cad", maker, C)
        elif st == "CUSTOMER_DESIGN_APPROVED":
            manufacturing.upload_design(ctx, o, maker, {"cad": ("r.3dm", b"3dm " + order_no.encode()),
                                                         "preview": ("p.png", b"png " + order_no.encode()),
                                                         "stl": ("r.stl", b"solid " + order_no.encode())})
            manufacturing.record_design_ok(ctx, order(), maker, {**C, "channel": "카카오톡", "evidence": "최종 시안 좋아요"})
        elif st == "FACTORY_SENT":
            stl = ctx.db.one("SELECT a.id FROM assets a JOIN orders o ON o.id=a.order_id WHERE o.order_no=? AND "
                             "a.kind='stl' ORDER BY a.id DESC", (order_no,))["id"]
            manufacturing.save_factory_spec(ctx, o, maker, {"material": "골드", "purity": "18K", "size": "12호",
                                                             "stones": "없음", "engraving": "없음", "finish_na": "on",
                                                             "special_na": "on", "stl_asset_id": stl})
            manufacturing.build_packet(ctx, order(), maker)
            manufacturing.send_factory(ctx, order(), maker, {**C, "expected_at": "2026-11-20"})
        elif st == "FACTORY_RECEIVED":
            manufacturing.receive(ctx, o, maker, {**C, "received_at": "2026-11-19", "actual_cost": str(total // 2)})
        elif st == "QC_PASSED":
            manufacturing.record_qc(ctx, o, maker, {**{k: "pass" for k in ("spec", "purity_engraving", "size", "finish",
                                                                            "setting", "fit")}, **C})
        elif st == "FINAL_PAYMENT_VERIFIED":
            if o["payment_policy"] == "FULL":
                payments.verify_final_full(ctx, o, operator, C)
            else:
                payments.create_balance(ctx, o, operator)
                r = payments.requests_of(ctx, o["id"])[-1]
                payments.record_payment(ctx, order(), operator, {**C, "phase": "BALANCE", "naver_order_id": f"N2-{order_no}",
                                                                 "naver_product_order_id": f"P-{order_no}-2",
                                                                 "amount": str(r["expected_krw"]), "naver_status": "PAYED",
                                                                 "looked_up_at": "2026-11-20T12:00"})
        elif st == "SHIPPED":
            manufacturing.ship(ctx, o, maker, {**C, "recipient": "수령인", "method": "택배", "platform_status": "배송중"})
        elif st == "CLOSED":
            manufacturing.close(ctx, o, operator, {**C, "evidence": "배달 완료 조회"})
    return order()


def seed_demo(ctx):
    if not ctx.cfg.env in ("test", "dev"):
        raise PermissionError("시험·개발 환경에서만 쓸 수 있어요")
    maker, operator = users(ctx)
    for p in ctx.db.all("SELECT id FROM policies WHERE status='DRAFT'"):
        settings.approve_policy(ctx, p["id"], operator)
    f = ctx.forms
    ctx.files.files.update({"img1": b"\xff\xd8demo1", "img2": b"\xff\xd8demo2"})
    descs = ["18K 금으로 12호 반지를 만들고 싶어요. 예산 80만원", "커플링 2개, 각인 원해요", "고양이 모양 펜던트 목걸이",
             "티파니 스타일 솔리테어 반지", "", "엄마 생신 선물 귀걸이"]
    for i, n in enumerate(FIRST[:9]):
        f.add(f"demo-{i}", answers(n, descs[i % len(descs)], ["반지", "목걸이", "귀걸이"][i % 3],
                                    consent=(i != 4), files=["img1", "img2"] if i == 0 else None),
              f"2026-10-1{i % 9}T0{i}:00:00Z")
    intake.sync_once(ctx)
    intake.fetch_pending_files(ctx)
    nos = [r["order_no"] for r in ctx.db.all("SELECT order_no FROM orders ORDER BY id")]
    targets = ["QUOTE_APPROVED", "PAYMENT_PENDING", "DETAIL_CAD", "FACTORY_SENT", None, "SHIPPED", "CLOSED"]
    for no, t in zip(nos, targets):
        if t:
            advance(ctx, no, t, maker, operator)
    return nos
