// Runs static/player/plugins/host.js (and sdk.js, for the end-to-end cases) in Node vm
// contexts with a fake clock, fake storage, a fake iframe and Node's real MessageChannel,
// and prints JSON results for tests/test_plugins_host.py.
// Usage: node plugin_host.js <static/player/plugins directory>.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const DIR = process.argv[2];
const HOST = fs.readFileSync(path.join(DIR, "host.js"), "utf-8");
const SDK = fs.readFileSync(path.join(DIR, "sdk.js"), "utf-8");
const tick = () => new Promise((r) => setImmediate(r));
async function flush(n) { for (let i = 0; i < (n || 8); i++) await tick(); }
const clone = (x) => (x === undefined ? null : JSON.parse(JSON.stringify(x)));
const quiet = { info() {}, error() {}, log() {}, warn() {} };
const DATA = "/api/player/v1/plugins/7/data";
const ASSET = "a".repeat(32);

function Clock() {
  const c = { t: 1700000000000, timers: [], seq: 0 };
  const add = (fn, ms, repeat) => {
    const id = ++c.seq;
    c.timers.push({ id, fn, at: c.t + Math.max(0, Number(ms) || 0), ms: Math.max(1, Number(ms) || 0), repeat });
    return id;
  };
  const clear = (id) => { const i = c.timers.findIndex((x) => x.id === id); if (i >= 0) c.timers.splice(i, 1); };
  c.api = {
    setTimeout: (fn, ms) => add(fn, ms, false), setInterval: (fn, ms) => add(fn, ms, true),
    clearTimeout: clear, clearInterval: clear,
  };
  c.advance = async (ms) => {
    const end = c.t + ms;
    await flush();
    for (;;) {
      c.timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const x = c.timers[0];
      if (!x || x.at > end) break;
      c.t = Math.max(c.t, x.at);
      if (x.repeat) x.at += x.ms; else c.timers.shift();
      x.fn();
      await flush();
    }
    c.t = end;
    await flush();
  };
  return c;
}

function Storage() {
  const m = new Map();
  return {
    map: m,
    get length() { return m.size; },
    key: (i) => Array.from(m.keys())[i] || null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
  };
}

function resp(status, body, headers) {
  headers = headers || {};
  return {
    status, ok: status >= 200 && status < 300,
    headers: { get: (n) => (n in headers ? headers[n] : null) },
    json: async () => clone(body),
    arrayBuffer: async () => new Uint8Array(body).buffer,
  };
}
function dataBody(data, extra) {
  return Object.assign({ v: 1, fetched_at_ms: 1700000000000, stale: false, error: null, refresh_s: 60,
    data: data, assets: [] }, extra || {});
}

// A world: one host page. opts: {shell, files}
function World(opts) {
  opts = opts || {};
  const clock = Clock();
  const listeners = [];
  const storage = Storage();
  const warmed = [];
  const tpl = { content: { querySelectorAll: () => (opts.files || []).map((h) => ({ getAttribute: () => h })) } };
  const win = Object.assign({
    console: quiet, localStorage: storage, MessageChannel,
    addEventListener: (t, fn) => { if (t === "message") listeners.push(fn); },
    document: { getElementById: (id) => (id === "plugin-files" ? tpl : null) },
    AiSignX: { net: { shell: () => opts.shell || null } },
    fetch: (u) => { warmed.push(u); return Promise.resolve(resp(200, [1])); },
    __now: () => clock.t,
  }, clock.api);
  vm.createContext(win);
  win.window = win;
  vm.runInContext("Date.now = function () { return __now(); };", win);
  vm.runInContext(HOST, win, { filename: "host.js" });
  const w = { clock, win, storage, warmed, PH: win.AiSignX.pluginHost };
  w.dispatch = (data, source, origin) => listeners.forEach((fn) => fn({ data, source, origin }));
  // fetcher(responder): io.fetch that records calls; responder(call) -> Promise<resp>
  w.fetcher = (responder) => {
    const calls = [];
    const fetch = (url, init) => {
      const call = { url, at: clock.t, inm: (init.headers || {})["If-None-Match"] || null };
      calls.push(call);
      return responder(call, calls.length - 1);
    };
    return { calls, fetch };
  };
  return w;
}

// A fake iframe whose plugin side is driven by hand: the transferred port is kept.
function FakeFrame(w) {
  const fw = { inits: 0, port: null, msgs: [], closed: false };
  fw.postMessage = (m, origin, transfer) => {
    if (m && m.t === "aisignx:init") {
      fw.inits++;
      fw.port = transfer[0];
      fw.port.on("close", () => { fw.closed = true; });
      fw.port.onmessage = (e) => fw.msgs.push(clone(e.data));
    }
  };
  const frame = { contentWindow: fw, src: "" };
  frame.hello = (source, origin, v) => w.dispatch({ t: "aisignx:hello", v: v || 1 }, source || fw, origin || "null");
  frame.send = (m) => fw.port.postMessage(m);
  frame.types = () => fw.msgs.map((m) => m.t);
  return frame;
}

// A frame running the real sdk.js plus a recording plugin.
function SdkFrame(w, ready) {
  const rec = [];
  const sdkListeners = [];
  const fw = {};
  const parent = { postMessage: (m) => setImmediate(() => w.dispatch(clone(m), fw, "null")) };
  fw.postMessage = (m, origin, transfer) => setImmediate(() => sdkListeners.forEach((fn) =>
    fn({ data: m, source: parent, ports: transfer || [] })));
  const el = (tag) => ({ tagName: tag, className: "", textContent: "", style: {}, children: [], parentNode: null,
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; } });
  const root = el("div"), body = el("body");
  const win = Object.assign({
    console: quiet, parent, URL, Blob,
    addEventListener: (t, fn) => { if (t === "message") sdkListeners.push(fn); },
    document: { body, getElementById: (id) => (id === "root" ? root : null), createElement: el },
    __rec: (name, x) => rec.push([name, clone(x)]),
    __ready: ready !== false,
    __now: () => w.clock.t,
  }, w.clock.api);
  vm.createContext(win);
  win.window = win;
  vm.runInContext("Date.now = function () { return __now(); };", win);
  vm.runInContext(SDK, win, { filename: "sdk.js" });
  vm.runInContext(`
    var ticks = 0;
    AiSignXPlugin.register({
      init: function (ctx) {
        window.ctx = ctx;
        __rec("init", { data: ctx.data, warming: ctx.warming, stale: ctx.stale, offline: ctx.offline,
          config: ctx.config, tz: ctx.tz, offset: ctx.now() - Date.now(), key: ctx.key });
        ctx.every(function () { ticks++; }, 1000);
        window.alignedHits = [];
        window.alignedId = ctx.aligned(function () { window.alignedHits.push(ctx.now()); }, 1000);
        if (__ready) ctx.ready();
      },
      data: function (ctx) { __rec("data", { data: ctx.data, stale: ctx.stale }); },
      show: function () { __rec("show"); }, pause: function () { __rec("pause"); },
      resume: function () { __rec("resume"); }, net: function (ctx) { __rec("net", ctx.offline); },
      destroy: function () { __rec("destroy"); },
    });`, win);
  return { frame: { contentWindow: fw, src: "" }, rec, win, names: () => rec.map((r) => r[0]) };
}

function item(extra, plugin) {
  return Object.assign({ id: 41, media_id: 7, type: "plugin", url: "/static/player/plugins/echo/index.html#c=abc",
    duration_ms: 15000,
    plugin: Object.assign({ key: "echo", render: "frame", config: { word: "hi" }, data: null, refresh_s: 60,
      ends_itself: false, locale: "", attribution: "" }, plugin || {}) }, extra || {});
}
function settled(p) {
  const s = { state: "pending", error: null };
  p.then(() => { s.state = "resolved"; }, (e) => { s.state = "rejected"; s.error = String(e && e.message); });
  return s;
}

async function handshake() {
  const w = World(), f = FakeFrame(w);
  const h = w.PH.create(f, item(), {});
  const st = settled(h.ready);
  const out = {};
  f.hello({}, "null"); out.foreign = f.contentWindow.inits;
  f.hello(null, "https://evil.example"); out.badOrigin = f.contentWindow.inits;
  f.hello(null, "null", 2); out.badVersion = f.contentWindow.inits;
  f.hello(); out.accepted = f.contentWindow.inits;
  f.hello(); out.second = f.contentWindow.inits;
  await flush();
  out.init = f.contentWindow.msgs[0];
  w.dispatch({ t: "ready" }, f.contentWindow, "null");  // not over the port: ignored
  await flush();
  out.readyViaWindow = st.state;
  f.send({ t: "ready" });
  await flush();
  out.readyViaPort = st.state;
  return out;
}

async function readyTimeout() {
  const w = World(), f = FakeFrame(w);
  const st = settled(w.PH.create(f, item(), { readyMs: 8000 }).ready);
  await w.clock.advance(7999);
  const before = st.state;
  await w.clock.advance(1);
  return { before, after: st.state, error: st.error };
}

async function fatalBeforeReady() {
  const w = World(), f = FakeFrame(w);
  const st = settled(w.PH.create(f, item(), {}).ready);
  f.hello(); await flush();
  f.send({ t: "error", message: "boom", fatal: true }); await flush();
  return { state: st.state, error: st.error };
}

async function warming() {
  const w = World(), f = FakeFrame(w);
  const io = w.fetcher((call, n) => Promise.resolve(n === 0 ? resp(503, { error: "warming" }, { "Retry-After": "7" })
    : resp(200, dataBody({ n: n }), { ETag: '"e1"' })));
  const h = w.PH.create(f, item({}, { data: DATA }), { fetch: io.fetch });
  f.hello();
  await w.clock.advance(100);
  const init = f.contentWindow.msgs[0];
  await w.clock.advance(7000);
  const data = f.contentWindow.msgs[1];
  h.destroy();
  return { init: { t: init.t, data: init.data, warming: init.warming }, data, at: io.calls.map((c) => c.at - 1700000000000) };
}

async function retryClamp(header) {
  const w = World(), f = FakeFrame(w);
  const io = w.fetcher(() => Promise.resolve(resp(503, { error: "upstream_unavailable" }, { "Retry-After": header })));
  const h = w.PH.create(f, item({}, { data: DATA }), { fetch: io.fetch });
  await w.clock.advance(130000);
  h.destroy();
  return io.calls.slice(0, 3).map((c) => c.at - 1700000000000);
}

async function cadence() {
  const w = World(), f = FakeFrame(w);
  const io = w.fetcher((call, n) => Promise.resolve(n === 0 ? resp(200, dataBody({ n: 0 }), { ETag: '"e0"' })
    : resp(304, null)));
  const h = w.PH.create(f, item({}, { data: DATA }), { fetch: io.fetch });
  f.hello();
  await w.clock.advance(200000);  // prepared but never shown: no refresh
  const hidden = io.calls.length;
  h.show();
  await w.clock.advance(180000);
  const msgs = f.contentWindow.msgs.map((m) => m.t);
  h.destroy();
  return { hidden, shown: io.calls.map((c) => c.at - 1700000000000), inm: io.calls.map((c) => c.inm), msgs };
}

async function lru() {
  const w = World();
  const big = "x".repeat(200 * 1024);
  for (let i = 1; i <= 8; i++) {
    const io = w.fetcher(() => Promise.resolve(resp(200, dataBody({ s: big }))));
    w.PH.create(FakeFrame(w), item({ media_id: i }, { data: `/api/player/v1/plugins/${i}/data` }), { fetch: io.fetch }).destroy();
    await w.clock.advance(1000);
  }
  const keys = () => Array.from(w.storage.map.keys()).filter((k) => /^aisignx\.plugin\.\d+$/.test(k)).sort();
  const afterEight = keys();
  let total = 0;
  afterEight.forEach((k) => { total += w.storage.map.get(k).length; });
  const io = w.fetcher(() => Promise.resolve(resp(200, dataBody({ s: "y".repeat(300 * 1024) }))));
  w.PH.create(FakeFrame(w), item({ media_id: 8 }, { data: "/api/player/v1/plugins/8/data" }), { fetch: io.fetch }).destroy();
  await w.clock.advance(1000);
  // persist: false (the admin preview) never writes
  const io2 = w.fetcher(() => Promise.resolve(resp(200, dataBody({ s: "z" }))));
  w.PH.create(FakeFrame(w), item({ media_id: 99 }, { data: "/x/99/data" }), { fetch: io2.fetch, persist: false }).destroy();
  await w.clock.advance(1000);
  return { afterEight, total, afterHuge: keys() };
}

async function offline() {
  const w = World();
  const ok = w.fetcher(() => Promise.resolve(resp(200, dataBody({ temp: 21 }), { ETag: '"t"' })));
  w.PH.create(FakeFrame(w), item({}, { data: DATA }), { fetch: ok.fetch }).destroy();
  await w.clock.advance(1000);
  w.PH.setOffline(true);
  const down = w.fetcher(() => Promise.reject(new TypeError("Failed to fetch")));
  const f1 = FakeFrame(w);
  w.PH.create(f1, item({}, { data: DATA }), { fetch: down.fetch });
  f1.hello();
  await w.clock.advance(100);
  // another config of the same media (other #c=): the saved copy does not apply
  const f2 = FakeFrame(w);
  w.PH.create(f2, item({ url: "/static/player/plugins/echo/index.html#c=new" }, { data: DATA }), { fetch: down.fetch });
  f2.hello();
  await w.clock.advance(100);
  // no answer at all: init waits 2.5 s, then starts warming without data
  const f3 = FakeFrame(w);
  const hang = w.fetcher(() => new Promise(() => {}));
  w.PH.create(f3, item({ media_id: 50 }, { data: "/api/player/v1/plugins/50/data" }), { fetch: hang.fetch });
  f3.hello();
  await w.clock.advance(2400);
  const early = f3.contentWindow.msgs.length;
  await w.clock.advance(200);
  const pick = (m) => m && { t: m.t, data: m.data, offline: m.offline, warming: m.warming };
  return { saved: pick(f1.contentWindow.msgs[0]), otherConfig: pick(f2.contentWindow.msgs[0]), early,
    hung: pick(f3.contentWindow.msgs[0]), inm: down.calls[0].inm };
}

async function lifecycle(endsItself) {
  const w = World(), f = FakeFrame(w);
  const h = w.PH.create(f, item({}, { ends_itself: endsItself }), {});
  const events = [];
  h.on("complete", () => events.push("complete"));
  h.on("error", (msg, fatal) => events.push("error:" + msg + ":" + fatal));
  h.on("degraded", () => events.push("degraded"));
  f.hello(); await flush();
  f.send({ t: "ready" }); await flush();
  h.show(); h.show();
  h.pause(); h.pause(); h.resume(); h.resume();
  w.PH.setOffline(true); w.PH.setOffline(true); w.PH.setOffline(false);
  f.send({ t: "complete" }); f.send({ t: "error", message: "soft", fatal: false }); f.send({ t: "degraded" });
  await flush();
  const degraded = h.degraded();
  h.destroy();
  await flush();
  f.send({ t: "complete" });
  await flush();
  return { msgs: f.contentWindow.msgs.map((m) => (m.t === "net" ? "net:" + m.offline : m.t)), events, degraded,
    closed: f.contentWindow.closed };
}

async function endToEnd() {
  const w = World();
  w.PH.setServerTime(1700000000000 + 5000);  // server is 5 s ahead
  const io = w.fetcher((call) => Promise.resolve(call.url.indexOf("/assets/") >= 0 ? resp(200, [137, 80, 78, 71])
    : resp(200, dataBody({ word: "hello" }, { assets: [{ id: ASSET, mime: "image/png", size: 4 }] }))));
  const s = SdkFrame(w);
  const h = w.PH.create(s.frame, item({}, { data: DATA }), { fetch: io.fetch });
  const st = settled(h.ready);
  await w.clock.advance(2000);  // hello goes out at once
  const readyState = st.state;
  h.show(); h.pause(); h.resume(); w.PH.setOffline(true);
  await flush();
  const url = await new Promise((res) => {
    s.win.ctx.asset(ASSET).then(res, (e) => res("error:" + e.message));
    w.clock.advance(10);
  });
  const again = await new Promise((res) => { s.win.ctx.asset(ASSET).then(res, () => res("error")); w.clock.advance(10); });
  const unknown = await new Promise((res) => {
    s.win.ctx.asset("b".repeat(32)).then(() => res("resolved"), (e) => res("rejected:" + e.message));
    w.clock.advance(10);
  });
  const assetFetches = io.calls.filter((c) => c.url.indexOf("/assets/") >= 0).map((c) => c.url);
  const ticksBefore = s.win.ticks;
  const hitsBefore = Array.from(s.win.alignedHits);
  h.destroy();
  await w.clock.advance(5000);
  const aligned = { hits: hitsBefore, after: s.win.alignedHits.length - hitsBefore.length };
  return { aligned, readyState, rec: s.rec, url: typeof url === "string" && url.slice(0, 5), same: url === again, unknown,
    assetFetches, ticksBefore, ticksAfter: s.win.ticks };
}

async function sdkFallback() {
  const w = World();
  const s = SdkFrame(w, false);
  const h = w.PH.create(s.frame, item(), {});
  const st = settled(h.ready);
  const events = [];
  h.on("degraded", () => events.push("degraded"));
  await w.clock.advance(100);
  vm.runInContext("ctx.fallback('Echo', 'This screen is offline');", s.win);
  await flush();
  return { state: st.state, degraded: h.degraded(), card: s.win.document.body.children.map((c) => c.className) };
}

async function warm() {
  const files = ["/static/player/plugins/sdk.js", "/static/player/plugins/echo/index.html"];
  const w = World({ shell: { info: {} }, files });
  await w.clock.advance(6000);
  w.PH.warm();
  await flush();
  const browser = World({ files });
  await browser.clock.advance(6000);
  return { shell: w.warmed, browser: browser.warmed };
}

(async () => {
  const out = {
    handshake: await handshake(),
    readyTimeout: await readyTimeout(),
    fatalBeforeReady: await fatalBeforeReady(),
    warming: await warming(),
    clampLow: await retryClamp("1"),
    clampHigh: await retryClamp("300"),
    cadence: await cadence(),
    lru: await lru(),
    offline: await offline(),
    lifecycle: await lifecycle(false),
    lifecycleEnds: await lifecycle(true),
    e2e: await endToEnd(),
    fallback: await sdkFallback(),
    warm: await warm(),
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
