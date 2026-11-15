// AiSignX weather radar: stacks the server-drawn base map and radar frames and steps
// through them. No map library, no network: the pictures arrive as assets from the player.
// Data: {frames: [{t (epoch ms), asset}], basemap, attribution, alerts, source, ...}.
(function () {
  "use strict";

  var HOLD_LAST = 3;          // the newest frame stays up this many frame times
  var READY_MS = 6000;        // show the slide by then, even if some frames are still loading
  var ALERT_EVERY_MS = 7000;  // several alerts take turns

  // ---- Pure helpers (also used by tests/js/plugin_weatherradar.js).
  function frameTimes(n, frameMs) {
    var ms = Math.max(300, Math.min(2000, Number(frameMs) || 600)), out = [];
    for (var i = 0; i < n; i++) out.push(i === n - 1 ? ms * HOLD_LAST : ms);
    return out;
  }
  function ago(t, now) {
    var min = Math.round((now - t) / 60000);
    if (min < 2) return "Just now";
    if (min < 60) return min + " min ago";
    var h = Math.floor(min / 60), m = min % 60;
    return h + " h" + (m ? " " + m + " min" : "") + " ago";
  }
  function liveAlerts(list, now) {
    return (Array.isArray(list) ? list : []).filter(function (a) {
      return a && a.event && !(a.ends_ms && a.ends_ms < now);
    });
  }
  function severityClass(sev) {
    var s = String(sev || "").toLowerCase();
    return "wr-alert wr-sev-" + (/^(extreme|severe|moderate|minor)$/.test(s) ? s : "unknown");
  }
  window.WeatherRadar = { frameTimes: frameTimes, ago: ago, liveAlerts: liveAlerts, severityClass: severityClass };

  // ---- DOM.
  function el(tag, cls, parent, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  var S = null;  // the built view

  function build(ctx) {
    var root = ctx.root;
    clear(root);
    root.className = "wr";
    var map = el("div", "wr-map", root);
    var base = el("img", "wr-base", map);
    base.alt = "";
    var radar = el("div", "wr-radar", map);
    el("div", "wr-pin", root);
    var top = el("div", "wr-top", root);
    var head = el("div", "wr-head", top);
    var place = el("div", "wr-place", head);
    el("div", "wr-kicker", head, "Weather radar");
    var when = el("div", "wr-when", top);
    var clock = el("div", "wr-clock", when);
    var rel = el("div", "wr-ago", when);
    var progress = el("div", "wr-progress", root);
    var alert = el("div", "wr-alert", root);
    alert.hidden = true;
    var loc = ctx.config.location || {};
    place.textContent = String(loc.label || loc.q || "").trim() || "Radar";
    S = { base: base, radar: radar, progress: progress, clock: clock, rel: rel, alert: alert, when: when,
      imgs: {}, frames: [], times: [], idx: 0, timer: 0, paused: false, alertIdx: 0, alertTimer: 0, gen: 0 };
  }

  // A promise of a loaded <img> for an asset id; null when it cannot be had.
  function image(ctx, id, cls) {
    if (!id) return Promise.resolve(null);
    return ctx.asset(id).then(function (url) {
      return new Promise(function (resolve) {
        var img = document.createElement("img");
        img.className = cls;
        img.alt = "";
        img.addEventListener("load", function () { resolve(img); });
        img.addEventListener("error", function () { resolve(null); });
        img.src = url;
      });
    }, function () { return null; });
  }

  function settle(promises) {
    return Promise.all(promises.map(function (p) { return p.then(null, function () { return null; }); }));
  }

  // Loads the pictures of ctx.data, then swaps them in (an older load that finishes late is dropped).
  function load(ctx) {
    var d = ctx.data || {}, gen = ++S.gen;
    var list = (Array.isArray(d.frames) ? d.frames : []).filter(function (f) { return f && f.asset; });
    var baseP = d.basemap === S.baseId ? Promise.resolve(false) : image(ctx, d.basemap, "wr-base");
    var frameP = list.map(function (f) {
      return S.imgs[f.asset] ? Promise.resolve(S.imgs[f.asset].cloneNode(false)) : image(ctx, f.asset, "wr-frame");
    });
    return Promise.all([baseP, settle(frameP)]).then(function (got) {
      if (!S || gen !== S.gen) return false;
      if (got[0]) { S.base.src = got[0].src; S.baseId = d.basemap; }
      var frames = [], imgs = {};
      list.forEach(function (f, i) {
        var img = got[1][i];
        if (!img) return;
        img.className = "wr-frame";
        frames.push({ t: Number(f.t) || 0, img: img });
        imgs[f.asset] = img;
      });
      if (!frames.length && !S.frames.length) return false;
      if (frames.length) {
        clear(S.radar);
        frames.forEach(function (f) { S.radar.appendChild(f.img); });
        S.frames = frames;
        S.imgs = imgs;
        S.times = frameTimes(frames.length, ctx.config.frame_ms);
        drawProgress();
        S.idx = Math.min(S.idx, frames.length - 1);
        show(ctx, S.idx);
      }
      return true;
    });
  }

  function drawProgress() {
    clear(S.progress);
    S.frames.forEach(function () { el("span", "wr-seg", S.progress); });
  }

  function show(ctx, i) {
    S.idx = i;
    S.frames.forEach(function (f, k) { f.img.classList.toggle("wr-on", k === i); });
    var segs = S.progress.children;
    for (var k = 0; k < segs.length; k++) segs[k].className = "wr-seg" + (k < i ? " wr-past" : k === i ? " wr-now" : "");
    var f = S.frames[i];
    if (!f) return;
    S.clock.textContent = ctx.format(f.t, { hour: "numeric", minute: "2-digit" });
    S.rel.textContent = i === S.frames.length - 1 ? "Latest · " + ago(f.t, ctx.now()) : ago(f.t, ctx.now());
  }

  function step(ctx) {
    S.timer = 0;
    if (S.paused || !S.frames.length) return;
    show(ctx, (S.idx + 1) % S.frames.length);
    S.timer = ctx.later(function () { step(ctx); }, S.times[S.idx]);
  }
  function play(ctx, fromStart) {
    if (S.timer) ctx.clear(S.timer);
    S.timer = 0;
    if (!S.frames.length) return;
    if (fromStart) show(ctx, 0);
    S.timer = ctx.later(function () { step(ctx); }, S.times[S.idx] || 600);
  }

  function drawAlert(ctx) {
    var list = liveAlerts((ctx.data || {}).alerts, ctx.now());
    if (S.alertTimer) { ctx.clear(S.alertTimer); S.alertTimer = 0; }
    S.alert.hidden = !list.length;
    if (!list.length) return;
    var paint = function () {
      var a = list[S.alertIdx % list.length];
      clear(S.alert);
      S.alert.className = severityClass(a.severity);
      el("div", "wr-alert-event", S.alert, a.event);
      if (a.headline) el("div", "wr-alert-text", S.alert, a.headline);
      if (list.length > 1) el("div", "wr-alert-count", S.alert, (S.alertIdx % list.length + 1) + " / " + list.length);
    };
    paint();
    if (list.length > 1) S.alertTimer = ctx.every(function () { S.alertIdx++; paint(); }, ALERT_EVERY_MS);
  }

  function badges(ctx) {
    ctx.staleBadge(ctx.stale || ctx.offline ? ctx.fetchedAt : 0);
    // The SDK pins its "Updated 14:32" badge to a corner; here it sits under the frame time.
    var badge = document.querySelector(".aisx-stale");
    if (badge && badge.parentNode !== S.when) S.when.appendChild(badge);
    var d = ctx.data || {};
    ctx.attribution(d.attribution ? String(d.attribution) : undefined);
  }

  function noData(ctx) {
    if (ctx.offline) ctx.fallback("Weather radar", "This screen is offline");
    else if (ctx.warming || !ctx.data) ctx.fallback("Weather radar", "Loading radar images…");
    else ctx.fallback("Weather radar", "No radar images are available right now");
  }

  // After a load: show the frames (or the right card), then tell the player the slide is ready.
  function apply(ctx, first) {
    if (!S) return;
    if (!S.frames.length) { noData(ctx); return; }
    ctx.fallback(null);
    badges(ctx);
    drawAlert(ctx);
    if (!S.paused && (first || !S.timer)) play(ctx, first);
    ctx.ready();
  }

  function render(ctx, first) {
    if (!ctx.data) { if (!S.frames.length) noData(ctx); return; }
    var applied = false;
    if (first) {
      ctx.later(function () {  // slow assets: a card now, the radar as soon as it is loaded
        if (!applied && !S.frames.length) ctx.fallback("Weather radar", "Loading radar images…");
      }, READY_MS);
    }
    load(ctx).then(function () { applied = true; apply(ctx, first); },
      function () { applied = true; apply(ctx, first); });
  }

  AiSignXPlugin.register({
    init: function (ctx) { build(ctx); render(ctx, true); },
    data: function (ctx) { render(ctx, false); },
    show: function (ctx) { if (S.frames.length) play(ctx, true); },
    net: function (ctx) { if (S.frames.length) badges(ctx); else noData(ctx); },
    pause: function (ctx) { S.paused = true; if (S.timer) ctx.clear(S.timer); S.timer = 0; },
    resume: function (ctx) { S.paused = false; play(ctx, false); },
    destroy: function () { S = null; },
  });
})();
