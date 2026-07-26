/* NovelGenerator — Service Worker v5
 * v5: stale-while-revalidate + network-first for app code
 *      cache-first only for large vendor libs (vue.js)
 *      auto-cleanup old caches on activate
 */

const CACHE_VERSION = 5;
const VENDOR_CACHE = `novel-vendor-v${CACHE_VERSION}`;
const APP_CACHE = `novel-app-v${CACHE_VERSION}`;

// Large vendor libraries — rarely change, safe to cache-first
const VENDOR_ASSETS = [
  '/vue.global.prod.js',
  '/manifest.json',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(VENDOR_CACHE).then(cache => cache.addAll(VENDOR_ASSETS))
  );
  // Take control immediately — don't wait for old tabs to close
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('novel-') && k !== VENDOR_CACHE && k !== APP_CACHE)
          .map(k => {
            console.log('[SW v5] Deleting old cache:', k);
            return caches.delete(k);
          })
      )
    )
  );
  // Take control of all open pages immediately
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API calls — network-first, no caching
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // SW script itself — never cache (handled by server Cache-Control header)
  if (url.pathname === '/sw.js') {
    event.respondWith(fetch(event.request));
    return;
  }

  // HTML navigation — network-first (always get latest app code)
  if (event.request.mode === 'navigate' || url.pathname === '/' || url.pathname.endsWith('.html')) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
    return;
  }

  // Vendor assets (vue.js etc) — cache-first (rarely change)
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
  // Serve cached immediately, update cache in background
  event.respondWith(
    caches.open(APP_CACHE).then(cache =>
      cache.match(event.request).then(cached => {
        const fetchPromise = fetch(event.request).then(response => {
          if (response.ok && response.status === 200) {
            cache.put(event.request, response.clone());
          }
          return response;
        }).catch(() => cached);
        // Return cached first, network update in background
        return cached || fetchPromise;
      })
    )
  );
});
