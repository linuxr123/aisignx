// AiSignX browser player: state machine, pairing, playback loop, live updates.
// Classic script, ES2018 at most. States: CONNECTING, SETUP_WAIT, PAIRING, PLAYING, NO_CONTENT.
(function () {
  "use strict";

  var A = window.AiSignX;
  var net = A.net, R = A.render, EM = A.emergency, store = net.store, K = net.store.KEYS;
  var body = document.body, screenEl = document.getElementById("screen");
  var BUILD = body.getAttribute("data-build") || "";
  var ROOT = body.getAttribute("data-root") || "";
  // Kiosk client hook (window.AiSignXShell, or the Android adapter in net.js); null in a browser.
  var shell = net.shell();
  var PLAYER_VERSION = (shell ? shell.info.name + "-" + shell.info.version : "web-" + BUILD).slice(0, 32);
  // Behind the Electron loopback proxy location.origin is 127.0.0.1, so prefer the shell's server URL.
  var ADMIN_URL = (shell && shell.info.admin_url) || location.origin + ROOT;
  var DEBUG = /[?&]debug=1(&|$)/.test(location.search);
  var CLIENT_ID = net.clientId();

  var TICK_MS = 250, PRELOAD_MS = 3000, FAIL_DWELL_MS = 2000, DEFAULT_DURATION_MS = 10000;
  var NO_CONTENT_RETRY_MS = 30000, SETUP_RETRY_MS = 10000, PAIRED_SHOW_MS = 2000;
  var TYPES = { image: 1, video: 1, web: 1 };

  var S = {
    state: "", mode: 0, gen: 0, timers: [], manifest: null, version: null, items: [],
    cur: null,  // on screen: {slide, item, shownAt, deadline, failed}
    nxt: null,  // being prepared: {index, item, state: loading|ready|wait, slide}
    pos: -1,    // index of the last item shown or attempted
    forceSwap: false, failures: 0, live: false, fetching: false, refetch: false, retryTimer: null,
    pairing: null, countdown: null, reloadPending: false, reloadBusy: false, reloadTriedAt: 0, note: "",
  };
  var manifestBackoff = net.Backoff(2000, 30000);
  var pairBackoff = net.Backoff(2000, 30000);

  function now() { return window.performance && performance.now ? performance.now() : Date.now(); }
  function note(msg) { S.note = msg; if (window.console) console.info("[player] " + msg); }
  function durationOf(item) { var d = Number(item.duration_ms); return d > 0 ? Math.max(1000, d) : DEFAULT_DURATION_MS; }

  // ---- Timers. Mode timers die on any state-machine transition; slide timers on any slide change.
  function enterMode() {
    S.mode++; S.refetch = false; S.pairing = null;
    clearTimeout(S.retryTimer); clearInterval(S.countdown);
  }
  function modeGuard(fn) { var m = S.mode; return function (x) { if (m === S.mode) fn(x); }; }
  function modeLater(fn, ms) { return setTimeout(modeGuard(fn), ms); }
  function slideTimer(fn, ms, repeat) {
    var g = S.gen, run = function () { if (g === S.gen) fn(); };
    S.timers.push({ id: repeat ? setInterval(run, ms) : setTimeout(run, ms), repeat: repeat });
  }
  function every(fn, ms) { slideTimer(fn, ms, true); }
  function clearSlideTimers() {
    S.timers.forEach(function (t) { if (t.repeat) clearInterval(t.id); else clearTimeout(t.id); });
    S.timers = [];
  }

  // ---- Screens (textContent only).
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    e.className = cls || "";
    if (text != null) e.textContent = text;
    return e;
  }
  function showScreen(kind, title, lines) {
    while (screenEl.firstChild) screenEl.removeChild(screenEl.firstChild);
    screenEl.className = "screen screen-" + kind;
    var box = el("div", "screen-box");
    box.appendChild(el("p", "screen-title", title));
    (lines || []).forEach(function (l) { box.appendChild(typeof l === "string" ? el("p", "screen-text", l) : l); });
    screenEl.appendChild(box);
    screenEl.hidden = false;
  }
  function setState(s) {
    S.state = s;
    body.setAttribute("data-state", s.toLowerCase());
    if (shell) shell.onState(s);
  }
  function showConnecting() {
    setState("CONNECTING");
    showScreen("connecting", "Connecting", ["Waiting for the server at " + ((shell && shell.info.admin_url) || location.host) + "."]);
  }

  // ---- Live updates: SSE + heartbeat.
  var events = net.Events({
    hello: function (d) {
      if (d.build && d.build !== BUILD) requestReload();
      if (d.heartbeat_s) hb.setSeconds(d.heartbeat_s);
      if (d.manifest_version !== S.version) fetchManifest();
    },
    manifest: function () { fetchManifest(); },
    command: function (d) { if (d.action === "reload") requestReload(); },
    revoked: function () { fetchManifest(); },  // the JSON 401 leads to PAIRING
    error: function () { fetchManifest(); },    // EventSource cannot see a 401 itself
    // Emergencies show at once; the manifest then confirms. Never cleared by a disconnect.
    emergency: function (d) { EM.show(d); fetchManifest(); },
    emergency_clear: function (d) { EM.clearIf(d && d.id); fetchManifest(); },
  });
  var hb = net.Heartbeat(function () {
    var dpr = window.devicePixelRatio || 1;
    var b = { client_id: CLIENT_ID, player_version: PLAYER_VERSION,
      screen_w: Math.round(window.screen.width * dpr), screen_h: Math.round(window.screen.height * dpr) };
    if (S.cur) b.current_item_id = S.cur.item.id;
    if (S.version) b.manifest_version = S.version;
    return b;
  }, function (res) {
    if (res.kind === "unauthorized") goPairing();
    else if (res.kind === "setup") goSetup();
    else if (res.kind === "ok" && res.data.manifest_version && res.data.manifest_version !== S.version) fetchManifest();
    // network errors: keep playing
  });
  function startLive() {
    if (!S.live) { S.live = true; events.start(); hb.start(S.manifest && S.manifest.heartbeat_s); }
  }
  function stopLive() { S.live = false; events.stop(); hb.stop(); }

  // ---- Manifest.
  function fetchManifest() {
    if (S.fetching) { S.refetch = true; return; }
    S.fetching = true;
    S.refetch = false;
    clearTimeout(S.retryTimer);
    var m = S.mode;
    net.api("/manifest", { etag: S.version }).then(function (res) {
      S.fetching = false;
      if (m === S.mode) onManifestResult(res);
      if (S.refetch) fetchManifest();
    });
  }
  function onManifestResult(res) {
    if (res.kind === "unauthorized") { goPairing(); return; }
    if (res.kind === "setup") { goSetup(); return; }
    if (res.kind === "ok" && typeof res.data.version === "string") {
      manifestBackoff.reset();
      store.local.set(K.manifest, res.data);
      applyManifest(res.data);
      if (shell) shell.onManifest(JSON.stringify(res.data));
    } else if (res.kind === "not_modified") {
      manifestBackoff.reset();
      if (S.state === "NO_CONTENT" && S.items.length) startPlaying();
    } else {
      // Keep playing what we have; retry only while there is nothing (good) to show.
      if (!S.manifest) showConnecting();
      if (!S.manifest || (S.state !== "PLAYING" && S.items.length)) {
        S.retryTimer = modeLater(fetchManifest, Math.max(manifestBackoff.next(), res.retryAfter * 1000));
      }
    }
    startLive();
  }
  function applyManifest(m) {
    try { EM.apply(m); } catch (e) { if (window.console) console.error(e); }
    S.manifest = m;
    S.version = typeof m.version === "string" ? m.version : null;
    S.items = (Array.isArray(m.items) ? m.items : []).filter(function (it) {
      return it && TYPES[it.type] && typeof it.url === "string" && it.url;
    });
    if (m.heartbeat_s) hb.setSeconds(m.heartbeat_s);
    dropNext();
    if (!S.items.length) { showNoContent(false); return; }
    if (S.state !== "PLAYING") { startPlaying(); return; }
    var cur = S.cur;
    if (!cur) { S.pos = -1; return; }
    // The current slide keeps its deadline; the new list takes effect at the boundary,
    // continuing after the current item (or from the start if it is gone).
    S.pos = -1;
    for (var i = 0; i < S.items.length; i++) if (S.items[i].id === cur.item.id) S.pos = i;
    var fresh = S.items[S.pos];
    if (!fresh || fresh.url !== cur.item.url || fresh.fit !== cur.item.fit || fresh.muted !== cur.item.muted) {
      S.forceSwap = true;  // gone or changed: advance now
      cur.deadline = 0;
      tick();
    } else {
      cur.item = fresh;
    }
  }

  // ---- Playback loop.
  function startPlaying() { stopPlayback(); setState("PLAYING"); S.pos = -1; S.failures = 0; tick(); }
  function stopPlayback() { S.gen++; clearSlideTimers(); dropNext(); R.clear(); S.cur = null; S.forceSwap = false; }
  function showNoContent(failed) {
    stopPlayback();
    setState("NO_CONTENT");
    var d = S.manifest && S.manifest.display;
    showScreen("nocontent", (d && d.name) || "AiSignX", [failed
      ? "None of the items could be played. Trying again shortly."
      : "Assign a playlist in the admin."]);
    if (failed) modeLater(fetchManifest, NO_CONTENT_RETRY_MS);
  }
  // Tears down the slide being prepared, even one still loading (stops its download).
  function dropNext() { var n = S.nxt; S.nxt = null; if (n && n.slide) R.teardown(n.slide); }
  // Single-item playlists never swap: an image re-arms its deadline, a video loops.
  function singleRepeat() {
    var cur = S.cur;
    return S.items.length === 1 && cur && !cur.failed && !S.forceSwap && cur.item.id === S.items[0].id;
  }
  function countFailure() {
    S.failures++;
    if (S.failures < S.items.length) return false;
    showNoContent(true);
    return true;
  }

  // Absolute deadlines checked by a 250 ms ticker survive timer throttling.
  function tick() {
    if (S.state !== "PLAYING") return;
    var t = now(), cur = S.cur;
    if (cur && !S.nxt && t >= cur.deadline - PRELOAD_MS) prepareNext();
    if (!cur || t >= cur.deadline) boundary();
  }
  function boundary() {
    if (S.reloadPending) guardedReload();
    if (singleRepeat()) { S.cur.deadline = now() + durationOf(S.cur.item); return; }
    if (!S.nxt) prepareNext();
    else if (S.nxt.state === "ready") swapTo(S.nxt);
    // loading: the old slide stays until the new one is ready; wait: failure dwell
  }
  function prepareNext() {
    if (S.nxt || !S.items.length || singleRepeat()) return;
    var index = (S.pos + 1) % S.items.length;
    var n = { index: index, item: S.items[index], state: "loading", slide: null };
    S.nxt = n;
    var pending = R.prepare(n.item, S.gen);
    n.slide = pending.slide;  // lets dropNext() cancel a slide that is still loading
    pending.then(function (slide) {
      if (S.nxt !== n) { R.teardown(slide); return; }
      n.slide = slide;
      n.state = "ready";
      tick();
    }, function (err) {
      if (S.nxt !== n) return;
      note("skipped item " + n.item.id + ": " + (err && err.message));
      S.pos = n.index;
      n.state = "wait";
      if (countFailure()) return;
      slideTimer(function () { if (S.nxt === n) { S.nxt = null; tick(); } }, FAIL_DWELL_MS, false);
    });
  }
  function swapTo(n) {
    S.nxt = null; S.gen++; clearSlideTimers();
    S.forceSwap = false; S.failures = 0; S.pos = n.index;
    var item = n.item, g = S.gen, t = now();
    R.swap(n.slide, item.transition);
    screenEl.hidden = true;
    S.cur = { slide: n.slide, item: item, shownAt: t, deadline: t + durationOf(item), failed: false };
    EM.sync();  // a fullscreen emergency keeps the new video silent
    if (!R.isVideo(n.slide)) return;
    var guard = function (fn) { return function (x) { if (g === S.gen) fn(x); }; };
    R.runVideo(n.slide, {
      loop: function () {
        return S.items.length === 1 ||
          (item.media_duration_ms != null && durationOf(item) > item.media_duration_ms + 1000);
      },
      done: guard(function () { S.cur.deadline = Math.min(S.cur.deadline, now()); tick(); }),
      fail: guard(slideFailed),
      note: note,
      every: every,
    });
  }
  // The on-screen slide failed (video error or stall): its last frame stays until the next is ready.
  function slideFailed(reason) {
    var cur = S.cur;
    if (!cur || cur.failed) return;
    note("item " + cur.item.id + " failed: " + reason);
    cur.failed = true;
    if (countFailure()) return;
    cur.deadline = Math.min(cur.deadline, Math.max(now(), cur.shownAt + FAIL_DWELL_MS));
    tick();
  }

  // ---- Guarded reload: never reload into a browser error page.
  function requestReload() {
    S.reloadPending = true;
    if (S.state !== "PLAYING") guardedReload();
  }
  function guardedReload() {
    var t = Date.now();
    if (S.reloadBusy || t - S.reloadTriedAt < 10000) return;
    S.reloadBusy = true; S.reloadTriedAt = t;
    var release = setTimeout(function () { S.reloadBusy = false; }, 15000);
    var done = function () { clearTimeout(release); S.reloadBusy = false; };
    fetch(location.pathname + location.search, { cache: "no-store", credentials: "same-origin" })
      .then(function (res) { done(); if (res.status === 200) location.reload(); }, done);
  }

  // ---- Pairing.
  function deviceName() {
    if (shell && shell.info.device) return shell.info.device.slice(0, 120);
    var ua = navigator.userAgent || "", browser = "Browser";
    [["Edg", "Edge"], ["OPR", "Opera"], ["Firefox", "Firefox"], ["Chrome", "Chrome"], ["Version", "Safari"]]
      .some(function (b) {
        var m = new RegExp(b[0] + "/(\\d+)").exec(ua);
        if (m) browser = b[1] + " " + m[1];
        return !!m;
      });
    var os = /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows" : /CrOS/.test(ua) ? "ChromeOS"
      : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
    return (browser + (os ? " / " + os : "")).slice(0, 120);
  }
  function startPairing() {
    var p = store.session.get(K.pairing);
    // Reuse a stored code even if it looks expired: an approval in its last seconds extends
    // it on the server, so let the next poll decide ("expired" starts a new code).
    if (p && p.secret && p.display_code) { showPairing(p); return; }
    store.session.remove(K.pairing);
    showScreen("pairing", "Pair this screen", ["Requesting a pairing code…"]);
    var req = { client_id: CLIENT_ID, device: deviceName(), player_version: PLAYER_VERSION };
    net.api("/pair", { method: "POST", body: req }).then(modeGuard(function (res) {
      if (res.kind === "setup") { goSetup(); return; }
      var d = res.data || {};
      if (res.kind !== "ok" || !d.pairing_secret) {
        showScreen("pairing", "Pair this screen", ["Waiting for the server…"]);
        modeLater(startPairing, Math.max(pairBackoff.next(), res.retryAfter * 1000));
        return;
      }
      pairBackoff.reset();
      p = { secret: d.pairing_secret, display_code: d.display_code || d.code,
        expires_at: Date.now() + (d.expires_in || 600) * 1000, poll_ms: Math.max(1, d.poll_after_s || 3) * 1000 };
      store.session.set(K.pairing, p);
      showPairing(p);
    }));
  }
  function endPairing() { store.session.remove(K.pairing); S.pairing = null; clearInterval(S.countdown); }
  function showPairing(p) {
    S.pairing = p;
    var hint = el("p", "screen-text"), timer = el("p", "screen-sub");
    hint.appendChild(document.createTextNode("Open "));
    hint.appendChild(el("span", "pair-url", ADMIN_URL + "/displays/pair"));
    hint.appendChild(document.createTextNode(" and enter this code."));
    showScreen("pairing", "Pair this screen", [el("p", "pair-code", p.display_code), hint, timer]);
    var update = function () {
      if (S.pairing !== p) return;
      var left = Math.max(0, Math.round((p.expires_at - Date.now()) / 1000));
      // At 0 we keep polling: the server answers "expired" (then we fetch a fresh code) or
      // "paired" if an editor approved it at the last moment.
      timer.textContent = left > 0
        ? "Code expires in " + Math.floor(left / 60) + ":" + (left % 60 < 10 ? "0" : "") + (left % 60)
        : "Getting a new code…";
    };
    clearInterval(S.countdown);
    S.countdown = setInterval(update, 1000);
    update();
    pollPairing(p);
  }
  function pollPairing(p) {
    modeLater(function () {
      if (S.pairing !== p) return;
      var req = { pairing_secret: p.secret, credential: "cookie" };
      net.api("/pair/status", { method: "POST", body: req }).then(modeGuard(function (res) {
        if (S.pairing !== p) return;
        var d = res.data || {};
        if (res.kind === "ok" && d.status === "paired") {
          if (shell) shell.onState("PAIRED");  // lets the shell flush its cookie store to disk
          endPairing();
          showScreen("paired", "Paired", ["Paired as " + ((d.display && d.display.name) || "this display")]);
          modeLater(boot, PAIRED_SHOW_MS);  // boot starts the heartbeat, which beats at once
        } else if (res.kind === "expired" || res.status === 400) { endPairing(); startPairing(); }
        else if (res.kind === "setup") goSetup();
        else pollPairing(p);  // pending, or a network error: keep polling
      }));
    }, p.poll_ms || 3000);
  }

  // ---- State entry points.
  function reset(state) { enterMode(); stopLive(); stopPlayback(); setState(state); }
  function goPairing() {
    if (S.state === "PAIRING") return;  // several 401s can arrive at once; keep one code
    reset("PAIRING");
    EM.hide();
    store.local.remove(K.manifest);
    S.manifest = null; S.version = null; S.items = [];
    startPairing();
  }
  function goSetup() {
    reset("SETUP_WAIT");
    showScreen("setup", "Setup required", ["Finish the setup wizard in the admin. This screen continues by itself."]);
    modeLater(boot, SETUP_RETRY_MS);
  }
  function boot() {
    reset("CONNECTING");
    S.manifest = null; S.version = null; S.items = [];
    var cached = store.local.get(K.manifest);
    if (cached && typeof cached === "object" && typeof cached.version === "string") applyManifest(cached);
    else showConnecting();
    fetchManifest();
  }

  // ---- Debug overlay (?debug=1) and housekeeping.
  var dbg = DEBUG ? body.appendChild(el("pre", "debug")) : null;
  function housekeeping() {
    if (S.reloadPending && S.state !== "PLAYING") guardedReload();
    if (!dbg) return;
    var age = hb.lastAt ? Math.round((Date.now() - hb.lastAt) / 1000) + "s ago (" + hb.lastKind + ")" : "never";
    dbg.textContent = "state      " + S.state + (S.reloadPending ? " (reload pending)" : "") +
      "\nbuild      " + BUILD + "\nmanifest   " + (S.version || "-") + " (" + S.items.length + " items)" +
      "\ncurrent    " + (S.cur ? S.cur.item.id + " " + S.cur.item.type : "-") +
      "\nnext       " + (S.nxt ? S.nxt.item.id + " " + S.nxt.state : "-") + "\nfailures   " + S.failures +
      "\nsse        " + events.status + "\nheartbeat  " + age + "\nnote       " + (S.note || "-");
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) return;
    hb.now();
    if (S.cur) R.resume(S.cur.slide);
    tick();
  });

  EM.current = function () { return S.cur; };
  R.init(document.getElementById("stage"));
  setInterval(tick, TICK_MS);
  setInterval(housekeeping, 1000);
  boot();
})();
