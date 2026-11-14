# Media API (v1)

The media API lets scripts and the PowerPoint add-in list, upload and replace media without
the web admin. It is JSON over HTTP and authenticated with personal API tokens.

All paths below are relative to the server's address, for example
`http://192.168.1.10:5000/api/v1/media`. The screens use a different API (`/api/player/v1`,
see [player-api.md](player-api.md)).

## API tokens

Each user creates their own tokens under **your user menu > API tokens** (`/tokens/`):

1. Give the token a name that says what uses it, for example "PowerPoint, meeting room".
2. Choose its permissions (scopes) and when it expires (never, 30 days, 90 days or 1 year).
3. Click **Create token** and copy the token. **It is shown only once.** AiSignX keeps only a
   SHA-256 fingerprint, so a lost token cannot be shown again; revoke it and make a new one.

A token looks like `asx_` followed by 43 random characters. The first 12 characters are shown in
the lists so you can tell tokens apart. Treat a token like a password: anyone who has it can act
as you, within its scopes.

| Scope | Allows | Lowest role that may hold it |
|---|---|---|
| `media:read` | List media and read one item | Viewer |
| `media:write` | Upload new media and replace a file | Editor |

A token can never do more than its owner's role allows. The role is checked again on every
request: if an editor becomes a viewer, their `media:write` tokens stop working at once (403),
and disabling or deleting a user stops all of their tokens.

Revoke a token on the same page. Admins see every user's tokens under **Admin > All API
tokens** (`/tokens/all`) and can revoke any of them. Creating and revoking tokens, and every
upload or replace made with one, appears in the audit log with the token's owner and "api".

## Authentication

Send the token in the `Authorization` header on every request:

```
Authorization: Bearer asx_...
```

The API never reads a token from the URL and never accepts the admin's session cookie, so it
needs no CSRF token. Always use HTTPS when the server is reachable from outside your own
network (see [https.md](https.md)).

## Errors

Every error has the same JSON shape. Decide on `error` and the HTTP status, never on
`message`, which is for people:

```json
{"error": "insufficient_scope", "message": "This token lacks media:write."}
```

| HTTP | `error` | Meaning |
|---|---|---|
| 400 | `missing_file` | No multipart field named `file` |
| 400 | `invalid_duration` | `duration` is not a whole number from 1 to 86400 |
| 400 | `invalid_media` | The file is not a readable image or video |
| 401 | `invalid_token` | Token missing, unknown, revoked or expired, or its user is disabled |
| 403 | `insufficient_scope` | The token lacks the scope this request needs |
| 403 | `forbidden` | The owner's current role no longer allows this scope |
| 404 | `not_found` | No media with that id, or an unknown path |
| 405 | `method_not_allowed` | Wrong HTTP method |
| 409 | `not_replaceable` | The item is a web page; only uploaded files can be replaced |
| 413 | `too_large` | The upload is larger than `AISIGNX_MAX_UPLOAD_MB` (default 2048 MB) |
| 415 | `unsupported_type` | The file name does not end in a supported image or video extension |
| 429 | `rate_limited` | Too many failed token checks from this IP address; retry after `Retry-After` |
| 503 | `maintenance` / `busy` | The server is restoring a backup or busy; retry after `Retry-After` |
| 500 | `server_error` | Unexpected server fault |

## The media item

```json
{
  "id": 42,
  "name": "Quarterly results",
  "type": "video",
  "mime": "video/mp4",
  "size": 1843200,
  "width": 1920,
  "height": 1080,
  "duration": 31,
  "media_duration_s": 30.4,
  "default_duration": 15,
  "checksum_sha256": "9f2c...",
  "url": null,
  "created_at": "2026-11-12T10:00:00Z"
}
```

- `type` is `image`, `video` or `web`. Web pages have a `url` and no file fields.
- `duration` is how long the item plays (seconds) when a playlist item doesn't set its own.
- `media_duration_s` is the length read from a video file (null for images, or when ffmpeg is
  not installed).
- `default_duration` is the stored fallback duration.

### Duration rules

- **Video:** the length read from the file always wins. The `duration` you send is stored as
  `default_duration` and used only when the length could not be read (for example when ffmpeg is
  missing on the server).
- **Image:** the `duration` you send is how long it shows.
- Without a `duration`, a new upload gets the default from **Settings**.
- A playlist item's own duration overrides all of this.

## Endpoints

| Method and path | Scope | Purpose |
|---|---|---|
| `GET /api/v1/media` (also `GET /api/media`) | `media:read` | List media |
| `GET /api/v1/media/<id>` | `media:read` | One item |
| `POST /api/v1/media` (also `POST /api/media`) | `media:write` | Upload a new file |
| `POST /api/v1/media/<id>/replace` (also `POST /api/media/<id>/replace`) | `media:write` | Replace the file of an item |

The `/api/media` paths are what the PowerPoint add-in calls. They are kept forever and behave
exactly like the `/api/v1/media` paths.

### List: `GET /api/v1/media`

Query parameters: `page` (from 1, default 1) and `per_page` (1 to 100, default 50). Newest
first.

```json
{"items": [{"id": 42, "name": "...", "...": "..."}], "page": 1, "per_page": 50, "total": 1}
```

### One item: `GET /api/v1/media/<id>`

Returns the item, or 404 `not_found`.

### Upload: `POST /api/v1/media`

A `multipart/form-data` request with these fields:

| Field | Required | Meaning |
|---|---|---|
| `file` | yes | The image (JPG, PNG, GIF, WebP, BMP) or video (MP4, WebM, MOV, M4V, MKV). The type comes from the file name's extension. |
| `name` | no | Display name, cut to 200 characters. Blank or missing: the file name without its extension. |
| `duration` | no | Whole seconds, 1 to 86400. See the duration rules above. Blank counts as missing. |

The answer is **201 Created** with the new item and a `Location: /api/v1/media/<id>` header.
The new item is in the media library; add it to a playlist in the admin to show it on screens.

### Replace: `POST /api/v1/media/<id>/replace`

The same multipart fields. `file` is required. `name` and `duration` change the item only when
you send them; otherwise the current name and duration are kept.

The item keeps its id and stays in every playlist; an image can be replaced with a video and the
other way round. Screens pick up the new file within seconds. The old file is deleted at once.
The answer is **200** with the updated item.

## Examples (curl)

```sh
TOKEN=asx_...
SERVER=http://192.168.1.10:5000

# List
curl -H "Authorization: Bearer $TOKEN" "$SERVER/api/v1/media?per_page=20"

# Upload a video, shown as "Quarterly results"
curl -H "Authorization: Bearer $TOKEN" \
     -F file=@results.mp4 -F name="Quarterly results" -F duration=15 \
     "$SERVER/api/v1/media"

# Replace the file of item 42, keeping its name and duration
curl -H "Authorization: Bearer $TOKEN" -F file=@results-v2.mp4 \
     "$SERVER/api/v1/media/42/replace"
```

On Windows PowerShell, call `curl.exe` (not the `curl` alias) and use `$env:TOKEN`.

## PowerPoint add-in

The add-in exports the current presentation as an MP4 video and uploads it.

1. In AiSignX, create a token with the **media:write** scope (an editor or admin account).
2. In the add-in's settings, enter:
   - **Server URL:** the server's base address only, with no path, for example
     `http://192.168.1.10:5000` or `https://signage.example.com`. Do not add `/api` or
     `/media`.
   - **Token:** the token you copied.
3. **New upload:** the add-in sends the video with the name and duration you choose. The new item
   appears in **Media**; add it to a playlist.
4. **Replace mode:** to update a presentation that is already on screens, type the media **ID**.
   The ID is shown on each card in **Media** ("ID 42") and on the item's edit page. Every
   playlist that uses the item shows the new version within seconds.

Notes:

- The add-in does not show the ID of a new upload; look it up in **Media**.
- The add-in waits at most 100 seconds for an upload (a .NET default). A large video over a slow
  link can time out even though the server is fine. Use a faster link, a shorter or
  lower-resolution export, or upload the file in the admin instead.
- A reverse proxy in front of AiSignX must allow uploads as large as `AISIGNX_MAX_UPLOAD_MB`
  (for example `client_max_body_size` in nginx); see [https.md](https.md).
- If the server uses a certificate from a private CA, the PC running PowerPoint must trust that
  CA.

## Limits

- Uploads are limited by `AISIGNX_MAX_UPLOAD_MB` (default 2048 MB).
- Every 401 counts against the client's IP address, in the same bucket as failed sign-ins on
  the login page (`AISIGNX_LOGIN_MAX_PER_IP`, default 20 per `AISIGNX_LOGIN_WINDOW_MIN`, default
  15 minutes). Past the limit every API call from that address, even with a valid token, gets
  429 until the window passes. Requests with a valid token never count.
- AiSignX runs as one process; see [deployment.md](deployment.md).
