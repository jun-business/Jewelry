"""개발용 앱은 다른 주소(8771)에서 돌고 모든 화면 위에 띠가 뜬다 — 운영 앱(8770)과 헷갈리지 않게."""
from fastapi.testclient import TestClient

from jewelry_os.core.config import load_config
from jewelry_os.web.app import create_app


def test_dev_and_prod_ports_differ(tmp_path, monkeypatch):
    monkeypatch.delenv("JOS_PORT", raising=False)
    assert load_config("dev", tmp_path / "d").port == 8771
    assert load_config("prod", tmp_path / "p").port == 8770


def test_banner_only_in_dev(ctx):
    def login_page():
        return TestClient(create_app(ctx), base_url="http://127.0.0.1:8770").get("/login").text
    assert "dev-banner" not in login_page()
    ctx.cfg.env = "dev"
    try:
        assert 'class="dev-banner"' in login_page()
    finally:
        ctx.cfg.env = "test"
        create_app(ctx)
