"""채널톡 Open API 어댑터(카카오 상담톡 연동 준비, `docs/카카오_상담_자동연동_조사.md`).

자택 PC가 채널톡에 *나가서* 묻는 방식(폴링) — 외부 접속을 열지 않는다. 키는 PC의 secrets.env에만.
Base `https://api.channel.io/open/v5`, 인증 헤더 `x-access-key`·`x-access-secret`(Bearer 미지원)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

BASE = "https://api.channel.io/open/v5"
KEY_NAMES = ("JOS_CHANNELTALK_ACCESS_KEY", "JOS_CHANNELTALK_ACCESS_SECRET")


class ChannelTalkError(Exception):
    def __init__(self, kind: str, message: str = ""):
        super().__init__(f"{kind}: {message}")
        self.kind = kind  # NOT_CONFIGURED | AUTH | FORBIDDEN | RATE_LIMITED | NETWORK | NOT_FOUND


def configured(cfg) -> bool:
    return all(cfg.secret(k) for k in KEY_NAMES)


class ChannelTalk:
    def __init__(self, cfg, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.cfg = cfg
        self._http = httpx.Client(base_url=BASE, transport=transport, timeout=20.0)
        self._sleep = sleep

    def _headers(self):
        key, secret = (self.cfg.secret(k) for k in KEY_NAMES)
        if not (key and secret):
            raise ChannelTalkError("NOT_CONFIGURED", "keys missing")
        return {"x-access-key": key, "x-access-secret": secret, "Content-Type": "application/json"}

    def _req(self, method, path, params=None, json=None):
        for attempt in range(3):
            try:
                r = self._http.request(method, path, params=params, json=json, headers=self._headers())
            except httpx.HTTPError as e:
                if method == "GET" and attempt < 2:
                    self._sleep(0.2 * 2 ** attempt)
                    continue
                raise ChannelTalkError("NETWORK", type(e).__name__)
            if r.status_code == 429 and attempt < 2:
                ra = r.headers.get("Retry-After")
                self._sleep(min(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 1.0, 10))
                continue
            if r.status_code >= 500 and method == "GET" and attempt < 2:
                self._sleep(0.2 * 2 ** attempt)
                continue
            kind = {401: "AUTH", 403: "FORBIDDEN", 404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(r.status_code)
            if kind or r.status_code >= 400:
                raise ChannelTalkError(kind or "NETWORK", str(r.status_code))
            return r.json() if r.content else {}
        raise ChannelTalkError("NETWORK", "retries exhausted")

    # ---- 읽기 ----
    def channel(self) -> dict:
        return self._req("GET", "/channel").get("channel") or {}

    def user_chats(self, state: str = "opened", limit: int = 25, since=None, sort_order: str = "desc") -> dict:
        p = {"state": state, "limit": limit, "sortOrder": sort_order}
        if since:
            p["since"] = since
        return self._req("GET", "/user-chats", p)

    def messages(self, chat_id: str, limit: int = 25, since=None, sort_order: str = "desc") -> dict:
        p = {"limit": limit, "sortOrder": sort_order}
        if since:
            p["since"] = since
        return self._req("GET", f"/user-chats/{chat_id}/messages", p)


@dataclass
class MockChannelTalk:
    """시험용 — 같은 응답 모양(channel / userChats / messages)."""
    channel_info: dict = field(default_factory=lambda: {"id": "ch1", "name": "틴틴골드"})
    chats: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def _err(self):
        if self.errors:
            raise ChannelTalkError(self.errors.pop(0), "mock")

    def channel(self):
        self._err()
        return self.channel_info

    def user_chats(self, state="opened", limit=25, since=None, sort_order="desc"):
        self._err()
        return {"userChats": [c for c in self.chats if state in (None, c.get("state"))][:limit]}

    def messages(self, chat_id, limit=25, since=None, sort_order="desc"):
        self._err()
        return {"messages": []}


def check(client) -> dict:
    """연결 확인(읽기만): 채널 이름과 열린 상담 수. 메시지를 보내지 않는다."""
    try:
        ch = client.channel()
        chats = client.user_chats(state="opened", limit=25).get("userChats") or []
    except ChannelTalkError as e:
        return {"ok": False, "error": e.kind}
    return {"ok": True, "channel": ch.get("name") or ch.get("id") or "-", "opened": len(chats)}


ERROR_TEXT = {"NOT_CONFIGURED": "채널톡 API 키가 아직 없어요.", "AUTH": "API 키가 맞지 않아요(Access Key·Secret 확인).",
              "FORBIDDEN": "이 키로는 권한이 없어요(요금제·키 권한 확인).", "RATE_LIMITED": "요청이 많아 잠시 막혔어요.",
              "NETWORK": "채널톡에 연결하지 못했어요(인터넷 확인).", "NOT_FOUND": "채널톡 주소를 찾지 못했어요."}
