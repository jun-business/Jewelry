"""웹 시험 도우미: TestClient + 로그인 + CSRF."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from jewelry_os.web.app import create_app
from conftest import MAKER_PW, OPER_PW

CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')


class Web:
    def __init__(self, ctx):
        self.ctx = ctx
        self.app = create_app(ctx)
        self.c = TestClient(self.app, base_url="http://127.0.0.1:8770", follow_redirects=False)
        self.csrf = None

    def login(self, role="maker"):
        u, p = ("father", MAKER_PW) if role == "maker" else ("ops", OPER_PW)
        r = self.c.post("/login", data={"username": u, "password": p, "next": "/"})
        assert r.status_code == 303, r.text
        self.c.cookies.set("jos_session", r.cookies.get("jos_session"))
        page = self.c.get("/")
        m = CSRF_RE.search(page.text)
        self.csrf = m.group(1) if m else None
        return self

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, files=None, **kw):
        d = dict(data or {})
        d.setdefault("csrf", self.csrf)
        return self.c.post(url, data=d, files=files, **kw)

    def api(self, method, url, json=None):
        return self.c.request(method, url, json=json, headers={"X-CSRF-Token": self.csrf or ""})


def web(ctx, role="maker") -> Web:
    return Web(ctx).login(role)
