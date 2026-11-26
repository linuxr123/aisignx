// AiSignX browser player: player controls (input recognition only; player.js owns playback).
// Classic script, ES2018 at most. Text via textContent only; no inline style attributes (CSSOM only).
// Keyboard, mouse, touch, Media Session and shell keys all become one of seven actions:
// toggle, play, pause, forward, back, next, prev (docs/player-api.md, "Player controls").
// Nothing happens unless the display allows input, the player is PLAYING and no emergency is up.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var doc = typeof document !== "undefined" ? document : null;
  function byId(id) { return doc && typeof doc.getElementById === "function" ? doc.getElementById(id) : null; }

  var ACTIONS = { toggle: 1, play: 1, pause: 1, forward: 1, back: 1, next: 1, prev: 1 };
  var KEYS = {
    " ": "toggle", Spacebar: "toggle", Enter: "toggle",
    ArrowRight: "forward", ArrowLeft: "back", ArrowUp: "prev", ArrowDown: "next",
    PageUp: "prev", PageDown: "next",
    MediaPlayPause: "toggle", MediaPlay: "play", MediaPause: "pause", MediaStop: "pause",
    MediaTrackNext: "next", MediaTrackPrevious: "prev", MediaFastForward: "forward", MediaRewind: "back",
    ChannelUp: "prev", ChannelDown: "next",
  };
  var SESSION = { play: "play", pause: "pause", nexttrack: "next", previoustrack: "prev",
    seekforward: "forward", seekbackward: "back" };
  var TAP_MS = 300, TAP_PX = 10, SWIPE_PX = 50, HOLD_MS = 300, CLICK_DELAY_MS = 250;
  var WHEEL_MS = 300, REPEAT_MS = 250, DEDUPE_MS = 150, HINT_MS = 800, HINT_FADE_MS = 250;

  var pb = null;                                   // A.playback (player.js)
  var rotation = function () { return 0; };        // degrees the page turns its output (device.js)
  var enabled = false, state = "", started = false;
  var lastStepAt = -1e9, last = null, lastWheelAt = -1e9, lastPointer = "";
  var g = null;                                    // the gesture in progress
  var clickTimer = null, hintTimer = null, fadeTimer = null, pausedTimer = null, cursorTimer = null;
  var CURSOR_HIDE_MS = 2000;

  var overlay = byId("controls"), pausedEl = byId("paused"), hintEl = byId("hint");
  var pSub = pausedEl && typeof pausedEl.querySelector === "function" ? pausedEl.querySelector(".p-sub") : null;
  var hintText = null, hintFill = null;

  function now() { return window.performance && performance.now ? performance.now() : Date.now(); }
  function group(name) { return name === "toggle" || name === "play" || name === "pause" ? "pp" : name; }
  function mmss(s) {
    s = Math.max(0, Math.round(s));
    var m = Math.floor(s / 60), r = s % 60;
    return m + ":" + (r < 10 ? "0" : "") + r;
  }

  // ---- Actions.
  // action(name, source, repeat) -> true when it was carried out.
  function action(name, source, repeat) {
    if (!enabled || !pb || ACTIONS[name] !== 1 || !pb.enabled()) return false;
    var t = now(), step = name === "forward" || name === "back";
    if (repeat && (!step || t - lastStepAt < REPEAT_MS)) return false;  // held keys: only seeks repeat
    // A media key can arrive twice (keydown and Media Session): drop the echo from the other source.
    if (last && last.group === group(name) && last.source !== source && t - last.at < DEDUPE_MS) return false;
    last = { group: group(name), source: source, at: t };
    if (step) lastStepAt = t;
    var r = null;
    try { r = pb[name](); } catch (e) { if (window.console) console.error(e); return false; }
    feedback(name, r);
    return true;
  }
  function feedback(name, r) {
    if (r === "paused") hint("Paused");
    else if (r === "playing") hint("Playing");
    else if (r === "next" || (name === "next" && r === true)) hint("Next", name === "next" ? "" : "right");
    else if (r === "prev" || (name === "prev" && r === true)) hint("Previous", name === "prev" ? "" : "left");
    else if (typeof r === "number") {
      var n = Math.round(Math.abs(r));
      hint((name === "forward" ? "Fast forward +" : "Rewind −") + n + " s", name === "forward" ? "right" : "left");
    }
  }

  // ---- Feedback: one hint at a time, and the "Paused" pill.
  function ensureHintParts() {
    if (!hintEl || hintText || !doc || typeof doc.createElement !== "function") return;
    hintText = doc.createElement("span");
    hintText.className = "h-text";
    var bar = doc.createElement("span");
    bar.className = "h-bar";
    hintFill = doc.createElement("span");
    hintFill.className = "h-fill";
    bar.appendChild(hintFill);
    hintEl.appendChild(hintText);
    hintEl.appendChild(bar);
  }
  // hint(text, side, frac): side "left"/"right" places a seek hint; frac (0..1) shows the scrub bar.
  function hint(text, side, frac) {
    if (!hintEl) return;
    ensureHintParts();
    if (hintText) hintText.textContent = text; else hintEl.textContent = text;
    var scrub = typeof frac === "number";
    if (scrub && hintFill && hintFill.style) hintFill.style.width = Math.round(Math.max(0, Math.min(1, frac)) * 1000) / 10 + "%";
    hintEl.className = "show" + (side ? " hint-" + side : "") + (scrub ? " scrub" : "");
    hintEl.hidden = false;
    clearTimeout(hintTimer); clearTimeout(fadeTimer);
    hintTimer = setTimeout(function () {
      hintEl.className = hintEl.className.replace("show", "").trim();
      fadeTimer = setTimeout(function () { hintEl.hidden = true; }, HINT_FADE_MS);
    }, HINT_MS);
  }
  function showPaused(resumeAt) {
    if (!pausedEl) return;
    var update = function () {
      if (pSub) pSub.textContent = "resumes in " + mmss((resumeAt - now()) / 1000);
    };
    clearInterval(pausedTimer);
    pausedTimer = setInterval(update, 1000);
    update();
    pausedEl.hidden = false;
  }
  function hidePaused() {
    clearInterval(pausedTimer);
    pausedTimer = null;
    if (pausedEl) pausedEl.hidden = true;
  }

  // ---- Geometry: pointer coordinates are physical; gestures are judged in the page's upright frame.
  function upright(dx, dy) {
    var r = ((Number(rotation()) || 0) % 360 + 360) % 360;
    if (r === 90) return { x: dy, y: -dx };
    if (r === 180) return { x: -dx, y: -dy };
    if (r === 270) return { x: -dy, y: dx };
    return { x: dx, y: dy };
  }
  function uprightWidth() {
    var r = ((Number(rotation()) || 0) % 360 + 360) % 360;
    return (r === 90 || r === 270 ? window.innerHeight : window.innerWidth) || 1;
  }
  // side of a point: "right" or "left" of the upright centre.
  function sideOf(x, y) {
    var u = upright(x - (window.innerWidth || 0) / 2, y - (window.innerHeight || 0) / 2);
    return u.x >= 0 ? "right" : "left";
  }

  // ---- Keyboard.
  function onKey(ev) {
    if (!enabled || !doc || !doc.body.classList.contains("input-on")) return;
    if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
    var name = KEYS[ev.key];
    if (!name) return;
    ev.preventDefault();
    action(name, "key", !!ev.repeat);
  }

  // ---- Pointer gestures on the #controls overlay.
  function cancelGesture() {
    if (g && g.scrub && pb) { try { pb.scrubEnd(); } catch (e) { /* ignore */ } }
    g = null;
  }
  function onDown(ev) {
    lastPointer = ev.pointerType || "mouse";
    if (!enabled || (ev.pointerType === "mouse" && ev.button !== 0)) return;
    cancelGesture();
    g = { id: ev.pointerId, touch: ev.pointerType === "touch" || ev.pointerType === "pen",
      x0: ev.clientX, y0: ev.clientY, t0: now(), mode: null, scrub: false };
    try { if (overlay && overlay.setPointerCapture) overlay.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
  }
  function onMove(ev) {
    if (!g || ev.pointerId !== g.id) return;
    var dx = ev.clientX - g.x0, dy = ev.clientY - g.y0;
    if (g.mode === null) {
      if (Math.abs(dx) < TAP_PX && Math.abs(dy) < TAP_PX) return;
      // Touch: moving before the hold time is a swipe; press-hold first and then drag scrubs a
      // video. Mouse: press and drag scrubs at once (a plain click still pauses).
      g.mode = !g.touch || now() - g.t0 >= HOLD_MS ? "drag" : "swipe";
      if (g.mode === "drag" && enabled && pb && pb.enabled() && pb.kind() === "video") g.scrub = !!pb.scrubBegin();
    }
    if (!g.scrub) return;
    var r = pb.scrubTo(upright(dx, dy).x / uprightWidth());
    if (r && r.duration > 0) hint(mmss(r.t) + " / " + mmss(r.duration), "", r.t / r.duration);
  }
  function onUp(ev) {
    if (!g || ev.pointerId !== g.id) return;
    var gs = g, dt = now() - gs.t0;
    g = null;
    var dx = ev.clientX - gs.x0, dy = ev.clientY - gs.y0;
    if (gs.scrub) { try { pb.scrubEnd(); } catch (e) { /* ignore */ } return; }
    if (gs.mode === null) {
      if (dt >= TAP_MS) return;  // a long press without a drag does nothing
      if (gs.touch) { action("toggle", "pointer", false); return; }
      clearTimeout(clickTimer);  // a mouse click waits: a double click seeks instead
      clickTimer = setTimeout(function () { clickTimer = null; action("toggle", "pointer", false); }, CLICK_DELAY_MS);
      return;
    }
    if (!gs.touch) return;
    var u = upright(dx, dy);
    if (Math.abs(u.x) >= SWIPE_PX && Math.abs(u.x) > 1.5 * Math.abs(u.y)) action(u.x < 0 ? "next" : "prev", "pointer", false);
  }
  function onCancel(ev) { if (g && ev.pointerId === g.id) cancelGesture(); }
  function onDblClick(ev) {
    if (lastPointer !== "mouse" || !enabled) return;
    clearTimeout(clickTimer);
    clickTimer = null;
    ev.preventDefault();
    action(sideOf(ev.clientX, ev.clientY) === "right" ? "forward" : "back", "pointer", false);
  }
  function onWheel(ev) {
    if (!enabled) return;
    ev.preventDefault();
    var t = now();
    if (!ev.deltaY || t - lastWheelAt < WHEEL_MS) return;
    lastWheelAt = t;
    action(ev.deltaY > 0 ? "forward" : "back", "wheel", false);
  }
  // Mouse side buttons: 3 = back, 4 = forward. The browser must never go back in its history.
  function onMouseUp(ev) {
    if (!enabled || (ev.button !== 3 && ev.button !== 4)) return;
    ev.preventDefault();
    action(ev.button === 3 ? "prev" : "next", "pointer", false);
  }
  function onAux(ev) { if (enabled && (ev.button === 3 || ev.button === 4)) ev.preventDefault(); }

  // The mouse cursor shows while the mouse moves and hides CURSOR_HIDE_MS after it stops (player.css, mouse-active).
  function onMouseMove() {
    if (!enabled || !doc || !doc.body) return;
    doc.body.classList.add("mouse-active");
    clearTimeout(cursorTimer);
    cursorTimer = setTimeout(hideCursor, CURSOR_HIDE_MS);
  }
  function hideCursor() {
    clearTimeout(cursorTimer);
    cursorTimer = null;
    if (doc && doc.body) doc.body.classList.remove("mouse-active");
  }

  // ---- Media Session (media keys and OS media controls in Chrome).
  function mediaSession(on) {
    var ms = typeof navigator !== "undefined" && navigator ? navigator.mediaSession : null;
    if (!ms || typeof ms.setActionHandler !== "function") return;
    Object.keys(SESSION).forEach(function (k) {
      try { ms.setActionHandler(k, on ? function () { action(SESSION[k], "session", false); } : null); } catch (e) { /* unsupported */ }
    });
  }

  function syncOverlay() {
    var show = enabled && state === "PLAYING";
    if (!show) { cancelGesture(); clearTimeout(clickTimer); clickTimer = null; }
    if (overlay && overlay.hidden === show) overlay.hidden = !show;
  }

  // configure({allow_input, resume_after_s}): the display's settings (device.js config()).
  function configure(cfg) {
    var on = !!(cfg && cfg.allow_input);
    if (on !== enabled) mediaSession(on);
    enabled = on;
    if (!on) hideCursor();
    syncOverlay();
  }
  function onState(s) { state = s; syncOverlay(); }

  // init({playback, rotation, shell}): wires listeners once.
  function init(o) {
    o = o || {};
    if (o.playback) pb = o.playback;
    if (typeof o.rotation === "function") rotation = o.rotation;
    if (started) return;
    started = true;
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("mouseup", onMouseUp, true);
    window.addEventListener("auxclick", onAux, true);
    window.addEventListener("mousemove", onMouseMove, true);
    if (overlay) {
      overlay.addEventListener("pointerdown", onDown);
      overlay.addEventListener("pointermove", onMove);
      overlay.addEventListener("pointerup", onUp);
      overlay.addEventListener("pointercancel", onCancel);
      overlay.addEventListener("dblclick", onDblClick);
      overlay.addEventListener("wheel", onWheel, { passive: false });
      overlay.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });
    }
    if (o.shell && typeof o.shell.onControl === "function") {
      o.shell.onControl(function (a, repeat) { action(a, "shell", !!repeat); });
    }
  }

  A.controls = {
    init: init,
    configure: configure,
    onState: onState,
    action: action,
    hint: hint,
    showPaused: showPaused,
    hidePaused: hidePaused,
  };
})();
