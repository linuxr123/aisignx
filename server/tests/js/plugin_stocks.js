// Pure helpers of static/player/plugins/stocks/main.js, run in Node by test_plugin_stocks.py.
// Usage: node plugin_stocks.js <path to main.js>. Prints "ok" or exits non-zero.
"use strict";
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(process.argv[2], "utf8");
const win = {};
vm.runInNewContext(src, { window: win, document: {}, Intl, JSON, Math, Number, String, Array, Object });
const S = win.AiSignXStocks;
assert.ok(S, "main.js exports window.AiSignXStocks");
const plain = (v) => JSON.parse(JSON.stringify(v));   // objects from the vm realm

// settings: defaults and unknown values
assert.deepStrictEqual(plain(S.settings({})), { title: "", mode: "grid", speed: "normal", showName: true, showPct: true, theme: "dark" });
assert.deepStrictEqual(plain(S.settings({ mode: "ticker", speed: "fast", show_name: false, show_pct: false, theme: "light", title: " M " })),
  { title: "M", mode: "ticker", speed: "fast", showName: false, showPct: false, theme: "light" });
assert.strictEqual(S.settings({ mode: "spiral", speed: "warp" }).mode, "grid");
assert.strictEqual(S.settings({ speed: "warp" }).speed, "normal");

// direction
assert.strictEqual(S.direction({ price: 1, change: 0.5 }), "up");
assert.strictEqual(S.direction({ price: 1, change: -0.5 }), "down");
assert.strictEqual(S.direction({ price: 1, change: 0 }), "flat");
assert.strictEqual(S.direction({ price: null, change: 1 }), "flat");
assert.strictEqual(S.direction({ price: 1, change: null, pct: -2 }), "down");

// symbols
assert.strictEqual(S.displaySymbol("AAPL"), "AAPL");
assert.strictEqual(S.displaySymbol("OANDA:EUR_USD"), "EUR/USD");
assert.strictEqual(S.displaySymbol("BINANCE:BTCUSDT"), "BTCUSDT");

// numbers (en-US)
const L = "en-US";
assert.strictEqual(S.formatPrice(189.84, "USD", L), "$189.84");
assert.strictEqual(S.formatPrice(1208.8, "USD", L), "$1,208.80");
assert.strictEqual(S.formatPrice(182.4, "EUR", L), "€182.40");
assert.strictEqual(S.formatPrice(3412, "JPY", L), "¥3,412");        // the currency's own digits
assert.strictEqual(S.formatPrice(0.92341, null, L), "0.9234");          // below 1: four digits
assert.strictEqual(S.formatPrice(67012.451, "", L), "67,012.45");
assert.strictEqual(S.formatPrice(5, "xx1", L), "5.00");                  // bad currency: plain
assert.strictEqual(S.formatPrice(null, "USD", L), "—");
assert.strictEqual(S.formatPrice(NaN, "USD", L), "—");
assert.strictEqual(S.formatChange(1.234, 189, L), "+1.23");
assert.strictEqual(S.formatChange(-3.1, 415, L), "−3.10");
assert.strictEqual(S.formatChange(0, 415, L), "0.00");
assert.strictEqual(S.formatChange(-0.0012, 0.92, L), "−0.0012");
assert.strictEqual(S.formatChange(null, 1, L), "—");
assert.strictEqual(S.formatPct(0.6522, L), "+0.65%");
assert.strictEqual(S.formatPct(-12.5, L), "−12.50%");
assert.strictEqual(S.formatPct(undefined, L), "—");
assert.strictEqual(S.formatPrice(1234.5, "EUR", "de-DE").replace(/\s/g, " "), "1.234,50 €");

// "As of"
const fmt = (ms, o) => new Intl.DateTimeFormat("en-GB", Object.assign({ timeZone: "UTC" }, o)).format(ms);
const noon = Date.UTC(2026, 10, 14, 12, 0);
assert.strictEqual(S.asOfLabel(Date.UTC(2026, 10, 14, 9, 32), noon, fmt), "As of 09:32");
assert.strictEqual(S.asOfLabel(Date.UTC(2026, 10, 13, 21, 0), noon, fmt), "As of 13 Nov, 21:00");
assert.strictEqual(S.asOfLabel(0, noon, fmt), "");

// grid layout: cards fill landscape and portrait screens
const land = S.gridLayout(8, 1840, 830, 22, 1080, true);
assert.ok(land.cols >= 3 && land.cols <= 4, JSON.stringify(land));
const port = S.gridLayout(8, 1000, 1650, 22, 1080, true);
assert.strictEqual(port.cols, 2, JSON.stringify(port));
for (const g of [land, port]) {
  assert.ok(g.cols * g.rows >= 8);
  assert.ok(g.cardW * g.cols <= (g.cols === 2 ? 1000 : 1840));
  assert.ok(g.u > 15, "readable: " + g.u);
}
const one = S.gridLayout(1, 1840, 830, 22, 1080, true);
assert.strictEqual(one.cols, 1);
assert.ok(one.u <= 1080 * 0.055 + 1e-9, "one card is capped");
const twenty = S.gridLayout(20, 1840, 830, 22, 1080, true);
assert.ok(twenty.cols * twenty.rows >= 20 && twenty.cardH * twenty.rows <= 830);

// board layout: two columns only when it pays off on a wide screen
assert.strictEqual(S.boardLayout(5, 1840, 830, 32, 1080, true, true).cols, 1);
assert.strictEqual(S.boardLayout(14, 1840, 830, 32, 1080, true, true).cols, 2);
assert.strictEqual(S.boardLayout(14, 1000, 1650, 32, 1080, true, true).cols, 1);
const b = S.boardLayout(8, 1000, 1650, 32, 1080, true, true);
assert.ok(b.rowH >= b.u * 2.8 && b.rowH * 8 <= 1650, JSON.stringify(b));

// ticker
assert.strictEqual(S.tickerBands(8, 1840, 830), 2);
assert.strictEqual(S.tickerBands(3, 1840, 830), 1);
assert.strictEqual(S.tickerBands(8, 1000, 1650), 3);
assert.strictEqual(S.tickerBands(3, 1000, 1650), 1);
assert.strictEqual(S.tickerBands(1, 1000, 1650), 1);
assert.ok(S.tickerSeconds(4000, 10.8, "slow") > S.tickerSeconds(4000, 10.8, "fast"));
assert.strictEqual(S.tickerSeconds(10, 10.8, "normal"), 6);   // never faster than 6 s a lap

console.log("ok");
