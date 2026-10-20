/* Application assets update together. Mutations and SSE always use the network. */
const CACHE = 'novel-workshop-9297ab40a17c';
const SHELL = ["/", "/roleplay.html", "/scene.html", "/cards.html", "/vue.global.prod.js", "/manifest.json", "/assets/app-polish.css?v=fb1bbb201a6b", "/assets/app-shell.js?v=803ddc66d6ae", "/assets/card-renderer.js?v=afea6edf546b", "/assets/cards.css?v=9d915d2a84f2", "/assets/cards.js?v=5bc5cc62c612", "/assets/DOMPurify-LICENSE.txt?v=cfc7749b96f6", "/assets/dompurify.min.js?v=cd37898f9782", "/assets/icon.svg?v=62cce142b355", "/assets/marked-LICENSE.txt?v=8e3a3f82f59a", "/assets/marked.umd.js?v=b82b9334406d", "/assets/navigation.css?v=3c2c084407a7", "/assets/roleplay.css?v=37d07299cf81", "/assets/roleplay.js?v=216fe2f70c41", "/assets/runtime.js?v=0b7e3913acf0", "/assets/scene-runtime.js?v=8c0c90dc83d1", "/assets/scene.css?v=f2eb7dae578a", "/assets/scene.js?v=e5b4c14b914b", "/assets/theme.css?v=865324615487", "/assets/tools.css?v=ce007915307d", "/assets/workshop.css?v=740eab95b3e5", "/assets/workshop.js?v=eb415dbd7d58"];
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
