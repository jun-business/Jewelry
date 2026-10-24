"""결제·상담함·일정·고객·실적·설정·시스템·고객 문서·파일(S-04~S-11)."""
from __future__ import annotations

import calendar
import json
import secrets
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from ..core import clock, security
from ..core.db import jdump, jload
from ..domain.guards import TransitionBlocked, unmet
from ..domain.states import GATES, LIVE_INTAKE_GATES
from ..services import (alerts, analysis, audit, backup, capacity, contracts, cs, customers, files, intake, metrics, orders,
                        payments, quotes, settings, system)
from .app import blocked_status, flash_redirect, form_of, render, templates

router = APIRouter()


def _operator(request):
    u = request.state.user
    if u is None or u.role != "operator":
        raise TransitionBlocked([unmet("G-ROLE-OPERATOR")])


# ---------------- S-04 결제 관리 ----------------
@router.get("/payments")
async def payments_page(request: Request, tab: str = "todo"):
    ctx = request.state.ctx
    todo = ctx.db.all("SELECT r.*, o.order_no, o.lifecycle_state, c.display_name AS customer_name FROM payment_requests r "
                      "JOIN orders o ON o.id=r.order_id JOIN customers c ON c.id=o.customer_id WHERE r.status IN "
                      "('DRAFT','LINK_READY') AND o.lifecycle_state IN ('PAYMENT_PENDING','QC_PASSED') ORDER BY r.created_at")
    issues = ctx.db.all("SELECT i.*, o.order_no FROM issues i JOIN orders o ON o.id=i.order_id WHERE i.state='OPEN' AND "
                        "i.type IN ('PAYMENT_MISMATCH','REFUND_REVIEW') ORDER BY i.opened_at")
    events = ctx.db.all("SELECT e.*, r.phase, o.order_no, u.display_name AS recorder FROM payment_events e JOIN "
                        "payment_requests r ON r.id=e.request_id JOIN orders o ON o.id=r.order_id LEFT JOIN users u ON "
                        "u.id=e.recorded_by ORDER BY e.id DESC LIMIT 100")
    last = events[0] if events else None
    return render(request, "screens/payments.html", {"tab": tab, "todo": todo, "issues": issues, "events": events,
                                                      "recv": payments.receivables(ctx), "last": last})


# ---------------- S-05 상담함 ----------------
@router.get("/cs")
async def cs_page(request: Request):
    from ..services import kakao_import
    ctx = request.state.ctx
    return render(request, "screens/cs_inbox.html", {"box": cs.inbox(ctx), "open_threads": kakao_import.open_threads(ctx)})


async def _chat_text(f) -> str:
    """붙여넣은 대화 + 올린 .txt(카카오톡 ‘대화 내보내기’)를 합친다."""
    text = f.get("chat") or ""
    up = f.get("chat_file")
    if up is not None and hasattr(up, "read"):
        data = await up.read()
        if data:
            for enc in ("utf-8-sig", "cp949"):
                try:
                    text += "\n" + data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
    return text


@router.post("/cs/import")
async def cs_import(request: Request):
    """주문 없는 카카오톡 문의 대화 가져오기(D-30)."""
    from ..services import kakao_import
    ctx = request.state.ctx
    f = await form_of(request)
    try:
        st = kakao_import.import_chat(ctx, request.state.user, await _chat_text(f), title=f.get("title"))
    except TransitionBlocked as e:
        return flash_redirect("/cs#inquiries", e.unmet[0].message, "error")
    return flash_redirect(f"/cs/threads/{st['thread_id']}", f"대화 {st['new']}개를 기록했어요.")


@router.get("/cs/threads/{tid:int}")
async def cs_thread_page(request: Request, tid: int):
    from ..services import kakao_import
    ctx = request.state.ctx
    t = kakao_import.thread(ctx, tid)
    if t is None:
        raise HTTPException(404)
    if t["order_id"]:
        no = ctx.db.scalar("SELECT order_no FROM orders WHERE id=?", (t["order_id"],))
        return RedirectResponse(f"/orders/{no}/cs", 303)
    from ..services import cs_digest
    return render(request, "screens/cs_thread.html", {"t": t, "messages": kakao_import.thread_messages(ctx, tid),
                                                       "digest": cs_digest.view(ctx, thread_id=tid)})


@router.post("/cs/threads/{tid:int}/{op}")
async def cs_thread_op(request: Request, tid: int, op: str):
    from ..services import kakao_import
    ctx = request.state.ctx
    f = await form_of(request)
    try:
        if op == "import":
            st = kakao_import.import_chat(ctx, request.state.user, await _chat_text(f), thread_id=tid)
            msg = f"새 대화 {st['new']}개를 기록했어요." + (f" 이미 있던 {st['dup']}개는 건너뛰었어요." if st["dup"] else "")
            return flash_redirect(f"/cs/threads/{tid}", msg)
        if op == "digest":
            from ..services import cs_digest
            st = cs_digest.run(ctx, request.state.user, thread_id=tid, force=True)
            return flash_redirect(f"/cs/threads/{tid}", "대화를 다시 정리했어요." if st == "OK" else
                                  cs_digest.STATUS_LABEL.get(st, "정리하지 못했어요."), "success" if st == "OK" else "error")
        if op == "link":
            o = orders.get(ctx, (f.get("order_no") or "").strip())
            if o is None:
                return flash_redirect(f"/cs/threads/{tid}", "그 주문번호를 찾지 못했어요.", "error")
            kakao_import.link_order(ctx, tid, o, request.state.user)
            return flash_redirect(f"/orders/{o['order_no']}/cs", "문의 대화를 주문에 연결했어요.")
    except TransitionBlocked as e:
        return flash_redirect(f"/cs/threads/{tid}", e.unmet[0].message, "error")
    raise HTTPException(404)


# ---------------- S-06 일정 ----------------
@router.get("/schedule")
async def schedule(request: Request, month: str | None = None):
    ctx = request.state.ctx
    today = clock.today_kst()
    y, m = (int(month[:4]), int(month[5:7])) if month else (today.year, today.month)
    first = date(y, m, 1)
    days = calendar.monthrange(y, m)[1]
    hol = {r["date"]: r["name"] for r in ctx.db.all("SELECT * FROM holidays WHERE date LIKE ?", (f"{y:04d}-{m:02d}-%",))}
    slots = {}
    for r in ctx.db.all("SELECT s.date, o.order_no FROM cad_slots s JOIN orders o ON o.id=s.order_id WHERE s.date LIKE ?",
                        (f"{y:04d}-{m:02d}-%",)):
        slots.setdefault(r["date"], []).append(r["order_no"])
    fac = {}
    for r in ctx.db.all("SELECT f.expected_at, o.order_no FROM factory_jobs f JOIN orders o ON o.id=f.order_id "
                        "WHERE f.status='SENT' AND f.expected_at LIKE ?", (f"{y:04d}-{m:02d}-%",)):
        fac.setdefault(r["expected_at"], []).append(r["order_no"])
    prom = {}
    for r in ctx.db.all("SELECT promised_date, order_no FROM orders WHERE promised_date LIKE ? AND lifecycle_state NOT IN "
                        "('CLOSED','DECLINED','CANCELLED')", (f"{y:04d}-{m:02d}-%",)):
        prom.setdefault(r["promised_date"], []).append(r["order_no"])
    cad_days = set(settings.get(ctx, "cad_weekdays"))
    cells = []
    for d in range(1, days + 1):
        dd = date(y, m, d)
        k = dd.isoformat()
        cells.append({"date": dd, "iso": k, "holiday": hol.get(k), "cad_day": dd.weekday() in cad_days,
                      "slots": slots.get(k, []), "factory": fac.get(k, []), "promised": prom.get(k, []),
                      "today": dd == today})
    prev_m = (first.replace(day=1) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m")
    nxt = date(y + (m // 12), (m % 12) + 1, 1).strftime("%Y-%m")
    return render(request, "screens/schedule.html", {"cells": cells, "lead": first.weekday(), "y": y, "m": m,
                                                      "prev": prev_m, "next": nxt})


# ---------------- S-07 고객 ----------------
@router.get("/customers")
async def customers_page(request: Request, q: str = ""):
    ctx = request.state.ctx
    rows = customers.list_customers(ctx, q or None)
    items = [{"c": r, "masked": customers.masked(ctx.dec(r["contact_enc"]) if r["contact_enc"] else None)} for r in rows]
    return render(request, "screens/customers.html", {"items": items, "q": q})


@router.get("/customers/{cid}")
async def customer_page(request: Request, cid: int, reveal: int = 0):
    ctx = request.state.ctx
    d = customers.detail(ctx, cid)
    if d is None:
        raise HTTPException(404)
    others = [r for r in customers.list_customers(ctx) if r["id"] != cid]
    return render(request, "screens/customer.html", {"d": d, "others": others, "revealed": None,
                                                       "contracts": contracts.of_customer(ctx, cid),
                                                       "CONTRACT_LABEL": contracts.STATUS_LABEL,
                                                       "CONTRACT_CHIP": contracts.STATUS_CHIP})


@router.post("/customers/{cid}/{op}")
async def customer_op(request: Request, cid: int, op: str):
    ctx = request.state.ctx
    f = await form_of(request)
    try:
        if op == "link":
            customers.link(ctx, cid, int(f.get("other_id")), request.state.user, f.get("reason"))
            return flash_redirect(f"/customers/{cid}", "같은 고객으로 연결했어요.")
        if op == "erase":
            customers.erase(ctx, cid, request.state.user, f.get("reason"))
            return flash_redirect(f"/customers/{cid}", "연락처·이름을 지웠어요. 주문·결제 기록은 법정 보존을 위해 남겼어요.")
        if op == "reveal":
            v = customers.reveal_contact(ctx, cid, request.state.user)
            d = customers.detail(ctx, cid)
            return render(request, "screens/customer.html", {"d": d, "others": [], "revealed": v})
    except TransitionBlocked as e:
        d = customers.detail(ctx, cid)
        return render(request, "screens/customer.html", {"d": d, "others": [], "revealed": None, "unmet": e.unmet},
                      blocked_status(e.unmet))
    except ValueError as e:
        return flash_redirect(f"/customers/{cid}", str(e), "error")
    raise HTTPException(404)


# ---------------- S-08 실적 ----------------
@router.get("/analytics")
async def analytics(request: Request, test: int = 0):
    ctx = request.state.ctx
    src, summary = metrics.sources(ctx, bool(test))
    orders_rows = ctx.db.all("SELECT * FROM orders WHERE lifecycle_state='CLOSED'" + ("" if test else " AND is_test=0"))
    per = [{"o": o, "c": metrics.contribution(ctx, o)} for o in orders_rows]
    month = clock.kst(clock.now()).strftime("%Y-%m")
    spend = ctx.db.all("SELECT * FROM ad_spend ORDER BY month DESC, channel")
    return render(request, "screens/analytics.html", {"k": metrics.kpis(ctx, bool(test)), "sources": src,
                                                       "summary": summary, "funnel": metrics.funnel(ctx, bool(test)),
                                                       "per": per, "monthly": metrics.monthly(ctx), "test": test,
                                                       "month": month, "spend": spend,
                                                       "ad": metrics.ad_efficiency(ctx, bool(test)),
                                                       "tax_type": settings.get(ctx, "tax_type")})


@router.post("/analytics/ad-spend")
async def ad_spend(request: Request):
    ctx = request.state.ctx
    f = await form_of(request)
    if request.state.user.role != "operator":
        return flash_redirect("/analytics", "운영자 계정만 할 수 있어요.", "error")
    metrics.set_ad_spend(ctx, request.state.user, f.get("month"), f.get("channel"), int(f.get("amount") or 0))
    return flash_redirect("/analytics", "광고비를 저장했어요.")


# ---------------- S-09 설정 ----------------
SECTIONS = [("display", "내 표시 설정"), ("policy", "운영 정책"), ("calendar", "휴무·업무 시간"), ("policies", "상담·가격 정책"),
            ("google", "Google 연동"), ("ai", "AI"), ("backup", "백업"), ("accounts", "계정"), ("gates", "Gate 보드"),
            ("mapping", "폼 매핑")]


def settings_vm(ctx, section):
    vm = {"section": section, "sections": SECTIONS, "s": settings.all_settings(ctx)}
    if section == "gates":
        vm["gates"] = settings.gate_status(ctx)
        vm["gate_history"] = ctx.db.all("SELECT g.*, u.display_name FROM gate_records g LEFT JOIN users u ON "
                                        "u.id=g.recorded_by ORDER BY g.id DESC")
    if section == "calendar":
        vm["holidays"] = ctx.db.all("SELECT * FROM holidays ORDER BY date")
    if section == "policies":
        vm["policies"] = ctx.db.all("SELECT * FROM policies ORDER BY kind, version DESC")
    if section == "accounts":
        vm["users"] = ctx.db.all("SELECT * FROM users ORDER BY id")
    if section == "backup":
        vm["backups"] = ctx.db.all("SELECT * FROM backups ORDER BY id DESC LIMIT 10")
        vm["data_dir"] = str(ctx.cfg.data_dir)
    if section == "ai":
        vm["usage"] = analysis.usage(ctx)
        prov = settings.get(ctx, "ai_provider")
        vm["has_key"] = bool(ctx.cfg.secret("OPENAI_API_KEY" if prov == "openai" else "ANTHROPIC_API_KEY"))
        vm["ai_active"] = type(ctx.analyzer).__name__
        vm["sent_fields"] = analysis.SENT_FIELDS
    if section == "google":
        cur = ctx.db.one("SELECT * FROM sync_cursors WHERE provider='google_forms' AND scope=?",
                         (settings.get(ctx, "google_form_id"),))
        vm["cursor"] = cur
        vm["error_text"] = intake.ERROR_TEXT
        vm["live_allowed"] = settings.live_intake_allowed(ctx)
        vm["connected"] = bool(ctx.cfg.secret("JOS_GOOGLE_REFRESH_TOKEN"))
        vm["effective_mode"] = settings.effective_google_mode(ctx)
        vm["unmapped_count"] = len(intake.unmapped_questions(ctx))
        vm["form_b"] = contracts.live_status(ctx)  # D-24 Form B(계약 동의) 상태
        from ..adapters import channeltalk
        vm["ct_configured"] = channeltalk.configured(ctx.cfg)  # D-28 채널톡(카카오 상담톡) 키 — 값은 화면에 내지 않음
        vm["ct_check"] = ctx.extra.pop("ct_check", None)
        vm["ct_error_text"] = channeltalk.ERROR_TEXT
        vm["form_b_ready"] = contracts.readiness(ctx)
        vm["form_b_cursor"] = ctx.db.one("SELECT * FROM sync_cursors WHERE provider='google_forms' AND scope=?",
                                         (contracts.form_id(ctx),)) if contracts.form_id(ctx) else None
    if section == "mapping":
        vm["mapping"] = intake.current_mapping(ctx)
        vm["binding_rows"] = intake.binding_rows(ctx)
        vm["field_label"] = intake.FIELD_LABEL
        vm["required_fields"] = intake.REQUIRED_FIELDS
    return vm


@router.get("/settings")
async def settings_root(request: Request):
    return RedirectResponse("/settings/display", 303)


@router.get("/settings/{section}")
async def settings_page(request: Request, section: str):
    if section not in dict(SECTIONS):
        raise HTTPException(404)
    return render(request, "screens/settings.html", settings_vm(request.state.ctx, section))


def _settings_error(request, section, unmet_list=None, errors=None, status=409):
    vm = settings_vm(request.state.ctx, section)
    vm.update({"unmet": unmet_list or [], "errors": errors or {}})
    return render(request, "screens/settings.html", vm, status)


@router.post("/settings/{section}")
async def settings_post(request: Request, section: str):
    ctx = request.state.ctx
    f = await form_of(request)
    user = request.state.user
    if section == "display":
        prefs = dict(user.prefs)
        prefs["text"] = f.get("text") or "normal"
        prefs["theme"] = f.get("theme") or "system"
        with ctx.db.tx() as c:
            c.execute("UPDATE users SET prefs_json=? WHERE id=?", (jdump(prefs), user.id))
        return flash_redirect("/settings/display", "표시 설정을 바꿨어요.")
    if section == "password":
        if not security.verify_password(f.get("current") or "", ctx.db.one("SELECT password_hash FROM users WHERE id=?",
                                                                            (user.id,))["password_hash"]):
            return _settings_error(request, "accounts", errors={"current": "현재 비밀번호가 맞지 않아요."}, status=422)
        if len(f.get("new") or "") < 10:
            return _settings_error(request, "accounts", errors={"new": "비밀번호는 10자 이상이어야 해요."}, status=422)
        with ctx.db.tx() as c:
            c.execute("UPDATE users SET password_hash=? WHERE id=?", (security.hash_password(f["new"]), user.id))
        return flash_redirect("/settings/accounts", "비밀번호를 바꿨어요.")
    try:
        _operator(request)
    except TransitionBlocked as e:
        return _settings_error(request, section if section in dict(SECTIONS) else "display", e.unmet, status=403)
    try:
        if section == "policy":
            for k in ("capacity_limit", "free_concept_limit", "lock_minutes", "alert_sync_multiplier",
                      "alert_backup_hours", "alert_disk_pct", "alert_customer_wait_hours", "alert_delivery_days",
                      "quote_valid_days", "factory_days"):
                if f.get(k) not in (None, ""):
                    v = int(f.get(k))
                    if v < 0:
                        raise ValueError("0 이상의 숫자를 입력하세요.")
                    if settings.get(ctx, k) != v:
                        settings.put(ctx, k, v, user)
            if f.get("tax_type"):
                if f["tax_type"] not in ("unset", "general", "simplified", "exempt"):
                    raise ValueError("과세 유형을 목록에서 고르세요.")
                if settings.get(ctx, "tax_type") != f["tax_type"]:
                    settings.put(ctx, "tax_type", f["tax_type"], user)
            if f.get("simplified_vat_rate_pct") not in (None, ""):
                rate = int(f["simplified_vat_rate_pct"])
                if not 0 <= rate <= 100:
                    raise ValueError("부가가치율은 0~100 사이로 입력하세요.")
                if settings.get(ctx, "simplified_vat_rate_pct") != rate:
                    settings.put(ctx, "simplified_vat_rate_pct", rate, user)
            if "kakao_channel_url" in f:
                url = settings.normalize_kakao_channel(f.get("kakao_channel_url"))
                if settings.get(ctx, "kakao_channel_url") != url:
                    settings.put(ctx, "kakao_channel_url", url, user)
            if f.get("payment_policy") in ("THIRDS", "FULL") and settings.get(ctx, "payment_policy") != f.get("payment_policy"):
                settings.put(ctx, "payment_policy", f.get("payment_policy"), user)
            return flash_redirect("/settings/policy", "운영 정책을 저장했어요.")
        if section == "calendar":
            if f.get("add_date"):
                with ctx.db.tx() as c:
                    c.execute("INSERT OR REPLACE INTO holidays(date, name) VALUES (?,?)", (f["add_date"],
                                                                                         f.get("add_name") or "휴무"))
            if f.get("del_date"):
                with ctx.db.tx() as c:
                    c.execute("DELETE FROM holidays WHERE date=?", (f["del_date"],))
            if f.get("load_defaults"):
                capacity.load_default_holidays(ctx)
            for k in ("quiet_start", "quiet_end"):
                if f.get(k) and settings.get(ctx, k) != f.get(k):
                    settings.put(ctx, k, f.get(k), user)
            if f.get("cad_weekdays_set"):
                days = sorted(int(x) for x in f.getlist("cad_weekdays"))
                settings.put(ctx, "cad_weekdays", days, user)
                settings.put(ctx, "cad_on_holidays", f.get("cad_on_holidays") == "on", user)
            return flash_redirect("/settings/calendar", "휴무·업무 시간을 저장했어요.")
        if section == "google":
            mode = f.get("google_mode") or "mock"
            purpose = f.get("google_form_purpose") or "test"
            if mode not in ("auto", "mock", "live") or purpose not in ("test", "public"):
                return _settings_error(request, "google", errors={"_": "연동 모드와 폼 용도를 목록에서 고르세요."}, status=422)
            if mode in ("auto", "live") and purpose == "public" and not settings.live_intake_allowed(ctx):
                return _settings_error(request, "google", [unmet("G-GATE-LIVE")], status=409)
            if mode == "live" and purpose == "test" and not f.get("confirm_test_form"):
                return _settings_error(request, "google", [unmet("G-CONFIRM")], status=409)
            for k, v in (("google_mode", mode), ("google_form_purpose", purpose),
                         ("google_form_id", (f.get("google_form_id") or settings.get(ctx, "google_form_id")).strip())):
                if settings.get(ctx, k) != v:
                    settings.put(ctx, k, v, user)
            if f.get("sync_interval_min"):
                settings.put(ctx, "sync_interval_min", max(1, int(f["sync_interval_min"])), user)
            from ..bootstrap import configure_adapters
            configure_adapters(ctx)
            return flash_redirect("/settings/google", "Google 연동 설정을 저장했어요.")
        if section == "ai":
            for k in ("ai_monthly_call_limit", "ai_monthly_cost_limit_krw"):
                if f.get(k):
                    settings.put(ctx, k, int(f[k]), user)
            if f.get("ai_key"):  # D-32 키 입력(화면에 다시 보이지 않음)
                prov = f.get("ai_provider") or settings.get(ctx, "ai_provider")
                name = "OPENAI_API_KEY" if prov == "openai" else "ANTHROPIC_API_KEY"
                ctx.cfg.set_secret(name, f["ai_key"].strip())
                from ..core import logging_setup
                logging_setup.register_secret(f["ai_key"].strip())
                with ctx.db.tx() as c:
                    audit.record(c, "integration", name, user, "ai_key_saved", prev=audit.PII)
            if f.get("ai_provider") in ("mock", "claude", "openai"):
                settings.put(ctx, "ai_provider", f["ai_provider"], user)
            if f.get("ai_model"):
                settings.put(ctx, "ai_model", f["ai_model"].strip(), user)
            from ..bootstrap import configure_adapters
            configure_adapters(ctx)
            return flash_redirect("/settings/ai", "AI 설정을 저장했어요.")
        if section == "mapping":  # 실제 폼 질문 ID → 필드 연결(SRS-004 r2)
            qids = f.getlist("qid")
            try:
                st = intake.save_binding(ctx, {q: f.get(f"field_{q}") or "" for q in qids}, user)
            except intake.MappingError as e:
                return _settings_error(request, "mapping", errors={"_": str(e)}, status=422)
            alerts.evaluate(ctx)
            if not st["ok"]:
                return flash_redirect("/settings/mapping", "질문 연결을 저장했어요. 받은 응답을 다시 읽지 못했어요 — "
                                      + intake.ERROR_TEXT.get(st["error"], "동기화에 실패했어요."), "error")
            return flash_redirect("/settings/mapping", f"질문 연결을 저장했어요. 받은 응답 {st['total']}건을 다시 읽어 "
                                                       f"{st['revised']}건을 고쳤어요.")
        if section == "backup":
            if f.get("backup_keep_days"):
                settings.put(ctx, "backup_keep_days", int(f["backup_keep_days"]), user)
            return flash_redirect("/settings/backup", "백업 설정을 저장했어요.")
        if section == "accounts":
            security.create_user(ctx.db, f["username"].strip(), f["display_name"].strip(), f["role"], f["password"])
            return flash_redirect("/settings/accounts", "계정을 추가했어요.")
    except security.AuthError as e:
        return _settings_error(request, section, errors={"_": e.message}, status=422)
    except ValueError as e:
        return _settings_error(request, section, errors={"_": str(e)}, status=422)
    raise HTTPException(404)


@router.post("/settings/gates/{gate_id}")
async def gate_post(request: Request, gate_id: str):
    ctx = request.state.ctx
    f = await form_of(request)
    try:
        _operator(request)
        um = []
        if not (f.get("evidence") or "").strip():
            um.append(unmet("G-REASON"))
        if not f.get("confirm"):
            um.append(unmet("G-CONFIRM"))
        if um:
            raise TransitionBlocked(um)
        settings.record_gate(ctx, gate_id, f.get("status") == "PASSED", f["evidence"], request.state.user)
    except TransitionBlocked as e:
        return _settings_error(request, "gates", e.unmet, status=blocked_status(e.unmet))
    except KeyError:
        raise HTTPException(404)
    return flash_redirect("/settings/gates", f"{gate_id} 상태를 기록했어요.")


@router.post("/settings/policies/new")
async def policy_new(request: Request):
    ctx = request.state.ctx
    f = await form_of(request)
    try:
        _operator(request)
    except TransitionBlocked as e:
        return _settings_error(request, "policies", e.unmet, status=403)
    settings.create_policy(ctx, f.get("kind"), f.get("title") or "정책", f.get("body") or "", request.state.user)
    return flash_redirect("/settings/policies", "정책 초안을 만들었어요.")


@router.post("/settings/policies/{pid}/{op}")
async def policy_op(request: Request, pid: int, op: str):
    ctx = request.state.ctx
    f = await form_of(request)
    try:
        _operator(request)
        if op == "approve":
            if not f.get("confirm"):
                raise TransitionBlocked([unmet("G-CONFIRM")])
            settings.approve_policy(ctx, pid, request.state.user)
        elif op == "retire":
            settings.retire_policy(ctx, pid, request.state.user)
    except TransitionBlocked as e:
        return _settings_error(request, "policies", e.unmet, status=blocked_status(e.unmet))
    except ValueError as e:
        return _settings_error(request, "policies", errors={"_": str(e)}, status=422)
    return flash_redirect("/settings/policies", "정책 상태를 바꿨어요.")


# ---------------- S-10 시스템 ----------------
@router.get("/system")
async def system_page(request: Request):
    ctx = request.state.ctx
    return render(request, "screens/system.html", {"st": system.status(ctx), "items": system.SECURITY_ITEMS,
                                                    "error_text": intake.ERROR_TEXT, "error_code": intake.ERROR_CODE})


@router.post("/system/{op}")
async def system_op(request: Request, op: str):
    ctx = request.state.ctx
    f = await form_of(request)
    if op == "sync-now":
        st = intake.sync_once(ctx)
        intake.fetch_pending_files(ctx)
        contracts.sync_once(ctx)
        alerts.evaluate(ctx)
        if st["ok"]:
            return flash_redirect(f.get("back") or "/system", f"가져왔어요. 새 의뢰 {st['new']}건.")
        return flash_redirect(f.get("back") or "/system", intake.ERROR_TEXT.get(st["error"], "동기화에 실패했어요."), "error")
    if op == "backup-now":
        backup.make_backup(ctx)
        alerts.evaluate(ctx)
        return flash_redirect(f.get("back") or "/settings/backup", "백업을 만들었어요.")
    if op == "security-checklist":
        if request.state.user.role != "operator":
            return flash_redirect("/system", "운영자 계정만 할 수 있어요.", "error")
        for item, _ in system.SECURITY_ITEMS:
            system.set_security_item(ctx, item, f.get(item) == "on", request.state.user)
        return flash_redirect("/system", "PC 보안 점검표를 기록했어요.")
    if op.startswith("jobs-"):
        ok = system.retry_job(ctx, int(op.split("-", 1)[1]))
        return flash_redirect("/system", "다시 처리했어요." if ok else "다시 처리했지만 실패했어요.",
                              "success" if ok else "error")
    raise HTTPException(404)


# ---------------- D-28 채널톡(카카오 상담톡) 키·연결 확인 ----------------
@router.post("/settings/channeltalk/{op}")
async def channeltalk_op(request: Request, op: str):
    ctx = request.state.ctx
    if request.state.user.role != "operator":
        raise HTTPException(403)
    f = await form_of(request)
    from ..adapters import channeltalk
    if op == "keys":
        key, secret = (f.get("access_key") or "").strip(), (f.get("access_secret") or "").strip()
        if not (key and secret):
            return flash_redirect("/settings/google#channeltalk", "Access Key와 Access Secret을 모두 넣어야 해요.", "error")
        ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_KEY", key)
        ctx.cfg.set_secret("JOS_CHANNELTALK_ACCESS_SECRET", secret)
        from ..core import logging_setup
        for v in (key, secret):
            logging_setup.register_secret(v)
        with ctx.db.tx() as c:
            audit.record(c, "integration", "channeltalk", request.state.user, "channeltalk_keys_saved", prev=audit.PII)
        op = "check"
    if op == "check":
        st = channeltalk.check(ctx.extra.get("channeltalk") or channeltalk.ChannelTalk(ctx.cfg))
        ctx.extra["ct_check"] = st
        if st["ok"]:
            return flash_redirect("/settings/google#channeltalk", f"채널톡에 연결됐어요: {st['channel']} · 열린 상담 {st['opened']}건.")
        return flash_redirect("/settings/google#channeltalk", channeltalk.ERROR_TEXT.get(st["error"], "연결하지 못했어요."),
                              "error")
    raise HTTPException(404)


# ---------------- S-11 고객 견적 문서 ----------------
@router.get("/docs/quote/{qid:int}")
async def quote_doc(request: Request, qid: int):
    ctx = request.state.ctx
    pq = quotes.public_quote(ctx, qid)
    if pq is None:
        q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,))
        if q is None:
            raise HTTPException(404)
        return render(request, "screens/error.html", {"title": "승인 전 견적은 고객에게 보여줄 수 없어요",
                                                       "message": "견적을 먼저 승인하세요.",
                                                       "link": ("/orders", "주문 목록")}, 403)
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (qid,))
    return templates.TemplateResponse(request, "screens/quote_doc.html", {"pq": pq.__dict__, "qid": qid,
                                                                          "pq_ref": contracts.quote_ref(q),
                                                                          "kakao_url": settings.get(ctx, "kakao_channel_url")})


@router.get("/docs/quote/{qid:int}.txt")
async def quote_doc_txt(request: Request, qid: int):
    pq = quotes.public_quote(request.state.ctx, qid)
    if pq is None:
        return PlainTextResponse("승인 전 견적은 고객에게 보여줄 수 없어요", 403)
    from .filters import kdate
    from ..domain.money import won
    lines = [f"[{pq.shop_name}] 견적 안내 (v{pq.version})", f"{pq.customer_name} 님, 요청하신 제작 견적이에요.", ""]
    if pq.is_test:
        lines.insert(0, "※ 시험 견적 — 실제 결제용 아님")
    for it in pq.items:
        lines.append(f"· {it.label}: {won(it.amount)}")
    lines += ["", f"총액: {won(pq.total)} ({'부가세 포함' if pq.tax_included else '부가세 별도'}, "
              f"{'배송비 포함' if pq.shipping_included else '배송비 별도'})"]
    if pq.payment_policy == "FULL":
        lines.append(f"결제: 전액 {won(pq.total)}")
    else:
        lines.append(f"결제: 예약금 {won(pq.deposit)}(계약 동의 후·상세 CAD 시작 전) · "
                     f"잔금 {won(pq.balance)}(최종 CAD 승인 후·제작 발주 전)")  # D-24, Form B Q5
    lines += [f"완성 예정: {kdate(pq.promised_date)}", f"견적 유효기간: {kdate(pq.valid_until)}까지",
              "결제는 네이버 스마트스토어에서 진행돼요. 예약금이 확인되면 상세 CAD를, 잔금이 확인되면 제작을 시작해요.",
              f"지금 단계: {pq.phase_label}"]
    kakao = settings.get(request.state.ctx, "kakao_channel_url")
    if kakao:
        lines.append(f"문의: 카카오톡 채널 {pq.shop_name} {settings.kakao_chat_url(kakao)}")
    return PlainTextResponse("\n".join(lines))


# ---------------- 파일 ----------------
@router.get("/assets/{aid}")
async def asset(request: Request, aid: int):
    ctx = request.state.ctx
    a = ctx.db.one("SELECT * FROM assets WHERE id=?", (aid,))
    if a is None or not a["storage_ref"]:
        raise HTTPException(404)
    p = ctx.cfg.files_dir / a["storage_ref"]
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type=a["mime"] or "application/octet-stream", filename=a["filename"],
                        content_disposition_type="inline" if (a["mime"] or "").startswith("image/") else "attachment")


@router.get("/packets/{pid}.zip")
async def packet_zip(request: Request, pid: int):
    ctx = request.state.ctx
    pk = ctx.db.one("SELECT p.*, o.order_no FROM factory_packets p JOIN orders o ON o.id=p.order_id WHERE p.id=?", (pid,))
    if pk is None or not pk["zip_ref"]:
        raise HTTPException(404)
    return FileResponse(ctx.cfg.files_dir / pk["zip_ref"], media_type="application/zip",
                        filename=f"{pk['order_no']}-packet-{pk['packet_hash'][:12]}.zip")


# ---------------- Google OAuth(설치형 앱 — 루프백) ----------------
@router.post("/oauth/google/start")
async def oauth_start(request: Request):
    ctx = request.state.ctx
    await form_of(request)
    if request.state.user.role != "operator":
        return flash_redirect("/settings/google", "운영자 계정만 할 수 있어요.", "error")
    from ..adapters.google import GoogleOAuth
    state = secrets.token_urlsafe(16)
    ctx.extra["oauth_state"] = state
    redirect = f"http://127.0.0.1:{ctx.cfg.port}/oauth2cb"
    oauth = ctx.extra.get("oauth") or GoogleOAuth(ctx.cfg)
    ctx.extra["oauth"] = oauth
    if not ctx.cfg.secret("JOS_GOOGLE_CLIENT_ID"):
        return flash_redirect("/settings/google", "Google 클라이언트 ID가 설정되지 않았어요(secrets.env의 "
                              "JOS_GOOGLE_CLIENT_ID·SECRET).", "error")
    return RedirectResponse(oauth.auth_url(redirect, state), 303)


@router.get("/oauth2cb")
async def oauth_cb(request: Request, code: str = "", state: str = ""):
    ctx = request.state.ctx
    expected = ctx.extra.pop("oauth_state", None)
    if not expected or not secrets.compare_digest(state, expected):
        return PlainTextResponse("잘못된 요청이에요(state 불일치).", 400)
    try:
        ctx.extra["oauth"].exchange(code, f"http://127.0.0.1:{ctx.cfg.port}/oauth2cb")
    except Exception:
        return PlainTextResponse("Google 연결에 실패했어요. 다시 시도하세요.", 400)
    from ..bootstrap import configure_adapters
    configure_adapters(ctx)  # 자동 모드면 바로 실계정 폼으로(D-20)
    return RedirectResponse("/settings/google", 303)
