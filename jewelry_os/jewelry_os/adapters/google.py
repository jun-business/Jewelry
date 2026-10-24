"""Google Forms·Drive 어댑터(BD-005, SRS-167). 실제 REST(OAuth 설치형 앱) + Mock. 읽기 전용."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

SCOPES = ["https://www.googleapis.com/auth/forms.responses.readonly",
          "https://www.googleapis.com/auth/drive.readonly"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FORMS_BASE = "https://forms.googleapis.com/v1/forms"
DRIVE_BASE = "https://www.googleapis.com/drive/v3/files"


class SourceError(Exception):
    def __init__(self, kind: str, message: str = "", retry_after: float | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind = kind            # AUTH_EXPIRED | FORBIDDEN | RATE_LIMITED | NETWORK | NOT_FOUND
        self.retry_after = retry_after


@dataclass
class Page:
    responses: list[dict]
    next_page_token: str | None = None


# ---------------- OAuth ----------------
class GoogleOAuth:
    """refresh token은 Config 비밀 파일(secrets.env)에만 저장한다."""

    def __init__(self, cfg, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._http = httpx.Client(transport=transport, timeout=20.0)
        self._access: str | None = None

    @property
    def connected(self) -> bool:
        return bool(self.cfg.secret("JOS_GOOGLE_REFRESH_TOKEN"))

    def auth_url(self, redirect_uri: str, state: str) -> str:
        q = {"client_id": self.cfg.secret("JOS_GOOGLE_CLIENT_ID") or "", "redirect_uri": redirect_uri,
             "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
             "state": state}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def exchange(self, code: str, redirect_uri: str) -> None:
        r = self._http.post(TOKEN_URL, data={"code": code, "client_id": self.cfg.secret("JOS_GOOGLE_CLIENT_ID"),
                                             "client_secret": self.cfg.secret("JOS_GOOGLE_CLIENT_SECRET"),
                                             "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        if r.status_code != 200:
            raise SourceError("AUTH_EXPIRED", f"token exchange failed ({r.status_code})")
        data = r.json()
        if data.get("refresh_token"):
            self.cfg.set_secret("JOS_GOOGLE_REFRESH_TOKEN", data["refresh_token"])
        self._access = data.get("access_token")

    def refresh(self) -> str:
        rt = self.cfg.secret("JOS_GOOGLE_REFRESH_TOKEN")
        if not rt:
            raise SourceError("AUTH_EXPIRED", "not connected")
        try:
            r = self._http.post(TOKEN_URL, data={"refresh_token": rt, "client_id": self.cfg.secret("JOS_GOOGLE_CLIENT_ID"),
                                                 "client_secret": self.cfg.secret("JOS_GOOGLE_CLIENT_SECRET"),
                                                 "grant_type": "refresh_token"})
        except httpx.HTTPError as e:
            raise SourceError("NETWORK", type(e).__name__)
        if r.status_code != 200:
            raise SourceError("AUTH_EXPIRED", f"refresh failed ({r.status_code})")
        self._access = r.json()["access_token"]
        return self._access

    def access_token(self) -> str:
        return self._access or self.refresh()


def run_loopback_flow(oauth: GoogleOAuth, open_browser=None, timeout: float = 300) -> None:
    """설치형 앱 흐름: 127.0.0.1 임의 포트로 리디렉션받아 코드 교환(state 검증)."""
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if u.path != "/oauth2cb" or q.get("state") != state:
                result["error"] = "state"
                self.send_response(400)
                self.end_headers()
                self.wfile.write("잘못된 요청이에요(state 불일치).".encode())
                return
            result["code"] = q.get("code")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Google 연결이 끝났어요. 이 창을 닫고 Jewelry OS로 돌아가세요.".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}/oauth2cb"
    url = oauth.auth_url(redirect, state)
    if open_browser:
        open_browser(url)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    t.join(timeout)
    srv.server_close()
    if result.get("error") or not result.get("code"):
        raise SourceError("AUTH_EXPIRED", "oauth flow failed")
    oauth.exchange(result["code"], redirect)


# ---------------- 실제 Forms / Drive ----------------
def _raise_for(r: httpx.Response):
    if r.status_code == 401:
        raise SourceError("AUTH_EXPIRED", "401")
    if r.status_code == 403:
        raise SourceError("FORBIDDEN", "403")
    if r.status_code == 404:
        raise SourceError("NOT_FOUND", "404")
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        raise SourceError("RATE_LIMITED", "429", float(ra) if ra and ra.isdigit() else None)
    if r.status_code >= 500:
        raise SourceError("NETWORK", str(r.status_code))
    if r.status_code != 200:
        raise SourceError("NETWORK", str(r.status_code))


class GoogleForms:
    def __init__(self, oauth: GoogleOAuth, transport: httpx.BaseTransport | None = None):
        self.oauth = oauth
        self._http = httpx.Client(transport=transport, timeout=30.0)

    def _get(self, url, params=None):
        for attempt in (0, 1):
            try:
                r = self._http.get(url, params=params, headers={"Authorization": f"Bearer {self.oauth.access_token()}"})
            except httpx.HTTPError as e:
                raise SourceError("NETWORK", type(e).__name__)
            if r.status_code == 401 and attempt == 0:
                self.oauth.refresh()
                continue
            _raise_for(r)
            return r
        raise SourceError("AUTH_EXPIRED", "401")

    def list_responses(self, form_id: str, page_token: str | None = None, since_rfc3339: str | None = None) -> Page:
        params = {}
        if page_token:
            params["pageToken"] = page_token
        if since_rfc3339:
            params["filter"] = f"timestamp >= {since_rfc3339}"
        data = self._get(f"{FORMS_BASE}/{urllib.parse.quote(form_id)}/responses", params).json()
        return Page(data.get("responses", []), data.get("nextPageToken"))

    def get_form(self, form_id: str) -> dict:
        """forms.get — 질문 ID·제목·선택지·섹션 설명(고지 문구) 원본. drive.readonly 범위로 읽힌다."""
        return self._get(f"{FORMS_BASE}/{urllib.parse.quote(form_id)}").json()


class GoogleDrive:
    def __init__(self, oauth: GoogleOAuth, transport: httpx.BaseTransport | None = None):
        self.forms = GoogleForms(oauth, transport)

    def fetch(self, file_id: str) -> bytes:
        return self.forms._get(f"{DRIVE_BASE}/{urllib.parse.quote(file_id)}", {"alt": "media"}).content


# ---------------- Mock ----------------
@dataclass
class MockForms:
    """JSON 응답 목록 + 오류 주입. 응답 형식은 Forms API responses 리소스와 같다."""
    responses: list[dict] = field(default_factory=list)
    page_size: int = 50
    errors: list[str] = field(default_factory=list)   # 다음 호출들에서 낼 오류 kind
    calls: list[dict] = field(default_factory=list)
    by_form: dict = field(default_factory=dict)       # 다른 폼(Form B 등)의 응답 목록 {form_id: [response]}
    bodies: dict = field(default_factory=dict)        # forms.get 결과 {form_id: body}

    def add(self, response_id: str, answers: dict, submitted: str, last: str | None = None):
        self.responses.append({"responseId": response_id, "createTime": submitted,
                               "lastSubmittedTime": last or submitted, "answers": answers})

    def list_responses(self, form_id, page_token=None, since_rfc3339=None) -> Page:
        self.calls.append({"form_id": form_id, "page_token": page_token, "since": since_rfc3339})
        if self.errors:
            kind = self.errors.pop(0)
            raise SourceError(kind, "mock", 30 if kind == "RATE_LIMITED" else None)
        rows = self.by_form[form_id] if form_id in self.by_form else self.responses
        if since_rfc3339:
            rows = [r for r in rows if r["lastSubmittedTime"] >= since_rfc3339]
        start = int(page_token or 0)
        chunk = rows[start:start + self.page_size]
        nxt = str(start + self.page_size) if start + self.page_size < len(rows) else None
        return Page(json.loads(json.dumps(chunk)), nxt)


    def add_to(self, form_id: str, response: dict):
        """다른 폼에 응답 추가 — Forms API responses 리소스 모양 그대로(respondentEmail 포함 가능)."""
        self.by_form.setdefault(form_id, []).append(response)

    def get_form(self, form_id: str) -> dict:
        self.calls.append({"form_id": form_id, "get_form": True})
        if self.errors:
            kind = self.errors.pop(0)
            raise SourceError(kind, "mock", 30 if kind == "RATE_LIMITED" else None)
        if form_id not in self.bodies:
            raise SourceError("NOT_FOUND", "mock")
        return json.loads(json.dumps(self.bodies[form_id]))


@dataclass
class MockDrive:
    files: dict[str, bytes] = field(default_factory=dict)
    fail: set[str] = field(default_factory=set)

    def fetch(self, file_id: str) -> bytes:
        if file_id in self.fail or file_id not in self.files:
            raise SourceError("FORBIDDEN", "mock")
        return self.files[file_id]


def choice_answer(qid: str, value: str) -> tuple[str, dict]:
    """객관식 답도 Forms API에서는 textAnswers로 온다(선택지 원문)."""
    return text_answer(qid, value)


def text_answer(qid: str, value: str) -> tuple[str, dict]:
    return qid, {"questionId": qid, "textAnswers": {"answers": [{"value": value}]}}


def files_answer(qid: str, files: list[tuple[str, str]], mime="image/jpeg") -> tuple[str, dict]:
    """한 문항에 파일 여러 개(Forms API는 fileUploadAnswers.answers 목록 하나로 준다)."""
    return qid, {"questionId": qid, "fileUploadAnswers": {"answers": [{"fileId": f, "fileName": n, "mimeType": mime}
                                                                         for f, n in files]}}


def file_answer(qid: str, file_id: str, name: str, mime="image/jpeg") -> tuple[str, dict]:
    return qid, {"questionId": qid, "fileUploadAnswers": {"answers": [{"fileId": file_id, "fileName": name,
                                                                         "mimeType": mime}]}}


def sample_jpeg(seed: str = "x") -> bytes:
    """시험용 작은 JPEG 비슷한 바이트(내용 확인용이 아니라 해시·저장 확인용)."""
    return b"\xff\xd8\xff\xe0" + hashlib.sha256(seed.encode()).digest() * 4 + b"\xff\xd9"
