import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["JOS_ENV"] = "test"

from jewelry_os.adapters import google as g  # noqa: E402
from jewelry_os.adapters.ai import MockAI  # noqa: E402
from jewelry_os.bootstrap import build_ctx, run_setup  # noqa: E402
from jewelry_os.core import clock  # noqa: E402
from jewelry_os.core.config import load_config  # noqa: E402
from jewelry_os.core.security import User  # noqa: E402
from jewelry_os.services import settings  # noqa: E402

T0 = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)  # 2026-10-20(화) 11:00 KST — 평일·휴무 아님

MAKER_PW = "maker-pass-123"
OPER_PW = "oper-pass-1234"


@pytest.fixture
def fclock():
    c = clock.FixedClock(T0)
    clock.set_clock(c)
    yield c
    clock.set_clock(clock.Clock())


@pytest.fixture
def ctx(tmp_path, fclock):
    cfg = load_config("test", tmp_path / "data")
    c = build_ctx(cfg, forms=g.MockForms(), files=g.MockDrive(), analyzer=MockAI())
    run_setup(c, {"username": "father", "display_name": "김장인", "password": MAKER_PW},
              {"username": "ops", "display_name": "김운영", "password": OPER_PW})
    yield c
    c.db.close()


def user(ctx, role) -> User:
    r = ctx.db.one("SELECT * FROM users WHERE role=?", (role,))
    return User(r["id"], r["username"], r["display_name"], r["role"], {})


@pytest.fixture
def maker(ctx):
    return user(ctx, "maker")


@pytest.fixture
def operator(ctx):
    return user(ctx, "operator")


def pass_all_gates(ctx, operator, skip=()):
    from jewelry_os.domain.states import GATES
    for gid, _, _ in GATES:
        if gid not in skip:
            settings.record_gate(ctx, gid, True, "시험 증거", operator)


def answers(name="김하늘", desc="18K 금으로 12호 반지를 만들고 싶어요. 예산 50만원", consent=True, files=None,
            product="반지", source="스마트스토어", contact="010-1234-5678"):
    a = dict([g.text_answer("q01_display_name", name), g.text_answer("q02_contact_kind", "카카오톡"),
              g.text_answer("q02_contact_value", contact), g.text_answer("q03_product_type", product),
              g.text_answer("q11_source_channel", source)])
    if desc:
        a.update([g.text_answer("q04_description", desc)])
    if consent:
        a.update([g.text_answer("q12_consent", "동의합니다")])
    if files:
        a.update([g.files_answer("q10_reference_files", [(f, f"ref{i}.jpg") for i, f in enumerate(files)])])
    return a


def add_response(ctx, rid, ts="2026-10-20T01:00:00Z", **kw):
    ctx.forms.add(rid, answers(**kw), ts)


def approve_policies(ctx, operator):
    for p in ctx.db.all("SELECT id FROM policies WHERE status='DRAFT'"):
        settings.approve_policy(ctx, p['id'], operator)



# 브라우저 시험 픽스처(tests/bfix.py)
from bfix import browser, live, new_page  # noqa: E402,F401
