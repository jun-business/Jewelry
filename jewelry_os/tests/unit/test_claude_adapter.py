"""ClaudeAI 어댑터 계약 시험 — 실제 API 대신 HTTP 모의 서버(H3 인수 조건 C-1 전 단계: 요청 형식·오류 매핑만 확인)."""
import json

httpx = __import__("pytest").importorskip("httpx2")  # anthropic 1.x는 httpx2를 쓴다
import pytest

anthropic = pytest.importorskip("anthropic")
from jewelry_os.adapters import ai  # noqa: E402

BRIEF = {"product_type": "반지", "fields": {}, "required_missing_fields": [], "risk_flags": [], "customer_questions": [],
         "assumptions": [], "requires_human_approval": True, "draft_reply": None, "policy_refs": []}


def _client(handler):
    c = ai.ClaudeAI("sk-ant-test", model="claude-opus-5-5")
    c.client = anthropic.Anthropic(api_key="sk-ant-test", max_retries=0,
                                   http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return c


def test_request_shape_and_parse():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        seen["headers"] = dict(req.headers)
        return httpx.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                         "content": [{"type": "text", "text": json.dumps(BRIEF, ensure_ascii=False)}],
                                         "stop_reason": "end_turn", "stop_sequence": None,
                                         "usage": {"input_tokens": 10, "output_tokens": 10}})
    out = _client(handler).analyze({"product_type": "반지", "description": "18K"}, None)
    assert out["product_type"] == "반지"
    b = seen["body"]
    assert seen["url"].endswith("/v1/messages?beta=true") or seen["url"].endswith("/v1/messages")
    assert b["model"] == "claude-opus-5-5" and b["output_config"]["format"]["type"] == "json_schema"
    assert "<customer_data>" in b["messages"][0]["content"] and "x-api-key" in seen["headers"]


@pytest.mark.parametrize("status,kind", [(401, "AUTH"), (429, "RATE"), (500, "ERROR")])
def test_error_mapping(status, kind):
    c = _client(lambda req: httpx.Response(status, json={"type": "error", "error": {"type": "x", "message": "m"}}))
    with pytest.raises(ai.AnalyzerError) as e:
        c.analyze({"product_type": "반지"}, None)
    assert e.value.kind == kind
