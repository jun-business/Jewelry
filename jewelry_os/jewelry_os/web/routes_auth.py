"""첫 실행·로그인·세션 잠금(S-00a·S-00b, DD-007)."""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..bootstrap import needs_setup, run_setup
from ..core import security
from ..services import settings
from .app import flash_redirect, form_of, render, templates

router = APIRouter()


def _safe_next(n: str | None) -> str:
    if not n:
        return "/"
    u = urlparse(n)
    if u.scheme or u.netloc or not n.startswith("/") or n.startswith("//"):
        return "/"
    return n


@router.get("/setup")
async def setup_page(request: Request):
    if not needs_setup(request.state.ctx):
        return JSONResponse({"error": "not_found"}, 404)
    return templates.TemplateResponse(request, "screens/setup.html", {"errors": {}, "form": {}})


@router.post("/setup")
async def setup_post(request: Request):
    ctx = request.state.ctx
    if not needs_setup(ctx):
        return JSONResponse({"error": "not_found"}, 404)
    f = await form_of(request, check_csrf=False)  # 계정 0개 상태 — 세션이 없다
    form = dict(f)
    errors = {}
    for p in ("maker", "operator"):
        for k in ("username", "display_name", "password"):
            if not (form.get(f"{p}_{k}") or "").strip():
                errors[f"{p}_{k}"] = "입력하세요."
        if form.get(f"{p}_password") != form.get(f"{p}_password2"):
            errors[f"{p}_password2"] = "비밀번호 확인이 맞지 않아요."
        elif len(form.get(f"{p}_password") or "") < 10:
            errors[f"{p}_password"] = "비밀번호는 10자 이상이어야 해요."
    if form.get("maker_username") and form.get("maker_username") == form.get("operator_username"):
        errors["operator_username"] = "두 계정의 아이디가 같아요."
    if errors:
        return templates.TemplateResponse(request, "screens/setup.html", {"errors": errors, "form": form},
                                          status_code=422)
    try:
        run_setup(ctx, {"username": form["maker_username"].strip(), "display_name": form["maker_display_name"].strip(),
                        "password": form["maker_password"]},
                  {"username": form["operator_username"].strip(), "display_name": form["operator_display_name"].strip(),
                   "password": form["operator_password"]},
                  {"capacity_limit": int(form.get("capacity_limit") or 3),
                   "payment_policy": form.get("payment_policy") or "THIRDS",
                   "lock_minutes": int(form.get("lock_minutes") or 15),
                   "quiet_start": form.get("quiet_start") or "00:00", "quiet_end": form.get("quiet_end") or "07:00",
                   "google_mode": "mock", "load_holidays": form.get("load_holidays", "on") == "on"})
    except security.AuthError as e:
        return templates.TemplateResponse(request, "screens/setup.html",
                                          {"errors": {"_": e.message}, "form": form}, status_code=422)
    return RedirectResponse("/login?setup=1", 303)


@router.get("/login")
async def login_page(request: Request, next: str | None = None, expired: int = 0, setup: int = 0):
    return templates.TemplateResponse(request, "screens/login.html",
                                      {"next": _safe_next(next), "error": None, "expired": expired, "setup": setup,
                                       "shop_name": settings.get(request.state.ctx, "shop_name")})


@router.post("/login")
async def login_post(request: Request):
    ctx = request.state.ctx
    f = await form_of(request, check_csrf=False)
    nxt = _safe_next(f.get("next"))
    try:
        sid, _ = security.login(ctx.db, (f.get("username") or "").strip(), f.get("password") or "")
    except security.AuthError as e:
        return templates.TemplateResponse(request, "screens/login.html",
                                          {"next": nxt, "error": e.message, "expired": 0, "setup": 0,
                                           "shop_name": settings.get(ctx, "shop_name"),
                                           "username": f.get("username")}, status_code=422)
    r = RedirectResponse(nxt, 303)
    r.set_cookie("jos_session", sid, httponly=True, samesite="strict", path="/")
    return r


@router.post("/logout")
async def logout(request: Request):
    sid = request.cookies.get("jos_session")
    if sid:
        security.logout(request.state.ctx.db, sid)
    r = RedirectResponse("/login", 303)
    r.delete_cookie("jos_session")
    return r


@router.get("/api/session/ping")
async def ping(request: Request):
    return {"ok": True}


@router.post("/api/session/lock")
async def lock(request: Request):
    security.lock(request.state.ctx.db, request.cookies.get("jos_session"))
    return {"locked": True}


@router.post("/api/session/unlock")
async def unlock(request: Request):
    ctx = request.state.ctx
    try:
        body = await request.json()
    except ValueError:
        body = dict(await request.form())
    ok = security.unlock(ctx.db, request.cookies.get("jos_session"), body.get("password") or "")
    if not ok:
        return JSONResponse({"ok": False, "message": "비밀번호가 맞지 않아요."}, 401)
    return {"ok": True}


@router.post("/locked/unlock")
async def unlock_form(request: Request):
    """JS 없이 잠금 해제(잠금 화면 템플릿의 폼)."""
    ctx = request.state.ctx
    f = await form_of(request, check_csrf=False)
    nxt = _safe_next(f.get("next"))
    if security.unlock(ctx.db, request.cookies.get("jos_session"), f.get("password") or ""):
        return RedirectResponse(nxt, 303)
    return templates.TemplateResponse(request, "screens/locked.html", {"next": nxt, "error": "비밀번호가 맞지 않아요."},
                                      status_code=401)
