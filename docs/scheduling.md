# Scheduling, groups and emergencies

This page explains how AiSignX decides what each screen plays. Only the server evaluates
schedules; players just follow the manifest (see [player-api.md](player-api.md)).

## What a screen plays

For each screen, the server checks these in order and uses the first that has at least one
**playable** item (a media file that exists, or a web page with an `http(s)` URL):

1. **Emergency** with its own playlist, if one is live for this screen.
2. **Schedules** that are enabled, apply to this screen and are active now, best first (see
   *Precedence*).
3. The screen's **Default playlist** (set on the display's edit page).
4. **Nothing:** the screen shows "no content".

A schedule whose playlist is empty or has no playable items is skipped, and the next one gets
its turn. So you cannot blank a screen by scheduling an empty playlist.

The **Default playlist** is what a screen plays when no schedule matches. Screens without
schedules simply play their default playlist all the time.

## Schedules

A schedule has a playlist, a target, days of the week, an optional time window, optional start
and end dates, a priority (0 to 100) and an on/off switch.

- **Target:** all screens, one group, or one display.
- **Time zone:** all times use the organization's time zone from **Settings**. There is no
  per-schedule zone. Changing the zone updates the screens straight away.
- **All day** means from midnight to the next midnight.
- **Time window:** the start is included, the end is not. 08:00-17:00 plays from 08:00 up to
  16:59:59, so a schedule 17:00-20:00 follows without overlap. Start and end cannot be equal.
- **Overnight:** an end earlier than the start ends the next day, for example 22:00 → 06:00
  (+1 day).
- **Owning day:** the weekdays and dates are checked against the day a window **starts**. A
  Friday 22:00-02:00 schedule plays Friday night until Saturday 02:00, but does not play in the
  early hours of Friday. If its end date is a Friday, that Friday night still runs to Saturday
  02:00. Start and end dates are inclusive.

### Precedence

When several schedules are active on a screen, the winner is chosen by:

1. Higher **priority**.
2. Then the more **specific** target: display, then group, then all screens.
3. Then the **older** schedule (lower ID).

So a group schedule with priority 10 beats a display schedule with priority 0, but at equal
priority the display schedule wins. The schedule list shows **Playing on N** for schedules that
are on screen now, and **Shadowed** for schedules that have times in the next 7 days but never
win on any screen they target. Each display page lists what it will play over the next 7 days,
worked out with the same rules as playback.

### Daylight saving time

Each window is turned into exact instants in the organization's zone. On the two change days:

- **Clocks go forward** (Berlin, 29 March 2026, 02:00 → 03:00): a time that does not exist
  moves to the moment of the change. A schedule starting at 02:30 starts at 03:00 local time
  (01:00 UTC).
- **Clocks go back** (Berlin, 25 October 2026, 03:00 → 02:00): a time that happens twice means
  the **first** one. A 02:15-02:45 schedule plays once, from 00:15 to 00:45 UTC, and does not
  play again in the repeated hour. A 22:00-02:30 schedule from Saturday ends at the first
  02:30 and does not resume.
- Some windows are an hour shorter or longer on these days, and a window that ends up with no
  time at all is skipped for that day.

Each start and end happens once, so screens switch once.

## Groups

A group is a set of displays, used as a schedule or emergency target. Groups are flat, and a
display belongs to at most one group. Adding a display to a group moves it out of its old
group.

Deleting a group keeps its displays (they just have no group) but removes the group's
schedules and clears its live emergencies. Deleting a display also removes its schedules. A
playlist that a schedule or a live emergency uses cannot be deleted; the page names the
schedules that use it.

## Emergency override

Editors and admins send emergencies from the **Emergency** page; viewers can only look. An
emergency has a title, an optional message, a level (info, warning or critical), a target (all
screens, a group or a display) and an optional playlist.

- **No playlist:** the text covers the whole screen and video is muted.
- **With a playlist:** the playlist plays instead of the schedules. With **Show text** on, the
  text appears in a band at the bottom; with it off, only the playlist plays.
- If the chosen playlist has nothing playable, the text covers the screen, as without one.
- Emergencies stay until someone clears them. They never expire.
- If several are live, each screen shows one: the highest level, then the newest.
- While any emergency is live, every admin page shows a red banner with a link to manage it.
- **Send again** on a past message fills the form with its content.
- A screen that loses its connection keeps showing the emergency until it reaches the server
  again and learns it was cleared. This is deliberate.

Deleting the target display or group clears its emergencies; they stay in the history with the
reason "target deleted".

## How updates reach screens

- Any change to schedules, groups, displays, playlists, media or emergencies is pushed to
  screens over the live event stream within about a second.
- A background ticker checks every screen just after each minute and pushes an update to the
  screens whose content changed, so schedule starts and ends take effect within about a second.
  It also catches date changes, daylight saving changes and time zone changes.
- Emergencies are sent straight to the targeted screens and are never dropped from a full queue.
- The ticker is on by default. Set `AISIGNX_SCHEDULE_TICKER=0` to turn it off; screens then pick
  up schedule changes through their 30-second heartbeat.

**One process.** The ticker and the live event stream work inside one server process. Run a
single process (`flask --app wsgi serve`). With several processes, or with a change made from
another process, screens still catch up through the heartbeat, within about 30 seconds.

## Manual player checklist

Use a browser player at `/player/` and the admin side by side.

1. **Send** an emergency to the screen: the overlay appears within about a second.
2. **Disconnect the network** from the screen: the overlay stays.
3. **Reconnect, then reload the player** before clearing: the overlay comes straight back.
   (A reload while the server is unreachable shows the browser's own error page, because
   the page itself isn't cached offline. Keep the browser open; don't reload it while offline.)
4. **Reconnect and clear** the emergency: the overlay disappears.
5. Send one **with a playlist and Show text on:** the playlist plays with a band at the bottom.
6. Create a schedule that **starts in two minutes:** the screen switches within about a second
   of the start, and back at the end.
7. Send two emergencies to the same screen, then clear the newer one: the older one stays on
   screen without flicker.
