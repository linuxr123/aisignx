# Player side of AiSignX plugins

Files here are served as static files and cached by the kiosk shells, which keep
`/player/` and `/static/player/*`. Every installed plugin's files are listed on the
player page (see `player/routes.py`), so a screen that has been online once can show
any plugin offline.

| File | Loaded by | Purpose |
|---|---|---|
| `host.js` | player page, admin preview | Builds frame plugins: handshake, data, assets, last good copy |
| `sdk.js`, `sdk.css` | every frame plugin's `index.html` | `AiSignXPlugin.register()` and the `ctx` helpers |
| `<key>/...` | the plugin | Runner files (frame) or `player_script` / `player_style` (native) |

## Frame plugins

A frame plugin runs in `<iframe sandbox="allow-scripts">`, so its origin is opaque. It has
no cookies, no storage, no network (`connect-src 'none'`) and no access to the player.
The player fetches its data and passes it in.

### Runner page (`<key>/index.html`)

Copy this exactly. Only the `style.css` and `main.js` lines may change (add more files
the same way, and list every one in plugin.json `files`).

```html
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src blob:; font-src 'self'; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<link rel="stylesheet" href="../sdk.css"><link rel="stylesheet" href="style.css">
</head><body><div id="root"></div><script src="../sdk.js"></script><script src="main.js"></script></body></html>
```

Rules (tests in `tests/test_plugins_runner_html.py` check the page):
- URLs are relative and have no query string.
- No inline `<script>` or `<style>`, no `on*=` attributes, no `eval`.
- Never put data into `innerHTML`. Use `textContent`, `createElement` and
  `createElementNS` (SVG).
- Set styles with CSSOM (`el.style.x = ...`) or classes, never `setAttribute("style", ...)`.
- Every timer goes through `ctx.every` / `ctx.later`, so `destroy` stops them all.
- Size things in `vmin` (or `vw`/`vh`) so landscape and portrait screens both work.
- The player owns the slide length. Never keep your own duration or loop count.

### main.js

```js
AiSignXPlugin.register({
  init: function (ctx) {             // once; ctx.data may be null (ctx.warming or offline)
    if (!ctx.data) { ctx.fallback("Weather", ctx.offline ? "This screen is offline" : "Loading…"); return; }
    draw(ctx);
    ctx.ready();                      // required: the slide shows only after ready (or fallback)
  },
  data: function (ctx) { ctx.fallback(null); draw(ctx); ctx.ready(); },   // fresh data
  show: function (ctx) {},            // the slide became visible
  net: function (ctx) {},             // ctx.offline changed
  pause: function (ctx) {}, resume: function (ctx) {},   // the viewer paused the player
  destroy: function (ctx) {},         // timers and object URLs are released by the SDK
});
```

Each hook is called as `hook(ctx, message)`. A throw in `init` (or a rejected promise from
an async `init`) fails the slide; a throw elsewhere is logged as a note.

If neither `ctx.ready()` nor `ctx.fallback()` is called within 8 seconds, the player skips
the slide.

### `ctx`

| Member | Meaning |
|---|---|
| `root` | The `#root` element |
| `key`, `config` | Plugin key and the item's public config (private fields never arrive) |
| `data`, `stale`, `warming`, `fetchedAt`, `assets` | The latest data and its state |
| `offline` | The screen cannot reach the server |
| `tz`, `locale` | Time zone (empty: the screen's) and BCP 47 locale (empty: the screen's) |
| `duration_ms` | The slide length, for animations that should fit it |
| `now()` | Server-calibrated time in ms |
| `format(ms, intlOptions)` | `Intl.DateTimeFormat` in `tz` and `locale` |
| `every(fn, ms)`, `later(fn, ms)`, `clear(id)` | Timers that stop on destroy |
| `ready()` | The slide can be shown |
| `complete()` | The plugin is finished; honoured only when plugin.json has `ends_itself` |
| `error(message, fatal)` | Report a problem; fatal skips the slide |
| `asset(id)` | Promise of an object URL for an asset in `ctx.assets`; revoked on destroy |
| `fallback(title, msg)` | Full-screen card; also tells the player the slide is degraded. `fallback(null)` removes it |
| `staleBadge(fetchedAtMs)` | "Updated 14:32" corner badge; a falsy value removes it |
| `attribution(text)` | Data-source line at the bottom; no argument uses plugin.json `attribution` |
| `fit(el)` | Scale `el` down to fit its parent; returns the scale |

A degraded slide (fallback card) moves on after at most 10 s when the playlist has more
than one item.

### Protocol (`host.js` and `sdk.js`)

1. The frame posts `{t: "aisignx:hello", v: 1}` to its parent.
2. The host accepts one hello per frame, only when `e.source` is that frame and
   `e.origin === "null"`. It answers `{t: "aisignx:init", v: 1}` with `port2` of a new
   `MessageChannel`. Later hellos are ignored.
3. Everything else uses the port.

Host to plugin: `init {key, config, data, stale, warming, fetched_at_ms, assets, offline,
server_offset_ms, tz, locale, duration_ms, attribution}`, `data {data, stale, warming,
fetched_at_ms, assets}`, `asset {id, mime, buf}` or `asset {id, error}`, `net {offline}`,
`show`, `pause`, `resume`, `destroy`.

Plugin to host: `ready`, `error {message, fatal}`, `complete`, `want-asset {id}`, `degraded`.

### Data

`host.js` fetches `item.plugin.data` (`/api/player/v1/plugins/<media id>/data`) with the
display cookie and `If-None-Match`:
- `init` waits up to 2.5 s for the first answer, then starts with the last good copy, or
  with `data: null, warming: true`.
- A `503` (warming or upstream unavailable) is retried after `Retry-After`, clamped to
  5-60 s. Network errors are retried after at most 30 s.
- While the slide is on screen, the data is refreshed every `refresh_s` (at least 15 s).
- The last good copy is kept in localStorage `aisignx.plugin.<media id>` (at most 256 KB
  each and 1.5 MB in all, least recently used first out), only for the same config.
- Assets are kept in Cache Storage `aisignx-plugin-v1` where it exists, otherwise in
  memory (64 MB, least recently used first out).

## Native plugins

A native plugin (plugin.json `render: "native"`) ships a `player_script` that the player
page loads with its own CSP (`script-src 'self'`, no external data). Use it only when a
frame cannot work: the YouTube embed and the image library.

```js
(window.AiSignX = window.AiSignX || {}).pluginNative = window.AiSignX.pluginNative || {};
window.AiSignX.pluginNative.example = {
  prepare: function (slide, api) { /* build into slide.el with api.attach(slide, el); return a Promise */ },
  run: function (slide, ctx) { /* on screen: ctx {done, fail, note, every, loop, degraded} */ },
  teardown: function (slide) {},
  pause: function (slide) {}, resume: function (slide) {},
  setVolume: function (slide, volume0to1) {}, setMuted: function (slide, forced) {},
};
```

- `slide.item` is the manifest item; `slide.item.plugin` holds the plugin block.
- `api` is `{attach(slide, el), fitClass(fit), volume(), prepCard(slide, title, msg)}`.
  `prepCard` draws a plain card, for example when the screen is offline.
- `prepare` must settle within 8 s, or the slide is skipped.
- `setMuted(slide, true)` is called while a fullscreen emergency is shown, and
  `setMuted(slide, false)` when it ends (restore the item's own mute setting).
- `ctx.done()` is honoured only for plugins with `ends_itself`, and is ignored while the
  viewer has paused the player.
- CSS classes must start with a prefix unique to the plugin: `pl-<key>-` or a short form
  of it (the image library uses `pl-imglib-`).

The server side (plugin.json, config fields, Python hooks) is described in `docs/plugins.md`.
