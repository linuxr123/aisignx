// Runs static/player/render.js's plugin path in a Node vm with a fake DOM, a fake clock, a
// stub A.pluginHost and a stub native plugin, and prints JSON for tests/test_plugins_host.py.
// Usage: node plugin_render.js <static/player directory>.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const RENDER = fs.readFileSync(path.join(process.argv[2], "render.js"), "utf-8");
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function world() {
  const clock = { t: 0, timers: [], seq: 0 };
  const add = (fn, ms, repeat) => {
    const id = ++clock.seq;
    clock.timers.push({ id, fn, at: clock.t + (ms || 0), ms: Math.max(1, ms || 0), repeat });
    return id;
  };
  const clear = (id) => { const i = clock.timers.findIndex((x) => x.id === id); if (i >= 0) clock.timers.splice(i, 1); };
  async function advance(ms) {
    const end = clock.t + ms;
    await flush();
    for (;;) {
      clock.timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const x = clock.timers[0];
      if (!x || x.at > end) break;
      clock.t = Math.max(clock.t, x.at);
      if (x.repeat) x.at += x.ms; else clock.timers.shift();
      x.fn();
      await flush();
    }
    clock.t = end;
    await flush();
  }
  function el(tag) {
    const classes = new Set(), attrs = {};
    const e = {
      tagName: tag.toUpperCase(), style: {}, children: [], parentNode: null, attrs, src: "", tabIndex: 0,
      get className() { return Array.from(classes).join(" "); },
      set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
      classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
      setAttribute: (k, v) => { attrs[k] = String(v); }, getAttribute: (k) => (k in attrs ? attrs[k] : null),
      appendChild(c) { c.parentNode = e; e.children.push(c); return c; },
      removeChild(c) { e.children.splice(e.children.indexOf(c), 1); c.parentNode = null; },
      addEventListener() {}, removeEventListener() {},
      get firstChild() { return e.children[0] || null; },
      get offsetWidth() { return 1; },
      querySelectorAll: () => [],
    };
    return e;
  }
  const log = [];
  const handles = [];
  const host = {
    create(frame, item, io) {
      const h = { frame, item, io, srcAtCreate: frame.src, calls: [], handlers: {}, deg: false };
      h.ready = new Promise((res, rej) => { h.resolve = res; h.reject = rej; });
      ["show", "pause", "resume", "destroy"].forEach((n) => { h[n] = () => h.calls.push(n); });
      h.on = (n, fn) => { h.handlers[n] = fn; };
      h.degraded = () => h.deg;
      handles.push(h);
      return h;
    },
  };
  const native = {
    prepare: (slide, api) => {
      log.push("prepare");
      if (slide.item.plugin.config.hang) return new Promise(() => {});
      api.attach(slide, el("div"));
      return Promise.resolve();
    },
    run: (slide, ctx) => { log.push("run"); native.ctx = ctx; },
    teardown: () => log.push("teardown"), pause: () => log.push("pause"), resume: () => log.push("resume"),
    setVolume: (s, v) => log.push("volume:" + v), setMuted: (s, on) => log.push("muted:" + on),
  };
  const stage = el("div");
  const win = {
    console: { info() {}, error() {}, log() {} }, location: { href: "http://x/player/", origin: "http://x" },
    document: { createElement: el },
    setTimeout: (fn, ms) => add(fn, ms, false), clearTimeout: clear,
    setInterval: (fn, ms) => add(fn, ms, true), clearInterval: clear,
    __now: () => clock.t,
  };
  win.window = win;
  win.AiSignX = { pluginHost: host, pluginNative: { demo: native }, emergency: { shownId: null, layout: null } };
  vm.createContext(win);
  vm.runInContext("Date.now = function () { return __now(); };", win);
  vm.runInContext(RENDER, win, { filename: "render.js" });
  const R = win.AiSignX.render;
  R.init(stage);
  return { R, win, clock, advance, handles, log, native, stage };
}

function frameItem(plugin) {
  return { id: 1, media_id: 9, type: "plugin", url: "/static/player/plugins/echo/index.html#c=1", duration_ms: 30000,
    plugin: Object.assign({ key: "echo", render: "frame", config: {}, ends_itself: false }, plugin || {}) };
}
function nativeItem(config, extra) {
  return { id: 2, media_id: 10, type: "plugin", url: "about:blank#aisignx-plugin=demo&c=1", duration_ms: 30000,
    plugin: Object.assign({ key: "demo", render: "native", config: config || {}, ends_itself: false }, extra || {}) };
}
function ctxFor(w, loop) {
  const got = [];
  return { got, ctx: { loop: () => loop, done: () => got.push("done@" + w.clock.t), fail: (m) => got.push("fail:" + m),
    note: (m) => got.push("note:" + m), every: (fn, ms) => w.win.setInterval(fn, ms) } };
}
const outcome = (p) => p.then(() => "resolved", (e) => "rejected:" + (e && e.message));

async function frame() {
  const w = world();
  const p = w.R.prepare(frameItem(), 1);
  const h = w.handles[0];
  const f = h.frame;
  const out = { attrs: f.attrs, className: f.className, tabIndex: f.tabIndex, inert: f.inert, srcAtCreate: h.srcAtCreate, src: f.src,
    readyMs: h.io.readyMs };
  h.resolve();
  const slide = await p;
  out.isPlugin = w.R.isPlugin(slide);
  w.R.swap(slide, "cut");
  const c = ctxFor(w, false);
  w.R.runPlugin(slide, c.ctx);
  h.handlers.complete();           // not ends_itself: ignored
  h.handlers.error("soft", false);
  h.handlers.error("hard", true);
  w.R.pause(slide); w.R.resume(slide);
  out.calls = h.calls.slice();
  out.got = c.got.slice();
  w.R.teardown(slide);
  out.afterTeardown = h.calls.slice(-1)[0];
  out.blank = f.src;
  return out;
}

async function endsItself() {
  const w = world();
  const p = w.R.prepare(frameItem({ ends_itself: true }), 1);
  w.handles[0].resolve();
  const slide = await p;
  w.R.swap(slide, "cut");
  const c = ctxFor(w, false);
  w.R.runPlugin(slide, c.ctx);
  w.handles[0].handlers.complete();
  return c.got;
}

async function degraded(loop) {
  const w = world();
  const p = w.R.prepare(frameItem(), 1);
  w.handles[0].resolve();
  const slide = await p;
  w.R.swap(slide, "cut");
  const c = ctxFor(w, loop);
  w.R.runPlugin(slide, c.ctx);
  w.handles[0].handlers.degraded();
  await w.advance(30000);
  return c.got;
}

async function nativePath() {
  const w = world();
  const out = {};
  out.unsupported = await outcome(w.R.prepare(Object.assign(nativeItem(), { plugin: { key: "nope", render: "native" } }), 1));
  const hang = w.R.prepare(nativeItem({ hang: true }), 1);
  const hung = outcome(hang);
  await w.advance(8000);
  out.hang = await hung;
  w.log.length = 0;
  const slide = await w.R.prepare(nativeItem({}, { ends_itself: false }), 1);
  w.R.swap(slide, "cut");
  const c = ctxFor(w, false);
  w.R.runPlugin(slide, c.ctx);
  w.native.ctx.done();             // not ends_itself: ignored
  w.R.setVolume(50);
  w.win.AiSignX.emergency.shownId = 3; w.win.AiSignX.emergency.layout = "fullscreen";
  await w.advance(600);
  w.win.AiSignX.emergency.shownId = null;
  await w.advance(600);
  w.R.pause(slide); w.R.pause(slide); w.R.resume(slide); w.R.resume(slide);
  w.R.teardown(slide);
  w.R.setVolume(20);               // a torn-down native plugin hears nothing more
  out.log = w.log.slice();
  out.got = c.got;
  const ends = await w.R.prepare(nativeItem({}, { ends_itself: true }), 1);
  w.R.swap(ends, "cut");
  const c2 = ctxFor(w, false);
  w.R.runPlugin(ends, c2.ctx);
  w.native.ctx.done();
  out.endsGot = c2.got;
  return out;
}

(async () => {
  const out = { frame: await frame(), endsItself: await endsItself(), degradedBusy: await degraded(false),
    degradedAlone: await degraded(true), native: await nativePath() };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
