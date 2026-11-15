// AiSignX plugin SDK: loaded by every frame plugin's runner page (index.html), before main.js.
// The runner lives in an opaque-origin sandbox (sandbox="allow-scripts") with connect-src 'none':
// no cookies, no storage, no network. Everything arrives from the player (host.js) over a private
// MessageChannel. Classic script, ES2018 at most; never uses innerHTML or eval.
//
//   AiSignXPlugin.register({init, show, data, net, pause, resume, destroy})
//
// Every hook is called as hook(ctx, msg); see README.md for ctx and the message fields.
(function () {
  "use strict";

  var V = 1;
  var HELLO_EVERY_MS = 1000, HELLO_TRIES = 8, EVERY_MIN_MS = 50;

  var port = null;          // MessagePort to the host, set once by aisignx:init
  var hooks = null;         // the registered plugin
  var ctx = null;           // built by the "init" message
  var inbox = [];           // host messages that arrived before register()
  var outbox = [];          // plugin messages posted before the port existed
  var timers = {};          // id -> {id, repeat}
  var assets = {};          // asset id -> Promise<object URL>
  var waiting = {};         // asset id -> {resolve, reject}
  var urls = [];            // object URLs to revoke on destroy
  var offset = 0;           // server clock minus local clock, ms
  var isReady = false, degraded = false, dead = false;
  var overlay = { fallback: null, stale: null, attribution: null };

  function post(t, extra) {
    var m = extra || {};
    m.t = t;
    if (dead) return;
    if (!port) { outbox.push(m); return; }
    try { port.postMessage(m); } catch (e) { /* port gone */ }
  }

  // Runs a plugin hook; a throw (or a rejected promise from an async hook) is reported, and is
  // fatal only in init.
  function call(name, msg) {
    if (!hooks || typeof hooks[name] !== "function") return;
    var fail = function (e) {
      post("error", { message: String((e && e.message) || e).slice(0, 200), fatal: name === "init" });
    };
    try {
      var r = hooks[name](ctx, msg);
      if (r && typeof r.then === "function") r.then(null, fail);
    } catch (e) {
      fail(e);
    }
  }

  // ---- Timers: plugins use ctx.every / ctx.later so destroy can stop them all.
  function addTimer(fn, ms, repeat) {
    var t = { repeat: repeat, id: 0 };
    var run = function () {
      if (dead) return;
      if (!repeat) delete timers[t.id];
      try { fn(); } catch (e) { post("error", { message: String((e && e.message) || e).slice(0, 200), fatal: false }); }
    };
    ms = Math.max(repeat ? EVERY_MIN_MS : 0, Number(ms) || 0);
    t.id = repeat ? setInterval(run, ms) : setTimeout(run, ms);
    timers[t.id] = t;
    return t.id;
  }
  // aligned(fn, ms): fn runs at every multiple of ms on the SERVER clock (ctx.now()), not every ms
  // after this frame happened to load. Screens that load a moment apart then rotate and repaint
  // together, which synchronized playback needs. Cleared with ctx.clear like the others.
  var aligned = {}, alignedSeq = 0;
  function addAligned(fn, ms) {
    ms = Math.max(100, Number(ms) || 1000);
    var token = "al" + (++alignedSeq);
    var arm = function () {
      if (dead) return;
      var wait = ms - (ctx.now() % ms);               // to the next boundary (a whole interval at most)
      aligned[token] = addTimer(function () { arm(); fn(); }, wait + 2, false);
    };
    arm();
    return token;
  }
  function clearTimer(id) {
    if (typeof id === "string" && aligned[id] !== undefined) {
      var inner = aligned[id];
      delete aligned[id];
      clearTimer(inner);
      return;
    }
    var t = timers[id];
    if (!t) return;
    if (t.repeat) clearInterval(id); else clearTimeout(id);
    delete timers[id];
  }

  // ---- DOM helpers (textContent only).
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function setOverlay(name, node) {
    var old = overlay[name];
    if (old && old.parentNode) old.parentNode.removeChild(old);
    overlay[name] = node;
    if (node) document.body.appendChild(node);
  }

  function format(ms, opts) {
    var o = {};
    Object.keys(opts || {}).forEach(function (k) { o[k] = opts[k]; });
    if (ctx && ctx.tz && !o.timeZone) o.timeZone = ctx.tz;
    var loc = (ctx && ctx.locale) || undefined;
    try { return new Intl.DateTimeFormat(loc, o).format(ms); } catch (e) {
      delete o.timeZone;
      try { return new Intl.DateTimeFormat(undefined, o).format(ms); } catch (e2) { return new Date(ms).toISOString(); }
    }
  }

  function buildCtx(m) {
    offset = Number(m.server_offset_ms) || 0;
    var c = {
      key: String(m.key || ""),
      root: document.getElementById("root") || document.body,
      config: m.config && typeof m.config === "object" ? m.config : {},
      tz: typeof m.tz === "string" ? m.tz : "",
      locale: typeof m.locale === "string" ? m.locale : "",
      duration_ms: Number(m.duration_ms) || 0,
      attributionText: typeof m.attribution === "string" ? m.attribution : "",
      data: null, stale: false, warming: false, fetchedAt: 0, offline: !!m.offline, assets: [],
      now: function () { return Date.now() + offset; },
      every: function (fn, ms) { return addTimer(fn, ms, true); },
      aligned: addAligned,
      later: function (fn, ms) { return addTimer(fn, ms, false); },
      clear: clearTimer,
      format: format,
      ready: function () { if (!isReady) { isReady = true; post("ready"); } },
      complete: function () { post("complete"); },
      error: function (message, fatal) { post("error", { message: String(message || "").slice(0, 200), fatal: !!fatal }); },
      asset: asset,
      fallback: fallback,
      staleBadge: staleBadge,
      attribution: attribution,
      fit: fit,
    };
    return c;
  }
  // Copies the data fields of an init/data message onto ctx.
  function takeData(m) {
    ctx.data = m.data === undefined ? ctx.data : m.data;
    ctx.stale = !!m.stale;
    ctx.warming = !!m.warming;
    ctx.fetchedAt = Number(m.fetched_at_ms) || 0;
    ctx.fetched_at_ms = ctx.fetchedAt;
    if (Array.isArray(m.assets)) ctx.assets = m.assets;
  }

  // asset(id) -> Promise<object URL> for an asset listed in the current data. Revoked on destroy.
  function asset(id) {
    id = String(id || "");
    if (!/^[0-9a-f]{32}$/.test(id)) return Promise.reject(new Error("bad asset id"));
    if (!assets[id]) {
      assets[id] = new Promise(function (resolve, reject) { waiting[id] = { resolve: resolve, reject: reject }; });
      assets[id].then(null, function () { delete assets[id]; });  // a failed asset may be asked for again
      post("want-asset", { id: id });
    }
    return assets[id];
  }
  function onAsset(m) {
    var w = waiting[m.id];
    if (!w) return;
    delete waiting[m.id];
    if (m.error || !m.buf) { w.reject(new Error("asset unavailable")); return; }
    try {
      var url = URL.createObjectURL(new Blob([m.buf], { type: String(m.mime || "application/octet-stream") }));
      urls.push(url);
      w.resolve(url);
    } catch (e) { w.reject(e); }
  }

  // fallback(title, msg): a full-screen card (offline, loading, no data). It makes the slide
  // presentable (ready) and tells the player it is degraded, so a busy playlist moves on
  // after at most 10 s. fallback(null) removes the card.
  function fallback(title, msg) {
    if (title == null) { setOverlay("fallback", null); return; }
    var card = el("div", "aisx-fallback");
    var box = card.appendChild(el("div", "aisx-fallback-box"));
    box.appendChild(el("p", "aisx-fallback-title", String(title)));
    if (msg) box.appendChild(el("p", "aisx-fallback-msg", String(msg)));
    setOverlay("fallback", card);
    if (!degraded) { degraded = true; post("degraded"); }
    ctx.ready();
  }
  // staleBadge(fetchedAtMs): "Updated 14:32" in a corner; a falsy value removes it.
  function staleBadge(at) {
    at = Number(at) || 0;
    if (!at) { setOverlay("stale", null); return; }
    var sameDay = format(at, { year: "numeric", month: "2-digit", day: "2-digit" }) ===
      format(ctx.now(), { year: "numeric", month: "2-digit", day: "2-digit" });
    var when = format(at, sameDay ? { hour: "2-digit", minute: "2-digit" }
      : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    setOverlay("stale", el("div", "aisx-stale", (ctx.offline ? "Offline · updated " : "Updated ") + when));
  }
  // attribution(text): the data-source line at the bottom; empty removes it.
  function attribution(text) {
    text = text == null ? ctx.attributionText : String(text);
    setOverlay("attribution", text ? el("div", "aisx-attribution", text) : null);
  }
  // fit(el): scale el down (never up) so it fits inside its parent. Returns the scale.
  function fit(node) {
    if (!node || !node.parentNode) return 1;
    node.style.transform = "";
    var p = node.parentNode;
    var w = node.scrollWidth || 1, h = node.scrollHeight || 1;
    var s = Math.min(1, (p.clientWidth || w) / w, (p.clientHeight || h) / h);
    if (s < 1) {
      node.style.transformOrigin = "center center";
      node.style.transform = "scale(" + s.toFixed(4) + ")";
    }
    return s;
  }

  function destroy(m) {
    call("destroy", m);
    dead = true;
    Object.keys(timers).forEach(function (id) { clearTimer(Number(id)); });
    urls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) { /* ignore */ } });
    urls = [];
    Object.keys(waiting).forEach(function (id) { waiting[id].reject(new Error("destroyed")); });
    waiting = {};
    try { port.close(); } catch (e) { /* ignore */ }
  }

  function handle(m) {
    if (dead || !m || typeof m !== "object" || typeof m.t !== "string") return;
    if (m.t === "init") {
      if (ctx) return;
      ctx = buildCtx(m);
      takeData(m);
      call("init", m);
      return;
    }
    if (!ctx) return;
    switch (m.t) {
      case "data": takeData(m); call("data", m); break;
      case "asset": onAsset(m); break;
      case "net": ctx.offline = !!m.offline; call("net", m); break;
      case "show": case "pause": case "resume": call(m.t, m); break;
      case "destroy": destroy(m); break;
      default: break;
    }
  }
  function onPort(e) {
    if (!hooks) { inbox.push(e.data); return; }
    handle(e.data);
  }

  // ---- Handshake: say hello until the host hands over the private port (accepted once).
  window.addEventListener("message", function (e) {
    if (port || e.source !== window.parent) return;
    var d = e.data;
    if (!d || d.t !== "aisignx:init" || d.v !== V || !e.ports || !e.ports[0]) return;
    port = e.ports[0];
    port.onmessage = onPort;
    var queued = outbox; outbox = [];
    queued.forEach(function (m) { try { port.postMessage(m); } catch (err) { /* ignore */ } });
  });
  window.addEventListener("error", function (e) {
    post("error", { message: String((e && e.message) || "script error").slice(0, 200), fatal: false });
  });
  var tries = 0;
  (function hello() {
    if (port || dead || window.parent === window) return;
    try { window.parent.postMessage({ t: "aisignx:hello", v: V }, "*"); } catch (e) { /* ignore */ }
    if (++tries < HELLO_TRIES) setTimeout(hello, HELLO_EVERY_MS);
  })();

  window.AiSignXPlugin = {
    v: V,
    register: function (h) {
      if (hooks || !h || typeof h !== "object") return;
      hooks = h;
      var queued = inbox; inbox = [];
      queued.forEach(handle);
    },
  };
})();
