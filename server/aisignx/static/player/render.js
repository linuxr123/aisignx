// AiSignX browser player: slide rendering (prepare hidden, swap with a transition, tear down).
// Classic script, ES2018 at most. Never uses innerHTML or inline style attributes.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};

  // Transparent 1x1 GIF; the slide behind it is black, so posters never flash white.
  var BLANK_POSTER = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
  var TRANSITIONS = ["fade", "crossfade", "wipe", "slide-left", "slide-right", "slide-up", "slide-down", "zoom"];
  var FITS = { fit: 1, fill: 1, stretch: 1 };
  var IMAGE_TIMEOUT_MS = 10000;
  var VIDEO_TIMEOUT_MS = 10000;
  var WEB_SHOW_ANYWAY_MS = 8000;
  var TEARDOWN_SAFETY_MS = 1500;
  var STALL_MS = 10000;
  var NUDGE_MS = 3000;   // a frozen video gets one play() nudge after this long
  var PLUGIN_READY_MS = 8000;
  var DEGRADED_MS = 10000;  // a degraded plugin (fallback or loading card) moves on after 10 s

  var stage = null;   // #stage element
  var active = null;  // slide on screen
  var exiting = [];   // slides animating out (normally zero or one)
  var volume = 1;     // 0..1, the display's volume setting; applies to every <video> we create

  function init(el) { stage = el; }

  function pickTransition(t) {
    if (t === "cut") return "cut";
    if (t === "random") return TRANSITIONS[Math.floor(Math.random() * TRANSITIONS.length)];
    return TRANSITIONS.indexOf(t) >= 0 ? t : "fade";
  }

  function fitClass(fit) { return "fit-" + (FITS[fit] ? fit : "fit"); }

  function withTimeout(promise, ms, what) {
    var timer;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () { reject(new Error(what + " timed out")); }, ms);
    });
    return Promise.race([promise, timeout]).then(function (v) {
      clearTimeout(timer); return v;
    }, function (e) {
      clearTimeout(timer); throw e;
    });
  }

  // Double requestAnimationFrame, with a timer fallback: rAF stops in hidden or
  // occluded windows, and a slide must never wait for a paint to become active.
  function raf2(fn) {
    var done = false;
    var go = function () { if (!done) { done = true; fn(); } };
    var raf = window.requestAnimationFrame || function (cb) { return setTimeout(cb, 16); };
    raf(function () { raf(go); });
    setTimeout(go, 100);
  }

  // Each prepared slide is inserted hidden (opacity 0, below the active one).
  function attach(slide, media) {
    // Content is never interactive: an inert frame takes no pointer, keyboard or focus input
    // (the CSS also turns off its pointer events; older browsers ignore "inert").
    if (media && media.tagName === "IFRAME") {
      media.inert = true;
      media.setAttribute("inert", "");
    }
    slide.media = media;
    slide.el.appendChild(media);
    stage.appendChild(slide.el);
  }

  function prepImage(slide) {
    var item = slide.item;
    var img = document.createElement("img");
    img.className = "media " + fitClass(item.fit);
    img.alt = "";
    attach(slide, img);
    var canDecode = typeof img.decode === "function";
    // decode() can wait for a paint that never comes in a hidden window, so a
    // loaded image counts as ready shortly after load even if decode() is pending.
    var ready = new Promise(function (resolve, reject) {
      img.onerror = function () { reject(new Error("image failed to load")); };
      img.onload = function () { setTimeout(resolve, canDecode ? 1500 : 0); };
    });
    img.src = item.url;
    if (canDecode) ready = Promise.race([ready, img.decode()]);
    return withTimeout(ready, IMAGE_TIMEOUT_MS, "image");
  }

  // The playlist item's volume (0-100, default 100) as a 0..1 share of the display volume.
  function itemGain(item) {
    var n = item && Number(item.volume);
    return n >= 0 && n <= 100 ? n / 100 : 1;
  }

  function prepVideo(slide) {
    var item = slide.item;
    var v = document.createElement("video");
    v.className = "media " + fitClass(item.fit);
    // muted must be a property (the attribute alone is not reliable) and set before src.
    v.muted = !!item.muted;
    v.defaultMuted = !!item.muted;
    v._gain = itemGain(item);  // the playlist item's share of the display volume
    v.volume = volume * v._gain;
    v.playsInline = true;
    v.setAttribute("playsinline", "");
    v.setAttribute("webkit-playsinline", "");
    v.disablePictureInPicture = true;
    v.setAttribute("disablepictureinpicture", "");
    v.setAttribute("disableremoteplayback", "");
    v.controls = false;
    v.poster = BLANK_POSTER;
    v.preload = "auto";
    attach(slide, v);
    var ready = new Promise(function (resolve, reject) {
      v.addEventListener("loadeddata", function () { resolve(); });
      v.addEventListener("error", function () {
        reject(new Error("video error " + (v.error ? v.error.code : "")));
      });
    });
    v.src = item.url;
    return withTimeout(ready, VIDEO_TIMEOUT_MS, "video");
  }

  function prepWeb(slide) {
    var url;
    try { url = new URL(slide.item.url, location.href); } catch (e) { url = null; }
    if (!url || (url.protocol !== "http:" && url.protocol !== "https:")) {
      return Promise.reject(new Error("bad web url"));
    }
    var f = document.createElement("iframe");
    f.className = "media web";
    // Same-origin pages must never get allow-same-origin: they could call the player API.
    var sandbox = url.origin === location.origin
      ? "allow-scripts allow-forms"
      : "allow-scripts allow-same-origin allow-forms";
    f.setAttribute("sandbox", sandbox);
    f.setAttribute("referrerpolicy", "no-referrer");
    f.setAttribute("allow", "autoplay");
    f.setAttribute("scrolling", "no");
    f.setAttribute("title", "Web content");
    f.tabIndex = -1;
    attach(slide, f);
    // Load errors are invisible for iframes, and framed pages that refuse framing
    // never say so; show whatever we have after 8 s.
    var ready = new Promise(function (resolve) {
      f.addEventListener("load", function () { resolve(); });
      setTimeout(resolve, WEB_SHOW_ANYWAY_MS);
    });
    f.src = url.href;
    return ready;
  }

  // ---- Plugins. Frame plugins: an opaque-origin iframe driven by plugins/host.js. Native
  // plugins: first-party code in A.pluginNative[key] = {prepare, run, teardown, pause, resume,
  // setVolume, setMuted} that draws into the slide itself.
  var natives = [];  // prepared native plugin slides (for volume and mute)
  function prepCard(slide, title, msg) {
    var card = document.createElement("div");
    card.className = "media plugin-card";
    var s = card.style;
    s.display = "flex"; s.alignItems = "center"; s.justifyContent = "center"; s.flexDirection = "column";
    s.background = "#111"; s.color = "#fff"; s.textAlign = "center";
    s.fontFamily = "system-ui, sans-serif";
    var t = card.appendChild(document.createElement("p"));
    t.textContent = String(title || "");
    t.style.fontSize = "6vmin"; t.style.margin = "0 0 1.5vmin"; t.style.fontWeight = "600";
    if (msg) {
      var m = card.appendChild(document.createElement("p"));
      m.textContent = String(msg);
      m.style.fontSize = "3.2vmin"; m.style.margin = "0"; m.style.opacity = "0.7";
    }
    attach(slide, card);
    return Promise.resolve();
  }
  function prepPlugin(slide) {
    var item = slide.item, p = item.plugin;
    if (!p || typeof p !== "object" || typeof p.key !== "string") throw new Error("bad plugin item");
    if (p.render === "native") {
      var N = A.pluginNative && A.pluginNative[p.key];
      if (!N || typeof N.prepare !== "function") throw new Error("unsupported plugin " + p.key);
      slide.native = N;
      natives.push(slide);
      var api = {
        attach: attach,
        fitClass: fitClass,
        volume: function () { return volume * itemGain(item); },
        prepCard: prepCard,
      };
      // A native prepare() that never settles must not hold the playlist.
      return withTimeout(Promise.resolve(N.prepare(slide, api)), PLUGIN_READY_MS, "plugin");
    }
    if (!A.pluginHost) throw new Error("unsupported plugin " + p.key);
    var f = document.createElement("iframe");
    f.className = "media plugin";
    f.setAttribute("sandbox", "allow-scripts");
    f.setAttribute("referrerpolicy", "no-referrer");
    f.setAttribute("allow", "");
    f.setAttribute("scrolling", "no");
    f.setAttribute("title", "Plugin " + p.key);
    f.tabIndex = -1;
    f.style.background = "#000";
    attach(slide, f);
    slide.plugin = A.pluginHost.create(f, item, { readyMs: PLUGIN_READY_MS });
    f.src = item.url;  // after create(): the host must know the frame before it says hello
    return slide.plugin.ready;
  }
  function isPlugin(slide) { return !!(slide && (slide.plugin || slide.native)); }
  function nativeCall(slide, fn, arg) {
    var N = slide.native;
    if (N && typeof N[fn] === "function") {
      try { N[fn](slide, arg); } catch (e) { if (window.console) console.error(e); }
    }
  }
  function emergencyMuted() {
    var E = A.emergency;
    return !!(E && E.shownId !== null && E.shownId !== undefined && E.layout === "fullscreen");
  }

  // runPlugin(slide, ctx): start an on-screen plugin. ctx as for runVideo; ctx.loop() is true
  // when the playlist has a single item.
  function runPlugin(slide, ctx) {
    if (!isPlugin(slide)) return;
    var alive = function () { return !slide.dead && active === slide; };
    var shownAt = Date.now(), degraded = false, cut = false;
    var onDegraded = function () { degraded = true; };
    // Degraded (fallback or loading card) in a busy playlist: done at min(duration, 10 s).
    ctx.every(function () {
      if (cut || !alive() || !degraded || ctx.loop() || Date.now() - shownAt < DEGRADED_MS) return;
      cut = true;
      ctx.done();
    }, 1000);
    if (slide.native) {
      var muted = false;  // setMuted(slide, true) forces silence; false restores the item's own setting
      ctx.every(function () {  // fullscreen emergency text silences native plugins too
        var want = emergencyMuted();
        if (alive() && want !== muted) { muted = want; nativeCall(slide, "setMuted", want); }
      }, 500);
      var nctx = {};
      Object.keys(ctx).forEach(function (k) { nctx[k] = ctx[k]; });
      nctx.degraded = onDegraded;
      nctx.done = function () { if (slide.item.plugin.ends_itself) ctx.done(); };
      if (typeof slide.native.run !== "function") return;
      try { slide.native.run(slide, nctx); } catch (e) { ctx.fail("plugin failed: " + (e && e.message)); }
      return;
    }
    var h = slide.plugin;
    if (ctx.syncStart && h.slideClock) {  // synchronized playback: tell the frame when its slide began
      var began = ctx.syncStart();
      if (typeof began === "number") h.slideClock(began);
    }
    h.on("complete", function () { if (alive() && slide.item.plugin.ends_itself) ctx.done(); });
    h.on("error", function (msg, fatal) {
      if (!alive()) return;
      if (fatal) ctx.fail("plugin error: " + msg); else ctx.note("plugin " + slide.item.plugin.key + ": " + msg);
    });
    h.on("degraded", onDegraded);
    if (h.degraded()) degraded = true;
    h.show();
  }
  // pause(slide) / resume(slide) for plugins; videos use pauseVideo / playVideo.
  function pause(slide) {
    if (!slide || slide.dead) return;
    if (slide.plugin) slide.plugin.pause();
    else if (slide.native && !slide.nativePaused) { slide.nativePaused = true; nativeCall(slide, "pause"); }
  }

  // prepare(item, gen) -> Promise<slide>. Rejects (and cleans up) on failure.
  // The returned promise carries `.slide`, so a caller can teardown() a slide that is
  // still loading (the promise then rejects with "dropped").
  function prepare(item, gen) {
    var el = document.createElement("div");
    el.className = "slide";
    var slide = { el: el, item: item, gen: gen, media: null, dead: false };
    var p;
    try {
      if (!stage) throw new Error("render.init() not called");
      if (item.type === "image") p = prepImage(slide);
      else if (item.type === "video") p = prepVideo(slide);
      else if (item.type === "web") p = prepWeb(slide);
      else if (item.type === "plugin") p = prepPlugin(slide);
      else throw new Error("unsupported item type " + item.type);
    } catch (e) {
      p = Promise.reject(e);
    }
    var out = p.then(function () {
      if (slide.dead) throw new Error("dropped");
      return slide;
    }, function (err) {
      teardown(slide);
      throw err;
    });
    out.slide = slide;
    return out;
  }

  function isVideo(slide) { return !!(slide && slide.media && slide.media.tagName === "VIDEO"); }

  // swap(slide, transition): animate `slide` in and the current one out.
  function swap(slide, transition) {
    var t = pickTransition(transition);
    var old = active;
    var el = slide.el;
    active = slide;
    // At most 3 slides in the DOM: anything still exiting from an earlier swap goes now.
    exiting.slice().forEach(teardown);
    if (old) exiting.push(old);
    // Apply the transition's start state without animating into it.
    el.classList.add("prep");
    el.classList.add("t-" + t);
    void el.offsetWidth;
    el.classList.remove("prep");

    raf2(function () {
      if (slide.dead) return;
      el.classList.add("active");
      if (!old || old.dead) return;
      old.el.className = "slide t-" + t + " exit";
      // Keep the last frame of the outgoing video visible.
      if (isVideo(old)) { try { old.media.pause(); } catch (e) { /* ignore */ } }
      if (t === "cut") { teardown(old); return; }
      var done = false, timer = null;
      var finish = function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        el.removeEventListener("transitionend", onEnd);
        teardown(old);
      };
      var onEnd = function (ev) { if (ev.target === el) finish(); };
      el.addEventListener("transitionend", onEnd);
      timer = setTimeout(finish, TEARDOWN_SAFETY_MS);
    });
  }

  // teardown(slide): release media and remove the element. Idempotent.
  function teardown(slide) {
    if (!slide || slide.dead) return;
    slide.dead = true;
    var m = slide.media;
    if (slide.plugin) { try { slide.plugin.destroy(); } catch (e) { /* ignore */ } }
    if (slide.native) {
      nativeCall(slide, "teardown");
      var n = natives.indexOf(slide);
      if (n >= 0) natives.splice(n, 1);
    }
    try {
      if (isVideo(slide)) {
        m.pause();
        m.removeAttribute("src");
        m.load();
      } else if (m && m.tagName === "IFRAME") {
        m.src = "about:blank";
      }
    } catch (e) { /* ignore */ }
    if (slide.el.parentNode) slide.el.parentNode.removeChild(slide.el);
    if (active === slide) active = null;
    var i = exiting.indexOf(slide);
    if (i >= 0) exiting.splice(i, 1);
  }

  // clear(): remove everything from the stage (prepared slides must be torn down by the caller).
  function clear() {
    exiting.slice().forEach(teardown);
    teardown(active);
    active = null;
    if (stage) while (stage.firstChild) stage.removeChild(stage.firstChild);
  }

  function playVideo(v, onError) {
    var p;
    try { p = v.play(); } catch (e) { p = Promise.reject(e); }
    if (p && typeof p.then === "function") p.then(null, onError);
  }

  // runVideo(slide, ctx): start an on-screen video and watch it.
  // ctx: {loop() -> bool, done(), fail(reason), note(msg), every(fn, ms)}
  function runVideo(slide, ctx) {
    if (!isVideo(slide)) return;
    var v = slide.media;
    var retried = false;
    var alive = function () { return !slide.dead && active === slide; };
    var onError = function (err) {
      if (!alive()) return;
      var name = err && err.name;
      if (name === "AbortError") return;
      if (name === "NotAllowedError" && !retried && !v.muted) {
        // Unmuted autoplay blocked (no kiosk flags): fall back to muted once.
        retried = true;
        v.muted = true;
        ctx.note("autoplay blocked, playing muted");
        playVideo(v, onError);
        return;
      }
      ctx.fail("play() failed: " + (name || err));
    };
    v.addEventListener("ended", function () {
      if (!alive()) return;
      if (ctx.loop()) {
        try { v.currentTime = 0; } catch (e) { /* ignore */ }
        playVideo(v, onError);
      } else {
        ctx.done();
      }
    });
    v.addEventListener("error", function () {
      if (alive()) ctx.fail("video error " + (v.error ? v.error.code : ""));
    });
    // Stall watchdog: currentTime frozen for 10 s while supposed to play.
    var lastTime = -1, lastAt = Date.now();
    var nudged = false;
    ctx.every(function () {
      if (!alive()) return;
      var now = Date.now();
      // A video that is busy seeking (a big file, a long gap between keyframes) is not stalled.
      if (v.paused || v.ended || v.seeking === true || v.currentTime !== lastTime) {
        lastTime = v.currentTime;
        lastAt = now;
        nudged = false;
      } else if (!nudged && now - lastAt > NUDGE_MS) {
        // Frozen for a few seconds (often the first showing, while the file is still loading): ask the
        // browser to play again before giving up on the slide.
        nudged = true;
        ctx.note("video not advancing, nudging");
        playVideo(v, function () { /* the watchdog decides */ });
      } else if (now - lastAt > STALL_MS) {
        lastAt = now;
        ctx.fail("video stalled");
      }
    }, 1000);
    playVideo(v, onError);
  }

  // resume(slide): restart a video the browser paused (e.g. while the tab was hidden), or
  // resume a paused plugin.
  function resume(slide) {
    if (slide && !slide.dead && slide.plugin) { slide.plugin.resume(); return; }
    if (slide && !slide.dead && slide.native) {
      if (slide.nativePaused) { slide.nativePaused = false; nativeCall(slide, "resume"); }
      return;
    }
    if (isVideo(slide) && slide === active && slide.media.paused && !slide.media.ended) {
      playVideo(slide.media, function () { /* ignore; the stall watchdog decides */ });
    }
  }

  // ---- Player controls (player.js): direct access to the on-screen video.
  function videoOf(slide) { return isVideo(slide) ? slide.media : null; }
  function durationOfVideo(v) { var d = Number(v.duration); return isFinite(d) && d > 0 ? d : 0; }
  // setVideoTime(slide, t): jump to t seconds, kept inside the video. Returns the time set.
  function setVideoTime(slide, t, fast) {
    var v = videoOf(slide);
    if (!v) return 0;
    var d = durationOfVideo(v), max = d ? Math.max(0, d - 0.25) : Number.POSITIVE_INFINITY;
    var target = Math.max(0, Math.min(max, Number(t) || 0));
    try {
      if (fast && typeof v.fastSeek === "function") v.fastSeek(target);
      else v.currentTime = target;
    } catch (e) { return Number(v.currentTime) || 0; }
    return target;
  }
  // seekVideo(slide, deltaS) -> {applied, atEnd}: applied is the real jump in seconds; atEnd
  // says the request went past the end of the video.
  //
  // Seeks are coalesced: a key pressed (or repeated) while the video is still seeking only
  // moves the wanted position; the browser is asked once when the seek in progress finishes
  // ("seeked"), or after SEEK_WAIT_MS if that never comes. Writing currentTime again before
  // the last seek finished cancels it, and on a big long-GOP file every restarted seek
  // decodes from the last keyframe again, so the picture looks frozen.
  var SEEK_WAIT_MS = 1500;
  function seekState(v) {
    if (!v._sk) {
      v._sk = { target: null, timer: null };
      if (typeof v.addEventListener === "function") v.addEventListener("seeked", function () { flushSeek(v, false); });
    }
    return v._sk;
  }
  function writeTime(v, t) {
    try {
      if (typeof v.fastSeek === "function") v.fastSeek(t);
      else v.currentTime = t;
    } catch (e) { /* ignore */ }
  }
  // Writes the wanted position when the video is not seeking (or force is set).
  function flushSeek(v, force) {
    var s = v._sk;
    if (!s) return;
    clearTimeout(s.timer);
    s.timer = null;
    if (s.target === null) return;
    if (v.seeking === true && !force) { armSeek(v, s); return; }
    var t = s.target;
    s.target = null;
    writeTime(v, t);
  }
  function armSeek(v, s) {
    clearTimeout(s.timer);
    s.timer = setTimeout(function () { s.timer = null; flushSeek(v, true); }, SEEK_WAIT_MS);
  }
  function isSeeking(slide) { var v = videoOf(slide); return !!(v && v.seeking === true); }
  function seekVideo(slide, deltaS) {
    var v = videoOf(slide);
    if (!v) return { applied: 0, atEnd: false };
    var s = seekState(v);
    var from = s.target !== null ? s.target : Number(v.currentTime) || 0;
    var d = durationOfVideo(v), want = from + deltaS;
    var atEnd = deltaS > 0 && d > 0 && want > d - 0.25;
    if (atEnd) {
      s.target = null;
      clearTimeout(s.timer);
      s.timer = null;
      return { applied: 0, atEnd: true };
    }
    var max = d ? Math.max(0, d - 0.25) : Number.POSITIVE_INFINITY;
    var to = Math.max(0, Math.min(max, want));
    if (v.seeking === true) {  // still seeking: remember where to go, write once it is done
      s.target = to;
      armSeek(v, s);
    } else {
      s.target = null;
      writeTime(v, to);
    }
    return { applied: to - from, atEnd: false };
  }
  function pauseVideo(slide) {
    var v = videoOf(slide);
    if (v) { try { v.pause(); } catch (e) { /* ignore */ } }
  }
  // playVideo(slide, onErr): play the slide's video; a rejected play() calls onErr(err).
  function playSlide(slide, onErr) {
    var v = videoOf(slide);
    if (v) playVideo(v, onErr || function () { /* ignore */ });
  }

  // setVolume(0..100): the display's volume for the player's own videos (on screen and
  // being prepared). Muting is separate and unchanged; this never touches `muted`.
  function setVolume(v) {
    var n = Number(v);
    volume = n >= 0 && n <= 100 ? n / 100 : (n > 100 ? 1 : (n < 0 ? 0 : 1));
    if (!stage) return;
    var vids = stage.querySelectorAll("video");
    for (var i = 0; i < vids.length; i++) {
      try { vids[i].volume = volume * (vids[i]._gain === undefined ? 1 : vids[i]._gain); } catch (e) { /* ignore */ }
    }
    natives.forEach(function (s) { nativeCall(s, "setVolume", volume * itemGain(s.item)); });
  }

  A.render = {
    init: init,
    prepare: prepare,
    swap: swap,
    teardown: teardown,
    clear: clear,
    runVideo: runVideo,
    resume: resume,
    isVideo: isVideo,
    isPlugin: isPlugin,
    isSeeking: isSeeking,
    runPlugin: runPlugin,
    pause: pause,
    prepCard: prepCard,
    videoOf: videoOf,
    seekVideo: seekVideo,
    setVideoTime: setVideoTime,
    pauseVideo: pauseVideo,
    playVideo: playSlide,
    pickTransition: pickTransition,
    setVolume: setVolume,
  };
})();
