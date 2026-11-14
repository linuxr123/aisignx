// Runs static/player/pop.js in a Node vm with a fake clock, storage and API, and prints
// JSON results of the proof-of-play queue scenarios checked by tests/test_pop.py.
// Usage: node pop_queue.js <path to pop.js>.
"use strict";
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf-8");
const tick = () => new Promise((r) => setImmediate(r));

function load() {
  const clock = { t: 1_800_000_000_000 };
  const data = {};
  const store = {
    get: (k) => (k in data ? JSON.parse(data[k]) : null),
    set: (k, v) => { data[k] = JSON.stringify(v); },
    remove: (k) => { delete data[k]; },
  };
  const env = { calls: [], reply: null, intervals: [], listeners: {} };
  const window = {
    AiSignX: {
      net: {
        store: { local: store },
        api(path, opts) {
          env.calls.push({ path, body: opts.body });
          return env.reply ? Promise.resolve(env.reply) : new Promise(() => {});
        },
      },
      emergency: { shownId: null, layout: null },
    },
    addEventListener(name, fn) { env.listeners[name] = fn; },
  };
  const FakeDate = function (ms) { return new Date(ms); };
  FakeDate.now = () => clock.t;
  const ctx = {
    window, Date: FakeDate, Math, JSON, Number, Array, Uint8Array, Object,
    performance: { now: () => clock.t },
    setInterval: (fn) => { env.intervals.push(fn); return env.intervals.length; },
    setTimeout: () => 0,
  };
  ctx.window.performance = ctx.performance;
  vm.runInNewContext(source, ctx);
  const pop = window.AiSignXPop;
  const em = window.AiSignX.emergency;
  const sampleAll = () => env.intervals[0]();  // the first interval is the sampler
  return { pop, clock, store, env, em, sampleAll, data };
}

function play(h, item, ms, ok = true) {
  h.pop.begin(item);
  h.clock.t += ms;
  h.pop.end(ok);
}

async function main() {
  const out = {};

  let h = load();
  play(h, { id: 5, media_id: 9 }, 10000);
  play(h, { id: 6, media_id: 9 }, 300);           // too short
  play(h, { id: 7, media_id: 9 }, 5000, false);   // failed slide
  play(h, { id: 8, media_id: null }, 5000);       // no media id
  play(h, { id: null, media_id: 3 }, 2000);       // no item id is fine
  out.basic = h.store.get("aisignx.plays");

  // Fullscreen emergency time is subtracted; a banner is not.
  h = load();
  h.pop.begin({ id: 1, media_id: 2 });
  h.clock.t += 2000;
  h.em.shownId = 1; h.em.layout = "fullscreen"; h.sampleAll();
  h.clock.t += 3000;
  h.em.shownId = null; h.em.layout = null; h.sampleAll();
  h.clock.t += 1000;
  h.pop.end(true);
  h.pop.begin({ id: 1, media_id: 2 });
  h.em.shownId = 2; h.em.layout = "banner"; h.sampleAll();
  h.clock.t += 4000;
  h.pop.end(true);
  out.covered = h.store.get("aisignx.plays").map((e) => e.duration_ms);

  // Flush: sent ids are removed only after an "ok"; a network error keeps them.
  h = load();
  for (let i = 0; i < 3; i++) play(h, { id: i, media_id: 1 }, 1000);
  h.env.reply = { kind: "retry", status: 0, data: null };
  h.pop.flush(); await tick(); await tick();
  out.afterError = h.pop.queued();
  h.env.reply = { kind: "ok", status: 200, data: { status: "ok", accepted: 3 } };
  h.pop.flush(); await tick(); await tick();
  out.afterOk = h.pop.queued();
  out.sentCounts = h.env.calls.map((c) => c.body.events.length);

  // Disabled: the queue is cleared and nothing is collected until retry_after_s has passed.
  h = load();
  play(h, { id: 1, media_id: 1 }, 1000);
  h.env.reply = { kind: "ok", status: 200, data: { status: "disabled", retry_after_s: 1800 } };
  h.pop.flush(); await tick(); await tick();
  out.disabledQueued = h.pop.queued();
  play(h, { id: 1, media_id: 1 }, 1000);
  out.whileOff = h.pop.queued();
  h.pop.flush(); await tick();
  out.callsWhileOff = h.env.calls.length;
  h.clock.t += 1801 * 1000;
  h.env.reply = { kind: "ok", status: 200, data: { status: "ok", accepted: 0 } };
  h.pop.flush(); await tick(); await tick();
  out.probe = h.env.calls.length === 2 ? h.env.calls[1].body.events.length : null;
  play(h, { id: 1, media_id: 1 }, 1000);
  out.afterProbe = h.pop.queued();

  // The queue is capped at 1000, dropping the oldest; at 50 events a flush starts.
  h = load();  // env.reply is null: the request never answers
  for (let i = 0; i < 1005; i++) play(h, { id: i, media_id: 1 }, 1000);
  const q = h.store.get("aisignx.plays");
  out.cap = { queued: h.pop.queued(), firstItem: q[0].item_id, calls: h.env.calls.length,
              batch: h.env.calls[0].body.events.length };

  // A page unload keeps the play on screen so far.
  h = load();
  h.pop.begin({ id: 4, media_id: 4 });
  h.clock.t += 2500;
  h.env.listeners.pagehide();
  out.pagehide = h.store.get("aisignx.plays").map((e) => e.duration_ms);

  process.stdout.write(JSON.stringify(out));
}

main();
