// Clock plugin: shared helpers and the theme registry. Pure functions only (no DOM at load),
// so tests/js/plugin_clock.js can run them in Node.
//
//   Clock.themes[name] = {mount(root, opts) -> state, tick(state, t)}
//
// `t` is the result of Clock.read(ms, opts): numeric parts in the item's time zone plus the
// display strings, so themes never format dates themselves.
(function (g) {
  "use strict";

  var NUM = {};  // time zone -> Intl.DateTimeFormat for the numeric parts (cached)

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function numericFormat(tz) {
    var key = tz || "";
    if (!NUM[key]) {
      var o = { hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric",
                hour: "numeric", minute: "numeric", second: "numeric", weekday: "short" };
      if (tz) o.timeZone = tz;
      try { NUM[key] = new Intl.DateTimeFormat("en-US", o); } catch (e) {
        delete o.timeZone;
        NUM[key] = new Intl.DateTimeFormat("en-US", o);
      }
    }
    return NUM[key];
  }

  // parts(ms, tz) -> {h, m, s, day, month, year} in that time zone (h is 0-23).
  function parts(ms, tz) {
    var out = {};
    numericFormat(tz).formatToParts(ms).forEach(function (p) {
      if (p.type !== "literal" && p.type !== "weekday") out[p.type] = Number(p.value);
    });
    return { h: (out.hour || 0) % 24, m: out.minute || 0, s: out.second || 0,
             day: out.day, month: out.month, year: out.year };
  }

  function intl(locale, tz, o) {
    if (tz) o.timeZone = tz;
    try { return new Intl.DateTimeFormat(locale || undefined, o); } catch (e) {
      delete o.timeZone;
      try { return new Intl.DateTimeFormat(locale || undefined, o); } catch (e2) {
        return new Intl.DateTimeFormat("en-US", o);
      }
    }
  }

  // The locale's AM/PM word for this moment ("AM", "p.m.", ...); "AM"/"PM" when it has none.
  function dayPeriod(ms, opts, h) {
    var text = "";
    try {
      intl(opts.locale, opts.tz, { hour: "numeric", hour12: true }).formatToParts(ms)
        .forEach(function (p) { if (p.type === "dayPeriod") text = p.value; });
    } catch (e) { /* use the default below */ }
    return text || (h < 12 ? "AM" : "PM");
  }

  var DATE_OPTS = {
    long: { weekday: "long", day: "numeric", month: "long", year: "numeric" },
    short: { weekday: "short", day: "numeric", month: "short" },
  };

  function dateText(ms, opts) {
    var o = {}, src = DATE_OPTS[opts.dateStyle] || DATE_OPTS.long;
    Object.keys(src).forEach(function (k) { o[k] = src[k]; });
    try { return intl(opts.locale, opts.tz, o).format(ms); } catch (e) { return ""; }
  }

  // read(ms, opts) -> everything a theme needs for one tick.
  // opts: {tz, locale, h12, seconds, showDate, dateStyle}
  function read(ms, opts) {
    var p = parts(ms, opts.tz);
    var hour = opts.h12 ? ((p.h + 11) % 12) + 1 : p.h;
    var hh = opts.h12 ? String(hour) : pad(hour);
    return {
      ms: ms, h: p.h, m: p.m, s: p.s,
      hour: hour, hh: hh, mm: pad(p.m), ss: pad(p.s),
      ampm: opts.h12 ? dayPeriod(ms, opts, p.h) : "",
      time: hh + ":" + pad(p.m) + (opts.seconds ? ":" + pad(p.s) : ""),
      date: opts.showDate ? dateText(ms, opts) : "",
      dayKey: p.year + "-" + p.month + "-" + p.day,
    };
  }

  // Hand angles in degrees (0 = 12 o'clock), moving smoothly between the marks.
  function angles(t) {
    return {
      hour: ((t.h % 12) + t.m / 60 + t.s / 3600) * 30,
      minute: (t.m + t.s / 60) * 6,
      second: t.s * 6,
    };
  }

  // ---- Word clock (English only). The classic 11 x 10 letter grid; each word is
  // [row, first column, length].
  var GRID = [
    "ITLISASAMPM", "ACQUARTERDC", "TWENTYFIVEX", "HALFSTENFTO", "PASTERUNINE",
    "ONESIXTHREE", "FOURFIVETWO", "EIGHTELEVEN", "SEVENTWELVE", "TENSEOCLOCK",
  ];
  var WORDS = {
    IT: [0, 0, 2], IS: [0, 3, 2], AM: [0, 7, 2], PM: [0, 9, 2],
    QUARTER: [1, 2, 7], TWENTY: [2, 0, 6], FIVE_M: [2, 6, 4], HALF: [3, 0, 4], TEN_M: [3, 5, 3],
    TO: [3, 9, 2], PAST: [4, 0, 4],
    NINE: [4, 7, 4], ONE: [5, 0, 3], SIX: [5, 3, 3], THREE: [5, 6, 5], FOUR: [6, 0, 4],
    FIVE: [6, 4, 4], TWO: [6, 8, 3], EIGHT: [7, 0, 5], ELEVEN: [7, 5, 6], SEVEN: [8, 0, 5],
    TWELVE: [8, 5, 6], TEN: [9, 0, 3], OCLOCK: [9, 5, 6],
  };
  var HOURS = ["TWELVE", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
               "TEN", "ELEVEN"];
  var MINUTES = [[], ["FIVE_M", "PAST"], ["TEN_M", "PAST"], ["QUARTER", "PAST"],
                 ["TWENTY", "PAST"], ["TWENTY", "FIVE_M", "PAST"], ["HALF", "PAST"],
                 ["TWENTY", "FIVE_M", "TO"], ["TWENTY", "TO"], ["QUARTER", "TO"], ["TEN_M", "TO"],
                 ["FIVE_M", "TO"]];

  // words(h, m) -> {words: [ids], dots: minutes past the 5-minute mark (0-4), text}.
  // Like the classic word clock the time is floored to five minutes; the dots add the rest.
  function words(h, m, withPeriod) {
    var step = Math.floor(m / 5);
    var hour = (step >= 7 ? h + 1 : h) % 24;
    var list = ["IT", "IS"].concat(MINUTES[step]);
    list.push(HOURS[hour % 12]);
    if (step === 0) list.push("OCLOCK");
    if (withPeriod) list.push(h < 12 ? "AM" : "PM");
    var text = list.filter(function (w) { return w !== "AM" && w !== "PM"; })
      .map(function (w) { return w === "OCLOCK" ? "O'CLOCK" : w.replace(/_M$/, ""); })
      .join(" ").replace("TWENTY FIVE", "TWENTY-FIVE");
    return { words: list, dots: m % 5, text: text };
  }

  // Which grid cells to light: an object "row,col" -> true.
  function litCells(list) {
    var lit = {};
    list.forEach(function (w) {
      var d = WORDS[w];
      if (!d) return;
      for (var i = 0; i < d[2]; i++) lit[d[0] + "," + (d[1] + i)] = true;
    });
    return lit;
  }

  // ---- DOM helpers for the themes (textContent only; called after load, never at load).
  function el(tag, cls, parent, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  // Sets text only when it changed (no layout work on most ticks).
  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }
  // The common layout: an optional label, the theme's face and the date line.
  //   <div class="clk-stage clk-{kind}"> .clk-label  .clk-face  .clk-date </div>
  function stage(root, opts, kind) {
    var s = el("div", "clk-stage clk-" + kind, root);
    var label = el("div", "clk-label", s, opts.label);
    if (!opts.label) label.hidden = true;
    var face = el("div", "clk-face", s);
    var date = el("div", "clk-date", s);
    if (!opts.showDate) date.hidden = true;
    return { stage: s, label: label, face: face, date: date };
  }

  g.Clock = {
    themes: {},
    pad: pad, parts: parts, read: read, angles: angles,
    words: words, litCells: litCells, GRID: GRID, WORDS: WORDS,
    el: el, setText: setText, stage: stage,
  };
})(typeof window !== "undefined" ? window : globalThis);
