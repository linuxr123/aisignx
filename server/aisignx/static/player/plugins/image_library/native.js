// AiSignX player: image library plugin (native; see plugins/bundled/image_library).
// Draws library images with <img> and their normal media URLs, so the display cookie, the HTTP
// cache and the kiosk shells' media caches apply. Three styles: slideshow, Ken Burns, collage.
// Classic script, ES2018 at most. No innerHTML; styles through classes and CSSOM only.
// CSS classes start with pl-imglib- (native.css).
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  A.pluginNative = A.pluginNative || {};

  var P = "pl-imglib-";
  var LOAD_MS = 10000;        // one image may take this long before it counts as failed
  var FIRST_MS = 7000;        // prepare() budget (the player gives up after 8 s)
  var DECODE_GRACE_MS = 1500; // decode() can wait for a paint that never comes when hidden
  var TICK_MS = 200;
  var AHEAD = 2;              // images loaded ahead in a slideshow (1 in a collage)
  var KB_VARIANTS = 4;
  var TRANSITION_MS = { fade: 1000, slide: 900, cut: 0 };
  var GRIDS = { "2x2": 2, "3x3": 3 };

  function int(v, lo, hi, d) {
    var n = Number(v);
    return isFinite(n) ? Math.max(lo, Math.min(hi, Math.round(n))) : d;
  }

  // settings(item) -> the item's display settings, with safe fallbacks for odd values.
  function settings(item) {
    var p = (item && item.plugin) || {}, c = p.config || {};
    var mode = c.mode === "kenburns" || c.mode === "collage" ? c.mode : "slideshow";
    var transition = TRANSITION_MS.hasOwnProperty(c.transition) ? c.transition : "fade";
    var fit = c.fit === "cover" || mode === "collage" ? "cover" : "contain";  // tiles always fill
    var cols = GRIDS[c.grid] || 2;
    return {
      mode: mode,
      cols: cols,
      tiles: cols * cols,
      periodMs: int(c.seconds_per_image, 3, 120, 8) * 1000,
      transition: transition,
      fit: fit,
      blur: fit === "contain" && c.background !== "black",
      caption: c.caption === "name",
      shuffle: c.shuffle === true,
    };
  }

  // entries(item) -> [{url, name}] for the manifest's plugin.media list (bad rows dropped).
  function entries(item) {
    var list = (item && item.plugin && item.plugin.media) || [];
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m && typeof m.url === "string" && m.url) {
        out.push({ url: m.url, name: typeof m.name === "string" ? m.name : "", bad: false });
      }
    }
    return out;
  }

  // order(list, shuffle, rnd) -> a copy, Fisher-Yates shuffled when asked.
  function order(list, shuffle, rnd) {
    var out = list.slice();
    if (!shuffle) return out;
    rnd = rnd || Math.random;
    for (var i = out.length - 1; i > 0; i--) {
      var j = Math.floor(rnd() * (i + 1));
      var t = out[i]; out[i] = out[j]; out[j] = t;
    }
    return out;
  }

  // pickTile(count, last, rnd) -> a random tile index other than `last` (when there is a choice).
  function pickTile(count, last, rnd) {
    if (count <= 1) return 0;
    var i = Math.floor((rnd || Math.random)() * (count - 1));
    if (i >= count - 1) i = count - 2;
    return last >= 0 && i >= last ? i + 1 : i;
  }

  // ---- DOM helpers.
  function div(cls) { var e = document.createElement("div"); e.className = cls; return e; }
  function image(cls) {
    var e = document.createElement("img");
    e.className = cls;
    e.alt = "";
    e.decoding = "async";
    e.draggable = false;
    return e;
  }
  function later(st, fn, ms) {
    var id = setTimeout(function () {
      var i = st.timeouts.indexOf(id);
      if (i >= 0) st.timeouts.splice(i, 1);
      if (!st.dead) fn();
    }, ms);
    st.timeouts.push(id);
  }
  function withTimeout(promise, ms) {
    var timer;
    return Promise.race([promise, new Promise(function (_, reject) {
      timer = setTimeout(function () { reject(new Error("image timed out")); }, ms);
    })]).then(function (v) { clearTimeout(timer); return v; },
      function (e) { clearTimeout(timer); throw e; });
  }
  // load(img, url) -> Promise that settles once the image can be shown.
  function load(img, url) {
    var canDecode = typeof img.decode === "function";
    var ready = new Promise(function (resolve, reject) {
      img.onload = function () { setTimeout(resolve, canDecode ? DECODE_GRACE_MS : 0); };
      img.onerror = function () { reject(new Error("image failed to load")); };
    });
    img.src = url;
    if (canDecode) ready = Promise.race([ready, img.decode()]);
    return withTimeout(ready, LOAD_MS);
  }

  // ---- Frames: one image (plus its blurred backdrop and caption) in an absolutely placed box.
  function makeFrame(st, entry) {
    var el = div(P + "frame");
    var bg = null;
    if (st.cfg.blur) {
      bg = el.appendChild(image(P + "bg"));
    }
    var img = el.appendChild(image(P + "img " + P + "fit-" + st.cfg.fit));
    if (st.cfg.caption && entry.name) {
      var cap = el.appendChild(div(P + "cap"));
      cap.textContent = entry.name;
    }
    var frame = { el: el, img: img, bg: bg, entry: entry };
    frame.ready = load(img, entry.url).then(function () {
      if (bg && !st.dead) bg.src = entry.url;  // from the memory cache by now
    });
    st.frames.push(frame);
    return frame;
  }
  function release(st, frame) {
    if (!frame) return;
    var i = st.frames.indexOf(frame);
    if (i >= 0) st.frames.splice(i, 1);
    [frame.img, frame.bg].forEach(function (im) {
      if (!im) return;
      im.onload = im.onerror = null;
      try { im.removeAttribute("src"); } catch (e) { /* ignore */ }
    });
    if (frame.el.parentNode) frame.el.parentNode.removeChild(frame.el);
  }
  function visible(st, entry) {
    for (var i = 0; i < st.tiles.length; i++) {
      if (st.tiles[i].frame && st.tiles[i].frame.entry === entry) return true;
    }
    return false;
  }
  // take(st) -> a new frame for the next usable entry, or null when every entry failed.
  // A collage with spare images skips the ones already on screen.
  function take(st) {
    var n = st.entries.length, spare = st.cfg.mode === "collage" && n > st.cfg.tiles;
    var fallback = null;
    for (var k = 0; k < n; k++) {
      var e = st.entries[st.next % n];
      st.next++;
      if (e.bad) continue;
      if (spare && visible(st, e)) { fallback = fallback || e; continue; }
      return makeFrame(st, e);
    }
    return fallback ? makeFrame(st, fallback) : null;
  }
  // loadOne(st, retry) -> Promise<frame|null>: the next image, once loaded. A failed image is
  // marked bad; with retry the next entry is tried, else null comes back (advance() moves on).
  function loadOne(st, retry) {
    var f = take(st);
    if (!f) return Promise.resolve(null);
    return f.ready.then(function () {
      if (st.dead) { release(st, f); return null; }
      return f;
    }, function () {
      f.entry.bad = true;
      release(st, f);
      return st.dead || !retry ? null : loadOne(st, true);
    });
  }
  function usable(st) {
    for (var i = 0; i < st.entries.length; i++) if (!st.entries[i].bad) return true;
    return false;
  }

  // ---- Showing a frame in a box (the root, or a collage tile), with the item's transition.
  function kenBurns(st, frame) {
    if (st.cfg.mode !== "kenburns") return;
    var v = st.kb = (st.kb + 1 + Math.floor(Math.random() * (KB_VARIANTS - 1))) % KB_VARIANTS;
    frame.img.style.animationDuration = (st.cfg.periodMs + TRANSITION_MS[st.cfg.transition] + 500) + "ms";
    frame.img.classList.add(P + "kb");
    frame.img.classList.add(P + "kb-" + v);
  }
  function put(st, box, old, frame, instant) {
    var el = frame.el, t = st.cfg.transition;
    if (instant || t === "cut") {
      el.classList.add(P + "on");
      box.appendChild(el);
      kenBurns(st, frame);
      release(st, old);
      return;
    }
    el.classList.add(P + "in");
    box.appendChild(el);
    void el.offsetWidth;  // apply the start state before animating out of it
    el.classList.remove(P + "in");
    el.classList.add(P + "on");
    kenBurns(st, frame);
    if (old) {
      old.el.classList.remove(P + "on");
      old.el.classList.add(P + "out");
      later(st, function () { release(st, old); }, TRANSITION_MS[t] + 150);
    }
  }

  // ---- The cycle: tick() counts on-screen, unpaused time; advance() shows the next image.
  function fail(st) {
    if (st.degraded) return;
    st.degraded = true;
    var card = st.root.appendChild(div(P + "card"));
    card.appendChild(div(P + "card-title")).textContent = "Images unavailable";
    card.appendChild(div(P + "card-text")).textContent = "The images could not be loaded.";
    if (st.ctx && typeof st.ctx.degraded === "function") st.ctx.degraded();
  }
  function swaps(st) {
    var n = st.entries.length;
    if (st.cfg.mode === "collage") return n > st.cfg.tiles;
    return n > 1 || st.cfg.mode === "kenburns";
  }
  function refill(st) {
    var ahead = st.cfg.mode === "collage" ? 1 : AHEAD;
    while (swaps(st) && st.queue.length < ahead) st.queue.push(loadOne(st, false));
  }
  function advance(st) {
    var next = st.queue.shift();
    if (!next) return;
    st.busy = true;
    next.then(function (frame) {
      st.busy = false;
      if (st.dead) { release(st, frame); return; }
      if (!frame) {  // that image failed: show the next one now, or the card if none is left
        if (!usable(st)) { fail(st); return; }
        refill(st);
        advance(st);
        return;
      }
      if (st.cfg.mode === "collage") {
        var i = st.lastTile = pickTile(st.tiles.length, st.lastTile, Math.random);
        var tile = st.tiles[i], old = tile.frame;
        tile.frame = frame;
        put(st, tile.el, old, frame, false);
      } else {
        var prev = st.cur;
        st.cur = frame;
        put(st, st.root, prev, frame, false);
      }
      st.elapsed = 0;
      refill(st);
    });
  }
  function tick(st) {
    var now = Date.now();
    if (st.dead) return;
    if (st.paused || st.busy || st.degraded) { st.last = now; return; }
    st.elapsed += Math.max(0, now - st.last);
    st.last = now;
    if (st.elapsed >= st.cfg.periodMs && swaps(st)) advance(st);
  }

  // ---- The player's native plugin interface (render.js).
  function prepare(slide, api) {
    var item = slide.item, cfg = settings(item);
    var list = order(entries(item), cfg.shuffle, Math.random);
    if (!list.length) return api.prepCard(slide, "Image library", "No images to show");
    var root = div("media " + P + "root " + P + "mode-" + cfg.mode + " " + P + "t-" + cfg.transition);
    var st = slide.imglib = {
      cfg: cfg, entries: list, next: 0, root: root, api: api, ctx: null, frames: [], tiles: [],
      queue: [], cur: null, timeouts: [], kb: 0, lastTile: -1, elapsed: 0, last: Date.now(),
      paused: false, busy: false, dead: false, degraded: false,
    };
    api.attach(slide, root);
    var first;
    if (cfg.mode === "collage") {
      var grid = root.appendChild(div(P + "grid " + P + "grid-" + cfg.cols));
      var loads = [];
      for (var i = 0; i < cfg.tiles; i++) {
        var tile = { el: grid.appendChild(div(P + "tile")), frame: null };
        st.tiles.push(tile);
        loads.push(loadOne(st, true));
      }
      first = Promise.all(loads).then(function (frames) {
        var any = false;
        frames.forEach(function (f, k) {
          if (!f) return;
          if (st.degraded) { release(st, f); return; }
          any = true;
          st.tiles[k].frame = f;
          put(st, st.tiles[k].el, null, f, true);
        });
        return any;
      });
    } else {
      first = loadOne(st, true).then(function (f) {
        if (!f) return false;
        if (st.degraded) { release(st, f); return false; }  // too late: the card is up
        st.cur = f;
        put(st, root, null, f, true);
        return true;
      });
    }
    var timeout = new Promise(function (resolve) { later(st, function () { resolve(false); }, FIRST_MS); });
    return Promise.race([first, timeout]).then(function (ok) {
      if (st.dead) return;
      if (!ok) { fail(st); return; }
      refill(st);
    });
  }

  function run(slide, ctx) {
    var st = slide.imglib;
    if (!st) { if (ctx && ctx.degraded) ctx.degraded(); return; }  // the "no images" card
    st.ctx = ctx;
    st.last = Date.now();
    st.elapsed = 0;
    if (st.degraded) { if (ctx.degraded) ctx.degraded(); return; }
    // The first Ken Burns move restarts now, so it starts as the slide comes on screen.
    if (st.cur && st.cfg.mode === "kenburns") {
      var img = st.cur.img;
      img.className = P + "img " + P + "fit-" + st.cfg.fit;
      void img.offsetWidth;
      kenBurns(st, st.cur);
    }
    ctx.every(function () { tick(st); }, TICK_MS);
  }

  function pause(slide) {
    var st = slide.imglib;
    if (!st) return;
    st.paused = true;
    st.root.classList.add(P + "paused");
  }

  function resume(slide) {
    var st = slide.imglib;
    if (!st) return;
    st.paused = false;
    st.last = Date.now();
    st.root.classList.remove(P + "paused");
  }

  function teardown(slide) {
    var st = slide.imglib;
    if (!st || st.dead) return;
    st.dead = true;
    st.timeouts.forEach(clearTimeout);
    st.timeouts = [];
    st.queue = [];
    st.frames.slice().forEach(function (f) { release(st, f); });
    st.cur = null;
  }

  A.pluginNative.image_library = {
    prepare: prepare,
    run: run,
    pause: pause,
    resume: resume,
    teardown: teardown,
    // Pure helpers, exported for tests/js/plugin_image_library.js.
    util: { settings: settings, entries: entries, order: order, pickTile: pickTile },
  };
})();
