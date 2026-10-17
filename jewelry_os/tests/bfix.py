"""브라우저 시험(Playwright headless msedge) 공용 — 시험마다 새 데이터 폴더 + 실서버(uvicorn) 스레드."""
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from jewelry_os.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
AXE = ROOT / "tools" / "node_modules" / "axe-core" / "axe.min.js"
SHOTS = ROOT / "reports" / "screens"
CREDS = {"maker": ("father", "maker-pass-123"), "operator": ("ops", "oper-pass-1234")}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def live(ctx):
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        yield b
        b.close()


def login(page, base, role="maker", next_url="/"):
    u, pw = CREDS[role]
    page.goto(f"{base}/login?next={next_url}")
    page.fill("#u", u)
    page.fill("#p", pw)
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")


@pytest.fixture
def new_page(browser):
    ctxs = []

    def make(width=1280, height=900, **kw):
        c = browser.new_context(viewport={"width": width, "height": height}, locale="ko-KR", **kw)
        ctxs.append(c)
        return c.new_page()
    yield make
    for c in ctxs:
        c.close()
