// AiSignX YouTube plugin (render mode "native"): plays the manifest item's youtube-nocookie
// embed URL in a cross-origin iframe and follows it over the IFrame API postMessage bridge.
// Classic script, ES2018 at most. No innerHTML, no third-party scripts, no network of its own.
//
// The slide is prepared with autoplay=0 (the player prepares the next slide ~3 s early, and a
// hidden video must not play), then run() sends playVideo. The bridge is undocumented: when
// the frame never answers, run() reloads the frame with autoplay=1 and the slide simply runs
// its full duration.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var NATIVE = A.pluginNative = A.pluginNative || {};

  var ORIGIN = "https://www.youtube-nocookie.com";
  var LOAD_MS = 6000;        // prepare() settles by then even if the frame never loads
  var LISTEN_MS = 400;       // "listening" is repeated until the frame answers...
  var LISTEN_TRIES = 40;     // ...for at most this many times
  var START_MS = 3000;       // not playing this long after run(): try the fallbacks
  var SYNC_DRIFT_S = 0.6;     // synchronized playback: seek when the video is this far off the shared timeline
  var SYNC_LEAD_S = 0.3;      // a seek takes a moment to start playing: aim a little ahead
  var SYNC_GAP_MS = 4000;     // at most one correction this often (every seek makes YouTube rebuffer)
  var END_GRACE_MS = 2500;   // "ended" with nothing new playing this long after: the list is done
  var MAX_MESSAGE = 65536;   // larger bridge messages are ignored unread
  var VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
  var ERRORS = {
    2: "invalid video id", 5: "the HTML5 player failed", 100: "video not found or private",
    101: "embedding is turned off for this video", 150: "embedding is turned off for this video",
    153: "the embed was refused (missing referrer)",
  };
  var PLAYING = 1, ENDED = 0, BUFFERING = 3, UNSTARTED = -1;
  var EVENTS = ["onReady", "onStateChange", "onError"];
  var seq = 0;

  // ---- Online state. Only the device's own network matters here: the embed comes straight
  // from YouTube, so the AiSignX server being down or restarting must not stop it. (The
  // player's offline flag measures the link to the server, not the internet.) Anything else
  // that stops the embed is caught by watchStart() below.
  function trackOffline() { /* kept for callers; nothing to track any more */ }
  function isOffline() {
    return typeof navigator !== "undefined" && navigator.onLine === false;
  }

  // ---- Pure helpers (exported as .util for tests).
  function shuffle(list, rand) {
    var a = list.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(rand() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  function pageOrigin() {
    var o = window.location && window.location.origin;
    return typeof o === "string" && /^https?:\/\/[^/]+$/.test(o) ? o : "";
  }

  // buildSrc(item, origin, rand) -> URL string, or null when the item's url is not a
  // youtube-nocookie /embed/ URL. Strips the #c= fragment, applies the client-side shuffle,
  // and adds enablejsapi, origin and autoplay=0.
  function buildSrc(item, origin, rand) {
    var raw = String((item && item.url) || "").split("#")[0];
    var u;
    try { u = new URL(raw); } catch (e) { return null; }
    if (u.origin !== ORIGIN || u.pathname.indexOf("/embed/") !== 0 || u.username || u.password) {
      return null;
    }
    var embed = (item.plugin && item.plugin.embed) || {};
    var ids = Array.isArray(embed.video_ids) ? embed.video_ids.filter(function (v) {
      return typeof v === "string" && VIDEO_ID.test(v);
    }) : [];
    if (embed.shuffle && !embed.live && !embed.list && ids.length > 1) {
      var order = shuffle(ids, rand || Math.random);
      u.pathname = "/embed/" + order[0];
      u.searchParams.set("playlist", order.slice(1).join(","));
      if (order[0] !== ids[0]) {  // start/end belong to the first listed video only
        u.searchParams.delete("start");
        u.searchParams.delete("end");
      }
    }
    u.searchParams.set("autoplay", "0");
    u.searchParams.set("enablejsapi", "1");
    if (origin) u.searchParams.set("origin", origin);
    else u.searchParams.delete("origin");
    u.hash = "";
    return u.href;
  }

  // readMessage(e, frameWindow) -> the parsed bridge message, or null when it is not one from
  // this frame: the source must be the frame's window and the origin youtube-nocookie.
  function readMessage(e, frameWindow) {
    if (!e || !frameWindow || e.source !== frameWindow || e.origin !== ORIGIN) return null;
    var d = e.data;
    if (typeof d === "string") {
      if (d.length > MAX_MESSAGE) return null;
      try { d = JSON.parse(d); } catch (x) { return null; }
    }
    if (!d || typeof d !== "object" || typeof d.event !== "string") return null;
    return d;
  }

  // facts(msg) -> {state, error, index, count, ready}: what a bridge message tells us.
  function facts(msg) {
    var out = {};
    var info = msg.info;
    if (msg.event === "onReady") out.ready = true;
    else if (msg.event === "onStateChange" && typeof info === "number") out.state = info;
    else if (msg.event === "onError") out.error = Number(info);
    else if ((msg.event === "infoDelivery" || msg.event === "initialDelivery") && info && typeof info === "object") {
      if (typeof info.playerState === "number") out.state = info.playerState;
      if (typeof info.playlistIndex === "number") out.index = info.playlistIndex;
      if (Array.isArray(info.playlist)) out.count = info.playlist.length;
      if (typeof info.currentTime === "number" && isFinite(info.currentTime)) out.time = info.currentTime;
      if (typeof info.duration === "number" && info.duration > 0 && isFinite(info.duration)) out.length = info.duration;
    }
    return out;
  }

  // ---- Bridge.
  function send(st, func, args) {
    var w = st.frame && st.frame.contentWindow;
    if (!w || st.dead) return;
    var msg = JSON.stringify({ event: "command", func: func, args: args || [], id: st.id, channel: "widget" });
    try { w.postMessage(msg, ORIGIN); } catch (e) { /* ignore */ }
  }
  function listen(st) {
    var tries = 0;
    var once = function () {
      var w = st.frame && st.frame.contentWindow;
      if (st.dead || st.heard || !w) return;
      try { w.postMessage(JSON.stringify({ event: "listening", id: st.id, channel: "widget" }), ORIGIN); } catch (e) { /* ignore */ }
      subscribe(st);
      if (++tries < LISTEN_TRIES) st.timers.push(setTimeout(once, LISTEN_MS));
    };
    once();
  }
  // What the IFrame API's own widget sends: without these some players report no state or errors.
  function subscribe(st) {
    EVENTS.forEach(function (name) { send(st, "addEventListener", [name]); });
  }
  function mutedNow(st) { return !!(st.itemMuted || st.forced || st.autoMuted); }
  function applySound(st) {
    send(st, mutedNow(st) ? "mute" : "unMute");
    send(st, "setVolume", [Math.round(Math.max(0, Math.min(1, st.volume)) * 100)]);
  }
  // cc_load_policy=0 only means "do not force captions on": YouTube still shows them when the
  // viewer's own setting (device or account) has them on. With captions off here, unload the
  // captions module, for every video the embed starts.
  function applyCaptions(st) {
    if (st.embed.captions) return;
    send(st, "unloadModule", ["captions"]);
    send(st, "unloadModule", ["cc"]);
  }
  function play(st) {
    applySound(st);
    applyCaptions(st);
    if (st.embed.list && st.embed.shuffle && !st.shuffled) {
      st.shuffled = true;
      send(st, "setShuffle", [true]);
      send(st, "playVideoAt", [0]);
    } else {
      send(st, "playVideo");
    }
  }
  // ---- Looping a clip. With loop=1 YouTube ignores the end time (and restarts from 0, not from the
  // start time), so a video set to play "from 0:30 to 1:30" would play to its real end. When the
  // slide loops a single video (synced screens, or "end the slide when the video ends" off) the
  // player keeps it inside its clip itself: at the end time it goes back to the start time.
  var LOOP_GAP_MS = 1000;
  function loopGuard(st, f) {
    var e = st.embed || {};
    if (e.play_to_end !== false || e.list || e.live || !Array.isArray(e.video_ids) || e.video_ids.length !== 1) return;
    if (!(e.end > 0) && !(e.start > 0)) return;
    if (st.paused || st.dead || st.state !== PLAYING || typeof f.time !== "number") return;
    var start = e.start > 0 ? e.start : 0;
    var past = e.end > 0 && f.time >= e.end - 0.25;      // at or after the end time
    var before = start > 0 && f.time < start - 0.75;     // YouTube restarted from 0
    if (!past && !before) return;
    var t = Date.now();
    if (st.loopAt !== undefined && t - st.loopAt < LOOP_GAP_MS) return;
    st.loopAt = t;
    send(st, "seekTo", [start, true]);
  }

  // ---- Synchronized playback. The slide knows where the shared timeline is (ctx.syncElapsed); a
  // single video can be held there by seeking it when it drifts. The embed only reports its
  // position about once a second and a seek makes it rebuffer, so this is close (a few tenths
  // of a second), not frame-exact. A list or playlist has no position we can compute: those
  // simply start together.
  function syncCheck(st, f) {
    var ctx = st.ctx;
    if (!ctx || typeof ctx.syncElapsed !== "function" || st.paused || st.dead || st.state !== PLAYING) return;
    var e = st.embed || {};
    if (e.list || e.live || !Array.isArray(e.video_ids) || e.video_ids.length !== 1) return;
    if (typeof f.time !== "number" || typeof f.length !== "number") return;
    var elapsed = ctx.syncElapsed();
    if (elapsed === null || elapsed === undefined) return;
    var start = e.start > 0 ? e.start : 0;
    var end = e.end > 0 && e.end <= f.length ? e.end : f.length;
    var span = end - start;
    if (!(span > 1)) return;
    var want = start + (elapsed / 1000) % span;
    var drift = f.time - want;
    if (drift > span / 2) drift -= span;        // across the loop point the shorter way round
    else if (drift < -span / 2) drift += span;
    if (Math.abs(drift) < SYNC_DRIFT_S) return;
    var t = Date.now();
    if (st.syncAt !== undefined && t - st.syncAt < SYNC_GAP_MS) return;
    st.syncAt = t;
    send(st, "seekTo", [Math.min(want + SYNC_LEAD_S, end), true]);
  }

  // finish(st): the list played to its end. The bridge reports "ended" twice (infoDelivery and
  // onStateChange); the slide ends once.
  function finish(st) {
    if (st.finished || !st.ctx) return;
    st.finished = true;
    st.ctx.done();
  }
  function fail(st, code) {
    var why = "youtube error " + code + (ERRORS[code] ? ": " + ERRORS[code] : "");
    if (!st.settled) { st.settled = true; st.reject(new Error(why)); return; }
    if (st.ctx) st.ctx.fail(why);
    else st.error = why;
  }

  function onMessage(st, e) {
    var msg = readMessage(e, st.frame.contentWindow);
    if (!msg || st.dead) return;
    var first = !st.heard;
    st.heard = true;
    if (first) subscribe(st);
    var f = facts(msg);
    if (typeof f.index === "number") st.index = f.index;
    if (typeof f.count === "number") st.count = f.count;
    if (f.ready && !st.settled) { st.settled = true; st.resolve(); }
    // Commands sent before the player was ready may be lost: send play again once it answers.
    if ((first || f.ready) && st.ctx && !st.started && !st.paused) play(st);
    if (typeof f.error === "number" && !isNaN(f.error) && ERRORS[f.error]) { fail(st, f.error); return; }
    if (typeof f.state === "number") st.state = f.state;
    if (f.time !== undefined) { loopGuard(st, f); syncCheck(st, f); }
    if (typeof f.state !== "number") return;
    if (f.state === PLAYING || f.state === BUFFERING || f.state === UNSTARTED) {
      if (f.state !== UNSTARTED) { st.started = true; applyCaptions(st); }
      if (st.endTimer) { clearTimeout(st.endTimer); st.endTimer = null; }  // the next video started
    }
    if (f.state !== ENDED || !st.ctx || st.paused) return;
    if (st.embed.live) { st.ctx.degraded(); return; }  // the stream ended: move on soon
    if (!st.embed.play_to_end) return;                  // loop=1: YouTube starts again
    var count = st.count || st.expected;
    if (count > 1 && st.index >= 0 && st.index < count - 1) return;  // more to come
    if (count === 1 || (count > 1 && st.index === count - 1)) { finish(st); return; }
    // Position in the list unknown (a playlist the bridge never described): a video ended. Done
    // unless the next one starts within the grace period.
    if (!st.endTimer) {
      st.endTimer = setTimeout(function () {
        st.endTimer = null;
        if (!st.dead && !st.paused && st.state === ENDED) finish(st);
      }, END_GRACE_MS);
      st.timers.push(st.endTimer);
    }
  }

  // Checked START_MS after run(). The bridge never answered: reload with autoplay=1 (the slide
  // then runs its full duration). It answered but the video is not playing: ask once more,
  // then mute (a browser without the kiosk flags refuses unmuted autoplay).
  function watchStart(st) {
    if (st.dead || st.started || st.paused) return;
    if (!st.heard) {
      if (st.reloaded) {
        // Still no answer after the reload (no internet, YouTube blocked): this slide shows
        // nothing useful, so let the playlist move on instead of holding it for the full
        // duration.
        if (!st.degradedSent && st.ctx) {
          st.degradedSent = true;
          st.ctx.degraded();
          st.ctx.note("youtube: no answer from the player, moving on");
        }
        return;
      }
      st.reloaded = true;
      st.timers.push(setTimeout(function () { watchStart(st); }, START_MS));
      try {
        var u = new URL(st.src);
        u.searchParams.set("autoplay", "1");
        st.frame.src = u.href;
      } catch (e) { /* ignore */ }
      if (st.ctx) st.ctx.note("youtube: no answer from the player, reloaded with autoplay");
      return;
    }
    if (!st.retried) {
      st.retried = true;
      play(st);
      st.timers.push(setTimeout(function () { watchStart(st); }, START_MS));
      return;
    }
    if (!mutedNow(st)) {
      st.autoMuted = true;
      applySound(st);
      send(st, "playVideo");
      if (st.ctx) st.ctx.note("youtube: autoplay blocked, playing muted");
    }
  }

  function prepare(slide, api) {
    trackOffline();
    var item = slide.item;
    var p = item.plugin || {};
    if (isOffline()) {
      slide.yt = { offline: true, dead: false, timers: [] };
      return api.prepCard(slide, "Video unavailable", "This screen is offline");
    }
    var src = buildSrc(item, pageOrigin(), Math.random);
    if (!src) return Promise.reject(new Error("youtube: not a youtube-nocookie embed URL"));

    // Only a verified cross-origin youtube-nocookie URL gets allow-same-origin.
    var f = document.createElement("iframe");
    f.className = "media pl-youtube-frame";
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-presentation");
    f.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture");
    // Overrides the page's no-referrer: an embed without a Referer fails with error 153.
    f.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    f.setAttribute("scrolling", "no");
    f.setAttribute("title", "YouTube video");
    f.tabIndex = -1;
    f.style.background = "#000";

    var st = {
      id: ++seq, frame: f, src: src, embed: p.embed || {}, itemMuted: !!item.muted,
      volume: typeof api.volume === "function" ? Number(api.volume()) : 1,
      forced: false, autoMuted: false, timers: [], dead: false, offline: false,
      heard: false, settled: false, started: false, paused: false, ctx: null, retried: false,
      reloaded: false, shuffled: false, endTimer: null, finished: false,
      state: null, index: -1, count: 0, error: null,
      expected: p.embed && !p.embed.list && Array.isArray(p.embed.video_ids) ? p.embed.video_ids.length : 0,
    };
    if (!(st.volume >= 0 && st.volume <= 1)) st.volume = 1;
    st.listener = function (e) { onMessage(st, e); };
    slide.yt = st;
    window.addEventListener("message", st.listener);
    api.attach(slide, f);
    return new Promise(function (resolve, reject) {
      st.resolve = resolve;
      st.reject = reject;
      var settle = function () { if (!st.settled && !st.dead) { st.settled = true; resolve(); } };
      f.addEventListener("load", function () {
        listen(st);
        if (!st.settled) st.timers.push(setTimeout(settle, 1500));  // onReady normally comes first
      });
      st.timers.push(setTimeout(settle, LOAD_MS));
      f.src = src;
    });
  }

  function run(slide, ctx) {
    var st = slide.yt;
    if (!st) return;
    if (st.offline) { ctx.degraded(); return; }
    st.ctx = ctx;
    if (st.error) { ctx.fail(st.error); return; }
    play(st);
    st.timers.push(setTimeout(function () { watchStart(st); }, START_MS));
  }

  function teardown(slide) {
    var st = slide.yt;
    if (!st || st.dead) return;
    st.dead = true;
    st.timers.forEach(clearTimeout);
    st.timers = [];
    if (st.listener) window.removeEventListener("message", st.listener);
    if (st.frame) { try { st.frame.src = "about:blank"; } catch (e) { /* ignore */ } }
  }

  function pause(slide) {
    var st = slide.yt;
    if (!st || st.offline || st.dead) return;
    st.paused = true;
    send(st, "pauseVideo");
  }
  function resume(slide) {
    var st = slide.yt;
    if (!st || st.offline || st.dead) return;
    st.paused = false;
    send(st, "playVideo");
  }
  function setVolume(slide, volume) {
    var st = slide.yt;
    var v = Number(volume);
    if (!st || st.offline || !(v >= 0 && v <= 1)) return;
    st.volume = v;
    send(st, "setVolume", [Math.round(v * 100)]);
  }
  // setMuted(slide, true) while a fullscreen emergency is shown; false restores the item's own.
  function setMuted(slide, forced) {
    var st = slide.yt;
    if (!st || st.offline) return;
    st.forced = !!forced;
    send(st, mutedNow(st) ? "mute" : "unMute");
  }

  NATIVE.youtube = {
    prepare: prepare, run: run, teardown: teardown, pause: pause, resume: resume,
    setVolume: setVolume, setMuted: setMuted,
    util: { ORIGIN: ORIGIN, buildSrc: buildSrc, readMessage: readMessage, facts: facts, shuffle: shuffle },
  };
  trackOffline();
})();
