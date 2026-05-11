const CACHE_PREFIX = 'tjai-offline-v1';
const STATIC_CACHE = `${CACHE_PREFIX}:static`;
const PAGE_CACHE = `${CACHE_PREFIX}:pages`;
const API_CACHE = `${CACHE_PREFIX}:api`;

const STATIC_ASSETS = [
  '/tjai/static/tjai/favicon.svg',
  '/tjai/static/tjai/menu.css',
  '/tjai/static/tjai/tjai-utils.js',
  '/tjai/static/tjai/turndown-7.2.0.js',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/codemirror.min.css',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/theme/material-darker.min.css',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/codemirror.min.js',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/mode/markdown/markdown.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/themes/prism-tomorrow.min.css',
  '/tjai/static/tjai/vendor/prism/1.29.0/prism.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-markup.min.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key.startsWith('tjai-offline-') && !key.startsWith(CACHE_PREFIX))
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') {
    event.respondWith(fetch(request));
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    event.respondWith(fetch(request));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, PAGE_CACHE));
    return;
  }

  if (url.pathname.startsWith('/tjai/static/')) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  if (url.pathname.startsWith('/tjai/api/')) {
    event.respondWith(networkFirst(request, API_CACHE));
    return;
  }

  event.respondWith(fetch(request));
});

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok) {
    cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}
