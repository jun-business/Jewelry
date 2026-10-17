"""모든 화면이 렌더되는지(500 없음) 확인하는 스모크."""
from webutil import web

from jewelry_os.devtools import seed_demo


def test_all_pages_render(ctx):
    nos = seed_demo(ctx)
    for role in ("maker", "operator"):
        w = web(ctx, role)
        urls = ["/", "/orders", "/orders?queue=all", "/orders?queue=ended", "/orders/new", "/payments", "/payments?tab=recv",
                "/payments?tab=issues", "/payments?tab=events", "/cs", "/schedule", "/customers", "/analytics", "/system"]
        urls += [f"/settings/{s}" for s in ("display", "policy", "calendar", "policies", "google", "ai", "backup",
                                            "accounts", "gates", "mapping")]
        for no in nos:
            for tab in ("intake", "quote", "payment", "cs", "making", "qc", "history", "time"):
                urls.append(f"/orders/{no}/{tab}")
        cid = ctx.db.scalar("SELECT id FROM customers LIMIT 1")
        urls.append(f"/customers/{cid}")
        q = ctx.db.scalar("SELECT id FROM quotes WHERE status='APPROVED' LIMIT 1")
        urls += [f"/docs/quote/{q}", f"/docs/quote/{q}.txt"]
        for u in urls:
            r = w.get(u)
            assert r.status_code == 200, (role, u, r.status_code, r.text[-2000:])
