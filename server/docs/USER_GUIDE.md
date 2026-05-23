# AISignX — User Guide

Day-to-day operator guide. Covers everything a content manager needs to run AISignX displays.

---

## Table of Contents

1. [Logging In](#1-logging-in)
2. [Dashboard Overview](#2-dashboard-overview)
3. [Media Library](#3-media-library)
4. [Playlists](#4-playlists)
5. [Schedules](#5-schedules)
6. [Emergency Broadcast](#6-emergency-broadcast)
7. [Displays](#7-displays)
8. [Tips and Best Practices](#8-tips-and-best-practices)

---

## 1. Logging In

Navigate to your server URL (e.g. `https://signage.example.com`) and log in with your username and password.

If you have forgotten your password, ask an administrator to reset it from the Users page.

---

## 2. Dashboard Overview

The dashboard shows a live summary of your system:

| Card | What it shows |
|---|---|
| Displays | Total registered displays and how many are currently online |
| Media | Total media files in the library |
| Playlists | Total playlists |
| Schedules | Total active schedules |

The sidebar gives access to all sections. Admin-only sections (Users, Downloads, Browser Access, Plugins) are only visible to administrator accounts.

---

## 3. Media Library

### Supported Content Types

| Type | Formats | Notes |
|---|---|---|
| Image | JPG, PNG, GIF, WebP | Duration = how many seconds it shows on screen |
| Video | MP4, WebM, MOV | Duration auto-detected; set 0 to play the full video |
| Webpage | Any URL | Rendered in an iframe; optional auto-refresh |
| Plugin | YouTube, Weather, Stocks, RSS, Clock, Radar | Dynamic live content |

### Uploading Media

1. Go to **Media** in the sidebar
2. Click **Add Media**
3. For images and videos: drag and drop files, or click to browse
4. For webpages: enter the URL (name is auto-filled from the domain)
5. For plugins: select the plugin type and fill in its configuration fields
6. Click **Upload / Save**

### Media Duration

- **Images:** Set the number of seconds the image displays before the playlist advances
- **Videos:** AISignX detects the video length on upload. Leave duration as `0`
  to play the full video, use **Use detected length** to reset a changed value,
  or set a value to cut it short (acts as a maximum cap)
- **Webpages and plugins:** Use the duration field, or let the content signal completion itself (see [Plugins](PLUGINS.md))

### Thumbnails

- Images and videos get thumbnails automatically on upload
- Webpage thumbnails are generated in the background using Playwright/Chromium
- If thumbnail generation fails a placeholder is shown — this does not affect playback

### Editing and Deleting Media

- Click the pencil icon on any media card to edit its name, duration, or other settings
- Select multiple media rows to use bulk actions such as **Set duration**,
  **Use video length**, tag edits, folder moves, rename, or delete
- Click the trash icon to delete — this removes the file permanently
- Media used in a playlist can still be deleted; the playlist item will show an error until the item is also removed

---

## 4. Playlists

A playlist is an ordered list of media items that plays in sequence on a display.

### Creating a Playlist

1. Go to **Playlists** in the sidebar
2. Click **New Playlist**
3. Enter a name and optional description
4. Click **Create**
5. Open the playlist detail page (click its name)
6. Click **Add Item** and select media from your library
7. Repeat for each item
8. Drag items up and down to reorder them

### Per-Item Settings

Each item in a playlist has its own settings, separate from the media library defaults:

| Setting | Description |
|---|---|
| Duration | Overrides the media-level duration for this specific slot |
| Aspect Mode | How the media fills the screen: `fit`, `fill`, or `stretch` |
| Clip Start | (Video only) Start playback at this time in seconds |
| Clip End | (Video only) Stop playback at this time in seconds |

For video items, duration `0` means play the full detected video length, or the
clip range if Clip End is set. Use **Use detected length** on one item, or
select multiple playlist items and click **Use video length** in the bulk bar.

### Video Clip Ranges

You can play only a portion of a video by setting Clip Start and Clip End:

| Item | Video | Start | End | What plays |
|---|---|---|---|---|
| 1 | promo.mp4 | 0 | 30 | First 30 seconds |
| 2 | clock plugin | — | — | Full duration |
| 3 | promo.mp4 | 30 | 60 | Seconds 30–60 |

The same video file can appear multiple times with different clip ranges. Clips are stored per playlist item — the media file is not modified.

**To set clip times:**
1. Open the playlist detail page
2. Click the pencil icon on a video item
3. Enter Clip Start and Clip End in seconds
4. Click **Save**

The playlist item filters can narrow by search text, media type, transition, and
audio state before selecting rows for bulk operations.

### Aspect Modes

| Mode | Behaviour |
|---|---|
| `fit` | Entire media visible with letterbox/pillarbox bars |
| `fill` | Media fills the screen, cropped if needed |
| `stretch` | Media stretched to fill — may distort aspect ratio |

### Copying a Playlist

Open a playlist and click **Copy Playlist** to duplicate it with all its items. Useful as a starting point for a variation.

### Deleting a Playlist

Open a playlist and click **Delete Playlist**. All items in the playlist are removed automatically — you do not need to remove media items first. The underlying media files are not deleted.

### Live Updates

When you change a playlist that is currently playing on a display, the change is pushed live via SSE — the display reloads the playlist on its next slide transition. The current slide is not interrupted.

---

## 5. Schedules

Schedules let different playlists play at different times of day or days of the week.

### How Scheduling Works

The server evaluates which schedule is active for each display at request time. The highest-priority matching schedule wins. If no schedule matches, the display falls back to its directly-assigned default playlist.

**Priority rules:**
- Higher priority number wins
- If two schedules have the same priority, the one with the lower ID wins
- Play Now overrides (priority 999) always win over regular schedules
- Emergency Broadcasts override everything via a separate live-push mechanism

### Creating a Schedule

1. Go to **Schedules** in the sidebar
2. Click **Create Schedule**
3. Fill in the fields:

| Field | Description |
|---|---|
| Name | Descriptive label (e.g. "Morning Lobby") |
| Playlist | Which playlist to play |
| Priority | Higher number = higher priority. Default is 0 |
| Target | Which display or display group this applies to |
| Days of Week | Leave blank for every day, or select specific days |
| Start Time / End Time | Time range (24-hour). Leave blank for all day |
| Start Date / End Date | Optional date range. Leave blank for no date limit |
| Active | Toggle to enable or disable without deleting |

4. Click **Create Schedule**

### Schedule Examples

**Morning news playlist weekdays 7–9am:**
- Days: Mon, Tue, Wed, Thu, Fri
- Start Time: 07:00 / End Time: 09:00
- Priority: 10

**Weekend loop all day:**
- Days: Sat, Sun
- No time range
- Priority: 5

**Lunch promotion every day 11:30am–1pm:**
- No days filter
- Start Time: 11:30 / End Time: 13:00
- Priority: 15

### Play Now Override

The **Play Now** button forces a playlist to start immediately on a specific display or group, bypassing the normal schedule. It creates a temporary priority-999 schedule.

1. Click **Play Now** on the Schedules page
2. Select the playlist and target display/group
3. Click **Play Now**

A "Play Now Override" entry appears in the Schedule List with a Stop button. Click **Stop** when you want normal scheduling to resume.

### Weekly Timeline View

The **Weekly Timeline** tab shows all active schedules in a 7-day × 24-hour grid. Each schedule appears as a coloured block at the correct time slot. Hover a block to see the schedule name and playlist.

### Now Playing Badges

The Schedule List shows a green "Now Playing" badge next to whichever schedule is currently active on its target. Badges refresh every 30 seconds.

---

## 6. Emergency Broadcast

The Emergency Broadcast system provides an immediate full-screen alert that overrides all content on targeted displays.

### Key Behaviours

- **Instant push:** Displays receive the alert within ~2 seconds via SSE — no polling required
- **Offline lock:** If a display loses network while an emergency is active, the overlay stays on screen and cannot be dismissed until an explicit clear is received from the server
- **Explicit clear only:** Emergencies never auto-expire. They stay locked until an authorised user clears them
- **Audio alert:** A two-tone alert sound plays and repeats every 8 seconds while the emergency is active
- **Active banner:** When a broadcast is live, a red banner appears at the top of the Schedules page. If no broadcast is active the banner is completely hidden

### Alert Levels

| Level | Colour | Use for |
|---|---|---|
| Critical | Red | Immediate danger — evacuate, lockdown, active threat |
| Warning | Amber | Attention required — severe weather, facility alert |
| Info | Blue | Informational — announcement, non-urgent notice |

---

### Saved Templates

Templates let you pre-author common emergency alerts and store them until needed.
In a real emergency, activate with one click instead of typing under pressure.

**Managing templates:**
1. Go to **Schedules ? Emergency** tab
2. Confirm the **Active tenant** in the sidebar matches the organisation you are
   managing (templates and history are per-tenant)
3. The left panel shows all saved templates
4. Click **New Template** to create one — give it an internal name, level, headline, and message
5. Activate any template instantly with its broadcast (antenna) icon

**Template actions:**
| Icon | Action |
|---|---|
| Broadcast icon | Send immediately to all displays |
| Pencil | Edit the template |
| Trash | Delete the template (does not affect past broadcasts) |

---

### Activating an Emergency

**Fastest — direct from a saved template:**
1. Go to **Schedules ? Emergency** tab
2. Find the template in the left panel
3. Click the broadcast icon and confirm

**From the Send Modal:**
1. Click **Emergency Broadcast** at the top of the Schedules page
2. Optionally pick a saved template from the **Load from Saved Template** dropdown and click **Load** to pre-fill the fields
3. Select the **Alert Level**, enter/edit the **Headline** and **Instruction Message**
4. Select the **Target** — All Displays, a specific Group, or a single Display
5. Click **Send Emergency Broadcast**

### Clearing an Emergency

**From the active banner (fastest):**
1. The red "EMERGENCY BROADCAST ACTIVE" banner appears at the top of Schedules when live
2. Click **Cancel Broadcast** and confirm

**From the Emergency tab:**
1. Go to **Schedules ? Emergency** tab
2. Find the live broadcast (red **LIVE** badge) in the right panel
3. Click **Clear** and confirm

All targeted displays return to normal playback within ~2 seconds.

### Broadcast History

The right panel of the Emergency tab shows the full history of all broadcasts:
- Level badge, headline, and message
- Target scope
- Who issued it and when
- Who cleared it and when (cleared records show a grey "Cleared" badge)

Delete individual history records with the trash icon (only available after clearing).

### API / Automation Trigger

Emergencies can be triggered externally via the REST API — including firing a saved template by ID.
See [EMERGENCY_BROADCAST.md](EMERGENCY_BROADCAST.md) for full details and integration examples.

---

## 7. Displays

### Viewing Displays

Go to **Displays** in the sidebar to see all registered displays with their online/offline status, currently playing content, IP address, and last ping time.
The Issues column can show badges such as Offline, No version, Version drift,
Alert active, or Alert snoozed. Version drift means displays in the same group
are reporting different client app versions; mixed Android/browser/desktop
groups may legitimately report different version strings.

### Assigning Content

1. Open a Display detail page (click its name)
2. Under **Default Playlist**, select a playlist
3. Click **Save**

This playlist plays whenever no schedule is active for this display.

### Display Settings

| Setting | Description |
|---|---|
| Name | Friendly name shown in the admin |
| Location | Optional — for your reference |
| Aspect Mode | Default aspect mode for all media on this display |
| Resolution | Expected output resolution |
| Orientation | Landscape or portrait |
| Auto-update client | Allows native clients to install newer client builds. Android silent install requires Device Owner |
| Diagnostics Enabled | Records client diagnostics to the server while troubleshooting |
| Unlock PIN | Opens a keypad on long-press/tap for locked players |
| Volume | Master display volume |
| Allow Input | Whether keyboard/mouse input is passed to the player |
| Show Media Buttons | Show on-screen prev/next/pause controls |

### Display Groups

Group displays together to target schedules and emergency broadcasts at multiple screens at once.
Groups can also use synchronized playback so displays use server time to stay on
the same playlist item. If one display starts late or drifts, it skips to the
current item rather than finishing stale content.

1. Go to **Displays ? Groups**
2. Click **New Group**
3. Name the group and add displays to it
4. Use the group name when creating schedules or sending emergency broadcasts

---

## 8. Tips and Best Practices

### Content

- Keep video files under 200 MB for smooth streaming — compress with HandBrake if needed
- Use H.264 MP4 for maximum compatibility across all display types
- For webpages, test the URL in a non-logged-in browser tab first to make sure it displays without a login wall
- PNG with transparency works for overlay-style images

### Playlists

- Give playlists descriptive names that include the context (e.g. "Lobby Morning Rotation", "Cafeteria Lunch Menu")
- Use display groups and schedules rather than per-display playlists where possible — easier to manage at scale
- Set a fallback default playlist on every display so there is always something playing even when no schedule matches

### Schedules

- Use priority levels consistently: e.g. 10 = base, 20 = time-specific, 30 = special event
- Test a new schedule by setting its date range to today only before rolling it out permanently
- The Weekly Timeline view is the fastest way to spot scheduling conflicts

### Emergency

- Keep emergency headlines short and ALL CAPS — they need to be readable from across a room
- Test the emergency system on a non-production display periodically to make sure it works
- Always clear the emergency explicitly when the event is over — do not rely on the system to auto-clear