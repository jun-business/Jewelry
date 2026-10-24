"""D-26 이름·전화번호가 같으면 같은 고객으로 자동 연결 — Form A 접수·수기 접수·기존 중복 정리·삭제 범위."""
from helpers import get, new_order

from jewelry_os.bootstrap import build_ctx
from jewelry_os.services import customers, intake
from webutil import web


def _cust(ctx, no):
    return get(ctx, no)["customer_id"]


def test_same_name_and_phone_links_automatically(ctx, maker, operator):
    a = new_order(ctx, "r1", name="김하늘", contact="010-1234-5678")
    b = new_order(ctx, "r2", name="김 하늘", contact="+82 10 1234 5678")  # 띄어쓰기·국가번호가 달라도 같은 사람
    c = new_order(ctx, "r3", name="김하늘", contact="010-9999-0000")      # 전화번호 다름 → 다른 고객
    d = new_order(ctx, "r4", name="김하나", contact="010-1234-5678")      # 이름 다름 → 다른 고객
    assert _cust(ctx, a) == _cust(ctx, b)
    assert len({_cust(ctx, a), _cust(ctx, c), _cust(ctx, d)}) == 3
    merged = ctx.db.one("SELECT * FROM customers WHERE merged_into=?", (_cust(ctx, a),))
    assert merged is not None  # 새 고객 기록은 남기고 대표 고객으로 연결
    ev = ctx.db.one("SELECT * FROM audit_events WHERE action='customer_linked'")
    assert ev["actor_role"] == "system" and ev["reason"] == customers.AUTO_LINK_REASON
    page = web(ctx, "operator").get(f"/customers/{_cust(ctx, a)}").text
    assert a in page and b in page and "주문 2건" in page


def test_kakao_id_contact_is_not_auto_linked(ctx, maker, operator):
    a = new_order(ctx, "r1", name="김하늘", contact="kakao_sky")
    b = new_order(ctx, "r2", name="김하늘", contact="kakao_sky")
    assert _cust(ctx, a) != _cust(ctx, b)  # 전화번호가 아니면 운영자가 직접 연결


def test_manual_intake_also_links(ctx, maker, operator):
    a = new_order(ctx, "r1", name="박민수", contact="010-3333-4444")
    no = intake.manual_intake(ctx, operator, {"display_name": "박민수", "contact_kind": "phone",
                                              "contact_value": "01033334444", "product_type": "반지",
                                              "description": "전화로 주문", "consent_channel": "전화",
                                              "consent_evidence": "통화 녹취 동의", "consent": "on"})
    assert _cust(ctx, no) == _cust(ctx, a)


def test_existing_duplicates_linked_once_at_startup(ctx, maker, operator):
    a = new_order(ctx, "r1", name="이서연", contact="010-5555-6666")
    b = new_order(ctx, "r2", name="이서연", contact="010-5555-6666")
    with ctx.db.tx() as c:  # 이 기능 전에 따로 생긴 중복 고객을 흉내 낸다
        c.execute("UPDATE customers SET merged_into=NULL")
        cid_b = c.execute("SELECT id FROM customers ORDER BY id DESC LIMIT 1").fetchone()["id"]
        c.execute("UPDATE orders SET customer_id=? WHERE order_no=?", (cid_b, b))
        c.execute("DELETE FROM meta WHERE key='auto_link_v1'")
    assert _cust(ctx, a) != _cust(ctx, b)
    ctx2 = build_ctx(ctx.cfg, forms=ctx.forms, files=ctx.files, analyzer=ctx.analyzer)
    assert _cust(ctx2, a) == _cust(ctx2, b) == min(_cust(ctx2, a), cid_b)  # 오래된 고객 쪽으로
    assert ctx2.db.scalar("SELECT value FROM meta WHERE key='auto_link_v1'")
    ctx2.db.close()


def test_erase_covers_linked_records(ctx, maker, operator):
    a = new_order(ctx, "r1", name="홍비밀", contact="010-7777-8888")
    new_order(ctx, "r2", name="홍비밀", contact="010-7777-8888")
    cid = _cust(ctx, a)
    assert web(ctx, "operator").post(f"/customers/{cid}/erase", data={"reason": "삭제 요청"}).status_code == 303
    for r in ctx.db.all("SELECT * FROM customers"):
        assert r["deleted_at"] and r["contact_enc"] is None and r["display_name"] == "삭제된 고객"
    assert customers.same_person(ctx, ctx.db.conn, cid) is None  # 삭제된 고객은 자동 연결 대상 아님
