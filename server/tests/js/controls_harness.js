// Runs static/player/controls.js + player.js in a Node vm with a fake DOM, a fake clock and
// stubbed net/render/emergency/device/pop modules, plus render.js's seek helpers, and prints
// JSON results for tests/test_player_controls.py.
// Usage: node controls_harness.js <static/player directory>.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const DIR = process.argv[2];
const read = (f) => fs.readFileSync(path.join(DIR, f), "utf-8");
const CONTROLS = read("controls.js"), PLAYER = read("player.js"), RENDER = read("render.js");
const quiet = { log() {}, info() {}, warn() {}, error() {}, debug() {} };  // stdout is the JSON result
const flush = () => new Promise((r) => setImmediate(r));

function element(id) {
  const classes = new Set();
  const e = {
    id, hidden: id === "controls" || id === "paused" || id === "hint", className: "", textContent: "",
    firstChild: null, children: [], listeners: {}, style: {},
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
    },
    getAttribute() { return ""; }, setAttribute() {},
    appendChild(c) { e.children.push(c); return c; }, removeChild() {},
    addEventListener(t, fn) { (e.listeners[t] = e.listeners[t] || []).push(fn); },
    querySelector(sel) { return sel === ".p-sub" ? e.sub : null; },
    setPointerCapture() {},
  };
  return e;
}

// items: manifest items; opts: {allow_input, resume_after_s, rotation, shell}
async function make(items, opts) {
  opts = opts || {};
  const clock = { t: 1000 };
  const timers = [];
  let seq = 0;
  const addTimer = (fn, ms, repeat) => { const id = ++seq; timers.push({ id, fn, at: clock.t + (ms || 0), ms: ms || 0, repeat }); return id; };
  const clearTimer = (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); };
  async function advance(ms) {
    const end = clock.t + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const t = timers[0];
      if (!t || t.at > end) break;
      clock.t = Math.max(clock.t, t.at);
      if (t.repeat) t.at += Math.max(1, t.ms); else timers.shift();
      t.fn();
      await flush();
    }
    clock.t = end;
    await flush();
  }

  const els = {};
  ["stage", "screen", "controls", "paused", "hint"].forEach((id) => { els[id] = element(id); });
  els.paused.sub = element("p-sub");
  const body = element("body");
  const winListeners = {};
  const calls = { pop: [], swaps: [], commands: [], hints: [] };
  const handlers = {};
  const cfg = { allow_input: opts.allow_input !== false, resume_after_s: opts.resume_after_s || 300 };

  // Fake video media.
  const videos = [];
  function video(dur) {
    const v = { tagName: "VIDEO", currentTime: 0, duration: dur, paused: true, muted: false,
      play() { v.paused = false; return Promise.resolve(); }, pause() { v.paused = true; } };
    videos.push(v);
    return v;
  }
  let active = null;
  const isVideo = (s) => !!(s && s.media && s.media.tagName === "VIDEO");
  const R = {
    init() {}, clear() { active = null; }, teardown() {}, resume() {},
    prepare(item) {
      if (opts.failIds && opts.failIds.includes(item.id) && opts.failNow && opts.failNow()) {
        const bad = Promise.reject(new Error("offline"));
        bad.slide = null;
        return bad;
      }
      const slide = { item, media: item.type === "video" ? video(item.media_duration_ms / 1000) : { tagName: "IMG" } };
      const p = Promise.resolve(slide);
      p.slide = slide;
      return p;
    },
    swap(slide) { active = slide; calls.swaps.push(slide.item.id); },
    runVideo(slide) { slide.media.play(); },
    isVideo,
    videoOf: (s) => (isVideo(s) ? s.media : null),
    setVideoTime(s, t) { const v = s.media; v.currentTime = Math.max(0, Math.min(v.duration - 0.25, t)); return v.currentTime; },
    seekVideo(s, d) {
      const v = s.media, want = v.currentTime + d;
      if (d > 0 && want > v.duration - 0.25) return { applied: 0, atEnd: true };
      const from = v.currentTime;
      v.currentTime = Math.max(0, want);
      return { applied: v.currentTime - from, atEnd: false };
    },
    pauseVideo(s) { if (isVideo(s)) s.media.pause(); },
    playVideo(s) { if (isVideo(s)) s.media.play(); },
  };
  const EM = { shownId: null, layout: null, apply() {}, sync() {}, clearIf() {},
    show(d) { EM.shownId = d.id; EM.layout = d.layout; }, hide() { EM.shownId = null; }, current: () => null };
  const D = {
    init() {}, noteError() {}, checkOffline() {}, command() {}, diag: () => null,
    applySettings() { body.classList.toggle("input-on", cfg.allow_input); body.classList.toggle("no-input", !cfg.allow_input); },
    config: () => Object.assign({}, cfg),
    rotation: () => opts.rotation || 0,
  };
  const shell = opts.shell ? {
    info: { name: "electron", version: "3", caps: ["controls", "kiosk-lock"] },
    onState() {}, onManifest() {},
    onCommand(o) { calls.commands.push(o); },
    onControl(fn) { shell.control = fn; },
  } : null;
  const manifest = { version: "v1", items, settings: {} };
  const net = {
    shell: () => shell, clientId: () => "c",
    store: { KEYS: { manifest: "m", pairing: "p" }, local: { get: () => null, set() {}, remove() {} },
      session: { get: () => null, set() {}, remove() {} } },
    Backoff: () => ({ next: () => 1000, reset() {} }),
    Events: (h) => { Object.assign(handlers, h); return { start() {}, stop() {}, status: "live" }; },
    Heartbeat: () => ({ start() {}, stop() {}, now() {}, setSeconds() {} }),
    api: () => Promise.resolve({ kind: "ok", data: manifest }),
  };
  const POP = {};
  ["begin", "end", "pause", "resume"].forEach((k) => { POP[k] = (a) => calls.pop.push(k === "begin" ? "begin:" + a.id : k); });
  const document = {
    body, createElement: () => element(""), createTextNode: () => element(""),
    getElementById: (id) => els[id] || null, addEventListener() {},
  };
  const window = {
    AiSignX: { net, render: R, emergency: EM, device: D }, AiSignXPop: POP,
    addEventListener(t, fn) { (winListeners[t] = winListeners[t] || []).push(fn); },
    innerWidth: 1000, innerHeight: 500, console: quiet,
  };
  const ctx = {
    window, document, console: quiet, navigator: { userAgent: "" },
    location: { search: opts.debug ? "?debug=1" : "", origin: "http://x", host: "x", pathname: "/player/" },
    performance: { now: () => clock.t },
    setTimeout: (fn, ms) => addTimer(fn, ms, false), clearTimeout: clearTimer,
    setInterval: (fn, ms) => addTimer(fn, ms, true), clearInterval: clearTimer,
    Promise, JSON, Object, Array, Math, Number, RegExp, isFinite, String,
  };
  ctx.Date = { now: () => clock.t };
  window.performance = ctx.performance;
  vm.createContext(ctx);
  vm.runInContext(CONTROLS, ctx);
  vm.runInContext(PLAYER, ctx);
  await flush(); await flush();
  await advance(300);

  const ev = (o) => Object.assign({ prevented: false, preventDefault() { this.prevented = true; } }, o);
  const env = {
    clock, advance, calls, handlers, EM, els, videos, shell,
    pb: window.AiSignX.playback, ct: window.AiSignX.controls,
    cur: () => (active ? active.item.id : null),
    video: () => (active && isVideo(active) ? active.media : null),
    key(key, repeat) {
      const e = ev({ key, repeat: !!repeat });
      (winListeners.keydown || []).forEach((fn) => fn(e));
      return e.prevented;
    },
    pointer(type, o) {
      const e = ev(Object.assign({ pointerId: 1, pointerType: "touch", button: 0, clientX: 0, clientY: 0 }, o));
      (els.controls.listeners[type] || []).forEach((fn) => fn(e));
      (winListeners[type] || []).forEach((fn) => fn(e));
      return e;
    },
    hint: () => (els.hint.children[0] ? els.hint.children[0].textContent : els.hint.textContent),
  };
  return env;
}

const IMG = (id, dur) => ({ id, media_id: id, type: "image", url: "/m/" + id, duration_ms: dur || 10000 });
const VID = (id, secs, durMs) => ({ id, media_id: id, type: "video", url: "/v/" + id,
  duration_ms: durMs || secs * 1000, media_duration_ms: secs * 1000 });

// A gesture: down at (x0,y0), optional moves, up at the end point after `ms`.
async function gesture(h, pts, ms, type, holdFirst) {
  const pt = type || "touch";
  h.pointer("pointerdown", { pointerType: pt, clientX: pts[0][0], clientY: pts[0][1] });
  if (holdFirst) await h.advance(holdFirst);
  const step = Math.max(1, Math.floor(ms / pts.length));
  for (let i = 1; i < pts.length; i++) {
    await h.advance(step);
    h.pointer("pointermove", { pointerType: pt, clientX: pts[i][0], clientY: pts[i][1] });
  }
  const last = pts[pts.length - 1];
  h.pointer("pointerup", { pointerType: pt, clientX: last[0], clientY: last[1] });
}

async function main() {
  const out = {};
  const three = () => [IMG(1), IMG(2), IMG(3)];

  // Key mapping: each key's action, seen through what it does (or the action name via a spy).
  {
    const h = await make(three());
    const seen = [];
    const real = h.pb;
    Object.keys(real).forEach((k) => { if (typeof real[k] === "function" && k !== "enabled" && k !== "kind") real[k] = ((name) => () => { seen.push(name); return null; })(k); });
    const keys = [" ", "Enter", "ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "PageUp", "PageDown",
      "MediaPlayPause", "MediaPlay", "MediaPause", "MediaStop", "MediaTrackNext", "MediaTrackPrevious",
      "MediaFastForward", "MediaRewind", "ChannelUp", "ChannelDown", "a"];
    out.keys = {};
    for (const k of keys) { seen.length = 0; const prevented = h.key(k); await h.advance(400); out.keys[k] = { action: seen[0] || null, prevented }; }
    // Repeats: forward/back throttled to one per 250 ms; other repeats ignored.
    seen.length = 0;
    h.key("ArrowRight");
    for (let i = 0; i < 10; i++) { await h.advance(50); h.key("ArrowRight", true); }
    out.repeatForward = seen.length;  // 1 + repeats at >= 250 ms
    seen.length = 0;
    await h.advance(1000);
    h.key("ArrowDown"); await h.advance(260); h.key("ArrowDown", true); await h.advance(260); h.key("ArrowDown", true);
    out.repeatNext = seen.length;
    // Cross-source dedupe: the same media key from the Media Session within 150 ms.
    seen.length = 0;
    await h.advance(1000);
    h.ct.action("toggle", "key", false); h.ct.action("pause", "session", false);
    await h.advance(200); h.ct.action("pause", "session", false);
    out.dedupe = seen.slice();
  }

  // Gestures: tap, swipe, hold-drag; mouse click delay and dblclick; wheel; rotation.
  {
    const h = await make(three());
    const seen = [];
    ["toggle", "next", "prev", "forward", "back"].forEach((k) => { h.pb[k] = () => { seen.push(k); return null; }; });
    const run = async (fn) => { seen.length = 0; await fn(); await h.advance(400); return seen.slice(); };
    out.gestures = {
      tap: await run(() => gesture(h, [[500, 250]], 100)),
      swipeLeft: await run(() => gesture(h, [[600, 250], [520, 255], [450, 260]], 200)),
      swipeRight: await run(() => gesture(h, [[400, 250], [480, 250], [560, 250]], 200)),
      vertical: await run(() => gesture(h, [[500, 100], [505, 200], [510, 300]], 200)),
      shortMove: await run(() => gesture(h, [[500, 250], [530, 250]], 100)),
      longPress: await run(() => gesture(h, [[500, 250]], 0, "touch", 500)),
      holdDragImage: await run(() => gesture(h, [[500, 250], [560, 250], [620, 250]], 200, "touch", 400)),
    };
    // Mouse: a click waits 250 ms; a double click cancels it and seeks by the half clicked.
    seen.length = 0;
    await gesture(h, [[500, 250]], 50, "mouse");
    await h.advance(100);
    const before = seen.length;
    await h.advance(300);
    out.gestures.clickDelayed = { at100: before, after: seen.slice() };
    seen.length = 0;
    await gesture(h, [[800, 250]], 30, "mouse"); await h.advance(60);
    await gesture(h, [[800, 250]], 30, "mouse");
    h.pointer("dblclick", { pointerType: "mouse", clientX: 800, clientY: 250 });
    await h.advance(400);
    await gesture(h, [[100, 250]], 30, "mouse"); await h.advance(60);
    await gesture(h, [[100, 250]], 30, "mouse");
    h.pointer("dblclick", { pointerType: "mouse", clientX: 100, clientY: 250 });
    await h.advance(400);
    out.gestures.dblclick = seen.slice();
    // Wheel: one step per 300 ms.
    seen.length = 0;
    for (let i = 0; i < 6; i++) { h.pointer("wheel", { deltaY: 100 }); await h.advance(100); }
    await h.advance(400);
    const w = h.pointer("wheel", { deltaY: -100 });
    out.gestures.wheel = { actions: seen.slice(), prevented: w.prevented };
    // Mouse side buttons, without history navigation.
    seen.length = 0;
    const b3 = h.pointer("mouseup", { button: 3 }); await h.advance(400);
    const b4 = h.pointer("mouseup", { button: 4 });
    const aux = h.pointer("auxclick", { button: 3 });
    out.gestures.sideButtons = { actions: seen.slice(), prevented: b3.prevented && b4.prevented && aux.prevented };
  }
  {
    // Body rotated 90: a physical swipe downwards is an upright swipe to the right (prev).
    const h = await make(three(), { rotation: 90 });
    const seen = [];
    ["next", "prev"].forEach((k) => { h.pb[k] = () => { seen.push(k); return null; }; });
    await gesture(h, [[500, 100], [500, 200], [500, 300]], 200);
    await h.advance(400);
    await gesture(h, [[500, 300], [500, 200], [500, 100]], 200);
    await h.advance(400);
    out.rotated90 = seen.slice();
  }

  // Controls ignored: input off, not PLAYING, emergency.
  {
    const off = await make(three(), { allow_input: false });
    off.key(" ");
    out.ignoredInputOff = { paused: off.pb.paused(), overlayHidden: off.els.controls.hidden };
    const h = await make(three());
    out.overlayShownPlaying = !h.els.controls.hidden;
    h.EM.show({ id: 7, layout: "none" });
    h.key(" ");
    out.ignoredEmergency = h.pb.paused();
    h.EM.hide();
    h.handlers.revoked && null;
    const np = await make([]);  // no content: never PLAYING
    np.key(" ");
    out.ignoredNotPlaying = { paused: np.pb.paused(), enabled: np.pb.enabled(), overlayHidden: np.els.controls.hidden };
  }

  // Pause freezes the deadline; resume gives back the time left; auto-resume after resume_after_s.
  {
    const h = await make(three());
    const first = h.cur();
    await h.advance(3000);
    h.key(" ");
    const pausedNow = h.pb.paused();
    const pillShown = !h.els.paused.hidden, sub = h.els.paused.sub.textContent;
    await h.advance(20000);
    const stillFirst = h.cur();
    h.key(" ");
    await h.advance(6500);
    const beforeEnd = h.cur();
    await h.advance(1000);
    out.pause = { first, pausedNow, pillShown, sub, stillFirst, beforeEnd, after: h.cur(),
      pop: h.calls.pop.slice(0, 5), hint: h.hint() };
  }
  {
    const h = await make(three(), { resume_after_s: 60 });
    await h.advance(1000);
    h.key(" ");
    await h.advance(59000);
    const at59 = h.pb.paused();
    await h.advance(1500);
    out.autoResume = { at59, at60: h.pb.paused(), cur: h.cur(), pillHidden: h.els.paused.hidden };
  }
  {
    const h = await make(three());
    h.key(" ");
    h.EM.show({ id: 9, layout: "banner" });
    await h.advance(500);
    out.emergencyResumes = { paused: h.pb.paused(), pop: h.calls.pop.filter((c) => c === "pause" || c === "resume") };
  }

  // Skips: next/prev targets, rapid x3, wrap-around, single-item restart.
  {
    const h = await make([IMG(1), IMG(2), IMG(3), IMG(4)]);
    const seq = [h.cur()];
    h.key("ArrowDown"); await h.advance(400); seq.push(h.cur());
    h.key("ArrowUp"); await h.advance(400); seq.push(h.cur());
    h.key("ArrowUp"); await h.advance(400); seq.push(h.cur());  // wraps to the last item
    out.skips = seq;
    // Rapid x3 (before the targets are shown): lands three ahead.
    const h2 = await make([IMG(1), IMG(2), IMG(3), IMG(4), IMG(5)]);
    h2.pb.next(); h2.pb.next(); h2.pb.next();
    await h2.advance(400);
    out.rapid3 = h2.cur();
    const h3 = await make([IMG(1), IMG(2), IMG(3)]);
    h3.pb.prev(); h3.pb.prev();
    await h3.advance(400);
    out.rapidPrev2 = h3.cur();
    // A skipped play ends early with pop end + begin.
    out.skipPop = h.calls.pop.slice(0, 4);
  }
  {
    const h = await make([IMG(1)]);
    await h.advance(4000);
    const pops = h.calls.pop.length;
    h.key("PageDown");
    await h.advance(9000);
    const stillShown = h.cur();
    const swaps = h.calls.swaps.length;
    out.singleRestart = { newPlay: h.calls.pop.slice(pops), stillShown, swaps };
    const v = await make([VID(5, 30)]);
    await v.advance(7000);
    v.video().currentTime = 7;
    v.key("ArrowDown");
    out.singleVideoRestart = { t: v.video().currentTime, playing: !v.video().paused };
  }

  // Image forward = next, back = previous.
  {
    const h = await make(three());
    h.key("ArrowRight"); await h.advance(400);
    const a = h.cur(), hintA = h.hint();
    h.key("ArrowLeft"); await h.advance(400);
    out.imageSeek = { forward: a, back: h.cur(), hint: hintA };
  }

  // Video seek: +10 s moves the deadline of a play-once video; looping videos keep theirs.
  {
    const h = await make([VID(1, 30), IMG(2)]);
    await h.advance(2000);
    h.video().currentTime = 2;
    h.key("ArrowRight");
    const t = h.video().currentTime, hint = h.hint();
    await h.advance(17500);   // 30 s - 10 s seek = 20 s: still on the video at 19.5 s
    const at19 = h.cur();
    await h.advance(1000);
    out.videoSeek = { t, hint, at19, at21: h.cur() };
    // Back stops at 0.
    const b = await make([VID(1, 30), IMG(2)]);
    b.video().currentTime = 4;
    b.key("ArrowLeft");
    out.videoBack = { t: b.video().currentTime, hint: b.hint() };
    // Looping (duration longer than the media): the deadline is unchanged.
    const l = await make([VID(1, 20, 60000), IMG(2)]);
    await l.advance(2000);
    l.video().currentTime = 2;
    l.key("ArrowRight");
    await l.advance(55000);
    const at57 = l.cur();
    await l.advance(4000);
    out.loopSeek = { at57, at61: l.cur() };
    // Forward at the end goes to the next item.
    const e = await make([VID(1, 30), IMG(2)]);
    e.video().currentTime = 25;
    e.key("ArrowRight");
    await e.advance(400);
    out.forwardAtEnd = e.cur();
    // Paused + seek adjusts the remaining time instead.
    const p = await make([VID(1, 30), IMG(2)]);
    p.key(" ");
    p.key("ArrowRight");
    const pv = p.video().paused;
    p.key(" ");
    await p.advance(19500);
    const pAt = p.cur();
    await p.advance(1000);
    out.pausedSeek = { videoPausedWhilePaused: pv, at19: pAt, at21: p.cur() };
  }

  // Hold-drag scrub on a video.
  {
    const h = await make([VID(1, 300), IMG(2)]);
    h.video().currentTime = 100;
    await gesture(h, [[200, 250], [300, 250], [450, 250]], 300, "touch", 400);  // +250 px of 1000 = +30 s
    await h.advance(200);
    out.scrub = { t: h.video().currentTime, playing: !h.video().paused, hintScrub: h.els.hint.className.indexOf("scrub") >= 0 };
  }

  // Shell keys reach the same actions; unlock/lock commands are forwarded to the shell.
  {
    const h = await make(three(), { shell: true });
    h.shell.control("toggle", false);
    const paused = h.pb.paused();
    h.shell.control("bogus", false);
    h.handlers.command({ action: "unlock", minutes: 15, request_id: "0123456789abcdef" });
    const hintU = h.hint();
    h.handlers.command({ action: "lock", request_id: "fedcba9876543210" });
    out.shell = { paused, commands: h.calls.commands, hintUnlock: hintU, hintLock: h.hint() };
    const b = await make(three(), { debug: true });
    b.handlers.command({ action: "unlock", minutes: 15, request_id: "0123456789abcdef" });
    const nb = await make(three());
    nb.handlers.command({ action: "unlock", minutes: 15, request_id: "0123456789abcdef" });
    out.browserUnlock = { debugHint: b.hint(), plainHintHidden: nb.els.hint.hidden };
  }

  // Every other item fails to load (server down): the slide on screen keeps playing instead of
  // the screen going blank, and the others are tried again; once they load, playback moves on.
  {
    let down = true;
    const h = await make([IMG(1, 5000), IMG(2, 5000), IMG(3, 5000)],
      { failIds: [2, 3], failNow: () => down });
    await h.advance(60000);
    const stuck = { cur: h.cur(), swaps: h.calls.swaps.length, noContent: !!h.els.screen.hidden === false };
    down = false;
    await h.advance(30000);
    const after = h.calls.swaps.slice(stuck.swaps);
    out.offlineKeeps = { stuck, onlyWorking: h.calls.swaps.slice(0, stuck.swaps).every((id) => id === 1), after };
  }

  // render.js helpers on a fake video.
  {
    const ctx = { window: {}, document: { createElement() {} }, location: { href: "http://x/" }, Promise, Math, Number, isFinite,
      setTimeout, clearTimeout };
    ctx.window.AiSignX = {};
    vm.createContext(ctx);
    vm.runInContext(RENDER, ctx);
    const R = ctx.window.AiSignX.render;
    const v = { tagName: "VIDEO", currentTime: 50, duration: 60, play() { return Promise.resolve(); }, pause() {} };
    const slide = { media: v };
    const a = R.seekVideo(slide, 10);
    const b = R.seekVideo(slide, -100);
    v.currentTime = 55;
    const c = R.seekVideo(slide, 10);
    const d = R.setVideoTime(slide, 999);
    out.render = { a, b, c, d, img: R.seekVideo({ media: { tagName: "IMG" } }, 10) };

    // A video that is still seeking: presses only move the wanted position, and the browser is
    // asked once, when the seek in progress ends ("seeked").
    const writes = [];
    let t = 100;
    const listeners = {};
    const sv = { tagName: "VIDEO", duration: 600, seeking: false, paused: false,
      get currentTime() { return t; },
      set currentTime(x) { t = x; writes.push(x); sv.seeking = true; },
      addEventListener(n, fn) { listeners[n] = fn; }, play() { return Promise.resolve(); }, pause() {} };
    const sslide = { media: sv };
    const r1 = R.seekVideo(sslide, 10);   // not seeking: written at once
    const r2 = R.seekVideo(sslide, 10);   // seeking: remembered
    const r3 = R.seekVideo(sslide, 10);
    const r4 = R.seekVideo(sslide, -5);
    const duringSeek = writes.slice();
    sv.seeking = false; listeners.seeked();  // the first seek finished
    const afterSeeked = writes.slice();
    sv.seeking = false;
    const end = R.seekVideo(sslide, 9999);   // past the end while idle
    out.coalesce = { r: [r1.applied, r2.applied, r3.applied, r4.applied], duringSeek, afterSeeked,
      end, isSeeking: R.isSeeking(sslide) };

  }

  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
