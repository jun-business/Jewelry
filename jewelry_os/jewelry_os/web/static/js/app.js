/* Jewelry OS — 점진적 향상(PDS §13.1). JS가 없어도 모든 승인·저장은 폼 POST로 동작한다. */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const body = document.body;
  const CSRF = body.dataset.csrf || "";
  const LOCK_MIN = parseInt(body.dataset.lockMinutes || "15", 10);
  const SESSION_CREATED = body.dataset.sessionCreated ? Date.parse(body.dataset.sessionCreated) : null;
  const ABS_MS = 12 * 3600 * 1000;
  const live = $("#live");

  function announce(msg, assertive) {
    if (!live) return;
    live.setAttribute("aria-live", assertive ? "assertive" : "polite");
    live.textContent = "";
    setTimeout(() => { live.textContent = msg; }, 30);
  }
  function api(url, opts = {}) {
    opts.headers = Object.assign({ "X-CSRF-Token": CSRF, "X-Requested-With": "fetch" }, opts.headers || {});
    opts.credentials = "same-origin";
    return fetch(url, opts);
  }
  function toast(msg, kind) {
    const box = $("#toasts");
    if (!box) return;
    const t = document.createElement("div");
    t.className = "toast " + (kind || "success");
    t.setAttribute("role", kind === "error" ? "alert" : "status");
    t.innerHTML = '<span class="t-body"></span><button type="button" class="icon-btn" aria-label="알림 닫기">×</button>';
    t.querySelector(".t-body").textContent = msg;
    t.querySelector("button").onclick = () => t.remove();
    box.appendChild(t);
    if (kind !== "error") setTimeout(() => t.remove(), 4000);
  }
  window.josToast = toast;

  /* ---- 플래시 토스트 ---- */
  $$(".toast[data-auto]").forEach((t) => setTimeout(() => t.remove(), 4000));

  /* ---- 세션 잠금(DD-007, EXP-006) ---- */
  if (body.dataset.authed === "1") {
    let last = Date.now(), lastPing = 0, warned = false, locked = false, absWarned = false, trailing = null;
    const overlay = $("#lock-overlay"), warn = $("#lock-warning");
    const mainEls = [$(".shell"), $("#global-banners")].filter(Boolean);
    function activity() {
      if (locked) return;
      last = Date.now();
      if (warned) { warned = false; if (warn) warn.hidden = true; }
      const send = () => { lastPing = Date.now(); trailing = null; api("/api/session/ping").catch(() => {}); };
      if (Date.now() - lastPing > 60000) send();
      else if (!trailing) trailing = setTimeout(send, 60000 - (Date.now() - lastPing)); /* 마지막 활동 뒤 한 번 더 — 서버가 화면보다 먼저 잠그지 않게 */
    }
    ["keydown", "mousedown", "pointermove", "touchstart", "wheel"].forEach((e) => document.addEventListener(e, activity, { passive: true }));
    function lockNow() {
      locked = true;
      api("/api/session/lock", { method: "POST" }).catch(() => {});
      if (warn) warn.hidden = true;
      if (overlay) { overlay.hidden = false; mainEls.forEach((el) => el.setAttribute("inert", "")); const pw = $("#lock-password"); if (pw) pw.focus(); }
    }
    window.josLockNow = lockNow;
    setInterval(() => {
      const idle = Date.now() - last;
      if (!locked && idle >= LOCK_MIN * 60000) lockNow();
      else if (!locked && !warned && idle >= (LOCK_MIN * 60000 - 60000)) {
        warned = true; if (warn) { warn.hidden = false; $("#lock-continue").focus(); } announce("1분 뒤 화면이 잠겨요", true);
      }
      if (SESSION_CREATED && !absWarned && Date.now() - SESSION_CREATED > ABS_MS - 10 * 60000) {
        absWarned = true; toast("10분 뒤 다시 로그인해야 해요. 저장하지 않은 내용을 저장하세요.", "error");
      }
    }, 1000);
    const cont = $("#lock-continue");
    if (cont) cont.onclick = () => { activity(); lastPing = 0; activity(); };
    const lf = $("#lock-form");
    if (lf) lf.addEventListener("submit", async (e) => {
      e.preventDefault();
      const pw = $("#lock-password");
      const r = await api("/api/session/unlock", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw.value }) });
      if (r.ok) {
        locked = false; last = Date.now(); pw.value = ""; overlay.hidden = true;
        mainEls.forEach((el) => el.removeAttribute("inert")); $("#lock-error").textContent = "";
        announce("잠금을 풀었어요");
      } else { $("#lock-error").textContent = "비밀번호가 맞지 않아요."; pw.focus(); }
    });
  }

  /* ---- 저장 안 한 변경 경고(IMPLICIT-02) ---- */
  let dirty = false;
  $$("form[data-guard]").forEach((f) => {
    f.addEventListener("input", () => { dirty = true; });
    f.addEventListener("submit", () => { dirty = false; });
  });
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || !dirty || a.target === "_blank" || a.dataset.noguard !== undefined) return;
    if (!confirm("저장하지 않은 내용이 있어요. 나갈까요?")) { e.preventDefault(); } else { dirty = false; }
  });

  /* ---- 대화상자(C-11/C-12) ---- */
  $$("[data-open]").forEach((b) => b.addEventListener("click", (e) => {
    const d = document.getElementById(b.dataset.open);
    if (!d || typeof d.showModal !== "function") return;
    e.preventDefault(); d._opener = b; d.showModal();
    const h = d.querySelector("h2, h3"); if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
  }));
  $$("dialog").forEach((d) => {
    d.addEventListener("close", () => { if (d._opener) d._opener.focus(); });
    $$("[data-close]", d).forEach((b) => b.addEventListener("click", () => d.close()));
  });
  /* 확인 체크 전에는 승인 버튼을 aria-disabled로 (서버도 G-CONFIRM으로 막음) */
  $$("form[data-confirm-required]").forEach((f) => {
    const cb = f.querySelector('input[name="confirm"]'), btn = f.querySelector("[data-primary]");
    if (!cb || !btn) return;
    const sync = () => btn.setAttribute("aria-disabled", cb.checked ? "false" : "true");
    cb.addEventListener("change", sync); sync();
    f.addEventListener("submit", (e) => { if (!cb.checked && (!e.submitter || e.submitter.hasAttribute("data-primary"))) { e.preventDefault(); const m = f.querySelector(".need-confirm"); if (m) m.hidden = false; cb.focus(); } });
  });
  /* aria-disabled 버튼은 동작 대신 이유로 이동 */
  document.addEventListener("click", (e) => {
    const b = e.target.closest('[aria-disabled="true"]');
    if (!b || b.closest("form[data-confirm-required]")) return;
    e.preventDefault();
    const id = (b.getAttribute("aria-describedby") || "").split(" ")[0];
    const r = id && document.getElementById(id);
    if (r) { r.scrollIntoView({ block: "center" }); r.classList.add("flash"); announce(r.textContent.trim()); }
  }, true);

  /* ---- 복사(C-21) ---- */
  $$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    if (b.getAttribute("aria-disabled") === "true") return;
    const src = document.getElementById(b.dataset.copy);
    const text = src ? (src.value || src.textContent) : "";
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (_) { if (src && src.select) { src.focus(); src.select(); } }
    if (ok) {
      const old = b.innerHTML; b.textContent = "복사됨 ✓"; announce("복사됐어요"); setTimeout(() => { b.innerHTML = old; }, 2000);
      if (b.dataset.copyLog) api(b.dataset.copyLog, { method: "POST" }).catch(() => {});
    } else announce("Ctrl+C를 눌러 복사하세요", true);
  }));

  /* ---- 이미지 확대(C-27) ---- */
  const lb = $("#lightbox");
  if (lb) {
    const img = $("img", lb), cap = $(".lb-cap", lb);
    let list = [], idx = 0, scale = 1;
    const show = () => { const t = list[idx]; img.src = t.dataset.full; img.alt = t.dataset.alt; cap.textContent = t.dataset.alt; scale = 1; img.style.transform = ""; };
    $$("[data-lightbox]").forEach((t, i, all) => t.addEventListener("click", () => { list = all; idx = i; show(); lb._opener = t; lb.showModal(); $(".lb-close", lb).focus(); }));
    lb.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") { idx = (idx + 1) % list.length; show(); }
      if (e.key === "ArrowLeft") { idx = (idx - 1 + list.length) % list.length; show(); }
      if (e.key === "+" || e.key === "=") { scale = Math.min(4, scale + 0.25); img.style.transform = `scale(${scale})`; }
      if (e.key === "-") { scale = Math.max(1, scale - 0.25); img.style.transform = `scale(${scale})`; }
    });
    $$("[data-zoom]", lb).forEach((b) => b.addEventListener("click", () => { scale = Math.max(1, Math.min(4, scale + parseFloat(b.dataset.zoom))); img.style.transform = `scale(${scale})`; }));
    $$("[data-step]", lb).forEach((b) => b.addEventListener("click", () => { idx = (idx + parseInt(b.dataset.step, 10) + list.length) % list.length; show(); }));
  }

  /* ---- 전역 검색(C-23) ---- */
  const sIn = $("#global-search"), sOut = $("#search-results");
  if (sIn && sOut) {
    let timer, sel = -1;
    sIn.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = sIn.value.trim(); sOut.innerHTML = ""; sel = -1;
        if (!q) { sOut.hidden = true; sIn.setAttribute("aria-expanded", "false"); return; }
        const r = await api("/api/search?q=" + encodeURIComponent(q)); const j = await r.json();
        j.items.forEach((it, i) => { const li = document.createElement("li"); li.innerHTML = `<a role="option" id="sr-${i}" href="/orders/${it.order_no}"></a>`; li.firstChild.textContent = `${it.order_no} · ${it.customer || ""} · ${it.product || ""}`; sOut.appendChild(li); });
        const li = document.createElement("li"); li.innerHTML = `<a href="/orders?queue=all&q=${encodeURIComponent(q)}">모든 결과 보기</a>`; sOut.appendChild(li);
        sOut.hidden = false; sIn.setAttribute("aria-expanded", "true");
      }, 200);
    });
    sIn.addEventListener("keydown", (e) => {
      const opts = $$("a", sOut);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + opts.length) % opts.length;
        opts.forEach((o, i) => o.setAttribute("aria-selected", i === sel ? "true" : "false"));
        if (opts[sel]) sIn.setAttribute("aria-activedescendant", opts[sel].id || "");
      } else if (e.key === "Enter" && sel >= 0 && opts[sel]) { e.preventDefault(); location.href = opts[sel].href; }
      else if (e.key === "Escape") { sOut.hidden = true; sIn.setAttribute("aria-expanded", "false"); }
    });
    document.addEventListener("keydown", (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (e.key === "/" && !["input", "textarea", "select"].includes(tag) && !e.target.isContentEditable) { e.preventDefault(); sIn.focus(); }
    });
  }

  /* ---- 메뉴·알림 센터 ---- */
  const mb = $("#menu-btn"), sb = $(".sidebar");
  if (mb && sb) mb.addEventListener("click", () => { const o = sb.classList.toggle("open"); mb.setAttribute("aria-expanded", o ? "true" : "false"); if (o) { const a = $("a", sb); if (a) a.focus(); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sb && sb.classList.contains("open")) { sb.classList.remove("open"); mb && mb.focus(); } });
  const bell = $("#bell"), np = $("#notif-dialog");
  if (bell && np) bell.addEventListener("click", async (e) => {
    e.preventDefault();
    const r = await api("/api/notifications"); const j = await r.json(); const ul = $("ul", np); ul.innerHTML = "";
    if (!j.items.length) ul.innerHTML = "<li class='t-muted'>새 알림이 없어요.</li>";
    const ICON = { customer_wait: "clock", delivery_risk: "alert", payment_issue: "octagon", system: "server" };
    const ago = (iso) => { const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
      return m < 1 ? "방금 전" : m < 60 ? m + "분 전" : m < 1440 ? Math.floor(m / 60) + "시간 전" : Math.floor(m / 1440) + "일 전"; };
    j.items.forEach((it) => {
      const li = document.createElement("li"); li.className = "sev-" + (it.severity || "info");
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
      const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", "/static/icons.svg#i-" + (ICON[it.kind] || "info")); svg.appendChild(use);
      const body = document.createElement("div");
      const a = document.createElement(it.order_no ? "a" : "span"); if (it.order_no) a.href = "/orders/" + it.order_no; a.textContent = it.title;
      const meta = document.createElement("span"); meta.className = "meta"; meta.textContent = it.at ? ago(it.at) : "";
      body.append(a, meta); li.append(svg, body); ul.appendChild(li);
    });
    np._opener = bell; np.showModal(); api("/api/notifications/read", { method: "POST" }).then(() => { const b = $(".badge", bell); if (b) b.remove(); });
  });

  /* ---- 대시보드 숫자 갱신(60초, 포커스가 카드에 있으면 보류) ---- */
  const dashRoot = $("[data-dashboard]");
  if (dashRoot) setInterval(async () => {
    if (dashRoot.contains(document.activeElement)) return;
    try { const r = await api("/api/dashboard/summary"); if (!r.ok) return; const j = await r.json();
      $$("[data-queue-count]").forEach((el) => { const k = el.dataset.queueCount; if (k in j.queues) el.textContent = j.queues[k]; });
    } catch (_) {}
  }, 60000);

  /* ---- 드롭존 ---- */
  $$(".dropzone").forEach((z) => {
    const input = $("input[type=file]", z); if (!input) return;
    ["dragenter", "dragover"].forEach((ev) => z.addEventListener(ev, (e) => { e.preventDefault(); z.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => z.addEventListener(ev, (e) => { e.preventDefault(); z.classList.remove("drag"); }));
    z.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; announce(e.dataTransfer.files[0].name + " 선택됨"); } });
  });

  /* ---- 서비스 워커(E-27 오프라인 셸) ---- */
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
})();
