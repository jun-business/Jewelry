/* 오프라인 셸(PDS E-27): 로컬 서버가 응답하지 않으면 안내 화면만 보여 준다. 데이터는 캐시하지 않는다. */
const CACHE = "jos-shell-v1";
const OFFLINE = "/static/offline.html";
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll([OFFLINE, "/static/css/tokens.css", "/static/css/app.css"]))); self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE)));
});
