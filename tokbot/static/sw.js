// Minimal service worker for install + basic offline shell
const CACHE = "ct-token-monitor-v1";
const ASSETS = [
  "/",
  "/login",
  "/static/manifest.webmanifest",
  "/static/icons/icon.svg"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Network-first for app pages, cache fallback
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Only handle same-origin
  if (url.origin !== self.location.origin) return;

  event.respondWith(
    fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then(cache => cache.put(req, copy)).catch(()=>{});
      return res;
    }).catch(() => caches.match(req).then(m => m || caches.match("/")))
  );
});
