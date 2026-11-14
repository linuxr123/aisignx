// Runs static/player/pop.js in a Node vm with a fake clock and prints JSON results for the
// pause/skip scenarios checked by tests/test_player_controls.py.
// Usage: node pop_pause.js <path to pop.js>.
"use strict";
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf-8");

function load() {
  const clock = { t: 1_800_000_000_000 };
  const data = {};
  const store = {
    get: (k) => (k in data ? JSON.parse(data[k]) : null),
    set: (k, v) => { data[k] = JSON.stringify(v); },
    remove: (k) => { delete data[k]; },
  };
  const intervals = [];
  const window = {
    AiSignX: { net: { store: { local: store }, api: () => new Promise(() => {}) }, emergency: { shownId: null, layout: null } },
    addEventListener() {},
  };
  const FakeDate = function (ms) { return new Date(ms); };
  FakeDate.now = () => clock.t;
  const ctx = {
    window, Date: FakeDate, Math, JSON, Number, Array, Uint8Array, Object,
    performance: { now: () => clock.t },
    setInterval: (fn) => { intervals.push(fn); return intervals.length; },
    setTimeout: () => 0,
  };
  ctx.window.performance = ctx.performance;
  vm.runInNewContext(source, ctx);
  return {
    pop: window.AiSignXPop, clock, em: window.AiSignX.emergency,
    sample: () => intervals[0](),
    plays: () => (store.get("aisignx.plays") || []).map((e) => e.duration_ms),
  };
}

const out = {};
const item = { id: 1, media_id: 2 };

// 2 s play, 5 s paused, 3 s play: 5 s recorded. pause/resume twice in a row are no-ops.
let h = load();
h.pop.begin(item);
h.clock.t += 2000;
h.pop.pause(); h.pop.pause();
h.clock.t += 5000;
h.pop.resume(); h.pop.resume();
h.clock.t += 3000;
h.pop.end(true);
out.paused = h.plays();

// Ended while still paused: the open pause is subtracted too.
h = load();
h.pop.begin(item);
h.clock.t += 4000;
h.pop.pause();
h.clock.t += 6000;
h.pop.end(true);
out.endWhilePaused = h.plays();

// Fullscreen emergency during a pause is not subtracted twice:
// 1 s play, 2 s covered, pause (4 s, covered the whole time), resume (still covered 1 s), 2 s play.
h = load();
h.pop.begin(item);
h.clock.t += 1000;
h.em.shownId = 1; h.em.layout = "fullscreen"; h.sample();
h.clock.t += 2000;
h.pop.pause();
h.clock.t += 2000; h.sample();
h.clock.t += 2000; h.sample();
h.pop.resume();
h.clock.t += 1000;
h.em.shownId = null; h.em.layout = null; h.sample();
h.clock.t += 2000;
h.pop.end(true);
out.pausedAndCovered = h.plays();

// A skip after 300 ms is dropped; after 3 s it is recorded with its real duration.
h = load();
h.pop.begin(item);
h.clock.t += 300;
h.pop.end(true);
h.pop.begin(item);
h.clock.t += 3000;
h.pop.end(true);
out.skips = h.plays();

process.stdout.write(JSON.stringify(out));
