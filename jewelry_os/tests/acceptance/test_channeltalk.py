"""D-28 채널톡(카카오 상담톡) API 키 저장·연결 확인(읽기만) — 로컬 Mock 시험."""
import httpx

from jewelry_os.adapters import channeltalk as ct
from jewelry_os.cli import main as cli_main
from webutil import web

KEY, SECRET = "ak-test-1234567890", "as-test-abcdefghij"


def test_keys_saved_from_settings_never_shown(ctx, maker, operator):
    ctx.extra["channeltalk"] = ct.MockChannelTalk(chats=[{"id": "u1", "state": "opened"}])
    w = web(ctx, "operator")
    page = w.get("/settings/google").text
    assert "카카오 상담(채널톡)" in page and 'type="password"' in page
    assert web(ctx, "maker").post("/settings/channeltalk/keys", data={"access_key": KEY, "access_secret": SECRET}).status_code == 403
    assert w.post("/settings/channeltalk/keys", data={"access_key": KEY, "access_secret": ""}).status_code == 303
    assert not ct.configured(ctx.cfg)  # 둘 다 있어야 저장
    r = w.post("/settings/channeltalk/keys", data={"access_key": KEY, "access_secret": SECRET})
    assert r.status_code == 303 and ct.configured(ctx.cfg)
    page = w.get("/settings/google").text
    assert "연결됨" in page and "틴틴골드" in page and "열린 상담 1건" in page
    assert KEY not in page and SECRET not in page  # 값은 화면에 다시 나오지 않음
    secrets_file = ctx.cfg.secrets_path.read_text(encoding="utf-8")
    assert f"JOS_CHANNELTALK_ACCESS_KEY={KEY}" in secrets_file
    ev = ctx.db.one("SELECT * FROM audit_events WHERE action='channeltalk_keys_saved'")
    assert ev and KEY not in str(dict(ev)) and SECRET not in str(dict(ev))


def test_check_reports_auth_error(ctx, operator):
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_KEY", KEY)
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_SECRET", SECRET)
    ctx.extra["channeltalk"] = ct.MockChannelTalk(errors=["AUTH"])
    w = web(ctx, "operator")
    assert w.post("/settings/channeltalk/check", data={}).status_code == 303
    assert "API 키가 맞지 않아요" in w.get("/settings/google").text


def test_adapter_sends_documented_headers_and_reads_only(ctx):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/open/v5/channel":
            return httpx.Response(200, json={"channel": {"id": "c1", "name": "틴틴골드"}})
        if req.url.path == "/open/v5/user-chats":
            return httpx.Response(200, json={"userChats": [{"id": "u1"}, {"id": "u2"}]})
        return httpx.Response(404)
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_KEY", KEY)
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_SECRET", SECRET)
    st = ct.check(ct.ChannelTalk(ctx.cfg, httpx.MockTransport(handler), sleep=lambda s: None))
    assert st == {"ok": True, "channel": "틴틴골드", "opened": 2}
    assert all(r.method == "GET" for r in seen)  # 연결 확인은 읽기만
    assert seen[0].headers["x-access-key"] == KEY and seen[0].headers["x-access-secret"] == SECRET
    assert "authorization" not in seen[0].headers  # Bearer 미지원
    assert dict(seen[1].url.params) == {"state": "opened", "limit": "25", "sortOrder": "desc"}


def test_adapter_retries_rate_limit_then_reports(ctx):
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"})
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_KEY", KEY)
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_SECRET", SECRET)
    assert ct.check(ct.ChannelTalk(ctx.cfg, httpx.MockTransport(handler), sleep=lambda s: None)) == \
        {"ok": False, "error": "RATE_LIMITED"}
    assert n["calls"] == 3
    assert ct.check(ct.ChannelTalk(type("C", (), {"secret": lambda self, k: None})())) == \
        {"ok": False, "error": "NOT_CONFIGURED"}


def test_key_file_carries_channeltalk_keys(ctx, tmp_path, capsys):
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_KEY", KEY)
    ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_SECRET", SECRET)
    out = tmp_path / "keys.txt"
    assert cli_main(["--env", "test", "--data-dir", str(ctx.cfg.data_dir), "export-keys", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert f"JOS_CHANNELTALK_ACCESS_KEY={KEY}" in text and f"JOS_CHANNELTALK_ACCESS_SECRET={SECRET}" in text
