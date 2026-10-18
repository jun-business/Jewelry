"""D-19 브랜드(틴틴골드) — 상호·로고·파비콘이 화면과 고객 견적 문서에 나온다."""
from fastapi.testclient import TestClient
from helpers import order_at

from jewelry_os.services import settings
from jewelry_os.web.app import create_app
from webutil import web


def test_brand_name_logo_favicon(ctx, maker, operator):
    assert settings.get(ctx, "shop_name") == "틴틴골드"
    anon = TestClient(create_app(ctx), base_url="http://127.0.0.1:8770")
    login = anon.get("/login").text
    assert 'src="/static/img/logo.webp" alt="틴틴골드"' in login and "/static/img/favicon.png" in login
    for url, mime in (("/favicon.ico", "image/x-icon"), ("/static/img/logo.webp", "image/webp"),
                      ("/static/img/mark.png", "image/png")):
        r = anon.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith(mime), url
    page = web(ctx, "maker").get("/").text
    assert "/static/img/mark.png" in page and "틴틴골드" in page
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED")
    qid = ctx.db.scalar("SELECT id FROM quotes WHERE order_id=? AND status='APPROVED'", (o["id"],))
    doc = web(ctx, "maker").get(f"/docs/quote/{qid}").text
    assert 'class="doc-logo"' in doc and 'alt="틴틴골드"' in doc
    assert web(ctx, "maker").get(f"/docs/quote/{qid}.txt").text.startswith(("[틴틴골드]", "※ 시험 견적"))
