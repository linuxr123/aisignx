# AISignX — Browser Access

How to connect any browser to AISignX as a display — no software installation required.

---

## Overview

Any modern browser can act as an AISignX display. There are two ways to connect:

| Method | Best for |
|---|---|
| **Self-registration** (`/request-access`) | End users setting up a new display themselves |
| **Direct player URL** | Admin opening a pre-configured display directly |

---

## Method 1: Self-Registration

Self-registration lets someone set up a browser display without needing admin access. The browser requests access, an admin approves it, and the browser automatically loads the player.

### Step 1 — Open the Registration Page

Navigate to:
```
https://your-server/request-access
```

No login is required. Share this URL with anyone setting up a display.

**Admin shortcut:** the **Browser Access Link** button in the sidebar opens this URL in a new tab.

### Step 2 — Submit the Request

1. Enter a **Display Name** (e.g. "Reception Desk", "Conference Room B")
2. Enter a **Location** (optional)
3. Click **Request Access**

The page switches to a waiting screen: "Waiting for admin approval..."

### Step 3 — Admin Approves

1. In the AISignX admin, go to **Displays**
2. Pending requests appear at the top with a yellow badge
3. Review the display name, location, and IP address
4. Click **Approve**

The browser detects approval within **5 seconds** and automatically loads the player. No page refresh needed.

To decline, click **Decline** — the browser shows "Access Declined".

### Step 4 — Player Loads

The browser now shows the player in fullscreen-ready mode. Press **F11** (Windows/Linux) or use the browser's fullscreen option to go full screen.

---

## Method 2: Direct Player URL

If an admin has already created a display, copy its player URL from the Display detail page and open it directly on the device:

```
https://your-server/display/<token>
```

No registration step needed. The player starts immediately.

---

## Going Fullscreen / Kiosk Mode

For a clean kiosk experience, run the browser fullscreen so no browser chrome is visible.

| Platform | How to go fullscreen |
|---|---|
| Chrome / Edge (Windows/Linux) | Press **F11** |
| Chrome / Edge (kiosk, permanent) | Launch with `--kiosk https://your-server/display/<token>` |
| Firefox | Press **F11** |
| macOS Chrome | View ? Enter Full Screen (or Ctrl+Cmd+F) |
| iPad / iPhone | Add to Home Screen ? tap icon ? opens fullscreen |
| Android Chrome | Tap the three-dot menu ? Add to Home Screen, or use the browser fullscreen button |

### Chrome Kiosk Launch (Windows)

Create a shortcut with the target:
```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --noerrdialogs --disable-infobars https://your-server/display/<token>
```

This opens Chrome in true kiosk mode — no address bar, no tabs, no close button. Press Alt+F4 to exit.

### Chrome Kiosk Auto-Start (Windows)

Add the shortcut to `shell:startup` (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) to auto-launch on boot.

---

## Offline Playback

The player uses a **service worker** to cache content for offline playback:

- All static assets (CSS, JS, fonts) are cached on first load
- All media files from `/uploads/` are cached as they play
- Recent player page and playlist payloads are cached for fallback
- If the server goes offline, the display continues playing from cache
- When the server comes back, the display reconnects and picks up playlist/settings changes automatically

**Note:** Service workers require **HTTPS** (or `localhost`). If you are serving AISignX over plain HTTP in production, offline caching will not work. Set up SSL — see [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md).

In synchronized display groups, the browser player uses server-anchored time to
stay aligned with other clients. If it starts late or wakes from sleep, it skips
to the item that should currently be playing.

The player cursor stays visible while the mouse is active and hides after a
short idle delay.

---

## Browser vs. Native Client

| Feature | Browser | Electron / Android |
|---|---|---|
| Installation required | No | Yes |
| True kiosk lock | Needs OS/browser config | Built-in |
| Auto-start on boot | Needs OS shortcut setup | Built-in |
| Crash recovery | Depends on OS | Built-in watchdog |
| Offline playback | Yes (service worker) | Yes |
| Emergency broadcast | Yes | Yes |
| Recommended for permanent installs | No | Yes |
| Recommended for testing / temp displays | Yes | No |

For permanent digital signage installations, the **Electron** (Windows/Linux) or **Android** native client is recommended. See [CLIENTS.md](CLIENTS.md).

---

## Comparison: Self-Registration vs. Manual Token

| | Self-Registration | Manual (Admin creates display) |
|---|---|---|
| Admin pre-creates display | No | Yes |
| User enters display name | Yes | Admin enters it |
| Requires login | No | Admin only |
| Works on any device | Yes | Yes |
| Auto-redirects to player on approval | Yes | Manual URL copy |
| Best for | Distributed rollouts | Admin-managed installs |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `/request-access` page not loading | Check that the server is reachable and HTTPS is configured |
| "Waiting for approval" stuck | Ask admin to check Displays ? Pending in the admin |
| Player loads but shows blank content | No default playlist assigned — admin should assign one on the Display detail page |
| Browser shows old content | Hard-refresh: Ctrl+Shift+R. Or DevTools ? Application ? Service Workers ? Unregister ? reload |
| Cursor disappears | Move the mouse. The player hides it after idle for kiosk cleanliness |
| F11 not going truly fullscreen | Use the `--kiosk` Chrome launch flag for permanent installs |
| Audio not playing on emergency | Browser requires a user interaction before playing audio. Use the Electron client for kiosk installs |