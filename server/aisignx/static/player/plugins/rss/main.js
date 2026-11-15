// AiSignX news feed plugin (RSS / Atom), frame runner. Three layouts:
//   list    pages of headlines that fit the screen, one page every rotate_seconds
//   single  one headline at a time, large, with its picture
//   ticker  the latest headline, and a continuously scrolling band with the rest
// Data comes from the server (already plain text); pictures and QR codes are assets.
// Classic script, ES2018; DOM built with textContent only; timers through ctx.
(function () {
  "use strict";

  var TICKER_SPEED_VMIN_S = 11;     // band speed, in vmin per second
  var FADE_MS = 800;
  var READY_WAIT_MS = 2000;         // wait this long for the first pictures
  var MODES = { list: 1, single: 1, ticker: 1 };

  // ---- pure helpers (also used by tests/js/plugin_rss.js) ---------------------------------

  function settings(config) {
    var c = config || {};
    var num = function (v, lo, hi, d) { v = Number(v); return isFinite(v) ? Math.min(hi, Math.max(lo, Math.round(v))) : d; };
    return {
      title: typeof c.title === "string" ? c.title.trim() : "",
      mode: MODES[c.mode] ? c.mode : "list",
      rotateMs: num(c.rotate_seconds, 5, 60, 10) * 1000,
      showImages: c.show_images !== false,
      showDates: c.show_dates !== false,
      showSource: c.show_source !== false,
      showQr: !!c.show_qr,
      theme: c.theme === "light" ? "light" : "dark",
    };
  }

  function feedTitle(cfg, data) {
    var feed = (data && data.feed) || {};
    return cfg.title || feed.title || feed.link_host || "News";
  }

  // The first letter or digit of a title, for the tile shown in place of a missing picture.
  function initial(text) {
    var m = /[0-9A-Za-zÀ-ɏͰ-ϿЀ-ӿ]/.exec(String(text || ""));
    return m ? m[0].toUpperCase() : "";
  }

  // "5 minutes ago", "3 hours ago", else a short date ("12 Nov, 14:32").
  function when(now, ms, locale, format) {
    ms = Number(ms);
    if (!ms) return "";
    var diff = now - ms;
    var rel = function (value, unit) {
      try { return new Intl.RelativeTimeFormat(locale || undefined, { numeric: "auto" }).format(value, unit); } catch (e) { return ""; }
    };
    var out = "";
    if (diff >= -5 * 60000 && diff < 60000) out = rel(0, "minute");
    else if (diff >= 0 && diff < 3600000) out = rel(-Math.round(diff / 60000), "minute");
    else if (diff >= 0 && diff < 86400000) out = rel(-Math.round(diff / 3600000), "hour");
    return out || format(ms, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  // Groups rows into pages: heights in px, gap between rows, avail = page height.
  // Every page holds at least one row. Pages are balanced (8 rows that need 3 pages
  // become 3 + 3 + 2, not 4 + 3 + 1) whenever that still takes no more pages.
  function paginate(heights, gap, avail) {
    var fill = function (most) {
      var pages = [], page = [], used = 0;
      for (var i = 0; i < heights.length; i++) {
        var need = (page.length ? gap : 0) + heights[i];
        if (page.length && (used + need > avail || page.length >= most)) {
          pages.push(page); page = []; used = 0; need = heights[i];
        }
        page.push(i);
        used += need;
      }
      if (page.length) pages.push(page);
      return pages;
    };
    var greedy = fill(Infinity);
    if (greedy.length < 2) return greedy;
    var even = fill(Math.ceil(heights.length / greedy.length));
    return even.length === greedy.length ? even : greedy;
  }

  // Which page/card to start on, so successive showings move through the feed.
  function startIndex(now, rotateMs, count) {
    if (count < 2) return 0;
    return Math.floor(now / Math.max(1000, rotateMs)) % count;
  }

  function tickerSeconds(seqWidthPx, vminPx) {
    var speed = Math.max(1, TICKER_SPEED_VMIN_S * (vminPx || 1));
    return Math.max(8, seqWidthPx / speed);
  }

  // ---- DOM helpers -------------------------------------------------------------------------

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null && text !== "") e.textContent = String(text);
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function vmin() { return Math.min(window.innerWidth || 0, window.innerHeight || 0) / 100 || 1; }

  // ---- state ---------------------------------------------------------------------------------

  var S = { ctx: null, cfg: null, data: null, shown: false, paused: false, timer: null,
            index: 0, slides: [], layer: null, stage: null, progress: null, count: null,
            track: null, pendingBand: null, resizeTimer: null, column: false };

  // A picture box whose <img> fills in when the asset arrives. Returns [node, promise].
  function picture(id, cls) {
    var box = el("div", "rss-pic" + (cls ? " " + cls : ""));
    var img = el("img");
    img.alt = "";
    img.decoding = "async";
    box.appendChild(img);
    var p = S.ctx.asset(id).then(function (url) {
      return new Promise(function (resolve) {
        img.onload = function () { img.className = "is-loaded"; resolve(true); };
        img.onerror = function () { resolve(false); };
        img.src = url;
      });
    }, function () { return false; });
    return [box, p];
  }

  function qrCode(id) {
    var box = el("div", "rss-qr");
    var img = el("img");
    img.alt = "";
    box.appendChild(img);
    S.ctx.asset(id).then(function (url) { img.src = url; }, function () {
      if (box.parentNode) box.parentNode.removeChild(box);
    });
    return box;
  }

  function meta(item) {
    var parts = [];
    if (S.cfg.showDates && item.published_ms) {
      var text = when(S.ctx.now(), item.published_ms, S.ctx.locale, S.ctx.format);
      if (text) parts.push(text);
    }
    var feed = (S.data && S.data.feed) || {};
    if (S.cfg.showSource && item.source && item.source !== feed.title) parts.push(item.source);
    if (!parts.length) return null;
    var line = el("div", "rss-meta");
    parts.forEach(function (p, i) {
      if (i) line.appendChild(el("span", "rss-meta-sep", "·"));
      line.appendChild(el("span", null, p));
    });
    return line;
  }

  function header() {
    var head = el("header", "rss-head");
    head.appendChild(el("div", "rss-mark"));
    var text = head.appendChild(el("div", "rss-head-text"));
    text.appendChild(el("h1", "rss-title rss-clamp", feedTitle(S.cfg, S.data)));
    var host = S.data.feed && S.data.feed.link_host;
    if (S.cfg.showSource && host && host !== feedTitle(S.cfg, S.data)) text.appendChild(el("div", "rss-host", host));
    return head;
  }

  function footer() {
    var foot = el("footer", "rss-foot");
    S.progress = el("div", "rss-progress-bar");
    foot.appendChild(el("div", "rss-progress")).appendChild(S.progress);
    S.count = foot.appendChild(el("div", "rss-count"));
    return foot;
  }

  // ---- list ------------------------------------------------------------------------------------

  // A tile in place of a missing picture, so the headlines of a list stay in one column.
  function blankPicture() {
    var box = el("div", "rss-pic is-blank");
    box.appendChild(el("span", null, initial(feedTitle(S.cfg, S.data))));
    return box;
  }

  function row(item, waits, column) {
    var r = el("article", "rss-row");
    if (S.cfg.showImages && item.image) {
      var pic = picture(item.image);
      r.appendChild(pic[0]);
      if (waits) waits.push(pic[1]);
    } else if (column) {
      r.appendChild(blankPicture());
    }
    var body = r.appendChild(el("div", "rss-row-body"));
    body.appendChild(el("h2", "rss-row-title rss-clamp", item.title));
    if (item.summary) body.appendChild(el("p", "rss-row-summary rss-clamp", item.summary));
    var m = meta(item);
    if (m) body.appendChild(m);
    if (S.cfg.showQr && item.qr) r.appendChild(qrCode(item.qr));
    return r;
  }

  // Measures every row in a hidden page and splits the items into pages that fit.
  function listSlides(items) {
    var probe = el("div", "rss-layer rss-page");
    probe.style.visibility = "hidden";
    S.stage.appendChild(probe);
    var rows = items.map(function (item) { return probe.appendChild(row(item, null, S.column)); });
    var avail = probe.clientHeight;
    var gap = parseFloat(window.getComputedStyle(probe).rowGap) || 0;
    var heights = rows.map(function (r) { return r.offsetHeight; });
    S.stage.removeChild(probe);
    if (!avail || !heights[0]) {  // not laid out (hidden frame): a fixed count per page
      var per = window.innerHeight > window.innerWidth ? 6 : 4, out = [];
      for (var i = 0; i < items.length; i += per) out.push(items.slice(i, i + per));
      return out;
    }
    return paginate(heights, gap, avail).map(function (idx) { return idx.map(function (i) { return items[i]; }); });
  }

  function buildPage(items, waits) {
    var page = el("div", "rss-layer rss-page");
    items.forEach(function (item) { page.appendChild(row(item, waits, S.column)); });
    return page;
  }

  // ---- one at a time -----------------------------------------------------------------------------

  function buildCard(item, waits) {
    var card = el("div", "rss-layer rss-card");
    var hasPic = S.cfg.showImages && item.image;
    if (hasPic) {
      var pic = picture(item.image);
      card.appendChild(pic[0]);
      waits.push(pic[1]);
    } else {
      card.className += " no-pic";
    }
    var body = card.appendChild(el("div", "rss-card-body"));
    var src = S.cfg.showSource && item.source ? item.source : "";
    if (src) body.appendChild(el("div", "rss-kicker", src));
    body.appendChild(el("h2", "rss-card-title rss-clamp", item.title));
    if (item.summary) body.appendChild(el("p", "rss-card-summary rss-clamp", item.summary));
    var foot = body.appendChild(el("div", "rss-card-foot"));
    var m = meta({ published_ms: item.published_ms });
    foot.appendChild(m || el("div"));
    if (S.cfg.showQr && item.qr) foot.appendChild(qrCode(item.qr));
    return card;
  }

  // ---- rotation (list pages and cards) -----------------------------------------------------------

  function show(i, waits) {
    var n = S.slides.length;
    S.index = ((i % n) + n) % n;
    var next = S.slides[S.index](waits || []);
    var old = S.layer;
    S.stage.appendChild(next);
    void next.offsetWidth;  // start the fade from opacity 0
    next.className += " is-in";
    S.layer = next;
    if (old) {
      old.className = old.className.replace(" is-in", "");
      S.ctx.later(function () { if (old.parentNode) old.parentNode.removeChild(old); }, FADE_MS);
    }
    if (S.count) S.count.textContent = n > 1 ? (S.index + 1) + " / " + n : "";
    if (S.progress) S.progress.parentNode.style.visibility = n > 1 ? "" : "hidden";
    runProgress();
  }

  function runProgress() {
    var bar = S.progress;
    if (!bar) return;
    // The bar shows the time to the next change on the shared clock, so it fills in step on every screen.
    var phase = (S.ctx.now() % S.cfg.rotateMs) / S.cfg.rotateMs;
    bar.style.transition = "none";
    bar.style.width = S.slides.length > 1 ? (phase * 100).toFixed(1) + "%" : "100%";
    if (S.slides.length < 2 || !S.shown || S.paused) return;
    void bar.offsetWidth;
    bar.style.transition = "width " + Math.round(S.cfg.rotateMs * (1 - phase)) + "ms linear";
    bar.style.width = "100%";
  }

  function startRotation() {
    stopRotation();
    if (S.slides.length < 2 || !S.shown || S.paused) return;
    runProgress();
    // On the shared clock: every screen changes to the same headline at the same moment.
    S.timer = S.ctx.aligned(function () { show(startIndex(S.ctx.now(), S.cfg.rotateMs, S.slides.length)); }, S.cfg.rotateMs);
  }

  function stopRotation() {
    if (S.timer) { S.ctx.clear(S.timer); S.timer = null; }
    if (S.progress) {
      var w = window.getComputedStyle(S.progress).width;
      S.progress.style.transition = "none";
      S.progress.style.width = w;
    }
  }

  // ---- ticker --------------------------------------------------------------------------------------

  function fillTrack(track, items) {
    clear(track);
    var seq = [];
    items.forEach(function (item) {
      var span = el("span", "rss-tick", item.title);
      if (S.cfg.showDates && item.published_ms) {
        var t = when(S.ctx.now(), item.published_ms, S.ctx.locale, S.ctx.format);
        if (t) span.appendChild(el("span", "rss-tick-time", t));
      }
      seq.push(span, el("span", "rss-tick-sep"));
    });
    var view = track.parentNode;
    var viewW = (view && view.clientWidth) || window.innerWidth || 1000;
    var copies = 0, width = 0;
    do {  // one sequence at least as wide as the band, then the same again for the loop
      seq.forEach(function (n) { track.appendChild(n.cloneNode(true)); });
      copies++;
      width = track.scrollWidth;
    } while (width > 0 && width < viewW && copies < 20);
    var once = Array.prototype.slice.call(track.childNodes);
    once.forEach(function (n) { track.appendChild(n.cloneNode(true)); });
    var seconds = tickerSeconds(width || viewW, vmin());
    track.style.animationDuration = seconds.toFixed(2) + "s";
    // Start the scroll where the shared clock says it is, so every screen shows the same part.
    track.style.animationDelay = "-" + ((S.ctx.now() / 1000) % seconds).toFixed(2) + "s";
  }

  // The band carries the headlines after the lead (all of them when there is only one).
  function bandItems(list) { return list.length > 1 ? list.slice(1) : list; }

  // The latest headline as a card above the band; it fades to the new one on a data update.
  function buildTicker(root, list, waits) {
    S.stage = root.appendChild(el("main", "rss-stage"));
    S.slides = [function (w) { return buildCard(items()[0], w); }];
    show(0, waits);
    var band = root.appendChild(el("div", "rss-band"));
    band.appendChild(el("div", "rss-band-label")).appendChild(el("span", null, "Headlines"));
    var view = band.appendChild(el("div", "rss-band-view"));
    S.track = view.appendChild(el("div", "rss-track"));
    if (!S.shown || S.paused) S.track.className += " is-paused";
    fillTrack(S.track, bandItems(list));
    // New headlines replace the text in place, at the seam, so the band never jumps.
    S.track.addEventListener("animationiteration", function () {
      if (!S.pendingBand) return;
      var pending = S.pendingBand;
      S.pendingBand = null;
      fillTrack(S.track, pending);
    });
  }

  // ---- render --------------------------------------------------------------------------------------

  function items() { return (S.data && Array.isArray(S.data.items)) ? S.data.items : []; }

  function badge() {
    var ctx = S.ctx;
    var at = ctx.stale || ctx.offline ? ctx.fetchedAt : 0;
    ctx.staleBadge(at);
    toggle(ctx.root, "has-badge", !!at);  // the header leaves room for the badge
  }

  function toggle(node, cls, on) {
    var list = node.className.split(" ").filter(function (c) { return c && c !== cls; });
    if (on) list.push(cls);
    node.className = list.join(" ");
  }

  function render() {
    var ctx = S.ctx, root = ctx.root, list = items();
    stopRotation();
    S.slides = []; S.layer = null; S.track = null; S.pendingBand = null; S.progress = null; S.count = null;
    clear(root);
    root.className = "rss theme-" + S.cfg.theme + " mode-" + S.cfg.mode;
    if (!S.data) {
      ctx.fallback(feedTitle(S.cfg, null), ctx.offline ? "This screen is offline" : "Loading the latest headlines…");
      return Promise.resolve();
    }
    if (!list.length) {
      ctx.fallback(feedTitle(S.cfg, S.data), "This feed has no headlines right now");
      return Promise.resolve();
    }
    ctx.fallback(null);
    badge();
    var waits = [];
    root.appendChild(header());
    if (S.cfg.mode === "ticker") {
      buildTicker(root, list, waits);
      return Promise.all(waits);
    }
    S.stage = root.appendChild(el("main", "rss-stage"));
    root.appendChild(footer());
    if (S.cfg.mode === "single") {
      S.slides = list.map(function (item) { return function (w) { return buildCard(item, w); }; });
    } else {
      S.column = S.cfg.showImages && list.some(function (item) { return !!item.image; });
      S.slides = listSlides(list).map(function (page) { return function (w) { return buildPage(page, w); }; });
    }
    show(startIndex(ctx.now(), S.cfg.rotateMs, S.slides.length), waits);
    startRotation();
    return Promise.all(waits);
  }

  // Draws, then reports ready once the first pictures are in (or after READY_WAIT_MS).
  function draw() {
    var done = false;
    var ready = function () { if (!done) { done = true; S.ctx.ready(); } };
    S.ctx.later(ready, READY_WAIT_MS);
    render().then(ready, ready);
  }

  function setPaused(paused) {
    S.paused = paused;
    if (S.track) S.track.className = "rss-track" + (paused || !S.shown ? " is-paused" : "");
    if (paused) stopRotation(); else startRotation();
  }

  if (window.AiSignXPlugin) {
    window.AiSignXPlugin.register({
      init: function (ctx) {
        S.ctx = ctx;
        S.cfg = settings(ctx.config);
        S.data = ctx.data;
        draw();
        window.addEventListener("resize", function () {
          if (S.resizeTimer) ctx.clear(S.resizeTimer);
          S.resizeTimer = ctx.later(function () { S.resizeTimer = null; render(); }, 250);
        });
      },
      data: function (ctx) {
        var had = !!S.data;
        S.data = ctx.data;
        if (had && S.cfg.mode === "ticker" && S.track && items().length) {
          S.pendingBand = bandItems(items());  // the band updates at its seam
          show(0);                             // the lead fades to the newest headline
          badge();
          return;
        }
        draw();
      },
      show: function () {
        S.shown = true;
        setPaused(S.paused);
      },
      net: function () { if (S.data) badge(); },
      pause: function () { setPaused(true); },
      resume: function () { setPaused(false); },
      destroy: function () { stopRotation(); },
    });
  }

  window.AiSignXRss = { settings: settings, feedTitle: feedTitle, initial: initial, when: when, paginate: paginate,
                        startIndex: startIndex, tickerSeconds: tickerSeconds };
})();
