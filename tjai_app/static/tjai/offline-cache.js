(function() {
  const META_KEY = 'tjai-offline-material-cache-meta';
  const CACHE_PREFIX = 'tjai-offline-v2';
  const PAGE_CACHE = `${CACHE_PREFIX}:pages`;
  const API_CACHE = `${CACHE_PREFIX}:api`;
  const STATIC_CACHE = `${CACHE_PREFIX}:static`;
  const SCOPES = [
    '/tjai/agent-log/',
    '/tjai/agent-queue/',
    '/tjai/assessment/',
    '/tjai/context/',
    '/tjai/dashboard/',
    '/tjai/dev/',
    '/tjai/diary/',
    '/tjai/entry/',
    '/tjai/git/',
    '/tjai/goals/',
    '/tjai/kind/',
    '/tjai/picks/',
    '/tjai/readme/',
    '/tjai/rss/',
    '/tjai/synopsis/',
    '/tjai/system/',
    '/tjai/tag/',
    '/tjai/this-week/',
    '/tjai/weekly/',
    '/tjai/workday/',
    '/tjai/workweek/',
  ];

  function fmtBytes(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function fmtAge(ts) {
    if (!ts) return '';
    const seconds = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (seconds < 60) return seconds + 's';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + 'm';
    const hours = Math.round(minutes / 60);
    if (hours < 48) return hours + 'h';
    return Math.round(hours / 24) + 'd';
  }

  function readMeta() {
    try { return JSON.parse(localStorage.getItem(META_KEY) || '{}'); } catch(e) { return {}; }
  }

  function writeMeta(meta) {
    try { localStorage.setItem(META_KEY, JSON.stringify(meta)); } catch(e) {}
  }

  function renderStatus(el, text) {
    if (el) el.textContent = text;
  }

  async function registerWorkers() {
    if (!('serviceWorker' in navigator)) return;
    await Promise.all(SCOPES.map((scope) => (
      navigator.serviceWorker.register('/tjai/offline-sw.js', {scope}).catch(() => null)
    )));
  }

  function cacheNameFor(item) {
    if (item.type === 'page') return PAGE_CACHE;
    if (item.type === 'static') return STATIC_CACHE;
    return API_CACHE;
  }

  async function cacheItem(item) {
    const request = normalizedRequest(item.url);
    const response = await fetch(request, {cache: 'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const bytes = (await response.clone().arrayBuffer()).byteLength;
    const cache = await caches.open(cacheNameFor(item));
    await cache.put(request, response.clone());
    return bytes;
  }

  async function warmManifest(options) {
    const statusEl = options && options.statusEl;
    const previous = readMeta();
    renderStatus(statusEl, previous.ts
      ? `offline cache: checking; last observed ${previous.count || 0} items, ${fmtAge(previous.ts)} old`
      : 'offline cache: checking');

    await registerWorkers();

    // The warmer wrote v1 buckets while the service worker read v2;
    // drop the orphaned v1 caches left behind in existing browsers.
    for (const name of await caches.keys()) {
      if (name.startsWith('tjai-offline-v1')) await caches.delete(name);
    }

    const manifestResp = await fetch('/tjai/api/offline/material-cache-manifest', {
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!manifestResp.ok) throw new Error('manifest HTTP ' + manifestResp.status);
    const manifest = await manifestResp.json();
    const items = manifest.items || [];
    const cacheIndex = await buildCacheIndex();
    let cached = countCachedItems(items, cacheIndex);
    let done = 0;
    let failed = 0;
    let bytes = 0;
    const missing = items.filter((item) => !cacheIndex.get(cacheNameFor(item)).has(normalizedUrl(item.url)));

    if (!missing.length) {
      const meta = {
        ts: Date.now(),
        count: cached,
        failed: 0,
        total: items.length,
        bytes: previous.bytes || 0,
        run_count: 0,
        run_bytes: 0,
        groups: manifest.groups || {},
      };
      writeMeta(meta);
      renderStatus(statusEl, `offline cache: ${meta.count}/${meta.total} cached, just now`);
      return meta;
    }

    renderStatus(statusEl, `offline cache: ${cached}/${items.length} cached, adding ${missing.length}`);

    for (const item of missing) {
      try {
        bytes += await cacheItem(item);
        cacheIndex.get(cacheNameFor(item)).add(normalizedUrl(item.url));
        cached++;
      } catch(e) {
        failed++;
      }
      done++;
      if (done === 1 || done === missing.length || done % 10 === 0) {
        renderStatus(statusEl, `offline cache: ${cached}/${items.length} cached, ${fmtBytes(bytes)} added`);
      }
    }

    const successCount = done - failed;
    const meta = {
      ts: Date.now(),
      count: countCachedItems(items, cacheIndex),
      failed: failed,
      total: items.length,
      bytes: (previous.bytes || 0) + bytes,
      run_count: successCount,
      run_bytes: bytes,
      groups: manifest.groups || {},
    };
    writeMeta(meta);
    renderStatus(statusEl, `offline cache: ${meta.count}/${meta.total} cached, just now${bytes ? ', ' + fmtBytes(bytes) + ' added' : ''}${failed ? ', ' + failed + ' failed' : ''}`);
    return meta;
  }

  async function cacheStats() {
    if (!('caches' in window)) return {count: 0};
    let count = 0;
    for (const cacheName of [PAGE_CACHE, API_CACHE, STATIC_CACHE]) {
      const cache = await caches.open(cacheName);
      const requests = await cache.keys();
      count += requests.length;
    }
    return {count};
  }

  async function buildCacheIndex() {
    const index = new Map();
    for (const cacheName of [PAGE_CACHE, API_CACHE, STATIC_CACHE]) {
      const cache = await caches.open(cacheName);
      const keys = await cache.keys();
      index.set(cacheName, new Set(keys.map((request) => normalizedUrl(request.url))));
    }
    return index;
  }

  function countCachedItems(items, cacheIndex) {
    let count = 0;
    for (const item of items) {
      const urls = cacheIndex.get(cacheNameFor(item));
      if (urls && urls.has(normalizedUrl(item.url))) count++;
    }
    return count;
  }

  async function fetchJson(url) {
    const request = normalizedRequest(url);
    try {
      const response = await fetch(request);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return await response.json();
    } catch(e) {
      const cache = await caches.open(API_CACHE);
      const cached = await cache.match(request);
      if (!cached) throw e;
      return await cached.json();
    }
  }

  function normalizedRequest(url) {
    return new Request(normalizedUrl(url), {credentials: 'same-origin'});
  }

  function normalizedUrl(url) {
    const u = new URL(url, window.location.origin);
    u.searchParams.delete('_');
    u.searchParams.delete('ts');
    return u.toString();
  }

  window.TjaiOfflineCache = {
    fetchJson,
    fmtAge,
    fmtBytes,
    readMeta,
    registerWorkers,
    warmManifest,
    cacheStats,
    buildCacheIndex,
  };
})();
