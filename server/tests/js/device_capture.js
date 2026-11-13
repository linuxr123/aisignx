// Runs static/player/device.js in a Node vm with a stub shell and canvas, and checks that a
// shell screenshot of a CSS-rotated screen is turned back upright before it is uploaded.
// Usage: node device_capture.js <path to device.js>. Prints JSON results.
"use strict";
const fs = require("fs");
const vm = require("vm");

function classList() {
  const set = new Set();
  return {
    toggle(c, on) { if (on) set.add(c); else set.delete(c); },
    contains(c) { return set.has(c); },
  };
}

function run(orientation, caps) {
  const drawn = [];
  const raw = { kind: "raw" };
  const shell = { info: { name: "electron", version: "3", caps }, capture: () => Promise.resolve(raw) };
  const canvas = () => {
    const c = { width: 0, height: 0, ops: [] };
    c.getContext = () => ({
      translate(x, y) { c.ops.push(["translate", x, y]); },
      rotate(a) { c.ops.push(["rotate", Math.round(a * 180 / Math.PI)]); },
      drawImage(img, x, y) { c.ops.push(["draw", x, y]); },
      fillRect() {}, set fillStyle(v) {},
    });
    c.toBlob = (cb) => { drawn.push({ w: c.width, h: c.height, ops: c.ops }); cb({ kind: "rotated" }); };
    return c;
  };
  const el = () => ({ hidden: true, textContent: "", querySelector: () => el(), classList: classList() });
  const body = { getAttribute: () => "", classList: classList() };
  const noop = () => {};
  const window = {
    AiSignX: { net: { shell: () => shell }, render: { setVolume: noop }, emergency: {} },
    addEventListener: noop,
    createImageBitmap: () => Promise.resolve({ width: 1920, height: 1080, close: noop }),
  };
  const ctx = {
    window, console, Promise, Math, Date, JSON, Object, Array, String, Number, RegExp,
    document: { body, getElementById: () => el(), createElement: canvas },
    setTimeout: () => 0, clearTimeout: noop,
  };
  vm.runInNewContext(fs.readFileSync(process.argv[2], "utf-8"), ctx);
  const D = window.AiSignX.device;
  D.applySettings({ orientation, volume: 100, show_offline_banner: true, allow_input: false });
  return D.capture().then((blob) => ({ blob: blob.kind, drawn }));
}

Promise.all([
  run(90, ["capture"]), run(180, ["capture"]), run(270, ["capture"]), run(0, ["capture"]),
  run(90, ["capture", "rotate"]),  // the shell rotates the device itself: already upright
]).then((results) => process.stdout.write(JSON.stringify(results)));
