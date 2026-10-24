"""FastAPI 앱(BD-006): 세션·잠금·CSRF·보안 헤더·템플릿·공통 응답 규칙."""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..core import clock, security
from ..core.db import ConflictError
from ..domain.guards import Forbidden, TransitionBlocked, Unmet, unmet
from ..services import settings
from . import filters

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
filters.install(templates.env)

PUBLIC = ("/static/", "/login", "/setup", "/oauth2cb", "/favicon.ico", "/sw.js", "/offline")
JSON_PREFIX = "/api/"
ACTIVITY_EXEMPT = ("/api/dashboard/summary", "/api/notifications")


def create_app(ctx, run_scheduler: bool = False) -> FastAPI:
    templates.env.globals["app_env"] = ctx.cfg.env  # 개발용이면 모든 화면 위에 띠 표시
    stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app):
        from ..services import system
        task = None
        if run_scheduler:
            sid = system.on_startup(ctx)
            await asyncio.to_thread(system.first_sync_after_startup, ctx, sid)
            task = asyncio.create_task(system.scheduler_loop(ctx, stop))
        yield
        stop.set()
        if task:
            await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def session_mw(request: Request, call_next):
        path = request.url.path
        request.state.user = None
        request.state.session = None
        request.state.flash = None
        request.state.ctx = ctx
        from ..bootstrap import needs_setup
        if needs_setup(ctx) and not path.startswith(("/setup", "/static/")):
            return RedirectResponse("/setup", 303)
        sid = request.cookies.get("jos_session")
        lock_min = int(settings.get(ctx, "lock_minutes"))
        state, srow = security.session_state(ctx.db, sid, lock_min)
        if state in ("ok", "locked"):
            request.state.user = security.to_user(srow)
            request.state.session = srow
        fl = request.cookies.get("jos_flash")
        if fl:
            try:
                request.state.flash = json.loads(base64.urlsafe_b64decode(fl.encode()).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                request.state.flash = None
        if not path.startswith(PUBLIC):
            if state in ("none", "expired"):
                if path.startswith(JSON_PREFIX) or path.startswith(("/assets/", "/packets/")):
                    return JSONResponse({"error": "unauthenticated"}, 401)
                nxt = quote(str(request.url.path) + (("?" + request.url.query) if request.url.query else ""))
                resp = RedirectResponse(f"/login?next={nxt}" + ("&expired=1" if state == "expired" else ""), 303)
                return resp
            if state == "locked" and path not in ("/api/session/unlock", "/logout", "/locked/unlock"):
                if path.startswith(JSON_PREFIX) or path.startswith(("/assets/", "/packets/")):
                    return JSONResponse({"locked": True}, 401)
                return templates.TemplateResponse(request, "screens/locked.html",
                                                  {"next": str(request.url)}, status_code=200)
            if request.method == "POST" and path.startswith(JSON_PREFIX):
                token = request.headers.get("x-csrf-token")
                if not token or not secrets.compare_digest(str(token), srow["csrf"]):
                    return JSONResponse({"error": "csrf"}, 403)
            # 폼 POST의 CSRF는 라우트의 form_of()가 검사한다(본문을 미들웨어에서 읽지 않기 위해)
            if not path.startswith(ACTIVITY_EXEMPT):
                security.touch(ctx.db, sid)
        response = await call_next(request)
        if fl:
            response.delete_cookie("jos_flash")
        response.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; "
                                                       "object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(Forbidden)
    async def forbidden(request, exc):
        return JSONResponse({"error": "forbidden"}, 403)

    @app.exception_handler(CsrfError)
    async def csrf_error(request, exc):
        return HTMLResponse("보안 토큰이 맞지 않아요. 화면을 새로 열고 다시 시도하세요.", 403)

    @app.exception_handler(404)
    async def not_found(request, exc):
        if request.url.path.startswith(JSON_PREFIX) or request.state.user is None:
            return JSONResponse({"error": "not_found"}, 404)
        return render(request, "screens/error.html", {"title": "찾을 수 없어요",
                                                       "message": "주소가 바뀌었거나 없는 주문이에요.",
                                                       "link": ("/orders", "주문 목록")}, 404)

    from . import routes_auth, routes_contracts, routes_main, routes_orders, routes_ops
    for m in (routes_auth, routes_main, routes_orders, routes_ops, routes_contracts):
        app.include_router(m.router)
    return app


# ---------------- 공통 도우미 ----------------
class CsrfError(Exception):
    pass


async def form_of(request: Request, check_csrf: bool = True):
    f = await request.form()
    if check_csrf:
        s = request.state.session
        token = f.get("csrf") or request.headers.get("x-csrf-token")
        if s is None or not token or not secrets.compare_digest(str(token), s["csrf"]):
            raise CsrfError()
    return f


def flash_redirect(url: str, message: str, kind: str = "success") -> RedirectResponse:
    r = RedirectResponse(url, 303)
    # 쿠키 값은 latin-1만 허용 — 한국어 문구는 base64url로 감싼다
    raw = json.dumps({"message": message, "kind": kind}, ensure_ascii=False).encode("utf-8")
    r.set_cookie("jos_flash", base64.urlsafe_b64encode(raw).decode("ascii"), httponly=True, samesite="strict")
    return r


def blocked_status(unmet_list: list[Unmet]) -> int:
    """BD-006: 역할 부족만 있으면 403, 그 밖 409."""
    codes = {u.code for u in unmet_list}
    if codes and all(c.startswith("G-ROLE") for c in codes):
        return 403
    return 409


def render(request: Request, template: str, vm: dict, status: int = 200) -> HTMLResponse:
    ctx = request.state.ctx
    base = base_vm(request)
    base.update(vm)
    return templates.TemplateResponse(request, template, base, status_code=status)


def base_vm(request: Request) -> dict:
    ctx = request.state.ctx
    user = request.state.user
    from ..services import alerts, system
    s = request.state.session
    vm = {
        "user": user, "csrf": s["csrf"] if s else "", "flash": request.state.flash,
        "test_mode": settings.test_mode(ctx), "unpassed_gates": settings.unpassed_test_gates(ctx),
        "shop_name": settings.get(ctx, "shop_name"), "path": request.url.path,
        "lock_minutes": int(settings.get(ctx, "lock_minutes")),
        "session_created": s["created_at"] if s else None,
        "prefs": user.prefs if user else {},
    }
    if user:
        vm["badge"] = alerts.badge_count(ctx, user.id)
        vm["sync_indicator"] = filters.sync_indicator(ctx)
        vm["startup_banner"] = system.pending_startup_banner(ctx)
        vm["global_banners"] = [p for p in __import__("jewelry_os.services.alerts", fromlist=["x"])
                                .system_problems(ctx, ctx.db.conn) if p[0] in ("system:sync_auth", "system:backup",
                                                                                  "system:disk")]
        from ..services import orders
        vm["queue_counts"] = orders.queue_counts(ctx)
    return vm


def handle_errors(fn):
    """POST 핸들러 공통: 차단·충돌·검증 오류를 BD-006 규칙으로 바꾼다."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        return await fn(*a, **kw)
    return wrapper
