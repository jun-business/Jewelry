"""시험 공용 도우미."""
from __future__ import annotations

from datetime import timedelta

from conftest import add_response, approve_policies, pass_all_gates
from jewelry_os.core import clock
from jewelry_os.devtools import advance
from jewelry_os.domain import transitions as tr
from jewelry_os.services import intake, orders

C = {"confirm": "on"}


def new_order(ctx, rid="r1", **kw) -> str:
    add_response(ctx, rid, **kw)
    intake.sync_once(ctx)
    intake.fetch_pending_files(ctx)
    return ctx.db.scalar("SELECT o.order_no FROM orders o JOIN intake_responses i ON i.id=o.intake_id "
                         "WHERE i.response_id=?", (rid,))


def order_at(ctx, maker, operator, target, rid="r1", live=False, **kw) -> dict:
    approve_policies(ctx, operator)
    if live:
        pass_all_gates(ctx, operator)
    no = new_order(ctx, rid, **kw)
    if target == "OWNER_REVIEW":
        return orders.get(ctx, no)
    return advance(ctx, no, target, maker, operator)


def get(ctx, no):
    return orders.get(ctx, no)


def audits(ctx, order_id, action=None):
    q = "SELECT * FROM audit_events WHERE order_id=?" + (" AND action=?" if action else "") + " ORDER BY id"
    return ctx.db.all(q, (order_id, action) if action else (order_id,))


def codes(unmet_list):
    return {u.code for u in unmet_list}


def check(ctx, o, action, actor, payload=None, submitting=False):
    return codes(tr.check(ctx, o["id"], action, actor, payload, submitting))


def advance_clock(fclock, **kw):
    fclock.advance(**kw)
