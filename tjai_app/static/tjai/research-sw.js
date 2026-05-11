const CACHE_NAME = 'tjai-research-shell-v1';
const SHELL_URLS = [
    '/tjai/research/',
    '/tjai/research-detail/',
    '/tjai/static/tjai/menu.css',
    '/tjai/static/tjai/research-cache.js',
    '/tjai/static/tjai/favicon.svg',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(SHELL_URLS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
    caches.keys()
            .then(keys => Promise.all(
                keys
                    .filter(key => key.startsWith('tjai-research-') && key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

function isResearchShell(pathname) {
    return pathname === '/tjai/research/'
        || pathname === '/tjai/research-list/'
        || pathname === '/tjai/research-detail/'
        || pathname.startsWith('/tjai/research-detail/');
}

async function networkFirst(request, fallbackUrl) {
    const cache = await caches.open(CACHE_NAME);
    try {
        const response = await fetch(request);
        if (response && response.ok) {
            await cache.put(request, response.clone());
        }
        return response;
    } catch (e) {
        return await cache.match(request)
            || await cache.match(fallbackUrl)
            || new Response('Offline research cache is not initialized.', {
                status: 503,
                headers: {'Content-Type': 'text/plain; charset=utf-8'},
            });
    }
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    const fetched = fetch(request)
        .then(response => {
            if (response && response.ok) cache.put(request, response.clone());
            return response;
        })
        .catch(() => null);
    return cached || await fetched || new Response('', {status: 503});
}

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;

    if (event.request.mode === 'navigate' && isResearchShell(url.pathname)) {
        const fallback = url.pathname.startsWith('/tjai/research-detail/')
            ? '/tjai/research-detail/'
            : '/tjai/research/';
        event.respondWith(networkFirst(event.request, fallback));
        return;
    }

    if (url.pathname.startsWith('/tjai/static/tjai/')) {
        event.respondWith(staleWhileRevalidate(event.request));
    }
});
