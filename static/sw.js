const CACHE = 'puente-v6';
const PRECACHE = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/images/puenteHoyTransparente.png',
  '/static/images/android-chrome-192x192.png',
  '/static/images/android-chrome-512x512.png',
  '/static/images/apple-touch-icon.png',
  '/static/images/favicon-32x32.png',
  '/static/images/favicon-16x16.png',
  '/favicon.ico',
  '/manifest.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.pathname === '/api/trafico') {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request).then((r) => r || Response.error()))
    );
    return;
  }
  if (url.pathname.startsWith('/static/') || url.pathname === '/favicon.ico' || url.pathname === '/manifest.json') {
    // Stale-while-revalidate: serve from cache immediately (fast), but always
    // fetch + update the cache in the background so stale assets never get stuck.
    e.respondWith(
      caches.open(CACHE).then((cache) =>
        cache.match(e.request).then((cached) => {
          const networkFetch = fetch(e.request).then((res) => {
            if (res.ok) cache.put(e.request, res.clone());
            return res;
          });
          return cached || networkFetch;
        })
      )
    );
  }
});

// === Web Push Event Listeners ===

self.addEventListener('push', function (event) {
  if (!event.data) return;

  try {
    const data = event.data.json();
    const title = data.title || 'Alerta de Tránsito';
    const options = {
      body: data.body || 'Nuevos datos del puente disponibles.',
      icon: '/static/images/vista_previa.webp',
      vibrate: [200, 100, 200],
      badge: '/static/images/vista_previa.webp',
      data: {
        url: '/'
      }
    };

    event.waitUntil(
      self.registration.showNotification(title, options)
    );
  } catch (err) {
    console.error('Error al procesar evento push en Service Worker:', err);
  }
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();

  const targetUrl = '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
      // Intentar enfocar una pestaña abierta que corresponda al sitio
      for (const client of clientList) {
        if (client.url.indexOf(targetUrl) !== -1 && 'focus' in client) {
          return client.focus();
        }
      }
      // Si no hay pestañas abiertas, abrir una nueva
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
