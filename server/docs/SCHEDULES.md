# AISignX — Schedules

Complete reference for the scheduling system — how it works, how to create schedules, and how to use Play Now and the Weekly Timeline.

---

## Table of Contents

1. [How the Scheduler Works](#1-how-the-scheduler-works)
2. [Priority System](#2-priority-system)
3. [Creating a Schedule](#3-creating-a-schedule)
4. [Schedule Fields Reference](#4-schedule-fields-reference)
5. [Time and Day Rules](#5-time-and-day-rules)
6. [Play Now Override](#6-play-now-override)
7. [Weekly Timeline View](#7-weekly-timeline-view)
8. [Now Playing Badges](#8-now-playing-badges)
9. [Editing and Deleting Schedules](#9-editing-and-deleting-schedules)
10. [Filtering and Bulk Editing](#10-filtering-and-bulk-editing)
11. [Schedule Examples](#11-schedule-examples)
12. [Interaction with Emergency Broadcasts](#12-interaction-with-emergency-broadcasts)

---

## 1. How the Scheduler Works

When a display requests its next content, the server runs the schedule resolver:

1. Collect all **active** schedules whose target matches this display (directly assigned or via group)
2. Filter to those whose **day of week** includes today (or have no day filter)
3. Filter to those whose **time range** includes the current time (or have no time filter)
4. Filter to those whose **date range** includes today (or have no date range)
5. Of the remaining candidates, select the one with the **highest priority**
6. If no schedule matches ? fall back to the display's **default playlist**

This evaluation happens on every slide transition, so schedule changes take effect within one slide.

---

## 2. Priority System

Priority is a number you assign to each schedule. Higher numbers win.

```
[Emergency Broadcast]    ? top layer, live push, overrides all
        |
[Play Now Override]      ? priority 999, temporary
        |
[Your schedules]         ? your priority values (0–998)
        |
[Default Playlist]       ? fallback, no priority
```

**Rules:**
- Higher priority number wins when two schedules overlap
- If two schedules have equal priority, the one with the lower database ID wins (older schedule wins)
- Play Now overrides use priority 999 — they always beat regular schedules
- Emergency broadcasts use a separate live-push mechanism and sit above everything

### Recommended Priority Ladder

| Priority | Use for |
|---|---|
| 5 | Base all-day, all-week loops |
| 10 | Weekday vs. weekend variations |
| 20 | Time-of-day segments (morning, lunch, afternoon, evening) |
| 30 | Special event or campaign overrides |
| 50 | Holiday / closure overrides |
| 999 | Play Now (auto-assigned) |

---

## 3. Creating a Schedule

1. Go to **Schedules** in the sidebar
2. Click **Create Schedule**
3. Fill in the fields (see [Section 4](#4-schedule-fields-reference))
4. Click **Create Schedule**

The schedule is active immediately. Check the Weekly Timeline to verify it appears as expected.

---

## 4. Schedule Fields Reference

| Field | Required | Description |
|---|---|---|
| Name | Yes | Descriptive label — e.g. "Lobby Morning Mon-Fri" |
| Playlist | Yes | Which playlist plays when this schedule is active |
| Priority | Yes | Higher = higher priority. Default 0 |
| Target | Yes | All Displays, a Display Group, or a single Display |
| Active | Toggle | Enable/disable without deleting |
| Days of Week | No | Mon/Tue/Wed/Thu/Fri/Sat/Sun checkboxes. Leave blank = every day |
| Start Time | No | 24-hour time. Leave blank = start of day (00:00) |
| End Time | No | 24-hour time. Leave blank = end of day (23:59) |
| Start Date | No | Date from which the schedule is valid. Leave blank = no start limit |
| End Date | No | Date after which the schedule expires. Leave blank = no end limit |

---

## 5. Time and Day Rules

### Days of Week

Check specific days to restrict the schedule to those days. Leave all days unchecked to run every day.

| Checked Days | Behaviour |
|---|---|
| None (all blank) | Runs every day |
| Mon, Tue, Wed, Thu, Fri | Runs weekdays only |
| Sat, Sun | Runs weekends only |
| Fri | Runs Fridays only |

### Time Range

Start Time and End Time define a window during which the schedule is active.

| Start Time | End Time | Behaviour |
|---|---|---|
| blank | blank | Active all day |
| 09:00 | 17:00 | Active from 9am to 5pm |
| 00:00 | 23:59 | Same as all day |
| 11:30 | 13:00 | Lunchtime slot |

**Note:** If Start Time and End Time span midnight (e.g. 22:00 to 06:00), create two schedules — one for 22:00–23:59 and one for 00:00–06:00.

### Date Range

Start Date and End Date limit the schedule to a calendar range.

| Start Date | End Date | Behaviour |
|---|---|---|
| blank | blank | No date limit — runs indefinitely |
| 2025-12-01 | 2025-12-31 | December only |
| 2025-11-25 | 2025-11-28 | Thanksgiving long weekend |
| 2025-01-15 | blank | From Jan 15 onward |

Date ranges are useful for seasonal campaigns and events. After the end date, the schedule stops matching and the display falls back to a lower-priority schedule or the default playlist.

---

## 6. Play Now Override

Play Now immediately forces a specific playlist onto a target display or group, bypassing all normal schedules. It creates a temporary priority-999 schedule entry.

### Activating Play Now

1. Go to **Schedules**
2. Click **Play Now**
3. Select the **Playlist** to play
4. Select the **Target** (all displays, a group, or a single display)
5. Click **Play Now**

A "Play Now Override" entry appears at the top of the schedule list with a red **LIVE** badge.

### Stopping a Play Now

Click the **Stop** button next to the Play Now Override entry. Normal scheduling resumes immediately on the next slide transition.

### Use Cases

| Scenario | Use Play Now? |
|---|---|
| Urgent announcement for 30 minutes | Yes |
| CEO visiting — show welcome loop | Yes |
| Permanent change to the schedule | No — create a proper schedule instead |
| Emergency safety alert | No — use Emergency Broadcast instead |

---

## 7. Weekly Timeline View

The **Timeline** tab on the Schedules page shows all active schedules in a 7-day × 24-hour grid.

### Reading the Timeline

- Each row is one day of the week (Monday through Sunday)
- Each column is one hour of the day
- Schedules appear as coloured blocks at the correct time slots
- The current time is marked with a vertical line

### Hover Details

Hover over any block to see:
- Schedule name
- Playlist name
- Time range
- Priority

### Finding Conflicts

If two schedules overlap on the same display/group, the higher-priority one wins. Overlapping blocks are shown stacked — inspect them to decide whether the priorities are set correctly.

### All-Day Schedules

Schedules with no time restriction appear as a full-width bar spanning the entire row. They are always at the bottom layer in the visual stack.

---

## 8. Now Playing Badges

The Schedule List tab shows a green **Now Playing** badge next to the schedule that is currently active and serving content to its target.

| Badge | Meaning |
|---|---|
| Now Playing (green) | This schedule is currently the active one for its target |
| Play Now (red LIVE) | A Play Now override is active |
| Active (no badge) | Schedule is in the future or outside its current window |
| Inactive | Schedule is disabled |

Badges refresh every **30 seconds** automatically. They are server-evaluated — the badge reflects what the server would actually send to a display right now.

---

## 9. Editing and Deleting Schedules

### Editing

Click the pencil icon next to a schedule to open the edit form. All fields can be changed. Changes take effect on the next slide transition on targeted displays.

### Enabling / Disabling

Click the toggle in the Active column to enable or disable a schedule without deleting it. Disabled schedules are skipped by the resolver.

### Deleting

Click the trash icon and confirm. The schedule is removed immediately. Displays that were using it fall back to the next matching schedule or the default playlist.

---

## 10. Filtering and Bulk Editing

The Schedules page includes a filter toolbar so operators can narrow the list
before editing:

- Search by name
- Playlist dropdown
- Target dropdown
- Status dropdown
- Day-of-week filter

Selected rows appear in the bulk action bar. Bulk edits follow the standard
admin pattern: only checked fields are changed, destructive changes require
confirmation, and tenant/permission checks are enforced server-side.

---

## 11. Schedule Examples

### Example 1: Simple all-day weekday loop

| Field | Value |
|---|---|
| Name | Lobby Weekday |
| Playlist | General Loop |
| Priority | 5 |
| Target | Lobby (group) |
| Days | Mon, Tue, Wed, Thu, Fri |

### Example 2: Lunch promotion daily

| Field | Value |
|---|---|
| Name | Lunch Promo |
| Playlist | Lunch Specials |
| Priority | 20 |
| Target | Cafeteria (group) |
| Days | (all) |
| Start Time | 11:30 |
| End Time | 13:30 |

### Example 3: Weekend loop

| Field | Value |
|---|---|
| Name | Weekend Loop |
| Playlist | Weekend Content |
| Priority | 10 |
| Target | All Displays |
| Days | Sat, Sun |

### Example 4: Holiday closure (date-limited)

| Field | Value |
|---|---|
| Name | Christmas Closure |
| Playlist | Holiday Closed Message |
| Priority | 50 |
| Target | All Displays |
| Start Date | 2025-12-24 |
| End Date | 2025-12-26 |

### Example 5: Evening mode

| Field | Value |
|---|---|
| Name | Evening Reduced Content |
| Playlist | After Hours Loop |
| Priority | 15 |
| Target | All Displays |
| Start Time | 18:00 |
| End Time | 22:00 |

### Combined View — what plays on a weekday

| Time | Active Schedule | Playlist |
|---|---|---|
| 00:00–09:00 | Lobby Weekday (P5) | General Loop |
| 09:00–11:30 | Lobby Weekday (P5) | General Loop |
| 11:30–13:30 | Lunch Promo (P20) wins | Lunch Specials |
| 13:30–18:00 | Lobby Weekday (P5) | General Loop |
| 18:00–22:00 | Evening Reduced (P15) wins | After Hours Loop |
| 22:00–23:59 | Lobby Weekday (P5) | General Loop |

---

## 12. Interaction with Emergency Broadcasts

Emergency broadcasts are completely separate from the scheduling system.

- An active emergency **overrides all schedules** and the default playlist
- Emergencies are pushed via SSE and appear within 2 seconds — they do not wait for a slide transition
- The schedule resolver is not consulted while an emergency is active on a display
- When the emergency is cleared, the schedule resolver runs normally on the next slide transition

See [EMERGENCY_BROADCAST.md](EMERGENCY_BROADCAST.md) for full emergency documentation.