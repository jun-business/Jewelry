"""TC-S-003·007·019·041·042·051 — 브라우저 시험(B-02·B-04·B-07·B-08)."""
import json
import re
from datetime import timedelta

import pytest
from helpers import C, get, new_order, order_at

from bfix import AXE, SHOTS, login
from conftest import T0, add_response, approve_policies
from jewelry_os.adapters import google as g
from jewelry_os.core import clock
from jewelry_os.devtools import advance
from jewelry_os.services import backup, intake, orders

pytestmark = pytest.mark.browser
CHANGE_NO = None
WIDTHS = [320, 390, 768, 960, 1366, 1920]  # SRS-153
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa", "best-practice"]


def seed_screens(ctx, maker, operator):
    ctx.files.files["i1"] = g.sample_jpeg("i1")
    ctx.files.files["i2"] = g.sample_jpeg("i2")
    a = order_at(ctx, maker, operator, "OWNER_REVIEW", rid="a", files=["i1", "i2"])
    b = order_at(ctx, maker, operator, "PAYMENT_PENDING", rid="b", name="박민수")
    c = order_at(ctx, maker, operator, "CUSTOMER_DESIGN_APPROVED", rid="c", name="이서연")
    qid = ctx.db.scalar("SELECT id FROM quotes WHERE order_id=? AND status='APPROVED'", (b["id"],))
    d = order_at(ctx, maker, operator, "FACTORY_RECEIVED", rid="d", name="최변경")  # H1 r1 Q10 변경 주문 카드
    orders.open_issue_action(ctx, d, maker, "REWORK", "각인 문구 변경 요청", "CUSTOMER_CHANGE")
    from jewelry_os.services import settings
    settings.put(ctx, "capacity_limit", 3, operator)  # 활성 b·c·d = 3 → a는 상한 초과 예외 승인 화면(Q2)
    global CHANGE_NO
    CHANGE_NO = d["order_no"]
    return a["order_no"], b["order_no"], c["order_no"], qid


def screens(a, b, c, qid):
    return [("dashboard", "/"), ("orders", "/orders"), ("order-intake", f"/orders/{a}"), ("order-quote", f"/orders/{b}/quote"),
            ("order-payment", f"/orders/{b}/payment"), ("order-making", f"/orders/{c}/making"), ("payments", "/payments"),
            ("cs", "/cs"), ("schedule", "/schedule"), ("customers", "/customers"), ("analytics", "/analytics"),
            ("settings", "/settings/policy"), ("system", "/system"), ("quote-doc", f"/docs/quote/{qid}"),
            ("order-change", f"/orders/{CHANGE_NO}/qc"), ("settings-gates", "/settings/gates"),
            ("settings-backup", "/settings/backup")]


OVERFLOW_JS = """() => {
  const cw = document.documentElement.clientWidth;
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('[data-scroll-x], .table-wrap, dialog:not([open]), [hidden], .sr-only')) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.position === 'fixed' && el.closest('[hidden]')) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > cw + 0.5) bad.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + el.className).slice(0, 60) + ' ' + Math.round(r.right));
  }
  return {sw: document.documentElement.scrollWidth, cw, bad: bad.slice(0, 5)};
}"""


# ---------------- 041 반응형 6폭 × 화면 ----------------
def test_tcs_041_responsive(ctx, maker, operator, live, new_page):
    a, b, c, qid = seed_screens(ctx, maker, operator)
    for w in WIDTHS:
        page = new_page(w, 900)
        login(page, live, "maker")
        (SHOTS / str(w)).mkdir(parents=True, exist_ok=True)
        for name, url in screens(a, b, c, qid):
            page.goto(live + url)
            page.wait_for_load_state("networkidle")
            res = page.evaluate(OVERFLOW_JS)
            assert res["sw"] <= res["cw"], (w, name, res)
            assert not res["bad"], (w, name, res["bad"])
            squeezed = page.evaluate("""() => [...document.querySelectorAll('ul.timeline li, ul.notif-list li')]
                .filter(li => li.offsetParent).flatMap(li => [...li.children].length ? [...li.children] : [li])
                .filter(el => el.textContent.trim().length > 3 && el.getBoundingClientRect().width < 60)
                .map(el => el.textContent.trim().slice(0, 20))""")
            assert squeezed == [], (w, name, squeezed)  # 글자가 한 자씩 세로로 늘어서는 칸(28px 아이콘 칸 갇힘) 없음
            if name == "settings":  # 설정 메뉴 탭: 글자가 줄바꿈되지 않고, 600px 이상에서는 넘치지 않는다
                tabs = page.evaluate("""() => { const ul = document.querySelector('.settings-grid .queue-tabs');
                    const links = [...ul.querySelectorAll('a')];
                    return {overflow: ul.scrollWidth > ul.clientWidth + 0.5,
                            wrapped: links.filter(a => a.getClientRects().length > 1 || a.offsetHeight > 48).map(a => a.textContent),
                            bullets: getComputedStyle(ul).listStyleType}; }""")
                assert tabs["wrapped"] == [] and tabs["bullets"] == "none", (w, tabs)
                if w >= 600:
                    assert not tabs["overflow"], (w, tabs)
            if name.startswith("order-"):
                sel = ".action-bar .btn-primary" if w < 600 else ".next-card .btn-primary"
                loc = page.locator(sel).first
                if loc.count():
                    box = loc.bounding_box()
                    assert box and box["x"] >= 0 and box["x"] + box["width"] <= res["cw"] + 0.5, (w, name, box)
                    if w < 600:
                        assert box["y"] + box["height"] <= 900, (w, name, box)
            page.screenshot(path=str(SHOTS / str(w) / f"{name}.png"), full_page=True)


# ---------------- 042 접근성 axe + 키보드·글자 크기 ----------------
def test_tcs_042_accessibility(ctx, maker, operator, live, new_page):
    a, b, c, qid = seed_screens(ctx, maker, operator)
    report = {}
    for role in ("maker", "operator"):
        page = new_page(1280, 900, bypass_csp=True)  # axe 주입용(시험 도구만 CSP 우회)
        login(page, live, role)
        for name, url in screens(a, b, c, qid):
            page.goto(live + url)
            page.wait_for_load_state("networkidle")
            page.add_script_tag(path=str(AXE))
            r = page.evaluate("tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", AXE_TAGS)
            v = [{"id": x["id"], "impact": x["impact"], "nodes": [n["target"] for n in x["nodes"]][:3]} for x in r["violations"]]
            report[f"{role}:{name}"] = v
    (SHOTS.parent / "axe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = {k: v for k, v in report.items() if v}
    assert not bad, json.dumps(bad, ensure_ascii=False)[:3000]
    # 키보드 도달·포커스 표시·글자 크기
    page = new_page(1280, 900)
    login(page, live, "maker")
    page.goto(f"{live}/orders/{a}")
    seen = []
    for _ in range(25):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => { const e = document.activeElement; const s = getComputedStyle(e);
            return {tag: e.tagName, text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 20),
                    ow: parseFloat(s.outlineWidth), os: s.outlineStyle}; }""")
        seen.append(info)
        assert info["os"] != "none" and info["ow"] >= 2, info
    assert any("수락" in s["text"] for s in seen) or any(s["tag"] == "A" for s in seen)
    sizes = page.evaluate("""() => ({body: parseFloat(getComputedStyle(document.body).fontSize),
        meta: Math.min(...[...document.querySelectorAll('.meta, .t-caption')].filter(e => e.offsetParent)
              .map(e => parseFloat(getComputedStyle(e).fontSize)))})""")
    assert sizes["body"] >= 16 and sizes["meta"] >= 13, sizes
    page.goto(f"{live}/settings/display")
    page.check("input[name=text][value=large]")
    page.click("form[action='/settings/display'] button[type=submit]")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)") >= 18


# ---------------- 051 결과 메시지·확대 ----------------
def test_tcs_051_notification_panel(ctx, maker, operator, live, new_page):
    """알림 패널: 항목 글자가 한 자씩 줄바꿈되지 않고(아이콘 + 제목 + 시각), 휴대폰 폭에서도 읽힌다."""
    order_at(ctx, maker, operator, "QUOTE_APPROVED")
    from jewelry_os.services import alerts
    alerts.evaluate(ctx)
    assert ctx.db.scalar("SELECT COUNT(*) FROM notifications WHERE resolved_at IS NULL") >= 1
    for w in (1280, 320):
        page = new_page(w, 800)
        login(page, live, "maker")
        page.click("#bell")
        page.wait_for_selector("#notif-dialog[open] .notif-list li")
        rows = page.evaluate("""() => [...document.querySelectorAll('#notif-dialog .notif-list li')].map(li => {
            const t = li.querySelector('a, span'); const r = t.getBoundingClientRect();
            const lh = parseFloat(getComputedStyle(t).lineHeight) || 24;
            return {text: t.textContent, width: r.width, lines: Math.round(r.height / lh),
                    icon: !!li.querySelector('svg.icon'), time: (li.querySelector('.meta') || {}).textContent}; })""")
        assert rows, w
        for r in rows:
            assert r["icon"] and r["time"], (w, r)
            assert r["width"] > 150 and r["lines"] <= 3, (w, r)  # 한 자씩 세로로 늘어서면 폭이 좁고 줄 수가 많다


def test_tcs_051_status_and_lightbox(ctx, maker, operator, live, new_page):
    a, *_ = seed_screens(ctx, maker, operator)
    page = new_page(1280, 900)
    login(page, live, "maker")
    page.goto(f"{live}/orders/{a}/history")
    page.fill("textarea[name=memo]", "브라우저 메모")
    page.click("form[action$='/memo'] button[type=submit]")
    page.wait_for_load_state("networkidle")
    st = page.locator("#toasts [role=status]")
    assert st.count() >= 1 and st.first.inner_text().strip()
    page.goto(f"{live}/orders/{a}")
    thumb = page.locator("[data-lightbox]").first
    thumb.click()
    assert page.locator("#lightbox").evaluate("d => d.open")
    assert page.evaluate("document.activeElement.classList.contains('lb-close')")
    page.keyboard.press("Escape")
    assert not page.locator("#lightbox").evaluate("d => d.open")
    assert page.evaluate("document.activeElement.hasAttribute('data-lightbox')")


# ---------------- 003 두 번째 이후 — 목록 복귀와 큐 ----------------
def test_tcs_003_list_return(ctx, maker, operator, live, new_page):
    approve_policies(ctx, operator)
    plan = [("FREE_CONCEPT", "김하늘"), ("QUOTE_APPROVED", "김하나"), ("CUSTOMER_ACCEPTED", "박민수"),
            ("OWNER_REVIEW", "김하린"), ("PAYMENT_PENDING", "최결제"), ("FACTORY_SENT", "정제작"),
            ("QC_PASSED", "한검수"), ("FINAL_PAYMENT_VERIFIED", "윤배송"), ("OWNER_REVIEW", "오정보"),
            ("OWNER_REVIEW", "장대기"), ("DETAIL_CAD", "문이슈"), ("CLOSED", "고종료")]
    nos = {}
    for i, (stt, name) in enumerate(plan):
        o = order_at(ctx, maker, operator, stt, rid=f"q{i}", name=name)
        nos[name] = o["order_no"]
    with ctx.db.tx() as c:
        orders.set_hold(c, get(ctx, nos["오정보"])["id"], "NEEDS_INFO", maker, "t")
        orders.set_hold(c, get(ctx, nos["장대기"])["id"], "WAITLISTED", None, "t")
        orders.open_issue(c, get(ctx, nos["문이슈"])["id"], "DELIVERY_DELAY", maker, "t")
    page = new_page(1280, 900)
    login(page, live, "maker")
    page.goto(f"{live}/orders")
    counts = orders.queue_counts(ctx)
    for key, n in counts.items():
        assert page.locator(f"[data-queue-count={key}]").inner_text().strip() == str(n), key  # ①
    page.click("a[href^='/orders?queue=concept']")
    page.fill("#q", "김하")
    page.click("form.filterbar button[type=submit]")
    page.wait_for_load_state("networkidle")
    assert "queue=concept" in page.url and "q=%EA%B9%80%ED%95%98" in page.url or "q=김하" in page.url  # ③

    def rows():
        return [r.get_attribute("data-order") for r in page.locator("table tbody tr").all()]
    first = rows()
    assert sorted(first) == sorted([nos["김하늘"], nos["김하나"]])  # ④
    cells = page.locator("table tbody tr").first.locator("td").count()
    assert cells >= 7  # ② 행마다 7개 정보 이상
    url1 = page.url
    page.locator("table tbody tr a").first.click()
    page.wait_for_load_state("networkidle")
    page.go_back()
    page.wait_for_load_state("networkidle")
    assert page.url == url1 and rows() == first  # ⑤
    page.locator("table tbody tr a").first.click()
    page.wait_for_load_state("networkidle")
    page.click("a:has-text('목록으로')")
    page.wait_for_load_state("networkidle")
    assert page.url.split("?")[1].count("queue=concept") == 1 and rows() == first  # ⑥
    p2 = page.context.new_page()
    p2.goto(url1)
    assert [r.get_attribute("data-order") for r in p2.locator("table tbody tr").all()] == first  # ⑦
    tail = nos["윤배송"][-4:]
    page.goto(f"{live}/orders?queue=all&q={tail}")
    assert rows() == [nos["윤배송"]]  # ⑧


# ---------------- 007 세션 잠금 중 입력 보존 ----------------
def test_tcs_007_lock_preserves_input(ctx, maker, operator, live, new_page, fclock):
    o = order_at(ctx, maker, operator, "FREE_CONCEPT")
    no = o["order_no"]
    from jewelry_os.services import quotes
    quotes.upload_concept(ctx, o, maker, "c.png", b"\x89PNG", "", 10)
    quotes.draft(ctx, get(ctx, no), maker)
    page = new_page(1280, 900)
    page.clock.install(time=clock.now())
    login(page, live, "maker", f"/orders/{no}/quote")
    page.goto(f"{live}/orders/{no}/quote")
    page.fill("input[name=item_0_label]", "18K 금")
    page.fill("input[name=item_0_amount]", "900000")
    page.fill("input[name=item_1_label]", "설계비")

    def tick(minutes):
        page.clock.run_for(int(minutes * 60_000))
        fclock.advance(seconds=int(minutes * 60))
    tick(14)
    assert page.locator("#lock-warning").is_visible()  # ①
    assert "1분 뒤 화면이 잠겨요" in page.locator("#lock-warning").inner_text()
    tick(1)
    assert page.locator("#lock-overlay").is_visible()  # ②
    assert page.locator(".shell").get_attribute("inert") is not None
    assert page.request.get(f"{live}/api/notifications").status == 401  # ③
    page.fill("#lock-password", "maker-pass-123")
    page.click("#lock-form button[type=submit]")
    page.locator("#lock-overlay").wait_for(state="hidden")
    assert page.input_value("input[name=item_0_label]") == "18K 금"  # ④
    assert page.input_value("input[name=item_0_amount]") == "900000"
    msgs = []

    def on_dialog(d):
        msgs.append((d.type, d.message))
        d.dismiss()
    page.on("dialog", on_dialog)
    page.click("nav a[href='/orders']")
    page.wait_for_timeout(500)
    assert len(msgs) == 1 and "저장하지 않은 내용이 있어요" in msgs[0][1] and page.url.endswith("/quote")  # ⑤ → 취소하면 머무름
    page.remove_listener("dialog", on_dialog)
    page.on("dialog", lambda d: d.accept())
    # 절대 만료(12시간): 화면 경고 → 만료 → 로그인 후 원래 URL
    page.clock.fast_forward("11:50:00")  # 1초 타이머를 4만 번 돌리지 않고 건너뛴다
    fclock.advance(minutes=11 * 60 + 50)
    page.clock.run_for(2000)
    toasts = page.locator("#toasts").inner_text()
    assert "10분 뒤 다시 로그인해야 해요" in toasts  # ⑥
    fclock.advance(minutes=11)
    r = page.request.get(f"{live}/orders/{no}/quote", max_redirects=0)
    assert r.status == 303 and "/login?next=" in r.headers["location"]  # ⑧
    assert page.request.get(f"{live}/api/notifications").status == 401
    page.goto(f"{live}/orders/{no}/quote")
    assert "/login" in page.url
    page.fill("#u", "father")
    page.fill("#p", "maker-pass-123")
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    assert page.url.endswith(f"/orders/{no}/quote")  # ⑦


# ---------------- 019 하루 시작 — 경고 해결 ----------------
def test_tcs_019_morning_warnings(ctx, maker, operator, live, new_page, fclock):
    ctx.files.files["i1"] = g.sample_jpeg("i1")
    ctx.files.files["i2"] = g.sample_jpeg("i2")
    a = order_at(ctx, maker, operator, "OWNER_REVIEW", files=["i1", "i2"])
    backup.make_backup(ctx)
    with ctx.db.tx() as c:
        c.execute("UPDATE backups SET created_at=?", (clock.iso(clock.now() - timedelta(hours=27)),))
        c.execute("UPDATE sync_cursors SET last_success_at=?", (clock.iso(clock.now() - timedelta(minutes=16)),))
    from jewelry_os.services import alerts
    alerts.evaluate(ctx)
    page = new_page(1280, 900)
    login(page, live, "maker")
    warns = page.locator(".sys-row .led.warning")
    assert warns.count() == 2  # ①
    links = [x.get_attribute("href") for x in page.locator(".sys-row a").all()]
    assert "/settings/backup" in links and "/system" in links  # ②
    page.click(".sys-row a[href='/settings/backup']")
    page.click("form[action='/system/backup-now'] button")
    page.wait_for_load_state("networkidle")
    assert page.locator("#toasts [role=status]").count() >= 1  # ⑤
    page.goto(f"{live}/")
    page.click(".sys-row a[href='/system']")
    page.click("form[action='/system/sync-now'] button")
    page.wait_for_load_state("networkidle")
    assert page.locator("#toasts [role=status]").count() >= 1
    page.goto(f"{live}/")
    assert page.locator(".sys-row .led.warning").count() == 0  # ③
    page.goto(f"{live}/orders/{a['order_no']}")
    t = page.locator("[data-lightbox]").nth(1)
    t.click()
    assert page.locator("#lightbox").evaluate("d => d.open")
    page.keyboard.press("Escape")
    assert not page.locator("#lightbox").evaluate("d => d.open")
    assert page.evaluate("document.activeElement === document.querySelectorAll('[data-lightbox]')[1]")  # ④
    page.goto(f"{live}/orders/{a['order_no']}/history")
    page.fill("textarea[name=memo]", "아침 확인")
    page.click("form[action$='/memo'] button[type=submit]")
    page.wait_for_load_state("networkidle")
    assert page.locator("#toasts [role=status]").count() >= 1
