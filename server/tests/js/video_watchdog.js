// Drives render.js's pure watchStep() (the frozen-video watchdog) with a fake clock.
// Usage: node video_watchdog.js <static/player directory>
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const win = { };
win.window = win;
vm.createContext(win);
vm.runInContext(fs.readFileSync(path.join(process.argv[2], "render.js"), "utf-8"), win, { filename: "render.js" });
const step = win.AiSignX.render.watchStep;
assert.strictEqual(typeof step, "function");

const fresh = () => ({ lastTime: -1, lastAt: 0, nudgeAt: 0, seekAt: 0, reseeked: false });
const playing = (t) => ({ paused: false, ended: false, seeking: false, time: t });

// A moving video is healthy for as long as you like.
let st = fresh();
for (let s = 1; s <= 30; s++) assert.strictEqual(step(st, playing(s), s * 1000, false), "ok");

// Time standing still: nudged after 3 s, again every 2 s, failed after 10 s.
st = fresh(); step(st, playing(5), 0, false);
assert.strictEqual(step(st, playing(5), 2000, false), "ok");
assert.strictEqual(step(st, playing(5), 3500, false), "play");
assert.strictEqual(step(st, playing(5), 4500, false), "ok");          // not again within 2 s
assert.strictEqual(step(st, playing(5), 5600, false), "play");
assert.strictEqual(step(st, playing(5), 10600, false), "fail");

// Paused when it should be playing (the first-showing freeze): nudged quickly, failed after 10 s.
st = fresh(); step(st, { paused: true, ended: false, seeking: false, time: 3 }, 0, false);
assert.strictEqual(step(st, { paused: true, ended: false, seeking: false, time: 3 }, 1000, false), "ok");
assert.strictEqual(step(st, { paused: true, ended: false, seeking: false, time: 3 }, 2500, false), "play");
assert.strictEqual(step(st, { paused: true, ended: false, seeking: false, time: 3 }, 11000, false), "fail");

// A video that starts moving again is healthy again (and the nudge timer resets).
st = fresh(); step(st, playing(5), 0, false);
assert.strictEqual(step(st, playing(5), 3500, false), "play");
assert.strictEqual(step(st, playing(6), 4500, false), "ok");
assert.strictEqual(step(st, playing(6), 7600, false), "play");

// Deliberately held by the viewer: never nudged, never failed, however long.
st = fresh(); step(st, playing(5), 0, true);
for (let s = 1; s <= 60; s++) assert.strictEqual(step(st, { paused: true, ended: false, seeking: false, time: 5 }, s * 1000, true), "ok");
// ... and the clock starts fresh when the hold ends.
assert.strictEqual(step(st, { paused: true, ended: false, seeking: false, time: 5 }, 61000, false), "ok");

// An ended video (a synced video resting on its last frame) is fine.
st = fresh(); step(st, playing(5), 0, false);
for (let s = 1; s <= 30; s++) assert.strictEqual(step(st, { paused: true, ended: true, seeking: false, time: 49.9 }, s * 1000, false), "ok");

// Seeking: fine for a while, asked again once after 6 s, failed after 20 s.
const seeking = (t) => ({ paused: false, ended: false, seeking: true, time: t });
st = fresh(); step(st, playing(2), 0, false);
assert.strictEqual(step(st, seeking(30), 1000, false), "ok");
assert.strictEqual(step(st, seeking(30), 5000, false), "ok");
assert.strictEqual(step(st, seeking(30), 7500, false), "reseek");
assert.strictEqual(step(st, seeking(30), 9000, false), "ok");          // only once
assert.strictEqual(step(st, seeking(30), 21500, false), "fail");
// A seek that finishes resets everything.
st = fresh(); step(st, playing(2), 0, false);
step(st, seeking(30), 1000, false);
assert.strictEqual(step(st, playing(30), 2000, false), "ok");
assert.strictEqual(st.seekAt, 0);
console.log("OK");
