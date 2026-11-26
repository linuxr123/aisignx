// AiSignX browser player: synchronized playback helpers (pure, no DOM).
//
// Screens in a group with synchronized playback work out which slide should be on screen from
// a shared clock, so they stay in step without talking to each other:
//
//   position(durations, anchorMs, serverMs): index of the slide that plays at server time
//     serverMs, how far into it that is, and how long it still has to run. The playlist repeats
//     every sum(durations) milliseconds, counted from the anchor (the server uses 0).
//   Clock: this screen's offset from the server clock, learned from the server_time_ms in the
//     responses it already gets. Each sample is corrected by half its round trip, and the sample
//     with the lowest round trip of the last few is used, because a fast answer is the most
//     accurate one.
//
// Classic script, ES2018 at most.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var MIN_ITEM_MS = 1000;       // the player never shows a slide for less than this
  var SAMPLES = 8;              // round trips kept for the estimate
  var MAX_RTT_MS = 10000;       // a slower answer says nothing about the clock

  function itemMs(d) { d = Number(d); return d > 0 ? Math.max(MIN_ITEM_MS, d) : 0; }

  // position(durations, anchorMs, serverMs) -> {index, elapsedMs, remainingMs, cycleMs} or null.
  function position(durations, anchorMs, serverMs) {
    var total = 0, i;
    for (i = 0; i < durations.length; i++) total += itemMs(durations[i]);
    if (!(total > 0) || !isFinite(serverMs)) return null;
    var t = (serverMs - (Number(anchorMs) || 0)) % total;
    if (t < 0) t += total;
    var acc = 0;
    for (i = 0; i < durations.length; i++) {
      var d = itemMs(durations[i]);
      if (d > 0 && t < acc + d) return { index: i, elapsedMs: t - acc, remainingMs: acc + d - t, cycleMs: total };
      acc += d;
    }
    return null;  // not reachable: t < total
  }

  // ---- Group transport: next, previous, seek and pause move the timeline for the whole group.
  // The server keeps one small state per group (player/transport.py):
  //   {rev, key, offset_ms, paused_ms, resume_at_ms}
  // The timeline position at server time T is effective(T, state): T - offset_ms while running,
  // paused_ms while paused, and from resume_at_ms on it runs again from paused_ms (a pause ends by itself).
  function effective(serverMs, tr) {
    if (!tr) return serverMs;
    if (typeof tr.paused_ms === "number") {
      if (typeof tr.resume_at_ms === "number" && serverMs >= tr.resume_at_ms) return tr.paused_ms + (serverMs - tr.resume_at_ms);
      return tr.paused_ms;
    }
    return serverMs - (Number(tr.offset_ms) || 0);
  }
  function isPaused(serverMs, tr) {
    return !!tr && typeof tr.paused_ms === "number" && !(typeof tr.resume_at_ms === "number" && serverMs >= tr.resume_at_ms);
  }

  // plan(action, o) -> {state, result} or null: the group's new state for a control, worked out from
  // o = {durations, anchorMs, key, tr (the current state or null), t (server now), resumeMs (pause length),
  //      seekMs (video: the step of a fast forward, else 0)}.
  // `result` is what the controls show: "paused", "playing", "next", "prev" or the seconds seeked.
  function plan(action, o) {
    var pos = position(o.durations, o.anchorMs, effective(o.t, o.tr));
    if (!pos) return null;
    var eff = effective(o.t, o.tr), paused = isPaused(o.t, o.tr), n = o.durations.length;
    var start = eff - pos.elapsedMs;                       // timeline position where the slide on screen began
    function state(target, hold) {
      return {
        key: o.key,
        offset_ms: Math.round(o.t - target),               // running: the timeline is `target` right now
        paused_ms: hold ? Math.round(target) : null,
        resume_at_ms: hold ? Math.round(o.t + o.resumeMs) : null,
      };
    }
    function slideMs(i) { return itemMs(o.durations[((i % n) + n) % n]); }
    switch (action) {
      case "toggle": case "play": case "pause":
        if (paused) return action === "pause" ? null : { state: state(eff, false), result: "playing" };
        return action === "play" ? null : { state: state(eff, true), result: "paused" };
      case "next":   // the start of the next slide (a single slide restarts)
        return { state: state(start + pos.elapsedMs + pos.remainingMs, false), result: "next" };
      case "prev":
        return { state: state(start - slideMs(pos.index - 1) + (n === 1 ? slideMs(0) : 0), false), result: "prev" };
      case "forward": case "back": {
        var step = o.seekMs || 0;
        if (!(step > 0)) return plan(action === "forward" ? "next" : "prev", o);   // not a video: change the slide
        var want = eff + (action === "forward" ? step : -step);
        // never out of the slide: not before its start, and a moment before its end
        want = Math.max(start, Math.min(start + pos.elapsedMs + pos.remainingMs - 250, want));
        return { state: state(want, paused), result: Math.round((want - eff) / 100) / 10 };
      }
      default: return null;
    }
  }

  function Clock() {
    var samples = [], best = null;
    var self = {
      // sample(serverMs, sentAt, recvAt): sentAt and recvAt are local Date.now() values around the
      // request whose response carried server_time_ms.
      sample: function (serverMs, sentAt, recvAt) {
        var rtt = recvAt - sentAt;
        if (!isFinite(serverMs) || !isFinite(rtt) || rtt < 0 || rtt > MAX_RTT_MS) return false;
        samples.push({ rtt: rtt, offset: serverMs + rtt / 2 - recvAt });
        if (samples.length > SAMPLES) samples.shift();
        best = samples.reduce(function (a, b) { return b.rtt < a.rtt ? b : a; });
        return true;
      },
      ready: function () { return best !== null; },
      offset: function () { return best ? best.offset : 0; },  // server time minus local time
      rtt: function () { return best ? best.rtt : 0; },
      // now(): the server's time as best known. localMs defaults to Date.now().
      now: function (localMs) { return (localMs === undefined ? Date.now() : localMs) + (best ? best.offset : 0); },
      reset: function () { samples = []; best = null; },
    };
    return self;
  }

  A.sync = { position: position, effective: effective, isPaused: isPaused, plan: plan, Clock: Clock, clock: Clock(), active: false, MIN_ITEM_MS: MIN_ITEM_MS };
})();
