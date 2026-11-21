// Runs static/player/device.js in a Node vm and checks brightnessFor() and the dimmer layer. Usage: node device_brightness.js <path to device.js>.
"use strict";
const fs = require("fs");
const vm = require("vm");
const SRC = fs.readFileSync(process.argv[2], "utf-8");

function world(emergencyHidden) {
  const layers = [];
  const el = () => ({ hidden: true, textContent: "", querySelector: () => el(), classList: { toggle() {}, contains: () => false } });
  const emergency = { hidden: emergencyHidden, textContent: "", querySelector: () => el(), classList: { toggle() {}, contains: () => false } };
  const window = { AiSignX: { net: { shell: () => null }, render: { setVolume() {} }, emergency: {} }, addEventListener() {}, innerWidth: 1920, innerHeight: 1080,
    devicePixelRatio: 1, screen: { width: 1920, height: 1080 } };
  const body = { getAttribute: () => "", classList: { toggle() {}, contains: () => false }, appendChild: (n) => layers.push(n) };
  const document = { body, getElementById: (id) => (id === "emergency" ? emergency : el()),
    createElement: () => ({ style: {}, setAttribute() {} }) };
  const ctx = { window, navigator: { userAgent: "t" }, console, Promise, Math, Date, Intl, JSON, Object, Array, String, Number, RegExp, document,
    setTimeout: () => 0, clearTimeout() {}, TextEncoder };
  vm.runInNewContext(SRC, ctx);
  return { D: window.AiSignX.device, layers, emergency };
}

const out = {};
const { D, layers, emergency } = world(true);
const at = (iso) => Date.parse(iso);
const night = { brightness: 80, dim: { level: 30, from: "22:00", to: "06:00", tz: "America/New_York" } };
out.plain = [D.brightnessFor({}, 0), D.brightnessFor({ brightness: 55 }, 0), D.brightnessFor({ brightness: 5 }, 0), D.brightnessFor({ brightness: "x" }, 0), D.brightnessFor(null, 0)];
// 23:00 New York in winter is 04:00 UTC the next day; 12:00 NY is 17:00 UTC.
out.nightInside = D.brightnessFor(night, at("2026-01-15T04:00:00Z"));
out.nightMorning = D.brightnessFor(night, at("2026-01-15T10:00:00Z"));   // 05:00 NY: still inside (crosses midnight)
out.nightOutside = D.brightnessFor(night, at("2026-01-15T17:00:00Z"));   // 12:00 NY
out.edgeFrom = [D.brightnessFor(night, at("2026-01-15T02:59:00Z")), D.brightnessFor(night, at("2026-01-15T03:00:00Z"))];   // 21:59 / 22:00 NY
out.edgeTo = [D.brightnessFor(night, at("2026-01-15T10:59:00Z")), D.brightnessFor(night, at("2026-01-15T11:00:00Z"))];      // 05:59 / 06:00 NY
out.daytimeWindow = D.brightnessFor({ brightness: 100, dim: { level: 50, from: "12:00", to: "14:00", tz: "UTC" } }, at("2026-01-15T13:00:00Z"));
out.dimNeverBrighter = D.brightnessFor({ brightness: 20, dim: { level: 60, from: "00:00", to: "23:59", tz: "UTC" } }, at("2026-01-15T13:00:00Z"));
out.badDim = [D.brightnessFor({ brightness: 70, dim: { level: 30, from: "25:00", to: "06:00" } }, 0), D.brightnessFor({ brightness: 70, dim: { level: 30, from: "22:00", to: "22:00" } }, 0),
  D.brightnessFor({ brightness: 70, dim: "x" }, 0)];
// the layer: created only when dimming, hidden during an emergency
D.applySettings({ brightness: 100 });
out.noLayerAt100 = layers.length;
D.applySettings({ brightness: 60 });
out.layerOpacity = layers.length === 1 ? layers[0].style.opacity : null;
emergency.hidden = false;
D.applySettings({ brightness: 60 });
out.emergencyOpacity = layers[0].style.opacity;
emergency.hidden = true;
D.applySettings({ brightness: 100 });
out.backTo = layers[0].style.opacity;
out.config = [D.config().brightness, D.config().dim];
process.stdout.write(JSON.stringify(out));
