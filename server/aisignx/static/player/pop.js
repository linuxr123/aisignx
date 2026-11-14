// AiSignX browser player: proof of play. Classic script, ES2018 at most.
// player.js calls AiSignXPop.end(true) and then begin(item) when a slide goes on screen, and
// end(false) when the slide on screen fails (failed slides are not recorded). Each finished
// play is queued in localStorage and posted to /plays in batches; the server ignores resent
// ids, so a batch is removed only after a 2xx "ok" and may safely be sent twice.
// Gaps: time under a fullscreen emergency overlay is subtracted, sampled twice a second;
// started_at uses the device clock, so a screen with a wrong clock has its plays dropped.
(function () {
  "use strict";

  var A = window.AiSignX || {};
  var net = A.net;
  var QUEUE_KEY = "aisignx.plays", OFF_KEY = "aisignx.plays_off";
  var MAX_QUEUE = 1000, BATCH = 200, FLUSH_AT = 50, FLUSH_MS = 60000, SAMPLE_MS = 500;
  var FIRST_FLUSH_MS = 15000, MIN_MS = 500, MAX_MS = 86400000;
  var ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

  var store = net && net.store ? net.store.local : null;
  var queue = load();
  var cur = null;    // {id, media_id, item_id, started_at, t0, covered, coverFrom}
  var sending = false;
  var offUntil = Number(store && store.get(OFF_KEY)) || 0;  // collection is off until then

  function mono() { return window.performance && performance.now ? performance.now() : Date.now(); }
  function load() {
    var q = store ? store.get(QUEUE_KEY) : null;
    return Array.isArray(q) ? q.filter(function (e) { return e && ID_RE.test(e.id); }).slice(-MAX_QUEUE) : [];
  }
  function save() { if (store) store.set(QUEUE_KEY, queue); }
  function off() { return offUntil > Date.now(); }

  // crypto.randomUUID needs a secure context (not plain-HTTP LANs), so fall back to random hex.
  function newId() {
    try { if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID(); } catch (e) { /* fall back */ }
    var bytes = new Uint8Array(16), i;
    if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(bytes);
    else for (i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    return Array.prototype.map.call(bytes, function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
  }

  // The state emergency.js keeps: a fullscreen overlay hides the content underneath.
  function covered() {
    var em = A.emergency;
    return !!(em && em.shownId !== null && em.shownId !== undefined && em.layout === "fullscreen");
  }
  function sample() {
    if (!cur) return;
    var c = covered(), t = mono();
    if (c && cur.coverFrom === null) cur.coverFrom = t;
    else if (!c && cur.coverFrom !== null) { cur.covered += t - cur.coverFrom; cur.coverFrom = null; }
  }

  function begin(item) {
    cur = null;
    if (off() || !item || typeof item.media_id !== "number" || item.media_id < 1) return;
    cur = {
      id: newId(), media_id: Number(item.media_id),
      item_id: typeof item.id === "number" && item.id >= 0 ? item.id : null,
      started_at: new Date(Date.now()).toISOString(),
      t0: mono(), covered: 0, coverFrom: null,
    };
    sample();
  }
  function end(ok) {
    var c = cur;
    if (!c) return;
    sample();
    cur = null;
    var t = mono(), shown = t - c.t0 - c.covered - (c.coverFrom !== null ? t - c.coverFrom : 0);
    var ms = Math.min(MAX_MS, Math.round(shown));
    if (!ok || off() || ms < MIN_MS) return;
    queue.push({ id: c.id, media_id: c.media_id, item_id: c.item_id, started_at: c.started_at, duration_ms: ms });
    if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);  // drop the oldest
    save();
    if (queue.length >= FLUSH_AT) flush();
  }

  // Posts up to BATCH events; an empty POST probes whether collection is back on.
  function flush() {
    if (sending || !net) return;
    if (off()) return;
    if (!queue.length && !offUntil) return;
    sending = true;
    var batch = queue.slice(0, BATCH);
    net.api("/plays", { method: "POST", body: { events: batch } }).then(function (res) {
      sending = false;
      var d = res.data || {};
      if (res.kind === "ok" && d.status === "disabled") {
        cur = null;
        queue = [];
        save();
        offUntil = Date.now() + Math.max(60, Number(d.retry_after_s) || 1800) * 1000;
        if (store) store.set(OFF_KEY, offUntil);
        return;
      }
      if (res.kind === "ok" && d.status === "ok") {
        if (offUntil) { offUntil = 0; if (store) store.remove(OFF_KEY); }
        removeSent(batch);
        if (queue.length >= FLUSH_AT) setTimeout(flush, 0);
      } else if (res.status === 400 || res.status === 413) {
        removeSent(batch);  // the server will never take this batch; do not retry it forever
      }
      // 401: pairing is handled by the player's own requests. Anything else: retry later.
    });
  }
  function removeSent(batch) {
    var sent = {};
    batch.forEach(function (e) { sent[e.id] = 1; });
    queue = queue.filter(function (e) { return !sent[e.id]; });
    save();
  }

  // A reload or shutdown keeps the play on screen so far; the next page load sends it.
  window.addEventListener("pagehide", function () { end(true); });
  setInterval(sample, SAMPLE_MS);
  setInterval(flush, FLUSH_MS);
  setTimeout(flush, FIRST_FLUSH_MS);

  window.AiSignXPop = {
    begin: begin,
    end: end,
    flush: flush,
    queued: function () { return queue.length; },
  };
})();
