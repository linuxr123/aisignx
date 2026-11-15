// Clock plugin: picks the theme, mounts it and ticks on every second (or minute) boundary
// of the server-calibrated clock (ctx.now()). No external data, so it works fully offline.
(function () {
  "use strict";

  var DEFAULT_THEME = "minimal-dark";
  var state = null, theme = null, opts = null, timer = 0, shape = "";

  function options(ctx) {
    var c = ctx.config || {};
    return {
      theme: Object.prototype.hasOwnProperty.call(Clock.themes, c.theme) ? c.theme : DEFAULT_THEME,
      tz: String(c.timezone || ctx.tz || ""),
      locale: ctx.locale || "",
      h12: c.format === "12h",
      seconds: c.seconds !== false,
      showDate: c.show_date !== false,
      dateStyle: c.date_style === "short" ? "short" : "long",
      label: String(c.label || "").slice(0, 60),
    };
  }

  // Scales the layout down when it would not fit (long dates, long labels, odd frame sizes).
  // Only re-measured when something that changes its size changed.
  function refit(ctx, t, force) {
    var key = window.innerWidth + "x" + window.innerHeight + "|" + t.time.length + "|" +
      t.ampm + "|" + t.date;
    if (key === shape && !force) return;
    shape = key;
    ctx.fit(state.stage);
  }

  function tick(ctx, force) {
    var t = Clock.read(ctx.now(), opts);
    theme.tick(state, t);
    refit(ctx, t, force);
  }

  // Next tick just after the next boundary: a fresh timeout each time, so it never drifts.
  function schedule(ctx) {
    var every = opts.seconds ? 1000 : 60000;
    var wait = every - (ctx.now() % every) + 15;
    timer = ctx.later(function () { tick(ctx); schedule(ctx); }, wait);
  }

  function restart(ctx) {
    ctx.clear(timer);
    tick(ctx, true);
    schedule(ctx);
  }

  AiSignXPlugin.register({
    init: function (ctx) {
      opts = options(ctx);
      theme = Clock.themes[opts.theme];
      document.body.className = "clock theme-" + opts.theme + (opts.h12 ? " h12" : "") +
        (opts.seconds ? " with-seconds" : "");
      state = theme.mount(ctx.root, opts);
      tick(ctx, true);
      schedule(ctx);
      window.addEventListener("resize", function () { if (state) tick(ctx, true); });
      ctx.ready();
    },
    // Catch up at once: the frame may have been prepared a while before it is shown, and a
    // throttled background frame can have missed ticks.
    show: function (ctx) { if (state) restart(ctx); },
    resume: function (ctx) { if (state) restart(ctx); },
  });
})();
