# AISignX — Troubleshooting

Common problems and how to fix them.

---

## Installation

| Problem | Cause | Fix |
|---|---|---|
| `python migration.py` fails with "No such file or directory: migrations" | Running on a totally fresh install without ever initialising | Run `python migration.py` — it handles init automatically. Never run `flask db upgrade` directly |
| `pip install` fails on `psycopg2` | Missing PostgreSQL dev headers | `sudo apt install libpq-dev` then retry |
| `playwright install chromium` hangs | Slow download | Add `--timeout 300` or use a VPN if the CDN is blocked |
| Port 5000 already in use | Another process is using port 5000 | `netstat -tulnp \| grep 5000` then kill the process, or change the port in your startup command |
| `config.py` not found | `generate_config.py` was not run | `python generate_config.py` |

---

## Displays

| Problem | Cause | Fix |
|---|---|---|
| Display shows "No content scheduled" | No playlist assigned and no matching schedule | Assign a default playlist on the Display detail page |
| Display shows blank/black screen | Playlist is empty, or all items have errors | Check the playlist in the admin — look for red error indicators on items |
| Display status stuck on "Offline" | Display has not pinged the server recently | Open the player URL on the device or check if the device has network access |
| "Another client is already connected" | Two devices using the same display token | Close the other browser/device, or regenerate the token on the Display detail page |
| Display not updating after playlist change | SSE connection dropped or service worker caching | Hard-refresh the player page (`Ctrl+Shift+R`), or open the player URL fresh |
| Video has no audio | Browser autoplay policy blocks audio | Ensure the display browser has had a user interaction, or the Electron/Android client is used |
| Player page shows 404 | Invalid or deleted display token | Check the token on the Display detail page and update the URL on the device |
| Display list shows Version drift | Displays in the same group report different app versions | Update the out-of-date client if all devices are the same type. If the group mixes Android/browser/Windows/Linux, different version strings can be normal |
| Client sync starts late or drifts | Client clock/network delay or device sleep | In synchronized groups the player recalibrates against server time and skips to the current item. Reload or restart clients that remain stuck |
| Web client mouse cursor disappears | Player hides the cursor after idle | Move the mouse to show it; it hides again after a short idle period |

---

## Scheduling

| Problem | Cause | Fix |
|---|---|---|
| Wrong playlist playing | Schedule priority conflict | Check the Weekly Timeline — two schedules may overlap. Adjust priorities |
| Schedule not triggering | Wrong days/time set, or schedule is inactive | Open the schedule and verify the time, days, and Active toggle |
| Play Now not working | Playlist not selected, or display not targeted | Make sure you selected both a playlist and a target display/group |
| Schedule shows "Now Playing" badge incorrectly | Clock skew between browser and server | The badge is driven by server time — verify the server system clock is correct |

---

## Emergency Broadcast

| Problem | Cause | Fix |
|---|---|---|
| Emergency banner showing when no broadcast is active | Stale `is_active=True` record in DB | Check `/api/emergency/active` — if it returns records, clear them from the Emergency tab |
| Emergency not showing on displays | Display not connected to SSE | Check the display is online (last ping recent). Reload the player page |
| Emergency still showing after clearing | SSE connection dropped before clear was sent | Reload the player page — on reconnect the server sends the current state (cleared) |
| Emergency showing on wrong displays | Target was "All" when a specific target was intended | Create the emergency again with the correct target; clear the incorrect one |
| No audio on emergency | Browser audio policy / first-load restriction | Use the Electron or Android client for kiosk displays |
| Cannot clear emergency | User does not have admin rights | Ask an administrator to clear it from the Emergency tab |
| Cannot delete broadcast record | Broadcast is still active | Clear it first — the trash icon is disabled on live broadcasts |
| Template not appearing in dropdown | Templates not loaded yet | Open the Emergency modal — templates load when the modal opens; or visit the Emergency tab first |
| Activating template via API returns 404 | Wrong template ID | Confirm the ID with `GET /api/emergency/templates?domain_id=N` for the correct tenant |
| Emergency tab stuck on "Loading…", **+ New Template** does nothing | JavaScript error on Schedules page (e.g. no schedule rows) | Hard-refresh the page; ensure you are on a current build. Open browser devtools console for errors |
| Templates/history empty but wrong tenant | Active tenant mismatch | Switch **Active tenant** in the sidebar, reload **Schedules ? Emergency** |
| `403 forbidden` on emergency API | User lacks permission in that tenant | Use a tenant where you have `emergency.use` / `domain.admin`, or pass the correct `domain_id` as superuser |

---

## Proof of Play

| Problem | Cause | Fix |
|---|---|---|
| Page not in sidebar | User is not a tenant admin / lacks `audit.read` | Assign **domain admin** or a role with `audit.read` in that tenant |
| `403 forbidden` on `/api/proof-of-play` | Wrong tenant or no admin rights | Switch Active tenant; tenant admins cannot use `scope=all` |
| No rows despite playback | Feature disabled | Superuser: enable **Recording enabled** on Proof of Play or set `proof_of_play.enabled` in System Settings |
| Rows from another tenant visible | Should not happen for tenant admins | Report as a bug; superusers may intentionally use **All tenants** filter |

---

## Media

| Problem | Cause | Fix |
|---|---|---|
| Video upload fails | File too large for the server's upload limit | Increase `client_max_body_size` in nginx config (e.g. `500M`) |
| Video thumbnail not generated | FFmpeg not installed or not in PATH | Install FFmpeg and verify `ffmpeg -version` works |
| Uploaded video duration is wrong or missing | FFmpeg/ffprobe could not read metadata | Install FFmpeg, replace/re-upload the video, or manually set duration |
| Need to reset a changed video duration | Duration was manually overridden | Use **Use detected length** on the media/item editor, or **Use video length** from the Media/Playlist bulk bar |
| Webpage thumbnail not generated | Playwright/Chromium not installed | Run `playwright install chromium` |
| Webpage shows blank in player | URL requires login or blocks iframes | Test the URL in an incognito tab. Add `X-Frame-Options: SAMEORIGIN` bypass or use a different URL |
| Image distorted on display | Wrong aspect mode | Change Aspect Mode to `fit` on the display or the playlist item |
| Video clip not playing from correct start | Clip start > video duration | Verify the video length and that Clip Start < video duration |

---

## Performance

| Problem | Cause | Fix |
|---|---|---|
| Server slow with many displays | Too few Gunicorn workers | Increase workers: `gunicorn -w 8 ...` |
| SSE connections dropping frequently | Nginx proxy timeout too short | Set `proxy_read_timeout 3600s` in nginx config |
| Database lock errors (SQLite) | Too many concurrent writes | Switch to PostgreSQL for production use with many displays |
| High memory usage | Many large video files being served | Serve uploads directly from nginx (not through Flask) — see PRODUCTION_DEPLOYMENT.md |

---

## Native Clients

| Problem | Cause | Fix |
|---|---|---|
| Electron build fails — no Node.js | Node.js not installed | Run `install_build_prereqs_windows.ps1` or `install_build_prereqs_linux.sh` |
| Android build fails — JAVA_HOME not set | JDK 17 not configured | Set `JAVA_HOME` to your JDK 17 path. Run the prereqs installer |
| Android build fails — SDK not found | ANDROID_HOME not set | Set `ANDROID_HOME` or run the prereqs installer |
| Client shows "Cannot reach server" | Wrong URL or server not accessible | Verify the server URL is reachable from the device. Check firewall rules |
| Client stuck on "Waiting for Approval" | Admin has not approved the registration | Go to Displays in the admin and approve the pending request |
| Client not updating automatically | Manifest is not newer, update loop has not run yet, or Android is not Device Owner | Confirm `/api/version`, wait for the next check, enable **Auto-update client**, or push **Update Client** on the Display detail page |
| Android client downloads update but asks for approval | Device is not Android Device Owner | Provision the device as Device Owner for fully unattended installs: `adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver` |
| Android client never finds an update | Manifest version is not higher or APK is missing | Confirm `/api/version` shows a higher `clients.android.version` and `static/clients/AISignX-Player.apk` exists |
| Android long-press unlock does not open keypad | Older APK or WebView touch event issue | Install Android client `1.4.6+`; it includes a native WebView long-click fallback |
| Android shows white video placeholder between videos | Android WebView native video surface placeholder | Use Android client/player assets `v24+`; if it persists, prefer cut transitions for video-heavy Android playlists |
| Electron client starts in a window then snaps fullscreen | Old build before kiosk-from-launch (pre-1.0.5) | Update to 1.0.5+ via the Downloads page |
| Electron client starts minimized after an update | Old wrapper logic (pre-1.0.15) | Update to 1.0.15+ — the scheduled-task launcher is reliable |
| Downloads page buttons all dimmed (disabled) | `client_versions.json` failed to parse — usually a UTF-8 BOM added by PowerShell `Set-Content` or Notepad | Use the Manifest Editor on the Downloads page (it writes BOM-free). Or strip the BOM: `(Get-Content path -Raw).TrimStart([char]0xFEFF) \| Set-Content -Encoding utf8` |
| **Update Client** says "display is not currently online" | The display's SSE connection is not active | Wait for the display to reconnect (you'll see it go green on the Displays list), then retry. Push commands are not queued — they only deliver to live connections |
| **Update Client** runs but the new app never comes back | Older client without scheduled-task launcher (pre-1.0.15), OR `static/clients/AISignX-Player-Setup.exe` is missing/corrupt | First confirm the installer is present and runs cleanly when double-clicked. Then upgrade the display to 1.0.15+ manually one time; future pushes will work |
| **Update Client** appears to crash the player | Same as above (older wrapper that exited before installer took over) | Same fix — manually install 1.0.15+ once, then all future pushes are clean |
| Need to escape the Electron kiosk on a deployed display | No window chrome / DevTools by default | Press **Ctrl+Alt+Q** (force quit), or **Ctrl+Alt+D** to open DevTools |
| Need to read a deployed display's log without a keyboard at the device | n/a | Push **Reboot App** to restart cleanly. For deeper diagnostics ask whoever is on-site to press **Ctrl+Alt+L** (opens the log in Notepad) |
| Windows installer asks for admin / shows UAC prompt | Old per-machine build (pre-1.0.4) | Upgrade to 1.0.4+ which is per-user (`perMachine: false`) — installs to `%LOCALAPPDATA%`, no UAC. Uninstall the old version once before installing the new |
| Update fails with "failed to uninstall old application files" | App still has files locked when installer runs | Should not happen on 1.0.6+. If it does, the wrapper log will show details — paste contents to support |

---

## Service Worker / Caching

| Problem | Cause | Fix |
|---|---|---|
| Display showing old content after server update | Service worker is caching old assets | Hard-refresh (`Ctrl+Shift+R`). Or in DevTools ? Application ? Service Workers ? Unregister, then reload |
| Service worker not registering | Served over HTTP not HTTPS in production | Service workers require HTTPS. Set up SSL or use `localhost` for testing |
| Offline banner showing when server is up | Short network blip triggered offline mode | Banner auto-hides when SSE reconnects. No action needed |
| Android does not show latest player JS after reload | WebView cached the old player page/assets | Fully close/reopen the Android app, or push Reboot App after the server cache version is bumped |

---

## Logs

Check server logs first when diagnosing any issue:

**Linux (systemd):**
```bash
journalctl -u aisignx -n 100 --no-pager
journalctl -u aisignx -f        # live tail
```

**Windows / development:**
```bash
python app.py    # logs print to console
```

**nginx error log:**
```bash
sudo tail -n 100 /var/log/nginx/error.log
```

Key log messages to look for:

| Log message | Meaning |
|---|---|
| `SSE connection opened: display=...` | A display connected successfully |
| `SSE connection closed: display=...` | A display disconnected |
| `SSE emergency pushed on connect` | Display received emergency on reconnect |
| `SSE emergency pushed to display=...` | Emergency sent to display |
| `SSE emergency cleared for display=...` | Emergency cleared on display |
| `Emergency broadcast ACTIVATED` | Emergency was created — includes username |
| `Emergency broadcast CLEARED` | Emergency was cleared — includes username |
| `SSE reload pushed` | Playlist update sent to display |
| `SSE version check error` | Error in the SSE loop — check for DB issues |

---

## Getting More Help

1. Check this troubleshooting guide
2. Check the server logs (see above)
3. Review the relevant documentation in the `docs/` folder
4. Open an issue on the project repository with the log output and a description of the problem