// TecnoGems Service Worker — V61 NEON
// Strategy:
//  - HTML pages: StaleWhileRevalidate (instant load from cache + refresh
//    in the background) so layout updates ship within a single page view
//    instead of forcing one full network round-trip first.
//  - Static assets (css/js/img/fonts): CacheFirst with background revalidate.
//  - Never cache API, admin, auth or wallet routes.
//
// V61 changes vs V60:
//   - Bumped CACHE_VERSION so every visitor purges OLD caches that still
//     reference the pre-redesign games grid CSS.
//   - HTML strategy switched from NetworkFirst -> StaleWhileRevalidate.
//     The previous strategy made every navigation wait on the network
//     even when a perfectly good copy was already cached, which was the
//     #1 reason mobile users felt the site was slow.
//   - Pre-cache the new V61 CSS + the home page so first paint after
//     install is instant (and offline-friendly).
const CACHE_VERSION = 'tg-v67-3';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const PAGES_CACHE = `${CACHE_VERSION}-pages`;
const OFFLINE_URL = '/';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      cache.addAll([
        '/static/css/v60-neon.css?v=68',
        '/static/css/v60-neon.css',
        '/static/js/app.min.js',
        '/static/js/pages/home.js',
        '/static/img/tecnogems-logo.webp',
        '/static/img/logo-32.webp',
      ]).catch(() => {})
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => !k.startsWith(CACHE_VERSION)).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

function shouldBypass(url) {
  return (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/admin') ||
    url.pathname.startsWith('/auth/') ||
    url.pathname.startsWith('/login') ||
    url.pathname.startsWith('/logout') ||
    url.pathname.startsWith('/checkout') ||
    url.pathname.startsWith('/wallet') ||
    url.pathname.startsWith('/profile') ||
    url.pathname.startsWith('/orders') ||
    url.pathname.startsWith('/uploads/')
  );
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (shouldBypass(url)) return;

  // HTML navigations: StaleWhileRevalidate.
  // Serve cached HTML immediately (sub-100ms feel), update in background.
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      caches.open(PAGES_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          const network = fetch(req)
            .then((res) => {
              // Only cache successful, complete responses.
              if (res && res.status === 200 && res.type !== 'opaque') {
                cache.put(req, res.clone());
              }
              return res;
            })
            .catch(() => cached || cache.match(OFFLINE_URL));
          return cached || network;
        })
      )
    );
    return;
  }

  // Static assets: CacheFirst with background revalidate.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => {
        if (hit) {
          fetch(req).then((res) => {
            if (res && res.status === 200) {
              caches.open(STATIC_CACHE).then((c) => c.put(req, res.clone()));
            }
          }).catch(() => {});
          return hit;
        }
        return fetch(req).then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
          }
          return res;
        });
      })
    );
  }
});
