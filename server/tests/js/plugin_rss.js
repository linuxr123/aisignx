// Pure helpers of static/player/plugins/rss/main.js, run in Node: node plugin_rss.js <main.js>
"use strict";
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const window = { innerWidth: 1920, innerHeight: 1080 };
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), { window, Intl, Math, Number, Promise, isFinite });
const R = window.AiSignXRss;
const plain = (x) => JSON.parse(JSON.stringify(x));  // objects from the vm realm
assert.ok(R, "main.js exports AiSignXRss");

// settings(): defaults and clamping
let s = R.settings({});
assert.deepStrictEqual(plain(s), { title: "", mode: "list", rotateMs: 10000, showImages: true, showDates: true,
  showSource: true, showQr: false, theme: "dark" });
s = R.settings({ mode: "bogus", rotate_seconds: 999, theme: "light", show_images: false, show_qr: 1, title: "  Hi " });
assert.strictEqual(s.mode, "list");
assert.strictEqual(s.rotateMs, 60000);
assert.strictEqual(s.theme, "light");
assert.strictEqual(s.showImages, false);
assert.strictEqual(s.showQr, true);
assert.strictEqual(s.title, "Hi");
assert.strictEqual(R.settings({ rotate_seconds: 1 }).rotateMs, 5000);
assert.strictEqual(R.settings({ mode: "ticker" }).mode, "ticker");

// feedTitle(): config title, feed title, host, "News"
assert.strictEqual(R.feedTitle({ title: "Mine" }, { feed: { title: "Feed" } }), "Mine");
assert.strictEqual(R.feedTitle({ title: "" }, { feed: { title: "Feed" } }), "Feed");
assert.strictEqual(R.feedTitle({ title: "" }, { feed: { title: "", link_host: "h.test" } }), "h.test");
assert.strictEqual(R.feedTitle({ title: "" }, null), "News");

// initial(): the letter on the tile that stands in for a missing picture
assert.strictEqual(R.initial("harbour news"), "H");
assert.strictEqual(R.initial("  «2026» review"), "2");
assert.strictEqual(R.initial("Ålesund"), "Å");
assert.strictEqual(R.initial("…"), "");
assert.strictEqual(R.initial(null), "");

// when(): relative for the last day, a date otherwise
const now = Date.UTC(2026, 10, 15, 12, 0, 0);
const fmt = () => "DATE";
assert.strictEqual(R.when(now, 0, "en", fmt), "");
assert.strictEqual(R.when(now, now - 30000, "en", fmt), "this minute");
assert.strictEqual(R.when(now, now - 5 * 60000, "en", fmt), "5 minutes ago");
assert.strictEqual(R.when(now, now - 3 * 3600000, "en", fmt), "3 hours ago");
assert.strictEqual(R.when(now, now - 3 * 86400000, "en", fmt), "DATE");
assert.strictEqual(R.when(now, now + 2 * 3600000, "en", fmt), "DATE");  // future: a date

// paginate(): rows fill pages; an oversized row still gets a page of its own
assert.deepStrictEqual(plain(R.paginate([100, 100, 100, 100], 10, 300)), [[0, 1], [2, 3]]);
assert.deepStrictEqual(plain(R.paginate([100, 100, 100], 10, 300)), [[0, 1], [2]]);
assert.deepStrictEqual(plain(R.paginate([100, 100, 100], 10, 320)), [[0, 1, 2]]);
assert.deepStrictEqual(plain(R.paginate([500, 50], 10, 300)), [[0], [1]]);
assert.deepStrictEqual(plain(R.paginate([], 10, 300)), []);
// balanced: 4 + 4 + 1 becomes 3 + 3 + 3; uneven heights keep the greedy split when needed
assert.deepStrictEqual(plain(R.paginate([100, 100, 100, 100, 100, 100, 100, 100, 100], 10, 430)), [[0, 1, 2], [3, 4, 5], [6, 7, 8]]);
assert.deepStrictEqual(plain(R.paginate([100, 100, 100, 100, 100], 10, 430)), [[0, 1, 2], [3, 4]]);
assert.deepStrictEqual(plain(R.paginate([50, 50, 50, 300, 300], 10, 320)), [[0, 1, 2], [3], [4]]);

// startIndex(): moves through the feed with wall time
assert.strictEqual(R.startIndex(123456, 10000, 1), 0);
assert.strictEqual(R.startIndex(0, 10000, 3), 0);
assert.strictEqual(R.startIndex(25000, 10000, 3), 2);
assert.strictEqual(R.startIndex(35000, 10000, 3), 0);

// tickerSeconds(): constant speed, never under 8 s
assert.strictEqual(R.tickerSeconds(100, 10), 8);
assert.ok(Math.abs(R.tickerSeconds(11000, 10) - 100) < 1e-9);

console.log("ok");
