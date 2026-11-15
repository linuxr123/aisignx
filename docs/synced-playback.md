# Synchronized playback

The screens of a group can show the same slide at the same moment, for example the panels of a
video wall or two screens in one room. Turn it on per group: **Groups > Edit > Synchronized
playback**.

## How it works

There is no coordinator and no extra traffic. Every screen of the group works out which slide
should be on screen from two things it already has:

- the **shared server clock**. Each screen learns how far its own clock is from the server's from
  the `server_time_ms` in the answers it already gets (manifest and heartbeat), corrected by half the
  round trip and using the fastest of the last eight answers. A screen whose own clock is minutes
  wrong is fine: only its distance from the server clock matters.
- the **playlist's item durations**. The playlist repeats every `sum(durations)` milliseconds,
  counted from the Unix epoch (the manifest's `sync.anchor_ms` is 0), so every screen and every
  restart agrees without anything being stored.

A screen that joins late, restarts or loses its network for a while simply jumps to where the
timeline is when it is back. A video joins at the right position and is pulled back if it drifts
more than about 0.35 s (checked every 5 seconds).

Expect screens to agree to within a fraction of a second. Screens on slow networks or with slow
decoders can be a little behind while a new slide loads.

## What changes on a synced screen

- **Player controls are off** (pause, next, seek): one screen going its own way would break the sync.
  The display page says so.
- **Slides always run for their set duration.** A video that is shorter than its slide plays again
  (a single-item playlist loops); a plugin cannot end its slide early. A YouTube video therefore
  **loops** for the whole slide (its "end the slide when the video ends" option does not apply).
  Set the slide's duration to about the length of the video, or the video repeats.
- Changing the playlist, an item's duration or the group setting restarts the whole group on the
  new timeline at the same moment.
- Emergency messages still show on every screen as usual.

## Plugins

The news reader and the stock ticker change headlines and scroll on the shared clock, so they match
on every screen. The clock plugin shows the server's time. The image library's slideshow and the
weather radar's animation still run on each screen's own timer and can be out of step. Plugin
authors: `ctx.now()` is the shared server time and `ctx.aligned(fn, ms)` runs `fn` on every
multiple of `ms` of it.

## Tips

- Put screens that should match in one group and give them the **same playlist** (the same default
  playlist, or the same schedule). Screens of a group that play different playlists are each in
  step with themselves only.
- Use **images, videos and plugins with a fixed duration** for the best result. Live web pages are
  fine, but each screen loads them on its own. **YouTube is close, not frame-accurate.** A single
  YouTube video is held on the shared timeline: each screen compares the position the embed reports
  with where the timeline says it should be, and seeks it back when it is more than about 0.6 s off
  (at most every 4 s, because a seek makes YouTube rebuffer for a moment). Expect screens within
  roughly half a second of each other. A YouTube *list*, *playlist* or *live stream* has no position
  that can be worked out, so those only start together. For a video wall, or anything that must be
  frame-exact, use an uploaded video (download your own videos and upload the MP4s).
- The display page shows each synced screen's clock offset and round trip (Diagnostics).

## Player API

Manifests of synced screens carry a `sync` block and `settings.allow_input: false`:

```json
"sync": {"group_id": 3, "anchor_ms": 0, "cycle_ms": 15000}
```

`cycle_ms` is the sum of the items' `duration_ms` (each at least 1000). The slide at server time
`T` is found by walking the items' durations through `(T - anchor_ms) mod cycle_ms`. Clients that do
not know `sync` simply play the playlist normally.
