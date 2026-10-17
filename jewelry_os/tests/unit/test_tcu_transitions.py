"""TC-U-001~039 — DD-002 전이 서비스·차단 표·확인·결제 확인·되돌리기."""
import pytest
from helpers import C, check, codes, get, new_order, order_at

from jewelry_os.core.db import ConflictError
from jewelry_os.domain import transitions as tr
from jewelry_os.domain.guards import Forbidden, TransitionBlocked
from jewelry_os.domain.states import S
from jewelry_os.services import orders, payments, quotes, settings
from conftest import approve_policies


def test_tcu_001_018_wrong_state_and_role(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    for action, t in tr.TRANSITIONS.items():
        if t.to is None:
            continue
        if S.OWNER_REVIEW not in t.frm:
            assert "G-WRONG-STATE" in check(ctx, o, action, maker), action
    assert codes(tr.check(ctx, o["id"], "accept", operator)) == {"G-ROLE-MAKER"}
    assert codes(tr.check(ctx, o["id"], "accept", maker)) == set()


def test_tcu_019_system_cannot_human(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    for action, t in tr.TRANSITIONS.items():
        if t.actors != {"system"}:
            with pytest.raises(Forbidden):
                tr.system_perform(ctx, o["id"], action)
    assert get(ctx, o["order_no"])["lifecycle_state"] == "OWNER_REVIEW"


BLOCK_CASES = [
    ("NEEDS_INFO", "hold"), ("WAITLISTED", "hold"), ("HOLD", "hold"), ("NO_CONSENT", "hold"),
    ("PAYMENT_MISMATCH", "issue"), ("REFUND_REVIEW", "issue"), ("DISPUTED", "issue"), ("DELIVERY_DELAY", "issue"),
    ("CHANGE_REQUEST", "issue"), ("CANCEL_REQUEST", "issue"), ("REWORK", "issue"),
]


@pytest.mark.parametrize("key,kind", BLOCK_CASES)
def test_tcu_020_030_block_table(ctx, maker, operator, key, kind):
    """차단 표 11행 — block_unmet이 표와 같은지(모든 action에 대해)."""
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    with ctx.db.tx() as c:
        if kind == "hold":
            orders.set_hold(c, o["id"], key, maker, "t")
        else:
            orders.open_issue(c, o["id"], key, maker, "t")
        all_actions = set(tr.TRANSITIONS) | {"create_balance_req", "recreate_payment_req"}
        for a in all_actions:
            got = tr.block_unmet(c, o["id"], a)
            if a in tr.BLOCKS[key]:
                assert got, (key, a)
            else:
                assert not got, (key, a)
    expected_progress = tr.PROGRESS
    if key in ("HOLD", "DISPUTED", "NO_CONSENT"):
        assert tr.BLOCKS[key] == expected_progress and "receive" in expected_progress
    if key == "REFUND_REVIEW":
        assert "receive" not in tr.BLOCKS[key]


def test_tcu_031_confirm_required(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    assert "G-CONFIRM" in check(ctx, o, "accept", maker, {}, True)
    assert "G-CONFIRM" not in check(ctx, o, "accept", maker, C, True)
    assert tr.CONFIRM_ACTIONS == {"accept", "decline", "approve_quote", "record_acceptance", "start_cad",
                                  "record_design_ok", "send_factory", "pass_qc", "ship", "close", "cancel"}


def test_tcu_032_policy_version_required(ctx, maker, operator):
    no = new_order(ctx)
    o = get(ctx, no)
    tr.perform(ctx, o["id"], "accept", maker, C)
    quotes.upload_concept(ctx, get(ctx, no), maker, "c.png", b"png", "", 10)
    qid = quotes.draft(ctx, get(ctx, no), maker)
    quotes.save(ctx, qid, maker, quotes.QuoteForm(items=[{"kind": "metal", "label": "금", "amount": "900000"}],
                                                  spec={"material": "골드"}, tax_included=True, shipping_included=True,
                                                  promised_date="2026-12-01", valid_until="2026-11-01"))
    um = tr.check(ctx, o["id"], "approve_quote", maker)
    assert any("적용 정책 버전" in u.message for u in um)
    approve_policies(ctx, operator)
    assert not tr.check(ctx, o["id"], "approve_quote", maker)


def _pay(ctx, o, operator, amount, poid, status="PAYED", phase="DEPOSIT"):
    return payments.record_payment(ctx, o, operator, {**C, "phase": phase, "naver_order_id": "N" + poid,
                                                      "naver_product_order_id": poid, "amount": str(amount),
                                                      "naver_status": status, "looked_up_at": "2026-10-20T12:00"})


def test_tcu_033_payment_cumulative(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    assert _pay(ctx, o, operator, 350000, "P1")["result"] == "mismatch"
    i = ctx.db.one("SELECT id FROM issues WHERE type='PAYMENT_MISMATCH'")
    orders.resolve_issue(ctx, i["id"], operator, "추가 결제 예정")
    assert get(ctx, o["order_no"])["lifecycle_state"] == "PAYMENT_PENDING"
    r = _pay(ctx, get(ctx, o["order_no"]), operator, 50000, "P2")
    assert r["result"] == "verified"
    assert get(ctx, o["order_no"])["lifecycle_state"] == "INITIAL_PAYMENT_VERIFIED"


def test_tcu_033b_refund_reduces_sum(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    with ctx.db.tx() as c:
        orders.set_hold(c, o["id"], "HOLD", maker, "잠깐")
    _pay(ctx, get(ctx, o["order_no"]), operator, 400000, "P1")
    _pay(ctx, get(ctx, o["order_no"]), operator, 400000, "P1", status="REFUND")
    with ctx.db.tx() as c:
        req = tr.payment_request(c, o["id"], ("DEPOSIT",))
        assert tr.paid_sum(c, req["id"]) == 0
    orders.release_hold(ctx, get(ctx, o["order_no"]), "HOLD", maker)
    assert get(ctx, o["order_no"])["lifecycle_state"] == "PAYMENT_PENDING"


def test_tcu_034_hold_then_release_auto_verify(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    with ctx.db.tx() as c:
        orders.set_hold(c, o["id"], "HOLD", maker, "고객 확인 중")
    r = _pay(ctx, get(ctx, o["order_no"]), operator, 400000, "P1")
    assert r["result"] == "awaiting"
    assert ctx.db.scalar("SELECT status FROM payment_requests WHERE order_id=?", (o["id"],)) == "PAID"
    assert get(ctx, o["order_no"])["lifecycle_state"] == "PAYMENT_PENDING"
    orders.release_hold(ctx, get(ctx, o["order_no"]), "HOLD", maker)  # maker가 해제 → 결제 입력한 operator 명의로 확인
    assert get(ctx, o["order_no"])["lifecycle_state"] == "INITIAL_PAYMENT_VERIFIED"
    a = ctx.db.one("SELECT * FROM audit_events WHERE order_id=? AND action='verify_initial'", (o["id"],))
    assert a["actor_role"] == "operator" and a["actor_id"] == operator.id


def test_tcu_035_payment_verify_atomic(ctx, maker, operator, monkeypatch):
    o = order_at(ctx, maker, operator, "PAYMENT_PENDING")
    orig = tr._perform

    def boom(*a, **k):
        raise RuntimeError("injected")
    monkeypatch.setattr(tr, "_perform", boom)
    with pytest.raises(RuntimeError):
        _pay(ctx, o, operator, 400000, "P1")
    monkeypatch.setattr(tr, "_perform", orig)
    assert ctx.db.scalar("SELECT COUNT(*) FROM payment_events") == 0
    assert get(ctx, o["order_no"])["lifecycle_state"] == "PAYMENT_PENDING"


def test_tcu_036_revert_rules(ctx, maker, operator):
    no = new_order(ctx)
    approve_policies(ctx, operator)
    o = get(ctx, no)
    tr.perform(ctx, o["id"], "accept", maker, C)
    quotes.upload_concept(ctx, get(ctx, no), maker, "c.png", b"png", "", 10)  # 시안은 되돌림을 막지 않음
    tr.revert_last(ctx, o["id"], maker, "실수")
    assert get(ctx, no)["lifecycle_state"] == "OWNER_REVIEW"
    assert ctx.db.scalar("SELECT COUNT(*) FROM concepts WHERE order_id=?", (o["id"],)) == 1
    from jewelry_os.devtools import advance
    advance(ctx, no, "QUOTE_APPROVED", maker, operator)
    tr.revert_last(ctx, o["id"], maker, "견적 다시")
    assert get(ctx, no)["lifecycle_state"] == "FREE_CONCEPT"
    sts = [r["status"] for r in ctx.db.all("SELECT status FROM quotes WHERE order_id=? ORDER BY version", (o["id"],))]
    assert sts[0] == "SUPERSEDED" and sts[-1] == "DRAFT"
    advance(ctx, no, "PAYMENT_PENDING", maker, operator)
    with pytest.raises(TransitionBlocked) as e:
        tr.revert_last(ctx, o["id"], operator, "결제 요청 취소")
    assert codes(e.value.unmet) == {"G-REVERT"}


def test_tcu_037_rejected_audited(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    with pytest.raises(TransitionBlocked):
        tr.perform(ctx, o["id"], "ship", maker, C)
    assert ctx.db.scalar("SELECT COUNT(*) FROM audit_events WHERE order_id=? AND action='transition_rejected'",
                         (o["id"],)) == 1


def test_tcu_038_optimistic_lock(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "OWNER_REVIEW")
    v = o["version"]
    tr.perform(ctx, o["id"], "accept", maker, C, expected_version=v)
    with pytest.raises(ConflictError):
        tr.revert_last(ctx, o["id"], maker, "x") if False else tr.perform(ctx, o["id"], "approve_quote", maker, C,
                                                                           expected_version=v)


def test_tcu_039_send_factory_spec_recheck(ctx, maker, operator):
    o = order_at(ctx, maker, operator, "CUSTOMER_DESIGN_APPROVED")
    from jewelry_os.services import manufacturing
    stl = ctx.db.scalar("SELECT id FROM assets WHERE order_id=? AND kind='stl'", (o["id"],))
    manufacturing.save_factory_spec(ctx, o, maker, {"material": "골드", "purity": "18K", "size": "12호", "stones": "없음",
                                                     "engraving": "없음", "finish_na": "on", "special_na": "on",
                                                     "stl_asset_id": stl})
    manufacturing.build_packet(ctx, get(ctx, o["order_no"]), maker)
    assert "G-SPEC-DIFF" not in check(ctx, get(ctx, o["order_no"]), "send_factory", maker)
    # 재견적으로 견적 사양이 바뀐 상황을 만든다(새 승인 견적 v2: 14K)
    with ctx.db.tx() as c:
        q = tr.latest_quote(c, o["id"], "APPROVED")
        nid = quotes.clone_as_draft(c, q, maker)
        c.execute("UPDATE quotes SET spec_json=? WHERE id=?", ('{"material":"골드","purity":"14K","size":"12호",'
                                                               '"stones":"없음","engraving":"없음"}', nid))
        c.execute("UPDATE quotes SET status='SUPERSEDED' WHERE id=?", (q["id"],))
        c.execute("UPDATE quotes SET status='APPROVED' WHERE id=?", (nid,))
    assert "G-SPEC-DIFF" in check(ctx, get(ctx, o["order_no"]), "send_factory", maker)
