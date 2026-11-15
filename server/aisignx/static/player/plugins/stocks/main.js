// AiSignX stocks plugin, frame runner. Three layouts:
//   grid    a card per symbol, sized so the cards fill the screen (16:9 and 9:16)
//   board   one row per symbol, in one or two columns
//   ticker  bands that scroll without end (more than one on a tall screen); refreshes
//           update the text in place, so the band never restarts
// Quotes come from the server ({quotes:[{symbol, name, price, change, pct, currency, ts}],
// as_of_ms, attribution, delayed}); the API key never reaches the screen.
// Classic script, ES2018; DOM built with textContent only; timers through ctx.
(function () {
  "use strict";

  var DASH = "\u2014", UP = "\u25B2", DOWN = "\u25BC";
  var SPEEDS = { slow: 7, normal: 11, fast: 16 };   // ticker, vmin per second
  var MODES = { grid: 1, board: 1, ticker: 1 };
  var CARD = { w: 17, h: 9.4, hNoName: 8 };          // card size in --u units
  var ROW = { h: 2.8, hNoName: 2.0, head: 1.6, w: 38.5, wNoPct: 30 };
  var FLASH_MS = 1600;

  // ---- pure helpers (also used by tests/js/plugin_stocks.js) -------------------------------

  function settings(config) {
    var c = config || {};
    return {
      title: typeof c.title === "string" ? c.title.trim() : "",
      mode: MODES[c.mode] ? c.mode : "grid",
      speed: SPEEDS[c.speed] ? c.speed : "normal",
      showName: c.show_name !== false,
      showPct: c.show_pct !== false,
      theme: c.theme === "light" ? "light" : "dark",
    };
  }

  function num(v) { return typeof v === "number" && isFinite(v) ? v : null; }

  function direction(q) {
    var ch = num(q && q.change), pct = num(q && q.pct);
    var v = ch !== null ? ch : pct;
    if (num(q && q.price) === null || v === null || v === 0) return "flat";
    return v > 0 ? "up" : "down";
  }

  // "OANDA:EUR_USD" -> "EUR/USD", "BINANCE:BTCUSDT" -> "BTCUSDT", "AAPL" -> "AAPL".
  function displaySymbol(symbol) {
    var s = String(symbol || "");
    var i = s.lastIndexOf(":");
    if (i >= 0 && i < s.length - 1) s = s.slice(i + 1);
    return s.replace(/_/g, "/");
  }

  // Fraction digits: 2 for normal prices, up to 4 below 1 (penny stocks, forex, crypto).
  function digits(price) {
    var p = Math.abs(num(price) || 0);
    return p > 0 && p < 1 ? 4 : 2;
  }

  var formats = {};
  function nf(locale, opts) {
    var key = (locale || "") + "|" + JSON.stringify(opts);
    if (!formats[key]) {
      try { formats[key] = new Intl.NumberFormat(locale || undefined, opts); } catch (e) {
        try { formats[key] = new Intl.NumberFormat(undefined, opts); } catch (e2) {
          formats[key] = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
        }
      }
    }
    return formats[key];
  }

  function formatPrice(price, currency, locale) {
    price = num(price);
    if (price === null) return DASH;
    var d = digits(price);
    var cur = typeof currency === "string" && /^[A-Z]{3}$/.test(currency) ? currency : "";
    if (cur) {
      try {
        var o = { style: "currency", currency: cur };
        if (d > 2) { o.minimumFractionDigits = d; o.maximumFractionDigits = d; }
        return nf(locale, o).format(price);
      } catch (e) { /* unknown currency: plain number below */ }
    }
    return nf(locale, { minimumFractionDigits: d, maximumFractionDigits: d }).format(price);
  }

  function signed(v, text) { return (v > 0 ? "+" : v < 0 ? "\u2212" : "") + text; }

  function formatChange(change, price, locale) {
    change = num(change);
    if (change === null) return DASH;
    var d = digits(price);
    return signed(change, nf(locale, { minimumFractionDigits: d, maximumFractionDigits: d }).format(Math.abs(change)));
  }

  function formatPct(pct, locale) {
    pct = num(pct);
    if (pct === null) return DASH;
    return signed(pct, nf(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(pct))) + "%";
  }

  // "As of 14:32", or "As of 14 Nov, 14:32" when the quotes are not from today.
  function asOfLabel(asOf, now, format) {
    asOf = Number(asOf);
    if (!asOf) return "";
    var day = { year: "numeric", month: "2-digit", day: "2-digit" };
    var today = format(asOf, day) === format(now, day);
    return "As of " + format(asOf, today ? { hour: "2-digit", minute: "2-digit" }
      : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  // Cards: the column count that gives the largest cards. u = size unit in px.
  function gridLayout(n, W, H, gap, vmin, showName) {
    var ch = showName ? CARD.h : CARD.hNoName;
    var best = null;
    n = Math.max(1, n);
    for (var cols = 1; cols <= n; cols++) {
      var rows = Math.ceil(n / cols);
      var cellW = (W - gap * (cols - 1)) / cols, cellH = (H - gap * (rows - 1)) / rows;
      var u = Math.min(cellW / CARD.w, cellH / ch);
      if (!best || u > best.u + 0.01) best = { cols: cols, rows: rows, cellW: cellW, cellH: cellH, u: u };
    }
    var uMax = vmin * 0.055;
    var u2 = Math.max(4, Math.min(best.u, uMax));
    return {
      cols: best.cols, rows: best.rows, u: u2,
      cardW: Math.floor(Math.min(best.cellW, u2 * CARD.w * 1.45)),
      cardH: Math.floor(Math.min(best.cellH, u2 * ch * 1.3)),
    };
  }

  // Board: one column, or two when that gives larger rows (many symbols on a wide screen).
  function boardLayout(n, W, H, gap, vmin, showName, showPct) {
    var rh = showName ? ROW.h : ROW.hNoName, rw = showPct ? ROW.w : ROW.wNoPct;
    var best = null;
    n = Math.max(1, n);
    for (var cols = 1; cols <= (n >= 6 ? 2 : 1); cols++) {
      var per = Math.ceil(n / cols);
      var colW = (W - gap * (cols - 1)) / cols;
      var u = Math.min(colW / rw, H / (per * rh + ROW.head));
      if (!best || u > best.u * 1.15) best = { cols: cols, per: per, u: u };
    }
    var u2 = Math.max(4, Math.min(best.u, vmin * 0.05));
    // Rows grow (up to 1.8x) into spare height, so a tall screen is not mostly empty.
    var rowH = Math.max(u2 * rh, Math.min((H - u2 * ROW.head) / best.per, u2 * rh * 1.8));
    return { cols: best.cols, per: best.per, u: u2, rowH: Math.floor(rowH),
             colW: Math.floor(Math.min((W - gap * (best.cols - 1)) / best.cols, u2 * rw * 1.6)) };
  }

  // Ticker bands: up to two on a wide screen (so a 16:9 slide is not one thin strip in the
  // middle), up to three on a tall one; two symbols per band at least.
  function tickerBands(n, W, H) {
    var most = H > W * 1.2 ? 3 : 2;
    return Math.max(1, Math.min(most, Math.floor(n / 2)));
  }

  function tickerSeconds(runWidthPx, vminPx, speed) {
    var pxs = Math.max(1, (SPEEDS[speed] || SPEEDS.normal) * (vminPx || 1));
    return Math.max(6, runWidthPx / pxs);
  }

  // ---- DOM ---------------------------------------------------------------------------------

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function vmin() { return Math.min(window.innerWidth || 1, window.innerHeight || 1) / 100; }

  // Shrinks an element's font (never grows it) until its text fits on one line.
  function fitText(node) {
    node.style.fontSize = "";
    var cw = node.clientWidth, sw = node.scrollWidth;
    if (!cw || sw <= cw + 1) return;
    var px = parseFloat(window.getComputedStyle(node).fontSize) || 16;
    node.style.fontSize = Math.max(6, Math.floor(px * cw / sw * 0.97)) + "px";
  }

  function start(ctx) {
    var S = { cfg: settings(ctx.config), data: null, prev: {}, paused: false, ticks: null, resizeTimer: null };
    var loc = ctx.locale || undefined;
    var root = ctx.root;
    root.className = "stk" + (S.cfg.theme === "light" ? " stk-light" : "");
    var app = root.appendChild(el("div", "stk-app"));
    var head = app.appendChild(el("div", "stk-head"));
    var title = S.cfg.title ? head.appendChild(el("h1", "stk-title", S.cfg.title)) : null;
    var meta = head.appendChild(el("div", "stk-meta"));
    var body = app.appendChild(el("div", "stk-body"));
    if (!title) head.classList.add("stk-head-plain");

    function quotes() { return (S.data && Array.isArray(S.data.quotes)) ? S.data.quotes.filter(function (q) { return q && q.symbol; }) : []; }

    function texts(q) {
      return {
        sym: displaySymbol(q.symbol),
        name: typeof q.name === "string" ? q.name : "",
        price: formatPrice(q.price, q.currency, loc),
        change: formatChange(q.change, q.price, loc),
        pct: formatPct(q.pct, loc),
        dir: direction(q),
      };
    }
    function arrow(dir) { return dir === "up" ? UP + " " : dir === "down" ? DOWN + " " : ""; }
    function dirClass(node, base, dir) { node.className = base + " stk-" + dir; }

    // A price that moved since the last refresh flashes once.
    function flash(node, q) {
      var before = S.prev[q.symbol], now = num(q.price);
      if (before == null || now === null || before === now) return;
      var cls = now > before ? "stk-flash-up" : "stk-flash-down";
      node.classList.add(cls);
      ctx.later(function () { node.classList.remove(cls); }, FLASH_MS);
    }
    function remember() {
      quotes().forEach(function (q) { var p = num(q.price); if (p !== null) S.prev[q.symbol] = p; });
    }

    function header() {
      while (meta.firstChild) meta.removeChild(meta.firstChild);
      var label = asOfLabel(S.data && S.data.as_of_ms, ctx.now(), ctx.format);
      if (label) meta.appendChild(el("span", "stk-asof", label));
      if (S.data && S.data.delayed) meta.appendChild(el("span", "stk-delayed", "Prices may be delayed"));
      ctx.staleBadge(ctx.stale || ctx.offline ? ctx.fetchedAt : 0);
      ctx.attribution((S.data && typeof S.data.attribution === "string" && S.data.attribution) || null);
    }

    function clearBody() { while (body.firstChild) body.removeChild(body.firstChild); S.ticks = null; }

    // ---- grid
    function card(q) {
      var t = texts(q), c = el("div", "");
      dirClass(c, "stk-card", t.dir);
      var top = c.appendChild(el("div", "stk-card-top"));
      top.appendChild(el("div", "stk-sym", t.sym));
      top.appendChild(el("div", "stk-pill", arrow(t.dir) + (S.cfg.showPct ? t.pct.replace(/^[+\u2212]/, "") : t.change.replace(/^[+\u2212]/, ""))));
      if (S.cfg.showName) c.appendChild(el("div", "stk-name", t.name || "\u00A0"));
      var price = c.appendChild(el("div", "stk-price", t.price));
      if (S.cfg.showPct) c.appendChild(el("div", "stk-chg", t.change));
      flash(price, q);
      return c;
    }
    function drawGrid(list) {
      var gap = Math.round(vmin() * 2);
      var L = gridLayout(list.length, body.clientWidth, body.clientHeight, gap, vmin() * 100, S.cfg.showName);
      var grid = body.appendChild(el("div", "stk-grid"));
      grid.style.setProperty("--u", L.u.toFixed(2) + "px");
      grid.style.gap = gap + "px";
      grid.style.maxWidth = (L.cardW * L.cols + gap * (L.cols - 1) + 2) + "px";
      list.forEach(function (q) {
        var c = grid.appendChild(card(q));
        c.style.width = L.cardW + "px";
        c.style.height = L.cardH + "px";
      });
      fitAll(grid);
    }

    // ---- board
    function row(q) {
      var t = texts(q), r = el("div", "");
      dirClass(r, "stk-row", t.dir);
      var id = r.appendChild(el("div", "stk-id"));
      id.appendChild(el("div", "stk-sym", t.sym));
      if (S.cfg.showName && t.name) id.appendChild(el("div", "stk-name", t.name));
      flash(r.appendChild(el("div", "stk-price", t.price)), q);
      r.appendChild(el("div", "stk-chg", t.change));
      if (S.cfg.showPct) r.appendChild(el("div", "stk-pill", arrow(t.dir) + t.pct.replace(/^[+\u2212]/, "")));
      return r;
    }
    function drawBoard(list) {
      var gap = Math.round(vmin() * 3);
      var L = boardLayout(list.length, body.clientWidth, body.clientHeight, gap, vmin() * 100, S.cfg.showName, S.cfg.showPct);
      var wrap = body.appendChild(el("div", "stk-boards"));
      wrap.style.setProperty("--u", L.u.toFixed(2) + "px");
      wrap.style.setProperty("--rowh", L.rowH + "px");
      wrap.style.gap = gap + "px";
      for (var c = 0; c < L.cols; c++) {
        var part = list.slice(c * L.per, (c + 1) * L.per);
        if (!part.length) break;
        var b = wrap.appendChild(el("div", "stk-board" + (S.cfg.showPct ? "" : " stk-nopct")));
        b.style.width = L.colW + "px";
        var h = b.appendChild(el("div", "stk-row stk-row-head"));
        ["Symbol", "Price", "Change"].concat(S.cfg.showPct ? ["%"] : []).forEach(function (s) { h.appendChild(el("div", "", s)); });
        part.forEach(function (q) { b.appendChild(row(q)); });
      }
      fitAll(wrap);
    }

    function fitAll(scope) {
      var nodes = scope.querySelectorAll(".stk-price, .stk-sym");
      for (var i = 0; i < nodes.length; i++) fitText(nodes[i]);
    }

    // ---- ticker
    function tick(q) {
      var node = el("div", "stk-tick"), parts = {};
      parts.sym = node.appendChild(el("span", "stk-sym"));
      if (S.cfg.showName) parts.name = node.appendChild(el("span", "stk-name"));
      parts.price = node.appendChild(el("span", "stk-price"));
      parts.chg = node.appendChild(el("span", "stk-chg"));
      parts.node = node;
      return parts;
    }
    function fillTick(p, q) {
      var t = texts(q);
      p.sym.textContent = t.sym;
      if (p.name) { p.name.textContent = t.name; p.name.hidden = !t.name; }
      p.price.textContent = t.price;
      p.chg.textContent = arrow(t.dir) + (t.dir === "flat" && t.change === DASH ? DASH : t.change.replace(/^[+\u2212]/, "")) +
        (S.cfg.showPct && t.pct !== DASH ? "\u2002" + t.pct : "");
      dirClass(p.node, "stk-tick", t.dir);
      flash(p.price, q);
    }
    function drawBand(box, list) {
      var band = box.appendChild(el("div", "stk-band"));
      var track = band.appendChild(el("div", "stk-track"));
      var runA = track.appendChild(el("div", "stk-run"));
      var T = { list: list, parts: [], track: track };
      // Repeat the symbols inside one run until it is wider than the band, so no gap shows.
      var copies = 0, bandW = band.clientWidth || window.innerWidth;
      do {
        list.forEach(function (q, i) { var p = tick(q); runA.appendChild(p.node); fillTick(p, q); T.parts.push({ p: p, q: q.symbol, i: i }); });
        copies++;
      } while (runA.scrollWidth < bandW && copies < 12);
      T.clone = track.appendChild(runA.cloneNode(true));
      T.clone.setAttribute("aria-hidden", "true");
      track.style.animationDuration = tickerSeconds(runA.scrollWidth, vmin(), S.cfg.speed).toFixed(2) + "s";
      track.classList.add("stk-go");
      track.style.animationPlayState = S.paused ? "paused" : "running";
      return T;
    }
    function drawTicker(list) {
      var box = body.appendChild(el("div", "stk-ticker"));
      var n = tickerBands(list.length, body.clientWidth, body.clientHeight), per = Math.ceil(list.length / n);
      S.ticks = { symbols: symbolsOf(list), bands: [] };
      for (var b = 0; b < n; b++) S.ticks.bands.push(drawBand(box, list.slice(b * per, (b + 1) * per)));
    }
    function symbolsOf(list) { return list.map(function (q) { return q.symbol; }).join(","); }
    // Refresh without restarting the bands: same symbols -> update the text in place.
    function updateTicker(list) {
      if (!S.ticks || S.ticks.symbols !== symbolsOf(list)) return false;
      var by = {};
      list.forEach(function (q) { by[q.symbol] = q; });
      S.ticks.bands.forEach(function (T) {
        T.parts.forEach(function (x) { if (by[x.q]) fillTick(x.p, by[x.q]); });
        var copy = T.track.firstChild.cloneNode(true);
        copy.setAttribute("aria-hidden", "true");
        T.track.replaceChild(copy, T.clone);
        T.clone = copy;
      });
      return true;
    }

    function draw(isRefresh) {
      header();
      var list = quotes();
      if (!list.length) { clearBody(); ctx.fallback("Stocks", "No quotes yet"); return; }
      ctx.fallback(null);
      if (!(isRefresh && S.cfg.mode === "ticker" && updateTicker(list))) {
        clearBody();
        if (S.cfg.mode === "ticker") drawTicker(list);
        else if (S.cfg.mode === "board") drawBoard(list);
        else drawGrid(list);
      }
      remember();
      ctx.ready();
    }

    function setPaused(p) {
      S.paused = p;
      if (S.ticks) S.ticks.bands.forEach(function (T) { T.track.style.animationPlayState = p ? "paused" : "running"; });
    }

    // No data yet (cold cache, or offline with no saved copy): a card that says why.
    function waiting() {
      ctx.fallback("Stocks", ctx.offline ? "This screen is offline" : "Loading quotes\u2026");
    }

    function begin() {
      if (ctx.data) { S.data = ctx.data; draw(false); return; }
      waiting();
    }

    window.addEventListener("resize", function () {
      if (S.resizeTimer) ctx.clear(S.resizeTimer);
      S.resizeTimer = ctx.later(function () { S.resizeTimer = null; if (S.data) draw(false); }, 250);
    });

    return {
      begin: begin,
      data: function () {
        if (!ctx.data) { if (S.data) header(); else waiting(); return; }   // keep what is shown
        var had = !!S.data;
        S.data = ctx.data;
        draw(had);
      },
      net: function () { if (S.data) header(); else waiting(); },
      pause: function () { setPaused(true); },
      resume: function () { setPaused(false); },
    };
  }

  if (window.AiSignXPlugin) {
    var app = null;
    window.AiSignXPlugin.register({
      init: function (ctx) { app = start(ctx); app.begin(); },
      data: function () { if (app) app.data(); },
      net: function () { if (app) app.net(); },
      pause: function () { if (app) app.pause(); },
      resume: function () { if (app) app.resume(); },
    });
  }

  window.AiSignXStocks = {
    settings: settings, direction: direction, displaySymbol: displaySymbol, digits: digits,
    formatPrice: formatPrice, formatChange: formatChange, formatPct: formatPct, asOfLabel: asOfLabel,
    gridLayout: gridLayout, boardLayout: boardLayout, tickerBands: tickerBands, tickerSeconds: tickerSeconds,
  };
})();
