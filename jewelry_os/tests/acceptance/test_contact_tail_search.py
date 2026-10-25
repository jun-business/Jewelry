"""카카오 채널 ‘진행 중인 주문’ 안내(성함 + 연락처 뒷자리 4자리)로 고객·주문 찾기."""
from helpers import new_order

from conftest import approve_policies
from webutil import web


def test_search_by_contact_last4(ctx, operator):
    approve_policies(ctx, operator)
    a = new_order(ctx, "r1", name="김하늘", contact="010-1234-5678")
    b = new_order(ctx, "r2", name="이바다", contact="010-9999-0000")
    w = web(ctx, "operator")
    page = w.get("/customers?q=5678").text
    assert "김하늘" in page and "이바다" not in page
    page = w.get("/orders?q=5678").text
    assert a in page and b not in page
    assert "김하늘" in w.get("/customers?q=하늘").text  # 이름 검색은 그대로
    assert "김하늘" not in w.get("/customers?q=1111").text
