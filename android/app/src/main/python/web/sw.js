/* Application assets update together. Mutations and SSE always use the network. */
const CACHE = 'novel-workshop-20261019';
const SHELL = ['/', '/roleplay.html', '/vue.global.prod.js', '/manifest.json', '/assets/icon.svg',
  '/assets/runtime.js?v=20261019', '/assets/theme.css?v=20261019',
  '/assets/workshop.js?v=20261019', '/assets/tools.css?v=20261019', '/assets/workshop.css?v=20261019',
  '/assets/roleplay.js?v=20261019', '/assets/roleplay.css?v=20261019', '/assets/roleplay-forms.css?v=20261019'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('novel-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname === '/sw.js') return;
  const chapter = /^\/api\/novels\/[^/]+\/chapters\/\d+$/.test(url.pathname);
  if (url.pathname.startsWith('/api/') && !chapter) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(request);
      if (response.ok && !response.headers.get('content-type')?.includes('text/event-stream')) await cache.put(request, response.clone());
      return response;
    } catch (_) {
      const saved = await cache.match(request) || (request.mode === 'navigate' ? await cache.match(url.pathname) : null);
      if (saved) return saved;
      return new Response(chapter ? JSON.stringify({detail:'离线且本章节尚未缓存'}) : '当前离线，请连接网络后重试。', {
        status:503, headers:{'Content-Type':chapter?'application/json':'text/plain;charset=utf-8'}
      });
    }
  })());
});
