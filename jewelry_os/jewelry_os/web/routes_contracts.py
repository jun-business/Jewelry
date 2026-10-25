"""계약 동의(Form B)·취소·환불 화면(D-24·D-25)."""
from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..domain.guards import TransitionBlocked, unmet
from ..services import contracts, orders, refunds, settings
from .app import flash_redirect, form_of, render, templates

router = APIRouter()
log = logging.getLogger("jos.web.contracts")
STATIC = Path(__file__).resolve().parent / "static"
EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]


def _order(ctx, no):
    o = orders.get(ctx, no)
    if o is None:
        raise HTTPException(404)
    return o


def _admin(request):
    u = request.state.user
    if u is None or u.role not in ("maker", "operator"):
        raise HTTPException(403)
    return u


# ---------------- 견적 문서 원본(증거) ----------------
def quote_document_html(request, q) -> str:
    """고객 견적 문서를 스타일·로고까지 한 파일에 담는다(나중에 서버 없이도 그대로 열리게)."""
    from ..services import quotes
    pq = quotes.public_quote(request.state.ctx, q["id"])
    html = templates.get_template("screens/quote_doc.html").render(
        {"request": request, "pq": pq.__dict__, "qid": q["id"], "pq_ref": contracts.quote_ref(q), "asset_v": "evidence",
         "kakao_url": settings.get(request.state.ctx, "kakao_channel_url")})

    def css(m):
        p = STATIC / "css" / m.group(1)
        return f"<style>{p.read_text(encoding='utf-8')}</style>" if p.exists() else ""
    html = re.sub(r'<link rel="stylesheet" href="/static/css/([\w.-]+)\?v=[^"]*">', css, html)
    html = re.sub(r'<link rel="(?:icon|apple-touch-icon)"[^>]*>', "", html)
    html = re.sub(r'<script src="/static/js/[^"]*" defer></script>', "", html)
    html = re.sub(r'<nav class="doc-tools no-print".*?</nav>', "", html, flags=re.S)
    ctx = request.state.ctx

    def asset(m):  # 시안 이미지도 파일 안에 넣는다(서버 없이 열리게)
        a = ctx.db.one("SELECT * FROM assets WHERE id=?", (int(m.group(1)),))
        from ..services import files as fs
        data = fs.read(ctx, a) if a else None
        return f'src="data:{a["mime"]};base64,{base64.b64encode(data).decode()}"' if data else 'src=""'
    html = re.sub(r'src="/assets/(\d+)"', asset, html)
    logo = STATIC / "img" / "logo.webp"
    if logo.exists():
        html = html.replace('src="/static/img/logo.webp"',
                            'src="data:image/webp;base64,' + base64.b64encode(logo.read_bytes()).decode() + '"')
    return html


def edge_pdf(html: str) -> bytes | None:
    """Windows Edge(헤드리스)로 PDF 인쇄. Edge가 없거나 실패하면 None — HTML 원본은 그대로 남는다."""
    exe = next((p for p in EDGE if p.exists()), None)
    if exe is None:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="jos-pdf-"))
    try:
        src, out = tmp / "quote.html", tmp / "quote.pdf"
        src.write_text(html, encoding="utf-8")
        subprocess.run([str(exe), "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={tmp / 'u'}",
                        f"--print-to-pdf={out}", src.as_uri()], capture_output=True, timeout=60, check=False)
        # Edge 실행기는 곧바로 끝나고 PDF는 뒤에서 쓰인다(2026-10-24 확인) — 파일이 생기고 크기가 멈출 때까지 기다린다
        last, deadline = -1, time.monotonic() + 45
        while time.monotonic() < deadline:
            size = out.stat().st_size if out.exists() else -1
            if size > 0 and size == last:
                return out.read_bytes()
            last = size
            time.sleep(0.5)
        return None
    except Exception:
        log.warning("quote pdf render failed")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _save_quote_document(request, req_id):
    ctx = request.state.ctx
    req = contracts.get(ctx, req_id)
    if req["quote_html_asset_id"]:
        return
    q = ctx.db.one("SELECT * FROM quotes WHERE id=?", (req["quote_id"],))
    html = quote_document_html(request, q)
    pdf = None if ctx.cfg.is_test else (ctx.extra.get("pdf_renderer") or edge_pdf)(html)
    contracts.attach_quote_document(ctx, req_id, html, pdf)


# ---------------- 주문 상세의 계약 동작 ----------------
@router.post("/orders/{no}/contract/{op}")
async def order_contract(request: Request, no: str, op: str):
    ctx = request.state.ctx
    user = _admin(request)
    o = _order(ctx, no)
    await form_of(request)
    f = await request.form()
    back = f"/orders/{no}/quote#contract"
    try:
        if op == "issue":
            rid = contracts.issue(ctx, o, user, reissue=f.get("reissue") == "1")
            _save_quote_document(request, rid)
            return flash_redirect(back, "계약 동의 링크를 만들었어요. 복사해서 카카오톡으로 보내고 ‘고객에게 보냄 기록’을 남기세요.")
        if op == "sync":
            st = contracts.sync_once(ctx)
            if not st["ok"]:
                from ..services import intake
                return flash_redirect(back, intake.ERROR_TEXT.get(st["error"], "Form B를 읽지 못했어요."), "error")
            return flash_redirect(back, f"Form B 응답을 확인했어요. 새 응답 {st['new']}건 · 수정 {st['revised']}건.")
        if op == "apply":
            contracts.apply_pending(ctx, o, user)
            return flash_redirect(back, "고객 동의 완료로 반영했어요.")
    except TransitionBlocked as e:
        return flash_redirect(back, " ".join(u.message for u in e.unmet), "error")
    raise HTTPException(404)


@router.post("/contracts/{rid:int}/{op}")
async def contract_op(request: Request, rid: int, op: str):
    ctx = request.state.ctx
    user = _admin(request)
    req = contracts.get(ctx, rid)
    if req is None:
        raise HTTPException(404)
    f = await form_of(request)
    no = ctx.db.scalar("SELECT order_no FROM orders WHERE id=?", (req["order_id"],))
    back = f"/orders/{no}/quote#contract"
    try:
        if op == "copied":
            with ctx.db.tx() as c:
                contracts._event(c, req, "link_copied", user)
            return flash_redirect(back, "링크를 복사했어요.")
        if op == "delivered":
            ev = f.get("evidence")
            data = await ev.read() if ev is not None and hasattr(ev, "read") else b""
            missing = contracts.record_delivery(ctx, rid, user, f.get("channel") or "", f.get("delivery_note") or "",
                                                (ev.filename, data) if data else None, f.getlist("items"))
            if missing:
                return flash_redirect(back, "보낸 기록을 남겼어요. 아직 안 보낸 것: " +
                                      ", ".join(contracts.SEND_ITEMS[m] for m in missing) + " — 보낸 뒤 다시 기록하세요.",
                                      "error")
            return flash_redirect(back, "견적서·사전 고지·계약 동의 링크를 모두 보낸 기록을 남겼어요.")
        if op == "review":
            st = contracts.review(ctx, rid, user, f.get("method") or "", f.get("review_note") or "",
                                  remember_email=f.get("remember_email") == "on")
            msg = "검증을 기록했어요. 계약이 유효해졌어요." if st == "ACCEPTED" else \
                "검증을 기록했어요. 남은 문제는 새로 발행해야 해요."
            return flash_redirect(back, msg)
    except TransitionBlocked as e:
        return flash_redirect(back, " ".join(u.message for u in e.unmet), "error")
    raise HTTPException(404)


@router.get("/contracts/{rid:int}/quote-file")
async def contract_quote_file(request: Request, rid: int):
    """① 고객에게 보낼 확정 견적서 — 증거로 보관한 원본과 같은 파일(PDF, 없으면 HTML)."""
    ctx = request.state.ctx
    _admin(request)
    req = contracts.get(ctx, rid)
    if req is None:
        raise HTTPException(404)
    _save_quote_document(request, rid)
    req = contracts.get(ctx, rid)
    if not req["quote_pdf_asset_id"] and not ctx.cfg.is_test:  # HTML만 있으면 PDF를 한 번 더 시도
        from ..services import files as fs
        html = fs.read(ctx, ctx.db.one("SELECT * FROM assets WHERE id=?", (req["quote_html_asset_id"],)))
        pdf = (ctx.extra.get("pdf_renderer") or edge_pdf)(html.decode("utf-8")) if html else None
        if pdf:
            contracts.attach_quote_document(ctx, rid, html.decode("utf-8"), pdf)
            req = contracts.get(ctx, rid)
    aid = req["quote_pdf_asset_id"] or req["quote_html_asset_id"]
    a = ctx.db.one("SELECT * FROM assets WHERE id=?", (aid,))
    from ..services import files as fs
    data = fs.read(ctx, a)
    if data is None:
        raise HTTPException(404)
    ext = "pdf" if req["quote_pdf_asset_id"] else "html"
    shop = settings.get(ctx, "shop_name")
    from urllib.parse import quote as q
    name = f"{shop}_견적서_{req['quote_ref']}.{ext}"
    return Response(data, media_type="application/pdf" if ext == "pdf" else "text/html; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=\"quote_{req['quote_ref']}.{ext}\"; "
                                                    f"filename*=UTF-8''{q(name)}"})


# ---------------- 계약 증거 ----------------
@router.get("/contracts/{rid:int}")
async def contract_page(request: Request, rid: int):
    ctx = request.state.ctx
    user = _admin(request)
    if contracts.get(ctx, rid) is None:
        raise HTTPException(404)
    ev = contracts.evidence(ctx, rid)
    reveal = request.query_params.get("reveal") == "1" and user.role == "operator"
    if reveal:  # 원본 응답의 이메일은 운영자만, 기록을 남기고 본다
        from ..services import audit
        with ctx.db.tx() as c:
            audit.record(c, "contract", rid, user, "contract_pii_viewed", order_id=ev["req"]["order_id"], prev=audit.PII)
    for r in ev["responses"]:
        r["email_shown"] = r["email"] if reveal else contracts.mask_email(r["email"])
    return render(request, "screens/contract.html", {"ev": ev, "reveal": reveal, "LABEL": contracts.STATUS_LABEL,
                                                       "CHIP": contracts.STATUS_CHIP, "IDM": contracts.IDENTITY_METHODS,
                                                       "CH": contracts.DELIVERY_CHANNELS})


@router.get("/contracts/{rid:int}/export")
async def contract_export(request: Request, rid: int):
    ctx = request.state.ctx
    user = _admin(request)
    if user.role != "operator":  # 개인정보가 담긴 증거 묶음 — 운영자만(고객 개인정보 삭제 권한과 같음)
        raise HTTPException(403)
    if contracts.get(ctx, rid) is None:
        raise HTTPException(404)
    data = contracts.export_zip(ctx, rid, user)
    no = ctx.db.scalar("SELECT o.order_no FROM orders o JOIN contract_requests r ON r.order_id=o.id WHERE r.id=?", (rid,))
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="contract-{no}-{rid}.zip"'})


# ---------------- 취소·환불 ----------------
@router.post("/orders/{no}/refunds")
async def refund_new(request: Request, no: str):
    ctx = request.state.ctx
    user = _admin(request)
    o = _order(ctx, no)
    f = await form_of(request)
    try:
        rid = refunds.record(ctx, o, user, f.get("reason_kind") or "", f.get("reason") or "", f.get("via") or "",
                             f.get("requested_at") or None)
    except TransitionBlocked as e:
        return flash_redirect(f"/orders/{no}/payment#refunds", " ".join(u.message for u in e.unmet), "error")
    return flash_redirect(f"/refunds/{rid}", "취소·환불 요청을 접수했어요. 상세 CAD 착수·발주는 검토가 끝날 때까지 막혀요.")


@router.get("/refunds/{rid:int}")
async def refund_page(request: Request, rid: int):
    ctx = request.state.ctx
    _admin(request)
    r = refunds.get(ctx, rid)
    if r is None:
        raise HTTPException(404)
    o = ctx.db.one("SELECT * FROM orders WHERE id=?", (r["order_id"],))
    creq = contracts.get(ctx, r["contract_request_id"]) if r["contract_request_id"] else None
    consents = ctx.db.all("SELECT * FROM contract_consents WHERE request_id=? AND response_row_id=? ORDER BY id",
                          (creq["id"], creq["response_id"])) if creq and creq["response_id"] else []
    return render(request, "screens/refund.html", {"v": refunds.view(ctx, r), "o": o, "creq": creq, "consents": consents,
                                                     "CLABEL": contracts.STATUS_LABEL, "BEARER": refunds.BEARER_LABEL})


@router.post("/refunds/{rid:int}/{op}")
async def refund_op(request: Request, rid: int, op: str):
    ctx = request.state.ctx
    user = _admin(request)
    f = await form_of(request)
    back = f"/refunds/{rid}"
    try:
        if op == "legal":
            if (f.get("legal_withdrawal") or "") not in ("yes", "no", ""):  # 선택지 밖 값은 거부(S-50)
                raise TransitionBlocked([unmet("G-REFUND-INPUT", fields="법정 청약철회 판단(선택지 중 하나)")])
            lw = {"yes": True, "no": False}.get(f.get("legal_withdrawal") or "")
            refunds.legal_review(ctx, rid, user, f.get("legal_note") or "", lw)
            msg = "법적 검토 결과를 기록했어요."
        elif op == "approve":
            refunds.approve(ctx, rid, user, f.get("amount"), f.get("reason") or "", f.get("bearer") or None,
                            f.get("shipping_fee"), f.get("basis_date") or None)
            msg = "환불액을 결정했어요. 결제 취소·이체 후 완료를 기록하세요."
        elif op == "complete":
            refunds.complete(ctx, rid, user, f.get("amount"), f.get("method") or "", f.get("evidence") or "",
                             f.get("completed_at") or None)
            msg = "환급 완료를 기록했어요."
        elif op == "resolve":
            refunds.resolve_without_refund(ctx, rid, user, f.get("kind") or "", f.get("note") or "")
            msg = "환불 없이 해결한 내용을 기록했어요."
        elif op == "close_order":
            msg = "주문을 취소로 정리했어요." if refunds.close_order_after_refund(ctx, rid, user) else "정리할 주문이 없어요."
        elif op == "withdraw":
            refunds.withdraw(ctx, rid, user, f.get("withdraw_note") or "")
            msg = "고객 요청 철회를 기록했어요."
        else:
            raise HTTPException(404)
    except TransitionBlocked as e:
        return flash_redirect(back, " ".join(u.message for u in e.unmet), "error")
    return flash_redirect(back, msg)
