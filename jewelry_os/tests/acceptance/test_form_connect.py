"""D-20 틴틴골드 의뢰 폼 연결 — 폼 ID는 코드 기본값, 모드는 자동(Google 연결 시 실계정·시험 폼)."""
import httpx

from conftest import answers
from jewelry_os.adapters import google as g
from jewelry_os.bootstrap import configure_adapters
from jewelry_os.services import intake, settings
from webutil import web

FORM_ID = "1xE5XV0lHA9URgOdTYFvcZhM5T-ZYbecH5bXDsSGxF_o"


def test_defaults_point_to_real_form(ctx):
    assert settings.get(ctx, "google_form_id") == FORM_ID
    assert settings.get(ctx, "google_mode") == "auto" and settings.get(ctx, "google_form_purpose") == "test"
    assert settings.effective_google_mode(ctx) == "mock"  # Google 계정 연결 전
    configure_adapters(ctx)
    assert isinstance(ctx.forms, g.MockForms)


def test_auto_switches_to_live_when_connected(ctx, operator):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        return httpx.Response(200, json={"responses": [{"responseId": "real1", "createTime": "2026-10-20T01:00:00Z",
                                                        "lastSubmittedTime": "2026-10-20T01:00:00Z", "answers": answers()}]})
    ctx.cfg.set_secret("JOS_GOOGLE_REFRESH_TOKEN", "RT")
    assert settings.effective_google_mode(ctx) == "live"
    configure_adapters(ctx)
    assert isinstance(ctx.forms, g.GoogleForms)
    tp = httpx.MockTransport(handler)
    oauth = g.GoogleOAuth(ctx.cfg, tp)
    ctx.forms = g.GoogleForms(oauth, tp)
    st = intake.sync_once(ctx)
    assert st["ok"] and st["new"] == 1
    api = [r for r in seen if r.url.host == "forms.googleapis.com"]
    assert api[0].url.path == f"/v1/forms/{FORM_ID}/responses"
    assert ctx.db.scalar("SELECT is_test FROM orders") == 1  # 시험 폼 = 시험 주문(H3: 실고객 운영 승인 전)


def test_settings_auto_mode_rules(ctx, operator):
    w = web(ctx, "operator")
    page = w.get("/settings/google").text
    assert 'value="auto"' in page and "지금: 시험용 Mock" in page and FORM_ID in page
    r = w.post("/settings/google", data={"google_mode": "auto", "google_form_purpose": "public", "google_form_id": FORM_ID})
    assert r.status_code == 409  # 고객 공개 폼은 Gate 통과 전 거부(자동 모드도 같음)
    assert w.post("/settings/google", data={"google_mode": "auto", "google_form_purpose": "test",
                                            "google_form_id": FORM_ID}).status_code == 303


def test_legacy_install_migrates_to_auto_once(ctx, operator):
    """D-20 이전 설치(첫 설정에서 mock·mock-form 저장)도 업데이트 후 Google 연결이 바로 쓰인다 — 한 번만."""
    from jewelry_os.bootstrap import build_ctx
    settings.put(ctx, "google_mode", "mock", None)
    settings.put(ctx, "google_form_id", "mock-form", None)
    with ctx.db.tx() as c:
        c.execute("DELETE FROM settings WHERE key=?", (settings.D20_MARK,))
    ctx.cfg.set_secret("JOS_GOOGLE_REFRESH_TOKEN", "RT")
    c2 = build_ctx(ctx.cfg)
    assert settings.get(c2, "google_mode") == "auto" and settings.get(c2, "google_form_id") == FORM_ID
    assert settings.effective_google_mode(c2) == "live" and isinstance(c2.forms, g.GoogleForms)
    assert settings.D20_MARK not in settings.all_settings(c2)
    settings.put(c2, "google_mode", "mock", operator)  # 이후 직접 고른 시험용은 다시 바꾸지 않는다
    c3 = build_ctx(ctx.cfg)
    assert settings.get(c3, "google_mode") == "mock"
    c2.db.close()
    c3.db.close()


REAL_BINDING = settings.DEFAULTS["intake_question_binding"]  # 틴틴골드 의뢰 폼의 실제 질문 ID(D-22)


def test_real_form_binding_merges_split_questions():
    """폼은 스톤·각인을 둘로, 참고 링크를 따로 묻는다 — 앱은 스톤·각인 한 칸과 의뢰 설명 끝에 담는다(D-22)."""
    from jewelry_os.services import intake
    m = intake.load_mapping(binding=intake.validate_binding(REAL_BINDING))
    a = dict([g.text_answer("702b0596", "반지"), g.text_answer("219660f9", "얇은 밴드"),
              g.text_answer("2388b48b", "https://example.com/ref"), g.text_answer("5d016376", "0.1캐럿 다이아"),
              g.text_answer("42b53d66", "안쪽 각인 J&K"), g.text_answer("70b5dc7c", "홍길동"),
              g.text_answer("4c6af8a8", "010-0000-0000"), g.text_answer("18976f60", "문자메시지"),
              g.text_answer("683c3173", "네이버 블로그"), g.text_answer("72899728", "동의합니다")])
    p = intake.parse_answers(m, a)
    assert p["stones_engraving"] == "스톤: 0.1캐럿 다이아 / 각인·기타: 안쪽 각인 J&K"
    assert p["description"] == "얇은 밴드\n참고 링크: https://example.com/ref"
    assert p["source_channel"] == "naver_blog" and p["consent"] is True
    assert not {"stones", "engraving", "reference_link"} & set(p)
    only_engraving = intake.parse_answers(m, {k: v for k, v in a.items() if k != "5d016376"})
    assert only_engraving["stones_engraving"] == "각인·기타: 안쪽 각인 J&K"
    assert intake.parse_answers(m, {**a, **dict([g.text_answer("683c3173", "네이버 검색")])})["source_channel"] == "naver_search"


def test_real_binding_is_default(ctx):
    """폼 매핑을 코드 기본값으로 — 새 PC·업데이트한 PC가 따로 연결하지 않아도 된다(D-22)."""
    from jewelry_os.services import intake
    assert set(REAL_BINDING) >= {"70b5dc7c", "4c6af8a8", "18976f60", "702b0596", "219660f9", "72899728"}
    assert intake.validate_binding(REAL_BINDING) == REAL_BINDING
    assert "6fd6cc59" not in REAL_BINDING  # 이메일은 받지 않음
    assert intake.current_mapping(ctx)["questions"]["4c6af8a8"]["field"] == "contact_value"
