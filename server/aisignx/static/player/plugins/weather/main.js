// AiSignX weather plugin (frame). Draws the normalized data from the server (see
// plugins/bundled/weather/providers.py) as large, glanceable signage. Layouts: full (now,
// details, next hours, days), compact (now and days) and current (now and details). Sizes are
// in vmin through the --u custom property, so 16:9 and 9:16 screens both read well.
// Pure helpers are exposed as window.WxView for tests/js/plugin_weather.js.
(function () {
  "use strict";

  var I = window.WxIcons;
  var DASH = "—";
  var COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  var HOURS_SHOWN = 8;            // portrait hides the last two in CSS
  var CLOCK_MS = 15000;

  // --- pure helpers ---------------------------------------------------------------------

  function isNum(v) { return typeof v === "number" && isFinite(v); }

  function tempText(v) {
    if (!isNum(v)) return DASH;
    var r = Math.round(v);
    return (r === 0 ? 0 : r) + "°";         // never "-0°"
  }

  function numText(v, digits) {
    if (!isNum(v)) return DASH;
    var f = Math.pow(10, digits || 0);
    return String(Math.round(v * f) / f);
  }

  function compass(deg) {
    if (!isNum(deg)) return "";
    var i = Math.round((((deg % 360) + 360) % 360) / 22.5) % 16;
    return COMPASS[i];
  }

  // "YYYY-MM-DD" of ms in tz (falls back to the screen's zone when tz is unknown).
  function localDate(ms, tz) {
    var opts = { year: "numeric", month: "2-digit", day: "2-digit" };
    var parts;
    try {
      parts = new Intl.DateTimeFormat("en-CA", tz ? Object.assign({ timeZone: tz }, opts) : opts)
        .formatToParts(ms);
    } catch (e) {
      parts = new Intl.DateTimeFormat("en-CA", opts).formatToParts(ms);
    }
    var get = function (t) {
      for (var i = 0; i < parts.length; i++) if (parts[i].type === t) return parts[i].value;
      return "";
    };
    return get("year") + "-" + get("month") + "-" + get("day");
  }

  // Hours still ahead of now (an hour that started under 30 minutes ago still counts).
  function upcoming(hourly, nowMs, n) {
    return (Array.isArray(hourly) ? hourly : []).filter(function (h) {
      return h && isNum(h.t) && h.t > nowMs - 30 * 60000;
    }).slice(0, n);
  }

  // Days from today on (stale data never shows yesterday as "Today").
  function futureDays(daily, today, n) {
    return (Array.isArray(daily) ? daily : []).filter(function (d) {
      return d && typeof d.date === "string" && d.date >= today;
    }).slice(0, n);
  }

  // The lowest and highest temperature across the shown days (for the range bars).
  function bounds(days, current) {
    var lo = Infinity, hi = -Infinity;
    days.forEach(function (d) {
      if (isNum(d.tmin)) lo = Math.min(lo, d.tmin);
      if (isNum(d.tmax)) hi = Math.max(hi, d.tmax);
    });
    if (isNum(current)) { lo = Math.min(lo, current); hi = Math.max(hi, current); }
    if (!isFinite(lo) || !isFinite(hi)) return null;
    if (hi - lo < 1) hi = lo + 1;
    return { lo: lo, hi: hi };
  }

  // [left, width] in percent of a bar spanning b.
  function span(b, from, to) {
    if (!b || !isNum(from) || !isNum(to)) return null;
    var w = b.hi - b.lo;
    var left = (Math.min(from, to) - b.lo) / w * 100;
    var width = Math.abs(to - from) / w * 100;
    return [Math.max(0, Math.min(100, left)), Math.max(2, Math.min(100 - left, width))];
  }

  // The background class: a weather mood for "auto", else the fixed theme.
  function themeClass(theme, code, isDay) {
    if (theme === "dark" || theme === "light") return "theme-" + theme;
    return "theme-auto mood-" + I.mood(code, isDay);
  }

  function weekdayOf(date, locale) {
    var p = String(date).split("-");
    var ms = Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2]), 12);
    try {
      return new Intl.DateTimeFormat(locale || undefined, { weekday: "short", timeZone: "UTC" }).format(ms);
    } catch (e) {
      return new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" }).format(ms);
    }
  }

  window.WxView = {
    tempText: tempText, numText: numText, compass: compass, localDate: localDate,
    upcoming: upcoming, futureDays: futureDays, bounds: bounds, span: span,
    themeClass: themeClass, weekdayOf: weekdayOf,
  };

  // --- DOM --------------------------------------------------------------------------------

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }
  function add(parent, tag, cls, text) { return parent.appendChild(el(tag, cls, text)); }

  var clockTimer = null;

  function tzOf(ctx) {
    var d = ctx.data || {};
    return (d.location && d.location.tz) || ctx.tz || "";
  }
  function fmt(ctx, ms, opts) {
    var tz = tzOf(ctx);
    return ctx.format(ms, tz ? Object.assign({ timeZone: tz }, opts) : opts);
  }
  function timeText(ctx, ms) {
    return isNum(ms) ? fmt(ctx, ms, { hour: "numeric", minute: "2-digit" }) : DASH;
  }

  function place(ctx) {
    var loc = (ctx.config && ctx.config.location) || {};
    var data = (ctx.data && ctx.data.location) || {};
    return String(loc.label || data.label || "");
  }

  function header(ctx, parent) {
    var head = add(parent, "header", "wx-head");
    add(head, "div", "wx-place", place(ctx));
    var when = add(head, "div", "wx-when");
    var date = add(when, "span", "wx-date");
    var time = add(when, "span", "wx-time");
    function tick() {
      var now = ctx.now();
      date.textContent = fmt(ctx, now, { weekday: "long", day: "numeric", month: "long" });
      time.textContent = fmt(ctx, now, { hour: "numeric", minute: "2-digit" });
    }
    tick();
    if (clockTimer != null) ctx.clear(clockTimer);
    clockTimer = ctx.every(tick, CLOCK_MS);
  }

  function hero(ctx, parent, today) {
    var cur = ctx.data.current || {};
    var units = ctx.data.units || {};
    var box = add(parent, "section", "wx-now");
    var icon = add(box, "div", "wx-now-icon");
    icon.appendChild(I.icon(cur.code, cur.is_day));
    var main = add(box, "div", "wx-now-main");
    var temp = add(main, "div", "wx-now-temp", tempText(cur.temp));
    temp.title = units.temp || "";
    add(main, "div", "wx-now-text", I.text(cur.code) || DASH);
    if (today && (isNum(today.tmax) || isNum(today.tmin))) {
      var hl = add(main, "div", "wx-now-range");
      add(hl, "span", "wx-hi", "H " + tempText(today.tmax));
      add(hl, "span", "wx-lo", "L " + tempText(today.tmin));
    }
  }

  function tile(parent, iconNode, label, value, extra) {
    var t = add(parent, "div", "wx-tile");
    var ic = add(t, "div", "wx-tile-icon");
    ic.appendChild(iconNode);
    var txt = add(t, "div", "wx-tile-text");
    var l = add(txt, "div", "wx-tile-label", label);
    if (extra) l.appendChild(extra);
    add(txt, "div", "wx-tile-value", value);
    return t;
  }

  function stats(ctx, parent, today) {
    var cur = ctx.data.current || {};
    var units = ctx.data.units || {};
    var box = add(parent, "section", "wx-stats");
    tile(box, I.stat("feels"), "Feels like", tempText(cur.feels));
    var wind = isNum(cur.wind) ? numText(cur.wind) + " " + (units.wind || "") : DASH;
    var dir = compass(cur.wind_dir);
    var arrow = null;
    if (dir) {
      arrow = el("span", "wx-dir");
      arrow.appendChild(I.arrow(cur.wind_dir));
      add(arrow, "span", "", dir);
    }
    tile(box, I.stat("wind"), "Wind", wind, arrow);
    tile(box, I.stat("humidity"), "Humidity", isNum(cur.humidity) ? cur.humidity + "%" : DASH);
    var pop = today && isNum(today.pop) ? today.pop + "%" : null;
    if (pop) tile(box, I.stat("rain"), "Chance of rain", pop);
    else tile(box, I.stat("rain"), "Precipitation",
      isNum(cur.precip) ? numText(cur.precip, 1) + " " + (units.precip || "") : DASH);
    tile(box, I.stat("sunrise"), "Sunrise", timeText(ctx, today && today.sunrise));
    tile(box, I.stat("sunset"), "Sunset", timeText(ctx, today && today.sunset));
  }

  function hours(ctx, parent) {
    var list = upcoming(ctx.data.hourly, ctx.now(), HOURS_SHOWN);
    if (!list.length) return;
    var box = add(parent, "section", "wx-hours");
    list.forEach(function (h) {
      var cell = add(box, "div", "wx-hour");
      add(cell, "div", "wx-hour-time", fmt(ctx, h.t, { hour: "numeric" }));
      var ic = add(cell, "div", "wx-hour-icon");
      ic.appendChild(I.icon(h.code, h.is_day));
      add(cell, "div", "wx-hour-temp", tempText(h.temp));
      add(cell, "div", "wx-pop" + (isNum(h.pop) && h.pop >= 20 ? "" : " wx-pop-low"),
        isNum(h.pop) ? h.pop + "%" : "");
    });
  }

  function dayRows(ctx, parent, days, today) {
    if (!days.length) return;
    var b = bounds(days, ctx.data.current && ctx.data.current.temp);
    var box = add(parent, "section", "wx-days");
    days.forEach(function (d) {
      var row = add(box, "div", "wx-day");
      add(row, "div", "wx-day-name", d.date === today ? "Today" : weekdayOf(d.date, ctx.locale));
      var ic = add(row, "div", "wx-day-icon");
      ic.appendChild(I.icon(d.code, true));
      add(row, "div", "wx-pop" + (isNum(d.pop) && d.pop >= 20 ? "" : " wx-pop-low"),
        isNum(d.pop) ? d.pop + "%" : "");
      add(row, "div", "wx-day-lo", tempText(d.tmin));
      var track = add(row, "div", "wx-bar");
      var s = span(b, d.tmin, d.tmax);
      if (s) {
        var fill = add(track, "div", "wx-bar-fill");
        fill.style.left = s[0].toFixed(1) + "%";
        fill.style.width = s[1].toFixed(1) + "%";
      }
      var cur = ctx.data.current && ctx.data.current.temp;
      if (d.date === today && b && isNum(cur)) {
        var dot = add(track, "div", "wx-bar-now");
        dot.style.left = Math.max(0, Math.min(100, (cur - b.lo) / (b.hi - b.lo) * 100)).toFixed(1) + "%";
      }
      add(row, "div", "wx-day-hi", tempText(d.tmax));
    });
  }

  function intOr(v, lo, hi, dflt) {
    v = Number(v);
    return isFinite(v) ? Math.max(lo, Math.min(hi, Math.round(v))) : dflt;
  }

  function draw(ctx) {
    var cfg = ctx.config || {};
    var data = ctx.data;
    var root = ctx.root;
    root.textContent = "";
    if (!data || typeof data !== "object" || !data.current) {
      ctx.fallback("Weather", ctx.offline ? "This screen is offline" : "Weather data is not available yet");
      return false;
    }
    var layout = ["full", "compact", "current"].indexOf(cfg.layout) >= 0 ? cfg.layout : "full";
    var today = localDate(ctx.now(), tzOf(ctx));
    var days = futureDays(data.daily, today, intOr(cfg.days, 3, 7, 5));
    var first = days.length && days[0].date === today ? days[0] : null;
    var cur = data.current;
    var withHours = layout === "full" && cfg.show_hourly !== false &&
      upcoming(data.hourly, ctx.now(), 1).length > 0;

    document.body.className = "wx-body " + themeClass(cfg.theme, cur.code, cur.is_day);
    var wrap = add(root, "div", "wx layout-" + layout + (withHours ? " has-hours" : " no-hours"));
    header(ctx, wrap);
    hero(ctx, wrap, first);
    if (layout !== "compact") stats(ctx, wrap, first);
    if (withHours) hours(ctx, wrap);
    if (layout !== "current") dayRows(ctx, wrap, days, today);

    ctx.fallback(null);
    ctx.attribution(data.attribution || null);
    ctx.staleBadge(ctx.stale || ctx.offline ? ctx.fetchedAt : 0);
    return true;
  }

  function shown(ctx) {
    if (ctx.data) { draw(ctx); return; }
    ctx.root.textContent = "";
    ctx.fallback("Weather", ctx.offline ? "This screen is offline" : "Loading the forecast…");
  }

  if (!window.AiSignXPlugin) return;   // loaded by a test without the SDK
  window.AiSignXPlugin.register({
    init: function (ctx) { shown(ctx); ctx.ready(); },
    data: function (ctx) { shown(ctx); },
    show: function (ctx) { if (ctx.data) draw(ctx); },   // the hours move on between showings
    net: function (ctx) { if (ctx.data) ctx.staleBadge(ctx.stale || ctx.offline ? ctx.fetchedAt : 0); else shown(ctx); },
    destroy: function () { clockTimer = null; },
  });
})();
