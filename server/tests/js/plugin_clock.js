// Node harness for the clock plugin (static/player/plugins/clock). Loads clock.js, every theme
// and main.js into a VM with a tiny fake DOM, runs them and prints one JSON object that
// tests/test_plugin_clock.py checks. Usage: node plugin_clock.js <static/player/plugins/clock>
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const DIR = process.argv[2];
const THEME_FILES = ["themes/minimal.js", "themes/analog-classic.js", "themes/flip-clock.js",
                     "themes/word-clock.js"];

// ---- A fake DOM: just enough for the themes (createElement(NS), classList, textContent).
function makeNode(tag) {
  const node = {
    tagName: tag, children: [], parentNode: null, attrs: {}, hidden: false, className: "",
    style: {}, _text: "", textWrites: 0, offsetWidth: 0, scrollWidth: 100, scrollHeight: 100,
    clientWidth: 100, clientHeight: 100,
    appendChild(child) { child.parentNode = node; node.children.push(child); return child; },
    setAttribute(k, v) { node.attrs[k] = String(v); if (k === "class") node.className = String(v); },
    getAttribute(k) { return node.attrs[k]; },
    get textContent() { return node._text; },
    set textContent(v) { node._text = String(v); node.textWrites++; },
  };
  node.classList = {
    _set() { return new Set(node.className.split(/\s+/).filter(Boolean)); },
    contains(c) { return this._set().has(c); },
    add(c) { const s = this._set(); s.add(c); node.className = [...s].join(" "); },
    remove(c) { const s = this._set(); s.delete(c); node.className = [...s].join(" "); },
    toggle(c, on) { if (on === undefined ? !this.contains(c) : on) this.add(c); else this.remove(c); },
  };
  return node;
}
function makeDocument() {
  const body = makeNode("body");
  const root = body.appendChild(makeNode("div"));
  return {
    body, root,
    createElement: makeNode,
    createElementNS: (ns, tag) => Object.assign(makeNode(tag), { ns }),
    getElementById: (id) => (id === "root" ? root : null),
  };
}
function walk(node, fn) { fn(node); node.children.forEach((c) => walk(c, fn)); }
function find(node, cls) {
  let hit = null;
  walk(node, (n) => { if (!hit && n.classList.contains(cls)) hit = n; });
  return hit;
}
function findAll(node, cls) {
  const out = [];
  walk(node, (n) => { if (n.classList.contains(cls)) out.push(n); });
  return out;
}

function sandbox(extra) {
  const document = makeDocument();
  const g = { document, console, Intl, Date, Math, Object, Array, String, Number, Set, JSON,
              listeners: {}, innerWidth: 1920, innerHeight: 1080 };
  g.window = g;
  g.globalThis = g;
  g.addEventListener = (name, fn) => { g.listeners[name] = fn; };
  Object.assign(g, extra || {});
  vm.createContext(g);
  return g;
}
function load(g, file) {
  vm.runInContext(fs.readFileSync(path.join(DIR, file), "utf-8"), g, { filename: file });
}
function loadAll(g) {
  load(g, "clock.js");
  THEME_FILES.forEach((f) => load(g, f));
}

const out = {};
const AMS = "Europe/Amsterdam";
const T = Date.parse("2026-11-14T13:52:37Z");  // 14:52:37 in Amsterdam, a Saturday

// ---- Pure helpers.
{
  const g = sandbox();
  loadAll(g);
  const C = g.Clock;
  const opts = (o) => Object.assign({ tz: AMS, locale: "en-US", h12: false, seconds: true,
                                       showDate: true, dateStyle: "long", label: "" }, o);
  const pick = (t) => ({ h: t.h, m: t.m, s: t.s, hour: t.hour, hh: t.hh, mm: t.mm, ss: t.ss,
                         ampm: t.ampm, time: t.time, date: t.date, dayKey: t.dayKey });
  out.read = {
    h24: pick(C.read(T, opts())),
    h12: pick(C.read(T, opts({ h12: true }))),
    noSeconds: C.read(T, opts({ seconds: false })).time,
    noDate: C.read(T, opts({ showDate: false })).date,
    short: C.read(T, opts({ dateStyle: "short" })).date,
    tokyo: pick(C.read(T, opts({ tz: "Asia/Tokyo" }))),
    newYork: pick(C.read(T, opts({ tz: "America/New_York", h12: true }))),
    midnight12: pick(C.read(Date.parse("2026-11-14T23:05:09Z"), opts({ h12: true }))),
    noon12: C.read(Date.parse("2026-11-14T11:00:00Z"), opts({ h12: true })).time,
    badZone: C.read(T, opts({ tz: "Not/AZone" })).time.length > 0,
    german: C.read(T, opts({ locale: "de-DE" })).date,
  };
  out.angles = {
    three: C.angles({ h: 15, m: 0, s: 0 }),
    halfPast: C.angles({ h: 0, m: 30, s: 30 }),
    last: C.angles({ h: 23, m: 59, s: 59 }),
  };
  const words = (h, m, p) => C.words(h, m, p);
  out.words = {
    "14:52": words(14, 52), "09:00": words(9, 0), "23:37": words(23, 37),
    "00:15": words(0, 15), "12:30": words(12, 30), "11:58": words(11, 58),
    "14:52pm": words(14, 52, true).words, "09:00am": words(9, 0, true).words,
  };
  // Every word id must spell its own text in the letter grid.
  const bad = [];
  Object.keys(C.WORDS).forEach((id) => {
    const [r, c, n] = C.WORDS[id];
    const text = C.GRID[r].substr(c, n);
    const want = id.replace(/_M$/, "");
    if (text !== want) bad.push(id + "=" + text);
  });
  out.grid = { rows: C.GRID.length, widths: [...new Set(C.GRID.map((r) => r.length))], bad };
  // Every time of day yields known words and 0-4 dots.
  let worst = null;
  for (let h = 0; h < 24 && !worst; h++) {
    for (let m = 0; m < 60; m++) {
      const w = C.words(h, m, true);
      const unknown = w.words.filter((x) => !C.WORDS[x]);
      if (unknown.length || w.dots < 0 || w.dots > 4) { worst = h + ":" + m; break; }
    }
  }
  out.wordsAllDay = worst;
  out.lit = Object.keys(C.litCells(["IT", "IS", "OCLOCK"])).sort();
  out.themes = Object.keys(C.themes).sort();
}

// ---- Every theme mounts and ticks on the fake DOM.
{
  const results = {};
  ["minimal-dark", "minimal-light", "analog-classic", "flip-clock", "word-clock"].forEach((name) => {
    [false, true].forEach((h12) => {
      const g = sandbox();
      loadAll(g);
      const C = g.Clock;
      const opts = { tz: AMS, locale: "en-US", h12, seconds: true, showDate: true,
                     dateStyle: "long", label: "Lobby" };
      const s = C.themes[name].mount(g.document.root, opts);
      C.themes[name].tick(s, C.read(T, opts));
      const first = {};
      walk(g.document.root, (n) => { first[n.className] = (first[n.className] || 0) + n.textWrites; });
      C.themes[name].tick(s, C.read(T, opts));  // same second again: nothing rewritten
      let rewrites = 0;
      walk(g.document.root, (n) => { rewrites += n.textWrites; });
      const root = g.document.root;
      const r = {
        label: (find(root, "clk-label") || {}).textContent,
        date: (find(root, "clk-date") || {}).textContent,
        rewrites: rewrites - Object.values(first).reduce((a, b) => a + b, 0),
      };
      if (name.startsWith("minimal")) {
        r.main = find(root, "clk-hm").textContent;
        r.sec = find(root, "clk-sec").textContent;
        r.ampm = find(root, "clk-ampm").textContent;
        r.ampmHidden = find(root, "clk-ampm").hidden;
      } else if (name === "analog-classic") {
        r.hands = findAll(root, "clk-hand").map((n) => n.attrs.transform);
        r.svgNs = find(root, "clk-dial").ns;
        r.numbers = findAll(root, "clk-num").map((n) => n.textContent).join(",");
        r.marks = findAll(root, "clk-mark").length;
        r.digital = find(root, "clk-digital").textContent;
      } else if (name === "flip-clock") {
        r.digits = findAll(root, "clk-digit").map((n) => n.textContent).join("");
        r.blank = findAll(root, "clk-card").filter((n) => n.classList.contains("blank")).length;
        // The next minute flips only the cards that changed.
        C.themes[name].tick(s, C.read(T + 23000, opts));
        r.flipped = findAll(root, "clk-card").filter((n) => n.classList.contains("flip")).length;
      } else {
        const on = findAll(root, "clk-letter").filter((n) => n.classList.contains("on"));
        r.lit = on.map((n) => n.textContent).join("");
        r.dots = findAll(root, "clk-dot").filter((n) => n.classList.contains("on")).length;
        r.spoken = find(root, "clk-spoken").textContent;
        r.letters = findAll(root, "clk-letter").length;
      }
      results[name + (h12 ? "/12h" : "/24h")] = r;
    });
  });
  out.mount = results;
}

// ---- main.js: options, ready, and ticks aligned to the next second or minute.
function runMain(config, ctxExtra) {
  let hooks = null;
  const g = sandbox({ AiSignXPlugin: { register: (h) => { hooks = h; } } });
  loadAll(g);
  load(g, "main.js");
  let now = T + 400;  // 400 ms into the second
  const timers = [];
  let readyCalls = 0, fits = 0, nextId = 1;
  const ctx = Object.assign({
    root: g.document.root, config, tz: AMS, locale: "en-US",
    now: () => now,
    later: (fn, ms) => { const t = { id: nextId++, fn, ms, at: now + ms }; timers.push(t); return t.id; },
    every: () => { throw new Error("clock should not use every()"); },
    clear: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    ready: () => { readyCalls++; },
    fit: () => { fits++; return 1; },
  }, ctxExtra || {});
  hooks.init(ctx);
  const result = { body: g.document.body.className, ready: readyCalls, waits: [], pending: 0 };
  // Run the next three ticks; each must land just after a boundary.
  for (let i = 0; i < 3; i++) {
    const t = timers.shift();
    result.waits.push(t.ms);
    now = t.at;
    t.fn();
  }
  result.pending = timers.length;
  hooks.show(ctx);                 // catch up: still exactly one pending timer
  result.afterShow = timers.length;
  hooks.resume(ctx);
  result.afterResume = timers.length;
  result.fits = fits;
  result.time = (find(g.document.root, "clk-hm") || find(g.document.root, "clk-digital") ||
                 { textContent: "" }).textContent;
  result.resizeListener = typeof g.listeners.resize === "function";
  result.nowAtEnd = now;
  return result;
}
out.main = {
  seconds: runMain({ theme: "minimal-dark", format: "24h", seconds: true, show_date: true,
                     date_style: "long", timezone: AMS, label: "" }),
  minutes: runMain({ theme: "analog-classic", format: "12h", seconds: false, show_date: false,
                     date_style: "short", timezone: "", label: "Hall" }),
  unknownTheme: runMain({ theme: "nope", timezone: AMS }),
  emptyConfig: runMain({}),
};

process.stdout.write(JSON.stringify(out));
