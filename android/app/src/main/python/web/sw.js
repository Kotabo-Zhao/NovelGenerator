/* Application assets update together. Mutations and SSE always use the network. */
const CACHE = 'novel-workshop-a31bb9428305';
const SHELL = ["/", "/roleplay.html", "/scene.html", "/cards.html", "/vue.global.prod.js", "/manifest.json", "/assets/DOMPurify-LICENSE.txt?v=cfc7749b96f6", "/assets/app-polish.css?v=f6b355bca346", "/assets/app-shell.js?v=5e1958a4e119", "/assets/card-renderer.js?v=afea6edf546b", "/assets/cards.css?v=1ecba2f5f9a7", "/assets/cards.js?v=8787ab72ccc9", "/assets/dompurify.min.js?v=cd37898f9782", "/assets/icon.svg?v=4553a5f934a5", "/assets/marked-LICENSE.txt?v=8e3a3f82f59a", "/assets/marked.umd.js?v=b82b9334406d", "/assets/navigation.css?v=060d36cc8074", "/assets/roleplay.css?v=a5f5fb3ec43b", "/assets/roleplay.js?v=a7a180308d7d", "/assets/runtime.js?v=a98f7bad7c8a", "/assets/scene-runtime.js?v=8c0c90dc83d1", "/assets/scene.css?v=15054beabba1", "/assets/scene.js?v=b9f6cfc8b03b", "/assets/theme.css?v=50fb48f925b3", "/assets/tools.css?v=3e387fdf4687", "/assets/workshop.css?v=92055e7a3142", "/assets/workshop.js?v=1bef73b71e74"];
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
