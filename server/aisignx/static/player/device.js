// AiSignX browser player: per-display settings (rotation, volume, input lock, offline banner),
// identify overlay, screenshots, diagnostics and the error ring.
// Classic script, ES2018 at most. Text via textContent only; no inline styles (strict CSP).
// The page never reads settings.unlock: the unlock PIN is for the native apps only.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var net = A.net, R = A.render, EM = A.emergency;
  var body = document.body;
  var shell = net.shell();
  var BUILD = body.getAttribute("data-build") || "";
  var STARTED = Date.now();

  var ORIENTATIONS = [0, 90, 180, 270];
  // A settings object with a bad field falls back to these.
  var DEFAULTS = { orientation: 0, volume: 100, show_offline_banner: true, allow_input: false };
  // No settings key at all (older server or cached manifest): keep the old behaviour.
  var LEGACY = { orientation: 0, volume: 100, show_offline_banner: true, allow_input: true };
  var BLOCKED = ["keydown", "keyup", "keypress", "contextmenu", "wheel", "dragstart"];
  var MAX_SIDE = 1920, JPEG_QUALITY = 0.8, MAX_ERRORS = 10, DIAG_MAX = 4000;
  var RID = /^[0-9a-f]{16}$/;

  var cfg = { orientation: 0, volume: 100, show_offline_banner: true, allow_input: true };
  var ctx = { manifest: function () { return null; } };
  var errors = [];
  var idTimer = null, shotRid = null;
  var cssRot = 0;  // degrees the page itself turns the output clockwise (body.rot-*)

  var offlineEl = document.getElementById("offline");
  var idEl = document.getElementById("identify");
  var idName = idEl && idEl.querySelector(".id-name");
  var idSub = idEl && idEl.querySelector(".id-sub");

  function hasCap(c) { return !!(shell && shell.info.caps && shell.info.caps.indexOf(c) >= 0); }
  function isBool(v) { return typeof v === "boolean"; }
  function count(v) { return typeof v === "number" && isFinite(v) && v >= 0 ? Math.round(v) : null; }

  // ---- Settings.
  function applySettings(s) {
    var legacy = !s || typeof s !== "object";
    var d = legacy ? LEGACY : DEFAULTS;
    s = legacy ? {} : s;
    cfg = {
      orientation: ORIENTATIONS.indexOf(s.orientation) >= 0 ? s.orientation : d.orientation,
      volume: typeof s.volume === "number" && s.volume >= 0 && s.volume <= 100 ? Math.round(s.volume) : d.volume,
      show_offline_banner: isBool(s.show_offline_banner) ? s.show_offline_banner : d.show_offline_banner,
      allow_input: isBool(s.allow_input) ? s.allow_input : d.allow_input,
    };
    var rot = hasCap("rotate") ? 0 : cfg.orientation;  // the shell rotates the device itself
    cssRot = rot;
    [90, 180, 270].forEach(function (deg) { body.classList.toggle("rot-" + deg, rot === deg); });
    R.setVolume(cfg.volume);
    body.classList.toggle("no-input", !cfg.allow_input);
    body.classList.toggle("input-on", cfg.allow_input);
  }

  // Input lock: keys, the context menu, the wheel and drags do nothing. Clicks and touches
  // never reach web items because the CSS turns their pointer events off (body.no-input).
  function block(ev) { if (body.classList.contains("no-input")) ev.preventDefault(); }
  BLOCKED.forEach(function (type) { window.addEventListener(type, block, { capture: true, passive: false }); });

  // ---- Identify overlay.
  function identify(seconds) {
    if (!idEl) return;
    var n = Number(seconds);
    if (!(n > 0)) n = 15;
    n = Math.max(3, Math.min(60, n));
    var m = ctx.manifest(), d = m && m.display;
    idName.textContent = d && typeof d.name === "string" && d.name ? d.name : "AiSignX";
    var sub = [];
    if (d && d.id != null) sub.push("#" + d.id);
    if (shell && shell.info.device) sub.push(shell.info.device);
    idSub.textContent = sub.join(" · ");
    idEl.hidden = false;
    clearTimeout(idTimer);
    idTimer = setTimeout(function () { idEl.hidden = true; }, n * 1000);
  }

  // ---- Errors ring (sent with the diagnostics).
  function noteError(msg) {
    if (msg == null || msg === "") return;
    errors.push({ t: Date.now(), msg: String(msg).slice(0, 200) });
    if (errors.length > MAX_ERRORS) errors.splice(0, errors.length - MAX_ERRORS);
  }
  window.addEventListener("error", function (ev) {
    if (ev && ev.message) noteError(ev.message + (ev.filename ? " (" + ev.filename.split("/").pop() + ":" + ev.lineno + ")" : ""));
  });
  window.addEventListener("unhandledrejection", function (ev) {
    var r = ev && ev.reason;
    noteError("unhandled rejection: " + (r && r.message ? r.message : String(r)));
  });

  // ---- Screenshots. The shell captures the real screen when it can; otherwise the page
  // draws its own media on a canvas (same-origin media, so the canvas is not tainted).
  function wrapLines(g, text, maxW) {
    var out = [];
    String(text).split("\n").forEach(function (para) {
      var line = "";
      para.split(/\s+/).forEach(function (word) {
        var test = line ? line + " " + word : word;
        if (line && g.measureText(test).width > maxW) { out.push(line); line = word; } else line = test;
      });
      out.push(line);
    });
    return out;
  }
  // blocks: [{text, size, bold}]; drawn centred in the box, cut off at its bottom.
  function drawText(g, blocks, x, y, w, h, color) {
    var lines = [];
    blocks.forEach(function (b) {
      if (!b.text) return;
      g.font = (b.bold ? "bold " : "") + b.size + "px system-ui, Segoe UI, Roboto, Arial, sans-serif";
      wrapLines(g, b.text, w * 0.9).forEach(function (l) { lines.push({ text: l, font: g.font, size: b.size }); });
    });
    var total = lines.reduce(function (s, l) { return s + l.size * 1.3; }, 0);
    var cy = y + Math.max(0, (h - total) / 2);
    g.fillStyle = color;
    g.textAlign = "center";
    g.textBaseline = "top";
    lines.forEach(function (l) {
      if (cy + l.size > y + h) return;
      g.font = l.font;
      g.fillText(l.text, x + w / 2, cy);
      cy += l.size * 1.3;
    });
  }
  function drawMedia(g, m, item, cw, ch) {
    if (m.tagName === "IFRAME") {
      var host = "";
      try { host = new URL(m.src, location.href).host; } catch (e) { host = ""; }
      g.fillStyle = "#444";
      g.fillRect(0, 0, cw, ch);
      drawText(g, [{ text: "Web page: " + host, size: Math.round(ch / 18), bold: true }], 0, 0, cw, ch, "#fff");
      return;
    }
    var nw = m.tagName === "VIDEO" ? m.videoWidth : m.naturalWidth;
    var nh = m.tagName === "VIDEO" ? m.videoHeight : m.naturalHeight;
    if (!nw || !nh) return;
    var fit = item && item.fit, dw = cw, dh = ch;
    if (fit !== "stretch") {
      var k = fit === "fill" ? Math.max(cw / nw, ch / nh) : Math.min(cw / nw, ch / nh);
      dw = nw * k; dh = nh * k;
    }
    g.drawImage(m, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
  }
  function textOf(root, sel) {
    var e = root.querySelector(sel);
    return e && !e.hidden ? e.textContent : "";
  }
  function drawOverlays(g, cw, ch) {
    var scr = document.getElementById("screen");
    if (scr && !scr.hidden) {  // pairing, connecting, no content
      g.fillStyle = "#000";
      g.fillRect(0, 0, cw, ch);
      var texts = [{ text: textOf(scr, ".screen-title"), size: Math.round(ch / 16), bold: true }];
      Array.prototype.forEach.call(scr.querySelectorAll(".screen-text, .pair-code, .screen-sub"), function (e) {
        texts.push({ text: e.textContent, size: Math.round(ch / (e.className === "pair-code" ? 8 : 28)), bold: e.className === "pair-code" });
      });
      drawText(g, texts, 0, 0, cw, ch, "#fff");
    }
    var em = document.getElementById("emergency");
    if (em && !em.hidden) {
      var cs = window.getComputedStyle(em);
      var full = em.classList.contains("layout-fullscreen");
      var bh = full ? ch : Math.round(ch * 0.25), by = ch - bh;
      g.fillStyle = cs.backgroundColor || "#b71c1c";
      g.fillRect(0, by, cw, bh);
      drawText(g, [
        { text: textOf(em, ".em-title"), size: Math.round(full ? ch / 12 : bh / 5), bold: true },
        { text: textOf(em, ".em-msg"), size: Math.round(full ? ch / 22 : bh / 8), bold: false },
      ], 0, by, cw, bh, cs.color || "#fff");
    }
  }
  function canvasCapture() {
    return new Promise(function (resolve) {
      var sideways = body.classList.contains("rot-90") || body.classList.contains("rot-270");
      var dpr = window.devicePixelRatio || 1;
      // The page's own (upright) layout: rotated screens are drawn as their viewers see them.
      var w = (sideways ? window.innerHeight : window.innerWidth) * dpr;
      var h = (sideways ? window.innerWidth : window.innerHeight) * dpr;
      var k = Math.min(1, MAX_SIDE / Math.max(w, h, 1));
      var c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(w * k));
      c.height = Math.max(1, Math.round(h * k));
      var g = c.getContext("2d");
      if (!g) { resolve(null); return; }
      g.fillStyle = "#000";
      g.fillRect(0, 0, c.width, c.height);
      var cur = null;
      try { cur = EM.current(); } catch (e) { cur = null; }
      if (cur && cur.slide && cur.slide.media) drawMedia(g, cur.slide.media, cur.item, c.width, c.height);
      drawOverlays(g, c.width, c.height);
      c.toBlob(function (b) { resolve(b); }, "image/jpeg", JPEG_QUALITY);  // throws if tainted
    }).then(null, function (e) {
      noteError("screenshot: " + (e && e.message ? e.message : e));
      return null;
    });
  }
  // A shell grabs the raw window, which the CSS rotation has turned clockwise by `deg`:
  // turn it back so the admin sees the screen upright, like the canvas capture draws it.
  // createImageBitmap, because the player CSP has no blob: in img-src. Keeps the raw blob
  // if the image cannot be decoded or redrawn.
  function unrotate(blob, deg) {
    if (!blob || !deg || typeof window.createImageBitmap !== "function") return Promise.resolve(blob);
    return window.createImageBitmap(blob).then(function (img) {
      var sideways = deg === 90 || deg === 270;
      var c = document.createElement("canvas");
      c.width = sideways ? img.height : img.width;
      c.height = sideways ? img.width : img.height;
      var g = c.getContext("2d");
      if (!g) return blob;
      g.translate(c.width / 2, c.height / 2);
      g.rotate(-deg * Math.PI / 180);
      g.drawImage(img, -img.width / 2, -img.height / 2);
      if (img.close) img.close();
      return new Promise(function (resolve) {
        c.toBlob(function (b) { resolve(b || blob); }, "image/jpeg", JPEG_QUALITY);
      });
    }).then(null, function () { return blob; });
  }
  function capture() {
    var native = hasCap("capture") ? shell.capture() : Promise.resolve(null);
    var deg = cssRot;
    return native.then(function (blob) {
      return blob ? unrotate(blob, deg) : canvasCapture();
    }, canvasCapture);
  }
  function screenshot(rid) {
    // The same request can arrive twice (it is re-sent when the event stream reconnects).
    if (typeof rid !== "string" || !RID.test(rid) || rid === shotRid) return;
    shotRid = rid;
    capture().then(function (blob) {
      if (!blob) { shotRid = null; noteError("screenshot: capture failed"); return null; }
      return net.upload("/screenshot", blob, { "X-Screenshot-Request": rid }).then(function (res) {
        if (res.kind === "ok") return;
        if (!res.status || res.status >= 500) shotRid = null;  // let a re-sent request try again
        noteError("screenshot upload: " + ((res.data && res.data.error) || res.status || "network error"));
      });
    }).then(null, function (e) { shotRid = null; noteError("screenshot: " + (e && e.message ? e.message : e)); });
  }

  // ---- Diagnostics: kept small, because a heartbeat over 8 KB fails (and shows "offline").
  function byteLength(s) {
    if (typeof TextEncoder === "function") return new TextEncoder().encode(s).length;
    return encodeURIComponent(s).replace(/%[0-9A-F]{2}/g, "x").length;
  }
  function diag() {
    var dpr = window.devicePixelRatio || 1;
    var d = {
      ua: (navigator.userAgent || "").slice(0, 300),
      build: BUILD.slice(0, 40),
      page_uptime_s: Math.round((Date.now() - STARTED) / 1000),
      screen: { w: Math.round(window.screen.width * dpr), h: Math.round(window.screen.height * dpr), dpr: dpr },
      viewport: { w: window.innerWidth, h: window.innerHeight },
    };
    if (shell) {
      d.shell = { name: shell.info.name, version: shell.info.version, caps: shell.info.caps.slice(0, 8) };
      var st = shell.stats();
      if (st) {
        if (count(st.uptime_s) !== null) d.shell.uptime_s = count(st.uptime_s);
        if (st.cache && typeof st.cache === "object") {
          d.cache = { files: count(st.cache.files) || 0, bytes: count(st.cache.bytes) || 0 };
        }
        if (count(st.storage_free_bytes) !== null) d.storage_free_bytes = count(st.storage_free_bytes);
      }
    }
    if (errors.length) d.errors = errors.slice();
    while (d.errors && d.errors.length && byteLength(JSON.stringify(d)) > DIAG_MAX) d.errors.shift();
    if (d.errors && !d.errors.length) delete d.errors;
    return byteLength(JSON.stringify(d)) > DIAG_MAX ? null : d;
  }

  // ---- Offline banner: only while playing (or showing "no content") and cut off for a while.
  function checkOffline(hb, events, state) {
    if (!offlineEl) return;
    var show = cfg.show_offline_banner && (state === "PLAYING" || state === "NO_CONTENT") &&
      events.status !== "live" && Date.now() - (hb.lastOkAt || STARTED) > 2.5 * hb.seconds * 1000;
    if (offlineEl.hidden === show) offlineEl.hidden = !show;
  }

  // command(d): the "identify" and "screenshot" admin commands.
  function command(d) {
    if (!d || typeof d !== "object") return;
    if (d.action === "identify") identify(d.seconds);
    else if (d.action === "screenshot") screenshot(d.request_id);
  }

  // init({manifest: () => current manifest or null})
  function init(c) { if (c && typeof c.manifest === "function") ctx.manifest = c.manifest; }

  A.device = {
    init: init,
    applySettings: applySettings,
    command: command,
    identify: identify,
    capture: capture,
    unrotate: unrotate,
    screenshot: screenshot,
    noteError: noteError,
    diag: diag,
    checkOffline: checkOffline,
  };
})();
