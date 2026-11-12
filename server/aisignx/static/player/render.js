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

  var stage = null;   // #stage element
  var active = null;  // slide on screen
  var exiting = [];   // slides animating out (normally zero or one)

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

  function prepVideo(slide) {
    var item = slide.item;
    var v = document.createElement("video");
    v.className = "media " + fitClass(item.fit);
    // muted must be a property (the attribute alone is not reliable) and set before src.
    v.muted = !!item.muted;
    v.defaultMuted = !!item.muted;
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
    ctx.every(function () {
      if (!alive()) return;
      var now = Date.now();
      if (v.paused || v.ended || v.currentTime !== lastTime) {
        lastTime = v.currentTime;
        lastAt = now;
      } else if (now - lastAt > STALL_MS) {
        lastAt = now;
        ctx.fail("video stalled");
      }
    }, 1000);
    playVideo(v, onError);
  }

  // resume(slide): restart a video the browser paused (e.g. while the tab was hidden).
  function resume(slide) {
    if (isVideo(slide) && slide === active && slide.media.paused && !slide.media.ended) {
      playVideo(slide.media, function () { /* ignore; the stall watchdog decides */ });
    }
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
    pickTransition: pickTransition,
  };
})();
