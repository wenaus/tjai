const CACHE_PREFIX = 'tjai-offline-v2';
const STATIC_CACHE = `${CACHE_PREFIX}:static`;
const PAGE_CACHE = `${CACHE_PREFIX}:pages`;
const API_CACHE = `${CACHE_PREFIX}:api`;

const STATIC_ASSETS = [
  '/tjai/static/tjai/favicon.svg',
  '/tjai/static/tjai/menu.css',
  '/tjai/static/tjai/offline-cache.js',
  '/tjai/static/tjai/tjai-utils.js',
  '/tjai/static/tjai/turndown-7.2.0.js',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/codemirror.min.css',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/theme/material-darker.min.css',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/codemirror.min.js',
  '/tjai/static/tjai/vendor/codemirror/5.65.18/mode/markdown/markdown.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/themes/prism-tomorrow.min.css',
  '/tjai/static/tjai/vendor/prism/1.29.0/prism.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-markup.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-bash.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-python.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-c.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-cpp.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-json.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-yaml.min.js',
  '/tjai/static/tjai/vendor/prism/1.29.0/components/prism-sql.min.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
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
  const cacheRequest = normalizedCacheRequest(request);
  const cached = await cache.match(cacheRequest);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok) {
    cache.put(cacheRequest, response.clone());
  }
  return response;
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cacheRequest = normalizedCacheRequest(request);
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(cacheRequest, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await matchCached(cache, cacheRequest);
    if (cached) return cached;
    throw err;
  }
}

function normalizedCacheRequest(request) {
  const url = new URL(request.url);
  url.searchParams.delete('_');
  url.searchParams.delete('ts');
  return url.toString();
}

async function matchCached(cache, cacheRequest) {
  const cached = await cache.match(cacheRequest);
  if (cached) return cached;

  for (const alternate of alternateCacheRequests(cacheRequest)) {
    const alternateCached = await cache.match(alternate);
    if (alternateCached) return alternateCached;
  }
  return null;
}

function alternateCacheRequests(cacheRequest) {
  const url = new URL(cacheRequest);
  const prefix = '/tjai/entry/';
  if (!url.pathname.startsWith(prefix) || url.pathname === prefix || url.search) {
    return [];
  }

  const ref = decodeURIComponent(url.pathname.slice(prefix.length).replace(/\/$/, ''));
  if (!ref) return [];

  const queryUrl = new URL('/tjai/entry/', url.origin);
  const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  queryUrl.searchParams.set(uuidRe.test(ref) ? 'uuid' : 'entry_id', ref);
  return [queryUrl.toString()];
}
