/* NovelGenerator — Service Worker v3
 * Fix: 不再缓存 HTML 页面, 仅缓存静态资源.
 * HTML 始终走网络, 确保新版部署立即生效.
 */
const CACHE = 'novel-v3';

// 仅缓存静态资源，不缓存 HTML 页面
const CACHE_ASSETS = [
  '/vue.global.prod.js',
  '/manifest.json',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(CACHE_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  // 清除所有旧版本缓存
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API 调用 — 始终走网络
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
    return;
  }

  // HTML 页面 — 始终走网络 (不缓存，确保新版立即生效)
  if (event.request.mode === 'navigate' || url.pathname === '/' || url.pathname.endsWith('.html')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 静态资源 — cache-first, 网络更新缓存
  event.respondWith(
    caches.match(event.request).then(cached =>
      cached || fetch(event.request).then(response => {
        if (response.ok && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
    )
  );
});
