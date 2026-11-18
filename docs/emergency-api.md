# Emergency messages: saved messages and the API

How emergencies behave on screens (targets, levels, playlists) is in
[scheduling.md](scheduling.md#emergency-override). This page covers saved messages, scheduled
messages and the token API. Tokens and the other errors are in [api.md](api.md).

## Saved messages (templates)

On **Emergency**, fill in the send form and put a name in "Save as a template", then **Save as template**.
Nothing is sent. Saved messages appear in a table above the form with **Send now** (asks to confirm) and
**Remove**. Each has an **API id**, used below. Editors and admins can save, send and remove them; viewers
only see the page.

A template keeps its screens as chosen. If that group or display is later deleted, the template refuses to
send instead of widening to all screens. The same happens when its playlist is gone or has nothing playable.

## Raising one from a button, script or webhook

1. Create an API token (user menu > My API tokens) with the **emergency:send** permission. Only editors and
   admins can hold it. Give each button or system its own token so you can revoke one without the others.
2. Call the API with `Authorization: Bearer <token>`:

```
GET  /api/v1/emergency/templates               list saved messages
POST /api/v1/emergency/templates/<id>/send     raise it (201; 200 with "already_live": true if it is already up)
GET  /api/v1/emergency                         what is live now
POST /api/v1/emergency/clear                   clear everything; or {"id": 12} for one message
```

```
curl -X POST -H "Authorization: Bearer $TOKEN" http://server:5000/api/v1/emergency/templates/3/send
curl -X POST -H "Authorization: Bearer $TOKEN" http://server:5000/api/v1/emergency/clear
```

Answers are JSON:

- `GET .../templates`: `{"templates": [{"id", "name", "title", "level", "target"}]}`.
- `POST .../send`: `{"message": {...}, "screens": <number of screens pushed>, "already_live": false}` with 201.
  `message` has `id`, `title`, `level`, `target`, `live`, `created_at` and `cleared_at`.
- `GET /api/v1/emergency`: `{"live": [message, ...]}`.
- `POST .../clear`: `{"cleared": [message, ...]}`. The id can also be given as `?id=12`. An unknown id is 404
  `not_found`. Clearing when nothing is live is not an error; `cleared` is empty.
- Errors: 401 `invalid_token`, 403 `insufficient_scope` or `forbidden`, 404 `not_found` (unknown saved message),
  409 `template_unusable` (its screens or playlist no longer exist).

Safety by design:

- The API can only send **prepared** messages. A leaked token can repeat "Fire drill" but cannot write a new
  message or choose new screens.
- Sending the same template twice (button pressed twice, webhook retried) does not stack duplicates.
- Every send and clear is audited with the token's owner and `via: api`.
- Use HTTPS if the token travels beyond your LAN ([https.md](https.md)).

## Scheduled emergencies

Under **Scheduled** on the Emergency page (editors and admins), pick a saved message, a start time (in the
organization's time zone, set in Settings) and optionally how many minutes it stays up (1 to 1440). At the
start time the server sends it by itself and, if you gave a duration, clears it at the end (shown as
"expired" in the history). Without a duration it stays up until someone clears it.

- Checked every 30 seconds by the background jobs thread, so allow up to about half a minute. If background jobs
  are off (`AISIGNX_JOBS=0`) nothing is sent.
- A start time in the past is refused.
- If the server was off at the start time and is more than 10 minutes late, the plan is marked **missed** and
  is **not** sent: a drill announced late is worse than none.
- A plan whose screens or playlist were deleted is marked **failed**; it never widens to all screens.
- Cancel a pending plan at any time.
- Sends and clears made by a plan are audited with `via: schedule`.

## What is not built

- **Recurring schedules.** A plan fires once. A weekly drill has to be entered again each time.
- **The API cannot create, change or cancel plans, and cannot create or edit saved messages.** It can only send
  a saved message now, list saved messages and what is live, and clear. Save messages and plan them in the admin.
- The API cannot send with a delay or a duration. To end a message after a while, use a plan, or call clear.
- No webhook in the other direction: AiSignX does not call out when an emergency is raised or cleared.
- Nothing has been tested against a real button or alarm system yet; the endpoints are covered by automated
  tests only.
