# AISignX — Plugins

Plugins are special media items that show dynamic live content — weather, clocks, stock tickers, RSS feeds, YouTube videos, and more. They run inside the player alongside regular images and videos.

---

## Table of Contents

1. [What Is a Plugin?](#1-what-is-a-plugin)
2. [Adding a Plugin to Your Playlist](#2-adding-a-plugin-to-your-playlist)
3. [Built-In Plugins](#3-built-in-plugins)
4. [How Plugins Signal Completion](#4-how-plugins-signal-completion)
5. [Plugin Admin Page](#5-plugin-admin-page)
6. [Writing a Custom Plugin](#6-writing-a-custom-plugin)

---

## 1. What Is a Plugin?

A plugin is a media item whose content is dynamically generated rather than being a static file or URL. Plugins run in a sandboxed iframe inside the player and can display anything a web page can — live data, animations, embedded video, interactive graphics.

Plugins are installed on the server and configured per-media-item. Multiple media items can use the same plugin with different configuration (e.g. two Weather items for different cities).

---

## 2. Adding a Plugin to Your Playlist

1. Go to **Media ? Add Media**
2. Set Type to **Plugin**
3. Select the plugin from the dropdown
4. Fill in the plugin's configuration fields
5. Set a duration (used as a fallback if the plugin never signals completion)
6. Click **Save**
7. Add the new media item to a playlist as usual

The plugin appears in the playlist alongside images and videos and plays in rotation.

---

## 3. Built-In Plugins

### Clock

Displays a fullscreen clock in one of several user-selectable themes. Themes are drop-in folders that ship with the plugin — you can add your own without touching the plugin's main code.
The clock uses the player-provided server clock offset, so grouped displays show
the same wall-clock time even if a device's local OS clock is off.

| Field | Description |
|---|---|
| Theme | Dropdown — populated automatically from subfolders of `plugins/clock/themes/` |
| Format | `HH:MM:SS` (24h with seconds), `HH:MM` (24h), `hh:MM:SS AM/PM` (12h with seconds), `hh:MM AM/PM` (12h) |
| Show Date | Toggle date display below the time |
| Duration | Seconds to display before advancing |

**Built-in themes:**

| Theme | Look |
|---|---|
| Minimal Dark | Large white digits on a black background |
| Minimal Light | Large dark digits on a white background |
| Analog Classic | SVG analog wall clock with hour, minute and second hands |
| Flip Clock | Retro split-flap mechanical clock cards |
| Word Clock | Tells the time in plain English ("IT IS HALF PAST TEN") |

**Adding your own theme:**

Drop a folder into `plugins/clock/themes/<your-theme-name>/` containing:

- `theme.json` — `{ "label": "Your Theme Name", "description": "..." }`
- `render.js` — defines `window.renderClock = function(ctx) { ... }`
- `style.css` — optional
- Any image / font assets — accessed via `ctx.themeAsset('filename')`

The theme appears in the **Theme** dropdown automatically. Hot-reload with **Plugins ? Reload Registry** (no server restart required).

The `ctx` object passed to `renderClock` contains:

| Property | Description |
|---|---|
| `now` | A live `Date` object — current time |
| `hh24`, `hh12`, `mm`, `ss`, `ampm` | Pre-formatted strings |
| `timeStr`, `dateStr` | Pre-formatted full strings (respect Format and Show Date) |
| `showDate` | Boolean from the user's config |
| `cfg` | The full plugin config (theme, format, etc.) |

When writing time-sensitive plugins, prefer the player helpers exposed in the
plugin runner:

```javascript
const now = window.signageDate ? window.signageDate() : new Date();
const ms = window.signageNowMs ? window.signageNowMs() : Date.now();
```

These helpers apply the display player's calibrated server-time offset.
| `root` | The DOM element your render function should populate |
| `themeAsset(name)` | Returns a URL to a file inside your theme folder |

`renderClock` is called once per second. The first call should build the DOM (typically gated by `if (!root._init) { ... root._init = true; }`); subsequent calls update the textual values only.

See `plugins/clock/themes/minimal-dark/render.js` for the simplest possible reference theme (~10 lines of code).

---

### Weather

Shows current weather conditions for a location.

| Field | Description |
|---|---|
| Location | City name or `lat,lon` coordinates (e.g. `Chicago` or `41.85,-87.65`) |
| Units | `metric` (°C, km/h) or `imperial` (°F, mph) |
| API Key | OpenWeatherMap API key — free tier is sufficient |
| Duration | Seconds to display before advancing |

Get a free API key at https://openweathermap.org/api

---

### Weather Radar

Animated weather radar overlay for any location, using the free RainViewer tile service over OpenStreetMap base tiles. Renders dark mode automatically via a CSS filter (no API key required).

| Field | Description |
|---|---|
| Location | City name or `lat,lon` coordinates |
| Zoom | Map zoom level (4–12). Higher = closer in. Capped at 12 because higher zooms exceed the radar tile resolution |
| Frames | Number of historical radar frames to play (1–15). 10 ˜ last 1 hour |
| Frame ms | Milliseconds per frame in the loop animation (default 600) |
| Alerts | Toggle NWS alert WMS overlay (US only) |
| Duration | Seconds to display before advancing |

Tiles come from `tile.openstreetmap.org` and `tilecache.rainviewer.com` — both free and unauthenticated. The display device needs internet access.

Good for: displays in areas prone to severe weather; safety and operations screens.

---

### YouTube

Plays a YouTube video or playlist in the signage display.

| Field | Description |
|---|---|
| Video URL(s) | One URL per line. Use the full `youtube.com/watch?v=...` or `youtu.be/...` URL |
| Clip Range | Append `start=30 end=90` to a URL to play only that segment (e.g. `https://youtu.be/abc123 start=10 end=60`) |
| Loop | Loop after all videos have played |
| Mute | Mute audio — **required for autoplay in most browsers** |

The plugin plays all listed videos in sequence, then signals completion so the playlist advances.

**Note:** YouTube videos require the display device to have internet access. Videos with embedding disabled will not play.

---

### Stocks

Scrolling stock ticker with live prices.

| Field | Description |
|---|---|
| Tickers | Comma-separated symbols (e.g. `AAPL,MSFT,GOOG,AMZN`) |
| API Key | Alpha Vantage or Finnhub API key |

The ticker scrolls across the bottom of the screen. It advances to the next playlist item after one complete scroll animation.

Get a free API key at:
- https://www.alphavantage.co/support/#api-key
- https://finnhub.io/register

---

### RSS Feed

Displays scrolling RSS or Atom news headlines.

| Field | Description |
|---|---|
| Feed URL | Any public RSS/Atom feed URL |
| Items | Number of headlines to show per cycle |
| Duration | Seconds to display before advancing |

Good for: news tickers, corporate announcement feeds, event feeds.

---

## 4. How Plugins Signal Completion

When a plugin finishes its content, it posts a message to the parent player window:

```javascript
window.parent.postMessage({ type: 'signage:complete' }, '*');
```

The player receives this message and immediately advances to the next playlist item.

**Duration as a safety net:** every plugin item has a duration value. If the plugin never signals completion (e.g. due to a network error), the player automatically advances after the duration expires.

---

## 5. Plugin Admin Page

Go to **Plugins** in the sidebar (admin only) to see all installed plugins.

| Column | Description |
|---|---|
| Name | Plugin display name |
| Version | Plugin version from `plugin.json` |
| Description | What the plugin does |
| Folder | Directory name under `plugins/` |

### Reloading the Plugin Registry

If you drop a new plugin folder into `plugins/` while the server is running:

1. Go to **Plugins**
2. Click **Reload Registry**

The new plugin is available for use immediately — no server restart required.

---

## 6. Writing a Custom Plugin

Plugins are self-contained folders under `plugins/`. Each plugin needs two files: `plugin.json` and `main.js`.

### Folder Structure

```
plugins/
  my-plugin/
    plugin.json
    main.js
    (any other static assets — CSS, images, etc.)
```

### plugin.json

```json
{
  "name": "My Plugin",
  "description": "What it does",
  "version": "1.0.0",
  "entry": "main.js",
  "config_schema": [
    { "key": "my_text_field",  "label": "Some Text",       "type": "text",     "required": true  },
    { "key": "my_number",      "label": "A Number",         "type": "number",   "default": 30     },
    { "key": "my_select",      "label": "Choose One",       "type": "select",   "options": [
        { "value": "a", "label": "Option A" },
        { "value": "b", "label": "Option B" }
    ]},
    { "key": "my_toggle",      "label": "Enable Feature",   "type": "checkbox", "default": true   },
    { "key": "my_secret",      "label": "API Key",          "type": "password"                    },
    { "key": "duration",       "label": "Duration (s)",     "type": "number",   "default": 30     }
  ]
}
```

### Config Schema Field Types

| type | Renders as |
|---|---|
| `text` | Single-line text input |
| `textarea` | Multi-line text area |
| `number` | Number input |
| `select` | Dropdown — requires `options` array OR `options_from` folder |
| `checkbox` | Boolean toggle |
| `password` | Masked text input |

### Dynamic Select Options (`options_from`)

For dropdowns whose options come from a directory of files (theme folders, layout presets, profiles, etc.), use `options_from` instead of a hard-coded `options` array:

```json
{ "key": "theme", "label": "Theme", "type": "select", "options_from": "themes" }
```

The server scans `plugins/<your-plugin>/themes/` at registry-load time. For each subdirectory it reads `theme.json` for a friendly `label` (falling back to the folder name) and adds it as a dropdown option whose value is the folder name.

The clock plugin uses this to make the **Theme** dropdown auto-populate from `plugins/clock/themes/`. Drop a new theme folder, click **Plugins ? Reload Registry**, and it appears in the dropdown immediately.

### main.js

This script runs inside an iframe on the display device. It receives its configuration via a signed JWT in the `cfg` URL parameter.

```javascript
// Decode configuration
const params = new URLSearchParams(location.search);
const cfg = JSON.parse(atob(params.get('cfg').split('.')[1]));

// cfg now contains all the values from your config_schema
// e.g. cfg.my_text_field, cfg.duration, cfg.my_number

// --- Your plugin content goes here ---
document.body.innerHTML = `<h1>${cfg.my_text_field}</h1>`;

// Signal the player to advance when done
function signalComplete() {
  window.parent.postMessage({ type: 'signage:complete' }, '*');
}

// Advance after duration (safety net)
setTimeout(signalComplete, (cfg.duration || 30) * 1000);

// Or call signalComplete() earlier if your content finishes sooner
```

### Styling Tips

- The iframe fills the full player viewport — your content should fill `100vw` × `100vh`
- Use `body { margin: 0; padding: 0; overflow: hidden; }` to avoid scrollbars
- Font sizes should use `vw`/`vh` or `clamp()` units for screen-size independence
- Avoid `position: fixed` — prefer `position: absolute` inside a full-viewport container

### Making HTTP Requests

The plugin iframe can make any fetch/XHR calls as normal JavaScript. API keys should be stored in config fields and decoded from the JWT — do not hardcode them in `main.js`.

```javascript
const response = await fetch(`https://api.example.com/data?key=${cfg.api_key}`);
const data = await response.json();
```

### Installing and Testing

1. Copy your plugin folder to `plugins/your-plugin-name/`
2. In the admin, go to **Plugins ? Reload Registry**
3. Go to **Media ? Add Media ? Plugin** — your plugin should appear in the dropdown
4. Create a media item, fill in the config, add it to a playlist, and test on a display

---

## Security Notes

- Plugin config is passed as a signed JWT — the configuration values cannot be tampered with by the display
- Plugins run in a sandboxed iframe — they cannot access the parent page's DOM or session cookies
- Only admin users can install plugins or reload the plugin registry
- Review any third-party plugin code before installing it — plugins can make arbitrary HTTP requests from the display device