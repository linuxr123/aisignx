// AiSignX browser player: host side of frame plugins (the runner pages use sdk.js).
// The player page brokers all data: it fetches /api/player/v1/plugins/<id>/data with its own
// cookie, keeps the last good copy and hands it to the opaque-origin frame over a private
// MessageChannel. Classic script, ES2018 at most.
//
//   A.pluginHost.create(iframe, item, io) -> handle {ready, show, pause, resume, destroy, on, degraded}
//   A.pluginHost.setServerTime(ms), setOffline(bool), warm()
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var V = 1;
  var READY_MS = 8000;          // render.js passes PLUGIN_READY_MS
  var INIT_WAIT_MS = 2500;      // how long init waits for the first data fetch
  var FETCH_TIMEOUT_MS = 10000;
  var RETRY_MIN_S = 5, RETRY_MAX_S = 60, ERROR_RETRY_S = 30, REFRESH_MIN_S = 15, REFRESH_DEFAULT_S = 300;
  var LS_PREFIX = "aisignx.plugin.", LS_INDEX = "aisignx.plugins.lru";
  var LS_ENTRY_MAX = 256 * 1024, LS_TOTAL_MAX = 1536 * 1024;
  var CACHE_NAME = "aisignx-plugin-v1", MEM_MAX = 64 * 1024 * 1024, CACHE_WAIT_MS = 1500;
  var ASSET_ID = /^[0-9a-f]{32}$/;
  var ASSET_MIME = { "image/png": 1, "image/jpeg": 1, "image/webp": 1 };

  var live = [];      // handles not yet destroyed
  var offset = 0;     // server clock - local clock (ms)
  var offline = false;
  var warmed = false;

  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
  function log(msg) { if (window.console) console.info("[plugin] " + msg); }

  // ---- Last good copy: localStorage, LRU by last use, wrapped (storage may throw).
  function ls() { try { return window.localStorage || null; } catch (e) { return null; } }
  function lsIndex(s) {
    var idx = null;
    try { idx = JSON.parse(s.getItem(LS_INDEX) || "null"); } catch (e) { idx = null; }
    if (idx && typeof idx === "object" && !Array.isArray(idx)) return idx;
    idx = {};
    for (var i = 0; i < s.length; i++) {
      var k = s.key(i);
      if (k && k.indexOf(LS_PREFIX) === 0) idx[k] = [(s.getItem(k) || "").length, 0];
    }
    return idx;
  }
  function saveCopy(mid, entry) {
    var s = ls();
    if (!s) return;
    var key = LS_PREFIX + mid;
    try {
      var raw = JSON.stringify(entry), idx = lsIndex(s);
      delete idx[key];
      if (raw.length > LS_ENTRY_MAX) {
        s.removeItem(key);
      } else {
        var keys = Object.keys(idx).sort(function (a, b) { return idx[a][1] - idx[b][1]; });
        var total = raw.length;
        keys.forEach(function (k) { total += idx[k][0]; });
        while (total > LS_TOTAL_MAX && keys.length) {
          var old = keys.shift();
          total -= idx[old][0];
          s.removeItem(old);
          delete idx[old];
        }
        s.setItem(key, raw);
        idx[key] = [raw.length, Date.now()];
      }
      s.setItem(LS_INDEX, JSON.stringify(idx));
    } catch (e) {
      try { s.removeItem(key); } catch (e2) { /* ignore */ }
    }
  }
  function loadCopy(mid) {
    var s = ls();
    if (!s) return null;
    try {
      var key = LS_PREFIX + mid, e = JSON.parse(s.getItem(key) || "null");
      if (!e || typeof e !== "object" || !e.body || typeof e.body !== "object") return null;
      var idx = lsIndex(s);
      if (idx[key]) { idx[key][1] = Date.now(); s.setItem(LS_INDEX, JSON.stringify(idx)); }
      return e;
    } catch (err) { return null; }
  }

  // ---- Assets: Cache Storage where it exists (secure contexts, e.g. Electron's 127.0.0.1),
  // otherwise an in-memory LRU. Assets are content-addressed, so a cached copy never changes.
  var mem = [], memSize = 0;  // [{url, buf, t}]
  function memGet(url) {
    for (var i = 0; i < mem.length; i++) if (mem[i].url === url) { mem[i].t = Date.now(); return mem[i].buf.slice(0); }
    return null;
  }
  function memPut(url, buf) {
    if (buf.byteLength > MEM_MAX || memGet(url)) return;
    mem.push({ url: url, buf: buf.slice(0), t: Date.now() });
    memSize += buf.byteLength;
    mem.sort(function (a, b) { return a.t - b.t; });
    while (memSize > MEM_MAX && mem.length) memSize -= mem.shift().buf.byteLength;
  }
  function hasCaches() { try { return !!window.caches && typeof window.caches.open === "function"; } catch (e) { return false; } }
  // p, or null after ms or on failure: a slow or broken Cache Storage never holds an asset.
  function within(p, ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms, null); p.then(resolve, function () { resolve(null); }); });
  }
  function loadAsset(h, url) {
    var cached = hasCaches()
      ? within(window.caches.open(CACHE_NAME).then(function (c) { return c.match(url); })
        .then(function (r) { return r ? r.arrayBuffer() : null; }), CACHE_WAIT_MS)
      : Promise.resolve(memGet(url));
    return cached.then(function (buf) {
      if (buf) return buf;
      return timedFetch(h, url, {}).then(function (res) {
        if (!res.ok) throw new Error("asset http " + res.status);
        return res.arrayBuffer();
      }).then(function (got) {
        if (hasCaches()) {
          var copy = got.slice(0);
          window.caches.open(CACHE_NAME).then(function (c) { return c.put(url, new Response(copy)); })
            .then(null, function () { /* quota: ignore */ });
        } else {
          memPut(url, got);
        }
        return got;
      });
    });
  }
  // Drops cached assets of this media that neither the current nor the previous data uses.
  function pruneAssets(h, keep) {
    if (!hasCaches()) return;
    window.caches.open(CACHE_NAME).then(function (c) {
      return c.keys().then(function (reqs) {
        reqs.forEach(function (r) {
          var u = String(r.url || r), i = u.indexOf(h.assetBase);
          if (i < 0) return;
          var id = u.slice(i + h.assetBase.length);
          if (!keep[id]) c["delete"](r);
        });
      });
    }).then(null, function () { /* ignore */ });
  }

  // ---- Data fetching.
  function timedFetch(h, url, opts) {
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    var init = { credentials: "same-origin", cache: "no-store", headers: opts.headers || {} };
    if (ctrl) init.signal = ctrl.signal;
    var timer;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () { if (ctrl) ctrl.abort(); reject(new Error("timeout")); }, FETCH_TIMEOUT_MS);
    });
    return Promise.race([h.io.fetch(url, init), timeout]).then(function (r) {
      clearTimeout(timer); return r;
    }, function (e) { clearTimeout(timer); throw e; });
  }
  function refreshS(h) {
    var b = h.body, s = Number((b && b.refresh_s) || h.p.refresh_s) || REFRESH_DEFAULT_S;
    return Math.max(REFRESH_MIN_S, s);
  }
  function schedule(h, seconds) {
    clearTimeout(h.timer);
    h.timer = h.dead ? null : setTimeout(function () { h.timer = null; fetchData(h); }, seconds * 1000);
  }
  function isStale(h) {
    var b = h.body;
    if (!b) return false;
    return !!b.stale || Date.now() + offset - (Number(b.fetched_at_ms) || 0) > 2 * refreshS(h) * 1000;
  }
  function dataMsg(h, t) {
    var b = h.body;
    return { t: t, data: b ? b.data : null, stale: isStale(h), warming: !b && (h.warming || !h.firstDone),
      fetched_at_ms: b ? Number(b.fetched_at_ms) || 0 : 0, assets: b && Array.isArray(b.assets) ? b.assets : [] };
  }
  function fetchData(h) {
    if (h.dead || h.inflight || !h.p.data) return;
    h.inflight = true;
    clearTimeout(h.timer);
    var headers = { Accept: "application/json" };
    if (h.body && h.etag) headers["If-None-Match"] = h.etag;
    var next = 0;  // seconds until the next try; 0 = the normal refresh (only while shown)
    timedFetch(h, h.p.data, { headers: headers }).then(function (res) {
      if (res.status === 304) { h.checked = true; return null; }
      if (res.status === 200) {
        return res.json().then(function (body) {
          if (!body || typeof body !== "object" || body.v !== 1) throw new Error("bad data");
          onBody(h, body, res.headers.get("ETag") || "");
        });
      }
      if (res.status === 503) {
        h.warming = true;
        next = clamp(parseInt(res.headers.get("Retry-After") || "", 10) || RETRY_MIN_S, RETRY_MIN_S, RETRY_MAX_S);
        return null;
      }
      throw new Error("http " + res.status);
    }).then(null, function (err) {
      log("data for " + h.mid + ": " + (err && err.message));
      next = Math.min(ERROR_RETRY_S, refreshS(h));
    }).then(function () {
      h.inflight = false;
      h.firstDone = true;
      h.lastAt = Date.now();
      if (h.dead) return;
      if (next) schedule(h, next);
      else if (h.shown) schedule(h, refreshS(h));
      maybeInit(h);
    });
  }
  // Asset ids (-> mime) of a data body.
  function assetMap(body, into) {
    var m = into || {};
    (body && Array.isArray(body.assets) ? body.assets : []).forEach(function (a) {
      if (a && typeof a.id === "string") m[a.id] = String(a.mime || "");
    });
    return m;
  }
  function onBody(h, body, etag) {
    var keep = assetMap(body, assetMap(h.body));  // the previous data's assets stay valid
    h.body = body;
    h.etag = etag;
    h.warming = false;
    h.checked = true;
    h.known = keep;
    if (h.persist) saveCopy(h.mid, { etag: etag, src: h.src, body: body });
    pruneAssets(h, keep);
    if (h.inited) post(h, dataMsg(h, "data"));
  }

  // ---- Messages.
  function post(h, m, transfer) {
    if (!h.port || h.dead) return;
    try { h.port.postMessage(m, transfer || []); } catch (e) { /* closed */ }
  }
  function emit(h, name, a, b) {
    var fn = h.handlers[name];
    if (typeof fn === "function") { try { fn(a, b); } catch (e) { if (window.console) console.error(e); } }
  }
  function settle(h, err) {
    if (h.settled) return;
    h.settled = true;
    clearTimeout(h.readyTimer);
    if (err) h.reject(err); else h.resolve();
  }
  function maybeInit(h) {
    if (!h.port || h.inited || h.dead) return;
    if (h.p.data && !h.firstDone && !h.body && !h.initWaitOver) return;
    h.inited = true;
    clearTimeout(h.initTimer);
    var p = h.p, cfg = p.config && typeof p.config === "object" ? p.config : {};
    var m = dataMsg(h, "init");
    m.key = String(p.key || "");
    m.config = cfg;
    m.offline = offline;
    m.server_offset_ms = offset;
    m.tz = String(p.tz || cfg.timezone || "");
    m.locale = String(p.locale || "");
    m.duration_ms = Number(h.item.duration_ms) || 0;
    m.attribution = String(p.attribution || "");
    post(h, m);
    if (h.shown) post(h, { t: "show" });
  }
  function sendAsset(h, id) {
    id = String(id || "");
    var mime = ASSET_ID.test(id) && h.known && Object.prototype.hasOwnProperty.call(h.known, id) ? h.known[id] : "";
    if (!ASSET_MIME[mime]) {
      post(h, { t: "asset", id: id, error: "unknown" });
      return;
    }
    loadAsset(h, h.assetBase + id).then(function (buf) {
      post(h, { t: "asset", id: id, mime: mime, buf: buf }, [buf]);
    }, function () {
      post(h, { t: "asset", id: id, error: "unavailable" });
    });
  }
  function onPluginMsg(h, m) {
    if (h.dead || !m || typeof m !== "object" || typeof m.t !== "string") return;
    if (m.t === "ready") settle(h, null);
    else if (m.t === "error") {
      var text = String(m.message || "plugin error").slice(0, 200);
      if (m.fatal && !h.settled) settle(h, new Error("plugin error: " + text));
      else emit(h, "error", text, !!m.fatal);
    } else if (m.t === "complete") {
      if (h.p.ends_itself) emit(h, "complete");
    } else if (m.t === "want-asset") sendAsset(h, m.id);
    else if (m.t === "degraded") { h.degraded = true; emit(h, "degraded"); }
  }

  // Handshake: accepted once per frame, only from that frame and only from an opaque origin.
  function connect(h) {
    var ch = new MessageChannel();
    ch.port1.onmessage = function (e) { onPluginMsg(h, e.data); };
    try {
      h.frame.contentWindow.postMessage({ t: "aisignx:init", v: V }, "*", [ch.port2]);
    } catch (e) { try { ch.port1.close(); } catch (e2) { /* ignore */ } return; }
    h.port = ch.port1;
    if (h.p.data && !h.firstDone && !h.body) {
      h.initTimer = setTimeout(function () { h.initWaitOver = true; maybeInit(h); }, INIT_WAIT_MS);
    }
    maybeInit(h);
  }
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || typeof d !== "object" || d.t !== "aisignx:hello" || d.v !== V) return;
    for (var i = 0; i < live.length; i++) {
      var h = live[i];
      if (h.frame.contentWindow && e.source === h.frame.contentWindow) {
        if (h.port || h.dead || e.origin !== "null") return;
        connect(h);
        return;
      }
    }
  });

  // create(iframe, item, io): call before setting iframe.src. io (optional): {fetch, readyMs,
  // persist}; persist: false keeps no last good copy (the admin preview shares the origin).
  function create(frame, item, io) {
    io = io || {};
    var p = item.plugin || {};
    var dataUrl = typeof p.data === "string" && p.data ? p.data : null;
    var h = {
      frame: frame, item: item, p: { key: p.key, config: p.config, data: dataUrl, refresh_s: p.refresh_s,
        ends_itself: !!p.ends_itself, tz: p.tz, locale: p.locale, attribution: p.attribution },
      mid: String(item.media_id), io: { fetch: io.fetch || function (u, o) { return window.fetch(u, o); } },
      src: String(item.url || ""), persist: io.persist !== false,
      assetBase: dataUrl ? dataUrl.replace(/\/data(\?.*)?$/, "/assets/") : "",
      port: null, body: null, etag: "", known: null, warming: false, inflight: false, firstDone: false,
      checked: false, lastAt: 0, inited: false, initWaitOver: false, shown: false, paused: false, dead: false,
      degraded: false, settled: false, timer: null, initTimer: null, handlers: {},
    };
    h.ready = new Promise(function (resolve, reject) { h.resolve = resolve; h.reject = reject; });
    h.ready.then(null, function () { /* the caller sees it; never an unhandled rejection */ });
    h.readyTimer = setTimeout(function () { settle(h, new Error("plugin not ready")); }, io.readyMs || READY_MS);
    if (dataUrl) {
      // The saved copy counts only for the same config (item.url ends in #c=<config hash>).
      var saved = h.persist ? loadCopy(h.mid) : null;
      if (saved && saved.body.v === 1 && saved.src === h.src) {
        h.body = saved.body;
        h.etag = String(saved.etag || "");
        h.known = assetMap(h.body);
      }
      fetchData(h);
    }
    live.push(h);
    return {
      ready: h.ready,
      item: item,
      on: function (name, fn) { h.handlers[name] = fn; },
      degraded: function () { return h.degraded; },
      show: function () {
        if (h.dead || h.shown) return;
        h.shown = true;
        if (h.inited) post(h, { t: "show" });
        if (dataUrl && !h.inflight && !h.timer) {
          schedule(h, Math.max(0, h.lastAt + refreshS(h) * 1000 - Date.now()) / 1000);
        }
      },
      pause: function () { if (!h.paused && !h.dead) { h.paused = true; post(h, { t: "pause" }); } },
      resume: function () { if (h.paused && !h.dead) { h.paused = false; post(h, { t: "resume" }); } },
      destroy: function () {
        if (h.dead) return;
        post(h, { t: "destroy" });
        h.dead = true;
        clearTimeout(h.timer); clearTimeout(h.initTimer); clearTimeout(h.readyTimer);
        if (!h.settled) { h.settled = true; h.reject(new Error("dropped")); }
        if (h.port) { try { h.port.close(); } catch (e) { /* ignore */ } }
        var i = live.indexOf(h);
        if (i >= 0) live.splice(i, 1);
      },
    };
  }

  function setServerTime(ms) { var n = Number(ms); if (n > 0 && isFinite(n)) offset = n - Date.now(); }
  function setOffline(on) {
    if (!!on === offline) return;
    offline = on = !!on;
    live.forEach(function (h) { if (h.inited) post(h, { t: "net", offline: on }); });
  }

  // Kiosk shells cache only what the page fetches; fetch every runner file once per page load so
  // the shell holds them all (Android promotes a new page only when every listed file is stored).
  function warm() {
    var sh = null;
    try { sh = A.net && A.net.shell ? A.net.shell() : null; } catch (e) { sh = null; }
    if (warmed || !sh) return;
    warmed = true;
    var tpl = document.getElementById("plugin-files");
    var root = tpl && (tpl.content || tpl);
    var links = root && root.querySelectorAll ? root.querySelectorAll("link[href]") : [];
    var urls = [];
    for (var i = 0; i < links.length; i++) urls.push(links[i].getAttribute("href"));
    (function next() {
      var u = urls.shift();
      if (!u) return;
      window.fetch(u, { credentials: "same-origin" }).then(function (r) { return r.arrayBuffer(); })
        .then(next, next);
    })();
  }

  A.pluginHost = { create: create, setServerTime: setServerTime, setOffline: setOffline, warm: warm,
    offset: function () { return offset; } };
  setTimeout(warm, 5000);
})();
