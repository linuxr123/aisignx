# Plugins

Plugins are library items that draw live content: a clock, the weather, a radar map, news
headlines, stock quotes, YouTube videos and image slideshows. A plugin item goes into
playlists like any image or video, with its own duration, transition and proof of play.

Seven plugins ship with AiSignX. There is no plugin upload or import: third-party plugins
need signing and review and are planned for later.

- [Using plugins](#using-plugins): adding items, organization settings, roles.
- [The bundled plugins](#the-bundled-plugins): options and data terms per plugin.
- [How plugins work](#how-plugins-work): data flow, offline behaviour, security.
- [Writing a plugin](#writing-a-plugin): the contract for plugin code in this repository.
- [Changes from v2](#changes-from-v2).

Firewall hosts, environment variables and licence notes are in
[deployment.md](deployment.md#plugins). The player API is in
[player-api.md](player-api.md#plugin-items).

## Using plugins

- **Add an item.** Media → **Add plugin** (or **Plugins** → **Add plugin item**), pick a plugin and fill
  in the form. **Preview** shows the slide at 16:9 or 9:16 before you save. The item then
  appears in the media library with a green "plugin" badge; add it to playlists as usual.
- **Edit.** Editing a plugin item from the library opens the same form. Screens pick up the
  change within about a second.
- **Location fields** (weather, radar) take a place name, a postal code or `lat,lon`. Click
  **Find** to choose from the matches. A name you type without **Find** is looked up when you
  save. `lat,lon` always works, with no lookup at all. The **Label** overrides the name shown
  on screen.
- **Plugins page** (`/plugins/`). Everyone can see each plugin's version, outbound hosts,
  data terms and API key status. Admins can also:
  - turn each plugin on or off. A plugin that is off disappears from **Add plugin** and from
    every screen at once; its items stay in the library and come back when it is turned on;
  - change each plugin's organization settings (weather provider, radar source and base map,
    RSS **Intranet feed hosts**, the stocks "delayed" label);
  - set **Contact for data providers** (`plugins.ua_contact`): an e-mail or URL sent to data
    providers in the `User-Agent` header. It is empty by default, so nothing about you is sent.
    MET Norway requires it;
  - set **Locale** (`plugins.locale`): a BCP 47 tag such as `nl-NL` for dates and numbers in
    plugins. Empty uses each screen's own language;
  - **Clear cache**: drop all fetched plugin data. Screens fetch fresh data on their next
    refresh.
- **Roles.** Admins change plugin settings and clear the cache. Editors create and edit items,
  preview unsaved forms and use **Find**. Viewers see the plugins page and item previews.
- **API keys** can be entered by an admin in the plugin's card on this page, or set as an
  environment variable (in `server/.env` or the service environment). A saved key is stored
  encrypted in the database (keyed from the instance secret key, so a backup restores it with the
  secret key) and never shown again, only "configured" or "missing". If the environment variable
  is set it wins, and the field says so. Changing a saved key takes effect without a restart.

**Duration.** The playlist item's duration (or the item's default duration) is how long the
slide stays on screen. Plugins never keep their own duration or loop count. Two exceptions:
YouTube with **Play to the end** ends the slide when the video or list ends (the duration is
then the maximum), and a plugin that can only show a "Loading" or "offline" card moves on after
at most 10 seconds when the playlist has more than one item.

## The bundled plugins

| Plugin | Shows | External data | Default length |
|---|---|---|---|
| Clock | Time and date in 5 styles | none, works offline | 10 s |
| Weather | Current conditions, hourly and daily forecast | Open-Meteo or MET Norway | 15 s |
| Weather radar | Animated precipitation radar on a map | RainViewer or IEM, OpenStreetMap | 20 s |
| RSS / Atom | Headlines as a list, one at a time, or a ticker | the feed you enter | 30 s |
| Stocks | Quotes as cards, a board or a ticker | Finnhub (API key) | 15 s |
| YouTube | Videos, a playlist or a live stream | youtube-nocookie.com, on the screen | 300 s (maximum) |
| Image library | Slideshow, Ken Burns or collage of library images | none | auto |

### Clock
Themes `minimal-dark`, `minimal-light`, `analog-classic`, `flip-clock` and `word-clock`;
24h or 12h; seconds; date (long or short); a time zone (empty means the organization's) and
a label. The word clock is English only and rounds to five minutes, with dots for the minutes
in between.

### Weather
- **Options:** location, metric or imperial units, layout (full, compact, current), 3-7 days,
  hourly row, theme (auto follows the weather and day or night, dark, light), refresh (at
  least 5 minutes, default 10).
- **Provider** (organization setting):
  - **Open-Meteo** (default). Free for **non-commercial** use under CC BY 4.0. A business needs
    an Open-Meteo API key in `AISIGNX_OPENMETEO_KEY`; AiSignX then uses
    `customer-api.open-meteo.com` and keeps the key out of its logs.
  - **MET Norway** (api.met.no). Free including commercial use, under CC BY 4.0 and NLOD.
    It requires a contact in the User-Agent, so it cannot be selected until **Contact for data
    providers** is set. AiSignX honours its `Expires` header and sends `If-Modified-Since`.
    Daily highs and lows are computed in the organization time zone.
- **Place lookup** at save: names through Open-Meteo geocoding, postal codes through
  OpenStreetMap Nominatim (at most one request per second for the whole server).
- After switching provider, screens show the new provider's data on their next refresh.

### Weather radar
- **Options:** location, zoom 5-10, 3-12 frames, 300-2000 ms per frame, picture shape
  (landscape, portrait, square), darkened base map, US weather alerts, refresh (at least 5
  minutes).
- **Source** (organization setting):
  - **RainViewer** (default, worldwide). Past frames only. Radar tiles are fetched at zoom 7
    at most and scaled up. Check RainViewer's current free-API limits before commercial use.
  - **IEM NEXRAD** (United States only), from the Iowa Environmental Mesonet. Free, no key.
- **Base map:** `https://tile.openstreetmap.org/{z}/{x}/{y}.png` by default. The OpenStreetMap
  tile servers allow only light use under their tile usage policy and require attribution,
  which is always drawn. **Busy or commercial deployments should set their own tile server**
  (https only, a `{z}/{x}/{y}` pattern) and its attribution.
- The server draws the frames in the background with Pillow, keeps map tiles for 30 days, and
  reuses each finished frame, so a refresh only fetches new radar times. Screens receive WebP
  pictures; there is no map library or tile traffic on the screen.
- **US alerts** come from api.weather.gov (public domain).

### RSS / Atom
- **Options:** feed URL, title, mode (list, single headline, ticker), 1-30 items, seconds per
  headline, images, dates, source, QR codes, theme, refresh (at least 2 minutes).
- The feed URL is a **private** field: it stays on the server and never reaches a screen.
- The server fetches and parses the feed (feedparser), strips all HTML to plain text, and
  re-encodes up to 10 images per feed to WebP (no SVG). QR codes are made for http(s) links
  only.
- Feeds may use http or https, on ports 80, 443, 8080 or 8443. Private network addresses are
  refused unless an admin lists the exact host name under **Intranet feed hosts**
  (`plugins.rss.allow_private_hosts`). Loopback and link-local addresses are always refused.
- Each feed host gets at most 6 requests per minute (feed and images together), so a feed's
  images can take a couple of refreshes to all appear.
- Make sure you may show the feed's content on public screens.

### Stocks
- **Options:** up to 20 symbols (for example `AAPL, MSFT, BINANCE:BTCUSDT`), title, mode (grid,
  board, ticker), ticker speed, company names, percent change, theme, refresh (at least 60 s).
- **Needs a Finnhub key** (enter it on the Plugins page, or set `AISIGNX_FINNHUB_KEY`). Without it, stocks slides are never sent to screens. The key
  is sent to Finnhub in a header and never leaves the server.
- **Terms:** Finnhub's free plan is for personal, non-commercial use. Showing quotes on public
  or business screens is redistribution and may need a paid Finnhub plan and exchange display
  licences. The **Show "Prices may be delayed"** setting adds that line to the slide.
- Quotes are cached for 60 s and company profiles for 7 days, per symbol, shared by every item
  and screen. A symbol that cannot be fetched keeps its last quote, or shows "—".

### YouTube
- **Options:** source (videos, playlist, live), up to 50 videos as `URL-or-ID [start] [end]`
  per line, playlist URL or ID, live video or `UC…` channel ID, shuffle, mute (default on),
  captions (in the plugin locale), play to the end (default on).
- Accepted links: `youtube.com/watch?v=`, `youtu.be/`, `/shorts/`, `/live/`, `/embed/`, the
  `m.` and `music.` hosts and `youtube-nocookie.com`. Other hosts are refused.
- Videos play in the privacy-enhanced `youtube-nocookie.com` embed with no controls.
  For several videos, start and end times apply to the first video only. `live_stream?channel=`
  (a channel's current live stream) is unreliable on YouTube's side; prefer a video ID.
- **Realities:**
  - Unmuted autoplay works in the Windows and Android apps. A plain browser only autoplays
    muted, unless started with `--autoplay-policy=no-user-gesture-required`.
  - Embeds may show ads.
  - Videos whose owner turned off embedding fail (errors 101/150) and are skipped.
  - Showing YouTube videos in public or in a business is subject to YouTube's terms and the
    rights of the video owners. That is the operator's responsibility.
  - The screen talks to YouTube directly, so it needs internet access; offline it shows a
    "Video unavailable" card.

### Image library
- **Options:** images you choose (up to 200) or the newest 1-100 images; slideshow, Ken Burns
  or collage (2×2 or 3×3); seconds per image; transition (fade, cut, slide); fit (cover or
  contain) and a blurred or black background; shuffle; captions with the image name.
- **Auto duration** (default on) sets the item's length to images × seconds per image (for a
  collage, one full round of tile swaps).
- "Newest" is worked out each time the manifest is built, so a new upload shows up on screens
  by itself. Deleted images are skipped.
- Images load with the normal media URLs, so the browser cache and the apps' media caches keep
  them for offline use, as for image items.

## How plugins work

There are two kinds:
- **Frame plugins** (clock, weather, radar, RSS, stocks) run in a sandboxed iframe
  (`sandbox="allow-scripts"`, an opaque origin) with `connect-src 'none'`. Plugin code has no
  network access, no cookies, no storage and no access to the player page.
- **Native plugins** (YouTube, image library) are first-party scripts loaded into the player
  page under its own CSP. They are used only where a frame cannot work.

**Data path.** Screens never talk to weather, news or quote providers. The AiSignX server
fetches the data, caches it and passes it on:

1. The player fetches `/api/player/v1/plugins/<id>/data` with the display cookie and hands the
   result to the plugin frame over a private `MessageChannel`.
2. The server answers from its file cache (`plugin-cache/`, beside `instance/`). Expired data
   is served at once while a background thread refreshes it (stale-while-revalidate). A
   request never waits more than 2 seconds on a provider; the first request for new data may
   answer "warming", and the slide shows "Loading…" until the data is there.
3. A failing provider keeps the last good data on screen with an "Updated 14:32" badge, and
   is retried with a backoff from 1 to 15 minutes.
4. Items with the same data options (the same place, rounded to about 1 km, or the same
   symbols) share one cache entry, whatever the number of screens.

**Offline.** Each screen keeps the last good data per item (and its pictures) and shows it,
with the "Updated" badge, while it cannot reach the server. The Windows and Android apps also
keep every plugin's player files, so a screen that was online once can start offline. The
clock and the image library work fully offline; YouTube does not.

**Cache.** The cache is capped at `AISIGNX_PLUGIN_CACHE_MB` (500 MB). A daily job removes data
nobody has read for 7 days. The cache is never part of a backup; after a restore the data is
simply fetched again.

**Security.**
- The outbound HTTP client allows only each plugin's listed hosts (plus RSS feeds and a
  custom radar base map), https only for providers, and refuses private, loopback, link-local
  and multicast addresses, including IPv4 hidden inside IPv6 (mapped, 6to4, Teredo, NAT64).
  It connects to the checked address so DNS cannot switch it, re-checks every redirect (at
  most 3), and caps time, size and decompression.
- Environment proxies are ignored. Set `AISIGNX_PLUGIN_HTTP_PROXY` to send plugin requests
  through an HTTPS proxy; the host allowlist still applies, but the private-address check can
  then only look at local DNS answers.
- API keys stay on the server. Private fields (the RSS feed URL) never reach a screen, the
  manifest or the API.
- Errors stored and shown are short codes (`timeout`, `http_503`, `blocked_address`, …), never
  provider text.
- On Android, `window.AiSignXShellInfo` (read-only: app version, device, client ID, admin
  URL, cache stats) is visible to every frame, including plugin frames and web page items. It
  holds no token or secret. The Windows app exposes nothing to frames.

## Writing a plugin

Plugins live in this repository:

```
server/aisignx/plugins/bundled/<key>/plugin.json      the definition
server/aisignx/plugins/bundled/<key>/__init__.py      optional Python hooks (+ helper modules)
server/aisignx/static/player/plugins/<key>/...        player files
server/tests/test_plugin_<key>.py, tests/js/plugin_<key>.js
```

The player side (runner page, `ctx`, the message protocol, native plugins) is documented in
[`server/aisignx/static/player/plugins/README.md`](../server/aisignx/static/player/plugins/README.md).

### plugin.json

```json
{
  "key": "weather", "name": "Weather", "version": "3.0.0", "icon": "cloud-sun",
  "description": "Current conditions and forecast.",
  "render": "frame",
  "files": ["index.html", "main.js", "icons.js", "style.css"],
  "default_duration": 15,
  "ends_itself": false,
  "data": {
    "ttl": 600, "min_ttl": 300, "fetch_budget_s": 15,
    "keys": ["location.lat", "location.lon", "units"],
    "hosts": ["api.open-meteo.com", "api.met.no"],
    "rate": {"api.open-meteo.com": 60, "api.met.no": 30},
    "max_bytes": 2097152
  },
  "schema": [{"name": "location", "type": "location", "label": "Location", "required": true}],
  "settings": [{"name": "provider", "type": "select", "label": "Provider",
                "options": [{"value": "open-meteo", "label": "Open-Meteo"},
                            {"value": "met-norway", "label": "MET Norway",
                             "requires_contact": true}]}],
  "secrets": [{"name": "openmeteo_key", "env": "AISIGNX_OPENMETEO_KEY", "label": "Open-Meteo API key"}],
  "attribution": "Weather data: Open-Meteo.com (CC BY 4.0)",
  "terms": "Shown on the plugins page and in the form."
}
```

Checked when the app starts (a bad plugin.json stops the app; tests cover each rule):
- `key` matches `^[a-z][a-z0-9_]{1,31}$` and equals the folder name.
- The file is UTF-8 **without** a byte-order mark.
- `render: "frame"` needs `index.html` in `files` and may have `data`. `render: "native"`
  needs a `player_script` (and optionally `player_style`) and may not have `data`.
- Every file in `files`, `player_script` and `player_style` exists under
  `static/player/plugins/<key>/`, matches `^[a-z0-9_\-./]+$` and contains no `..`.
- `hosts` are exact host names: no wildcards, IP addresses or ports.
- Field names `name`, `duration`, `loop`, `video_duration` and `enabled` are reserved.
- Secret `env` names match `AISIGNX_[A-Z0-9_]+`.

`data.keys` lists the config fields (dotted paths) that change the fetched data. Items whose
values match share one cache entry, so leave out display-only options. The plugin's
organization settings are always part of the cache key. `ttl` can be overridden by an item
field named `refresh`, never below `min_ttl`.

### Config fields (`schema` and `settings`)

`{name, type, label, help, default, required, min, max, max_length, options, pattern,
private, show_if}`

| Type | Stored as | Rules |
|---|---|---|
| `string` | str | trimmed; `max_length` (200); optional `pattern` (full match) |
| `text` | str | textarea; `max_length` (5000); line breaks kept |
| `integer` | int | `min` and `max` required; empty uses the default |
| `boolean` | bool | checkbox; absent is false |
| `select` | str | one of `options`; an option with `"requires_contact": true` needs `plugins.ua_contact` |
| `url` | str | http or https, no `user:password@`, at most 2048 characters |
| `timezone` | str | empty is the organization time zone; otherwise an IANA name |
| `location` | `{q, lat, lon, label}` | rounded to 4 decimals; set by **Find**, by typing `lat,lon`, or geocoded at save |
| `media_multi` | `[int]` | image media IDs, order kept, at most `max` (200) |

The server validates the whole config whatever `show_if` says, drops unknown keys and caps the
JSON at 16 KB. `private` fields never leave the server. `settings` are organization-wide, set
by admins on the plugins page, and stored as `plugins.<key>.<name>` settings.

### Python hooks (`bundled/<key>/__init__.py`, all optional)

```python
from aisignx.plugins.service import FetchResult, Asset, Context
from aisignx.plugins.schema import SchemaError

def normalize(config: dict, ctx) -> dict: ...          # after validation; raise SchemaError(msg, field)
def fetch(config: dict, ctx) -> FetchResult: ...       # background worker only
def manifest_extra(media, config: dict, ctx) -> dict: ...  # extra keys for the manifest's plugin block
def playable(media, config: dict, ctx) -> bool: ...    # cheap, no network
def auto_duration(config: dict) -> int | None: ...     # default item duration in seconds
```

- `normalize` must be idempotent: the stored config is validated again when the manifest is
  built.
- `fetch` returns `FetchResult(data, assets=[...], ttl=None)`. `data` must be JSON. Assets are
  PNG, JPEG or WebP made with `ctx.asset(bytes, mime)`. Raise on failure: the cache keeps the
  last good copy and stores only an error code. `RateLimited` from the HTTP client is handled
  the same way.
- `manifest_extra` may return `url` (the slide URL for a native plugin) and `muted`, which are
  lifted to the manifest item; everything else goes into the `plugin` block. If it raises, the
  item is left out of the manifest.
- `playable` returning false keeps the item off screens (stocks without a key, an image
  library with no images left).

`ctx` gives:

| Member | Meaning |
|---|---|
| `ctx.http` | The guarded HTTP client, bound to the plugin's `hosts` and `rate`. `get(url, headers=…, redact=[…], allow_hosts=[…])`; secrets in the query string go in `redact`. |
| `ctx.secret(name)`, `ctx.setting(name)` | A declared secret (or `None`) and an organization setting (or its default) |
| `ctx.org_tz`, `ctx.locale` | Organization time zone and `plugins.locale` |
| `ctx.asset(bytes, mime)` | An `Asset` with `id = sha256[:32]` |
| `ctx.cache(subkey, ttl, fn)` | A value shared by every item of the plugin (one quote per symbol). Returns the stored value while fresh, else `fn()`; if `fn()` raises, an older value is returned. |
| `ctx.deadline` | `time.monotonic()` deadline from `fetch_budget_s` |
| `ctx.file_entry(media)` | Manifest file fields of an image or video (for `manifest_extra`) |

### Rules for player code
- Frame plugins start from the runner page in the player README, unchanged except for the
  extra script and style lines. No inline script or style, no `on*=` attributes, no `eval`,
  no `innerHTML` with data, styles through CSSOM or classes, and every timer through
  `ctx.every` / `ctx.later`.
- Size everything in `vmin` (or container units) so 16:9 and 9:16 screens both work, and check
  both.
- Show `ctx.fallback(...)` when there is no data, and `ctx.staleBadge(...)` for stale data.
- Native plugin CSS classes start with a prefix unique to the plugin (`pl-<key>-`, or a short
  form such as `pl-imglib-`).
- Every file stays under 800 lines.

### Tests
Each plugin has `tests/test_plugin_<key>.py` (plugin.json loads, defaults, `normalize`, the
fetcher against recorded fixtures with `ctx.http` replaced, the data shape, failure → stale,
`manifest_extra` and `playable`) and `tests/js/plugin_<key>.js` for its pure functions, run
with Node. **Tests never use the network.** `tests/test_plugins_runner_html.py` checks every
runner page and the player page's file list; `tests/test_integration_plugins.py` plays all
plugins in one playlist.

## Changes from v2

| v2 | v3 |
|---|---|
| Clock, 5 themes, 12h/24h, date | Kept; adds a time zone and a label. |
| Weather: NWS, Open-Meteo, zip lookup, `locationLabel`, `language` | Open-Meteo or MET Norway; place lookup at save; a label override; `plugins.locale`. NWS and zippopotam dropped. |
| Radar: Leaflet, RainViewer, nowcast, alerts, play and zoom controls | Frames drawn on the server; RainViewer or IEM; US alerts. Nowcast and controls dropped. |
| RSS through rss2json | Fetched and parsed on the server; three modes; images; QR codes. |
| Stocks: Finnhub key on screens, direction, speed, theme, `loops` | The key stays on the server; ticker, grid or board; speed; theme. `loops` dropped (the player owns the duration). |
| YouTube: list, random, mute, start/end, cc, rate, quality, branding | Videos, playlist, live, shuffle, mute, start/end, captions, play to the end. Rate, quality, branding and the per-session mode dropped. |
| Image library (an empty folder in v2) | New: slideshow, Ken Burns, collage. |
| Online state and clock offset sent to plugins | The `net` message and `server_offset_ms`. |
| Per-tenant policy, signing, zip upload | One on/off switch per plugin; bundled plugins only. |
| `plugin_policy` live reload | The "manifest" event after a settings change. |
