"""D-40 계약 안 된 문의 보관기간(최종 상담일로부터 3개월) 정리 + 계약 안 된 이유 분석 기록."""
from helpers import order_at

from jewelry_os.adapters import google as g
from jewelry_os.services import retention
from webutil import web

PII = ("홍비밀", "7777-8888", "01077778888")


def _no_pii(ctx):
    for row in ctx.db.all("SELECT * FROM lost_inquiries"):
        s = " ".join(str(v) for v in tuple(row))
        assert not any(p in s for p in PII)


def test_old_uncontracted_inquiry_is_recorded_then_erased(ctx, maker, operator, fclock):
    ctx.files.files["i"] = g.sample_jpeg("i")
    o = order_at(ctx, maker, operator, "OWNER_REVIEW", name="홍비밀", contact="010-7777-8888", files=["i"],
                 desc="홍비밀입니다. 18K 꼬임 반지, 연락은 010-7777-8888로 주세요")
    ref = ctx.db.one("SELECT * FROM assets WHERE order_id=? AND kind='reference'", (o["id"],))
    path = ctx.cfg.files_dir / ref["storage_ref"]
    assert path.exists()
    fclock.advance(days=80)
    assert retention.purge(ctx) == 0  # 아직 3개월 안
    fclock.advance(days=15)
    assert retention.purge(ctx) == 1
    cust = ctx.db.one("SELECT * FROM customers WHERE id=?", (o["customer_id"],))
    assert cust["deleted_at"] and cust["contact_enc"] is None and cust["display_name"] == "삭제된 고객"
    assert not path.exists()
    assert ctx.db.one("SELECT storage_ref, fetch_status FROM assets WHERE id=?", (ref["id"],))["fetch_status"] == "PURGED"
    rec = ctx.db.one("SELECT * FROM lost_inquiries WHERE order_id=?", (o["id"],))
    assert rec["reason_code"] == "NOT_REVIEWED" and rec["product_type"] == "반지" and rec["source_channel"]
    assert "제품: 반지" in rec["design_summary"] and "꼬임 반지" in rec["design_summary"]  # 사진 대신 글 설명
    _no_pii(ctx)
    assert ctx.db.one("SELECT 1 FROM audit_events WHERE action='inquiry_purged'")
    assert ctx.db.scalar("SELECT COUNT(*) FROM orders WHERE id=?", (o["id"],)) == 1  # 주문 뼈대는 남음(통계)
    assert retention.purge(ctx) == 0  # 다시 돌려도 그대로
    assert ctx.db.scalar("SELECT COUNT(*) FROM lost_inquiries") == 1
    page = web(ctx, "operator").get("/analytics?test=1").text
    assert "계약 안 된 문의" in page and "검토 전 방치" in page and "꼬임 반지" in page


def test_contracted_or_recent_inquiries_are_kept(ctx, maker, operator, fclock):
    a = order_at(ctx, maker, operator, "CUSTOMER_ACCEPTED", rid="r1", name="계약고객", contact="010-1111-2222")
    fclock.advance(days=200)
    assert retention.candidates(ctx) == []
    assert retention.purge(ctx) == 0
    assert ctx.db.one("SELECT deleted_at FROM customers WHERE id=?", (a["customer_id"],))["deleted_at"] is None
    assert ctx.db.scalar("SELECT COUNT(*) FROM lost_inquiries") == 0


def test_daily_due(ctx, fclock):
    assert retention.due(ctx)
    retention.purge(ctx)
    assert not retention.due(ctx)
    fclock.advance(hours=23)
    assert retention.due(ctx)
