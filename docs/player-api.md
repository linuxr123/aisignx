# Player API (v1)

This is the contract between the AiSignX server and its players: the browser player at
`/player/`, the Windows (Electron) app, the Android app and the optional privileged Windows agent.
A client that follows this document can pair, play content, stay up to date and recover from
errors without any other knowledge of the server. It has been exercised with the browser player,
the Windows and Android apps and automated tests; apart from the owner's own test devices (a Galaxy
Tab and a Shield) none of it has run on real screens yet.

Summary of what exists (all paths under `/api/player/v1`, except the last two rows):

| Path | Credential | Purpose |
|---|---|---|
| `POST /pair`, `POST /pair/status` | none | Pairing |
| `GET /manifest` | display token | What to play |
| `GET /events` | display token | Live updates (SSE) |
| `POST /heartbeat` | display token | Online status, diagnostics |
| `GET /media/<id>/<name>` | display token | Image, video and web screenshot files |
| `GET /plugins/<id>/data`, `GET /plugins/<id>/assets/<asset>` | display token | Plugin data |
| `POST /plays` | display token | Proof of play |
| `POST /screenshot` | display token | Answer to a screenshot request |
| `GET /commands`, `POST /commands/<id>/ack`, `POST /commands/<id>/logs` | display token | Device commands |
| `GET /command-key`, `GET /commands/<id>/package` | display token | Signed-command key, installer for an update |
| `POST /agent/enroll`, `GET /agent/commands`, `GET /agent/command-key`, `POST /agent/commands/<id>/ack`, `GET /agent/commands/<id>/package`, `POST /agent/diagnostics`, `GET /agent/status`, `POST /agent/unenroll` | agent token (enroll: one-time code) | The privileged Windows agent |
| `GET /provision/apk/<token>`, `GET /get/<code>` (outside `/api/player/v1`) | time-limited link | Android installation |

All paths below are relative to `<server>/api/player/v1`, for example
`http://192.168.1.10:5000/api/player/v1/manifest`. Every request and response body is JSON,
except the media files and the event stream.

## Rules for every client

- **Ignore what you don't know.** New fields in a response and new event names may appear in
  any release. Ignore unknown fields and unknown events; never fail because of them. Fields are
  only added, never renamed or removed, within `v1`.
- **Never put a token in a URL.** Not in a query string, not in a path, not in a log line. The
  server never reads tokens from the URL.
- **Keep playing.** Network errors, timeouts, 5xx, 429 and 503 never stop playback. Keep showing
  the last manifest and the media you already have, and retry with backoff.
- **Re-pair only on a JSON 401.** Drop the credential only when the response is HTTP 401 *and*
  the body is JSON with `"error": "unauthorized"`. A 401 page from a proxy or captive portal must
  never unpair the screen.
- **Use HTTPS properly.** Never turn off TLS certificate checks. To trust a self-signed server,
  trust its CA or pin its certificate fingerprint.
- **Keep the token secret.** Store it in the platform's protected storage (for example Electron
  `safeStorage` or the Android Keystore). Never show it on screen.

## Authentication

| Client | How the display token is sent |
|---|---|
| Native (Electron, Android) | `Authorization: Bearer <token>` header on every request, including media and events |
| Browser player | HttpOnly cookie `aisignx_display` (Path `/api/player`, SameSite=Strict, Secure on HTTPS). Page scripts never see the token. |

The server checks the `Authorization` header first, then the cookie. Native clients need an SSE
library that can send request headers (the browser's `EventSource` cannot).

The token is a random 43-character string. The server stores only its SHA-256 hash, so a lost
token cannot be recovered: the screen pairs again.

## Errors

Every error response has the same shape:

```json
{"error": "unauthorized", "message": "This display is not paired."}
```

Decide on `error` (and the HTTP status), never on `message`, which is for people.

| HTTP | `error` | Meaning | What the client does |
|---|---|---|---|
| 400 | `bad_request` | Body is not a JSON object, or a field is invalid | Client bug. Log it; don't repeat the same request in a loop. |
| 400 | `enroll_refused` | `POST /agent/enroll` with a bad public key | Client bug. |
| 403 | `enroll_refused` | `POST /agent/enroll` with a wrong, used or expired code | Ask the admin for a new code. |
| 409 | `agent_enrolled` | The display has a privileged agent; `GET /command-key` and `GET /commands/<id>/package` are for the agent now | Use the `/agent/` routes (the agent) or ignore (the player). |
| 400/404/409/413 | `command_*`, `logs_*`, `package_*` | A command, log upload or installer request that is not valid (see [Device commands](#device-commands)) | Do not retry the same request. |
| 400 | `too_many_events` | `POST /plays` with more than 200 events | Send smaller batches. |
| 400 | `bad_image` | `POST /screenshot` body is not a readable JPEG or PNG, or is over 40 megapixels | Drop the screenshot. |
| 401 | `unauthorized` | Token missing, wrong, unpaired or the display was deleted | Drop the token (and cached manifest), go back to pairing. |
| 403 | `forbidden` | Not allowed | Client bug. |
| 404 | `not_found` | Unknown path or media | Skip that item or request. |
| 404 | `expired` | Pairing secret unknown, used or expired | Start pairing again with `POST /pair`. |
| 405 | `method_not_allowed` | Wrong HTTP method | Client bug. |
| 409 | `stale_request` | `POST /screenshot` with a request id that is not (or no longer) open | Drop the screenshot. |
| 413 | `too_large` | Request body over 8 KB (64 KB for `POST /plays`, 4 MB for `POST /screenshot`) | Client bug. |
| 415 | `unsupported_type` | `POST /screenshot` body is not `image/jpeg` or `image/png` | Client bug. |
| 429 | `too_many_pending` | Too many screens are waiting to pair, or too many `POST /agent/enroll` attempts from one address | Wait `Retry-After` seconds (60), then try again. |
| 503 | `busy` | Server is at its connection limit, or a pairing code collision | Wait `Retry-After` seconds, keep playing. |
| 500 | `server_error` | Unexpected server fault | Retry later, keep playing. |
| 503 | `setup_required` | The server has no admin account yet | Show "waiting for setup", retry after `Retry-After` (30). |

Anything else (network error, timeout, other 5xx, a non-JSON response) means "retry later".
Use one retry loop with a backoff from 2 s to 30 s and ±20% random jitter, and keep playing
meanwhile. Use a request timeout of about 10 seconds.

## Request bodies

- Send a JSON object with `Content-Type: application/json`. The body limit is 8 KB (the
  exceptions are `POST /plays`, up to 64 KB, and the raw image body of `POST /screenshot`, up
  to 4 MB).
- `client_id` must match `^[A-Za-z0-9_-]{1,64}$`. Make one random hex ID on first start and keep
  it forever (for example 16 random bytes as 32 hex characters).
- `player_version` is cut to 32 characters and `device` to 120. Use a prefix that names the
  client, for example `web-3.0.0`, `electron-3.0.0` or `android-3.0.0`.
- Screen sizes are clamped to 0..16384.
- The server ignores unknown keys.

## Pairing

1. The screen calls `POST /pair` and shows `display_code` in large type, with the line
   "Open `<server>`/displays/pair and enter this code" and a countdown.
2. It polls `POST /pair/status` every `poll_after_s` seconds.
3. An editor types the code in the admin (**Displays > Pair a display**).
4. The next poll returns the token. The screen fetches the manifest, opens the event stream and
   sends one heartbeat right away.

Codes are single-use and expire after 10 minutes. When a code expires (`404 expired`), request a
new one automatically. If the response with the token is lost, the screen simply pairs again.

### `POST /pair`

No authentication. All fields are optional.

```json
{"client_id": "9f86d081884c7d65", "device": "Android 14 / Philips 55BDL", "player_version": "android-3.0.0"}
```

`201 Created`:

```json
{"code": "K7PQ4X", "display_code": "K7P-Q4X", "pairing_secret": "<43 characters>",
 "expires_in": 600, "poll_after_s": 3}
```

`pairing_secret` is the proof of ownership. Keep it in memory (or short-lived storage), send it
only in a request body, and never show it. Errors: `429 too_many_pending`, `503 busy`.

### `POST /pair/status`

No authentication; the secret is the proof.

```json
{"pairing_secret": "<43 characters>", "credential": "bearer"}
```

`credential` is `"bearer"` (the default, for native clients) or `"cookie"` (browser player).

| Result | Response |
|---|---|
| Still waiting | `200 {"status": "pending", "expires_in": 512}` |
| Paired, bearer | `200 {"status": "paired", "token": "<43 characters>", "display": {"id": 3, "name": "Lobby"}, "heartbeat_s": 30}` |
| Paired, cookie | Same body without `token`, plus a `Set-Cookie: aisignx_display=…` header |
| Unknown, used or expired | `404 {"error": "expired", …}`: start again at `POST /pair` |

The token is returned exactly once.

## Manifest

### `GET /manifest`

Authenticated. Returns what the screen should play.

- The response has `ETag: "<version>"` and `Cache-Control: no-cache`.
- Send `If-None-Match: "<version>"` with the version you have. If nothing changed, the server
  answers `304 Not Modified` with no body.
- `version` is a 16-character hash of the content. Treat it as an opaque string and compare it
  for equality only.
- Cache the last good manifest on disk and play from it at start-up, before the server answers.

```json
{"v": 1, "version": "9f2c4e1a7b3d5f60", "server_time_ms": 1731312000000, "heartbeat_s": 30,
 "display": {"id": 3, "name": "Lobby"},
 "playlist": {"id": 2, "name": "Morning"},
 "items": [
  {"id": 41, "media_id": 7, "type": "image", "url": "/api/player/v1/media/7/3f9c….jpg",
   "mime": "image/jpeg", "sha256": "…", "size": 523411, "width": 1920, "height": 1080,
   "duration_ms": 10000, "media_duration_ms": null, "transition": "fade", "fit": "fit", "muted": true},
  {"id": 42, "media_id": 9, "type": "video", "url": "/api/player/v1/media/9/….mp4",
   "mime": "video/mp4", "sha256": "…", "size": 8812331, "width": 1920, "height": 1080,
   "duration_ms": 32000, "media_duration_ms": 31500, "transition": "random", "fit": "fill", "muted": false},
  {"id": 43, "media_id": 12, "type": "web", "url": "https://example.com/menu",
   "mime": null, "sha256": null, "size": null, "width": null, "height": null,
   "duration_ms": 20000, "media_duration_ms": null, "transition": "cut", "fit": "fit", "muted": true}
 ],
 "source": {"type": "schedule", "id": 7, "name": "Mornings"},
 "emergency": null,
 "settings": {"orientation": 0, "volume": 100, "show_offline_banner": true,
              "allow_input": false, "group_controls": true, "kiosk_lock": false, "resume_after_s": 300,
              "unlock": null,
              "agent": {"watchdog": true, "restart": true, "logs": true,
                        "reboot": false, "startup": false, "update": false}}}
```

| Field | Meaning |
|---|---|
| `v` | Schema version of the manifest (1). |
| `server_time_ms` | Server clock (Unix ms) when the manifest was built. Use it to estimate clock drift. |
| `heartbeat_s` | How often to send a heartbeat. |
| `playlist` | `null` when nothing plays (`source.type` is `none`). Then `items` is `[]`: show a "no content" screen with the display name. |
| `items` | Play in this order, then loop. |
| `sync` | Only in manifests of screens in a group with synchronized playback: `{group_id, anchor_ms, cycle_ms, controls}` (`controls`: `false` when the group's option "Controls move the whole group" is off; then `POST /group/transport` answers 409), plus `transport` once a screen of the group has used a control (see [`POST /group/transport`](#post-grouptransport)). The slide at server time `T` comes from walking the items' `duration_ms` (each at least 1000) through `(T - anchor_ms) mod cycle_ms`; slides run for their duration, never ended early. The server clock is `server_time_ms` corrected by half the round trip. See [synced-playback.md](synced-playback.md). Clients that do not know it play normally. |
| `id` | Playlist item ID (report it as `current_item_id`). |
| `type` | `image`, `video`, `web` or `plugin` (see [Plugin items](#plugin-items)). Skip types you don't support. |
| `url` | Image and video: a path on this server; resolve it against the server base URL and fetch it with your token. Web: an absolute `http(s)://` URL to show in a web view, without your token. A web page that cannot be embedded (the server checks this) is sent as `type: "image"` with a `url` to its screenshot, a PNG fetched like any image; clients need no special case. |
| `sha256`, `size` | Checksum and byte size of the file, when known. Useful for an offline cache. |
| `duration_ms` | How long the item stays on screen. This is a hard limit. |
| `media_duration_ms` | Length of the video file, or `null`. |
| `transition` | How this item enters (see below). |
| `fit` | How the item fills the screen (see below). When the admin sets a fit for the whole display, every item carries that value. |
| `video` | Videos only, when known: `{"codec": "avc1.640028", "width", "height", "fps", "bitrate"}`. Lets the player ask its decoder whether this screen plays the file smoothly; the result is reported in the diagnostics and shown as a warning on the display page. Ignore it if you do not check. |
| `muted` | Play the video's sound only when this is `false`. Always `true` for images and web pages. For plugins with sound (YouTube) it follows the playlist item's Mute box. |
| `volume` | `0`-`100` (default `100` when missing): the playlist item's share of the display volume. The player multiplies it with `settings.volume`. Videos and YouTube only. |

**Video timing.** Stop the video at `duration_ms` at the latest. If the video ends earlier and
`duration_ms > media_duration_ms + 1000`, start it again from the beginning; otherwise move to the
next item. A video that stops making progress for 10 seconds counts as failed.

**Failures.** Skip an item that fails to load and keep the previous item on screen. If every item
fails, show "no content" and fetch the manifest again after 30 seconds.

**Fit modes**

| `fit` | Meaning | CSS `object-fit` | Android `ImageView.ScaleType` |
|---|---|---|---|
| `fit` | Whole item visible, black bars if needed | `contain` | `FIT_CENTER` |
| `fill` | Fill the screen, crop the edges | `cover` | `CENTER_CROP` |
| `stretch` | Fill the screen, ignore the aspect ratio | `fill` | `FIT_XY` |

**Transitions:** `cut` (no animation), `fade`, `crossfade`, `wipe`, `slide-left`, `slide-right`,
`slide-up`, `slide-down`, `zoom` and `random`. For `random`, pick one of the eight that are not
`cut`, per slide. A client may show any transition it doesn't support as `fade` or `cut`.

### Display settings

`settings` holds the options an admin sets per display. If the key is missing (an older
server), keep today's behaviour: no rotation, full volume, input allowed, no PIN, no lock,
auto-resume after 300 seconds. Clients ignore keys they don't know.

| Field | Meaning |
|---|---|
| `orientation` | `0`, `90`, `180` or `270`: rotate the output clockwise by this many degrees. The browser player rotates with CSS unless the shell reports the `rotate` capability (it then rotates the device itself). |
| `volume` | `0`-`100`. Applied to the player's own videos (not the OS volume, not inside web pages). `muted` items stay muted. |
| `show_offline_banner` | Show a small "Offline" pill while the screen cannot reach the server. |
| `group_controls` | Only matters on a screen of a synchronized group with `allow_input` on. `true` (default): a control moves the timeline of the whole group. `false`: it moves this screen only, which rejoins the group after `resume_after_s`. The screen follows the group's state either way. Missing: `true`. |
| `allow_input` | `false`: touches, clicks, keys and the wheel are ignored. `true`: they become player controls (see [Player controls](#player-controls)); a transparent overlay covers every item, so web items cannot be clicked or scrolled either way. On a screen in a synchronized group, a control moves the timeline of the whole group (see [`POST /group/transport`](#post-grouptransport)); a pause ends by itself after `resume_after_s`. |
| `agent` | `{"watchdog", "restart", "logs", "reboot", "startup", "update"}`, all booleans: the admin's switches for the device agent (Edit > Device agent). See [Device commands](#device-commands). A client must not do what its switch forbids. |
| `kiosk_lock` | `true`: the native apps cannot be left without the unlock PIN (window close and minimize blocked; Android lock task). The server only allows it while a PIN is set. A shell that sees `true` with no valid `unlock` hash stays locked but lets its admin exit through without a PIN, so a bad hash never bricks a screen. Missing (older server): `false`. The browser player never reads it. |
| `resume_after_s` | `60`-`3600`: a paused player resumes on its own after this many seconds. A missing or bad value means `300`; clamp anything else to the range. |
| `unlock` | `null`, or `{"alg": "pbkdf2-sha256", "iter": 200000, "salt": "<32 hex>", "hash": "<64 hex>"}`: the PIN that guards leaving the native apps. |

`unlock` is for the native apps only. They check a PIN with PBKDF2-HMAC-SHA256
(`iter` iterations, the hex-decoded `salt`, 32-byte output) and compare it to `hash` in
constant time, with a growing wait after wrong PINs. The browser player never reads it and
removes it before it stores the manifest in `localStorage`. A 4-8 digit PIN can be
brute-forced offline by anyone who gets the manifest or the shell's config: it is a kiosk lock
against passers-by, not an account password.

Location and notes the admin enters are never sent to the screen.

### Source and emergency

The server decides what plays; the client never evaluates schedules. A schedule starting or
ending, an emergency being sent or cleared, and a change of the default playlist all change
`version`, so the normal `manifest` event, heartbeat check and `If-None-Match` flow pick them up.
Clients need no clock or schedule logic.

**`source`** says why this playlist plays. It is for display and logging only.

```json
"source": {"type": "schedule", "id": 7, "name": "Mornings"}
```

| `type` | `id`, `name` | Meaning |
|---|---|---|
| `emergency` | emergency ID and title | An emergency message with its own playlist is live. |
| `schedule` | schedule ID and name | A schedule is active. |
| `default` | `null`, `null` | No schedule matches; the display's default playlist plays. |
| `none` | `null`, `null` | Nothing to play; `playlist` is `null` and `items` is `[]`. |

**`emergency`** is `null` or an object:

```json
"emergency": {"id": 3, "title": "Fire drill", "message": "Leave by the nearest exit.",
  "level": "critical", "background": "#b71c1c", "foreground": "#ffffff",
  "layout": "fullscreen", "since": "2026-11-11T10:00:00Z"}
```

| Field | Meaning |
|---|---|
| `title`, `message` | Plain text. Show it as text, never as HTML. `message` may be empty and may contain line breaks. |
| `level` | `info`, `warning` or `critical`. |
| `background`, `foreground` | Colours to use: info `#1565c0`, warning `#e65100`, critical `#b71c1c`, all with white text. |
| `layout` | `fullscreen`: cover the whole screen with the text and mute any video. `banner`: keep playing `items` (the emergency playlist) and show the text in a band at the bottom. `none`: play `items` and show no text. |
| `since` | When the message was sent (UTC, ISO 8601). |

`layout` is `fullscreen` whenever the emergency has no playable playlist, so the text is always
shown in that case. When several emergencies are live, the server picks one per screen: the
highest level, then the newest.

Rules for the overlay:

- `emergency` in a manifest you fetched successfully is authoritative: an object means show it
  (replacing any other overlay), `null` means hide it.
- If the key is missing (an older server), leave the overlay as it is.
- Never hide the overlay because of a network error, a failed fetch, a `304`, an SSE error,
  `bye` or a reconnect. A screen that is offline keeps showing the last emergency until it
  reaches the server again. Only going back to pairing clears it.

## Media

### `GET /media/<media_id>/<name>`

Authenticated. Returns the image or video file for an item `url`, or the PNG screenshot of a web page item
that is played as an image.

- Supports `Range` requests (`206 Partial Content`) for video seeking and resumed downloads.
- `Cache-Control: private, max-age=31536000, immutable`. The URL changes when the file changes,
  so a cached file never needs to be checked again.
- `404 not_found` when the media was deleted or the name doesn't match.

## Plugin items

Items with `type: "plugin"` (clock, weather, radar, RSS, stocks, YouTube, image library; see
[plugins.md](plugins.md)) carry a `plugin` block. The other item fields keep their meaning;
`mime`, `sha256`, `size`, `width` and `height` are `null`. A client that cannot run plugins
skips these items, as it skips any unknown type. The browser player at `/player/` runs them,
and so do the Windows and Android apps, which load that player.

```json
{"id": 44, "media_id": 21, "type": "plugin",
 "url": "/static/player/plugins/weather/index.html#c=3f9a1c0b77de",
 "mime": null, "sha256": null, "size": null, "width": null, "height": null,
 "duration_ms": 15000, "media_duration_ms": null, "transition": "fade", "fit": "fit", "muted": true,
 "plugin": {"key": "weather", "version": "3.0.0", "render": "frame",
            "config": {"location": {"q": "Oslo", "lat": 59.9139, "lon": 10.7522, "label": "Oslo"},
                       "units": "metric", "layout": "full", "days": 5, "show_hourly": true,
                       "theme": "auto", "refresh": 600},
            "data": "/api/player/v1/plugins/21/data", "refresh_s": 600,
            "ends_itself": false, "locale": "",
            "attribution": "Weather data: Open-Meteo.com (CC BY 4.0)"}}
```

| Field | Meaning |
|---|---|
| `url` | Frame plugins: the plugin's runner page under `/static/player/plugins/<key>/`, with no query string. YouTube: the `youtube-nocookie.com` embed URL. Image library: `about:blank#aisignx-plugin=image_library&…`, never loaded. The `#c=` fragment changes whenever the plugin's config changes, so compare `url` to know when to rebuild the slide. |
| `plugin.key`, `version` | Which plugin, and its version. |
| `plugin.render` | `frame`: load `url` in an iframe with `sandbox="allow-scripts"` and talk to it as described in `server/aisignx/static/player/plugins/README.md`. `native`: drawn by the plugin's own script on the player page. |
| `plugin.config` | The item's public options. Private fields (an RSS feed address) are never included. A `timezone` left empty is filled in with the organization time zone. |
| `plugin.data` | Path of the data endpoint below, or `null` when the plugin has no server data. |
| `plugin.refresh_s` | How often to fetch the data again while the item is on screen (`null` without data). |
| `plugin.ends_itself` | `true` only for YouTube: the slide may end before `duration_ms` (when the video ends). Otherwise `duration_ms` always applies. |
| `plugin.locale` | BCP 47 tag for dates and numbers; empty means the screen's own. |
| `plugin.attribution` | Data source credit to show, or `null`. |
| `plugin.embed` | YouTube only: `{video_ids, list, channel, shuffle, captions, play_to_end, live}`. |
| `plugin.media` | Image library only: the images to show, in order. Each entry has the same `url`, `mime`, `sha256`, `size`, `width` and `height` fields as an image item, plus `media_id` (and `name` when captions are on). **An offline cache should download these like image items.** |

Data requests and data refreshes never change the manifest. Turning a plugin off in the admin
removes its items from the manifest.

### `GET /plugins/<media_id>/data`

Authenticated, like `/media`. The cached data of a plugin item. The server fetches from the
provider in the background and never makes a screen wait more than about 2 seconds.

```json
{"v": 1, "fetched_at_ms": 1731312000000, "stale": false, "error": null, "refresh_s": 600,
 "data": {"…": "plugin-specific"},
 "assets": [{"id": "9b0f…32 hex…", "mime": "image/webp", "size": 51234}]}
```

- `stale` is `true` when the data is older than its refresh time plus a grace period because
  the provider is failing; `error` then holds a short code such as `timeout`, `http_503` or
  `blocked_address` (never provider text). Show the data with an "Updated" time.
- `ETag` on every answer; send `If-None-Match` to get `304 Not Modified`.
  `Cache-Control: no-store`: keep your own last good copy for offline use.
- `503 warming` (no data yet, a fetch is running) or `503 upstream_unavailable` (the
  provider is failing and nothing is cached), each with `Retry-After` in seconds.
- `404 not_found` when the media is not a plugin item, the plugin is turned off, its config is
  no longer valid, or the plugin has no data.

### `GET /plugins/<media_id>/assets/<asset_id>`

Authenticated. One picture listed in that item's current or previous data (`asset_id` is 32
lowercase hex characters). `image/png`, `image/jpeg` or `image/webp`, with
`Cache-Control: private, max-age=86400, immutable`: an asset ID never changes content.
`404 not_found` for anything else.

## Device commands

For the native apps only (never the page): the Windows and Android apps fetch their own commands with
the display token. Design: [device-agent-design.md](device-agent-design.md); the signed ones are specified in
[signed-commands.md](signed-commands.md). An admin creates a command on the display page (a closed list, no
free-form commands); each only works when its switch is on for that display and the app reports it can do it.

| Action | Kind | Meaning | Waits for the device |
|---|---|---|---|
| `restart_player` | plain | Restart the player | 2 minutes |
| `collect_logs` | plain | Upload the app's redacted log | 5 minutes |
| `reboot_device` | signed | Reboot the device (not more than once per 10 minutes) | 2 minutes |
| `set_startup` | signed | Autostart on or off, app or shell mode (Windows) | 5 minutes |
| `update_app` | signed | Install the newest installer of the app's kind from Downloads | 15 minutes |
| `set_autologin` | signed, agent only | Set or undo automatic Windows sign-in | 5 minutes |

A display gets at most 10 commands per hour and one open command per action. A command is handed out **once**
and a display runs each at most once; finished commands are deleted after 30 days.

- The heartbeat answer carries `commands`: how many commands wait for this display.
- `GET /commands` returns `{"commands": [...], "server_time_ms": <epoch ms>}` and marks up to 5 as delivered.
  Each command is `{"id": "<16 hex>", "action": "...", "params": {}, "expires_in_s": 120}`; a signed action also
  has `"signed": {"payload": "<string>", "sig": "<base64>"}`, and the device must act on the signed payload only
  (verify the signature, `display_id`, `exp_ms`, `seq` and the local switch; see
  [signed-commands.md](signed-commands.md)). `set_autologin` is never handed to a display token. Once a privileged
  agent is enrolled for the display, **no** signed action is handed to the display token any more, and the
  heartbeat count leaves them out.
- `GET /command-key` (display token) returns `{"public_key": "<base64, 32 raw bytes>", "fingerprint": "ab12 cd34 ef56 7890"}`.
  The device pins the first key it sees together with the server address (trust on first use; a device set up by
  the Android QR code is given the key in the code and pins that). A different key later means the device keeps
  the old pin, refuses signed commands and reports `command_key_changed`. With an agent enrolled this answers
  `409 agent_enrolled`.
- `GET /commands/<id>/package` (display token): the installer of this display's own open `update_app` command,
  as `application/octet-stream`. Only while that command is delivered and not expired; the device checks the
  size and SHA-256 from the **signed** payload before installing. Errors: `404 package_unknown_command`,
  `404 package_the_installer_is_gone` (the file left the Downloads folder), `409 package_not_open`,
  `409 agent_enrolled`.
- `POST /commands/<id>/ack` with `{"state": "done" | "failed", "result": "short text"}` (result up to 200
  characters). Acknowledge `reboot_device` and `update_app` with `done` **before** the reboot or the install
  starts. 404 `command_unknown_command` for an id that is not this display's (or not one it may see), 409
  `command_expired`, `command_already_acknowledged` (answered differently) or `command_not_open`, 400 for a bad
  state. The same answer twice is fine.
- `POST /commands/<id>/logs` (raw body, gzip, at most 1 MB): the log for an open `collect_logs` command that
  was delivered to this display. 404 unknown id, 409 not open (answered, expired or not delivered), 413 too
  large, 400 not gzip or empty. The server stores it as is (never unpacks it), keeps the newest 5 per display
  for 7 days, and an admin downloads it from the display page. The app must **redact** it first: never
  the configuration, unlock PIN, certificate pin, cookies or the media cache.
- **Shell hook additions** (page to native app): `alive(json)`, called about every 10 s while the page runs
  (`{"t": ms, "state": "PLAYING", "item": id|null, "failures": n, "build": "..."}`; Android receives it on the
  shell port as `{"t":"alive","a":{...}}`), so the app's watchdog can restart a player whose page stopped
  sending it. And `stats().agent`, the app's own report that the page forwards in its diagnostics: `{"version",
  "tier": "plain"|"device_owner"|"admin", "can_restart", "can_collect_logs", "can_reboot", "can_update",
  "watchdog", "restarts_1h", "gave_up", "last_restart_ms"}`. The cap `agent` in `info().caps` marks an app
  that has an agent. Both are ignored by an older page or app.
- `settings.agent` in the manifest: `{"watchdog", "restart", "logs", "reboot", "startup", "update"}`, the admin's switches.
- The app reports what it can do in its heartbeat `diag.agent` (version, tier `plain` / `device_owner` /
  `admin`, `can_restart`, `can_collect_logs`, `can_reboot`, `can_set_startup`, `can_update`, `startup`,
  `watchdog`, `restarts_1h`, `gave_up`). The admin page hides what the app cannot do. Once an agent is enrolled,
  the privileged abilities (`can_reboot`, `can_update`, `can_set_startup`, `can_autologin`) are taken from the
  agent's own diagnostics only; what the player claims for them is ignored.

## Privileged Windows agent

The optional SYSTEM service on a Windows screen ([windows-agent.md](windows-agent.md)) uses a separate
credential and its own routes. The agent token (`agt_` plus 43 URL-safe characters) is sent only as
`Authorization: Bearer agt_...`; it works only under `/api/player/v1/agent/`, and a display token never works
there. A missing or unknown agent token is `401 unauthorized` with `{"error": "unauthorized", "message": "Unknown agent."}`.

| Route | Purpose |
|---|---|
| `POST /agent/enroll` | No credential. `{"code": "<one-time code>", "public_key": "<b64 X25519>", "host": "PC1", "version": "3.1.0"}` returns `{"agent_token", "display_id", "command_key", "fingerprint", "server_time_ms"}`. The code is made by an admin on the display page, is valid 15 minutes, works once, dies after 5 wrong tries and is rate limited to 10 requests a minute per address (`429 too_many_pending`). `403 enroll_refused` for a wrong, used or expired code, `400 enroll_refused` for a bad key. Enrolling again replaces the old token and cancels waiting signed commands. |
| `GET /agent/commands` | Only the signed actions, same signed format as above, plus `server_time_ms`. An automatic-sign-in envelope is wiped from the server's row right after it is handed out. |
| `GET /agent/command-key` | The public key and fingerprint (the agent compares it with its pin and never replaces it). |
| `POST /agent/commands/<id>/ack` | `{"state": "done" or "failed", "result": "..."}`, for signed actions only. |
| `GET /agent/commands/<id>/package` | The installer of the agent's own open `update_app` command. |
| `POST /agent/diagnostics` | `{"diag": {...}}` with a whitelist of fields: versions, `can_*`, `key_id`, `kiosk_user`, `startup`, `autologin`, `last_update`, `command_key_fingerprint`. Anything else is dropped. |
| `GET /agent/status` | `{"player_version", "player_last_seen_s"}`, for the health check after an update. |
| `POST /agent/unenroll` | The uninstall script's courtesy call; the server removes the agent. |

An admin can also remove the agent on the display page, and unpairing the display revokes it. The agent's
protocol for automatic sign-in (the encrypted envelope) is in [signed-commands.md](signed-commands.md).

## Provisioning an Android device

These routes are not under `/api/player/v1`. They are how an Android device gets the app and its first pairing
without a person typing a code ([android-kiosk.md](android-kiosk.md)).

- `/provision/android` (an admin page; the form is under **Downloads**, *Set up an Android tablet as a kiosk
  (QR code)*): submitting it makes the Android Enterprise provisioning QR code. It pre-approves a new display and puts a one-time pairing secret (valid 24 hours) in the code, together with the
  server address, the app's download link, the SHA-256 of the app, optional Wi-Fi and the server's command key.
  The device claims its display with `POST /pair/status` and that secret, like any screen.
- `GET /provision/apk/<token>`: the app (an APK of package `com.aisignx.player` or `.debug`, the newest
  Android app on Downloads). No sign-in, because the device has no account yet; the token is signed and valid 3
  days, and the device checks the file against the SHA-256 in the QR code.
- `GET /get/<code>`: a small public page with a download button and the server address, for phones that are
  already set up and Android TV boxes. The 8-character code is made by an admin, valid 7 days, and a new one
  replaces the old. It gives the app and the address only; it cannot pair anything.

## Heartbeat

### `POST /heartbeat`

Authenticated. Send one every `heartbeat_s` seconds (±10% random jitter), one right after pairing,
and one when the screen wakes up.

```json
{"client_id": "9f86d081884c7d65", "player_version": "android-3.0.0", "current_item_id": 41,
 "manifest_version": "9f2c4e1a7b3d5f60", "screen_w": 1920, "screen_h": 1080}
```

`200 OK`:

```json
{"server_time_ms": 1731312000000, "manifest_version": "9f2c4e1a7b3d5f60", "heartbeat_s": 30}
```

**Diagnostics.** Optionally add `"diag"`: on the first beat, every 10th beat, and right after a
`diagnostics` command. The server keeps only these keys (others are dropped, strings cut,
integers clamped to 0..2^53) and ignores the whole object when the result is over 4096
characters; the heartbeat itself still succeeds.

```json
"diag": {"ua": "…", "build": "3.0.0-1a2b3c4d", "page_uptime_s": 3600,
  "screen": {"w": 1920, "h": 1080, "dpr": 1}, "viewport": {"w": 1920, "h": 1080},
  "shell": {"name": "electron", "version": "3.0.0", "uptime_s": 7200, "caps": ["capture"]},
  "cache": {"files": 12, "bytes": 123456789}, "storage_free_bytes": 52000000000,
  "errors": [{"t": 1731312000000, "msg": "video stalled"}]}
```

`ua` up to 300 characters, `build` 40, shell `name`/`version` 20, up to 8 `caps` of 20
characters, up to 10 `errors` (`t` in Unix ms, `msg` up to 200 characters). Keep the whole
heartbeat body under 8 KB: a `413` counts as a failed heartbeat.

If `manifest_version` differs from the version you are playing, fetch the manifest. This is the
safety net when the event stream is down. The admin shows a display as online while heartbeats
arrive (offline after 90 seconds without one). On a JSON 401, go back to pairing; ignore network
errors and keep playing.

## Plays

### `POST /plays`

Authenticated. Proof of play: the screen reports each item it finished showing. Collection is
off until an admin turns it on (**Reports > Proof-of-play settings**).

```json
{"events": [{"id": "0b6f3c1e-8a4d-4f7e-9c2a-5d1e7f3a9b40", "media_id": 12, "item_id": 41,
  "started_at": "2026-11-12T09:15:00.000Z", "duration_ms": 10000}]}
```

| Field | Rule |
|---|---|
| `id` | A unique id per play, `^[A-Za-z0-9_-]{8,64}$` (a UUID works). The server ignores an id it already stored for this display, so a batch can safely be sent again. |
| `media_id` | The manifest item's `media_id` (integer, at least 1). An id the server no longer knows is still accepted. |
| `item_id` | The manifest item's `id`, or `null`. |
| `started_at` | When the item went on screen, ISO 8601 with a time zone (`Z`). Must be within the last 7 days (and inside the server's raw retention, `pop.raw_days`) and at most 5 minutes in the future. |
| `duration_ms` | How long it was visible, integer. Clamped to 0..86,400,000; plays under 500 ms are dropped. |

At most 200 events and 64 KB per request (`400 too_many_events`, `413 too_large`). A body that
is not a JSON object, or `events` that is not a list, gives `400 bad_request`. An event that
breaks a rule is dropped and counted, never an error, so one bad event cannot block a queue.

`200 OK`:

```json
{"status": "ok", "accepted": 1, "duplicate": 0, "dropped": 0}
```

When collection is off: `200 {"status": "disabled", "retry_after_s": 1800}`. Clear the queue,
stop collecting, and after `retry_after_s` seconds probe with an empty `{"events": []}`.

How the browser player (`static/player/pop.js`) does it, as a model for other clients:

- A play starts when a slide goes on screen and ends when the next one replaces it; each repeat
  of a single-item playlist is a new play. Failed slides are not recorded.
- Time under a fullscreen emergency overlay is subtracted (sampled twice a second); a banner
  overlay does not count as covering the content.
- The queue lives in `localStorage` (`aisignx.plays`), at most 1000 events (the oldest are
  dropped). It is sent at 50 events or every 60 seconds, 200 per request, and an event is
  removed only after a 2xx `"status": "ok"`. A `400` or `413` drops that batch.
- `started_at` comes from the device clock, so a screen with a wrong clock loses its plays.
- A 401 is left to the player's normal pairing logic.

The server rolls the events up into daily totals per screen and media once an hour, in the
organization time zone, for the play reports in the admin.

## Live events

### `GET /events`

Authenticated. A Server-Sent Events stream (`text/event-stream`). Every `data:` line is JSON.

- The stream starts with a padding comment and `retry: 5000`, then a `hello` event.
- A keep-alive event `ka` (data `{}`) arrives about every 20 seconds. It is a named event, not
  an SSE comment, so clients whose SSE library hides comments can still see it. If no event at
  all arrives for 60 seconds, close the connection and reconnect.
- The server ends each stream after about 30 minutes with `bye`. Reconnect; this re-checks the
  token.
- On any error, close the stream, fetch the manifest once (an event stream cannot show you a
  401, but the manifest request can), then reconnect with a backoff of 1, 2, 4 … up to 30
  seconds, with ±20% jitter. Reset the backoff when `hello` arrives.
- `503 busy` means the server has too many open streams. Keep playing; heartbeats still bring
  changes within `heartbeat_s`. Retry after `Retry-After`.

| Event | Data | What to do |
|---|---|---|
| `hello` | `{"build": "3.0.0-1a2b3c4d", "manifest_version": "…", "server_time_ms": …, "heartbeat_s": 30, "display": {"id": 3, "name": "Lobby"}}` | Sent on every connect. If `manifest_version` differs from yours, fetch the manifest. `build` is for the browser player; native clients ignore it. There is no `Last-Event-ID` replay; `hello` carries the current state. |
| `manifest` | `{}` | Something may have changed: fetch the manifest with `If-None-Match`. Several hints may arrive together. |
| `command` | `{"action": "reload"}` | Restart the player (the browser reloads the page). Wait for the end of the current item, and only when the server is reachable. Ignore unknown actions. |
| `command` | `{"action": "identify", "seconds": 15}` | Show the display name over the content for that many seconds (clamp to 3-60). |
| `command` | `{"action": "screenshot", "request_id": "<16 hex>"}` | Capture the screen and send it with `POST /screenshot`. Also sent right after `hello` while a request made in the last 2 minutes is still open. |
| `command` | `{"action": "diagnostics"}` | Send a heartbeat with `diag` now. |
| `command` | `{"action": "commands"}` | A device command was created. The browser player ignores it and does not pass it to the app. The Windows and Android apps poll `GET /commands` every 20 seconds by themselves, so a command reaches a screen within about 20 seconds either way. |
| `command` | `{"action": "unlock", "minutes": 15, "request_id": "<16 hex>"}` | Lift the Lock player for `minutes` (clamp to 1-240), measured on a monotonic clock and kept in memory only, so a restart locks again. While unlocked, the app can be closed and its admin exit skips the PIN. Ignore a `request_id` you have already seen. The browser player forwards it to the shell (`onCommand`) and, when the shell reports the `kiosk-lock` cap, shows "Unlocked for 15 min" (with no such shell, "No app to unlock" in debug mode only). Never stored and not re-sent after a reconnect. |
| `command` | `{"action": "lock", "request_id": "<16 hex>"}` | End a remote unlock now. Forwarded to the shell like `unlock`. |
| `revoked` | `{}` | The token was revoked. Fetch the manifest; the 401 sends you back to pairing. |
| `bye` | `{"reason": "max_age"}` or `{"reason": "replaced"}` | The stream is ending. Reconnect after a random 0-3 seconds. |
| `ka` | `{}` | Keep-alive. Only resets your silence watchdog. |
| `emergency` | The same object as `manifest.emergency` | Show it right away, replacing any current overlay, then fetch the manifest. Also sent right after `hello` on every connect while an emergency applies to this screen. |
| `emergency_clear` | `{"id": 3}` | Hide the overlay only if it shows emergency `id`, then always fetch the manifest. If another emergency is still live, an `emergency` event for it is sent instead. |

An emergency overlay is never cleared by a dropped stream, `bye`, a reconnect or a failed request;
only `emergency_clear`, a manifest with `"emergency": null`, or going back to pairing clears it.
Emergency events are never dropped when a screen's event queue is full.

Live push reaches only screens connected to the same server process. That is why the heartbeat
check above is required, not optional.

## Player controls

With `settings.allow_input` on, the browser player turns touch, mouse, keyboard and remote
input into seven control actions. Only while the player is `PLAYING` and no emergency shows;
an emergency also ends any pause.

| Action | Video item | Image / web item |
|---|---|---|
| `toggle` | Pause/resume | Pause/resume (freezes the item's timer) |
| `play` / `pause` | Explicit | Explicit |
| `forward` | +10 s (at the end: next item) | Next item |
| `back` | -10 s (stops at 0 s) | Previous item |
| `next` / `prev` | Next/previous item | Same |

In a one-item playlist, `next` and `prev` restart the item. A paused player resumes on its own
after `settings.resume_after_s`, and at once when a manifest turns `allow_input` off. The mouse
does not drag; it uses click, double-click, wheel and its side buttons.

| Input | Action |
|---|---|
| Space, Enter | `toggle` |
| ArrowRight / ArrowLeft | `forward` / `back` |
| ArrowUp / ArrowDown | `prev` / `next` |
| PageUp / PageDown | `prev` / `next` |
| MediaPlayPause | `toggle` |
| MediaPlay | `play` |
| MediaPause, MediaStop | `pause` |
| MediaTrackNext / MediaTrackPrevious | `next` / `prev` |
| MediaFastForward / MediaRewind | `forward` / `back` |
| ChannelUp / ChannelDown | `prev` / `next` |
| Tap (under 300 ms, under 10 px) | `toggle` |
| Swipe left / right (at least 50 px, mostly sideways) | `next` / `prev` |
| Press 300 ms, then drag | Scrub the video (on other items it is a swipe) |
| Mouse click | `toggle` (after 250 ms, cancelled by a double-click) |
| Double-click right / left half | `forward` / `back` |
| Wheel down / up | `forward` / `back` (one step per 300 ms) |
| Mouse back / forward button | `prev` / `next` |

A held key repeats `forward`/`back` at most every 250 ms; repeats of the other actions are
ignored. Gestures are measured in the screen's upright frame, so they follow the
`orientation` setting. On Android, the D-pad and remote buttons map the same way, and a short
press of OK is `toggle`.

**Proof of play.** Paused time does not count as play time. A skipped item is reported with
the time it was really on screen; a play under 500 ms is dropped.

## Screenshots

### `POST /group/transport`

Authenticated. A screen of a group with synchronized playback moved the group's timeline (next, previous, seek, pause or resume). The player works out the new state (it knows the item durations); the server numbers it, stores it on the group and sends a `manifest` hint to every screen of the group, whose manifest then carries it as `sync.transport`.

```json
{"key": "0:12=10000,15=8000", "offset_ms": 1790000000123, "paused_ms": null, "resume_at_ms": null}
```

- `key`: the player's sync key for the playlist (anchor and each item's id and duration). A screen whose own key differs ignores the state.
- `offset_ms`: the timeline position at server time `T` is `T - offset_ms`.
- `paused_ms` and `resume_at_ms`: both numbers while paused, else both `null`. The timeline stands at `paused_ms` until server time `resume_at_ms`, then runs on from there.

`200 {"transport": {"rev": 7, "key": ..., "offset_ms": ..., "paused_ms": ..., "resume_at_ms": ...}}` with `rev` increasing by one each time. `409` when the screen has no group or the group does not play in sync; `400` for a bad body. A screen applies a state only when its `rev` differs from the one it applied last. The state is kept across server restarts.

### `POST /screenshot`

Authenticated. Answers a `screenshot` command. The body is the raw image (not JSON), with
`Content-Type: image/jpeg` or `image/png` and the header `X-Screenshot-Request: <request_id>`.
Keep the longest side at 1920 pixels or less; the body limit is 4 MB.

- `200 {"status": "saved"}`: stored. The server re-encodes it as a JPEG (EXIF dropped) and
  keeps only the latest screenshot per display.
- `409 stale_request`: the id is not the open request (wrong, already used, or older than
  2 minutes). Each request id works once.
- `415 unsupported_type`, `400 bad_image` (not a readable image, or over 40 megapixels),
  `413 too_large`.

A screen cannot upload an image nobody asked for. Uploading publishes nothing to other screens.

## Shell hook

The kiosk clients (Electron and Android, see `docs/clients.md`) do not reimplement the player.
They load the browser player at `/player/` and talk to it through a small hook. In a plain
browser there is no hook and the player behaves exactly as before.

`net.shell()` in `static/player/net.js` looks for one of two hosts and returns a normalized
object `{info, onState(s), onManifest(json), capture(), stats(), onCommand(obj),
onControl(fn)}`, or `null`:

- **Electron:** `window.AiSignXShell`, exposed by the preload with `contextBridge`, with
  `info()` returning an object and the functions `onState(s)`, `onManifest(json)`, `capture()`
  and `stats()`.
- **Android:** `window.AiSignXShellInfo.info()` (an `addJavascriptInterface` object) returns a
  JSON string. `onState` and `onManifest` post a JSON string through `window.AiSignXShellPort`
  (`addWebMessageListener`): `{"t":"state","s":"PAIRED"}` or `{"t":"manifest","m":"<manifest JSON
  string>"}`. Without the port (old WebViews) they do nothing.

Every call into the shell is wrapped in `try/catch`; a broken shell never stops playback.

| `info` field | Meaning |
|---|---|
| `name` | `electron` or `android`. Any other value means "no shell". |
| `version` | Client version, for example `3.0.0`. The player reports `player_version` as `<name>-<version>` (for example `electron-3.0.0`) instead of `web-<build>`. |
| `device` | Device name sent with `POST /pair` (up to 120 characters), for example `HOSTNAME / Windows`. |
| `client_id` | 32 lowercase hex characters. If valid, the player stores and uses it as its `client_id`. |
| `admin_url` | The server URL configured in the client (`http(s)://host[:port]`). Used for the pairing hint `<admin_url>/displays/pair`, because behind the Electron proxy `location.origin` is a loopback address. |
| `caps` | Optional list of capabilities: `capture` (the shell can take a real screenshot), `rotate` (the shell rotates the device itself, so the page does not rotate with CSS), `pin-exit` (leaving the app asks for the unlock PIN), `controls` (the shell forwards keys and remote buttons as control actions), `kiosk-lock` (the shell honours `kiosk_lock` and the `unlock`/`lock` commands), `agent` (the device agent runs in the shell), `brightness` (the shell sets the screen's own brightness from `settings.brightness` and `settings.dim`, so the page draws no darkening layer). Unknown values are ignored. |

**Brightness.** With the `brightness` cap the shell applies the level itself and the page only
reports emergencies: `holdBrightness(true)` while an emergency message is on screen (full
brightness), `holdBrightness(false)` when it is gone. Android: the page posts
`{"t":"bright","full":true|false}` through the port. Electron (3.6.0+):
`AiSignXShell.holdBrightness(full)`. The Windows app reports the cap only when its probe found a
screen it can set (WMI for built-in panels, DDC/CI for monitors); otherwise the page keeps its
darkening layer. It does not touch a PC's brightness until a display asks for less than 100 %.
The Android app does not report the cap on Android TV (3.0.11+): a TV box sends HDMI and has no
backlight, so the page darkens the picture there.

**Capture.** `capture()` resolves to a JPEG `Blob` of the real screen, or `null`; the page then
falls back to drawing its own media on a canvas. Electron: `AiSignXShell.capture()` returns
JPEG bytes. Android: the page posts `{"t":"capture","id":<n>}` through the port and the shell
replies on the same port with `{"t":"capture","id":<n>,"ok":true,"jpeg":"<base64>"}` (or
`"ok":false`); the page gives up after 10 seconds.

**Stats.** `stats()` returns `{uptime_s, cache: {files, bytes}, storage_free_bytes, lock}` or
`null` (Electron `AiSignXShell.stats()`, Android `AiSignXShellInfo.stats()` as a JSON string).
It goes into the heartbeat `diag`. It never holds secrets: on Android, iframes can call it too.
The optional `lock` is one of `off`, `locked`, `unlocked`, `pinned` (Android screen pinning,
no Device Owner), `owner` (Android Device Owner lock task) or `failed`; the admin shows it as
`diag.shell.lock`.

**Commands (page to shell).** `onCommand(obj)` forwards the `unlock` and `lock` SSE commands,
and nothing else. Electron: `AiSignXShell.onCommand(json)` with the command as a JSON string
under 1 KB. Android: the page posts `{"t":"command","c":{...}}` through the port. Shells check
the payload themselves (`minutes` an integer, `request_id` 16 lowercase hex) and dedupe by
`request_id`.

**Controls (shell to page).** `onControl(fn)` registers `fn(action, repeat)`, where `action` is
one of `toggle`, `play`, `pause`, `forward`, `back`, `next`, `prev` and `repeat` is a boolean
(a held key). Electron: `AiSignXShell.onControl(cb)`; a later call replaces the callback.
Android: the page posts `{"t":"controls"}` on its first `onControl` call (and again after every
page load); the shell keeps that message's reply proxy and sends
`{"t":"control","a":"next","r":false}` on it. Shells forward keys only while their stored
`allow_input` is `true`; otherwise keys stay blocked. The page validates every action and
ignores them unless controls are on, it is `PLAYING` and no emergency shows. Both hooks are
no-ops on an old or broken shell.

`onState(s)` is called on every state change with one of `CONNECTING`, `SETUP_WAIT`, `PAIRING`,
`PLAYING`, `NO_CONTENT`, plus `PAIRED` once when pairing succeeds (before the player boots).
Shells flush their cookie store to disk on `PAIRED`.

`onManifest(json)` is called with the manifest as a JSON string after every `GET /manifest` that
returned 200 (never on 304, and not for the copy restored from `localStorage`). Shells use it to
prefetch and keep image and video files in their media cache, and to keep the native part of
`settings`: the unlock PIN hash, `allow_input` and `kiosk_lock` (both apps) and, on Android,
the orientation. They store these so an offline start still applies them. A manifest without `settings` changes
nothing; one whose `settings` has `"unlock": null` removes the PIN.

**No token or cookie ever reaches the hook.** The credential stays in the HttpOnly
`aisignx_display` cookie, which JavaScript cannot read. The manifest carries only a salted
PBKDF2 hash of the unlock PIN, for the native apps; the browser player never uses it and does
not store it in `localStorage`. The hook carries only device info, capabilities, stats, state
names, screenshots and the manifest.

Clients check a server address with `GET /healthz` (no login, no cookie, works before setup).
It answers `{"status": "ok", "app": "aisignx", "api": 1}`; `app` identifies an AiSignX server and
`api` is the player API generation. It never includes the software version.

## Example (curl)

```bash
S=http://192.168.1.10:5000/api/player/v1
curl -s -X POST -H "Content-Type: application/json" -d '{"client_id":"test01"}' $S/pair
# enter the code in the admin, then:
curl -s -X POST -H "Content-Type: application/json" -d '{"pairing_secret":"<secret>"}' $S/pair/status
curl -s -H "Authorization: Bearer <token>" $S/manifest
curl -sN -H "Authorization: Bearer <token>" $S/events
```
