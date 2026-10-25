"""H1/H3 PM Review r1 반영 회귀 시험 — TC-S-054(Q9 과세 유형·CAC) · TC-S-055(Q10 발주 후 고객 변경) · TC-S-056(로컬 전용·원격 미완료).
Q2(상한 초과 예외 승인)는 TC-S-005(r2)에서 판정한다."""
import pytest
from helpers import C, get, order_at

from jewelry_os.core import config as jcfg
from jewelry_os.domain.states import FAULT_LABEL, GATES
from jewelry_os.services import metrics, orders, payments, settings
from test_tcs_system_b import build_section_c
from webutil import web

QC_PASS = {k: "pass" for k in ("spec", "purity_engraving", "size", "finish", "setting", "fit")}


def act(w, no, code, **data):
    files = [("qc_photos", ("engraving.jpg", b"\xff\xd8 qc", "image/jpeg"))] if code == "record_qc" else None  # S-52
    return w.post(f"/orders/{no}/actions/{code}", data={"confirm": "on", **data}, files=files)


# ---------------- TC-S-054 과세 유형별 매출·문의당 광고비·CAC ----------------
@pytest.mark.parametrize("tax_type,contribution,per_hour,unset", [
    ("general", 757_576, 84_175, 0),     # A 1,200,000 ÷ 1.1 = 1,090,909
    ("simplified", 842_667, 93_629, 0),  # A 1,200,000 − 1,200,000 × 20% × 10% = 1,176,000
    ("exempt", 866_667, 96_296, 0),      # A 1,200,000 그대로
    ("unset", 350_000, 87_500, 1),       # 세금 포함 견적 A는 계산에서 뺌(B만, 240분)
])
def test_tcs_054_tax_type(ctx, maker, operator, tax_type, contribution, per_hour, unset):
    build_section_c(ctx, maker, operator, tax_type=tax_type)
    k = metrics.kpis(ctx)["KPI-06"]
    assert (k["contribution"], k["value"], k["tax_unset"]) == (contribution, per_hour, unset)
    page = web(ctx, "operator").get("/analytics").text
    assert f"{per_hour:,}원" in page
    assert ("사업자 과세 유형이 미설정" in page) == bool(unset)


def test_tcs_054_cac_and_settings(ctx, maker, operator):
    build_section_c(ctx, maker, operator)
    ad = [a for a in metrics.ad_efficiency(ctx) if a["channel"] == "ad"][0]
    assert (ad["spend"], ad["inquiries"], ad["per_inquiry"], ad["paid_customers"], ad["cac"]) == \
        (100_000, 3, 33_333, 2, 50_000)  # 광고 문의 A·C·D 중 결제까지 간 고객 A·C
    page = web(ctx, "operator").get("/analytics").text
    assert "문의당 광고비" in page and "CAC" in page and "유입비" not in page
    assert "33,333원" in page and "50,000원" in page
    w = web(ctx, "operator")
    assert w.post("/settings/policy", data={"tax_type": "foo"}).status_code == 422
    assert w.post("/settings/policy", data={"tax_type": "simplified", "simplified_vat_rate_pct": "15"}).status_code == 303
    assert settings.get(ctx, "tax_type") == "simplified" and settings.get(ctx, "simplified_vat_rate_pct") == 15
    assert metrics.revenue_ex_tax(ctx, 1_000_000, True) == 985_000 and metrics.revenue_ex_tax(ctx, 1_000_000, False) == 1_000_000
    assert web(ctx, "maker").post("/settings/policy", data={"tax_type": "general"}).status_code == 303  # D-43
    assert settings.get(ctx, "tax_type") == "general"


# ---------------- TC-S-055 발주 후 고객 변경 ----------------
def _rework(ctx, maker, operator, fault, rid="r1"):
    o = order_at(ctx, maker, operator, "FACTORY_RECEIVED", rid=rid)
    w = web(ctx, "maker")
    act(w, o["order_no"], "record_qc", **{**QC_PASS, "engraving": "fail", "spec": "fail"})
    assert act(w, o["order_no"], "open_issue", type="REWORK", note="고객이 각인 문구 변경 요청", fault=fault).status_code == 303
    iid = ctx.db.scalar("SELECT id FROM issues WHERE order_id=? AND type='REWORK' AND state='OPEN'", (o["id"],))
    return o, w, iid


def test_tcs_055_customer_change_full_flow(ctx, maker, operator):
    o, wm, iid = _rework(ctx, maker, operator, "CUSTOMER_CHANGE")
    no = o["order_no"]
    wo = web(ctx, "operator")
    co = ctx.db.one("SELECT * FROM change_orders WHERE issue_id=?", (iid,))
    assert co is not None
    assert "고객 변경 처리" in wm.get(f"/orders/{no}").text
    r = act(wm, no, "resolve_issue", issue_id=str(iid), resolution="재작업")
    assert r.status_code == 409 and "변경 가능 판정" in r.text  # 판정 전 재작업 불가
    assert act(wo, no, "co_decide", co_id=str(co["id"]), feasible="yes", note="각인만 변경 가능").status_code == 403
    assert act(wm, no, "co_accept", co_id=str(co["id"]), channel="카카오톡", evidence="좋아요").status_code == 409
    assert act(wm, no, "co_decide", co_id=str(co["id"]), feasible="yes", note="각인만 변경 가능").status_code == 303
    assert act(wm, no, "co_quote", co_id=str(co["id"]), amount="150,000", description="각인 재작업").status_code == 303
    r = act(wm, no, "resolve_issue", issue_id=str(iid), resolution="재작업")
    assert r.status_code == 409 and "고객 동의" in r.text and "추가 결제 확인" in r.text
    assert act(wo, no, "co_accept", co_id=str(co["id"]), channel="카카오톡", evidence="15만원 추가 동의합니다").status_code == 303
    assert act(wm, no, "co_quote", co_id=str(co["id"]), amount="1", description="x").status_code == 409  # 동의 뒤 수정 불가
    assert act(wm, no, "co_payment_req", co_id=str(co["id"])).status_code == 303  # D-43: 아버님도 만든다
    req = ctx.db.one("SELECT * FROM payment_requests WHERE phase='CHANGE'")
    assert req["expected_krw"] == 150_000 and req["change_order_id"] == co["id"]
    assert "변경 추가금" in wo.get(f"/orders/{no}/payment").text
    r = act(wm, no, "resolve_issue", issue_id=str(iid), resolution="재작업")
    assert r.status_code == 409 and "추가 결제 확인" in r.text
    act(wm, no, "record_payment", phase="CHANGE", naver_order_id="N9", naver_product_order_id="P9", amount="150000",
        naver_status="PAYED", looked_up_at="2026-10-21T10:00")
    assert ctx.db.scalar("SELECT status FROM payment_requests WHERE phase='CHANGE'") == "PAID_VERIFIED"
    assert get(ctx, no)["lifecycle_state"] == "FACTORY_RECEIVED"  # 단계 전이 없음
    assert act(wm, no, "resolve_issue", issue_id=str(iid), resolution="각인 재작업 완료").status_code == 303
    assert act(wm, no, "record_qc", **QC_PASS).status_code == 303
    assert get(ctx, no)["lifecycle_state"] == "QC_PASSED"
    assert metrics.contribution(ctx, get(ctx, no))["total"] == 1_350_000  # 확인된 변경 추가금이 매출에 포함
    acts = [r["action"] for r in ctx.db.all("SELECT action FROM audit_events WHERE object_type='change_order'")]
    assert acts == ["change_feasibility", "change_quoted", "change_accepted"]


def test_tcs_055_infeasible_and_free_change(ctx, maker, operator):
    o, wm, iid = _rework(ctx, maker, operator, "CUSTOMER_CHANGE")
    co = ctx.db.one("SELECT * FROM change_orders WHERE issue_id=?", (iid,))
    act(wm, o["order_no"], "co_decide", co_id=str(co["id"]), feasible="no", note="이미 주조돼 변경 불가")
    assert act(wm, o["order_no"], "resolve_issue", issue_id=str(iid), resolution="변경 불가 안내, 원래 사양으로 진행").status_code == 303
    o2, wm, iid2 = _rework(ctx, maker, operator, "CUSTOMER_CHANGE", rid="r2")
    co2 = ctx.db.one("SELECT * FROM change_orders WHERE issue_id=?", (iid2,))
    act(wm, o2["order_no"], "co_decide", co_id=str(co2["id"]), feasible="yes", note="간단")
    act(wm, o2["order_no"], "co_quote", co_id=str(co2["id"]), amount="0", description="무상 변경")
    act(wm, o2["order_no"], "co_accept", co_id=str(co2["id"]), channel="카카오톡", evidence="좋아요")
    assert act(web(ctx, "operator"), o2["order_no"], "co_payment_req", co_id=str(co2["id"])).status_code == 422
    assert act(wm, o2["order_no"], "resolve_issue", issue_id=str(iid2), resolution="무상 재작업").status_code == 303


@pytest.mark.parametrize("fault", ["DEFECT", "MISMATCH"])
def test_tcs_055_seller_fault_no_change_order(ctx, maker, operator, fault):
    o, wm, iid = _rework(ctx, maker, operator, fault)
    assert ctx.db.scalar("SELECT COUNT(*) FROM change_orders") == 0  # 하자·계약 불일치 = 추가 견적·결제 없음
    assert act(wm, o["order_no"], "resolve_issue", issue_id=str(iid), resolution="재제작 완료").status_code == 303


def test_tcs_055_fault_taxonomy_and_after_factory(ctx, maker, operator):
    assert set(FAULT_LABEL) == {"CUSTOMER_CHANGE", "DEFECT", "MISMATCH", "UNKNOWN"}
    o = order_at(ctx, maker, operator, "FACTORY_SENT")
    w = web(ctx, "maker")
    assert act(w, o["order_no"], "open_issue", type="REWORK", note="x", fault="SELLER").status_code == 409  # 옛 값 거부
    r = act(w, o["order_no"], "open_issue", type="CHANGE_REQUEST", note="크기 변경", fault="CUSTOMER_CHANGE")
    assert r.status_code == 409 and "고객 변심이면 변경 가능 판정·추가 견적·고객 동의·결제 확인" in r.text
    page = w.get(f"/orders/{o['order_no']}").text
    for lab in FAULT_LABEL.values():
        assert lab in page


# ---------------- TC-S-056 로컬 전용 구현 · 원격 관리자 미완료 ----------------
def test_tcs_056_local_only_remote_unfinished(ctx):
    assert jcfg.HOST == "127.0.0.1"  # SRS-145: 로컬 전용(완료)
    assert "GATE-REMOTE-01" in {g[0] for g in GATES}  # SRS-171: 원격 관리자 = Gate 뒤 미완료 기능
    assert not settings.gate_status(ctx)["GATE-REMOTE-01"]["passed"]
    page = web(ctx, "operator").get("/settings/gates").text
    assert "GATE-REMOTE-01" in page
