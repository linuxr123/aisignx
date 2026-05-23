/* ============================================================================
 * signage_offline.js — small helper plugins use to keep working when the
 * network is down. Provides cachedFetch() which transparently caches the
 * last successful response body + headers + timestamp for every (url, key)
 * tuple. On a network failure cachedFetch() returns the cached copy with
 * a synthetic 'X-From-Cache: 1' header and the original timestamp tucked
 * into 'X-Cached-At'.
 *
 * Usage in a plugin:
 *
 *     const r = await cachedFetch('https://api.weather.gov/...');
 *     if (!r.ok) return;
 *     const data = await r.json();
 *     const cachedAt = r.headers.get('X-Cached-At');   // ISO-8601 or null
 *     const fromCache = r.headers.get('X-From-Cache') === '1';
 *
 * Storage uses localStorage under keys 'signage_cache:<plugin>:<sha1>' so
 * plugins don't collide. If localStorage isn't available (private mode,
 * disabled DOM storage), cachedFetch degrades gracefully to a plain fetch
 * with no cache layer.
 *
 * Bytes are stored as base64 to handle binary responses (tiles, etc.).
 * Plugins that fetch ONLY JSON can ignore that detail.
 *
 * Designed to be included as:
 *   <script src="/static/js/signage_offline.js"></script>
 * BEFORE the plugin's main.js loads. Plugin runner template includes it
 * automatically.
 * ============================================================================ */
(function () {
  'use strict';

  const PREFIX     = 'signage_cache:';
  const MAX_BYTES  = 4 * 1024 * 1024;   // ~4 MB ceiling per cached response
  const HAS_STORAGE = (function () {
    try { localStorage.setItem('__sig_test', '1'); localStorage.removeItem('__sig_test'); return true; }
    catch (_) { return false; }
  })();

  // Plugin identifies itself via window.PLUGIN_META.type from plugin_runner.html.
  // Falls back to 'unknown' if the plugin loads without that metadata (manual
  // tests etc.) — still works, just gets a shared bucket.
  function pluginNamespace() {
    try { return (window.PLUGIN_META && window.PLUGIN_META.type) || 'unknown'; }
    catch (_) { return 'unknown'; }
  }

  /** Tiny non-crypto hash so the cache key stays bounded for long URLs. */
  function hashKey(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    }
    return (h >>> 0).toString(36);
  }

  function cacheKeyFor(url, opts) {
    const variant = (opts && opts.cacheKey) || '';
    return PREFIX + pluginNamespace() + ':' + hashKey(url + '|' + variant);
  }

  function bytesToBase64(buf) {
    let bin = '';
    const view = new Uint8Array(buf);
    const CHUNK = 0x8000;
    for (let i = 0; i < view.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, view.subarray(i, i + CHUNK));
    }
    return btoa(bin);
  }

  function base64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }

  function readCache(key) {
    if (!HAS_STORAGE) return null;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) { return null; }
  }

  function writeCache(key, entry) {
    if (!HAS_STORAGE) return;
    try {
      localStorage.setItem(key, JSON.stringify(entry));
    } catch (e) {
      // Quota error -- evict our own oldest entries and retry once.
      try { evictOldest(); localStorage.setItem(key, JSON.stringify(entry)); }
      catch (_) { /* give up silently */ }
    }
  }

  /** Drop the oldest 25% of our cache entries when quota is hit. */
  function evictOldest() {
    if (!HAS_STORAGE) return;
    const ours = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(PREFIX)) {
        try {
          const v = JSON.parse(localStorage.getItem(k));
          ours.push({ k, ts: v.ts || 0 });
        } catch (_) { ours.push({ k, ts: 0 }); }
      }
    }
    ours.sort((a, b) => a.ts - b.ts);
    const drop = Math.max(1, Math.floor(ours.length * 0.25));
    for (let i = 0; i < drop; i++) localStorage.removeItem(ours[i].k);
  }

  function buildResponseFromCache(entry) {
    const headers = new Headers(entry.headers || {});
    headers.set('X-From-Cache', '1');
    if (entry.ts) headers.set('X-Cached-At', new Date(entry.ts).toISOString());
    const body = entry.binary ? base64ToBytes(entry.body) : entry.body;
    return new Response(body, {
      status:     entry.status     || 200,
      statusText: entry.statusText || 'OK (cached)',
      headers
    });
  }

  /**
   * Cached fetch. Drop-in replacement for window.fetch with these added
   * options on the second argument:
   *   cacheKey       extra string mixed into the cache key (e.g. when the
   *                  same URL legitimately returns different data per call)
   *   cacheBinary    set true for binary endpoints (tiles, images) so we
   *                  base64-encode the body. JSON/text default works
   *                  without this flag.
   *   noCache        if true, skip the cache layer entirely (debug)
   *
   * The cache layer is best-effort. Any internal exception (storage quota,
   * clone failure, JSON parse error in headers) is swallowed and the call
   * still returns the network response or throws the network error. Plugins
   * see the EXACT same behaviour as a raw fetch() in the success path -- we
   * only kick in when the network has actually failed.
   */
  async function cachedFetch(url, opts) {
    opts = opts || {};
    const key = cacheKeyFor(url, opts);

    if (opts.noCache) return fetch(url, opts);

    let networkResp = null;
    let networkErr  = null;
    try {
      networkResp = await fetch(url, opts);
    } catch (e) { networkErr = e; }

    // Network ok -- save (best-effort) and return the original response.
    // The cache write happens in a fire-and-forget microtask so any error
    // there can't possibly affect the caller -- they get the SAME Response
    // object, body untouched, no .clone() side-effects.
    if (networkResp && networkResp.ok) {
      try {
        const respClone = networkResp.clone();
        // Don't await -- write happens in the background.
        Promise.resolve().then(async () => {
          try {
            const headers = {};
            respClone.headers.forEach((v, k) => { headers[k] = v; });
            let body, binary = false;
            if (opts.cacheBinary) {
              const buf = await respClone.arrayBuffer();
              if (buf.byteLength <= MAX_BYTES) {
                body = bytesToBase64(buf);
                binary = true;
              }
            } else {
              const text = await respClone.text();
              if (text.length <= MAX_BYTES) body = text;
            }
            if (body !== undefined) {
              writeCache(key, {
                ts: Date.now(),
                status: networkResp.status,
                statusText: networkResp.statusText,
                headers, body, binary
              });
            }
          } catch (_) { /* swallow -- cache writes are best-effort */ }
        });
      } catch (_) { /* clone failed -- give up on caching this one */ }
      return networkResp;
    }

    // Network failed (or returned non-ok) -- try cache
    const cached = readCache(key);
    if (cached) return buildResponseFromCache(cached);

    // No cache either -- propagate the failure as a synthetic Response
    if (networkResp) return networkResp;
    throw (networkErr || new Error('Network failed and no cached copy'));
  }

  /**
   * Returns the timestamp (ms since epoch) of the most recent successful
   * cached response for `url`, or null if there's no cache. Plugins can
   * use this to render a "Last updated 12 min ago" badge.
   */
  function lastUpdate(url, opts) {
    const entry = readCache(cacheKeyFor(url, opts || {}));
    return entry ? (entry.ts || null) : null;
  }

  /** Format ms-since-epoch as a friendly relative string. */
  function formatAgo(ts) {
    if (!ts) return 'never';
    const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (sec < 60)        return sec + 's ago';
    if (sec < 3600)      return Math.floor(sec / 60) + 'm ago';
    if (sec < 86400)     return Math.floor(sec / 3600) + 'h ago';
    return Math.floor(sec / 86400) + 'd ago';
  }

  // Expose globally. Plugins use them by name.
  window.cachedFetch = cachedFetch;
  window.signageCache = {
    cachedFetch,
    lastUpdate,
    formatAgo,
    /** True if the parent player has told us the network or server is offline. */
    isOffline: function () { return !!window.SIGNAGE_OFFLINE; }
  };
})();
