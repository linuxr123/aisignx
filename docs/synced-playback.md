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
more than about 0.35 s. The check runs every 5 seconds and makes at most one correction per 10
seconds, so a screen that keeps drifting is nudged, not chased. A video that is still loading
or paused is left alone until it plays.

Expect screens to agree to within a fraction of a second. Screens on slow networks or with slow
decoders can be a little behind while a new slide loads. This has been checked in automated
tests and in the browser player; it has not been measured on a real video wall or with
several physical screens, so treat the numbers as design targets, not measurements.

**A video that freezes.** On every screen, synced or not, a watchdog checks the playing video once
a second. If its time stands still it is told to play again (after 3 seconds, or 2 seconds if it is
paused when it should play) and again every 2 seconds. After 10 seconds without progress the slide
counts as failed, like any item that cannot play: the last picture stays up until the next slide is ready. A seek that has
not finished after 6 seconds is asked for again once, and after 20 seconds the slide fails. A video
the viewer paused on purpose is not touched. A synced video that is not playing is therefore handled by
this watchdog, and the drift check takes over again when it plays.

## What changes on a synced screen

- **A control on any screen moves the whole group** (if that display has controls on, see Displays > the screen > Edit > Device agent / controls): next, previous, fast forward, rewind and pause or resume. Every other screen of the group follows within a moment (they learn of it from the server, then fetch the new state). The shared timeline simply moves: after "next" the whole group goes on from the start of the next slide. Fast forward and rewind move every video by 10 s (less in a short video), never out of the slide that is playing. **Pause** freezes every screen on its slide; the group plays on by itself after the display's *Resume after* time (default 5 min) so a wall is never left frozen, or at once when any screen resumes. Dragging through a video is not offered on a synchronized screen (it would move only that one). A fullscreen emergency waits to be shown first.
  If a screen cannot reach the server when a control is used, it moves by itself, plays on alone, and rejoins the group after the *Resume after* time.
  The group's state is stored on the server, so a screen that starts late or loses its connection joins the group where it is, including while it is paused. It applies only to screens that play the same playlist (with the same durations). Changing the playlist restarts the timeline as before.
- **Slides always run for their set duration.** A video that is shorter than its slide plays again
  (a single-item playlist loops); a plugin cannot end its slide early. A YouTube video therefore
  **loops** for the whole slide (its "end the slide when the video ends" option does not apply).
  Set the slide's duration to about the length of the video, or the video repeats. The one exception
  is a single YouTube video with both a start and an end time: the server then sets the slide length to
  the clip length for you.
- Changing the playlist, an item's duration or the group setting restarts the whole group on the
  new timeline at the same moment.
- Emergency messages still show on every screen as usual.

## Plugins

- **News reader (RSS).** Every screen starts the slide on **page 1** and moves on at the same
  moments: the pages are counted from the moment the slide began on the shared clock, not from
  a fixed time of day, so a slide never starts halfway through a rotation. For this to give the
  same pages on every screen, a list page always holds the same number of rows (3 in landscape,
  4 in portrait) whatever the text looks like; rows that do not fit are cut off, not moved to
  the next page. When the feed changes, the new headlines are applied at the next page change on
  all screens together. The **ticker** mode scrolls on the shared clock and matches on every
  screen, but is not tied to the slide start.
- **Stocks ticker** scrolls on the shared clock like the RSS ticker. **Clock** shows the server's time.
- **YouTube.** See Tips below. A single video with both a start and an end time makes the slide exactly
  as long as that clip on synced screens, and the clip loops inside itself if the slide is longer.
  A video with only a start time, several videos, a list, or a live stream does not get this: set the
  slide's duration yourself.
- The image library's slideshow and the weather radar's animation still run on each screen's own
  timer and can be out of step. Weather is static and unaffected.

Plugin authors: `ctx.now()` is the shared server time and `ctx.aligned(fn, ms)` runs `fn` on every
multiple of `ms` of it. In synchronized playback a frame plugin also gets `ctx.slideStart`, the
server time (ms) at which its slide began, and a `slideclock` hook is called when it arrives
(once per showing of the slide, as soon as the frame is up). Outside synchronized playback
`ctx.slideStart` is `null` and the hook is never called, so a plugin must work without it. Use it
to count from the start of the slide instead of from the wall clock, as the news reader does. Native
plugins (YouTube) get `syncElapsed()` instead. See [plugins.md](plugins.md#synchronized-playback-in-plugins).

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

## What is still approximate

- Agreement between screens is "a fraction of a second", not frame-exact, and depends on each screen's
  network and decoder. Unmeasured on real hardware (see above).
- YouTube is held within about half a second; lists, playlists and live streams only start together.
- The image library and the radar animation are not in step (see Plugins).
- Web pages are loaded by each screen on its own, so what they show can differ.
- A screen on a slow or very variable network estimates the server clock less well and can sit a little off.
- Changing a playlist restarts the whole group's timeline; a screen that misses the change keeps its old
  one until it hears of it, normally within the 30-second heartbeat. The same holds for a control used on another screen.
- Group controls have been tested with scripted screens and the server, not with several physical screens.

## Player API

Manifests of synced screens carry a `sync` block and `settings.allow_input: false`:

```json
"sync": {"group_id": 3, "anchor_ms": 0, "cycle_ms": 15000}
```

`cycle_ms` is the sum of the items' `duration_ms` (each at least 1000; a single YouTube clip with start and end
times already carries its clip length as `duration_ms`). The slide at server time
`T` is found by walking the items' durations through `(T - anchor_ms) mod cycle_ms`. Clients that do
not know `sync` simply play the playlist normally.
