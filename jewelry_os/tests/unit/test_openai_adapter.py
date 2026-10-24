"""D-32 OpenAI(GPT-6 Luna) 어댑터 — 가짜 서버(httpx MockTransport)로 요청 모양·오류 처리·키 입력 확인."""
import json

import httpx
import pytest

from jewelry_os.adapters import ai
from jewelry_os.bootstrap import configure_adapters
from jewelry_os.services import settings

DIGEST = {"summary": "요약", "open_questions": [], "decisions": [], "requests": [], "urgency": "normal", "unclear": [],
          "spec_mentions": {k: None for k in ai.SPEC_KEYS}}


def _client(handler):
    return ai.OpenAIAI("sk-test-openai-key", transport=httpx.MockTransport(handler))


def test_request_shape_and_json_schema():
    seen = {}

    def handler(req):
        seen["req"] = req
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": json.dumps(DIGEST)}}]})
    out = _client(handler).digest_chat([{"id": 1, "from": "customer", "at": "x", "text": "12호 가능할까요?"}])
    assert out["summary"] == "요약"
    r = seen["req"]
    body = json.loads(r.content)
    assert str(r.url) == "https://api.openai.com/v1/chat/completions"
    assert r.headers["authorization"] == "Bearer sk-test-openai-key"
    assert body["model"] == "gpt-6-luna"
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["schema"] == ai.DIGEST_SCHEMA
    assert body["messages"][0]["role"] == "system" and "<customer_data>" in body["messages"][1]["content"]


@pytest.mark.parametrize("status,kind", [(401, "AUTH"), (429, "RATE"), (500, "ERROR")])
def test_http_errors(status, kind):
    with pytest.raises(ai.AnalyzerError) as e:
        _client(lambda req: httpx.Response(status, json={"error": {}})).analyze({"description": "x"}, None)
    assert e.value.kind == kind


@pytest.mark.parametrize("message,finish,kind", [({"refusal": "no", "content": None}, "stop", "REFUSAL"),
                                                 ({"content": "{"}, "stop", "SCHEMA"),
                                                 ({"content": "{}"}, "length", "SCHEMA")])
def test_refusal_truncation_bad_json(message, finish, kind):
    with pytest.raises(ai.AnalyzerError) as e:
        _client(lambda req: httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": message}]})) \
            .draft_cs("질문", {"phase_label": "접수"}, None)
    assert e.value.kind == kind


def test_default_openai_falls_back_to_mock_until_key(ctx, operator):
    assert settings.get(ctx, "ai_provider") == "openai" and settings.get(ctx, "ai_model") == "gpt-6-luna"
    configure_adapters(ctx)
    assert isinstance(ctx.analyzer, ai.MockAI)  # 키 없으면 규칙 기반
    from webutil import web
    w = web(ctx, "operator")
    assert "규칙 기반으로 동작 중" in w.get("/settings/ai").text
    assert w.post("/settings/ai", data={"ai_provider": "openai", "ai_key": "sk-test-openai-key"}).status_code == 303
    assert isinstance(ctx.analyzer, ai.OpenAIAI) and ctx.analyzer.model_id == "gpt-6-luna"  # 키 넣으면 바로 적용
    page = w.get("/settings/ai").text
    assert "sk-test-openai-key" not in page and "지금 쓰는 엔진: OpenAI" in page
    assert ctx.cfg.secret("OPENAI_API_KEY") == "sk-test-openai-key"
    ev = ctx.db.one("SELECT * FROM audit_events WHERE action='ai_key_saved'")
    assert ev and "sk-test" not in str(dict(ev))
