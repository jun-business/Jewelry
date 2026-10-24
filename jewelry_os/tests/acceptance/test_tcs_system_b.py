"""TC-S-037~053 시스템 시험(AGENT). 브라우저(041·042·051)는 tests/browser, HUMAN(045·047)은 TR 부록."""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import C, check, codes, get, new_order, order_at

from conftest import add_response, answers, approve_policies, pass_all_gates
from jewelry_os.adapters import google as g
from jewelry_os.core import clock
from jewelry_os.core.config import load_config
from jewelry_os.devtools import advance
from jewelry_os.domain import states as st_mod
from jewelry_os.domain import transitions as tr
from jewelry_os.domain.guards import MESSAGES, WARNINGS
from jewelry_os.domain.states import LABEL, PHASE_LABEL, PHASE_OF, S
from jewelry_os.services import capacity, cs, intake, manufacturing, metrics, orders, quotes, settings
from jewelry_os.web import filters
from jewelry_os.web.app import create_app
from webutil import web

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "jewelry_os"
QC_PASS = {k: "pass" for k in ("spec", "purity_engraving", "size", "finish", "setting", "fit")}
SPEC = {"material": "골드", "purity": "18K", "size": "12호", "stones": "없음", "engraving": "없음",
        "custom_made": "1점 맞춤 제작", "loss_reasons": "맞춤 호수로 재판매 불가"}  # D-25 견적서 고지


def act(w, no, code, **data):
    return w.post(f"/orders/{no}/actions/{code}", data={"confirm": "on", **data})


# ---------------- 037 §C 고정 데이터 ----------------
def _quote(ctx, maker, no, total, tax, variable, promised):
    o = get(ctx, no)
    quotes.upload_concept(ctx, o, maker, "c.png", b"\x89PNG " + no.encode(), "", 30)
    qid = quotes.draft(ctx, get(ctx, no), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(items=[{"kind": "metal", "label": "제작", "amount": str(total)}], spec=SPEC,
                                                  tax_included=tax, shipping_included=True, promised_date=promised,
                                                  valid_until="2026-11-30", variable_cost=variable))
    quotes.approve(ctx, get(ctx, no), maker, C)


def _work(ctx, maker, no, minutes):
    if minutes:
        orders.log_work(ctx, get(ctx, no), maker, "cad", minutes, clock.today_kst().isoformat())


def build_section_c(ctx, maker, operator, tax_type="general"):
    pass_all_gates(ctx, operator)
    settings.put(ctx, "tax_type", tax_type, operator)
    approve_policies(ctx, operator)
    w = web(ctx, "maker")
    # A: ad · 완료 · 세금 포함 1,200,000 · 실원가 600,000 · 변동비 50,000 · 300분 · 약속일 준수
    a = new_order(ctx, "A", source="광고를 보고")
    advance(ctx, a, "FREE_CONCEPT", maker, operator)
    _quote(ctx, maker, a, 1_200_000, True, 50_000, "2026-12-15")
    advance(ctx, a, "CLOSED", maker, operator)  # 수령 실원가 = 총액 ÷ 2 = 600,000
    _work(ctx, maker, a, 270)
    # B: instagram · 완료 · 세금 별도 800,000 · 실원가 420,000 · 변동비 30,000 · 240분 · 약속일 미준수 · 재작업 1
    b = new_order(ctx, "B", source="인스타그램")
    advance(ctx, b, "FREE_CONCEPT", maker, operator)
    _quote(ctx, maker, b, 800_000, False, 30_000, "2026-10-19")
    advance(ctx, b, "FACTORY_SENT", maker, operator)
    manufacturing.receive(ctx, get(ctx, b), maker, {**C, "received_at": "2026-10-20", "actual_cost": "420000"})
    manufacturing.record_qc(ctx, get(ctx, b), maker, {**QC_PASS, "finish": "fail", **C})
    iid = orders.open_issue_action(ctx, get(ctx, b), maker, "REWORK", "광택 재작업", "DEFECT")
    orders.resolve_issue(ctx, iid, maker, "재연마 완료")
    advance(ctx, b, "CLOSED", maker, operator)
    _work(ctx, maker, b, 210)
    # C: ad · 결제 확인 후 제작 중 · 1,000,000 · 120분
    c_ = new_order(ctx, "C", source="광고를 보고")
    advance(ctx, c_, "FREE_CONCEPT", maker, operator)
    _quote(ctx, maker, c_, 1_000_000, True, 0, "2026-12-20")
    advance(ctx, c_, "DETAIL_CAD", maker, operator)
    _work(ctx, maker, c_, 90)
    # D: ad · 거절(부적합) · 30분 / E: referral · 거절(일정 불가) · 20분 / F: 미응답 · 검토 중
    d = new_order(ctx, "D", source="광고를 보고")
    _work(ctx, maker, d, 30)
    assert act(w, d, "decline", reason="부적합").status_code == 409  # 분류 없는 거절 409
    assert act(w, d, "decline", category="UNFIT", reason="부적합").status_code == 303
    e = new_order(ctx, "E", source="지인 소개")
    _work(ctx, maker, e, 20)
    act(w, e, "decline", category="NO_CAPACITY", reason="일정")
    fa = answers()
    fa.pop("q11_source_channel")
    ctx.forms.add("F", fa, "2026-10-20T01:00:00Z")
    intake.sync_once(ctx)
    metrics.set_ad_spend(ctx, operator, clock.kst(clock.now()).strftime("%Y-%m"), "ad", 100_000)
    return {"A": a, "B": b, "C": c_, "D": d, "E": e}


def test_tcs_037_section_c(ctx, maker, operator):
    build_section_c(ctx, maker, operator)
    _, summary = metrics.sources(ctx)
    assert summary == {"광고": 3, "직접": 2, "미분류": 1}
    k = metrics.kpis(ctx)
    assert (k["KPI-01"]["num"], k["KPI-01"]["den"], k["KPI-01"]["pct"]) == (6, 6, 100)
    assert k["KPI-02"]["num"] == 0 and k["KPI-03"]["num"] == 2
    assert (k["KPI-04"]["num"], k["KPI-04"]["den"], k["KPI-04"]["pct"], k["KPI-04"]["undecided"]) == (4, 6, 67, 1)
    assert (k["KPI-05"]["num"], k["KPI-05"]["den"], k["KPI-05"]["pct"], k["KPI-05"]["small"]) == (3, 3, 100, True)
    assert k["KPI-06"]["contribution"] == 757_576 and k["KPI-06"]["minutes"] == 540
    assert k["KPI-06"]["value"] == 84_175 and k["KPI-06"]["small"]
    assert (k["KPI-07"]["num"], k["KPI-07"]["den"], k["KPI-07"]["pct"]) == (1, 2, 50)
    assert (k["KPI-08"]["num"], k["KPI-08"]["den"], k["KPI-08"]["pct"]) == (1, 3, 33)
    page = web(ctx, "operator").get("/analytics").text
    for s in ("84,175원", "67%", "미판정 1건", "100%", "50%", "33%", "표본 부족"):
        assert s in page, s


# ---------------- 038 정책 ----------------
def test_tcs_038_policies(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    with ctx.db.tx() as c:  # 상담 정책 승인본 없음 상태
        c.execute("UPDATE policies SET status='RETIRED' WHERE kind='cs'")
    did = cs.create_draft(ctx, get(ctx, o["order_no"]), operator, "진행 상황 알려주세요")
    page = web(ctx, "operator").get(f"/orders/{o['order_no']}/cs").text
    assert WARNINGS["W-NO-POLICY"] in page
    pid = settings.create_policy(ctx, "cs", "상담 정책", "본문", operator)
    settings.approve_policy(ctx, pid, operator)
    d2 = cs.draft(ctx, cs.create_draft(ctx, get(ctx, o["order_no"]), operator, "진행 상황 알려주세요"))
    assert d2["policy_id"] == pid and d2["policy_version"]
    q = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='APPROVED'", (o["id"],))
    tv = q["terms_version"]
    t2 = settings.create_policy(ctx, "terms", "약관·환불 정책", "v2", operator)
    settings.approve_policy(ctx, t2, operator)
    assert ctx.db.scalar("SELECT terms_version FROM quotes WHERE id=?", (q["id"],)) == tv


# ---------------- 039 작업시간·내부 예상일 ----------------
def test_tcs_039_work_and_eta(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "DETAIL_CAD")
    w = web(ctx, "maker")
    for kind, m in (("cad", "45"), ("cs", "15")):
        act(w, o["order_no"], "log_work", kind=kind, minutes=m)
    ws = orders.work_summary(ctx, o["id"])
    assert ws["_total"] == 30 + 45 + 15 == sum(v for k, v in ws.items() if k != "_total")
    eta = capacity.internal_eta(ctx)
    slot = capacity.suggest_cad_slots(ctx, 1)[0]
    assert slot.weekday() >= 5 and eta == slot + timedelta(days=14)
    n = new_order(ctx, "x")
    assert get(ctx, n)["promised_date"] is None  # 약속일 자동 0


# ---------------- 040 UI 최소 구성 ----------------
def test_tcs_040_ui_minimum(ctx, maker, operator):
    ctx.files.files["i"] = g.sample_jpeg("i")
    o = order_at(ctx, maker, operator, "OWNER_REVIEW", files=["i"])
    no = o["order_no"]
    wm, wo = web(ctx, "maker"), web(ctx, "operator")
    checks = {
        "/": ["검토할 새 의뢰", "고객 답을 기다리는", "동기화", "백업", "결제"],           # UI-01 신규·정보부족·수락 대기·경고
        f"/orders/{no}": ["참고 이미지", "AI 정리", "불명확한 점", "위험", "수락", "거절"],  # UI-02
    }
    for url, words in checks.items():
        t = wm.get(url).text
        for wd in words:
            assert wd in t, (url, wd)
    o = advance(ctx, no, "QUOTE_APPROVED", maker, operator)
    t = wm.get(f"/orders/{no}/quote").text  # UI-03
    for wd in ("시안", "v1", "품목", "원가", "고객 안내 문서", "승인"):
        assert wd in t, wd
    cs.create_draft(ctx, get(ctx, no), operator, "언제 받을 수 있나요?")
    t = wo.get(f"/orders/{no}/cs").text  # UI-04
    for wd in ("질문", "초안", "정책", "승인", "복사", "기록"):
        assert wd in t, wd
    o = advance(ctx, no, "PAYMENT_PENDING", maker, operator)
    t = wo.get(f"/orders/{no}/payment").text  # UI-05
    for wd in ("예약금", "400,000원", "상품주문번호", "조회한 시각"):
        assert wd in t, wd
    assert "결제" in wo.get("/payments").text
    o = advance(ctx, no, "FACTORY_SENT", maker, operator)
    t = wm.get(f"/orders/{no}/making").text  # UI-06
    for wd in ("디자인", "v1", "STL", "사양서", "패킷", "발주"):
        assert wd in t, wd
    o = advance(ctx, no, "QC_PASSED", maker, operator)
    t = wm.get(f"/orders/{no}/qc").text  # UI-07 수령/검수·실원가·잔금
    for wd in ("검수표", "실제 비용", "잔금"):
        assert wd in t, wd
    assert "완제품 수령" in wm.get(f"/orders/{no}/history").text
    advance(ctx, no, "FINAL_PAYMENT_VERIFIED", maker, operator)
    t = wm.get(f"/orders/{no}/qc").text  # 출고
    for wd in ("배송", "발송 기록", "네이버 판매 상태"):
        assert wd in t, wd
    for sec, words in (("policy", ["동시 진행"]), ("calendar", ["휴무일"]), ("ai", ["AI"]), ("policies", ["정책"]),
                       ("google", ["Google", "동기화"])):  # UI-08
        t = wo.get(f"/settings/{sec}").text
        for wd in words:
            assert wd in t, (sec, wd)
    t = wo.get("/analytics").text  # UI-09
    for wd in ("유입", "전환", "작업시간", "공헌이익", "납기"):
        assert wd in t, wd


# ---------------- 043 라벨·영문 코드·매핑·형식 ----------------
def test_tcs_043_labels(ctx, maker, operator):
    assert set(LABEL) == set(S) and all(LABEL[s] for s in S)
    assert set(st_mod.HOLD_LABEL) == {"NEEDS_INFO", "WAITLISTED", "HOLD", "NO_CONSENT"}
    assert set(st_mod.ISSUE_LABEL) == {"PAYMENT_MISMATCH", "REFUND_REVIEW", "DISPUTED", "DELIVERY_DELAY", "CHANGE_REQUEST",
                                      "CANCEL_REQUEST", "REWORK"}
    exp015 = {"접수": {"INQUIRY_RECEIVED", "AI_BRIEF_READY", "OWNER_REVIEW"},
              "시안·견적": {"FREE_CONCEPT", "QUOTE_APPROVED", "CUSTOMER_ACCEPTED"},
              "결제·제작준비": {"PAYMENT_PENDING", "INITIAL_PAYMENT_VERIFIED", "DETAIL_CAD", "CUSTOMER_DESIGN_APPROVED"},
              "제작 중": {"FACTORY_SENT", "FACTORY_RECEIVED"},
              "배송·완료": {"QC_PASSED", "FINAL_PAYMENT_VERIFIED", "SHIPPED", "CLOSED"}, "진행 종료": {"DECLINED", "CANCELLED"}}
    got: dict = {}
    for s in S:
        got.setdefault(PHASE_LABEL[PHASE_OF[s]], set()).add(s.value)
    assert got == exp015
    codes_re = re.compile(r"\b(" + "|".join([s.value for s in S] + list(st_mod.HOLD_LABEL) + list(st_mod.ISSUE_LABEL)) +
                          r")\b")
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    with ctx.db.tx() as c:
        orders.set_hold(c, o["id"], "HOLD", maker, "t")
        orders.open_issue(c, o["id"], "DELIVERY_DELAY", maker, "t")
    w = web(ctx, "maker")
    for url in ("/", "/orders", f"/orders/{o['order_no']}", f"/orders/{o['order_no']}/payment",
                f"/orders/{o['order_no']}/history", "/payments", "/schedule", "/analytics"):
        html = w.get(url).text
        text = re.sub(r"<script.*?</script>|<[^>]+>", " ", html, flags=re.S)
        assert not codes_re.search(text), (url, codes_re.search(text).group())
    assert filters.won(1_250_000) == "1,250,000원"
    assert filters.kdatetime("2026-10-16T13:30:00+00:00") == "10월 16일(금) 22:30"


# ---------------- 044 단일 인스턴스·스케줄러 ----------------
def test_tcs_044_single_instance_and_task(tmp_path):
    env = {**os.environ, "JOS_ENV": "test", "PYTHONIOENCODING": "utf-8"}
    data = tmp_path / "d"
    from jewelry_os.core.singleton import InstanceLock
    data.mkdir()
    lock = InstanceLock(data).acquire()
    try:
        r = subprocess.run([sys.executable, "-m", "jewelry_os", "--env", "test", "--data-dir", str(data), "serve",
                            "--no-browser"], cwd=ROOT, env=env, capture_output=True, timeout=60)
        assert r.returncode == 3
    finally:
        lock.release()
    r = subprocess.run([sys.executable, "-m", "jewelry_os", "--env", "test", "--data-dir", str(data), "install-task",
                        "--dry-run"], cwd=ROOT, env=env, capture_output=True, timeout=60)
    assert r.returncode == 0
    xml_path = Path(r.stdout.decode("utf-8", "ignore").strip().splitlines()[-1])
    raw = xml_path.read_bytes()
    out = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    assert "<LogonTrigger>" in out and "IgnoreNew" in out and "serve --env" in out


# ---------------- 046 구조·R2 경계 ----------------
def test_tcs_046_structure_r2(ctx):
    src = "\n".join(p.read_text(encoding="utf-8") for p in PKG.rglob("*.py"))
    tpl = "\n".join(p.read_text(encoding="utf-8") for p in (PKG / "web" / "templates").rglob("*.html"))
    for bad in ("webhook", "api.commerce.naver.com", "kapi.kakao.com", "send_message(", "smtplib", "twilio"):
        assert bad.lower() not in src.lower(), bad
    for bad in ("몰수", "환불 불가", "pay.naver.com/", "결제 링크 자동"):
        assert bad not in tpl, bad
    tables = {r[0] for r in ctx.db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"outbox_events", "payment_requests", "payment_events", "cs_messages", "sync_cursors"} <= tables
    cols = {r[1] for r in ctx.db.all("PRAGMA table_info(payment_requests)")}
    assert {"seller_code", "quote_version", "phase", "expected_krw", "is_test"} <= cols
    assert ctx.db.scalar("SELECT COUNT(*) FROM outbox_events") == 0


# ---------------- 048 기동 동기화·간격·빈 결과 ----------------
def test_tcs_048_startup_sync(ctx, operator):
    clock.set_clock(clock.Clock())  # 실제 시계
    add_response(ctx, "boot")
    t0 = time.monotonic()
    with TestClient(create_app(ctx, run_scheduler=True), base_url="http://127.0.0.1:8770") as cl:
        assert time.monotonic() - t0 <= 10
        assert ctx.db.scalar("SELECT COUNT(*) FROM orders") == 1
        assert cl.get("/login").status_code == 200
    settings.put(ctx, "sync_interval_min", 2, operator)
    assert settings.get(ctx, "sync_interval_min") == 2
    ctx.forms.responses.clear()
    st = intake.sync_once(ctx)
    assert st["ok"] and st["total"] == 0
    assert ctx.db.scalar("SELECT status FROM sync_cursors") == "OK"


# ---------------- 049 환경 분리·경로·ACL ----------------
def test_tcs_049_env_paths_acl(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    prod = load_config("prod")
    test = load_config("test")
    assert prod.data_dir == tmp_path / "la" / "JewelryOS" / "prod" and prod.data_dir != test.data_dir
    from jewelry_os.bootstrap import build_ctx
    c = build_ctx(load_config("test", tmp_path / "t"))
    cl = TestClient(create_app(c), base_url="http://127.0.0.1:8770", follow_redirects=False)
    for u in ("/data", "/data/jewelry_os.db", "/jewelry_os.db", "/static/../jewelry_os.db", "/files/x"):
        assert cl.get(u).status_code in (303, 401, 404), u
        assert cl.get(u).status_code != 200
    c.cfg.set_secret("JOS_TEST_SECRET", "x")
    if os.name == "nt":
        out = subprocess.run(["icacls", str(c.cfg.secrets_path)], capture_output=True).stdout.decode("mbcs", "ignore")
        aces = [ln.strip() for ln in out.splitlines()[0:] if ":" in ln and "(" in ln]
        aces = [a.split(" ", 1)[-1] if str(c.cfg.secrets_path) in a else a for a in aces]
        assert len(aces) == 1, out
        assert "(I)" not in out
        for who in ("SYSTEM", "Administrators", "BUILTIN", "Everyone", "Users"):
            assert who not in aces[0], out
    c.db.close()


# ---------------- 050 시각 표시 ----------------
def test_tcs_050_time_display(ctx, maker, operator):
    utc = "2026-10-20T15:30:00+00:00"
    assert filters.kdatetime(utc).endswith("00:30") and filters.kdatetime(utc).startswith("10월 21일")
    at, src = clock.from_kst_input("2026-10-21T00:30")
    assert at == utc and src == "2026-10-21T00:30"


# ---------------- 052 실제 Google 어댑터 × HTTP 모의 서버 ----------------
class FakeGoogle:
    def __init__(self, pages=3, per=2):
        self.reqs = []
        self.tokens = 0
        self.fail_401 = 0
        self.refresh_fail = False
        self.r429 = 0
        self.pages, self.per = pages, per

    def __call__(self, request: httpx.Request):
        self.reqs.append(request)
        url = request.url
        if url.host == "oauth2.googleapis.com":
            body = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            if self.refresh_fail and body.get("grant_type") == "refresh_token":
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.tokens += 1
            d = {"access_token": f"AT{self.tokens}", "expires_in": 3600}
            if body.get("grant_type") == "authorization_code":
                d["refresh_token"] = "RT-secret-value"
            return httpx.Response(200, json=d)
        if url.host == "www.googleapis.com":
            return httpx.Response(200, content=b"DRIVE-BYTES-" + url.path.encode())
        if self.fail_401:
            self.fail_401 -= 1
            return httpx.Response(401)
        if self.r429:
            self.r429 -= 1
            return httpx.Response(429, headers={"Retry-After": "45"})
        tok = int(url.params.get("pageToken") or 0)
        rs = [{"responseId": f"g{tok}-{i}", "createTime": "2026-10-20T01:00:00Z", "lastSubmittedTime": "2026-10-20T01:00:00Z",
               "answers": answers(name=f"구글{tok}{i}")} for i in range(self.per)]
        d = {"responses": rs}
        if tok + 1 < self.pages:
            d["nextPageToken"] = str(tok + 1)
        return httpx.Response(200, json=d)


def _live(ctx, fake):
    ctx.cfg.set_secret("JOS_GOOGLE_REFRESH_TOKEN", "RT-secret-value")
    ctx.cfg.set_secret("JOS_GOOGLE_CLIENT_ID", "cid")
    ctx.cfg.set_secret("JOS_GOOGLE_CLIENT_SECRET", "csec")
    tp = httpx.MockTransport(fake)
    oauth = g.GoogleOAuth(ctx.cfg, tp)
    ctx.forms = g.GoogleForms(oauth, tp)
    ctx.files = g.GoogleDrive(oauth, tp)
    return oauth


def test_tcs_052_google_adapter(ctx, operator, fclock, caplog):
    fake = FakeGoogle()
    _live(ctx, fake)
    settings.put(ctx, "google_form_id", "FORM123", operator)
    st = intake.sync_once(ctx)
    api = [r for r in fake.reqs if r.url.host == "forms.googleapis.com"]
    assert st["ok"] and st["total"] == 6 and len(api) == 3
    assert all(r.url.path == "/v1/forms/FORM123/responses" for r in api)
    assert [r.url.params.get("pageToken") for r in api] == [None, "1", "2"]
    assert all(r.headers["Authorization"].startswith("Bearer AT") for r in api)
    assert all("filter" not in r.url.params for r in api)  # 기본 모드
    settings.put(ctx, "sync_incremental", True, operator)
    fake.reqs.clear()
    intake.sync_once(ctx)
    cur = clock.parse(ctx.db.scalar("SELECT last_success_at FROM sync_cursors"))
    f = [r for r in fake.reqs if r.url.host == "forms.googleapis.com"][0].url.params["filter"]
    assert f.startswith("timestamp >= ") and clock.parse(f.split(">= ")[1]) <= cur - timedelta(days=1) + timedelta(seconds=1)
    fake.fail_401 = 1
    t_before = fake.tokens
    assert intake.sync_once(ctx)["ok"] and fake.tokens == t_before + 1  # 401 → 자동 갱신
    fake.fail_401, fake.refresh_fail = 2, True
    st = intake.sync_once(ctx)
    assert st["error"] == "AUTH_EXPIRED"  # 갱신 실패 = 재연결 필요
    fake.fail_401, fake.refresh_fail, fake.r429 = 0, False, 1
    st = intake.sync_once(ctx)
    assert st["error"] == "RATE_LIMITED"
    assert clock.parse(ctx.db.scalar("SELECT next_attempt_at FROM sync_cursors")) == clock.now() + timedelta(seconds=45)
    assert ctx.files.fetch("FILE9") == b"DRIVE-BYTES-/drive/v3/files/FILE9"
    # OAuth 루프백: redirect_uri·state 검증·refresh token 저장 위치
    ctx.cfg.secrets.pop("JOS_GOOGLE_REFRESH_TOKEN", None)
    oauth = g.GoogleOAuth(ctx.cfg, httpx.MockTransport(fake))
    seen = {}

    def browser_bad(url):
        q = dict(httpx.URL(url).params)
        seen["redirect"] = q["redirect_uri"]
        threading.Thread(target=lambda: _hit(q["redirect_uri"] + "?code=C1&state=WRONG")).start()
    with pytest.raises(g.SourceError):
        g.run_loopback_flow(oauth, browser_bad, timeout=10)
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/oauth2cb", seen["redirect"])

    def browser_ok(url):
        q = dict(httpx.URL(url).params)
        threading.Thread(target=lambda: _hit(q["redirect_uri"] + f"?code=C1&state={q['state']}")).start()
    g.run_loopback_flow(oauth, browser_ok, timeout=10)
    assert "RT-secret-value" in ctx.cfg.secrets_path.read_text(encoding="utf-8")
    assert b"RT-secret-value" not in ctx.cfg.db_path.read_bytes()
    assert "RT-secret-value" not in caplog.text
    for p in ctx.cfg.data_dir.rglob("*.log"):
        assert "RT-secret-value" not in p.read_text(encoding="utf-8", errors="ignore")


def _hit(url):
    try:
        urllib.request.urlopen(url, timeout=5).read()
    except Exception:  # noqa: BLE001 — 400 응답도 예외로 온다
        pass


# ---------------- 053 차단 표 전수(HTTP) + 해결 조건 ----------------
def test_tcs_053_block_table_http(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "INITIAL_PAYMENT_VERIFIED")
    no = o["order_no"]
    w = web(ctx, "maker")
    for key, kind in (("HOLD", "hold"), ("NEEDS_INFO", "hold"), ("PAYMENT_MISMATCH", "issue"), ("DISPUTED", "issue"),
                      ("DELIVERY_DELAY", "issue"), ("REWORK", "issue")):
        with ctx.db.tx() as c:
            if kind == "hold":
                orders.set_hold(c, o["id"], key, maker, "t")
            else:
                iid = orders.open_issue(c, o["id"], key, maker, "t")
        r = act(w, no, "start_cad")
        blocked = "start_cad" in tr.BLOCKS[key]
        assert (r.status_code == 409) == blocked, (key, r.status_code)
        if blocked:
            assert 'data-unmet-code="' in r.text
        with ctx.db.tx() as c:
            c.execute("UPDATE holds SET cleared_at=? WHERE order_id=? AND cleared_at IS NULL", (clock.now_iso(), o["id"]))
            c.execute("UPDATE issues SET state='RESOLVED' WHERE order_id=? AND state='OPEN'", (o["id"],))
        if not blocked:
            tr.revert_last(ctx, o["id"], maker, "다시")
    # DELIVERY_DELAY 해결 조건
    with ctx.db.tx() as c:
        iid = orders.open_issue(c, o["id"], "DELIVERY_DELAY", maker, "공장 지연")
    r = act(w, no, "resolve_issue", issue_id=str(iid), resolution="공장 지연", new_eta="2026-12-01")
    assert r.status_code == 409
    r = act(w, no, "resolve_issue", issue_id=str(iid), resolution="공장 지연", new_eta="2026-12-01",
            notice="카톡으로 지연 안내함")
    assert r.status_code == 303 and get(ctx, no)["internal_eta"] == "2026-12-01"
    # 대기 주문 수락 → 보류 0
    settings.put(ctx, "capacity_limit", 1, operator)
    n = new_order(ctx, "wait")
    assert any(h["reason"] == "WAITLISTED" for h in orders.holds_of(ctx, get(ctx, n)["id"]))
    orders.approve_capacity_exception(ctx, get(ctx, n), operator, "급한 의뢰", "2026-12-24")
    act(w, n, "accept", feasible_confirm="on")
    assert orders.holds_of(ctx, get(ctx, n)["id"]) == []
