// Кэш оболочки приложения, чтобы оно открывалось без интернета.
// После изменения index.html увеличьте номер версии.
const CACHE = "slova-v4";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon-180.png", "./icon-512.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const isOwn = url.origin === self.location.origin;
  const isFont = url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com";
  if (!isOwn && !isFont) return; // таблицу не трогаем: слова сохраняет само приложение

  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(req, { ignoreSearch: isOwn });
    const network = fetch(req)
      .then(res => {
        if (res.ok || res.type === "opaque") cache.put(req, res.clone());
        return res;
      })
      .catch(() => cached || (req.mode === "navigate" ? cache.match("./index.html") : Response.error()));
    return cached || network;
  })());
});
