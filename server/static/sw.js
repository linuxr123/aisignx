/**
 * Service Worker — AISignX Display Player Offline Cache
 *
 * Strategy:
 *   /static/*   → cache-first  (JS/CSS/fonts are content-addressed by filename)
 *   /uploads/*  → cache-first  (media files are immutable UUIDs)
 *   /display/*  → network-first, cache fallback  (player HTML page)
 *   everything else → network only (API writes, SSE, ping)
 */

const CACHE_VER    = 'signage-v68';
const STATIC_CACHE = `${CACHE_VER}-static`;
// Media cache deliberately uses a STABLE name (no version prefix) so that
// uploaded images/videos survive service-worker upgrades. Media files are
// content-addressed by UUID — they are immutable, so they never need cache
// busting alongside code updates.
const MEDIA_CACHE  = `signage-media`;
const PAGE_CACHE   = `${CACHE_VER}-page`;
const PLAYLIST_CACHE = `${CACHE_VER}-playlist`;

// JS/CSS files that change with app updates — always fetch fresh, cache as fallback
const NETWORK_FIRST_STATIC = [
    '/static/js/display_player.js',
];

const PREFETCH_MEDIA_CONCURRENCY = 3;

// ── Install ──────────────────────────────────────────────────────────────────
self.addEventListener('install', () => self.skipWaiting());

// ── Activate: prune old caches, claim clients, then reload all display pages ─
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys
                    // Keep current-version caches AND the stable media cache
                    .filter(k => !k.startsWith(CACHE_VER) && k !== MEDIA_CACHE)
                    .map(k => caches.delete(k))
            )
        ).then(() => self.clients.claim())
          .then(() => self.clients.matchAll({ type: 'window' }))
          .then(clients => {
              clients.forEach(client => {
                  // Only reload display player pages, not admin pages
                  if (client.url.includes('/display/')) {
                      client.navigate(client.url);
                  }
              });
          })
    );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
    const req = event.request;
    const url = new URL(req.url);

    // Only handle same-origin GET requests
    if (req.method !== 'GET' || url.origin !== self.location.origin) return;

    // Never intercept SSE streams, pings, or API calls
    if (
        url.pathname.endsWith('/events') ||
        url.pathname.endsWith('/ping')
    ) return;

    // Playlist JSON is safe to cache for offline boot when localStorage is empty.
    if (/^\/api\/display\/[^/]+\/playlist$/.test(url.pathname)) {
        event.respondWith(networkFirst(req, PLAYLIST_CACHE));
        return;
    }

    if (url.pathname.startsWith('/api/')) return;

    // Plugin pages and their assets — cache aggressively so the iframe shell
    // and its JS/CSS load even when the server is offline. We use a stale-
    // while-revalidate strategy: serve the cached copy instantly, then update
    // the cache in the background with whatever the network returns.
    //   /plugin/<type>?cfg=...   - the iframe HTML wrapper (cfg query is part
    //                              of the cache key, so different displays can
    //                              cache different configs simultaneously)
    //   /plugin_assets/...       - the plugin's JS/CSS/images
    if (url.pathname.startsWith('/plugin/') || url.pathname.startsWith('/plugin_assets/')) {
        event.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
        return;
    }

    // Static assets (JS, CSS, images in /static/)
    // display_player.js and other frequently-updated files → network first
    // everything else → cache first
    if (url.pathname.startsWith('/static/')) {
        if (NETWORK_FIRST_STATIC.includes(url.pathname)) {
            event.respondWith(networkFirst(req, STATIC_CACHE));
        } else {
            event.respondWith(cacheFirst(req, STATIC_CACHE));
        }
        return;
    }

    // Uploaded media files — cache first (UUIDs never change)
    // Videos require Range request handling — see serveMedia()
    if (url.pathname.startsWith('/uploads/')) {
        event.respondWith(serveMedia(req));
        return;
    }

    // Player HTML page (/display/<token>) — network first, page cache fallback
    if (url.pathname.startsWith('/display/')) {
        event.respondWith(networkFirst(req, PAGE_CACHE));
        return;
    }
});

// ── Message handler: pre-warm media cache when player receives a new playlist
self.addEventListener('message', event => {
    const data = event.data || {};

    if (data.type === 'prefetch_media') {
        const urls = data.urls || [];
        if (!urls.length) return;
        event.waitUntil(prefetchMediaUrls(urls));
        return;
    }

    if (data.type === 'prefetch_plugins') {
        const urls = data.urls || [];
        if (!urls.length) return;
        event.waitUntil((async () => {
            const cache = await caches.open(STATIC_CACHE);
            await Promise.all(urls.map(async (url) => {
                try {
                    const existing = await cache.match(url);
                    if (existing) return;
                    const resp = await fetch(url);
                    if (resp.ok) await cache.put(url, resp.clone());
                } catch (_) {}
            }));
        })());
        return;
    }

    if (data.type === 'prefetch_page') {
        const urls = data.urls || [];
        if (!urls.length) return;
        event.waitUntil((async () => {
            const cache = await caches.open(PAGE_CACHE);
            await Promise.all(urls.map(async (url) => {
                try {
                    const existing = await cache.match(url);
                    if (existing) return;
                    const resp = await fetch(url);
                    if (resp.ok) await cache.put(url, resp.clone());
                } catch (_) {}
            }));
        })());
        return;
    }
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function mediaCacheKey(urlStr) {
    const url = new URL(urlStr, self.location.origin);
    if (url.pathname.startsWith('/uploads/')) {
        return url.origin + url.pathname;
    }
    return urlStr;
}

function mediaCacheRequest(urlStr) {
    return new Request(mediaCacheKey(urlStr));
}

async function cachedMediaResponse(cache, request) {
    const direct = await cache.match(request);
    if (direct) return direct;
    const canonical = mediaCacheRequest(request.url);
    if (canonical.url !== request.url) {
        return cache.match(canonical);
    }
    return null;
}

async function prefetchMediaUrls(urls) {
    const cache = await caches.open(MEDIA_CACHE);
    let idx = 0;
    const workers = Math.min(PREFETCH_MEDIA_CONCURRENCY, urls.length);
    async function worker() {
        while (idx < urls.length) {
            const i = idx++;
            const url = urls[i];
            try {
                const key = mediaCacheRequest(url);
                if (await cache.match(key)) continue;
                const resp = await fetch(url, { headers: {} });
                if (resp.ok) await cache.put(key, resp.clone());
            } catch (_) {}
        }
    }
    await Promise.all(Array.from({ length: workers }, () => worker()));
}

/**
 * Serve uploaded media (images/videos) with Range request support.
 *
 * Videos send `Range: bytes=N-M` requests and require a 206 Partial Content
 * response. If the SW returns a full 200 response from cache, browsers will
 * refuse to play the video. So we:
 *   1. Try network first when online (let the server handle Range natively)
 *   2. On network failure, look up the FULL response in cache
 *   3. If a Range header is present, slice the cached body and return 206
 *   4. Otherwise return the full cached 200 response
 *
 * The cache always stores the FULL file (no Range), keyed by canonical path
 * so signed query parameters can rotate without orphaning cached bytes.
 */
async function serveMedia(request) {
    const cache = await caches.open(MEDIA_CACHE);
    const url   = request.url;
    const range = request.headers.get('range');

    // Try network first — preserves native Range support when online
    try {
        const response = await fetch(request);
        if (response.ok || response.status === 206) {
            // Background-store the FULL file so we can serve any byte range
            // from cache when offline. Skip if we already have a cached copy.
            cachedMediaResponse(cache, request).then(existing => {
                if (existing) return;
                // Fetch with the signed URL so the server authorizes the
                // download, but store under the stable unsigned cache key.
                fetch(url, { headers: {} }).then(full => {
                    if (full.ok) cache.put(mediaCacheRequest(url), full.clone()).catch(() => {});
                }).catch(() => {});
            });
            return response;
        }
        return response;
    } catch (err) {
        // Network failed — serve from cache
        const cached = await cachedMediaResponse(cache, request);
        if (!cached) {
            return new Response('', { status: 503, statusText: 'Service Unavailable' });
        }
        if (!range) {
            return cached;
        }
        // Browser asked for a byte range — slice the cached blob and return 206
        const blob  = await cached.blob();
        const total = blob.size;
        const m     = /bytes=(\d+)-(\d*)/.exec(range);
        if (!m) return cached;
        const start = parseInt(m[1], 10);
        const end   = m[2] ? parseInt(m[2], 10) : total - 1;
        const slice = blob.slice(start, end + 1);
        const headers = new Headers({
            'Content-Type':   cached.headers.get('Content-Type') || 'application/octet-stream',
            'Content-Length': String(slice.size),
            'Content-Range':  `bytes ${start}-${end}/${total}`,
            'Accept-Ranges':  'bytes',
            'Cache-Control':  'no-store',
        });
        return new Response(slice, { status: 206, statusText: 'Partial Content', headers });
    }
}

/** Serve from cache; if missing fetch, cache, and return. */
async function cacheFirst(request, cacheName) {
    const cache  = await caches.open(cacheName);
    const cached = await cache.match(request);
    if (cached) return cached;

    try {
        const response = await fetch(request);
        if (response.ok) cache.put(request, response.clone());
        return response;
    } catch (err) {
        // Nothing cached and network failed — return an empty 503
        return new Response('', { status: 503, statusText: 'Service Unavailable' });
    }
}

/** Try network first; on failure serve cached version. */
async function networkFirst(request, cacheName) {
    const cache = await caches.open(cacheName);
    try {
        const response = await fetch(request);
        if (response.ok) cache.put(request, response.clone());
        return response;
    } catch (err) {
        const cached = await cache.match(request);
        return cached || new Response('', { status: 503, statusText: 'Service Unavailable' });
    }
}

/**
 * Stale-while-revalidate: instantly serve the cached copy if we have one,
 * then fetch a fresh copy in the background and update the cache for next
 * time. If we have no cached copy, wait for the network. If both miss,
 * return a 503.
 *
 * Used for plugin HTML/assets so they keep working when the server is
 * offline, while still picking up updates the next time we reload.
 */
async function staleWhileRevalidate(request, cacheName) {
    const cache  = await caches.open(cacheName);
    const cached = await cache.match(request);
    const networkPromise = fetch(request).then(resp => {
        if (resp && resp.ok) cache.put(request, resp.clone()).catch(() => {});
        return resp;
    }).catch(() => null);

    if (cached) {
        // Don't block the response on the network update; just kick it off.
        networkPromise.catch(() => {});
        return cached;
    }
    const fresh = await networkPromise;
    return fresh || new Response('', { status: 503, statusText: 'Service Unavailable' });
}
