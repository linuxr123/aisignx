// AiSignX browser player: networking (API calls, SSE client, heartbeat, storage).
// Classic script, ES2018 at most (no ?. or ??) so older TV browsers and WebViews work.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var API = document.body.getAttribute("data-api") || "/api/player/v1";
  var TIMEOUT_MS = 10000;
  var UPLOAD_TIMEOUT_MS = 30000;

  // ---- Storage: every access is wrapped, because private modes and kiosk
  // profiles may throw or return nothing. Values are JSON.
  function wrap(kind) {
    function area() {
      try { return window[kind]; } catch (e) { return null; }
    }
    return {
      get: function (key) {
        try {
          var raw = area().getItem(key);
          return raw ? JSON.parse(raw) : null;
        } catch (e) { return null; }
      },
      set: function (key, value) {
        try { area().setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
      },
      remove: function (key) {
        try { area().removeItem(key); } catch (e) { /* ignore */ }
      },
    };
  }
  var store = {
    local: wrap("localStorage"),
    session: wrap("sessionStorage"),
    KEYS: { clientId: "aisignx.client_id", manifest: "aisignx.manifest", pairing: "aisignx.pairing" },
  };

  // ---- Shell hook: a kiosk client (Electron preload or Android WebView) may expose
  // device info and receive state/manifest notices. No credential ever passes here.
  // Returns {info: {name, version, device, client_id, admin_url, caps}, onState(s), onManifest(json),
  // capture() -> Promise<Blob|null>, stats() -> object|null, onCommand(obj), onControl(fn(action, repeat))}
  // or null in a plain browser.
  // Every shell call is wrapped; a broken shell never stops playback.
  var shellCache;
  var CAPS = { capture: 1, rotate: 1, "pin-exit": 1, controls: 1, "kiosk-lock": 1, agent: 1 };
  var ACTIONS = { toggle: 1, play: 1, pause: 1, forward: 1, back: 1, next: 1, prev: 1 };
  var CAPTURE_TIMEOUT_MS = 10000;
  function str(v, max) { return typeof v === "string" ? v.slice(0, max) : ""; }
  function withTimeout(promise, ms) {
    var timer;
    return Promise.race([promise, new Promise(function (resolve) { timer = setTimeout(resolve, ms, null); })])
      .then(function (v) { clearTimeout(timer); return v; }, function () { clearTimeout(timer); return null; });
  }
  function b64Blob(b64, type) {
    var bin = atob(b64), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: type });
  }
  // Android: one listener on the shell port dispatches replies by msg.t (capture replies,
  // control actions), so several features can share the port.
  var portHandlers = {}, portListening = false;
  function androidPort() {
    var port = window.AiSignXShellPort;
    return port && typeof port.postMessage === "function" ? port : null;
  }
  function onPortMessage(ev) {
    var msg = null;
    try { msg = JSON.parse(ev && ev.data); } catch (e) { return; }
    if (!msg || typeof msg !== "object" || typeof msg.t !== "string") return;
    var fn = portHandlers[msg.t];
    if (typeof fn === "function") fn(msg);
  }
  function portListen(port, t, fn) {
    portHandlers[t] = fn;
    if (portListening) return;
    portListening = true;
    if (typeof port.addEventListener === "function") port.addEventListener("message", onPortMessage);
    else port.onmessage = onPortMessage;
  }
  // Capture requests go out through the port; replies come back on it, keyed by id.
  function androidCapture() {
    var pending = {}, seq = 0;
    function onReply(msg) {
      if (!pending[msg.id]) return;
      var resolve = pending[msg.id];
      delete pending[msg.id];
      var blob = null;
      try { if (msg.ok && typeof msg.jpeg === "string" && msg.jpeg) blob = b64Blob(msg.jpeg, "image/jpeg"); } catch (e) { blob = null; }
      resolve(blob);
    }
    return function () {
      var port = androidPort();
      if (!port) return Promise.resolve(null);
      portListen(port, "capture", onReply);
      seq = seq % 1000000000 + 1;
      var id = seq;
      var p = new Promise(function (resolve) { pending[id] = resolve; });
      port.postMessage(JSON.stringify({ t: "capture", id: id }));
      return withTimeout(p, CAPTURE_TIMEOUT_MS).then(function (blob) { delete pending[id]; return blob; });
    };
  }
  // fn(action, repeat) receives only the known control actions.
  function controlCallback(fn) {
    return function (a, r) { if (typeof a === "string" && ACTIONS[a] === 1) fn(a, !!r); };
  }
  function readShell() {
    var raw = null, send = null, capture = null, stats = null, command = null, control = null, alive = null;
    try {
      if (window.AiSignXShell && typeof window.AiSignXShell.info === "function") {
        var es = window.AiSignXShell;
        raw = es.info();
        send = function (kind, value) {
          var fn = kind === "state" ? es.onState : es.onManifest;
          if (typeof fn === "function") fn(value);
        };
        capture = function () {
          if (typeof es.capture !== "function") return Promise.resolve(null);
          return withTimeout(Promise.resolve(es.capture()), CAPTURE_TIMEOUT_MS).then(function (bytes) {
            return bytes && bytes.length ? new Blob([bytes], { type: "image/jpeg" }) : null;
          });
        };
        stats = function () { return typeof es.stats === "function" ? es.stats() : null; };
        command = function (o) { if (typeof es.onCommand === "function") es.onCommand(JSON.stringify(o)); };
        control = function (fn) { if (typeof es.onControl === "function") es.onControl(controlCallback(fn)); };
        alive = function (o) { if (typeof es.alive === "function") es.alive(JSON.stringify(o)); };
      } else if (window.AiSignXShellInfo && typeof window.AiSignXShellInfo.info === "function") {
        var si = window.AiSignXShellInfo;
        raw = JSON.parse(si.info());
        send = function (kind, value) {
          var port = window.AiSignXShellPort;
          if (!port || typeof port.postMessage !== "function") return;
          port.postMessage(JSON.stringify(kind === "state" ? { t: "state", s: value } : { t: "manifest", m: value }));
        };
        capture = androidCapture();
        stats = function () { return typeof si.stats === "function" ? JSON.parse(si.stats()) : null; };
        command = function (o) {
          var port = androidPort();
          if (port) port.postMessage(JSON.stringify({ t: "command", c: o }));
        };
        control = function (fn) {
          var port = androidPort();
          if (!port) return;
          var cb = controlCallback(fn);
          portListen(port, "control", function (msg) { cb(msg.a, msg.r); });
          port.postMessage(JSON.stringify({ t: "controls" }));
        };
        alive = function (o) {
          var port = androidPort();
          if (port) port.postMessage(JSON.stringify({ t: "alive", a: o }));
        };
      }
    } catch (e) { raw = null; }
    if (!raw || typeof raw !== "object" || (raw.name !== "electron" && raw.name !== "android")) return null;
    var admin = str(raw.admin_url, 300).replace(/\/+$/, "");
    var info = {
      name: raw.name,
      version: str(raw.version, 20),
      device: str(raw.device, 120),
      client_id: str(raw.client_id, 32),
      admin_url: /^https?:\/\/[^\s]+$/i.test(admin) ? admin : "",
      caps: (Array.isArray(raw.caps) ? raw.caps : []).filter(function (c) {
        return typeof c === "string" && CAPS[c] === 1;
      }),
    };
    function safe(kind) {
      return function (value) { try { send(kind, value); } catch (e) { /* ignore */ } };
    }
    return {
      info: info,
      onState: safe("state"),
      onManifest: safe("manifest"),
      capture: function () {
        try { return Promise.resolve(capture()).then(null, function () { return null; }); } catch (e) { return Promise.resolve(null); }
      },
      stats: function () {
        try { var s = stats(); return s && typeof s === "object" ? s : null; } catch (e) { return null; }
      },
      // The page is running: a small status object every few seconds (device agent watchdog). A
      // frozen page stops sending it. A no-op for an old shell.
      alive: function (obj) { try { if (alive) alive(obj); } catch (e) { /* ignore */ } },
      // Admin commands for the shell itself (unlock, lock). A no-op for an old shell.
      onCommand: function (obj) { try { if (command) command(obj); } catch (e) { /* ignore */ } },
      // Keys and remote buttons the shell turns into control actions.
      onControl: function (fn) {
        if (typeof fn !== "function") return;
        try { if (control) control(fn); } catch (e) { /* ignore */ }
      },
    };
  }
  function shell() {
    if (shellCache === undefined) shellCache = readShell();
    return shellCache;
  }

  // crypto.randomUUID needs a secure context; plain-HTTP LANs are not one.
  function clientId() {
    var sh = shell();
    if (sh && /^[0-9a-f]{32}$/.test(sh.info.client_id)) {
      store.local.set(store.KEYS.clientId, sh.info.client_id);
      return sh.info.client_id;
    }
    var id = store.local.get(store.KEYS.clientId);
    if (typeof id === "string" && /^[0-9a-f]{32}$/.test(id)) return id;
    var bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
    id = Array.prototype.map.call(bytes, function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
    store.local.set(store.KEYS.clientId, id);
    return id;
  }

  // ms +/- frac (0.2 = +/-20%).
  function jitter(ms, frac) {
    return Math.max(0, Math.round(ms * (1 + (Math.random() * 2 - 1) * frac)));
  }

  // Exponential backoff with +/-20% jitter.
  function Backoff(minMs, maxMs) {
    var cur = minMs;
    return {
      next: function () { var d = jitter(cur, 0.2); cur = Math.min(maxMs, cur * 2); return d; },
      reset: function () { cur = minMs; },
    };
  }

  // ---- api(path, opts) -> Promise<{kind, status, data, retryAfter}>. Never rejects.
  // kind: ok | not_modified | unauthorized | setup | expired | retry
  function api(path, opts) {
    opts = opts || {};
    var headers = { Accept: "application/json" };
    var init = { method: opts.method || "GET", headers: headers, credentials: "same-origin", cache: "no-store" };
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    if (opts.etag) headers["If-None-Match"] = '"' + opts.etag + '"';
    return send(path, init, TIMEOUT_MS);
  }

  // upload(path, blob, headers) -> the same result shape as api(). POSTs the raw Blob.
  function upload(path, blob, extra) {
    var headers = { Accept: "application/json", "Content-Type": (blob && blob.type) || "application/octet-stream" };
    Object.keys(extra || {}).forEach(function (k) { headers[k] = extra[k]; });
    return send(path, { method: "POST", headers: headers, body: blob, credentials: "same-origin", cache: "no-store" },
      UPLOAD_TIMEOUT_MS);
  }

  function send(path, init, ms) {
    var sentAt = Date.now();  // results carry sentAt/recvAt: the clock estimate (sync.js) needs them
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    if (ctrl) init.signal = ctrl.signal;
    var timer;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () {
        if (ctrl) ctrl.abort();
        reject(new Error("timeout"));
      }, ms);
    });
    var request = fetch(API + path, init).then(function (res) {
      var retryAfter = parseInt(res.headers.get("Retry-After") || "", 10) || 0;
      var recvAt = Date.now();
      if (res.status === 304) return { kind: "not_modified", status: 304, data: null, retryAfter: 0, sentAt: sentAt, recvAt: recvAt };
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        var code = data && typeof data === "object" ? data.error : null;
        var kind = "retry";
        if (res.status >= 200 && res.status < 300 && data && typeof data === "object") kind = "ok";
        // Only a JSON 401 from our API drops the credential; an HTML 401 from a proxy never does.
        else if (res.status === 401 && code === "unauthorized") kind = "unauthorized";
        else if (res.status === 503 && code === "setup_required") kind = "setup";
        else if (res.status === 404 && code === "expired") kind = "expired";
        return { kind: kind, status: res.status, data: data, retryAfter: retryAfter, sentAt: sentAt, recvAt: recvAt };
      });
    });
    return Promise.race([request, timeout]).then(function (r) {
      clearTimeout(timer);
      return r;
    }, function () {
      clearTimeout(timer);
      return { kind: "retry", status: 0, data: null, retryAfter: 0 };
    });
  }

  // ---- Events: EventSource client with backoff, jitter and a watchdog.
  // on: {hello, manifest, command, revoked, error} handlers, each receives parsed JSON.
  // The server sends a named "ka" event every 20 s (SSE comments are invisible to
  // EventSource), so a stream with no frame for 3 x keepalive (60 s) is dead and is
  // recycled. A stream stuck before its headers (CONNECTING) is recycled after 60 s too.
  var NAMED = ["hello", "manifest", "command", "revoked", "bye", "ka", "emergency", "emergency_clear"];
  var SILENCE_MS = 60 * 1000;

  function Events(on) {
    var self = { status: "closed", lastEventAt: 0 };
    var es = null, retryTimer = null, dogTimer = null, openedAt = 0, running = false;
    var backoff = Backoff(1000, 30000);

    function close() {
      if (es) { es.close(); es = null; }
      clearTimeout(retryTimer); retryTimer = null;
    }
    function reconnectIn(ms) {
      close();
      self.status = "waiting";
      retryTimer = setTimeout(connect, ms);
    }
    function call(name, data) {
      if (typeof on[name] === "function") {
        try { on[name](data); } catch (e) { if (window.console) console.error(e); }
      }
    }
    function handle(name, ev) {
      self.lastEventAt = Date.now();
      if (name === "ka") return;
      var data = {};
      try { data = JSON.parse(ev.data || "{}") || {}; } catch (e) { data = {}; }
      if (name === "hello") { backoff.reset(); self.status = "live"; }
      if (name === "bye") { reconnectIn(Math.floor(Math.random() * 3000)); return; }
      if (name === "revoked") reconnectIn(backoff.next());
      call(name, data);
    }
    function connect() {
      close();
      if (!running) return;
      self.status = "connecting";
      openedAt = Date.now();
      self.lastEventAt = 0;
      try {
        es = new EventSource(API + "/events");
      } catch (e) {
        reconnectIn(backoff.next());
        return;
      }
      NAMED.forEach(function (name) {
        es.addEventListener(name, function (ev) { handle(name, ev); });
      });
      es.onopen = function () { self.status = "open"; };
      es.onmessage = function () { self.lastEventAt = Date.now(); };
      es.onerror = function () {
        // EventSource cannot see the status code; the player fetches the manifest
        // once, which reveals a revoked credential (JSON 401).
        reconnectIn(backoff.next());
        call("error", {});
      };
    }
    function watchdog() {
      if (!es) return;
      var now = Date.now();
      if (es.readyState !== 1) {
        if (now - openedAt > SILENCE_MS) reconnectIn(backoff.next());
        return;
      }
      if (now - (self.lastEventAt || openedAt) > SILENCE_MS) reconnectIn(jitter(1000, 0.2));
    }

    self.start = function () {
      if (running) return;
      running = true;
      backoff.reset();
      dogTimer = setInterval(watchdog, 5000);
      connect();
    };
    self.stop = function () {
      running = false;
      close();
      clearInterval(dogTimer); dogTimer = null;
      self.status = "closed";
    };
    return self;
  }

  // ---- Heartbeat: POST /heartbeat every `seconds` +/-10%.
  // payload() builds the body; onResult(res) receives the api() result.
  function Heartbeat(payload, onResult) {
    var self = { seconds: 30, lastAt: 0, lastKind: "", lastOkAt: 0 };
    var timer = null, running = false, inFlight = false;

    function schedule() {
      clearTimeout(timer);
      if (running) timer = setTimeout(beat, jitter(self.seconds * 1000, 0.1));
    }
    function beat() {
      clearTimeout(timer); timer = null;
      if (!running || inFlight) return;
      inFlight = true;
      api("/heartbeat", { method: "POST", body: payload() }).then(function (res) {
        inFlight = false;
        self.lastAt = Date.now();
        self.lastKind = res.kind === "ok" ? "ok" : res.kind + " " + res.status;
        if (res.kind === "ok") self.lastOkAt = self.lastAt;
        if (res.kind === "ok" && res.data.heartbeat_s > 0) self.seconds = res.data.heartbeat_s;
        schedule();
        if (running) onResult(res);
      });
    }

    self.start = function (seconds) {
      if (seconds > 0) self.seconds = seconds;
      running = true;
      beat();
    };
    self.stop = function () { running = false; clearTimeout(timer); timer = null; };
    self.now = function () { if (running) beat(); };
    self.setSeconds = function (seconds) {
      if (seconds > 0 && seconds !== self.seconds) { self.seconds = seconds; schedule(); }
    };
    return self;
  }

  A.net = {
    API: API,
    api: api,
    upload: upload,
    store: store,
    clientId: clientId,
    shell: shell,
    jitter: jitter,
    Backoff: Backoff,
    Events: Events,
    Heartbeat: Heartbeat,
  };
})();
