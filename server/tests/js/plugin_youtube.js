// Runs static/player/plugins/youtube/native.js in a Node vm with a fake DOM, a fake clock and a
// fake youtube-nocookie frame, and prints JSON for tests/test_plugin_youtube.py.
// Usage: node plugin_youtube.js <path to native.js>.
"use strict";
const fs = require("fs");
const vm = require("vm");

const SRC = fs.readFileSync(process.argv[2], "utf-8");
const ORIGIN = "https://www.youtube-nocookie.com";
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function world(opts) {
  opts = opts || {};
  const clock = { t: 0, timers: [], seq: 0 };
  const add = (fn, ms) => { const id = ++clock.seq; clock.timers.push({ id, fn, at: clock.t + (ms || 0) }); return id; };
  const clear = (id) => { const i = clock.timers.findIndex((x) => x.id === id); if (i >= 0) clock.timers.splice(i, 1); };
  async function advance(ms) {
    const end = clock.t + ms;
    await flush();
    for (;;) {
      clock.timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const x = clock.timers[0];
      if (!x || x.at > end) break;
      clock.t = Math.max(clock.t, x.at);
      clock.timers.shift();
      x.fn();
      await flush();
    }
    clock.t = end;
    await flush();
  }
  const frames = [];
  function el(tag) {
    const attrs = {}, listeners = {};
    const e = {
      tagName: tag.toUpperCase(), style: {}, attrs, className: "", tabIndex: 0, _src: "", srcLog: [],
      setAttribute: (k, v) => { attrs[k] = String(v); }, getAttribute: (k) => (k in attrs ? attrs[k] : null),
      addEventListener: (n, fn) => { (listeners[n] = listeners[n] || []).push(fn); },
      fire: (n) => (listeners[n] || []).forEach((fn) => fn({})),
      get src() { return e._src; },
      set src(v) { e._src = v; e.srcLog.push(v); },
    };
    if (tag === "iframe") {
      e.posted = [];
      e.contentWindow = { postMessage: (msg, target) => e.posted.push({ msg: JSON.parse(msg), target }) };
      frames.push(e);
    }
    return e;
  }
  const winListeners = {};
  const win = {
    console: { info() {}, error() {}, log() {} }, URL,
    location: { href: "http://signage.local:5000/player/", origin: "http://signage.local:5000" },
    navigator: { onLine: opts.onLine !== false },
    document: { createElement: el },
    setTimeout: (fn, ms) => add(fn, ms), clearTimeout: clear,
    addEventListener: (n, fn) => { (winListeners[n] = winListeners[n] || []).push(fn); },
    removeEventListener: (n, fn) => { winListeners[n] = (winListeners[n] || []).filter((x) => x !== fn); },
    Math, JSON, Promise, Array, Number, String, Error, isNaN, Date: { now: () => clock.t },
  };
  win.window = win;
  let hostOff = false;
  win.AiSignX = { pluginHost: { setOffline: (on) => { hostOff = !!on; }, offset: () => 0 } };
  vm.createContext(win);
  vm.runInContext(SRC, win, { filename: "native.js" });
  const Y = win.AiSignX.pluginNative.youtube;
  // A message from `source` (default: the newest frame) with `origin` (default youtube-nocookie).
  function message(data, source, origin) {
    const f = frames[frames.length - 1];
    const e = { data: typeof data === "string" ? data : JSON.stringify(data),
      source: source === undefined ? f.contentWindow : source, origin: origin || ORIGIN };
    (winListeners.message || []).slice().forEach((fn) => fn(e));
  }
  const api = { cards: [], attached: [],
    attach: (slide, node) => api.attached.push(node), fitClass: () => "", volume: () => 0.5,
    prepCard: (slide, title, msg) => { api.cards.push([title, msg]); return Promise.resolve(); } };
  return { Y, win, clock, advance, frames, message, api, winListeners, setHostOffline: (on) => win.AiSignX.pluginHost.setOffline(on), hostOff: () => hostOff };
}

const EMBED = ORIGIN + "/embed/aaaaaaaaaaa?autoplay=1&mute=1&playsinline=1&controls=0&start=30&end=90&playlist=bbbbbbbbbbb%2Cccccccccccc";
function item(plugin, extra) {
  return Object.assign({ id: 1, media_id: 5, type: "plugin", url: EMBED + "#c=0123456789ab", muted: true, duration_ms: 300000,
    plugin: Object.assign({ key: "youtube", render: "native", ends_itself: true,
      embed: { video_ids: ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"], list: null, channel: null, shuffle: false,
        captions: false, play_to_end: true, live: false } }, plugin || {}) }, extra || {});
}
function ctxFor(w) {
  const got = [];
  return { got, ctx: { done: () => got.push("done@" + w.clock.t), fail: (m) => got.push("fail:" + m),
    note: (m) => got.push("note:" + m), degraded: () => got.push("degraded"), loop: () => false, every() {} } };
}
const outcome = (p) => p.then(() => "resolved", (e) => "rejected:" + (e && e.message));
const funcs = (f) => f.posted.filter((p) => p.msg.event === "command").map((p) => p.msg.func);

function utilCases() {
  const w = world();
  const U = w.Y.util;
  const seq = [0.99, 0.0];  // a fixed "random" order
  let i = 0;
  const rand = () => seq[i++ % seq.length];
  const bad = ["https://www.youtube.com/embed/aaaaaaaaaaa", "http://www.youtube-nocookie.com/embed/aaaaaaaaaaa",
    "https://www.youtube-nocookie.com.evil.example/embed/aaaaaaaaaaa", "https://evil.example/embed/aaaaaaaaaaa",
    "https://www.youtube-nocookie.com/watch?v=aaaaaaaaaaa", "https://user@www.youtube-nocookie.com/embed/aaaaaaaaaaa",
    "javascript:alert(1)", "not a url", ""];
  return {
    plain: U.buildSrc(item(), "http://signage.local:5000", Math.random),
    noOrigin: U.buildSrc(item(), "", Math.random),
    shuffled: U.buildSrc(item({ embed: Object.assign(item().plugin.embed, { shuffle: true }) }), "", rand),
    rejected: bad.map((u) => U.buildSrc(item({}, { url: u }), "", Math.random)),
    shuffleKeepsAll: U.shuffle([1, 2, 3, 4, 5], Math.random).sort(),
    facts: [
      U.facts({ event: "onReady" }),
      U.facts({ event: "onStateChange", info: 0 }),
      U.facts({ event: "onError", info: 150 }),
      U.facts({ event: "infoDelivery", info: { playerState: 1, playlistIndex: 2, playlist: ["a", "b", "c"] } }),
    ],
  };
}

function messageFilter() {
  const w = world();
  const U = w.Y.util;
  const fw = { postMessage() {} }, other = { postMessage() {} };
  const ok = JSON.stringify({ event: "onReady" });
  return {
    ok: U.readMessage({ source: fw, origin: ORIGIN, data: ok }, fw),
    object: U.readMessage({ source: fw, origin: ORIGIN, data: { event: "onReady" } }, fw),
    foreignSource: U.readMessage({ source: other, origin: ORIGIN, data: ok }, fw),
    youtubeCom: U.readMessage({ source: fw, origin: "https://www.youtube.com", data: ok }, fw),
    nullOrigin: U.readMessage({ source: fw, origin: "null", data: ok }, fw),
    garbage: U.readMessage({ source: fw, origin: ORIGIN, data: "{nope" }, fw),
    noEvent: U.readMessage({ source: fw, origin: ORIGIN, data: "{\"x\":1}" }, fw),
    huge: U.readMessage({ source: fw, origin: ORIGIN, data: JSON.stringify({ event: "x", pad: "a".repeat(70000) }) }, fw),
  };
}

async function frameAndBridge() {
  const w = world();
  const slide = { item: item() };
  const p = outcome(w.Y.prepare(slide, w.api));
  const f = w.frames[0];
  const out = { attrs: f.attrs, className: f.className, tabIndex: f.tabIndex, attached: w.api.attached.length === 1 };
  out.src = f.src;
  f.fire("load");
  out.listening = f.posted.filter((x) => x.msg.event === "listening").length;
  out.targets = Array.from(new Set(f.posted.map((x) => x.target)));
  out.subscribed = funcs(f).filter((x) => x === "addEventListener").length > 0;
  w.message({ event: "onReady" }, null);                          // not from the frame: ignored
  w.message({ event: "onReady" }, undefined, "https://evil.example");  // wrong origin: ignored
  await w.advance(10);
  out.beforeReady = await Promise.race([p, new Promise((r) => setImmediate(() => r("pending")))]);
  w.message({ event: "onReady" });
  out.prepared = await p;
  f.posted.length = 0;
  const c = ctxFor(w);
  w.Y.run(slide, c.ctx);
  out.runCommands = funcs(f);
  w.message({ event: "infoDelivery", info: { playerState: 1, playlistIndex: 0, playlist: ["a", "b", "c"] } });
  w.message({ event: "onStateChange", info: 0 });                // first video ended, more to come
  w.message({ event: "infoDelivery", info: { playerState: 1, playlistIndex: 2 } });
  f.posted.length = 0;
  w.Y.pause(slide); w.Y.resume(slide); w.Y.setVolume(slide, 0.25); w.Y.setMuted(slide, true); w.Y.setMuted(slide, false);
  out.controls = f.posted.map((x) => [x.msg.func, x.msg.args]);
  w.message({ event: "onStateChange", info: 0 });                // the last one ended
  out.got = c.got.slice();
  w.Y.teardown(slide);
  out.blank = f.src;
  out.listeners = (w.winListeners.message || []).length;
  f.posted.length = 0;
  w.Y.pause(slide);
  out.afterTeardown = f.posted.length;
  return out;
}

async function endings() {
  const run = async (embed, msgs, wait) => {
    const w = world();
    const slide = { item: item({ embed: Object.assign(item().plugin.embed, embed) }) };
    const p = w.Y.prepare(slide, w.api);
    w.message({ event: "onReady" });
    await p;
    const c = ctxFor(w);
    w.Y.run(slide, c.ctx);
    w.message({ event: "onStateChange", info: 1 });
    for (const m of msgs) { w.message(m); await w.advance(100); }
    await w.advance(wait || 0);
    return c.got;
  };
  const ended = { event: "onStateChange", info: 0 };
  return {
    loopOff: await run({ play_to_end: false }, [ended], 5000),
    live: await run({ live: true, play_to_end: false, video_ids: ["aaaaaaaaaaa"] }, [ended], 5000),
    single: await run({ video_ids: ["aaaaaaaaaaa"] }, [ended]),
    reportedTwice: await run({ video_ids: ["aaaaaaaaaaa"] },
      [{ event: "infoDelivery", info: { playerState: 0 } }, ended], 3000),
    listUnknownEnds: await run({ list: "PLabcdefghij", video_ids: [] }, [ended], 3000),
    listNextStarts: await run({ list: "PLabcdefghij", video_ids: [] },
      [ended, { event: "onStateChange", info: -1 }, { event: "onStateChange", info: 1 }], 5000),
    errorAfterRun: await run({}, [{ event: "onError", info: 150 }]),
    unknownError: await run({}, [{ event: "onError", info: 42 }]),
  };
}

async function failures() {
  const w = world();
  const out = {};
  out.badUrl = await outcome(w.Y.prepare({ item: item({}, { url: "https://www.youtube.com/embed/aaaaaaaaaaa" }) }, w.api));
  out.framesForBadUrl = w.frames.length;
  const slide = { item: item() };
  const p = outcome(w.Y.prepare(slide, w.api));
  w.message({ event: "onError", info: 101 });
  out.errorBeforeReady = await p;
  // Prepared, but the bridge never answers: settles after the load grace, reloads with autoplay=1.
  const w2 = world();
  const s2 = { item: item() };
  const p2 = outcome(w2.Y.prepare(s2, w2.api));
  w2.frames[0].fire("load");
  await w2.advance(1600);
  out.silentPrepared = await p2;
  const c = ctxFor(w2);
  w2.Y.run(s2, c.ctx);
  await w2.advance(3100);
  out.reloadSrc = w2.frames[0].src;
  out.reloadNote = c.got.slice();
  await w2.advance(3100);  // still silent: the slide must not hold the playlist
  out.silentGaveUp = c.got.slice();
  // Answers but never plays: asks again, then mutes.
  const w3 = world();
  const s3 = { item: item({}, { muted: false }) };
  const p3 = w3.Y.prepare(s3, w3.api);
  w3.message({ event: "onReady" });
  await p3;
  const c3 = ctxFor(w3);
  w3.Y.run(s3, c3.ctx);
  await w3.advance(6100);
  out.autoMuted = { note: c3.got, mute: funcs(w3.frames[0]).includes("mute") };
  return out;
}

async function captionCommands() {
  const out = {};
  for (const on of [false, true]) {
    const w = world();
    const slide = { item: item({ embed: { video_ids: ["aaaaaaaaaaa"], list: null, channel: null, shuffle: false,
      captions: on, play_to_end: true, live: false } }) };
    const p = w.Y.prepare(slide, w.api);
    w.message({ event: "onReady" });
    await p;
    w.frames[0].posted.length = 0;
    w.Y.run(slide, ctxFor(w).ctx);
    w.message({ event: "onStateChange", info: 1 });  // playing: a new video loaded its modules
    out[on ? "on" : "off"] = w.frames[0].posted.filter((x) => x.msg.event === "command" && x.msg.func === "unloadModule")
      .map((x) => x.msg.args[0]);
  }
  return out;
}

async function sync() {
  const out = {};
  async function player(embed, elapsedMs) {
    const w = world();
    const slide = { item: item({ embed: Object.assign({ video_ids: ["aaaaaaaaaaa"], list: null, channel: null, shuffle: false,
      captions: false, play_to_end: false, live: false, start: null, end: null }, embed || {}) }) };
    const p = w.Y.prepare(slide, w.api);
    w.message({ event: "onReady" });
    await p;
    const c = ctxFor(w);
    let el = elapsedMs;
    c.ctx.syncElapsed = () => el;
    w.Y.run(slide, c.ctx);
    w.message({ event: "onStateChange", info: 1 });           // playing
    w.frames[0].posted.length = 0;
    const seeks = () => w.frames[0].posted.filter((x) => x.msg.func === "seekTo").map((x) => x.msg.args[0]);
    const tell = (time, length) => w.message({ event: "infoDelivery", info: { currentTime: time, duration: length || 212 } });
    return { w, seeks, tell, set: (v) => { el = v; } };
  }
  // Joined 95 s into the slide: the video is at 3 s, so it is sent to 95 s (a little ahead).
  let a = await player({}, 95000);
  a.tell(3.2);
  out.joined = a.seeks();
  a.tell(4.0);                                             // within the gap: no second seek yet
  out.noSecond = a.seeks().length;
  // Close enough: no seek. Drifted again after the gap: another one.
  await a.w.advance(5000);
  a.set(100000); a.tell(100.4);
  out.close = a.seeks().length;
  a.set(120000); a.tell(118.9);
  out.drifted = a.seeks().length;
  // The video repeats: 213 s into a 212 s video is 1 s in; at 211.5 s it is just before the loop point.
  a = await player({}, 213000);
  a.tell(211.5);
  out.wrap = a.seeks();
  // A start offset counts: start=30, 10 s into the slide means 40 s in the video.
  a = await player({ start: 30 }, 10000);
  a.tell(30.1);
  out.start = a.seeks();
  // Not synchronized (no position), a list, a playlist and a live stream are left alone.
  a = await player({}, null); a.tell(3.2);
  const b = await player({ video_ids: ["aaaaaaaaaaa", "bbbbbbbbbbb"] }, 95000); b.tell(3.2);
  const c2 = await player({ list: "PLx" }, 95000); c2.tell(3.2);
  const d = await player({ live: true }, 95000); d.tell(3.2);
  out.left = [a.seeks().length, b.seeks().length, c2.seeks().length, d.seeks().length];
  // A clip that loops (start 30, end 90) stays in its clip: YouTube ignores the end time with loop=1.
  const lp = async (embed, times) => {
    const x = await player(Object.assign({ start: 30, end: 90, play_to_end: false }, embed), null);
    times.forEach((t) => x.tell(t));
    return x.seeks();
  };
  out.loopEnd = await lp({}, [60]);                          // inside the clip: nothing
  out.loopAtEnd = await lp({}, [89.9]);                      // at the end time: back to the start
  out.loopPast = await lp({}, [101]);                        // YouTube played on past it: back to the start
  out.loopRestart = await lp({}, [2]);                       // YouTube looped to 0: to the start time
  out.loopOnce = await lp({}, [89.9, 90.4]);                 // not twice within a second
  out.noLoopGuard = [await lp({ play_to_end: true }, [95]), await lp({ video_ids: ["a", "b"] }, [95]), await lp({ start: null, end: null }, [95])];
  return out;
}

async function offline() {
  // The AiSignX server being unreachable (the player's offline flag) must NOT stop YouTube:
  // the embed comes from YouTube directly, so the frame is still created.
  const w = world();
  w.setHostOffline(true);
  const slide = { item: item() };
  const pending = outcome(w.Y.prepare(slide, w.api));
  w.message({ event: "onReady" });
  const res = await pending;
  const c = ctxFor(w);
  w.Y.run(slide, c.ctx);
  w.Y.teardown(slide);
  const w2 = world({ onLine: false });
  const r2 = await outcome(w2.Y.prepare({ item: item() }, w2.api));
  return { res, cards: w.api.cards, frames: w.frames.length, got: c.got, hostSaw: w.hostOff(), navigator: [r2, w2.api.cards, w2.frames.length] };
}

(async () => {
  const out = { util: utilCases(), filter: messageFilter(), bridge: await frameAndBridge(), endings: await endings(),
    failures: await failures(), offline: await offline(), captions: await captionCommands(), sync: await sync() };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
