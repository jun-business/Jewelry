"""주문 상세·탭·모든 주문 액션(S-03·S-12, BD-006 액션 코드 표)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..core import clock
from ..core.db import ConflictError, jload
from ..domain import transitions as tr
from ..domain.guards import TransitionBlocked, unmet
from ..domain.states import ENDED, S
from ..services import (analysis, capacity, change_orders, cs, customers, files, intake, manufacturing, orders, payments, quotes,
                        settings)
from .app import blocked_status, flash_redirect, form_of, render

router = APIRouter()
TABS = [("intake", "의뢰"), ("quote", "시안·견적"), ("payment", "결제"), ("cs", "상담"), ("making", "CAD·발주"),
        ("qc", "검수·배송"), ("history", "이력"), ("time", "작업시간")]
DEFAULT_TAB = {S.OWNER_REVIEW: "intake", S.INQUIRY_RECEIVED: "intake", S.AI_BRIEF_READY: "intake",
               S.FREE_CONCEPT: "quote", S.QUOTE_APPROVED: "quote", S.CUSTOMER_ACCEPTED: "payment",
               S.PAYMENT_PENDING: "payment", S.INITIAL_PAYMENT_VERIFIED: "making", S.DETAIL_CAD: "making",
               S.CUSTOMER_DESIGN_APPROVED: "making", S.FACTORY_SENT: "making", S.FACTORY_RECEIVED: "qc",
               S.QC_PASSED: "qc", S.FINAL_PAYMENT_VERIFIED: "qc", S.SHIPPED: "qc"}


def _order(ctx, no):
    o = orders.get(ctx, no)
    if o is None:
        raise HTTPException(404)
    return o


def order_vm(ctx, o, user, tab):
    st = S(o["lifecycle_state"])
    parsed, rev = intake.answers_of(ctx, o)
    q_draft = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='DRAFT'", (o["id"],))
    q_appr = ctx.db.one("SELECT * FROM quotes WHERE order_id=? AND status='APPROVED' ORDER BY version DESC LIMIT 1",
                        (o["id"],))
    na = orders.next_action(ctx, o, user)
    tab_alert = {}
    for u in na.unmet:
        if u.fix and not u.fix.startswith("/"):
            tab_alert[u.fix] = True
    revert_um, _ = tr.revert_check(ctx, o["id"], user)
    holds = orders.holds_of(ctx, o["id"])
    pay_reqs = payments.requests_of(ctx, o["id"])
    with ctx.db.tx() as c:
        bal_amount = payments.balance_amount(c, o) if q_appr else None
    vm = {
        "o": o, "tab": tab, "tabs": TABS, "tab_alert": tab_alert, "na": na, "steps": orders.phase_steps(o),
        "step_no": next((i + 1 for i, s in enumerate(orders.phase_steps(o)) if s["state"] == "current"), 5),
        "ended": st in ENDED, "holds": holds, "hold_codes": {h["reason"] for h in holds},
        "issues": orders.issues_of(ctx, o["id"]), "all_issues": orders.issues_of(ctx, o["id"], only_open=False),
        "approval": orders.approval_state(ctx, o, user), "parsed": parsed, "revisions": intake.revisions_of(ctx, o),
        "brief": analysis.latest_brief(ctx, o["id"]),
        "refs": ctx.db.all("SELECT * FROM assets WHERE order_id=? AND kind='reference' ORDER BY id", (o["id"],)),
        "spec": orders.current_spec(ctx, o["id"]), "concepts": quotes.concepts_of(ctx, o["id"]),
        "free_limit": int(settings.get(ctx, "free_concept_limit")),
        "q_draft": q_draft, "q_draft_items": quotes.items_of(ctx, q_draft["id"]) if q_draft else [],
        "q_appr": q_appr, "q_appr_items": quotes.items_of(ctx, q_appr["id"]) if q_appr else [],
        "q_all": ctx.db.all("SELECT * FROM quotes WHERE order_id=? ORDER BY version DESC", (o["id"],)),
        "acceptances": ctx.db.all("SELECT * FROM customer_acceptances WHERE order_id=? ORDER BY id", (o["id"],)),
        "pay_reqs": pay_reqs, "pay_events": payments.events_of(ctx, o["id"]), "balance_amount": bal_amount,
        "messages": cs.messages_of(ctx, o["id"]), "drafts": cs.drafts_of(ctx, o["id"]),
        "cs_policy": settings.approved_policy(ctx, "cs"),
        "designs": manufacturing.designs_of(ctx, o["id"]),
        "assets": {a["id"]: a for a in ctx.db.all("SELECT * FROM assets WHERE order_id=?", (o["id"],))},
        "design_acc": {a["design_version_id"] for a in ctx.db.all(
            "SELECT design_version_id FROM customer_acceptances WHERE order_id=? AND kind='design'", (o["id"],))},
        "fspec": manufacturing.default_factory_spec(ctx, o),
        "packets": ctx.db.all("SELECT * FROM factory_packets WHERE order_id=? ORDER BY id DESC", (o["id"],)),
        "fjob": manufacturing.factory_job(ctx, o["id"]), "qc": manufacturing.latest_qc(ctx, o["id"]),
        "shipment": ctx.db.one("SELECT * FROM shipments WHERE order_id=? ORDER BY id DESC LIMIT 1", (o["id"],)),
        "margin": manufacturing.margin(ctx, o),
        "slots": capacity.suggest_cad_slots(ctx, 2), "cad_slot": ctx.db.one(
            "SELECT * FROM cad_slots WHERE order_id=?", (o["id"],)), "internal_eta": capacity.internal_eta(ctx),
        "timeline": orders.timeline(ctx, o["id"]), "work": orders.work_summary(ctx, o["id"]),
        "work_logs": ctx.db.all("SELECT w.*, u.display_name FROM work_logs w LEFT JOIN users u ON u.id=w.created_by "
                                "WHERE order_id=? ORDER BY w.id DESC", (o["id"],)),
        "revert_ok": not revert_um, "holiday": capacity.is_holiday_today(ctx),
        "capacity": (capacity.active_count(ctx), capacity.limit(ctx)),
        "cap_exc": orders.pending_capacity_exception(ctx, o["id"]),
        "change_views": [dict(v, co_open=v["co"]["issue_state"] == "OPEN") for v in change_orders.view(ctx, o["id"])],
        "gate_q": settings.quote_gate_passed(ctx), "gate_cap": settings.gate_status(ctx)["GATE-CAP-01"]["passed"],
        "customer": customers.detail(ctx, o["customer_id"]),
        "change_open": any(i["type"] == "CHANGE_REQUEST" for i in orders.issues_of(ctx, o["id"])),
        "can_change": st in orders.CHANGE_OPEN_STATES,
        "decline_labels": __import__("jewelry_os.domain.states", fromlist=["x"]).DECLINE_LABEL,
        "sync": __import__("jewelry_os.web.filters", fromlist=["x"]).sync_indicator(ctx),
        "checks": {a: tr.check(ctx, o["id"], a, user) for a in tr.TRANSITIONS if S(o["lifecycle_state"]) in
                   tr.TRANSITIONS[a].frm},
    }
    return vm


def render_order(request, o, tab, status=200, unmet_list=None, errors=None, form=None, confirm=None):
    ctx = request.state.ctx
    o = orders.get(ctx, o["order_no"])
    vm = order_vm(ctx, o, request.state.user, tab)
    vm.update({"unmet": unmet_list or [], "errors": errors or {}, "form": form or {}, "confirm": confirm})
    return render(request, "screens/order.html", vm, status)


@router.get("/orders/new")
async def new_page(request: Request):
    return render(request, "screens/order_new.html", {"errors": {}, "form": {}})


@router.post("/orders/new")
async def new_post(request: Request):
    ctx = request.state.ctx
    f = dict(await form_of(request))
    try:
        no = intake.manual_intake(ctx, request.state.user, f)
    except intake.ValidationError as e:
        return render(request, "screens/order_new.html", {"errors": e.errors, "form": f}, 422)
    return flash_redirect(f"/orders/{no}", "수기 접수로 주문을 만들었어요.")


@router.get("/orders/{no}")
async def detail(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    tab = DEFAULT_TAB.get(S(o["lifecycle_state"]), "history")
    return RedirectResponse(f"/orders/{no}/{tab}", 303) if request.query_params.get("redirect") else \
        render_order(request, o, tab, confirm=request.query_params.get("confirm"))


@router.get("/orders/{no}/{tab}")
async def tab_page(request: Request, no: str, tab: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    if tab not in dict(TABS):
        raise HTTPException(404)
    return render_order(request, o, tab, confirm=request.query_params.get("confirm"))


def _bool(f, k):
    v = f.get(k)
    return None if v in (None, "") else v in ("1", "true", "on", "yes")


async def _files(f, *names):
    out = {}
    for n in names:
        up = f.get(n)
        if up is not None and hasattr(up, "filename") and up.filename:
            out[n] = (up.filename, await up.read())
    return out


@router.post("/orders/{no}/actions/{code}")
async def action(request: Request, no: str, code: str):
    ctx = request.state.ctx
    user = request.state.user
    o = _order(ctx, no)
    f = await form_of(request)
    p = {k: v for k, v in f.items() if isinstance(v, str)}
    tab = p.get("_tab") or DEFAULT_TAB.get(S(o["lifecycle_state"]), "history")
    msg = None
    try:
        if code == "accept":
            orders.accept_order(ctx, o, user, p)
            msg = "의뢰를 수락했어요. 무료 시안을 올려 주세요."
        elif code == "approve_capacity_exception":
            orders.approve_capacity_exception(ctx, o, user, p.get("reason") or "", p.get("expected_eta") or "")
            msg = "상한 초과 예외를 승인했어요. 아버님이 제작 가능 여부를 확인하고 수락하면 돼요."
        elif code == "co_decide":
            if p.get("feasible") not in ("yes", "no"):
                raise TransitionBlocked([unmet("G-ISSUE-RESOLVE", fields="변경 가능 여부")])
            change_orders.decide(ctx, int(p["co_id"]), user, p["feasible"] == "yes", p.get("note") or "")
            msg = "변경 가능 여부를 기록했어요."
        elif code == "co_quote":
            change_orders.quote(ctx, int(p["co_id"]), user, p.get("amount"), p.get("description") or "")
            msg = "변경 추가 견적을 저장했어요. 고객 동의를 받아 기록하세요."
        elif code == "co_accept":
            change_orders.accept(ctx, int(p["co_id"]), user, p.get("channel"), p.get("evidence"), p.get("accepted_at"))
            msg = "고객 변경 동의를 기록했어요."
        elif code == "co_payment_req":
            change_orders.create_payment_request(ctx, int(p["co_id"]), user)
            msg = "변경 추가금 결제 요청을 만들었어요."
        elif code == "decline":
            def eff(c, cur):
                c.execute("UPDATE orders SET decline_category=? WHERE id=?", (p.get("category"), o["id"]))
            tr.perform(ctx, o["id"], "decline", user, p, expected_version=p.get("version"), effect=eff)
            if p.get("make_notice"):
                o2 = orders.get(ctx, no)
                cs.create_draft(ctx, o2, user, "거절 안내", body="안녕하세요, 의뢰 주셔서 감사해요. 아쉽지만 이번 의뢰는 "
                                "진행하기 어렵다는 말씀을 드려요. 다른 디자인이 필요하시면 언제든 다시 문의해 주세요.", level="L1")
            msg = "의뢰를 거절했어요."
        elif code in ("request_info", "waitlist", "hold"):
            orders.apply_hold_action(ctx, o, code, user, p.get("note") or p.get("reason"))
            msg = {"request_info": "정보 요청으로 표시했어요. 상담 탭에서 질문을 보내 주세요.", "waitlist": "대기로 표시했어요.",
                   "hold": "보류했어요."}[code]
        elif code == "release_hold":
            orders.release_hold(ctx, o, p["reason_code"], user, p.get("note"))
            msg = "보류를 풀었어요."
        elif code == "record_consent":
            orders.record_consent(ctx, o, user, p.get("channel"), p.get("evidence"))
            analysis.process_new(ctx, o["id"])
            msg = "동의 증빙을 기록했어요. AI 정리를 시작했어요."
        elif code == "approve_quote":
            quotes.approve(ctx, o, user, p)
            msg = "견적을 승인했어요. 고객 안내 문서를 만들 수 있어요."
        elif code == "record_acceptance":
            quotes.record_acceptance(ctx, o, user, p)
            msg = "고객 동의를 기록했어요."
        elif code == "approve_requote":
            quotes.approve_requote(ctx, o, user, p)
            msg = "재견적을 승인했어요. 고객 재동의를 기록하세요."
        elif code == "record_reacceptance":
            quotes.record_reacceptance(ctx, o, user, p)
            msg = "고객 재동의를 기록했어요. 변경 요청이 해결됐어요."
        elif code == "create_payment_req":
            payments.create_initial(ctx, o, user, p)
            msg = "결제 요청을 만들었어요."
        elif code == "recreate_payment_req":
            payments.recreate_initial(ctx, o, user)
            msg = "새 견적 기준으로 결제 요청을 만들었어요."
        elif code == "create_balance_req":
            payments.create_balance(ctx, o, user)
            msg = "잔금 결제 요청을 만들었어요."
        elif code == "record_payment":
            r = payments.record_payment(ctx, o, user, p)
            msg = {"verified": "결제를 확인했어요.", "mismatch": "금액이 맞지 않아요. 결제 불일치 문제로 기록했어요.",
                   "refund_review": "취소·반품 요청을 기록했어요. 단계는 그대로 두고 멈췄어요.",
                   "duplicate": "이미 기록된 결제예요.", "recorded": "결제 이벤트를 기록했어요.",
                   "awaiting": "결제를 기록했어요. 막힌 조건이 풀리면 자동으로 결제 확인돼요.",
                   "verified_change": "변경 추가금 결제를 확인했어요."}[r["result"]]
            if r["result"] == "duplicate":
                ev = r["event"]
                msg = f"이미 기록된 결제예요({ev['recorded_at'][:16].replace('T', ' ')})."
        elif code == "verify_final_full":
            payments.verify_final_full(ctx, o, user, p)
            msg = "결제 조건을 확인했어요."
        elif code == "start_cad":
            tr.perform(ctx, o["id"], "start_cad", user, p, expected_version=p.get("version"))
            msg = "상세 CAD를 시작했어요."
        elif code == "record_design_ok":
            manufacturing.record_design_ok(ctx, o, user, p)
            msg = "고객 최종 시안 동의를 기록했어요."
        elif code == "build_packet":
            manufacturing.build_packet(ctx, o, user, p)
            msg = "발주 패킷을 만들었어요."
        elif code == "send_factory":
            manufacturing.send_factory(ctx, o, user, p)
            msg = "공장 발주를 기록했어요."
        elif code == "receive":
            manufacturing.receive(ctx, o, user, p)
            msg = "완제품 수령을 기록했어요."
        elif code == "record_qc":
            res = manufacturing.record_qc(ctx, o, user, p)
            msg = {"PASS": "검수를 통과했어요.", "FAIL": "검수에서 통과하지 못한 항목이 있어요. 재작업 문제로 기록하세요.",
                   "INCOMPLETE": "검수표를 임시 저장했어요."}[res]
        elif code == "ship":
            manufacturing.ship(ctx, o, user, p)
            msg = "발송을 기록했어요."
        elif code == "close":
            manufacturing.close(ctx, o, user, p)
            msg = "주문을 완료했어요."
        elif code == "cancel":
            orders.cancel_order(ctx, o, user, p)
            msg = "주문을 취소했어요. 환불은 결제 탭에서 기록하세요."
        elif code == "revert":
            tr.revert_last(ctx, o["id"], user, p.get("reason"))
            msg = "직전 단계로 되돌렸어요."
        elif code == "open_issue":
            orders.open_issue_action(ctx, o, user, p.get("type"), p.get("note"), p.get("fault"))
            msg = "문제를 기록했어요."
        elif code == "resolve_issue":
            r = orders.resolve_issue(ctx, int(p["issue_id"]), user, p.get("resolution"), p.get("outcome"),
                                     p.get("new_eta"), p.get("notice"))
            if r["open_cancel"]:
                return flash_redirect(f"/orders/{no}/{tab}?confirm=cancel", "문제를 해결했어요. 주문 취소를 진행하세요.")
            msg = "문제를 해결했어요."
        elif code == "retry_ai":  # 표시용 정리만 다시(상태·금액 불변, SRS-119)
            st = analysis.run_brief(ctx, o["id"], force=True)
            msg = "AI 정리를 다시 했어요." if st == "OK" else "AI 정리를 다시 시도했지만 실패했어요. 원문을 직접 확인해 주세요."
        elif code == "set_cad_slot":
            manufacturing.set_cad_slot(ctx, o, user, p["date"])
            msg = "CAD 날짜를 정했어요."
        elif code == "log_work":
            orders.log_work(ctx, o, user, p.get("kind"), int(p.get("minutes") or 0), p.get("work_date") or
                            clock.today_kst().isoformat(), p.get("note"))
            msg = "작업시간을 기록했어요."
        elif code == "correct_spec":
            from ..domain.states import SPEC_FIELDS
            spec = {k: p.get(k, "") for k, _ in SPEC_FIELDS}
            if user.role != "maker":
                raise TransitionBlocked([unmet("G-ROLE-MAKER")])
            orders.correct_spec(ctx, o, user, spec, p.get("reason"))
            msg = "확정 사양을 저장했어요."
        else:
            raise HTTPException(404)
    except TransitionBlocked as e:
        return render_order(request, o, tab, blocked_status(e.unmet), unmet_list=e.unmet, form=p,
                            confirm=p.get("_confirm_panel"))
    except ConflictError:
        return render_order(request, o, tab, 409, unmet_list=[unmet("G-CONFLICT")], form=p)
    except payments.PaymentInputError as e:
        return render_order(request, o, tab, 422, errors=e.errors, form=p)
    except files.FileRejected as e:
        return render_order(request, o, tab, 422, errors={"file": e.message}, form=p)
    except ValueError as e:
        return render_order(request, o, tab, 422, errors={"_": str(e)}, form=p)
    o2 = orders.get(ctx, no)
    new_tab = p.get("_next_tab") or (tab if p.get("_tab") else DEFAULT_TAB.get(S(o2["lifecycle_state"]), "history"))
    return flash_redirect(f"/orders/{no}/{new_tab}", msg or "저장했어요.")


@router.post("/orders/{no}/concepts")
async def concept_post(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    f = await form_of(request)
    up = await _files(f, "file")
    try:
        if "file" not in up:
            raise files.FileRejected("올릴 파일을 고르세요.")
        v = quotes.upload_concept(ctx, o, request.state.user, up["file"][0], up["file"][1], f.get("notes"),
                                  int(f.get("minutes") or 0))
    except TransitionBlocked as e:
        return render_order(request, o, "quote", blocked_status(e.unmet), unmet_list=e.unmet)
    except files.FileRejected as e:
        return render_order(request, o, "quote", 422, errors={"file": e.message})
    return flash_redirect(f"/orders/{no}/quote", f"시안 v{v}을 올렸어요.")


@router.post("/orders/{no}/references")
async def reference_post(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    f = await form_of(request)
    up = await _files(f, "file")
    try:
        if "file" not in up:
            raise files.FileRejected("올릴 파일을 고르세요.")
        with ctx.db.tx() as c:
            files.store(ctx, c, order_id=o["id"], order_no=o["order_no"], kind="reference", filename=up["file"][0],
                        data=up["file"][1], actor=request.state.user, source_file_id="manual")
            from ..services import audit
            audit.record(c, "asset", o["order_no"], request.state.user, "reference_uploaded", order_id=o["id"])
    except files.FileRejected as e:
        return render_order(request, o, "intake", 422, errors={"file": e.message})
    return flash_redirect(f"/orders/{no}/intake", "참고 이미지를 추가했어요.")


@router.post("/orders/{no}/quotes")
async def quote_draft(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    await form_of(request)
    try:
        quotes.draft(ctx, o, request.state.user)
    except TransitionBlocked as e:
        return render_order(request, o, "quote", blocked_status(e.unmet), unmet_list=e.unmet)
    return flash_redirect(f"/orders/{no}/quote", "견적 초안을 만들었어요.")


@router.post("/quotes/{qid}/save")
async def quote_save(request: Request, qid: int):
    ctx = request.state.ctx
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,))
    if q is None:
        raise HTTPException(404)
    o = ctx.db.one("SELECT order_no FROM orders WHERE id=?", (q["order_id"],))
    od = orders.get(ctx, o["order_no"])
    f = await form_of(request)
    items = []
    for i in range(0, 20):
        if f.get(f"item_{i}_label") is None and f.get(f"item_{i}_amount") is None:
            continue
        items.append({"kind": f.get(f"item_{i}_kind"), "label": f.get(f"item_{i}_label"),
                      "amount": f.get(f"item_{i}_amount"), "cost": f.get(f"item_{i}_cost"),
                      "note": f.get(f"item_{i}_note")})
    from ..domain.states import SPEC_FIELDS
    form = quotes.QuoteForm(items=items, spec={k: f.get(f"spec_{k}", "") for k, _ in SPEC_FIELDS},
                            spec_summary=f.get("spec_summary") or "", tax_included=_bool(f, "tax_included"),
                            shipping_included=_bool(f, "shipping_included"), promised_date=f.get("promised_date"),
                            valid_until=f.get("valid_until"), variable_cost=int((f.get("variable_cost") or "0")
                                                                               .replace(",", "") or 0),
                            design_minutes=int(f.get("design_minutes") or 0), internal_note=f.get("internal_note") or "")
    try:
        quotes.save(ctx, qid, request.state.user, form)
    except quotes.ImmutableQuote:
        return Response("승인된 견적은 고칠 수 없어요. 새 버전을 만드세요.", 405)
    except TransitionBlocked as e:
        return render_order(request, od, "quote", blocked_status(e.unmet), unmet_list=e.unmet)
    except ValueError as e:
        return render_order(request, od, "quote", 422, errors={"_": str(e)})
    if f.get("then") == "approve":
        return RedirectResponse(f"/orders/{o['order_no']}/quote?confirm=approve_quote", 303)
    return flash_redirect(f"/orders/{o['order_no']}/quote", "견적을 임시 저장했어요.")


@router.post("/orders/{no}/designs")
async def design_post(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    f = await form_of(request)
    up = await _files(f, "cad", "preview", "stl")
    try:
        manufacturing.upload_design(ctx, o, request.state.user, up, f.get("note"))
    except TransitionBlocked as e:
        return render_order(request, o, "making", blocked_status(e.unmet), unmet_list=e.unmet)
    except files.FileRejected as e:
        return render_order(request, o, "making", 422, errors={"file": e.message})
    return flash_redirect(f"/orders/{no}/making", "디자인 파일을 올렸어요.")


@router.post("/orders/{no}/factory-spec")
async def factory_spec_post(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    f = await form_of(request)
    try:
        if f.get("align_to_quote"):
            q = ctx.db.one("SELECT spec_json FROM quotes WHERE order_id=? AND status='APPROVED' ORDER BY version DESC "
                           "LIMIT 1", (o["id"],))
            base = manufacturing.default_factory_spec(ctx, o)
            base.update(jload(q["spec_json"], {}))
            manufacturing.save_factory_spec(ctx, o, request.state.user, base)
        else:
            manufacturing.save_factory_spec(ctx, o, request.state.user, {k: v for k, v in f.items() if isinstance(v, str)})
    except TransitionBlocked as e:
        return render_order(request, o, "making", blocked_status(e.unmet), unmet_list=e.unmet)
    return flash_redirect(f"/orders/{no}/making", "공장 사양서를 저장했어요.")


@router.post("/orders/{no}/memo")
async def memo_post(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    f = await form_of(request)
    from ..services import audit
    with ctx.db.tx() as c:
        audit.record(c, "memo", o["order_no"], request.state.user, "memo", order_id=o["id"], reason=f.get("memo"))
    return flash_redirect(f"/orders/{no}/{f.get('_tab') or 'history'}", "메모를 저장했어요.")


# ---- 상담(CS) ----
@router.post("/orders/{no}/cs/messages")
async def cs_message(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    f = await form_of(request)
    try:
        cs.record_message(ctx, o, request.state.user, f.get("summary"), f.get("content"), f.get("direction") or "in",
                          f.get("link"))
    except TransitionBlocked as e:
        return render_order(request, o, "cs", 422, errors={"summary": e.unmet[0].message})
    return flash_redirect(f"/orders/{no}/cs", "고객 메시지를 기록했어요.")


@router.post("/orders/{no}/cs/drafts")
async def cs_draft(request: Request, no: str):
    ctx = request.state.ctx
    o = _order(ctx, no)
    f = await form_of(request)
    body = f.get("body") or None
    cs.create_draft(ctx, o, request.state.user, f.get("question") or "", body=body,
                    level=f.get("level") if body else None)
    return flash_redirect(f"/orders/{no}/cs", "답변 초안을 만들었어요. 아버님 승인 후 복사할 수 있어요.")


@router.post("/cs/drafts/{did}/{op}")
async def cs_op(request: Request, did: int, op: str):
    ctx = request.state.ctx
    d = cs.draft(ctx, did)
    if d is None:
        raise HTTPException(404)
    o = ctx.db.one("SELECT order_no FROM orders WHERE id=?", (d["order_id"],))
    od = orders.get(ctx, o["order_no"])
    f = await form_of(request)
    user = request.state.user
    try:
        if op == "save":
            cs.save_body(ctx, did, user, f.get("body") or "")
            msg = "초안을 저장했어요."
        elif op == "judgment":
            cs.record_judgment(ctx, did, user, f.get("judgment"))
            msg = "운영자 판단을 기록했어요."
        elif op == "approve":
            if f.get("body") is not None and d["status"] == "DRAFT":
                cs.save_body(ctx, did, user, f.get("body"))
            cs.approve(ctx, did, user, f.get("confirm"))
            msg = "답변을 승인했어요. 복사해서 카카오톡으로 보내세요."
        elif op == "copied":
            cs.mark_copied(ctx, did, user)
            msg = "복사했어요."
        elif op == "sent":
            cs.mark_sent(ctx, did, user, None, f.get("evidence"))
            msg = "발송 완료를 기록했어요."
        elif op == "rewrite":
            cs.rewrite(ctx, did, user)
            msg = "고쳐 쓸 새 초안을 만들었어요. 다시 승인받아야 해요."
        else:
            raise HTTPException(404)
    except cs.ImmutableDraft:
        return Response("승인되거나 발송된 답변은 고칠 수 없어요. ‘고쳐 쓰기’를 쓰세요.", 405)
    except TransitionBlocked as e:
        if request.headers.get("x-requested-with") == "fetch":
            from fastapi.responses import JSONResponse
            return JSONResponse({"unmet": [u.__dict__ for u in e.unmet]}, blocked_status(e.unmet))
        return render_order(request, od, "cs", blocked_status(e.unmet), unmet_list=e.unmet)
    if request.headers.get("x-requested-with") == "fetch":
        return {"ok": True, "message": msg}
    return flash_redirect(f"/orders/{o['order_no']}/cs", msg)
