# AISignX — Clock Plugin

A plugin that displays a fullscreen clock in one of several user-selectable themes. Themes are drop-in folders so you can add new looks without ever touching `main.js`.

---

## Configuration

| Field | Description |
|---|---|
| Theme | Selects which theme to render (auto-populated from `themes/` subfolders) |
| Format | `HH:MM:SS` / `HH:MM` / `hh:MM:SS AM/PM` / `hh:MM AM/PM` |
| Show Date | Toggle date display below the time |
| Duration | Seconds to display before advancing |

---

## Built-in Themes

| Folder | Label | Look |
|---|---|---|
| `minimal-dark` | Minimal Dark | Large white digits on a black background |
| `minimal-light` | Minimal Light | Large dark digits on a white background |
| `analog-classic` | Analog Classic | SVG analog wall clock with hour/minute/second hands |
| `flip-clock` | Flip Clock | Retro split-flap mechanical clock cards |
| `word-clock` | Word Clock | "IT IS HALF PAST TEN" in plain English |

---

## Adding a Custom Theme

Drop a new folder into `plugins/clock/themes/<your-theme-name>/`:

```
plugins/clock/themes/
  my-theme/
	theme.json     ← required (label + description)
	render.js      ← required (defines window.renderClock)
	style.css      ← optional
	bg.png         ← optional, any other assets
```

### theme.json

```json
{
  "label": "My Theme",
  "description": "What it looks like",
  "author": "Your Name"
}
```

The `label` is shown in the **Theme** dropdown in the admin. The folder name becomes the value (so `my-theme` is what gets stored in the plugin config).

### render.js

```javascript
window.renderClock = function (ctx) {
  // First call: build the DOM
  if (!ctx.root._init) {
	document.body.style.background = '#000';
	ctx.root.innerHTML =
	  '<div id="t" style="font-size:30vmin;color:#fff;' +
	  'position:absolute;inset:0;display:flex;align-items:center;' +
	  'justify-content:center;font-family:monospace"></div>';
	ctx.root._init = true;
  }
  // Every second: update text
  document.getElementById('t').textContent = ctx.timeStr;
};
```

### The `ctx` object

| Property | Type | Description |
|---|---|---|
| `now` | `Date` | Current time, fresh on every call |
| `hh24` | string | 2-digit 24-hour hour (`'07'`, `'14'`) |
| `hh12` | string | 2-digit 12-hour hour (`'07'`, `'02'`) |
| `mm` | string | 2-digit minute |
| `ss` | string | 2-digit second |
| `ampm` | string | `'AM'` / `'PM'` |
| `timeStr` | string | Pre-formatted full time (respects Format) |
| `dateStr` | string | Pre-formatted date string (e.g. `'Fri Apr 25 2026'`) |
| `showDate` | boolean | The user's Show Date setting |
| `cfg` | object | Full plugin config (theme, format, show_date, duration) |
| `root` | HTMLElement | The container DIV your render function should populate |
| `themeAsset(name)` | function | Returns a URL to a file inside your theme folder. E.g. `<img src="${ctx.themeAsset('logo.png')}">` |

### Render lifecycle

1. The clock harness in `main.js` loads your theme's `render.js` once at plugin start
2. It then calls `window.renderClock(ctx)` **every second**
3. Your function should be cheap — typical pattern: build DOM once on first call (gated by `ctx.root._init`), then update text content on every subsequent call
4. Don't add `setInterval` / `setTimeout` of your own — the harness drives the tick

### Styling tips

- The plugin iframe fills the whole player viewport — your theme should fill `100vw × 100vh`
- Use `vmin` / `vw` / `vh` units for font sizes and margins so the layout is screen-size independent
- Set `body { margin: 0; overflow: hidden; }` (the harness does this for you on document load)
- Heavy SVG / canvas work is fine — modern browsers handle it at 1Hz updates trivially

### Testing

1. Drop your folder into `plugins/clock/themes/<your-theme>/`
2. In the admin, go to **Plugins → Reload Registry**
3. Edit (or create) a clock media item — your theme should appear in the **Theme** dropdown
4. Add the media item to a playlist and view it on a display

---

## How `options_from` populates the theme list

`plugin.json` declares the Theme field like this:

```json
{ "key": "theme", "label": "Theme", "type": "select", "options_from": "themes" }
```

When the plugin registry loads, the server scans `plugins/clock/themes/` and turns each subdirectory into a `{value, label}` option. This is a generic plugin-system feature — see [docs/PLUGINS.md](../../docs/PLUGINS.md#dynamic-select-options-options_from) for the full reference.
