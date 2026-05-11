(function () {
    const DB_NAME = 'tjai-research-cache';
    const DB_VERSION = 1;
    const DETAIL_PREFIX = '/tjai/api/research/detail?entry_id=';
    const LIST_LIMIT = 300;
    const PAGE_CACHE = 'tjai-research-shell-v1';
    let dbPromise = null;
    let refreshPromise = null;
    let statusEl = null;

    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta');
                if (!db.objectStoreNames.contains('details')) db.createObjectStore('details');
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
        return dbPromise;
    }

    async function storePut(store, key, value) {
        const db = await openDb();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(store, 'readwrite');
            tx.objectStore(store).put(value, key);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    }

    async function storeGet(store, key) {
        const db = await openDb();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(store, 'readonly');
            const req = tx.objectStore(store).get(key);
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
    }

    async function storeGetAll(store) {
        const db = await openDb();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(store, 'readonly');
            const req = tx.objectStore(store).getAll();
            req.onsuccess = () => resolve(req.result || []);
            req.onerror = () => reject(req.error);
        });
    }

    function bytesFor(value) {
        return new Blob([JSON.stringify(value)]).size;
    }

    function fmtBytes(bytes) {
        if (!bytes) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let value = bytes;
        let i = 0;
        while (value >= 1024 && i < units.length - 1) {
            value /= 1024;
            i += 1;
        }
        return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
    }

    function fmtAge(ts) {
        if (!ts) return 'never';
        const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
        if (seconds < 60) return `${seconds}s ago`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 48) return `${hours}h ago`;
        return `${Math.floor(hours / 24)}d ago`;
    }

    async function setStatus(text) {
        if (!statusEl) return;
        const meta = await storeGet('meta', 'summary').catch(() => null);
        const suffix = meta
            ? `cache ${fmtAge(meta.cached_at)} · ${meta.items || 0} topics · ${meta.entry_pages || 0} report pages · ${fmtBytes(meta.bytes || 0)}`
            : 'cache empty';
        statusEl.textContent = text ? `${text} · ${suffix}` : suffix;
    }

    async function fetchJson(url) {
        const resp = await fetch(url, {
            cache: 'no-store',
            credentials: 'same-origin',
        });
        const data = await resp.json();
        if (!resp.ok || data.error) {
            throw new Error(data.error || `HTTP ${resp.status}`);
        }
        return data;
    }

    async function saveDetail(detailEntryId, data, sourceModified) {
        if (!detailEntryId || !data) return;
        const record = {
            detail_entry_id: detailEntryId,
            data,
            source_modified: sourceModified || null,
            cached_at: Date.now(),
            bytes: bytesFor(data),
        };
        await storePut('details', detailEntryId, record);
    }

    async function cacheDetailData(data) {
        const topic = data && data.topic ? data.topic : null;
        if (!topic || !topic.detail_entry_id) return;
        await saveDetail(topic.detail_entry_id, data, topic.modified || null);
        await cacheLinkedEntryPages(data);
        await recomputeSummary();
    }

    async function cachePageIfMissing(url) {
        if (!url || !('caches' in window)) return {added: 0, bytes: 0};
        const request = new Request(url, {credentials: 'same-origin'});
        const cache = await caches.open(PAGE_CACHE);
        const cached = await cache.match(request);
        if (cached) return {added: 0, bytes: 0};

        const response = await fetch(request, {cache: 'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bytes = (await response.clone().arrayBuffer()).byteLength;
        await cache.put(request, response.clone());
        return {added: 1, bytes};
    }

    async function cacheLinkedEntryPages(data) {
        const urls = new Set();
        if (data.topic && data.topic.entry_url) urls.add(data.topic.entry_url);
        if (data.synthesis && data.synthesis.entry_url) urls.add(data.synthesis.entry_url);
        for (const branch of (data.branches || [])) {
            if (branch.entry_url) urls.add(branch.entry_url);
        }
        let added = 0;
        let bytes = 0;
        for (const url of urls) {
            try {
                const result = await cachePageIfMissing(url);
                added += result.added || 0;
                bytes += result.bytes || 0;
            } catch (e) {
                console.warn('research cache entry page failed', url, e);
            }
        }
        return {checked: urls.size, added, bytes};
    }

    async function refreshAll() {
        if (refreshPromise) return refreshPromise;
        refreshPromise = doRefreshAll().finally(() => { refreshPromise = null; });
        return refreshPromise;
    }

    async function doRefreshAll() {
        if (!navigator.onLine) {
            await setStatus('offline');
            return;
        }
        await setStatus('refreshing');
        const items = [];
        for (let offset = 0; ; offset += LIST_LIMIT) {
            const url = `/tjai/api/research/list?status=all&scope=title&offset=${offset}&limit=${LIST_LIMIT}`;
            const page = await fetchJson(url);
            items.push(...(page.items || []));
            if (!page.stats || !page.stats.has_more) break;
        }
        const manifest = {
            items,
            cached_at: Date.now(),
            bytes: bytesFor(items),
        };
        await storePut('meta', 'manifest', manifest);

        let done = 0;
        let linkedPagesAdded = 0;
        const queue = items.slice();
        async function worker() {
            while (queue.length) {
                const item = queue.shift();
                const detailId = item.detail_entry_id || `${item.entry_id}_detail`;
                const current = await storeGet('details', detailId).catch(() => null);
                if (current && current.source_modified === item.modified) {
                    const pageResult = await cacheLinkedEntryPages(current.data);
                    linkedPagesAdded += pageResult.added || 0;
                    done += 1;
                    if (done % 10 === 0) await setStatus(`cached ${done}/${items.length}${linkedPagesAdded ? ' · added ' + linkedPagesAdded + ' report pages' : ''}`);
                    continue;
                }
                try {
                    const detail = await fetchJson(DETAIL_PREFIX + encodeURIComponent(detailId));
                    await saveDetail(detailId, detail, item.modified);
                    const pageResult = await cacheLinkedEntryPages(detail);
                    linkedPagesAdded += pageResult.added || 0;
                } catch (e) {
                    console.warn('research cache detail failed', detailId, e);
                }
                done += 1;
                if (done % 5 === 0) await setStatus(`cached ${done}/${items.length}${linkedPagesAdded ? ' · added ' + linkedPagesAdded + ' report pages' : ''}`);
            }
        }
        await Promise.all([worker(), worker(), worker()]);
        await recomputeSummary();
        await setStatus('cache current');
    }

    async function recomputeSummary() {
        const manifest = await storeGet('meta', 'manifest').catch(() => null);
        const details = await storeGetAll('details').catch(() => []);
        const entryPages = await cachedEntryPageCount();
        const bytes = (manifest ? manifest.bytes || 0 : 0)
            + details.reduce((sum, rec) => sum + (rec.bytes || 0), 0);
        await storePut('meta', 'summary', {
            cached_at: Date.now(),
            items: manifest && manifest.items ? manifest.items.length : 0,
            details: details.length,
            entry_pages: entryPages,
            bytes,
        });
    }

    async function cachedEntryPageCount() {
        if (!('caches' in window)) return 0;
        const cache = await caches.open(PAGE_CACHE);
        const keys = await cache.keys();
        return keys.filter(request => {
            const url = new URL(request.url);
            return url.pathname === '/tjai/entry/' || url.pathname.startsWith('/tjai/entry/');
        }).length;
    }

    function displayStatus(item) {
        return item.status || 'pending';
    }

    async function offlineList(params) {
        const manifest = await storeGet('meta', 'manifest');
        if (!manifest || !manifest.items) throw new Error('No cached research list');
        const q = (params.get('q') || '').trim().toLowerCase();
        const scope = params.get('scope') || 'title';
        const statusParam = params.get('status') || 'open';
        const offset = Number(params.get('offset') || 0);
        const limit = Number(params.get('limit') || 100);
        let items = manifest.items.slice();

        if (statusParam !== 'all') {
            const wanted = statusParam === 'open'
                ? null
                : new Set(statusParam.split(',').map(s => s.trim()).filter(Boolean));
            items = items.filter(item => {
                const st = displayStatus(item);
                if (wanted) return wanted.has(st);
                return st !== 'archive';
            });
        }

        if (q) {
            const bodyHits = new Set();
            if (scope === 'bodies') {
                const details = await storeGetAll('details').catch(() => []);
                for (const rec of details) {
                    const d = rec.data || {};
                    const topic = d.topic || {};
                    const hay = [
                        topic.content,
                        topic.territory,
                        ...(d.branches || []).map(b => b.content),
                        d.synthesis && d.synthesis.content,
                    ].filter(Boolean).join('\n').toLowerCase();
                    if (hay.includes(q) && topic.entry_id) bodyHits.add(topic.entry_id);
                }
            }
            items = items.filter(item => {
                const title = (item.title || '').toLowerCase();
                const topic = `${item.title || ''}\n${item.description || ''}\n${item.entry_id || ''}`.toLowerCase();
                if (scope === 'title') return title.includes(q);
                if (scope === 'topic') return topic.includes(q);
                return topic.includes(q) || bodyHits.has(item.entry_id);
            });
        }

        return {
            offline: true,
            items: items.slice(offset, offset + limit),
            models: ['claude', 'gemini', 'chatgpt', 'qwen', 'deepseek-flash', 'deepseek-pro'],
            stats: {
                topics: manifest.items.length,
                filtered: items.length,
                returned: Math.min(limit, Math.max(0, items.length - offset)),
                offset,
                limit,
                has_more: offset + limit < items.length,
                counts_by_status: items.reduce((acc, item) => {
                    const st = displayStatus(item);
                    acc[st] = (acc[st] || 0) + 1;
                    return acc;
                }, {}),
            },
        };
    }

    async function offlineDetail(detailEntryId) {
        const rec = await storeGet('details', detailEntryId);
        if (!rec || !rec.data) throw new Error('No cached research detail');
        return {
            ...rec.data,
            offline: true,
            cached_at: rec.cached_at,
        };
    }

    async function init(options) {
        statusEl = options && options.statusEl ? options.statusEl : null;
        await openDb();
        if ('serviceWorker' in navigator) {
            try {
                await navigator.serviceWorker.register('/tjai/research-sw.js', {scope: '/tjai/'});
            } catch (e) {
                console.warn('research service worker registration failed', e);
            }
        }
        if (navigator.storage && navigator.storage.persist) {
            navigator.storage.persist().catch(() => {});
        }
        window.addEventListener('online', () => refreshAll().catch(console.warn));
        window.addEventListener('offline', () => setStatus('offline'));
        await setStatus(navigator.onLine ? '' : 'offline');
        if (navigator.onLine) {
            setTimeout(() => refreshAll().catch(console.warn), 1000);
        }
    }

    window.ResearchCache = {
        init,
        refreshAll,
        offlineList,
        offlineDetail,
        cacheDetailData,
        setStatus,
    };
})();
