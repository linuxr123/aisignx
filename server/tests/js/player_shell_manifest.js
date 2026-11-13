// Runs static/player/player.js in a Node vm with stubbed modules and checks that a kiosk
// shell receives one full manifest per page load even when the cached copy is current.
// Usage: node player_shell_manifest.js <path to player.js>. Prints JSON results.
"use strict";
const fs = require("fs");
const vm = require("vm");

function element() {
  return {
    hidden: false, className: "", textContent: "", firstChild: null,
    getAttribute() { return ""; }, setAttribute() {},
    appendChild(c) { return c; }, removeChild() {},
  };
}

function run(withShell) {
  const calls = [], fed = [];
  const handlers = {};
  const cached = { version: "v1", items: [], settings: { orientation: 0 } };
  const fresh = { version: "v1", items: [], settings: { orientation: 90, unlock: { hash: "h" } } };
  const shell = withShell
    ? { info: { name: "android", version: "3" }, onState() {}, onManifest(m) { fed.push(JSON.parse(m)); } }
    : null;
  const store = { get(k) { return k === "manifest" ? cached : null; }, set() {}, remove() {} };
  const net = {
    shell: () => shell,
    clientId: () => "c",
    store: { KEYS: { manifest: "manifest", pairing: "pairing" }, local: store, session: store },
    Backoff: () => ({ next: () => 1000, reset() {} }),
    Events: (h) => { Object.assign(handlers, h); return { start() {}, stop() {}, status: "" }; },
    Heartbeat: () => ({ start() {}, stop() {}, now() {}, setSeconds() {} }),
    api(path, opts) {
      calls.push({ path, etag: opts && opts.etag });
      const res = opts && opts.etag === "v1" ? { kind: "not_modified" } : { kind: "ok", data: fresh };
      return Promise.resolve(res);
    },
  };
  const noop = () => {};
  const window = {
    AiSignX: {
      net,
      render: { init: noop, clear: noop, teardown: noop, swap: noop, resume: noop, isVideo: () => false },
      emergency: { apply: noop, show: noop, hide: noop, sync: noop, clearIf: noop },
      device: { init: noop, applySettings: noop, noteError: noop, checkOffline: noop, command: noop, diag: () => null },
    },
  };
  const document = {
    body: element(),
    getElementById: () => element(),
    createElement: () => element(),
    createTextNode: () => element(),
    addEventListener: noop,
  };
  const ctx = {
    window, document, console,
    location: { search: "", origin: "http://x", host: "x", pathname: "/player/" },
    navigator: { userAgent: "" },
    setTimeout: () => 0, clearTimeout: noop, setInterval: () => 0, clearInterval: noop,
    Promise, JSON, Object, Array, Math, Date, Number, RegExp,
  };
  vm.runInNewContext(fs.readFileSync(process.argv[2], "utf-8"), ctx);
  return new Promise((resolve) => setImmediate(() => {
    handlers.manifest();  // a later push: the shell has its manifest, so the ETag is used again
    setImmediate(() => resolve({ etags: calls.map((c) => c.etag === undefined ? null : c.etag), fed }));
  }));
}

Promise.all([run(true), run(false)]).then(([withShell, browser]) => {
  process.stdout.write(JSON.stringify({ withShell, browser }));
});
