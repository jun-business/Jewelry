"""2026-10-25 시뮬레이션: 링크를 v1.1로 발행한 뒤 Form B가 v1.2로 바뀜 — 응답 전(ISSUED)이어도 다시 발행할 수 있어야 한다."""
import copy

from helpers import get, order_at

from jewelry_os.devtools import form_b_response
from jewelry_os.services import contracts, loss_review
from webutil import web


def _ready(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "QUOTE_APPROVED")
    q = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='APPROVED'", (o["id"],))
    assert loss_review.confirmed(ctx, q)  # D-36 손해 사유 사람 최종 판단(시험 도우미가 처리)
    return o


def test_issued_link_can_be_reissued_after_form_b_changed(ctx, maker, operator, monkeypatch):
    o = _ready(ctx, maker, operator)
    old = copy.deepcopy(contracts.mapping())
    old["version"] = "contract_v1.1"
    real = contracts.mapping
    monkeypatch.setattr(contracts, "mapping", lambda: old)
    rid = contracts.issue(ctx, get(ctx, o["order_no"]), operator, sleep=lambda s: None)
    monkeypatch.setattr(contracts, "mapping", real)  # 폼이 v1.2로 바뀜
    k = contracts.get(ctx, rid)
    assert k["status"] == "ISSUED" and contracts.is_stale(k)
    w = web(ctx, "operator")
    page = w.get(f"/orders/{o['order_no']}/quote").text
    assert "발행 뒤 Form B가 바뀌었어요" in page and "새 확인코드로 다시 발행" in page
    assert w.post(f"/orders/{o['order_no']}/contract/issue", data={"reissue": "1"}).status_code == 303
    old_k, new = contracts.get(ctx, rid), ctx.db.one("SELECT * FROM contract_requests ORDER BY id DESC LIMIT 1")
    assert old_k["status"] == "SUPERSEDED" and "Form B 변경" in old_k["superseded_reason"]
    assert new["id"] != rid and new["status"] == "ISSUED" and new["contract_form_schema_version"] == real()["version"]
    assert contracts.code_of(ctx, new) != contracts.code_of(ctx, old_k)
    assert "발행 뒤 Form B가 바뀌었어요" not in w.get(f"/orders/{o['order_no']}/quote").text


def test_fresh_issued_link_is_not_reissued(ctx, maker, operator):
    """폼이 그대로면 ISSUED 요청은 다시 발행하지 않는다(멱등) — 새로고침·반복 클릭에도 요청 하나."""
    o = _ready(ctx, maker, operator)
    rid = contracts.issue(ctx, get(ctx, o["order_no"]), operator, sleep=lambda s: None)
    assert not contracts.is_stale(contracts.get(ctx, rid))
    assert contracts.issue(ctx, get(ctx, o["order_no"]), operator, reissue=True, sleep=lambda s: None) == rid
    form_b_response(ctx, rid)  # 이 링크는 그대로 쓸 수 있다
    contracts.sync_once(ctx, sleep=lambda s: None)
    assert contracts.get(ctx, rid)["status"] == "REVIEW_REQUIRED"
