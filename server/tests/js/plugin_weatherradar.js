// Runs static/player/plugins/weatherradar/main.js in a Node vm with a tiny fake DOM, a fake
// clock and a stub SDK ctx, and prints JSON for tests/test_plugin_weatherradar.py.
// Usage: node plugin_weatherradar.js <static/player/plugins/weatherradar directory>.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const MAIN = fs.readFileSync(path.join(process.argv[2], "main.js"), "utf-8");
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

function makeDom() {
  function el(tag) {
    const classes = new Set(), listeners = {};
    const e = {
      tagName: tag.toUpperCase(), children: [], parentNode: null, hidden: false, alt: "", style: {},
      _text: "", _src: "",
      get className() { return Array.from(classes).join(" "); },
      set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
      classList: {
        toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
        contains: (c) => classes.has(c),
      },
      get firstChild() { return e.children[0] || null; },
      get textContent() { return e._text + e.children.map((c) => c.textContent).join(""); },
      set textContent(v) { e._text = String(v); e.children = []; },
      appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = e; e.children.push(c); return c; },
      removeChild(c) { const i = e.children.indexOf(c); if (i >= 0) e.children.splice(i, 1); c.parentNode = null; return c; },
      addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
      get src() { return e._src; },
      set src(v) {
        e._src = v;
        setImmediate(() => (listeners[/broken/.test(v) ? "error" : "load"] || []).forEach((fn) => fn()));
      },
      cloneNode() { const c = el(tag); c.className = e.className; c._src = e._src; c.alt = e.alt; return c; },
    };
    return e;
  }
  const doc = { createElement: el, badge: null };
  doc.querySelector = (sel) => (sel === ".aisx-stale" ? doc.badge : null);
  return doc;
}

function makeClock() {
  const c = { t: 0, seq: 0, timers: [] };
  c.add = (fn, ms, repeat) => { const id = ++c.seq; c.timers.push({ id, fn, at: c.t + ms, ms, repeat }); return id; };
  c.clear = (id) => { c.timers = c.timers.filter((x) => x.id !== id); };
  c.advance = async (ms) => {
    const end = c.t + ms;
    await flush();
    for (;;) {
      c.timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const x = c.timers[0];
      if (!x || x.at > end) break;
      c.t = x.at;
      if (x.repeat) x.at += x.ms; else c.timers.shift();
      x.fn();
      await flush();
    }
    c.t = end;
    await flush();
  };
  return c;
}

function load() {
  const document = makeDom();
  const sandbox = { document, window: {}, Promise, console };
  sandbox.AiSignXPlugin = { register: (h) => { sandbox.hooks = h; } };
  vm.createContext(sandbox);
  vm.runInContext(MAIN, sandbox);
  return sandbox;
}

function makeCtx(sb, clock, over) {
  const NOW = 1763208600000;
  const log = { ready: 0, fallbacks: [], assets: [], stale: [], attribution: null };
  const ctx = Object.assign({
    root: sb.document.createElement("div"), key: "weatherradar",
    config: { frame_ms: 500, location: { label: "Des Moines", lat: 41.59, lon: -93.62 } },
    data: null, stale: false, warming: false, offline: false, fetchedAt: NOW, tz: "UTC", locale: "en-GB",
    now: () => NOW + clock.t,
    format: (ms) => new Date(ms).toISOString().slice(11, 16),
    every: (fn, ms) => clock.add(fn, ms, true),
    later: (fn, ms) => clock.add(fn, ms, false),
    clear: clock.clear,
    ready: () => { log.ready++; },
    fallback: (t, m) => { log.fallbacks.push(t == null ? null : [t, m]); },
    staleBadge: (at) => {  // like the SDK: a badge in a corner of the page, or none
      log.stale.push(at);
      if (sb.document.badge && sb.document.badge.parentNode) sb.document.badge.parentNode.removeChild(sb.document.badge);
      sb.document.badge = at ? sb.document.createElement("div") : null;
      if (sb.document.badge) sb.document.createElement("body").appendChild(sb.document.badge);
    },
    attribution: (text) => { log.attribution = text === undefined ? "(default)" : text; },
    asset: (id) => { log.assets.push(id); return Promise.resolve("blob:" + id); },
  }, over || {});
  return { ctx, log, NOW };
}

function radarData(NOW, n) {
  const frames = [];
  for (let i = 0; i < n; i++) frames.push({ t: NOW - (n - 1 - i) * 600000, asset: String(i).repeat(32).slice(0, 32) });
  return {
    source: "rainviewer", frames, basemap: "b".repeat(32),
    attribution: "Radar: RainViewer · Map © OpenStreetMap contributors",
    alerts: [{ event: "Tornado Warning", headline: "Take shelter now", severity: "Extreme", ends_ms: NOW + 3600000 },
      { event: "Old Advisory", severity: "Minor", ends_ms: NOW - 1 }],
  };
}

function onIndex(ctx) {
  const radar = ctx.root.children[0].children[1];
  return radar.children.map((c, i) => (c.classList.contains("wr-on") ? i : -1)).filter((i) => i >= 0);
}

async function run() {
  const sb = load(), clock = makeClock();
  const { ctx, log, NOW } = makeCtx(sb, clock);
  ctx.data = radarData(NOW, 3);
  const h = sb.hooks;
  const out = {};
  h.init(ctx);
  await clock.advance(0);
  const radar = ctx.root.children[0].children[1];
  const top = ctx.root.children[2];
  const alert = ctx.root.children[4];
  out.ready = log.ready;
  out.fallbacks = log.fallbacks.slice();
  out.frames = radar.children.length;
  out.on = onIndex(ctx);
  await clock.advance(500); out.stepped = onIndex(ctx);
  h.show(ctx); await clock.advance(0); out.afterShow = onIndex(ctx);
  await clock.advance(1000); out.newest = onIndex(ctx);
  out.rel = top.children[1].children[1].textContent;
  out.clock = top.children[1].children[0].textContent;
  await clock.advance(1000); out.held = onIndex(ctx);
  await clock.advance(500); out.wrapped = onIndex(ctx);
  h.pause(ctx); await clock.advance(5000); out.paused = onIndex(ctx);
  h.resume(ctx); await clock.advance(500); out.resumed = onIndex(ctx);
  out.place = top.children[0].children[0].textContent;
  out.attribution = log.attribution;
  out.alert = { hidden: alert.hidden, event: alert.children[0] && alert.children[0].textContent };
  out.stale = log.stale.slice();
  // A refresh with one new frame loads only that frame's picture and keeps the map.
  log.assets = [];
  ctx.data = radarData(NOW, 4);
  ctx.data.frames = ctx.data.frames.map((f, i) => ({ t: f.t, asset: i < 3 ? String(i).repeat(32) : "f".repeat(32) }));
  h.data(ctx); await clock.advance(0);
  out.refresh = { frames: radar.children.length, assets: log.assets.slice(), ready: log.ready };
  // Stale data: the SDK's badge is moved from the page corner to under the frame time.
  ctx.stale = true;
  h.data(ctx); await clock.advance(0);
  out.badgeUnderTime = !!sb.document.badge && sb.document.badge.parentNode === top.children[1];
  h.destroy(ctx);
  return out;
}

async function cardOnly(over) {
  const sb = load(), clock = makeClock();
  const { ctx, log } = makeCtx(sb, clock, over);
  sb.hooks.init(ctx);
  await clock.advance(7000);
  return { fallbacks: log.fallbacks, ready: log.ready };
}

(async () => {
  const W = load().window.WeatherRadar;
  const r = {
    frameTimes: W.frameTimes(3, 500),
    frameTimesClamped: W.frameTimes(1, 100).concat(W.frameTimes(1, 5000)),
    ago: [0, 20, 60, 125].map((m) => W.ago(1000000000 - m * 60000, 1000000000)),
    liveAlerts: W.liveAlerts([{ event: "Tornado Warning", ends_ms: 2e12 }, { event: "Gone", ends_ms: 1 }, { headline: "x" }], 1e12)
      .map((a) => a.event),
    severity: [W.severityClass("Severe"), W.severityClass("<script>")],
    run: await run(),
    warming: await cardOnly({ warming: true }),
    offline: await cardOnly({ offline: true }),
  };
  process.stdout.write(JSON.stringify(r));
})().catch((e) => { process.stderr.write(String((e && e.stack) || e)); process.exit(1); });
