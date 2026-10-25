"""홈(대시보드)·주문 목록·검색·알림 API(S-01·S-02)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..core import clock
from ..domain.states import QUEUES
from ..services import alerts, capacity, orders, settings, system
from .app import flash_redirect, form_of, render

router = APIRouter()


def dashboard_vm(ctx, user):
    qc = orders.queue_counts(ctx)
    wait_n = ctx.db.scalar("SELECT COUNT(*) FROM notifications WHERE kind='customer_wait' AND resolved_at IS NULL")
    groups = alerts.for_dashboard(ctx)
    week_slots = ctx.db.all("SELECT s.date, o.order_no FROM cad_slots s JOIN orders o ON o.id=s.order_id "
                            "WHERE s.date >= ? ORDER BY s.date LIMIT 6", (clock.today_kst().isoformat(),))
    recent = ctx.db.all("SELECT a.*, u.display_name AS actor_name, o.order_no FROM audit_events a LEFT JOIN users u "
                        "ON u.id=a.actor_id LEFT JOIN orders o ON o.id=a.order_id WHERE a.order_id IS NOT NULL "
                        "ORDER BY a.id DESC LIMIT 10")
    today = clock.today_kst()
    return {
        "cards": [
            {"label": "검토할 새 의뢰", "n": qc["review"], "href": "/orders?queue=review", "mine": user.role == "maker"},
            {"label": "고객 답을 기다리는 주문", "n": qc["needs_info"] + ctx.db.scalar(
                "SELECT COUNT(*) FROM orders WHERE lifecycle_state IN ('QUOTE_APPROVED','PAYMENT_PENDING')"),
             "extra": f"그중 지연 {wait_n}" if wait_n else None, "href": "/orders?queue=needs_info", "mine": True},
            {"label": "결제 확인 필요", "n": qc["payment"], "href": "/orders?queue=payment",
             "mine": True},  # D-43: 결제 확인은 아버님·운영자 모두
            {"label": "제작 중인 주문", "n": qc["making"], "href": "/orders?queue=making", "mine": user.role == "maker"},
        ],
        "groups": groups, "active": capacity.active_count(ctx), "limit": capacity.limit(ctx),
        "waitlisted": qc["waitlisted"], "week_slots": week_slots, "recent": recent,
        "holiday": capacity.holiday_name(ctx, today), "late_night": capacity.after_midnight(),
        "status": system.status(ctx), "empty": ctx.db.scalar("SELECT COUNT(*) FROM orders") == 0,
        "today": today.isoformat(), "interval": settings.get(ctx, "sync_interval_min"),
    }


@router.get("/")
async def home(request: Request):
    ctx = request.state.ctx
    return render(request, "screens/dashboard.html", dashboard_vm(ctx, request.state.user))


@router.get("/api/dashboard/summary")
async def summary(request: Request):
    ctx = request.state.ctx
    return {"queues": orders.queue_counts(ctx), "badge": alerts.badge_count(ctx, request.state.user.id)}


@router.get("/api/notifications")
async def notifications(request: Request):
    ctx = request.state.ctx
    rows = ctx.db.all("SELECT n.*, o.order_no FROM notifications n LEFT JOIN orders o ON o.id=n.order_id "
                      "WHERE n.resolved_at IS NULL AND n.release_at<=? ORDER BY n.created_at DESC",
                      (clock.now_iso(),))
    return {"badge": alerts.badge_count(ctx, request.state.user.id),
            "items": [{"kind": r["kind"], "severity": r["severity"], "title": r["title"], "order_no": r["order_no"],
                       "at": r["created_at"]} for r in rows]}


@router.post("/api/notifications/read")
async def notifications_read(request: Request):
    alerts.mark_read(request.state.ctx, request.state.user.id)
    return {"ok": True}


@router.get("/api/search")
async def search(request: Request, q: str = ""):
    ctx = request.state.ctx
    if not q.strip():
        return {"items": []}
    rows, total, _ = orders.list_orders(ctx, "all", q.strip(), per_page=8)
    ended, _, _ = orders.list_orders(ctx, "ended", q.strip(), per_page=8)
    items = list(rows) + list(ended)
    return {"items": [{"order_no": r["order_no"], "customer": r["customer_name"], "product": r["product_type"]}
                      for r in items[:8]]}


@router.get("/orders")
async def order_list(request: Request, queue: str = "", q: str = "", sort: str = "", page: int = 1):
    ctx = request.state.ctx
    counts = orders.queue_counts(ctx)
    keys = [k for k, _ in QUEUES]
    if queue not in keys:
        queue = "review" if counts["review"] else "all"
    rows, total, sort = orders.list_orders(ctx, queue, q.strip() or None, sort or None, max(page, 1))
    user = request.state.user
    items = []
    for r in rows:
        items.append({"o": r, "holds": orders.holds_of(ctx, r["id"]), "issues": orders.issues_of(ctx, r["id"]),
                      "next": orders.next_action(ctx, r, user), "approval": orders.approval_state(ctx, r, user)})
    resp = render(request, "screens/orders.html", {"queue": queue, "q": q, "sort": sort, "items": items, "total": total,
                                                    "page": max(page, 1), "counts": counts})
    # EXP-002: 상세의 “목록으로”가 보던 큐·검색어로 돌아가도록 마지막 목록 URL을 기억
    resp.set_cookie("jos_list", str(request.url.path) + ("?" + request.url.query if request.url.query else
                                                          f"?queue={queue}"), httponly=True, samesite="strict")
    return resp


@router.get("/favicon.ico")
async def favicon(request: Request):
    from fastapi.responses import FileResponse
    from .app import HERE
    return FileResponse(HERE / "static" / "img" / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "max-age=86400"})


@router.get("/sw.js")
async def service_worker(request: Request):
    from fastapi.responses import FileResponse
    from .app import HERE
    return FileResponse(HERE / "static" / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.post("/system/ack-startup")
async def ack_startup(request: Request):
    f = await form_of(request)
    system.ack_startup(request.state.ctx, int(f.get("id")), request.state.user)
    return flash_redirect("/", "확인했어요. 기동 기록은 시스템 상태 화면에 남아 있어요.")
