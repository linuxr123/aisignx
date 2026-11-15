// Runs the weather plugin's icons.js and main.js in a Node vm with a tiny fake DOM and a stub
// SDK, and prints JSON for tests/test_plugin_weather.py.
// Usage: node plugin_weather.js <static/player/plugins/weather directory> <data.json>.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const DIR = process.argv[2];
const DATA = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));

function makeDocument() {
  function node(tag) {
    const n = {
      tagName: tag, children: [], parentNode: null, attrs: {}, style: {}, className: "", _text: "",
      appendChild(c) { c.parentNode = n; n.children.push(c); return c; },
      removeChild(c) { n.children = n.children.filter((x) => x !== c); c.parentNode = null; return c; },
      setAttribute(k, v) { if (k === "style") throw new Error("setAttribute(style)"); n.attrs[k] = String(v); },
      get textContent() { return n._text + n.children.map((c) => c.textContent).join(""); },
      set textContent(v) { n.children = []; n._text = String(v); },
      set innerHTML(v) { throw new Error("innerHTML used"); },
    };
    return n;
  }
  const body = node("body");
  const root = body.appendChild(node("div"));
  return {
    body, root,
    createElement: node,
    createElementNS: (ns, tag) => node("svg:" + tag),
    getElementById: () => root,
  };
}

function find(n, cls, out) {
  out = out || [];
  const c = n.className || n.attrs.class || "";
  if (String(c).split(/\s+/).indexOf(cls) >= 0) out.push(n);
  n.children.forEach((k) => find(k, cls, out));
  return out;
}

function run(config, data, opts) {
  const document = makeDocument();
  const calls = [];
  let hooks = null;
  const window = {};
  const sandbox = { window, document, Intl, Date, Math, Object, Array, String, Number, isFinite, JSON };
  window.AiSignXPlugin = opts.noSdk ? undefined : { register: (h) => { hooks = h; } };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(DIR, "icons.js"), "utf-8"), sandbox);
  vm.runInContext(fs.readFileSync(path.join(DIR, "main.js"), "utf-8"), sandbox);
  if (opts.noSdk) return { view: window.WxView };
  const timers = [];
  const ctx = {
    root: document.root, config, data, stale: !!opts.stale, offline: !!opts.offline,
    fetchedAt: 1794740400000, tz: "", locale: "en-GB",
    now: () => opts.now,
    format: (ms, o) => new Intl.DateTimeFormat("en-GB", o).format(ms),
    every: (fn, ms) => { timers.push(ms); return timers.length; },
    later: () => 0, clear: () => {},
    ready: () => calls.push("ready"),
    fallback: (t, m) => calls.push(["fallback", t, m === undefined ? null : m]),
    staleBadge: (at) => calls.push(["stale", at]),
    attribution: (t) => calls.push(["attribution", t]),
  };
  hooks.init(ctx);
  const text = (cls) => find(document.root, cls).map((n) => n.textContent);
  return {
    calls, timers, body: document.body.className,
    wrap: (find(document.root, "wx")[0] || { className: "" }).className,
    place: text("wx-place"), time: text("wx-time"), temp: text("wx-now-temp"),
    now_text: text("wx-now-text"), tiles: text("wx-tile-value"), labels: text("wx-tile-label"),
    hours: text("wx-hour-time"), hour_temps: text("wx-hour-temp"),
    days: text("wx-day-name"), bars: find(document.root, "wx-bar-fill").map((n) => [n.style.left, n.style.width]),
    dots: find(document.root, "wx-bar-now").length,
    icons: find(document.root, "wx-icon").length,
  };
}

const NOW = Date.UTC(2026, 10, 15, 12, 30);
const base = { location: { label: "Head office", lat: 59.9, lon: 10.7 }, units: "metric",
  layout: "full", days: 5, show_hourly: true, theme: "auto", refresh: 600 };
const v = run(base, DATA.om, { noSdk: true }).view;
const out = {
  helpers: {
    temp: [v.tempText(4.4), v.tempText(-0.4), v.tempText(null), v.tempText(-2.6)],
    compass: [v.compass(0), v.compass(204), v.compass(359), v.compass(-90), v.compass(null)],
    localDate: [v.localDate(Date.UTC(2026, 10, 15, 23, 30), "Europe/Oslo"), v.localDate(Date.UTC(2026, 10, 15, 23, 30), "UTC")],
    upcoming: v.upcoming(DATA.om.hourly, NOW, 3).map((h) => h.t),
    futureDays: v.futureDays(DATA.om.daily, "2026-11-17", 3).map((d) => d.date),
    bounds: v.bounds([{ tmin: -2, tmax: 6 }, { tmin: 1, tmax: 9 }], 10),
    flat: v.bounds([{ tmin: 3, tmax: 3 }], null),
    none: v.bounds([{ tmin: null, tmax: null }], null),
    span: v.span({ lo: 0, hi: 10 }, 2, 7),
    theme: [v.themeClass("dark", 0, true), v.themeClass("auto", 0, false), v.themeClass("auto", 95, true)],
    weekday: v.weekdayOf("2026-11-16", "en-GB"),
  },
  full: run(base, DATA.om, { now: NOW }),
  compact: run(Object.assign({}, base, { layout: "compact", days: 7 }), DATA.om, { now: NOW }),
  current: run(Object.assign({}, base, { layout: "current", theme: "light" }), DATA.met, { now: NOW, stale: true }),
  nohours: run(Object.assign({}, base, { show_hourly: false }), DATA.om, { now: NOW }),
  later: run(base, DATA.om, { now: NOW + 2 * 86400000 }),
  empty: run(base, null, { now: NOW, offline: true }),
};
process.stdout.write(JSON.stringify(out));
