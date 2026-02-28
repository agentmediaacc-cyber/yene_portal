const CACHE = "yene-v1";
const ASSETS = [
  "/",
  "/login",
  "/agent/dashboard",
  "/static/manifest.json",
  "/static/css/mobile.css",
  "/static/js/mobile.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  e.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((resp) => {
      // Cache GET responses for speed
      if (req.method === "GET" && resp.ok && resp.type === "basic") {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return resp;
    }).catch(() => cached))
  );
});
