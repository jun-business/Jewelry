"""TC-S-057 — 실제 Google 폼 질문 ID 연결(SRS-004 r2). 실제 폼은 질문마다 고유 ID를 주므로 번들 자리표시 ID로는
글 답이 비어 들어온다(H3 C-1 전 확인). 운영자가 설정 > 폼 매핑에서 질문 ID를 항목에 연결하면 받은 응답을 다시 읽는다."""
from helpers import get

from conftest import add_response
from jewelry_os.adapters import google as g
from jewelry_os.services import intake, settings
from webutil import web

# 실제 폼처럼 불투명한 질문 ID(Forms API questionId 모양)
Q = {"display_name": "1a2b3c4d", "contact_kind": "2b3c4d5e", "contact_value": "3c4d5e6f", "product_type": "4d5e6f70",
     "description": "5e6f7081", "source_channel": "6f708192", "consent": "708192a3"}
Q_FILE = "8192a3b4"
BIND = {f"field_{qid}": f for f, qid in Q.items()}


def real_answers(name="김하늘", contact="010-1234-5678", files=("F1",)):
    vals = {"display_name": name, "contact_kind": "카카오톡", "contact_value": contact, "product_type": "반지",
            "description": "18K 금으로 12호 반지를 만들고 싶어요. 예산 50만원", "source_channel": "스마트스토어",
            "consent": "동의합니다"}
    a = dict(g.text_answer(Q[f], v) for f, v in vals.items())
    a.update([g.files_answer(Q_FILE, [(f, f"{f}.jpg") for f in files])])
    return a


def _post_binding(w, binding):
    return w.post("/settings/mapping", data={"qid": list(Q.values()), **binding})


def _active_holds(ctx, oid):
    return {r["reason"] for r in ctx.db.all("SELECT reason FROM holds WHERE order_id=? AND cleared_at IS NULL", (oid,))}


def test_tcs_057_bind_real_question_ids(ctx, maker, operator):
    ctx.files.files["F1"] = g.sample_jpeg("F1")
    ctx.forms.add("real-1", real_answers(), "2026-10-20T01:00:00Z")
    op = web(ctx, "operator")
    assert op.post("/system/sync-now", data={"back": "/settings/google"}).status_code == 303
    oid = ctx.db.scalar("SELECT id FROM orders")
    o = get(ctx, ctx.db.scalar("SELECT order_no FROM orders"))

    # ① 연결 전: 글 답이 비어 보류 2종 · AI 미실행 · 사진은 모양으로 들어옴(d29e3d1)
    parsed, rev = intake.answers_of(ctx, o)
    assert rev == 1 and parsed["display_name"] is None and parsed["consent"] is False
    assert {"NO_CONSENT", "NEEDS_INFO"} <= _active_holds(ctx, oid)
    assert ctx.analyzer.calls == [] and ctx.db.scalar("SELECT COUNT(*) FROM ai_tasks") == 0
    assert ctx.db.scalar("SELECT COUNT(*) FROM assets WHERE order_id=? AND kind='reference' AND fetch_status='OK'",
                         (oid,)) == 1

    # ② 미인식 질문 안내 · 예시 답변(연락처 가림) · 사진 문항은 목록에 없음
    page = op.get("/settings/google").text
    assert "연결하지 않은 폼 질문 7개" in page and "/settings/mapping" in page
    page = op.get("/settings/mapping").text
    for qid in Q.values():
        assert qid in page
    assert Q_FILE not in page
    assert "김하늘" in page and "[가림]" in page and "010-1234-5678" not in page

    # ③ maker 403 · 주소 연결 422 · 필수 빠짐 422 — 저장 0
    assert _post_binding(web(ctx, "maker"), BIND).status_code == 403
    bad = _post_binding(op, {**BIND, f"field_{Q['source_channel']}": "address"})
    assert bad.status_code == 422 and "배송 주소" in bad.text
    bad = _post_binding(op, {**BIND, f"field_{Q['consent']}": ""})
    assert bad.status_code == 422 and "필수 항목을 모두 연결하세요" in bad.text and "개인정보 동의" in bad.text
    assert settings.get(ctx, intake.BINDING_KEY) == settings.DEFAULTS[intake.BINDING_KEY]  # 기본값 그대로(D-22)
    assert ctx.db.scalar("SELECT COUNT(*) FROM intake_revisions") == 1

    # ④ 연결 저장 → 받은 응답 다시 읽기: revision 2 · 필드 채움 · 시스템 보류 해제 · AI 1회
    r = _post_binding(op, BIND)
    assert r.status_code == 303
    assert settings.get(ctx, intake.BINDING_KEY) == {qid: f for f, qid in Q.items()}
    ev = ctx.db.one("SELECT * FROM audit_events WHERE action='setting_changed' AND object_id=?", (intake.BINDING_KEY,))
    assert ev["actor_id"] == operator.id and ev["actor_role"] == "operator"
    o = get(ctx, o["order_no"])
    parsed, rev = intake.answers_of(ctx, o)
    assert rev == 2 and o["input_revision"] == 2
    assert (parsed["display_name"], parsed["contact_value"], parsed["product_type"], parsed["consent"],
            parsed["source_channel"]) == ("김하늘", "010-1234-5678", "반지", True, "smartstore")
    assert [f["file_id"] for f in parsed["reference_files"]] == ["F1"]
    assert not {"NO_CONSENT", "NEEDS_INFO"} & _active_holds(ctx, oid)
    assert ctx.db.scalar("SELECT COUNT(*) FROM audit_events WHERE order_id=? AND action='hold_cleared' "
                         "AND actor_role='system'", (oid,)) == 2
    cust = ctx.db.one("SELECT * FROM customers WHERE id=?", (o["customer_id"],))
    assert cust["display_name"] == "김하늘" and ctx.dec(cust["contact_enc"]) == "010-1234-5678"
    assert cust["consent_version"] == "consent_v1"
    assert o["product_type"] == "반지" and o["summary"].startswith("18K") and o["source_channel"] == "smartstore"
    assert len(ctx.analyzer.calls) == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM ai_tasks WHERE order_id=? AND input_revision=2 AND status='OK'",
                         (oid,)) == 1
    assert o["lifecycle_state"] == "OWNER_REVIEW"

    # ⑤ 멱등: 다시 동기화·같은 연결 재저장 → revision·AI·자산 그대로 · 미인식 0 · 예시값 버림
    for _ in range(2):
        intake.sync_once(ctx)
    assert _post_binding(op, BIND).status_code == 303
    assert ctx.db.scalar("SELECT MAX(revision) FROM intake_revisions") == 2
    assert len(ctx.analyzer.calls) == 1 and ctx.db.scalar("SELECT COUNT(*) FROM ai_tasks") == 1
    assert ctx.db.scalar("SELECT COUNT(*) FROM assets WHERE order_id=?", (oid,)) == 1
    assert intake.unmapped_questions(ctx) == []
    assert "김하늘" not in (ctx.db.scalar("SELECT value FROM meta WHERE key=?", (intake.UNMAPPED_META,)) or "")
    assert "연결하지 않은 폼 질문" not in op.get("/settings/google").text
    page = op.get("/settings/mapping").text
    assert "연결됨" in page and 'value="contact_value" selected' in page

    # ⑥ 새 실제 응답은 바로 채워지고, Mock 자리표시 ID 응답도 그대로 읽힌다
    ctx.forms.add("real-2", real_answers(name="박서준", files=()), "2026-10-20T01:10:00Z")
    add_response(ctx, "mock-1", name="이자리")
    st = intake.sync_once(ctx)
    assert st["new"] == 2
    for rid, name in (("real-2", "박서준"), ("mock-1", "이자리")):
        no = ctx.db.scalar("SELECT o.order_no FROM orders o JOIN intake_responses i ON i.id=o.intake_id "
                           "WHERE i.response_id=?", (rid,))
        oo = get(ctx, no)
        assert intake.answers_of(ctx, oo)[0]["display_name"] == name and not _active_holds(ctx, oo["id"])
    assert len(ctx.analyzer.calls) == 3


def test_tcs_057_erased_customer_not_revived(ctx, operator):
    """삭제한 고객의 응답은 연결 저장 뒤 다시 읽어도 표시명·연락처가 되살아나지 않는다(SRS-084)."""
    ctx.forms.add("real-1", real_answers(name="홍비밀", contact="010-7777-8888", files=()), "2026-10-20T01:00:00Z")
    intake.sync_once(ctx)
    op = web(ctx, "operator")
    cid = ctx.db.scalar("SELECT customer_id FROM orders")
    assert op.post(f"/customers/{cid}/erase", data={"reason": "삭제 요청(카톡)"}).status_code == 303
    assert ctx.db.scalar("SELECT COUNT(*) FROM meta WHERE key=?", (intake.UNMAPPED_META,)) == 0
    assert _post_binding(op, BIND).status_code == 303
    for tname in ("intake_revisions", "customers", "meta"):
        for row in ctx.db.all(f"SELECT * FROM {tname}"):
            s = " ".join(str(v) for v in tuple(row))
            assert "홍비밀" not in s and "7777-8888" not in s, tname
    assert ctx.db.scalar("SELECT MAX(revision) FROM intake_revisions") == 1
