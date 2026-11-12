// AiSignX browser player: networking (API calls, SSE client, heartbeat, storage).
// Classic script, ES2018 at most (no ?. or ??) so older TV browsers and WebViews work.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var API = document.body.getAttribute("data-api") || "/api/player/v1";
  var TIMEOUT_MS = 10000;

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

  // crypto.randomUUID needs a secure context; plain-HTTP LANs are not one.
  function clientId() {
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
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    if (ctrl) init.signal = ctrl.signal;
    var timer;
    var timeout = new Promise(function (_, reject) {
      timer = setTimeout(function () {
        if (ctrl) ctrl.abort();
        reject(new Error("timeout"));
      }, TIMEOUT_MS);
    });
    var request = fetch(API + path, init).then(function (res) {
      var retryAfter = parseInt(res.headers.get("Retry-After") || "", 10) || 0;
      if (res.status === 304) return { kind: "not_modified", status: 304, data: null, retryAfter: 0 };
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
        return { kind: kind, status: res.status, data: data, retryAfter: retryAfter };
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
    var self = { seconds: 30, lastAt: 0, lastKind: "" };
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
    store: store,
    clientId: clientId,
    jitter: jitter,
    Backoff: Backoff,
    Events: Events,
    Heartbeat: Heartbeat,
  };
})();
