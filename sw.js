// Кэш приложения, чтобы оно открывалось без интернета.
// После изменения index.html увеличьте номер версии.
const CACHE = "slova-v19";
const IMAGE_CACHE = "slova-images"; // картинки к словам хранятся отдельно и не сбрасываются при обновлении
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon-180.png", "./icon-512.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE && k !== IMAGE_CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const isOwn = url.origin === self.location.origin;
  const isFont = url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com";
  const isImage = !isOwn && req.destination === "image";

  // Сама страница: сначала из сети, чтобы обновления приходили сразу; без интернета — из кэша
  if (isOwn && (req.mode === "navigate" || url.pathname.endsWith("/") || url.pathname.endsWith(".html"))) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      try {
        const res = await fetch(req, { cache: "no-store" });
        if (res.ok) cache.put("./index.html", res.clone());
        return res;
      } catch {
        return (await cache.match("./index.html")) || (await cache.match("./")) || Response.error();
      }
    })());
    return;
  }

  if (isImage) {
    event.respondWith(cachedImage(req));
    return;
  }
  if (!isOwn && !isFont) return; // таблицу не трогаем: слова сохраняет само приложение

  // Иконки, манифест, шрифты: из кэша, обновляются в фоне
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(req, { ignoreSearch: isOwn });
    const network = fetch(req)
      .then(res => {
        if (res.ok || res.type === "opaque") cache.put(req, res.clone());
        return res;
      })
      .catch(() => cached || Response.error());
    return cached || network;
  })());
});

// Картинка скачивается один раз и дальше берётся из кэша, в том числе без интернета.
// В кэш попадают только успешные ответы, чтобы не сохранить ошибку лимита запросов.
async function cachedImage(req) {
  const cache = await caches.open(IMAGE_CACHE);
  const hit = await cache.match(req.url);
  if (hit) return hit;
  try {
    const res = await fetch(req.url, { mode: "cors", credentials: "omit" });
    if (!res.ok) return Response.error(); // например, «Free limit reached»: не показываем картинку-заглушку
    cache.put(req.url, res.clone());
    return res;
  } catch {
    // сайт не отдаёт CORS: показываем картинку без сохранения в кэш
    return fetch(req);
  }
}
