# AISignX -- Emergency Broadcast System

Complete reference for the Emergency Broadcast system -- how it works, how to use it,
how to manage saved templates, and how to integrate with external systems via the API.

---

## Table of Contents

1. [Core Principles](#1-core-principles)
2. [Priority Model](#2-priority-model)
3. [Alert Levels](#3-alert-levels)
4. [What Displays Show](#4-what-displays-show)
5. [Saved Templates](#5-saved-templates)
6. [Activating an Emergency (Admin UI)](#6-activating-an-emergency-admin-ui)
7. [Clearing an Emergency (Admin UI)](#7-clearing-an-emergency-admin-ui)
8. [Broadcast History](#8-broadcast-history)
8b. [Tenant scope](#8b-tenant-scope)
9. [REST API](#9-rest-api)
10. [Targeting](#10-targeting)
11. [Offline Behaviour](#11-offline-behaviour)
12. [Audio Alert](#12-audio-alert)
13. [Security and Audit](#13-security-and-audit)
14. [Integration Examples](#14-integration-examples)
15. [Recommendations](#15-recommendations)

---

## 1. Core Principles

1. **Emergency overrides everything, immediately** -- does not compete with schedules
2. **Push, not pull** -- displays receive the alert via SSE within ~2 seconds of activation
3. **Offline lock** -- if a display loses network while an emergency is active, the alert stays on screen until connection is restored and an explicit clear is received
4. **Explicit clear only** -- emergencies never auto-expire; they stay locked until an authorised user clears them
5. **Saved templates** -- pre-author common alerts (fire, weather, lockdown) and activate them with one click or one API call
6. **Fully logged** -- every activation and clearance records who did it, when, and from which source

---

## 2. Priority Model

```
[Emergency Broadcast]   <-- top priority, live SSE push, overrides all layers
        |
[Play Now Override]     <-- priority 999, bypasses normal schedule
        |
[Scheduled Playlists]   <-- normal time/date/day-based scheduling
        |
[Default Playlist]      <-- fallback when no schedule matches
```

Emergency Broadcasts are delivered via a separate SSE push channel, not via the schedule resolver.

---

## 3. Alert Levels

| Level | Display Colour | Icon | Use For |
|---|---|---|---|
| Critical | Red (#b71c1c) | Siren | Immediate danger -- fire, evacuation, lockdown, active threat |
| Warning | Amber (#e65100) | Warning triangle | Attention required -- severe weather, facility closure, safety notice |
| Info | Blue (#1565c0) | Info circle | Non-urgent -- building notice, event information |

Colours are set automatically by level. Override them with the colour pickers if needed.

---

## 4. What Displays Show

When an emergency is active, targeted displays show:

- Full-screen takeover overlay (above all content)
- Level-appropriate icon (siren / triangle / info)
- Headline in very large, distance-readable text (scales with screen size)
- Instruction message below the headline (if provided)
- Authority label and issue timestamp in the footer
- Pulsing border animation
- Two-tone audio alert repeating every 8 seconds

All video and audio content is paused while the emergency is active.

---

## 5. Saved Templates

Saved Templates are pre-authored emergency alerts stored on the server until needed.
They let you compose alerts carefully in advance so that in a real emergency you
activate with one click -- no typing under pressure.

### Managing Templates

1. Go to **Schedules** in the sidebar
2. Click the **Emergency** tab
3. The left panel shows all saved templates

**Creating a template:**
1. Click **New Template**
2. Enter a **Template Name** (internal label, e.g. "Fire Evacuation -- Building A")
3. Select the **Alert Level**
4. Enter the **Headline** (shown large on displays, e.g. "EVACUATE IMMEDIATELY")
5. Optionally enter an **Instruction Message** (e.g. "Use stairwell B. Assembly point: Car Park A.")
6. Adjust colours if needed (auto-set by level)
7. Click **Save Template**

**Activating a template directly:**
- Click the broadcast (antenna) icon next to any template
- A confirmation prompt shows the headline
- Confirm to fire immediately to all displays

**Editing / deleting templates:**
- Pencil icon to edit -- all fields can be updated at any time
- Trash icon to delete -- only deletes the stored preset, does not affect any live broadcast

### Loading a Template into the Send Modal

When you open the **Emergency Broadcast** modal:
1. Use the **Load from Saved Template** dropdown at the top
2. Select a template and click **Load**
3. All fields are pre-filled from the template
4. Edit any field before sending -- common use: change the target to a specific group
5. Click **Send Emergency Broadcast**

---

## 6. Activating an Emergency (Admin UI)

### Method A -- From a Saved Template (fastest)
1. Go to **Schedules -- Emergency** tab
2. Find the template in the left panel
3. Click the broadcast icon and confirm

### Method B -- From the Send Modal
1. Click **Emergency Broadcast** button at the top of the Schedules page
2. Optionally load a saved template, or fill in fields manually
3. Select Alert Level, Headline, Message, Target
4. Click **Send Emergency Broadcast**

The alert appears on all targeted displays within ~2 seconds.

---

## 7. Clearing an Emergency (Admin UI)

**From the top banner (when a broadcast is active):**
1. The red "EMERGENCY BROADCAST ACTIVE" banner appears at the top of the Schedules page
2. Click **Cancel Broadcast** and confirm

**From the Emergency tab:**
1. Go to **Schedules -- Emergency** tab
2. Find the broadcast in the right panel with the red **LIVE** badge
3. Click **Clear** and confirm

All targeted displays return to normal playback within ~2 seconds.

**Note:** The banner only appears when a broadcast is actually live.
If no broadcast is active it is completely hidden.

---

## 8. Broadcast History

The right panel of the Emergency tab shows the full history of all broadcasts:

| Column | Description |
|---|---|
| Level badge | Critical / Warning / Info |
| LIVE badge | Shown only on currently active broadcasts |
| Headline | The alert title sent to displays |
| Target | Which displays received it |
| Issued | Timestamp and username of who activated it |
| Cleared | Timestamp and username of who cleared it |

History is retained indefinitely. Delete individual records using the trash icon
(only available after a broadcast has been cleared -- you cannot delete a live broadcast).

---

## 8b. Tenant scope

Emergency broadcasts and saved templates belong to a single tenant
(`domain_id`). The **Schedules ? Emergency** tab always uses the **Active
tenant** shown in the sidebar (switch tenant there, then reload the page if
templates look wrong).

| Role | Templates & history | Send / clear |
|---|---|---|
| **Superuser** | Any tenant (set Active tenant or pass `domain_id` in API calls) | Any tenant |
| **Tenant admin** | Active tenant only (must hold `domain.admin` or emergency permissions in that tenant) | Same tenant |

API calls accept `domain_id` in the query string or JSON body. If omitted, the
active session tenant is used. Tenant admins receive `403 forbidden` if they
target another tenant.

---

## 9. REST API

All write endpoints require authentication:
`Authorization: Bearer <your-api-token>`

See [ADMIN_GUIDE.md](ADMIN_GUIDE.md) for how to create API tokens.

---

### GET /api/emergency/active -- Public

Returns all currently live emergency broadcasts. Used by display players.

```json
{
    "status": "success",
    "broadcasts": [
        {
            "id": 42,
            "title": "EVACUATE IMMEDIATELY",
            "message": "Use stairwell B. Assembly point: Car Park A.",
            "level": "critical",
            "background_color": "#b71c1c",
            "text_color": "#ffffff",
            "target": "all",
            "is_active": true,
            "created_at": "2025-01-15T09:23:11",
            "cleared_at": null,
            "created_by": "admin",
            "cleared_by": null
        }
    ]
}
```

---

### GET /api/emergency

List all emergency broadcast history. Requires authentication.

---

### POST /api/emergency

Activate an emergency broadcast. Accepts three modes:

**Mode 1 -- Custom broadcast (all fields provided)**
```json
{
    "title":            "EVACUATE IMMEDIATELY",
    "message":          "Use stairwell B. Assemble at Car Park A.",
    "level":            "critical",
    "background_color": "#b71c1c",
    "text_color":       "#ffffff",
    "target":           "all"
}
```

**Mode 2 -- Fire a saved template as-is**
```json
{
    "template_id": 3,
    "target":      "group:2"
}
```

**Mode 3 -- Template as base with field overrides**
```json
{
    "template_id": 3,
    "title":       "EVACUATE -- NORTH WING ONLY",
    "target":      "group:4"
}
```

When `template_id` is supplied, template values are used as defaults.
Any fields also present in the request body override the template values.

**Request fields:**

| Field | Required | Values | Default |
|---|---|---|---|
| `title` | Yes (or via template_id) | String | -- |
| `template_id` | No | Integer | -- |
| `message` | No | String | Template value or blank |
| `level` | No | `critical`, `warning`, `info` | Template value or `critical` |
| `background_color` | No | Hex colour | Auto from level |
| `text_color` | No | Hex colour | `#ffffff` |
| `target` | No | `all`, `display:<id>`, `group:<id>` | `all` |

**Response:**
```json
{
    "status": "success",
    "broadcast": { "id": 42, "title": "EVACUATE IMMEDIATELY", "is_active": true, ... }
}
```

---

### POST /api/emergency/<id>/cancel

Clear a live emergency broadcast.
Sets `is_active=False`, records `cleared_at` and `cleared_by`. Pushes `emergency_clear` to all displays.

---

### DELETE /api/emergency/<id>

Permanently delete an emergency broadcast record.
Returns `400` if the broadcast is still active -- clear it first.

---

### GET /api/emergency/templates

List all saved templates.

```json
{
    "status": "success",
    "templates": [
        {
            "id": 1,
            "name": "Fire Evacuation",
            "title": "EVACUATE IMMEDIATELY",
            "message": "Use stairwell B. Assembly point: Car Park A.",
            "level": "critical",
            "background_color": "#b71c1c",
            "text_color": "#ffffff",
            "created_at": "2025-01-10T08:00:00",
            "created_by": "admin"
        }
    ]
}
```

---

### POST /api/emergency/templates

Create a new saved template.

```json
{
    "name":             "Severe Weather",
    "title":            "SEVERE WEATHER WARNING",
    "message":          "Remain indoors. Avoid windows.",
    "level":            "warning",
    "background_color": "#e65100",
    "text_color":       "#ffffff"
}
```

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Internal label for the template |
| `title` | Yes | Headline shown on displays |
| `message` | No | Instruction message |
| `level` | No | `critical`, `warning`, `info` (default: `critical`) |
| `background_color` | No | Hex colour (auto from level if omitted) |
| `text_color` | No | Hex colour (default: `#ffffff`) |

---

### GET /api/emergency/templates/<id>

Get a single template.

---

### PUT /api/emergency/templates/<id>

Update a template. Supply only the fields to change.

```json
{ "message": "Updated instruction message." }
```

---

### DELETE /api/emergency/templates/<id>

Delete a saved template. Does not affect any broadcasts already sent.

---

### POST /api/emergency/templates/<id>/activate

Convenience endpoint -- activate a saved template directly.
Accepts an optional body to override fields at activation time.

```json
{
    "target":  "group:2",
    "title":   "EVACUATE -- SOUTH WING",
    "message": "Exit via south stairwell only."
}
```

All fields are optional. Omitting them uses the template values.

---

## 10. Targeting

| Value | Who receives the alert |
|---|---|
| `all` | Every registered display |
| `display:5` | Only the display with ID 5 |
| `group:2` | All displays in display group 2 |

Find display and group IDs from the Displays page or via `GET /api/displays`.

---

## 11. Offline Behaviour

If a display loses server connection while an emergency is active:

- The emergency overlay **stays on screen** -- does not disappear
- Audio continues looping
- When the SSE reconnects, the server immediately re-sends the current emergency state
- The display only returns to normal when an explicit `emergency_clear` SSE event is received

This is a safety requirement -- a network dropout during an emergency must not silently return screens to normal content.

---

## 12. Audio Alert

A two-tone synthesised beep (880 Hz then 660 Hz) generated by the Web Audio API:

- Plays immediately when the emergency overlay appears
- Repeats every 8 seconds while active
- Stops immediately when the emergency is cleared
- Requires no external audio files

**Note:** browsers require a user interaction before playing audio.
On kiosk displays (Electron app, or a browser that has been interacted with), this is not an issue.
On a fresh browser tab, audio may be blocked until first interaction.
For fully automated kiosks use the Electron or Android native client.

---

## 13. Security and Audit

- Emergency data is isolated per tenant; cross-tenant access is superuser-only
- Only authenticated users (or valid API tokens) can activate or clear emergencies
- Every activation is server-logged: `Emergency broadcast ACTIVATED id=X title='...' level=... source=... by=username`
- Every clearance is server-logged: `Emergency broadcast CLEARED id=X by=username`
- Every template creation/edit/deletion is server-logged
- The Broadcast History panel in the UI shows the full audit trail
- The `source` field in the log indicates whether the broadcast came from a template (`template:3`) or was custom
- API tokens can be scoped to `emergency:write` only -- no other access needed

---

## 14. Integration Examples

### Python -- fire alarm webhook receiver

```python
import requests

API_URL = "https://signage.example.com"
TOKEN   = "your-api-token"

def activate_emergency(title, message="", level="critical", target="all"):
    r = requests.post(
        f"{API_URL}/api/emergency",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"title": title, "message": message, "level": level, "target": target}
    )
    r.raise_for_status()
    return r.json()["broadcast"]["id"]

def activate_template(template_id, target="all"):
    """Fire a pre-saved template -- no typing required at activation time."""
    r = requests.post(
        f"{API_URL}/api/emergency/templates/{template_id}/activate",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"target": target}
    )
    r.raise_for_status()
    return r.json()["broadcast"]["id"]

def clear_emergency(broadcast_id):
    r = requests.post(
        f"{API_URL}/api/emergency/{broadcast_id}/cancel",
        headers={"Authorization": f"Bearer {TOKEN}"}
    )
    r.raise_for_status()
```

### PowerShell -- building automation

```powershell
$headers = @{
    Authorization  = "Bearer your-api-token"
    "Content-Type" = "application/json"
}

# Custom broadcast
$body = @{
    title   = "BUILDING EVACUATION"
    message = "Exit via nearest stairwell."
    level   = "critical"
    target  = "all"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://signage.example.com/api/emergency" -Method POST -Headers $headers -Body $body

# Fire saved template ID 1
Invoke-RestMethod -Uri "https://signage.example.com/api/emergency/templates/1/activate" -Method POST -Headers $headers -Body '{}'

# Clear broadcast ID 42
Invoke-RestMethod -Uri "https://signage.example.com/api/emergency/42/cancel" -Method POST -Headers $headers
```

### curl

```bash
# Custom broadcast
curl -X POST https://signage.example.com/api/emergency \
  -H "Authorization: Bearer your-api-token" \
  -H "Content-Type: application/json" \
  -d '{"title":"SHELTER IN PLACE","level":"critical","target":"all"}'

# Fire saved template 2 to a specific group
curl -X POST https://signage.example.com/api/emergency/templates/2/activate \
  -H "Authorization: Bearer your-api-token" \
  -H "Content-Type: application/json" \
  -d '{"target":"group:3"}'

# Clear broadcast 42
curl -X POST https://signage.example.com/api/emergency/42/cancel \
  -H "Authorization: Bearer your-api-token"
```

---

## 15. Recommendations

- **Create templates in advance** -- do not compose a fire evacuation alert during a fire
- **Name templates clearly** -- "Fire Evacuation -- All Buildings", "Severe Weather Warning", "IT Outage Notice"
- **Test regularly** -- activate a test emergency on a non-production display at least monthly
- **Short headlines** -- under 40 characters; they display very large on screen
- **Plain language** -- "EVACUATE NOW" not "Emergency evacuation procedure is in effect"
- **Always clear explicitly** -- never rely on any timer or system restart to clear an emergency
- **Use groups** -- target specific areas (Floor 1, Lobby, Cafeteria) rather than always using "all"
- **Scope your API tokens** -- integration tokens should have `emergency:write` scope only