// AiSignX browser player: state machine, pairing, playback loop, live updates.
// Classic script, ES2018 at most. States: CONNECTING, SETUP_WAIT, PAIRING, PLAYING, NO_CONTENT.
(function () {
  "use strict";

  var A = window.AiSignX;
  var net = A.net, R = A.render, EM = A.emergency, D = A.device, store = net.store, K = net.store.KEYS;
  var POP = window.AiSignXPop || null;  // proof of play (pop.js); optional
  var noop = function () {};
  // Player controls (controls.js): input recognition only; the mechanics live here, next to S.
  var CT = A.controls || { init: noop, configure: noop, onState: noop, action: noop, hint: noop,
    showPaused: noop, hidePaused: noop };
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
  var PROBE_MS = 10000;  // heartbeat this often while the live connection is down
  var RETRY_AFTER_FAIL_MS = 10000;  // all other items failed: try them again this soon
  var NO_CONTENT_RETRY_MS = 30000, SETUP_RETRY_MS = 10000, PAIRED_SHOW_MS = 2000, DIAG_EVERY = 10;
  var TYPES = { image: 1, video: 1, web: 1, plugin: 1 };
  var SEEK_S = 10, SCRUB_SPAN_S = 120, SCRUB_STEP_MS = 100;
  var SYNC_CHECK_MS = 5000, SYNC_DRIFT_S = 0.35;  // in sync: how often a video is checked, and how far it may drift

  var S = {
    state: "", mode: 0, gen: 0, timers: [], manifest: null, version: null, items: [],
    cur: null,  // on screen: {slide, item, shownAt, deadline, failed}
    nxt: null,  // being prepared: {index, item, state: loading|ready|wait, slide}
    pos: -1,    // index of the last item shown or attempted
    forceSwap: false, failures: 0, live: false, fetching: false, refetch: false, retryTimer: null,
    pairing: null, countdown: null, reloadPending: false, reloadBusy: false, reloadTriedAt: 0, note: "",
    paused: null,  // {resumeAt, remaining, video}: the viewer paused the slide on screen
    scrub: null,   // {slide, t0, target, duration, wasPaused, timer}: a drag-scrub in progress
    skipTo: null,  // index a skip asked for while its slide is still loading
    manualDir: 0,  // +1/-1 while a hand-made next/previous is on its way (its slide moves left/right)
    sync: null,    // {anchor, key}: this display's group plays in sync (the manifest's sync block)
  };
  var manifestBackoff = net.Backoff(2000, 30000);
  var pairBackoff = net.Backoff(2000, 30000);
  var beats = 0, diagWanted = false;
  // A kiosk shell gets one full manifest per page load: the cached copy has no unlock hash,
  // and a 304 never reaches shell.onManifest, so a freshly started (or upgraded) shell
  // would otherwise lack its PIN, orientation and input lock until the content changes.
  var shellFed = false;
  var PH = A.pluginHost || null;  // plugins/host.js: frame plugin data, server clock, online state
  var SY = A.sync || null;        // sync.js: shared clock and slide position (synchronized playback)
  var STARTED = Date.now();

  function now() { return window.performance && performance.now ? performance.now() : Date.now(); }
  function note(msg) { S.note = msg; D.noteError(msg); if (window.console) console.info("[player] " + msg); }
  function pop(fn, arg) { if (POP) { try { POP[fn](arg); } catch (e) { if (window.console) console.error(e); } } }
  function durationOf(item) { var d = Number(item.duration_ms); return d > 0 ? Math.max(1000, d) : DEFAULT_DURATION_MS; }

  // ---- Synchronized playback. The slide that should be on screen is a function of the shared
  // server clock, the playlist's durations and the anchor, so the group needs no coordination.
  function serverNow() { return SY ? SY.clock.now() : Date.now(); }
  // The schedule at server time `at` (default: now): {index, elapsedMs, remainingMs} or null.
  function syncAt(at) {
    if (!S.sync || !SY) return null;
    return SY.position(S.items.map(durationOf), S.sync.anchor, at === undefined ? serverNow() : at);
  }
  // Index of the slide that plays right after the one on screen ends (or now, if none is shown).
  function syncNextIndex() {
    var wait = S.cur ? Math.max(0, S.cur.deadline - now()) : 0;
    var p = syncAt(serverNow() + wait + 1);
    return p ? p.index : 0;
  }
  function clockSample(res) {
    if (SY && res && res.kind === "ok" && res.data && typeof res.data.server_time_ms === "number" && res.sentAt) {
      SY.clock.sample(res.data.server_time_ms, res.sentAt, res.recvAt);
    }
  }

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
    CT.onState(s);
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
    command: function (d) {
      if (d.action === "reload") requestReload();
      else if (d.action === "diagnostics") { diagWanted = true; hb.now(); }
      else if (d.action === "unlock" || d.action === "lock") shellCommand(d);
      else D.command(d);  // identify, screenshot
    },
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
    beats++;
    if (beats === 1 || beats % DIAG_EVERY === 0 || diagWanted) {
      diagWanted = false;
      var diag = D.diag();
      if (diag) b.diag = diag;
    }
    return b;
  }, function (res) {
    if (res.kind === "unauthorized") goPairing();
    else if (res.kind === "setup") goSetup();
    else if (res.kind === "ok" && res.data.manifest_version && res.data.manifest_version !== S.version) fetchManifest();
    if (res.kind === "ok" && PH) PH.setServerTime(res.data.server_time_ms);
    clockSample(res);
    // network errors: keep playing
  });
  function startLive() {
    if (!S.live) { S.live = true; beats = 0; events.start(); hb.start(S.manifest && S.manifest.heartbeat_s); }
  }
  function stopLive() { S.live = false; events.stop(); hb.stop(); }

  // ---- Manifest.
  function fetchManifest() {
    if (S.fetching) { S.refetch = true; return; }
    S.fetching = true;
    S.refetch = false;
    clearTimeout(S.retryTimer);
    var m = S.mode;
    net.api("/manifest", { etag: shell && !shellFed ? null : S.version }).then(function (res) {
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
      // Only a manifest fresh from the server sets the plugin clock (a cached one is old).
      if (PH) PH.setServerTime(res.data.server_time_ms);
      clockSample(res);
      store.local.set(K.manifest, withoutUnlock(res.data));
      applyManifest(res.data);
      if (shell) { shell.onManifest(JSON.stringify(res.data)); shellFed = true; }
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
  // The PIN hash is for the native apps; never keep it in localStorage.
  function withoutUnlock(m) {
    if (!m.settings || typeof m.settings !== "object") return m;
    var copy = Object.assign({}, m);
    copy.settings = Object.assign({}, m.settings);
    delete copy.settings.unlock;
    return copy;
  }
  function applyManifest(m) {
    try { EM.apply(m); } catch (e) { if (window.console) console.error(e); }
    try {
      D.applySettings(m.settings);
      var cfg = D.config();
      CT.configure(cfg);
      if (!cfg.allow_input) doResume();  // controls turned off: never leave a screen paused
    } catch (e) { if (window.console) console.error(e); }
    S.manifest = m;
    S.version = typeof m.version === "string" ? m.version : null;
    S.items = (Array.isArray(m.items) ? m.items : []).filter(function (it) {
      return it && TYPES[it.type] && typeof it.url === "string" && it.url;
    });
    if (m.heartbeat_s) hb.setSeconds(m.heartbeat_s);
    if (D.mediaSupport) D.mediaSupport(S.items);  // does this screen decode the videos smoothly?
    // Synchronized playback: when the timeline changes (turned on or off, another playlist or
    // other durations) the whole group restarts on the new one at the same moment.
    var before = S.sync ? S.sync.key : "";
    var ms = m.sync && typeof m.sync === "object" && SY && typeof m.sync.anchor_ms === "number" ? m.sync : null;
    S.sync = ms ? { anchor: ms.anchor_ms, key: ms.anchor_ms + ":" + S.items.map(function (it) { return it.id + "=" + durationOf(it); }).join(",") } : null;
    if (SY) SY.active = !!S.sync;
    var resync = (S.sync ? S.sync.key : "") !== before && S.state === "PLAYING" && S.items.length;
    dropNext();
    S.skipTo = null;  // indexes of the old list mean nothing in the new one
    if (!S.items.length) { showNoContent(false); return; }
    if (S.state !== "PLAYING" || resync) { startPlaying(); return; }
    var cur = S.cur;
    if (!cur) { S.pos = -1; return; }
    // The current slide keeps its deadline; the new list takes effect at the boundary,
    // continuing after the current item (or from the start if it is gone).
    S.pos = -1;
    for (var i = 0; i < S.items.length; i++) if (S.items[i].id === cur.item.id) S.pos = i;
    var fresh = S.items[S.pos];
    if (!fresh || fresh.url !== cur.item.url || fresh.fit !== cur.item.fit || fresh.muted !== cur.item.muted ||
        (fresh.volume === undefined ? 100 : fresh.volume) !== (cur.item.volume === undefined ? 100 : cur.item.volume)) {
      S.forceSwap = true;  // gone or changed: advance now
      cur.deadline = 0;
      unpause(true);
      tick();
    } else {
      cur.item = fresh;
    }
  }

  // ---- Playback loop.
  function startPlaying() { stopPlayback(); setState("PLAYING"); S.pos = -1; S.failures = 0; tick(); }
  function stopPlayback() {
    pop("end", true); S.gen++; clearSlideTimers(); dropNext(); R.clear(); S.cur = null; S.forceSwap = false;
    S.skipTo = null;
    clearControls();
  }
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
    if (S.cur && !S.cur.failed) {
      // Every other item failed to load (server or network down), but this one is on screen:
      // keep it playing, and try the others again after its next full run. Blanking the
      // screen to a message would look like a freeze and lose a working slide.
      S.failures = 0;
      S.cur.deadline = now() + RETRY_AFTER_FAIL_MS;
      return false;
    }
    showNoContent(true);
    return true;
  }

  // Absolute deadlines checked by a 250 ms ticker survive timer throttling.
  function tick() {
    if (S.state !== "PLAYING") return;
    if (S.paused) {  // paused: no preload and no boundary until it resumes
      if (EM.shownId !== null || now() >= S.paused.resumeAt) doResume(); else return;
    }
    var t = now(), cur = S.cur;
    if (cur && !S.nxt && t >= cur.deadline - PRELOAD_MS) prepareNext();
    if (!cur || t >= cur.deadline) boundary();
  }
  function boundary() {
    if (S.reloadPending) guardedReload();
    if (singleRepeat()) {
      var again = S.sync ? syncAt() : null;  // in sync: the cycle restarts on the shared clock
      S.cur.deadline = now() + (again ? again.remainingMs : durationOf(S.cur.item));
      pop("end", true); pop("begin", S.cur.item);  // each repeat counts as a play
      return;
    }
    if (!S.nxt) prepareNext();
    else if (S.nxt.state === "ready") {
      // In sync, a slide that was prepared for a moment that has passed (a slow load) is dropped
      // for the one that should be on screen now.
      var due = S.sync ? syncAt() : null;
      if (due && due.index !== S.nxt.index && due.index !== (S.nxt.index + S.items.length - 1) % S.items.length) {
        dropNext(); prepareNext();
      } else swapTo(S.nxt);
    }
    // loading: the old slide stays until the new one is ready; wait: failure dwell
  }
  function prepareNext() {
    if (S.nxt || !S.items.length || singleRepeat()) return;
    var index = S.sync ? syncNextIndex() : (S.pos + 1) % S.items.length;
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
      S.skipTo = null;
      n.state = "wait";
      if (countFailure()) return;
      slideTimer(function () { if (S.nxt === n) { S.nxt = null; tick(); } }, FAIL_DWELL_MS, false);
    });
  }
  function swapTo(n) {
    S.nxt = null; S.gen++; clearSlideTimers();
    S.forceSwap = false; S.failures = 0; S.pos = n.index; S.skipTo = null;
    clearControls();
    var item = n.item, g = S.gen, t = now();
    // Going to the next or previous slide by hand slides left or right, whatever the playlist's
    // transition is; automatic changes keep the playlist's own.
    var trans = S.manualDir ? (S.manualDir > 0 ? "slide-left" : "slide-right") : item.transition;
    S.manualDir = 0;
    var elapsed = 0;  // in sync: how far into this slide the shared clock says we already are
    var sp = S.sync ? syncAt() : null;
    if (sp) elapsed = sp.index === n.index ? sp.elapsedMs : -sp.remainingMs;  // a hair early: start at the boundary
    if (sp && elapsed > 500 && R.isVideo(n.slide)) syncSeek(n.slide, item, elapsed);
    R.swap(n.slide, trans);
    screenEl.hidden = true;
    pop("end", true); pop("begin", item);
    S.cur = { slide: n.slide, item: item, shownAt: t, deadline: t + durationOf(item) - elapsed, failed: false };
    EM.sync();  // a fullscreen emergency keeps the new video silent
    var guard = function (fn) { return function (x) { if (g === S.gen) fn(x); }; };
    if (R.isPlugin && R.isPlugin(n.slide)) {
      R.runPlugin(n.slide, {
        loop: function () { return loops(item); },
        // A plugin that ends itself while the viewer has it paused waits for the resume.
        done: guard(function () { if (S.paused || S.sync) return; S.cur.deadline = Math.min(S.cur.deadline, now()); tick(); }),
        fail: guard(slideFailed),
        note: note,
        every: every,
      });
      return;
    }
    if (!R.isVideo(n.slide)) return;
    if (S.sync) every(function () { syncDrift(n.slide, item); }, SYNC_CHECK_MS);
    R.runVideo(n.slide, {
      loop: function () { return loops(item); },
      done: guard(function () { if (S.sync) return; S.cur.deadline = Math.min(S.cur.deadline, now()); tick(); }),
      fail: guard(slideFailed),
      note: note,
      every: every,
    });
  }
  // The on-screen slide failed (video error or stall): its last frame stays until the next is ready.
  function slideFailed(reason) {
    var cur = S.cur;
    if (!cur || cur.failed) return;
    note("item " + cur.item.id + " failed: " + reason);  // note() also feeds the error ring
    cur.failed = true;
    clearControls();
    pop("end", false);  // failed slides are not counted as plays
    if (countFailure()) return;
    cur.deadline = Math.min(cur.deadline, Math.max(now(), cur.shownAt + FAIL_DWELL_MS));
    tick();
  }
  // Where a synced video should be `elapsedMs` into its slide: it loops only where it would loop
  // anyway, otherwise it rests on its last frame.
  function syncWant(slide, item, elapsedMs) {
    var v = R.videoOf(slide), len = v ? Number(v.duration) : 0;
    var s = Math.max(0, elapsedMs) / 1000;
    if (!(len > 0) || !isFinite(len)) return s;
    return loops(item) ? s % len : Math.min(s, len);
  }
  function syncSeek(slide, item, elapsedMs) { R.setVideoTime(slide, syncWant(slide, item, elapsedMs)); }
  // Every few seconds a synced video is pulled back to the shared timeline if it has drifted.
  function syncDrift(slide, item) {
    var cur = S.cur;
    if (!S.sync || !cur || cur.slide !== slide || cur.failed || S.paused || (R.isSeeking && R.isSeeking(slide))) return;
    var p = syncAt(), v = R.videoOf(slide);
    if (!p || !v || p.index !== S.pos) return;
    var want = syncWant(slide, item, p.elapsedMs);
    if (Math.abs((Number(v.currentTime) || 0) - want) > SYNC_DRIFT_S) R.setVideoTime(slide, want);
  }

  // A video plays once (its deadline follows its length) unless it fills a longer duration or is alone.
  function loops(item) {
    return S.items.length === 1 ||
      (item.media_duration_ms != null && durationOf(item) > item.media_duration_ms + 1000);
  }

  // ---- Controls: pause, skip, seek and scrub (controls.js turns input into these calls).
  function canControl() { return S.state === "PLAYING" && !!S.cur && EM.shownId === null && !S.sync; }
  function curVideo() { var c = S.cur; return !!(c && !c.failed && R.isVideo(c.slide)); }
  // Plays the on-screen video again; a refused unmuted play() falls back to muted, as runVideo does.
  function playCur() {
    var cur = S.cur, g = S.gen;
    if (!cur) return;
    R.playVideo(cur.slide, function (err) {
      var v = R.videoOf(cur.slide);
      if (g !== S.gen || !v || !err || err.name !== "NotAllowedError" || v.muted) return;
      v.muted = true;
      note("autoplay blocked, playing muted");
      R.playVideo(cur.slide);
    });
  }
  function doPause() {
    var cur = S.cur;
    if (S.paused || !canControl() || cur.failed) return null;
    var t = now();
    S.paused = { resumeAt: t + D.config().resume_after_s * 1000, remaining: Math.max(0, cur.deadline - t), video: curVideo() };
    if (S.paused.video) R.pauseVideo(cur.slide);
    else if (R.pause) R.pause(cur.slide);
    pop("pause");
    CT.showPaused(S.paused.resumeAt);
    return "paused";
  }
  // Resume: the slide gets the time it had left when it was paused.
  function doResume() {
    var p = S.paused, cur = S.cur;
    if (!p) return null;
    S.paused = null;
    CT.hidePaused();
    if (!cur) return null;
    cur.deadline = now() + p.remaining;
    if (p.video && !S.scrub) playCur();
    else if (!p.video) R.resume(cur.slide);
    pop("resume");
    return "playing";
  }
  // Drops a pause without restoring the deadline (the slide is being replaced or restarted).
  function unpause(playAgain) {
    var p = S.paused;
    if (!p) return;
    S.paused = null;
    CT.hidePaused();
    pop("resume");
    if (playAgain && p.video && S.cur) playCur();
    else if (!p.video && S.cur) R.resume(S.cur.slide);  // a plugin restarted in place must not stay frozen
  }
  function clearControls() {
    if (S.scrub) clearTimeout(S.scrub.timer);
    S.paused = null; S.scrub = null;
    CT.hidePaused();
  }
  // skip(+1 | -1): next or previous item. Rapid skips count from the one still loading.
  function skip(dir) {
    var cur = S.cur, n = S.items.length;
    if (!canControl() || !n) return false;
    unpause(false);
    if (n === 1 && !S.forceSwap && !cur.failed && cur.item.id === S.items[0].id) {
      // Restart in place; it counts as a new play.
      if (curVideo()) { R.setVideoTime(cur.slide, 0); playCur(); }
      cur.deadline = now() + durationOf(cur.item);
      pop("end", true); pop("begin", cur.item);
      return true;
    }
    var base = S.skipTo !== null ? S.skipTo : S.pos;
    var target = ((base + dir) % n + n) % n;
    S.manualDir = dir;
    var nx = S.nxt;
    if (nx && nx.index === target && nx.state === "ready") { swapTo(nx); return true; }
    if (!(nx && nx.index === target && nx.state === "loading")) {
      dropNext();
      S.pos = (target - 1 + n) % n;  // prepareNext() loads the target; the old slide stays until it is ready
    }
    S.skipTo = target;
    S.forceSwap = true;
    cur.deadline = 0;
    tick();
    return true;
  }
  // A seek moves the end of a video that plays once; a looping video keeps its deadline.
  function shiftDeadline(cur, appliedS) {
    if (loops(cur.item) || !appliedS) return;
    if (S.paused) S.paused.remaining = Math.max(0, S.paused.remaining - appliedS * 1000);
    else cur.deadline -= appliedS * 1000;
  }
  // The step of a fast forward or rewind: 10 s, less in a short video (a third of it, at least 2 s).
  function seekStep(slide) {
    var v = R.videoOf(slide), d = v ? Number(v.duration) : 0;
    return d > 0 && isFinite(d) ? Math.max(2, Math.min(SEEK_S, d / 3)) : SEEK_S;
  }
  // seek(+1 | -1): fast forward or rewind a video (forward past the end goes to the next item);
  // on anything else the arrows change the slide.
  function seek(sign) {
    if (!canControl()) return null;
    if (!curVideo()) return skip(sign) ? (sign > 0 ? "next" : "prev") : null;
    var cur = S.cur, r = R.seekVideo(cur.slide, sign * seekStep(cur.slide));
    if (r.atEnd && sign > 0) return skip(1) ? "next" : null;
    shiftDeadline(cur, r.applied);
    return r.applied;
  }
  function scrubBegin() {
    if (S.scrub || !canControl() || !curVideo()) return null;
    var cur = S.cur, v = R.videoOf(cur.slide), d = Number(v.duration);
    if (!(d > 0) || !isFinite(d)) return null;
    var t0 = Number(v.currentTime) || 0;
    S.scrub = { slide: cur.slide, t0: t0, target: t0, duration: d, wasPaused: !!S.paused, timer: null };
    R.pauseVideo(cur.slide);
    return { t: t0, duration: d };
  }
  // scrubTo(frac): frac of the screen width dragged; the full width spans 2 minutes (or the video).
  function scrubTo(frac) {
    var sc = S.scrub;
    if (!sc || !S.cur || S.cur.slide !== sc.slide) return null;
    var span = Math.min(sc.duration, SCRUB_SPAN_S);
    sc.target = Math.max(0, Math.min(Math.max(0, sc.duration - 0.25), sc.t0 + (Number(frac) || 0) * span));
    if (sc.timer === null) scrubStep(sc);
    return { t: sc.target, duration: sc.duration };
  }
  // At most one seek per 100 ms while dragging, and none while the last one is unfinished:
  // a new seek cancels the old one, and on a big video that freezes the picture.
  function scrubStep(sc) {
    sc.timer = setTimeout(function () {
      sc.timer = null;
      if (S.scrub !== sc) return;
      if (R.isSeeking && R.isSeeking(sc.slide)) { scrubStep(sc); return; }
      R.setVideoTime(sc.slide, sc.target, true);
    }, SCRUB_STEP_MS);
  }
  function scrubEnd() {
    var sc = S.scrub;
    S.scrub = null;
    if (!sc) return null;
    clearTimeout(sc.timer);
    if (!S.cur || S.cur.slide !== sc.slide) return null;
    var to = R.setVideoTime(sc.slide, sc.target);
    shiftDeadline(S.cur, to - sc.t0);
    if (!sc.wasPaused && !S.paused) playCur();
    return to - sc.t0;
  }
  A.playback = {
    enabled: canControl,
    kind: function () { var c = S.cur; return c && !c.failed && TYPES[c.item.type] ? c.item.type : null; },
    toggle: function () { return S.paused ? doResume() : doPause(); },
    play: function () { return S.paused ? doResume() : null; },
    pause: function () { return S.paused ? null : doPause(); },
    next: function () { return skip(1); },
    prev: function () { return skip(-1); },
    forward: function () { return seek(1); },
    back: function () { return seek(-1); },
    scrubBegin: scrubBegin,
    scrubTo: scrubTo,
    scrubEnd: scrubEnd,
    paused: function () { return !!S.paused; },
  };
  // Remote unlock / lock: for the kiosk shell only; the page shows a short hint.
  function shellCommand(d) {
    var m = Math.floor(Number(d.minutes));
    m = m >= 1 ? Math.min(240, m) : 1;
    var hasLock = !!(shell && shell.info.caps.indexOf("kiosk-lock") >= 0);
    if (shell) {
      var c = { action: d.action, request_id: d.request_id };
      if (d.action === "unlock") c.minutes = d.minutes;
      shell.onCommand(c);
    }
    if (hasLock) CT.hint(d.action === "unlock" ? "Unlocked for " + m + " min" : "Locked");
    else if (DEBUG) CT.hint(d.action === "unlock" ? "No app to unlock" : "No app to lock");
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
    D.checkOffline(hb, events, S.state);
    // While the live connection is down, try the server every PROBE_MS (not only every
    // heartbeat interval) so an outage is noticed, and its end too, within seconds.
    if (events.status !== "live" && Date.now() - (hb.lastAt || 0) > PROBE_MS) hb.now();
    // Plugins hear the same offline state as the banner (without the banner setting).
    if (PH) PH.setOffline(D.isOffline(hb, events));
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
    if (S.cur && !S.paused && !S.scrub) R.resume(S.cur.slide);
    tick();
  });

  EM.current = function () { return S.cur; };
  D.init({ manifest: function () { return S.manifest; } });
  R.init(document.getElementById("stage"));
  CT.init({ playback: A.playback, rotation: D.rotation, shell: shell });
  setInterval(tick, TICK_MS);
  setInterval(housekeeping, 1000);
  boot();
})();
