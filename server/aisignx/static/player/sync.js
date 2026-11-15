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

  A.sync = { position: position, Clock: Clock, clock: Clock(), active: false, MIN_ITEM_MS: MIN_ITEM_MS };
})();
