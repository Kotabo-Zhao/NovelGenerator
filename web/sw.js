/* NovelGenerator — Service Worker v6
 * v6: 增强离线阅读 — 章节内容缓存 + 离线回退页
 *     Chapter content Cache-First（阅读已缓存的章节不耗流量）
 *     stale-while-revalidate 用于API章节数据
 *     auto-cleanup old caches on activate
 */

const CACHE_VERSION = 6;
const VENDOR_CACHE = `novel-vendor-v${CACHE_VERSION}`;
const APP_CACHE = `novel-app-v${CACHE_VERSION}`;
const CHAPTER_CACHE = `novel-chapters-v${CACHE_VERSION}`;

// Large vendor libraries — rarely change, cache-first
const VENDOR_ASSETS = [
  '/vue.global.prod.js',
  '/manifest.json',
];

// Pre-cache on install
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(VENDOR_CACHE).then(cache => {
      console.log('[SW v6] Pre-caching vendor assets...');
      return cache.addAll(VENDOR_ASSETS).catch(e =>
        console.warn('[SW v6] Pre-cache partial failure:', e.message)
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('novel-') && 
            k !== VENDOR_CACHE && k !== APP_CACHE && k !== CHAPTER_CACHE)
          .map(k => {
            console.log('[SW v6] Deleting old cache:', k);
            return caches.delete(k);
          })
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // SW script — never cache
  if (url.pathname === '/sw.js') {
    event.respondWith(fetch(event.request));
    return;
  }

  // Chapter data API — stale-while-revalidate (quick load + fresh data)
  if (url.pathname.includes('/api/novels/') && url.pathname.includes('/chapters/')) {
    event.respondWith(
      caches.open(CHAPTER_CACHE).then(cache =>
        cache.match(event.request).then(cached => {
          const fetchPromise = fetch(event.request).then(response => {
            if (response.ok && response.status === 200) {
              cache.put(event.request, response.clone());
            }
            return response;
          }).catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // Novel list / metadata API — network-first with 30s cache fallback
  if (url.pathname.includes('/api/novels') && !url.pathname.includes('/chapters/')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match(event.request).then(cached => {
          if (cached) {
            console.log('[SW v6] Serving cached novel list');
            return cached;
          }
          return new Response(JSON.stringify({ error: '离线模式，无法获取数据' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          });
        })
      )
    );
    return;
  }

  // Other API calls — network-only (generate/export etc.)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // HTML navigation — network-first, fallback to cache
  if (event.request.mode === 'navigate' || url.pathname === '/' || url.pathname.endsWith('.html')) {
    event.respondWith(
      fetch(event.request).then(response => {
        // Cache successful navigations for offline
        const clone = response.clone();
        caches.open(APP_CACHE).then(cache => cache.put(event.request, clone));
        return response;
      }).catch(() =>
        caches.match(event.request).then(cached => {
          if (cached) return cached;
          // Ultimate fallback — show offline page
          return new Response(
            `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <title>离线中 — 墨·局</title><style>
            body{font-family:serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#1a1410;color:#e5d9c8;text-align:center}
            h1{font-size:48px;margin-bottom:8px} p{opacity:.6}
            </style></head><body><div><h1>📖</h1><p>墨·局 离线模式</p><p style="font-size:12px">请连接网络后重试</p></div></body></html>`,
            { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          );
        })
      )
    );
    return;
  }

  // Vendor assets — cache-first
  if (VENDOR_ASSETS.some(a => url.pathname === a)) {
    event.respondWith(
      caches.match(event.request).then(cached =>
        cached || fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(VENDOR_CACHE).then(c => c.put(event.request, clone));
          }
          return response;
        })
      )
    );
    return;
  }

  // All other static assets — stale-while-revalidate
  event.respondWith(
    caches.open(APP_CACHE).then(cache =>
      cache.match(event.request).then(cached => {
        const fetchPromise = fetch(event.request).then(response => {
          if (response.ok && response.status === 200) {
            cache.put(event.request, response.clone());
          }
          return response;
        }).catch(() => cached);
        return cached || fetchPromise;
      })
    )
  );
});

// Background sync for chapter updates
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'CACHE_CHAPTER') {
    const { chapterUrl } = event.data;
    caches.open(CHAPTER_CACHE).then(cache => {
      fetch(chapterUrl).then(response => {
        if (response.ok) {
          cache.put(chapterUrl, response);
          console.log('[SW v6] Background cached:', chapterUrl);
        }
      }).catch(() => {});
    });
  }
});
