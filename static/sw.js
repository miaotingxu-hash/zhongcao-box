const CACHE_NAME = 'zhongcao-v1';
const STATIC_ASSETS = ['/', '/static/index.html', '/static/manifest.json', '/static/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
  ));
});

self.addEventListener('fetch', e => {
  // API 请求和上传不缓存
  if (e.request.url.includes('/api/') || e.request.url.includes('/uploads/')) {
    return;
  }
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
