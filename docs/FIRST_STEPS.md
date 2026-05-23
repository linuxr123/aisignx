# First steps after install

You already ran the [one-minute quick start](../README.md#one-minute-quick-start). This guide gets **one display showing content** — no demo database required.

---

## What first boot creates

On the first server start, AISignX automatically:

| Item | Default |
|------|---------|
| Admin user | `admin` / `Admin123!` (change immediately) |
| Tenant | **Default** (`slug=default`) |
| Roles | `domain_admin`, `content_editor`, `display_operator`, `viewer` |
| System settings | Auto-detected caps, signing key, timezone |

There is **no bundled sample media** yet — upload a few files or use a public image URL to learn the UI.

---

## 5-step walkthrough

### 1. Secure the server

1. Log in at `http://localhost:5000` (or your HTTPS URL).
2. Open **Profile** → change the admin password.
3. Optional: create additional users under **Users** with appropriate roles.

### 2. Add media

1. Go to **Media** → **Add Media**.
2. Upload a **PNG/JPG** or **MP4**, or add a **webpage** URL (`http://` or `https://` intranet pages work in kiosk clients).
3. Confirm thumbnails appear (FFmpeg required for video).

### 3. Create a playlist

1. **Playlists** → **New Playlist**.
2. Open the playlist → **Add items** → pick your media.
3. Set **duration** per item (e.g. 10 seconds for images).
4. Save. Optional: set a **default transition** (fade, crossfade, etc.).

### 4. Register a display

**Option A — Browser (fastest)**

1. **Displays** → **Add New Display** (or use an existing row).
2. Copy the player URL: `/display/<api_token>`.
3. Open that URL in a full-screen browser on the target screen.

**Option B — Native client**

1. Build or install Electron/Android from [clients/README.md](../clients/README.md).
2. Enter server URL (`http://host:5000` or `https://your-host`).
3. Complete enrollment; approve the display in **Displays** if pending.

### 5. Assign content

1. Open the **Display** detail page.
2. Choose your **playlist** (and optional schedule later).
3. The player should load within one refresh cycle; SSE pushes updates live.

---

## Optional next steps

| Task | Guide |
|------|--------|
| Time-based schedules | [SCHEDULES.md](../server/docs/SCHEDULES.md) |
| Display groups | [DISPLAYS.md](../server/docs/DISPLAYS.md) |
| Emergency override | [EMERGENCY_BROADCAST.md](../server/docs/EMERGENCY_BROADCAST.md) |
| HTTPS in production | [generate_config.py](../server/generate_config.py), [PRODUCTION_DEPLOYMENT.md](../server/docs/PRODUCTION_DEPLOYMENT.md) |
| Second tenant | [ADMIN_GUIDE.md](../server/docs/ADMIN_GUIDE.md), [MULTI_TENANCY.md](../server/docs/MULTI_TENANCY.md) |

---

## Troubleshooting

- **Blank player** — confirm display is approved, playlist has items, check browser console.
- **Video no duration** — install FFmpeg and ensure it is on `PATH`.
- **HTTPS login issues** — run `python generate_config.py --mode https` and verify proxy sends `X-Forwarded-Proto`.

More: [TROUBLESHOOTING.md](../server/docs/TROUBLESHOOTING.md)
