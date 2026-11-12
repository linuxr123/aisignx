# Player API (v1)

This is the contract between the AiSignX server and its players: the browser player at
`/player/` and the Electron and Android clients that follow in Phase 5. A client that follows
this document can pair, play content, stay up to date and recover from errors without any other
knowledge of the server.

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
| 401 | `unauthorized` | Token missing, wrong, unpaired or the display was deleted | Drop the token (and cached manifest), go back to pairing. |
| 403 | `forbidden` | Not allowed | Client bug. |
| 404 | `not_found` | Unknown path or media | Skip that item or request. |
| 404 | `expired` | Pairing secret unknown, used or expired | Start pairing again with `POST /pair`. |
| 405 | `method_not_allowed` | Wrong HTTP method | Client bug. |
| 413 | `too_large` | Request body over 8 KB | Client bug. |
| 429 | `too_many_pending` | Too many screens are waiting to pair | Wait `Retry-After` seconds (60), then try again. |
| 503 | `busy` | Server is at its connection limit, or a pairing code collision | Wait `Retry-After` seconds, keep playing. |
| 500 | `server_error` | Unexpected server fault | Retry later, keep playing. |
| 503 | `setup_required` | The server has no admin account yet | Show "waiting for setup", retry after `Retry-After` (30). |

Anything else (network error, timeout, other 5xx, a non-JSON response) means "retry later".
Use one retry loop with a backoff from 2 s to 30 s and ±20% random jitter, and keep playing
meanwhile. Use a request timeout of about 10 seconds.

## Request bodies

- Send a JSON object with `Content-Type: application/json`. The body limit is 8 KB.
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
 "emergency": null}
```

| Field | Meaning |
|---|---|
| `v` | Schema version of the manifest (1). |
| `server_time_ms` | Server clock (Unix ms) when the manifest was built. Use it to estimate clock drift. |
| `heartbeat_s` | How often to send a heartbeat. |
| `playlist` | `null` when nothing plays (`source.type` is `none`). Then `items` is `[]`: show a "no content" screen with the display name. |
| `items` | Play in this order, then loop. |
| `id` | Playlist item ID (report it as `current_item_id`). |
| `type` | `image`, `video` or `web`. Skip types you don't support. |
| `url` | Image and video: a path on this server; resolve it against the server base URL and fetch it with your token. Web: an absolute `http(s)://` URL to show in a web view, without your token. |
| `sha256`, `size` | Checksum and byte size of the file, when known. Useful for an offline cache. |
| `duration_ms` | How long the item stays on screen. This is a hard limit. |
| `media_duration_ms` | Length of the video file, or `null`. |
| `transition` | How this item enters (see below). |
| `fit` | How the item fills the screen (see below). |
| `muted` | Play the video's sound only when this is `false`. Always `true` for images and web pages. |

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

Authenticated. Returns the image or video file for an item `url`.

- Supports `Range` requests (`206 Partial Content`) for video seeking and resumed downloads.
- `Cache-Control: private, max-age=31536000, immutable`. The URL changes when the file changes,
  so a cached file never needs to be checked again.
- `404 not_found` when the media was deleted or the name doesn't match.

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

If `manifest_version` differs from the version you are playing, fetch the manifest. This is the
safety net when the event stream is down. The admin shows a display as online while heartbeats
arrive (offline after 90 seconds without one). On a JSON 401, go back to pairing; ignore network
errors and keep playing.

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

## Example (curl)

```bash
S=http://192.168.1.10:5000/api/player/v1
curl -s -X POST -H "Content-Type: application/json" -d '{"client_id":"test01"}' $S/pair
# enter the code in the admin, then:
curl -s -X POST -H "Content-Type: application/json" -d '{"pairing_secret":"<secret>"}' $S/pair/status
curl -s -H "Authorization: Bearer <token>" $S/manifest
curl -sN -H "Authorization: Bearer <token>" $S/events
```
