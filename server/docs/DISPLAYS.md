# AISignX — Displays

Complete reference for registering, configuring, and managing display devices.

---

## Table of Contents

1. [How Displays Work](#1-how-displays-work)
2. [Adding a Display Manually](#2-adding-a-display-manually)
3. [Display Detail Page](#3-display-detail-page)
4. [Display Settings](#4-display-settings)
5. [Online / Offline Status](#5-online--offline-status)
6. [Token Management](#6-token-management)
7. [Display Groups](#7-display-groups)
8. [Pending Registrations](#8-pending-registrations)
9. [Assigning Content](#9-assigning-content)
10. [Display Types](#10-display-types)
11. [Display List Health Badges](#11-display-list-health-badges)
12. [Diagnostics](#12-diagnostics)

---

## 1. How Displays Work

Every display device — whether a browser, an Electron kiosk, or an Android device — connects to the AISignX server by opening a player URL:

```
https://your-server/display/<token>
```

The `<token>` is a unique secret that identifies the display. The server uses it to:
- Look up which playlist and schedule apply
- Push live updates via SSE (playlist changes, settings, emergency broadcasts)
- Track online/offline status

Displays poll the server on each slide transition to check for playlist changes. Emergency broadcasts are pushed immediately without waiting for a transition.

---

## 2. Adding a Display Manually

1. Go to **Displays** in the sidebar
2. Click **Add New Display**
3. Fill in:

| Field | Description |
|---|---|
| Name | Friendly name (e.g. "Lobby TV", "Floor 2 East") |
| Location | Optional — physical location for your reference |

4. Click **Save**
5. Open the Display detail page — copy the **Player URL** and open it on the display device

Alternatively, let the client self-register — see [Section 8](#8-pending-registrations) and [BROWSER_ACCESS.md](BROWSER_ACCESS.md).

---

## 3. Display Detail Page

Open any display by clicking its name on the Displays list.

### Status Bar

| Item | Description |
|---|---|
| Status badge | Online (green) / Offline (grey) — updates every 30 seconds |
| Last Seen | Timestamp of the most recent ping from this display |
| IP Address | Last known IP of the display device |
| Now Playing | Current playlist and schedule name that resolved for this display |

### Player URL

```
https://your-server/display/<token>
```

Open this on the display device. It works in any modern browser with no login required — authentication is via the token in the URL.

### API Token

The raw token value. Used by native clients (Electron, Android) to identify the display. Treat it as a secret — do not share it publicly.

### Quick Actions

| Button | Action |
|---|---|
| Copy Player URL | Copies the full player URL to clipboard |
| Open Player | Opens the player in a new tab |
| **Reload** | Tells the connected display to reload its content. Works on every client (browser, Electron, Android). Useful when a display gets stuck or you want it to immediately pick up a config change. |
| **Reboot App** | Tells the connected display to fully restart its player application. Electron and Android restart natively; browser clients fall back to a page reload. |
| **Update Client** | Tells the native client to download and install the latest version from `/static/clients/`. Electron Windows can update silently. Android can update silently only when provisioned as Device Owner; otherwise Android may show an install approval prompt. See [CLIENTS.md ? Display Push Commands](CLIENTS.md#display-push-commands) for the full flow. |
| Regenerate Token | Issues a new token — old token stops working immediately |
| Delete Display | Permanently removes the display and all its schedule assignments |

Push commands are delivered instantly over SSE. If the display is offline at click time, you'll see a popup that the display is not connected — the command is discarded (no replay queue). Wait for the display to reconnect, then try again.

---

## 4. Display Settings

| Setting | Options | Description |
|---|---|---|
| Name | Text | Friendly name shown in the admin |
| Location | Text | Physical location — for reference only |
| Default Playlist | Select | Plays when no schedule is active |
| Aspect Mode | fit / fill / stretch | Default for all media on this display |
| Resolution | Text | Expected output resolution (e.g. 1920x1080) — for reference |
| Orientation | Landscape / Portrait | For reference; rotated displays need OS-level rotation |
| Allow Input | Toggle | Pass keyboard/mouse events to the player page |
| Show Media Buttons | Toggle | Show on-screen previous/next/pause controls on the player |
| Show Offline Banner | Toggle | Shows a banner when the player is using cached content because the server/network is unavailable |
| Auto-update client | Toggle | Allows native clients to automatically check for and install newer client packages. Android fully silent install requires Device Owner |
| Diagnostics Enabled | Toggle | Allows the display player to stream client-side diagnostic events and console errors back to the server |
| Unlock PIN | 4-8 digits | Long-press/tap unlock keypad for locked players. Android supports native long-press fallback |
| Volume | 0-100 | Master display video volume after item/media audio rules are applied |

### Aspect Mode Behaviour

| Mode | What happens |
|---|---|
| `fit` | Entire media visible; letterbox or pillarbox bars fill remaining space |
| `fill` | Media scales to fill the full screen; edges may be cropped |
| `stretch` | Media stretched to exact screen dimensions; may distort |

The display-level aspect mode is the default. Individual playlist items can override it per-item.

---

## 5. Online / Offline Status

A display is marked **Online** if it has pinged the server within the last **2 minutes**.

- The Displays list refreshes status every 30 seconds automatically
- A display pings on every slide transition (typically every 5–30 seconds depending on playlist duration)
- If a display is permanently offline, check network connectivity and that the player URL is open on the device
- Native clients (Electron/Android) ping more aggressively — they also reconnect the SSE within 5 seconds of a drop

### Offline Does Not Clear Emergencies

If a display goes offline while an emergency broadcast is active, the emergency overlay stays on screen. When the display reconnects, the server immediately re-sends the current emergency state via SSE.

---

## 6. Token Management

Each display has a unique player token. The token is:
- Embedded in the player URL
- Used by native clients to identify themselves to the server
- Sufficient to play content — no other login is required

### Regenerating a Token

If a token is exposed or a device is decommissioned:

1. Open the Display detail page
2. Click **Regenerate Token**
3. Click **Confirm**

The old token stops working **immediately**. Update the player URL or native client configuration on the device with the new token.

**Native client token update:**
- Electron: update the server URL setting in the client settings screen (includes the token)
- Android: re-enter the server URL in the app settings

---

## 7. Display Groups

Groups let you target schedules and emergency broadcasts at multiple displays simultaneously.
Groups can also enable synchronized playback. In synchronized groups, clients
use server-anchored wall-clock time to decide which playlist item should be on
screen. If one client drifts, suspends, or starts late, it skips forward to the
current item instead of slowly falling behind.

### Creating a Group

1. Go to **Displays ? Groups** in the sidebar
2. Click **New Group**
3. Enter a name and optional description
4. Select the displays to add from the multi-select list
5. Click **Save**

### Rules

- A display can belong to **one group at a time**
- Moving a display to a new group automatically removes it from its old group
- Deleting a group does not delete the displays — they become ungrouped
- Groups appear in the schedule target selector and emergency broadcast target selector
- Version drift badges are computed within each group. Mixed client families
  (Android, browser, Windows, Linux) may legitimately report different version
  strings, so compare versions within the same client type before assuming a
  device is out of date.

### Recommended Group Structure

Organise groups by physical area or purpose:

| Group | Displays |
|---|---|
| Lobby | Lobby Entry, Lobby East, Lobby West |
| Cafeteria | Caf North, Caf South |
| Floor 1 | Corridor 1A, Corridor 1B, Lift Lobby 1 |
| All Hands | (all displays) — use "All Displays" target instead |

---

## 8. Pending Registrations

When a native client (Electron or Android) or a browser using `/request-access` connects for the first time, it appears in **Displays ? Pending** with a status of "Pending Approval".

### What the pending record shows

| Field | Description |
|---|---|
| Requested Name | Name the user or device entered |
| Hostname | Device hostname |
| OS | Operating system reported by the client |
| IP Address | IP the request came from |
| Requested At | Timestamp |

### Approving a Registration

1. Go to **Displays** — pending registrations appear at the top with a yellow badge
2. Review the device details to confirm it is a legitimate display
3. Click **Approve**
4. The display is added and the device auto-loads the player within 5 seconds
5. Optionally rename or move the display to a group after approval

### Declining a Registration

Click **Decline** — the device shows a "Registration Declined" message. The pending record is retained for auditing and can be deleted manually.

### Security Note

The `/request-access` page requires no login — anyone who can reach your server URL can submit a registration request. Requests do nothing until an admin approves them. Consider restricting network access to the server if this is a concern.

---

## 9. Assigning Content

### Default Playlist

The default playlist plays on a display whenever no schedule is active.

1. Open the Display detail page
2. Select a playlist from the **Default Playlist** dropdown
3. Click **Save**

A display with no default playlist and no matching schedule shows "No content scheduled".

### Via Schedules

For time-based, day-based, or event-based playback, use schedules instead of the default playlist:

- See [SCHEDULES.md](SCHEDULES.md) for the full scheduling reference
- See [USER_GUIDE.md](USER_GUIDE.md) for step-by-step schedule creation

### Live Content Push

When a playlist assigned to a display is modified, the change is pushed live via SSE. The display picks it up on the next slide transition — the current slide is not interrupted.

---

## 10. Display Types

AISignX supports three types of display clients:

### Browser

Any modern browser opened to the player URL. No installation required.

| Pros | Cons |
|---|---|
| No install needed | Not kiosk-locked by default |
| Works on any device | May show browser chrome unless fullscreen |
| Easiest to set up | No auto-start on reboot |

Use the browser client for quick testing or temporary displays. For permanent installations use a native client or configure the browser in kiosk mode.

**Kiosk tips:**
- Chrome/Edge: press F11, or launch with `--kiosk` flag
- iPad: Add to Home Screen for fullscreen launch
- See [BROWSER_ACCESS.md](BROWSER_ACCESS.md) for full browser setup guide

### Electron (Windows / Linux)

A purpose-built desktop app that opens in a fullscreen, borderless, kiosk window.

| Pros | Cons |
|---|---|
| True kiosk — no OS chrome | Requires installation |
| Auto-starts on system boot | Larger download |
| Handles crash recovery | Windows/Linux only |
| Blocks keyboard shortcuts | Requires Node.js to build |

See [CLIENTS.md](CLIENTS.md) for build and install instructions. Download pre-built packages from the **Downloads** page.

### Android

Native Android app that runs in kiosk mode.

| Pros | Cons |
|---|---|
| Runs on low-cost Android hardware | Requires APK sideload |
| Battery-efficient | Android 8.0+ required |
| Kiosk mode via screen pinning | |
| Works on commercial Android signage panels | |

See [CLIENTS.md](CLIENTS.md) for build and install instructions. Download from the **Downloads** page.

---

## 11. Display List Health Badges

The Displays list polls `/api/displays/issues` and shows lightweight issue
badges without a full page reload.

| Badge | Meaning | Notes |
|---|---|---|
| Offline | Display has not pinged within the online window | Check network, power, and whether the player page/app is running |
| No version | Display is online but has not reported `app_version` | Usually an older client or a player still starting |
| Version drift | Displays in the same group report more than one non-empty app version | Most useful for same-client groups. Mixed Android/browser/Electron groups often have different version schemes |
| Alert active | Offline alerting currently has this display in an active outage | See Admin -> Alerts |
| Alert snoozed | Offline alert notifications are snoozed for this display/group | Clear snooze if alerts should resume |

---

## 12. Diagnostics

Diagnostics are opt-in per display. Enable **Diagnostics Enabled** on the
display detail edit form when you need deeper troubleshooting.

When enabled, the player records selected client events to the server:
- Console errors and warnings
- Sync calibration and drift recovery events
- Offline/network state transitions
- Player runtime events useful for diagnosing stuck media

Viewing diagnostics:
- Open a display detail page for that display's recent diagnostic log.
- Superusers can use **Administration -> Display Diagnostics** for a central
  cross-tenant view.
- Filters include search text, tenant, display, group, severity level, and
  result limit.

Diagnostics are for troubleshooting and should be left off unless needed.