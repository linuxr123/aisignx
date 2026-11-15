// Runs static/player/plugins/image_library/native.js in a Node vm with a fake DOM and a fake
// clock, and prints JSON for tests/test_plugin_image_library.py.
// Usage: node plugin_image_library.js <path to native.js>.
"use strict";
const fs = require("fs");
const vm = require("vm");

const SRC = fs.readFileSync(process.argv[2], "utf-8");
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

function world(opts) {
  opts = opts || {};
  const fails = new Set(opts.fail || []);
  const loadMs = opts.loadMs === undefined ? 50 : opts.loadMs;
  const clock = { t: 1000000, timers: [], seq: 0 };
  const add = (fn, ms, every) => {
    const id = ++clock.seq;
    clock.timers.push({ id, fn, at: clock.t + (ms || 0), every: every ? ms : 0 });
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
      if (x.every) x.at += x.every; else clock.timers.shift();
      x.fn();
      await flush();
    }
    clock.t = end;
    await flush();
  }
  const requested = [];
  function classList(e) {
    const set = () => new Set(e.className.split(/\s+/).filter(Boolean));
    const save = (s) => { e.className = Array.from(s).join(" "); };
    return {
      add: (c) => { const s = set(); s.add(c); save(s); },
      remove: (c) => { const s = set(); s.delete(c); save(s); },
      contains: (c) => set().has(c),
    };
  }
  function el(tag) {
    const attrs = {};
    const e = {
      tagName: tag.toUpperCase(), style: {}, className: "", children: [], parentNode: null,
      textContent: "", offsetWidth: 100, _src: "",
      setAttribute: (k, v) => { attrs[k] = String(v); },
      appendChild: (c) => {
        if (c.parentNode) c.parentNode.removeChild(c);
        c.parentNode = e; e.children.push(c); return c;
      },
      removeChild: (c) => { e.children = e.children.filter((x) => x !== c); c.parentNode = null; return c; },
      removeAttribute: (k) => { if (k === "src") e._src = ""; delete attrs[k]; },
    };
    e.classList = classList(e);
    if (tag === "img") {
      Object.defineProperty(e, "src", {
        get: () => e._src,
        set: (v) => {
          e._src = v;
          requested.push(v);
          add(() => {
            if (e._src !== v) return;
            if (fails.has(v)) { if (e.onerror) e.onerror(); } else if (e.onload) e.onload();
          }, loadMs);
        },
      });
    }
    return e;
  }
  const win = {
    console: { info() {}, error() {}, log() {} },
    document: { createElement: el },
    setTimeout: (fn, ms) => add(fn, ms), clearTimeout: clear,
    Date: { now: () => clock.t },
    Math, JSON, Promise, Array, Number, String, Error, Object, isFinite,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SRC, win);
  const N = win.AiSignX.pluginNative.image_library;
  const cards = [];
  function api() {
    return {
      attach: (slide, m) => { slide.media = m; slide.el.appendChild(m); },
      fitClass: (f) => "fit-" + f,
      volume: () => 1,
      prepCard: (slide, title, msg) => { cards.push([title, msg]); return Promise.resolve(); },
    };
  }
  function item(config, media) {
    return { id: 1, type: "plugin", url: "about:blank#aisignx-plugin=image_library&c=x",
      plugin: { key: "image_library", render: "native", config: config, media: media } };
  }
  async function start(config, media) {
    const slide = { el: el("div"), item: item(config, media), dead: false };
    let settled = null;
    N.prepare(slide, api()).then(() => { settled = "ok"; }, (e) => { settled = "error: " + e.message; });
    await advance(8000);
    const notes = { degraded: 0, done: 0 };
    const ctx = {
      loop: () => false, done: () => { notes.done++; }, fail: () => {}, note: () => {},
      every: (fn, ms) => add(fn, ms, true), degraded: () => { notes.degraded++; },
    };
    return { slide, settled, ctx, notes, run: () => N.run(slide, ctx) };
  }
  return { N, start, advance, clock, requested, cards, el };
}

const media = (n, extra) => Array.from({ length: n }, (_, i) => Object.assign(
  { url: "/m/" + (i + 1) + ".jpg", media_id: i + 1, name: "Photo " + (i + 1) }, extra || {}));
const BASE = { mode: "slideshow", seconds_per_image: 3, transition: "fade", fit: "contain",
  background: "black", caption: "none", shuffle: false, grid: "2x2" };
const cfg = (o) => Object.assign({}, BASE, o || {});

function root(slide) { return slide.el.children[0]; }
function frames(box) { return box.children.filter((c) => /pl-imglib-frame/.test(c.className)); }
function mainImg(frame) { return frame.children.find((c) => /pl-imglib-img/.test(c.className)); }
function srcs(box) { return frames(box).map((f) => mainImg(f).src); }

async function main() {
  const out = {};
  const { N } = world();
  const U = N.util;

  // ---- Pure helpers.
  out.settingsDefault = U.settings({ plugin: { config: {} } });
  out.settingsOdd = U.settings({ plugin: { config: { mode: "x", seconds_per_image: 999, transition: "spin",
    fit: "cover", grid: "3x3", background: "blur", caption: "name", shuffle: "yes" } } });
  out.settingsCollage = U.settings({ plugin: { config: { mode: "collage", grid: "3x3", fit: "contain" } } });
  out.entries = U.entries({ plugin: { media: [{ url: "/a" }, null, { url: 5 }, { url: "/b", name: "B" }] } });
  let seed = 7;
  const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  const list = [1, 2, 3, 4, 5, 6, 7, 8];
  out.orderPlain = U.order(list, false, rnd);
  out.orderShuffled = U.order(list, true, rnd);
  const picks = new Set();
  let neverLast = true;
  for (let i = 0; i < 400; i++) {
    const last = i % 4, p = U.pickTile(4, last, Math.random);
    if (p === last || p < 0 || p > 3) neverLast = false;
    picks.add(p);
  }
  out.pickTile = { neverLast, all: picks.size === 4, single: U.pickTile(1, 0, Math.random) };

  // ---- Slideshow: first image ready at prepare, two loaded ahead, swaps every period.
  {
    const w = world();
    const s = await w.start(cfg(), media(4));
    const r = root(s.slide);
    out.slideshow = { settled: s.settled, rootClass: r.className, first: srcs(r),
      preloaded: w.requested.slice() };
    s.run();
    await w.advance(2900);
    out.slideshow.before = srcs(r);
    await w.advance(400);
    const during = frames(r);
    out.slideshow.during = srcs(r);
    out.slideshow.classes = during.map((f) => f.className);
    await w.advance(1300);
    out.slideshow.after = srcs(r);
    await w.advance(3000 * 3);
    out.slideshow.wrapped = srcs(r);
    N.teardown(s.slide);
    out.slideshow.teardownSrcs = srcs(r).filter(Boolean).length;
  }

  // ---- A failing image is skipped; all failing shows the card and reports degraded.
  {
    const w = world({ fail: ["/m/2.jpg"] });
    const s = await w.start(cfg(), media(3));
    s.run();
    await w.advance(3300 + 1300);
    out.skip = srcs(root(s.slide));
  }
  {
    const w = world({ fail: ["/m/1.jpg", "/m/2.jpg"] });
    const s = await w.start(cfg(), media(2));
    const r = root(s.slide);
    s.run();
    out.allFail = { settled: s.settled, card: r.children.some((c) => /pl-imglib-card/.test(c.className)),
      degraded: s.notes.degraded, frames: frames(r).length };
  }
  {
    const w = world();
    const s = await w.start(cfg(), []);
    s.run();
    out.empty = { cards: w.cards, degraded: s.notes.degraded };
  }

  // ---- Pause holds the image; resume carries on.
  {
    const w = world();
    const s = await w.start(cfg(), media(3));
    const r = root(s.slide);
    s.run();
    await w.advance(1000);
    N.pause(s.slide);
    const pausedClass = r.className;
    await w.advance(10000);
    const held = srcs(r);
    N.resume(s.slide);
    await w.advance(2300);
    out.pause = { pausedClass, held, resumed: srcs(r), resumedClass: r.className };
  }

  // ---- One image: a slideshow never swaps (or reloads); Ken Burns starts a new move.
  {
    const w = world();
    const s = await w.start(cfg(), media(1));
    s.run();
    await w.advance(10000);
    out.single = { srcs: srcs(root(s.slide)), requests: w.requested.length };
  }
  {
    const w = world();
    const s = await w.start(cfg({ mode: "kenburns" }), media(1));
    const r = root(s.slide);
    s.run();
    const first = mainImg(frames(r)[0]);
    out.kenburns = { cls: first.className, duration: first.style.animationDuration };
    await w.advance(3300);
    const fs2 = frames(r);
    out.kenburns.framesDuring = fs2.length;
    out.kenburns.newCls = mainImg(fs2[fs2.length - 1]).className;
  }

  // ---- Blurred backdrop and caption.
  {
    const w = world();
    const s = await w.start(cfg({ background: "blur", caption: "name" }), media(2));
    const f = frames(root(s.slide))[0];
    out.blur = { kids: f.children.map((c) => c.className), bgSrc: f.children[0].src,
      caption: f.children[2] && f.children[2].textContent };
    const w2 = world();
    const s2 = await w2.start(cfg({ background: "blur", fit: "cover" }), media(2));
    out.blur.cover = frames(root(s2.slide))[0].children.map((c) => c.className);
  }

  // ---- Collage: tiles filled in order, one tile changes per period, no image shown twice.
  {
    const w = world();
    const s = await w.start(cfg({ mode: "collage" }), media(6));
    const grid = root(s.slide).children[0];
    const tileSrcs = () => grid.children.map((t) => srcs(t).slice(-1)[0]);
    out.collage = { gridClass: grid.className, tiles: grid.children.length, first: tileSrcs() };
    s.run();
    await w.advance(3300 + 1300);
    out.collage.after1 = tileSrcs();
    await w.advance(3000 + 400);
    out.collage.after2 = tileSrcs();
    let dupes = 0;
    for (let i = 0; i < 12; i++) {
      await w.advance(3000);
      const now = tileSrcs();
      if (new Set(now).size !== now.length) dupes++;
    }
    out.collage.dupes = dupes;
    const w3 = world();
    const s3 = await w3.start(cfg({ mode: "collage", grid: "3x3" }), media(4));
    const g3 = root(s3.slide).children[0];
    s3.run();
    const before = g3.children.map((t) => srcs(t)[0]);
    await w3.advance(10000);
    out.collage.small = { tiles: g3.children.length, before,
      after: g3.children.map((t) => srcs(t)[0]), requests: w3.requested.length };
  }

  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
