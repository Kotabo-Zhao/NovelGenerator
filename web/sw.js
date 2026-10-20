/* Application assets update together. Mutations and SSE always use the network. */
const CACHE = 'novel-workshop-f5a5ec920fe7';
const SHELL = ["/", "/roleplay.html", "/scene.html", "/cards.html", "/vue.global.prod.js", "/manifest.json", "/assets/app-polish.css?v=131c32a89913", "/assets/app-shell.js?v=9ad3bf2288e3", "/assets/cards.css?v=9d915d2a84f2", "/assets/cards.js?v=5bc5cc62c612", "/assets/icon.svg?v=62cce142b355", "/assets/navigation.css?v=3c2c084407a7", "/assets/roleplay.css?v=1efdee5c5ce7", "/assets/roleplay.js?v=12baee7f116e", "/assets/runtime.js?v=0b7e3913acf0", "/assets/scene-runtime.js?v=8c0c90dc83d1", "/assets/scene.css?v=c2ab692be7e0", "/assets/scene.js?v=4c08b7440af3", "/assets/theme.css?v=865324615487", "/assets/tools.css?v=ce007915307d", "/assets/workshop.css?v=740eab95b3e5", "/assets/workshop.js?v=eb415dbd7d58"];
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
