// AiSignX admin: live preview of a plugin item, drawn the way the player draws it.
// Frame plugins run in the same opaque-origin iframe through static/player/plugins/host.js, wired
// to the admin data routes; native plugins use their A.pluginNative[key] player script.
// The page must load host.js and every native player_script/player_style before this file.
//
// Page contract (templates/plugins/preview.html, or the item form):
//   <div data-plugin-preview data-item='{{ entry|tojson }}'>        the stage; entry = a manifest
//                                                                     item whose plugin.data is the
//                                                                     admin /plugins/items/<id>/data
//       optional: data-form="<form id>" data-preview-url="<POST /plugins/<key>/preview>"
//                 data-server-time="<ms>"
//   <button data-preview-aspect="16:9">, <button data-preview-aspect="9:16">   (optional)
//   <button data-preview-refresh>                                               (optional, form)
//   <span data-plugin-preview-status>                                           (optional)
// Classic script, ES2018 at most; no innerHTML; styles through CSSOM only.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var READY_MS = 8000, RESTART_MS = 1500;
  var OBJECT_FIT = { "fit-fit": "contain", "fit-fill": "cover", "fit-stretch": "fill" };
  var FITS = { fit: 1, fill: 1, stretch: 1 };

  function parse(raw) {
    try { var v = JSON.parse(raw || "null"); return v && typeof v === "object" ? v : null; } catch (e) { return null; }
  }
  function fill(s) {
    s.position = "absolute"; s.top = "0"; s.left = "0"; s.width = "100%"; s.height = "100%";
    s.border = "0"; s.display = "block"; s.margin = "0";
  }
  function fitClass(fit) { return "fit-" + (FITS[fit] ? fit : "fit"); }

  function Preview(stage) {
    var statusEl = document.querySelector("[data-plugin-preview-status]");
    var item = parse(stage.getAttribute("data-item"));
    var cur = null, gen = 0, timers = [], restartTimer = null;

    function status(text) { if (statusEl) statusEl.textContent = text; }
    function aspect(a) {
      var s = stage.style, portrait = a === "9:16";
      s.position = "relative"; s.overflow = "hidden"; s.background = "#000";
      s.marginLeft = "auto"; s.marginRight = "auto";
      s.aspectRatio = portrait ? "9 / 16" : "16 / 9";
      s.width = portrait ? "min(100%, calc(70vh * 9 / 16))" : "min(100%, calc(70vh * 16 / 9))";
      var btns = document.querySelectorAll("[data-preview-aspect]");
      for (var i = 0; i < btns.length; i++) {
        var on = btns[i].getAttribute("data-preview-aspect") === (portrait ? "9:16" : "16:9");
        btns[i].classList.toggle("active", on);
        btns[i].setAttribute("aria-pressed", on ? "true" : "false");
      }
    }

    // ---- The render path of render.js, cut down to one slide.
    function attach(slide, media) {
      slide.media = media;
      fill(media.style);
      var m = /(?:^|\s)(fit-\w+)/.exec(media.className || "");
      if (m && OBJECT_FIT[m[1]]) media.style.objectFit = OBJECT_FIT[m[1]];
      slide.el.appendChild(media);
    }
    function prepCard(slide, title, msg) {
      var card = document.createElement("div");
      card.className = "media plugin-card";
      var s = card.style;
      s.display = "flex"; s.alignItems = "center"; s.justifyContent = "center"; s.flexDirection = "column";
      s.background = "#111"; s.color = "#fff"; s.textAlign = "center";
      var t = card.appendChild(document.createElement("p"));
      t.textContent = String(title || "");
      t.style.fontSize = "6vmin"; t.style.margin = "0";
      if (msg) {
        var p = card.appendChild(document.createElement("p"));
        p.textContent = String(msg);
        p.style.opacity = "0.7"; p.style.margin = "1vmin 0 0";
      }
      attach(slide, card);
      card.style.display = "flex";
      return Promise.resolve();
    }
    function prepare(it) {
      var el = document.createElement("div");
      fill(el.style);
      el.style.visibility = "hidden";
      stage.appendChild(el);
      var slide = { el: el, item: it, media: null, dead: false, plugin: null, native: null };
      var p = it.plugin || {};
      var ready;
      try {
        if (p.render === "native") {
          var N = A.pluginNative && A.pluginNative[p.key];
          if (!N || typeof N.prepare !== "function") throw new Error("unsupported plugin " + p.key);
          slide.native = N;
          var api = { attach: attach, fitClass: fitClass, volume: function () { return 1; }, prepCard: prepCard };
          ready = Promise.resolve(N.prepare(slide, api));
        } else {
          if (!A.pluginHost) throw new Error("host.js is not loaded");
          var f = document.createElement("iframe");
          f.className = "media plugin";
          f.setAttribute("sandbox", "allow-scripts");
          f.setAttribute("referrerpolicy", "no-referrer");
          f.setAttribute("allow", "");
          f.setAttribute("title", "Plugin preview");
          f.tabIndex = -1;
          attach(slide, f);
          f.style.background = "#000";
          // persist: false: the admin shares the player's origin, so never touch its saved copies.
          slide.plugin = A.pluginHost.create(f, it, { readyMs: READY_MS, persist: false });
          f.src = it.url;
          ready = slide.plugin.ready;
        }
      } catch (e) {
        ready = Promise.reject(e);
      }
      return { slide: slide, ready: ready };
    }
    function teardown(slide) {
      if (!slide || slide.dead) return;
      slide.dead = true;
      if (slide.plugin) { try { slide.plugin.destroy(); } catch (e) { /* ignore */ } }
      if (slide.native && typeof slide.native.teardown === "function") {
        try { slide.native.teardown(slide); } catch (e) { /* ignore */ }
      }
      if (slide.media && slide.media.tagName === "IFRAME") { try { slide.media.src = "about:blank"; } catch (e) { /* ignore */ } }
      if (slide.el.parentNode) slide.el.parentNode.removeChild(slide.el);
    }
    function clearTimers() { timers.forEach(clearInterval); timers = []; clearTimeout(restartTimer); }

    function run(slide) {
      var g = gen;
      var guard = function (fn) { return function (x, y) { if (g === gen && !slide.dead) fn(x, y); }; };
      var ctx = {
        loop: function () { return true; },  // the preview is a one-item playlist
        done: guard(function () { status("Finished; starting again"); restartTimer = setTimeout(guard(show), RESTART_MS); }),
        fail: guard(function (why) { status("Error: " + why); }),
        note: guard(function (msg) { status(msg); }),
        every: function (fn, ms) { timers.push(setInterval(guard(fn), ms)); },
        degraded: guard(function () { status("Showing a fallback card"); }),
      };
      if (slide.native) {
        var nctx = {};
        Object.keys(ctx).forEach(function (k) { nctx[k] = ctx[k]; });
        nctx.done = function () { if (slide.item.plugin.ends_itself) ctx.done(); };
        if (typeof slide.native.run === "function") {
          try { slide.native.run(slide, nctx); } catch (e) { ctx.fail(e && e.message); }
        }
        return;
      }
      var h = slide.plugin;
      h.on("complete", function () { if (slide.item.plugin.ends_itself) ctx.done(); });
      h.on("error", function (msg, fatal) { (fatal ? ctx.fail : ctx.note)(msg); });
      h.on("degraded", ctx.degraded);
      if (h.degraded()) ctx.degraded();
      h.show();
    }
    function show() {
      gen++;
      clearTimers();
      teardown(cur);
      cur = null;
      if (!item || !item.plugin || typeof item.url !== "string") { status("Nothing to preview"); return; }
      status("Loading…");
      var g = gen, p = prepare(item);
      cur = p.slide;
      p.ready.then(function () {
        if (g !== gen) return;
        p.slide.el.style.visibility = "visible";
        status("Showing");
        run(p.slide);
      }, function (err) {
        if (g !== gen) return;
        status("Could not show the plugin: " + ((err && err.message) || err));
      });
    }

    // ---- Unsaved form: validate it on the server, then show the result.
    function refresh() {
      var form = document.getElementById(stage.getAttribute("data-form") || "");
      var url = stage.getAttribute("data-preview-url");
      if (!form || !url) { show(); return; }
      status("Checking the form…");
      fetch(url, { method: "POST", body: new FormData(form), credentials: "same-origin",
        headers: { Accept: "application/json" } }).then(function (res) {
        return res.json().then(function (d) { return { ok: res.ok, d: d || {} }; });
      }).then(function (r) {
        if (!r.ok) { status(r.d.error === "invalid" || r.d.errors ? "Fix the highlighted fields first" : "Preview failed"); return; }
        var next = r.d.item && typeof r.d.item === "object" ? r.d.item : JSON.parse(JSON.stringify(item || {}));
        if (!r.d.item) {
          next.plugin = next.plugin || {};
          if (r.d.config) next.plugin.config = r.d.config;
          if ("data_url" in r.d) next.plugin.data = r.d.data_url;
          if (r.d.url) next.url = r.d.url;
          if (r.d.plugin && typeof r.d.plugin === "object") Object.keys(r.d.plugin).forEach(function (k) { next.plugin[k] = r.d.plugin[k]; });
        }
        item = next;
        show();
      }, function () { status("Preview failed: the server did not answer"); });
    }

    if (A.pluginHost) A.pluginHost.setServerTime(Number(stage.getAttribute("data-server-time")) || 0);
    var aspects = document.querySelectorAll("[data-preview-aspect]");
    for (var i = 0; i < aspects.length; i++) {
      aspects[i].addEventListener("click", function (e) {
        aspect(e.currentTarget.getAttribute("data-preview-aspect"));
        show();  // re-render so plugins lay out for the new shape
      });
    }
    var again = document.querySelectorAll("[data-preview-refresh]");
    for (var j = 0; j < again.length; j++) again[j].addEventListener("click", function (e) { e.preventDefault(); refresh(); });
    aspect(stage.getAttribute("data-aspect") === "9:16" ? "9:16" : "16:9");
    show();
    return { show: show, refresh: refresh };
  }

  var stages = document.querySelectorAll("[data-plugin-preview]");
  for (var k = 0; k < stages.length; k++) Preview(stages[k]);
})();
