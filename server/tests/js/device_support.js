// Runs static/player/device.js in a Node vm with a fake navigator.mediaCapabilities and checks
// which videos end up in the diagnostics. Usage: node device_support.js <path to device.js>.
"use strict";
const fs = require("fs");
const vm = require("vm");

const SRC = fs.readFileSync(process.argv[2], "utf-8");
const flush = () => new Promise((r) => setImmediate(r));

function world(decide) {
  const asked = [];
  const el = () => ({ hidden: true, textContent: "", querySelector: () => el(), classList: { toggle() {}, contains: () => false } });
  const window = {
    AiSignX: { net: { shell: () => null }, render: { setVolume() {} }, emergency: {} },
    addEventListener() {}, innerWidth: 1920, innerHeight: 1080, devicePixelRatio: 1,
    screen: { width: 1920, height: 1080 },
  };
  const navigator = { userAgent: "test" };
  if (decide) {
    navigator.mediaCapabilities = { decodingInfo: (cfg) => { asked.push(cfg); return Promise.resolve(decide(cfg)); } };
  }
  const ctx = { window, navigator, console, Promise, Math, Date, JSON, Object, Array, String, Number, RegExp,
    document: { body: { getAttribute: () => "", classList: { toggle() {}, contains: () => false } }, getElementById: () => el() },
    setTimeout: () => 0, clearTimeout() {}, TextEncoder };
  vm.runInNewContext(SRC, ctx);
  return { D: window.AiSignX.device, asked };
}

const V = (id, w, h, extra) => Object.assign({ id: id + 100, media_id: id, type: "video", mime: "video/mp4", sha256: "s" + id,
  video: { codec: "avc1.640033", width: w, height: h, fps: 25, bitrate: 40000000 } }, extra || {});

(async () => {
  const out = {};
  {
    // 4K is refused, 1080p is "supported but not smooth", 720p is fine.
    const w = world((c) => c.video.width > 3000 ? { supported: false, smooth: false }
      : c.video.width > 1500 ? { supported: true, smooth: false } : { supported: true, smooth: true });
    w.D.mediaSupport([V(1, 3840, 2160), V(2, 1920, 1080), V(3, 1280, 720), { id: 9, media_id: 9, type: "image", url: "/x" },
      V(1, 3840, 2160, { id: 150 })]);  // the same video twice is asked once
    await flush();
    const d = w.D.diag();
    out.report = d.media_support;
    out.asked = w.asked.map((c) => [c.type, c.video.contentType, c.video.width, c.video.framerate, c.video.bitrate]);
    // The same playlist again is not asked again; a different one is.
    w.D.mediaSupport([V(1, 3840, 2160), V(2, 1920, 1080), V(3, 1280, 720)]);
    out.askedAgain = w.asked.length;
    w.D.mediaSupport([V(3, 1280, 720)]);
    await flush();
    out.afterChange = w.D.diag().media_support;
  }
  {
    // A browser without the API reports nothing and does not break.
    const w = world(null);
    w.D.mediaSupport([V(1, 3840, 2160)]);
    await flush();
    out.noApi = { diag: w.D.diag().media_support === undefined };
  }
  {
    // A rejecting check is ignored.
    const w = world(() => { throw new Error("nope"); });
    w.D.mediaSupport([V(1, 3840, 2160)]);
    await flush();
    out.throws = w.D.diag().media_support === undefined;
  }
  process.stdout.write(JSON.stringify(out));
})();
