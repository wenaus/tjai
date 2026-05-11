(function() {
  const META_KEY = 'tjai-offline-material-cache-meta';
  const CACHE_PREFIX = 'tjai-offline-v1';
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
    if (previous.ts && previous.count) {
      renderStatus(statusEl, `offline cache: ${previous.count} items, ${fmtBytes(previous.bytes || 0)}, ${fmtAge(previous.ts)} old`);
    } else {
      renderStatus(statusEl, 'offline cache: starting');
    }

    await registerWorkers();

    const manifestResp = await fetch('/tjai/api/offline/material-cache-manifest', {
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!manifestResp.ok) throw new Error('manifest HTTP ' + manifestResp.status);
    const manifest = await manifestResp.json();
    const items = manifest.items || [];
    const previousCount = previous.count || 0;
    let cached = 0;
    let done = 0;
    let failed = 0;
    let bytes = 0;
    const missing = [];

    for (const item of items) {
      if (await cacheHas(item)) {
        cached++;
      } else {
        missing.push(item);
      }
    }

    if (!missing.length) {
      const stats = await cacheStats();
      const meta = {
        ts: Date.now(),
        count: Math.max(stats.count || 0, cached, previousCount),
        failed: 0,
        total: items.length,
        bytes: previous.bytes || 0,
        run_count: 0,
        run_bytes: 0,
        groups: manifest.groups || {},
      };
      writeMeta(meta);
      renderStatus(statusEl, `offline cache: ${meta.count}/${meta.total} items, ${fmtBytes(meta.bytes)}, just now`);
      return meta;
    }

    const visibleCached = Math.max(cached, previousCount);
    renderStatus(statusEl, `offline cache: ${visibleCached}/${items.length} cached, adding ${missing.length}`);

    for (const item of missing) {
      try {
        bytes += await cacheItem(item);
      } catch(e) {
        failed++;
      }
      done++;
      if (done === 1 || done === missing.length || done % 10 === 0) {
        renderStatus(statusEl, `offline cache: ${Math.max(visibleCached, cached + done - failed)}/${items.length}, ${fmtBytes(bytes)} added`);
      }
    }

    const stats = await cacheStats();
    const successCount = done - failed;
    const meta = {
      ts: Date.now(),
      count: Math.max(stats.count || 0, cached + successCount, previousCount),
      failed: failed,
      total: items.length,
      bytes: (previous.bytes || 0) + bytes,
      run_count: successCount,
      run_bytes: bytes,
      groups: manifest.groups || {},
    };
    if (successCount === 0 && previous.ts) {
      meta.count = previous.count || 0;
      meta.bytes = previous.bytes || 0;
      meta.ts = previous.ts;
    }
    writeMeta(meta);
    const age = meta.ts === previous.ts ? `${fmtAge(meta.ts)} old` : 'just now';
    renderStatus(statusEl, `offline cache: ${meta.count}/${meta.total} items, ${fmtBytes(meta.bytes)}, ${age}${failed ? ', ' + failed + ' failed' : ''}`);
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

  async function cacheHas(item) {
    const cache = await caches.open(cacheNameFor(item));
    const normalized = normalizedUrl(item.url);
    return !!(
      await cache.match(normalized)
      || await cache.match(new Request(normalized, {credentials: 'same-origin'}))
      || await cache.match(item.url)
    );
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
  };
})();
