(function() {
  const META_KEY = 'tjai-offline-material-cache-meta';
  const CACHE_PREFIX = 'tjai-offline-v1';
  const PAGE_CACHE = `${CACHE_PREFIX}:pages`;
  const API_CACHE = `${CACHE_PREFIX}:api`;
  const STATIC_CACHE = `${CACHE_PREFIX}:static`;
  const SCOPES = [
    '/tjai/diary/',
    '/tjai/entry/',
    '/tjai/synopsis/',
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
    const response = await fetch(item.url, {credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const bytes = (await response.clone().arrayBuffer()).byteLength;
    const cache = await caches.open(cacheNameFor(item));
    await cache.put(item.url, response.clone());
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
    let done = 0;
    let failed = 0;
    let bytes = 0;

    for (const item of items) {
      try {
        bytes += await cacheItem(item);
      } catch(e) {
        failed++;
      }
      done++;
      if (done === 1 || done === items.length || done % 10 === 0) {
        renderStatus(statusEl, `offline cache: ${done}/${items.length}, ${fmtBytes(bytes)}`);
      }
    }

    const meta = {
      ts: Date.now(),
      count: done - failed,
      failed: failed,
      total: items.length,
      bytes: bytes,
      groups: manifest.groups || {},
    };
    writeMeta(meta);
    renderStatus(
      statusEl,
      `offline cache: ${meta.count}/${meta.total} items, ${fmtBytes(bytes)}, just now${failed ? ', ' + failed + ' failed' : ''}`
    );
    return meta;
  }

  async function fetchJson(url) {
    try {
      const response = await fetch(url, {credentials: 'same-origin'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return await response.json();
    } catch(e) {
      const cache = await caches.open(API_CACHE);
      const cached = await cache.match(url);
      if (!cached) throw e;
      return await cached.json();
    }
  }

  window.TjaiOfflineCache = {
    fetchJson,
    fmtAge,
    fmtBytes,
    readMeta,
    registerWorkers,
    warmManifest,
  };
})();
