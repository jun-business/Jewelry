"""시험·시연 데이터(JOS_ENV=test|dev 전용). 실제 고객 정보는 쓰지 않는다(NFR-15)."""
from __future__ import annotations

from .adapters import google as g
from .core import security
from .domain import transitions as tr
from .core import clock
from .services import contracts, intake, manufacturing, orders, payments, quotes, settings

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


def form_b_response(ctx, req_id, *, response_id=None, email="customer@example.com", order_no=None, quote_ref=None,
                    code=None, consents=None, submitted=None, drop=()) -> dict:
    """Form B 응답 1건을 Mock 폼에 넣는다(Forms API responses 리소스 모양). 기본값 = 사전입력 그대로 + 전부 동의."""
    req = contracts.get(ctx, req_id)
    o = ctx.db.one("SELECT order_no FROM orders WHERE id=?", (req["order_id"],))
    m = contracts.mapping()
    vals = {m["prefill"]["order_no"]["question_id"]: order_no if order_no is not None else o["order_no"],
            m["prefill"]["quote_ref"]["question_id"]: quote_ref if quote_ref is not None else req["quote_ref"],
            m["prefill"]["code"]["question_id"]: code if code is not None else contracts.code_of(ctx, req)}
    for cdef in m["consents"]:
        vals[cdef["question_id"]] = (consents or {}).get(cdef["key"], cdef["accept"])
    at = submitted or clock.now_iso().replace("+00:00", "Z")
    r = {"formId": req["form_id"], "responseId": response_id or f"ACYfb{req_id}x{len(ctx.forms.by_form.get(req['form_id'], []))}",
         "createTime": at, "lastSubmittedTime": at,
         "answers": dict(g.text_answer(q, v) for q, v in vals.items() if q not in drop and v is not None)}
    if email:
        r["respondentEmail"] = email
    ctx.forms.add_to(req["form_id"], r)
    return r


def accept_contract(ctx, o, operator, email=None) -> int:
    """시험·시연: 계약 링크 발행 → 고객 Form B 제출 → 수집 → 운영자 본인 확인(D-24)."""
    rid = contracts.issue(ctx, o, operator)
    form_b_response(ctx, rid, email=email or f"customer{o['customer_id']}@example.com")
    contracts.sync_once(ctx, sleep=lambda s: None)
    if contracts.get(ctx, rid)["status"] == "REVIEW_REQUIRED":
        contracts.review(ctx, rid, operator, "consult_channel", "시험: 카카오톡 상담방에서 고객 본인 제출 확인")
    return rid


def pay_balance(ctx, order_no, operator):
    o = orders.get(ctx, order_no)
    payments.create_balance(ctx, o, operator)
    r = payments.requests_of(ctx, o["id"])[-1]
    payments.record_payment(ctx, orders.get(ctx, order_no), operator, {
        "confirm": "on", "phase": "BALANCE", "naver_order_id": f"N2-{order_no}", "naver_product_order_id": f"P-{order_no}-2",
        "amount": str(r["expected_krw"]), "naver_status": "PAYED", "looked_up_at": "2026-11-20T12:00"})


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
                spec={"material": "골드", "purity": "18K", "size": "12호", "stones": "없음", "engraving": "없음",
                      "custom_made": "12호 맞춤 사이즈 18K 골드 반지를 이 고객님만을 위해 1점 제작",
                      "loss_reasons": "12호 맞춤 사이즈로 만들어 다른 고객에게 판매하기 어렵고, 상세 CAD 설계와 공장 발주 뒤의 금속·가공 비용은 회수하기 어려워요."},
                tax_included=True, shipping_included=True, promised_date="2026-12-15", valid_until="2026-11-30"))
            quotes.approve(ctx, order(), maker, C)
        elif st == "CUSTOMER_ACCEPTED":
            accept_contract(ctx, o, operator)  # D-24: 고객 동의 = Form B 계약 동의(자동 반영)
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
            if o["payment_policy"] != "FULL" and tr.payment_request(ctx.db.conn, o["id"], ("BALANCE",)) is None:
                pay_balance(ctx, order_no, operator)  # D-24 Gate 2: 잔금 2/3 확인 후 발주
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
            elif tr.payment_request(ctx.db.conn, o["id"], ("BALANCE",)) is not None:
                payments.try_verify(ctx, o["id"])  # 발주 전에 확인한 잔금으로 잔금 확인 단계 통과
            else:
                pay_balance(ctx, order_no, operator)
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
