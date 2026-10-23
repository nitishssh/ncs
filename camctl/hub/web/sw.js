// Network-first; the cached shell only makes the app open instantly. Camera data is never cached.
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/labels.js", "/icon.svg"];
self.addEventListener("install", (e) => e.waitUntil(caches.open("camctl-v1").then((c) => c.addAll(SHELL)).catch(() => {})));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/") || e.request.method !== "GET") return;
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open("camctl-v1").then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});
