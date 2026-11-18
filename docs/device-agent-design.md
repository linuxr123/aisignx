# Device agent and watchdog: design

Status: **design, mostly built** (see [Status of the phases](#status-of-the-phases) and
[Decisions taken](#decisions-taken)). Written 2026-11-15 from a read-only review of the Windows app, the Android app and
the server. The text below is the original design and is kept as written; where the build differs, the two sections at
the end say how. Platform scope: Windows and Android supported, Linux best effort and not built, macOS dropped. Nothing
has run on hardware other than the owner's Samsung Galaxy tablet and a Shield.

## Goal

Keep screens running without someone walking over to them, and let an admin fix a misbehaving
screen from the display page:

- **Watchdog:** restart the player when it has crashed or hung.
- **Restart** the player on request.
- **Collect logs** from a screen without visiting it.
- Later: **reboot** the device, **install signed updates** of the app, and help set up a **Windows
  kiosk**.

All of it is controlled per display from **Displays > display > Edit**, **off by default**, and every
action is audited.

## What already exists

| Area | Today |
|---|---|
| Server | Display tokens (hash stored, revocable). SSE events with commands `reload`, `identify`, `screenshot` (editor), `unlock`, `lock` (admin). Heartbeat with a 4 KB whitelisted `diag`. The screenshot request (one request id, a time limit, claimed once) is the pattern to copy. Audit log. |
| Windows app | In-page crash and hang watchdogs (reload, relaunch after 5 crashes in 10 min), 2 MB log, per-user login item, single-instance lock, kiosk and Lock player features, a command parser that only accepts `unlock` and `lock`. |
| Android app | Boot receiver (mostly refused on Android 10+), Home-alias mode, WebView crash recovery, one liveness check after the page loads, 1 MB x 2 log, Device Owner receiver (set up with adb), the same strict `unlock`/`lock` parser. |
| Missing | Any supervisor outside the player process; restart, reboot, update and collect-logs commands; a durable command queue with acknowledgements; a release model; a log upload endpoint; per-display agent settings. |

## Principles

1. **Two tiers of command.**
   - **Tier A** (restart player, collect logs, identify, screenshot): display token over TLS, admin
     or editor role, audited. First release.
   - **Tier B** (reboot, update, kiosk): additionally signed (Ed25519 envelope over display id,
     command id, sequence, action, parameters, expiry) and verified **on the device**. **None of
     these ships until signing exists.**
2. **A closed list of verbs with typed parameters.** Never a command line, a path or a URL to run.
3. **Dangerous commands never travel through the web page.** The page can show content from the
   internet; if a command could be injected there, so could a reboot. The native app fetches its own
   commands over its own pinned connection. The page-forwarded commands stay `unlock` and `lock`.
4. **The database row is the truth; SSE is only a wake-up.** Commands are stored, expire (judged by
   server time, because cheap devices have wrong clocks), are acknowledged, and run **at most
   once** (the device records the command id before acting).
5. **Rate limits and loop guards**: one open destructive command per display; the watchdog restarts
   the player at most N times per hour and then reports `gave_up` and raises an alert; reboots at most
   once per 10 minutes and 3 per hour.
6. **A stolen display token impersonates only that display.** New endpoints are scoped to the calling
   display and give no ability to issue commands.
7. **Honest capability reporting.** Each client says what it can do; the admin page hides what it
   cannot, and an unsupported command is refused with a clear message, never "queued".

## Data model (server)

- `displays`: agent settings (all default off): `agent_watchdog`, `agent_allow_restart`,
  `agent_allow_logs` (later `agent_allow_reboot`, `agent_update_channel`).
- `device_commands`: `id`, `display_id`, `seq`, `action` (restart_player, collect_logs, ...),
  `params` (validated JSON), `created_by`, `created_at`, `expires_at`, `state`
  (pending, delivered, done, failed, expired, cancelled), `result`, `acked_at`.
- `device_logs`: `id`, `display_id`, `command_id`, size, stored file, `created_at` (short retention,
  size cap per display).
- Heartbeat `diag.agent`: client version and build, privilege tier (`plain`, `device_owner`,
  `admin`), booleans `can_restart`, `can_collect_logs`, `can_reboot`, `can_update`, last watchdog
  actions.
- Endpoints: `GET /commands` (pending for this display), `POST /commands/<id>/ack`,
  `POST /logs` (request-bound: only accepted for an open `collect_logs` command).

## Watchdog design

### Windows (Electron)

The app cannot watch itself, so a **second, tiny process** does:

- The app writes a **heartbeat file** only after an internal probe proves the player page is alive
  (the page answers a ping and has rendered recently).
- A **current-user scheduled task** (no administrator needed, no service) runs every minute: if the
  app is not running, or the heartbeat file is older than ~90 seconds, it restarts the app (and
  respects the same hourly cap).
- Log rotation (keep the previous file), crash reasons in the log.
- *Limit:* it protects against crashes and hangs, not against a local user who kills it or edits the
  task. A reboot only recovers if Windows logs the user in again (auto-logon is a manual setting).

### Android

- The page sends a periodic **"alive"** message to the app; a native hang detector escalates:
  reload page, recreate the WebView, recreate the activity, restart the process. At most once per
  hour for the heavy steps, and aware of being offline.
- An uncaught-exception handler, the previous exit reason (`ApplicationExitInfo`) in the log, and the
  extra boot receivers (locked boot, quick boot, package replaced).
- *Limit:* without **Device Owner** or **Home mode**, Android may refuse to start the app from the
  background (Android 10+). The honest promise on a plain device is "the player stays alive while its
  process lives". A user's **Force stop** cannot be undone by the app.

## Commands in the first release

- **`restart_player`**: restarts the player process through the local watchdog path; acked and audited.
- **`collect_logs`**: the device compresses its log (and crash information) and uploads it **only
  for an open request** (same one-shot pattern as screenshots), about 1 MB compressed at most,
  redacted (never the config, PIN hash, certificate pin, cookies or media cache). Admins download it
  from the display page. Short retention. The server never decompresses it.

## Platform limits (to state in the product)

| | Windows | Android |
|---|---|---|
| Restart on crash or hang | Yes, within about a minute (scheduled task) | Yes while the process lives; reliable relaunch only in Home mode or as Device Owner |
| Collect logs | Yes | Yes |
| Reboot | Not in the first release. Later: `shutdown /r` after a self-test, or an optional signed service | Only as **Device Owner** |
| Silent update | Only with a **code-signing certificate** and a verified update path | Only as **Device Owner**; otherwise the system installer asks the user |
| Rollback of a bad update | Re-run the previous installer | Not possible on the device |
| Kiosk setup | Documented checklist; later a local script with undo. Shell replacement needs Windows Enterprise/IoT, Assigned Access needs Pro | Lock task mode, full lockdown as Device Owner |
| Unsigned installer | SmartScreen and antivirus warnings | n/a |

Nothing here has been run on hardware yet; every item gets a manual test checklist.

## Phases

1. **Server foundation (S):** agent settings, the capability fields in `diag.agent`, audit actions,
   admin UI that hides what a device cannot do, capability matrix in the docs.
2. **Durable commands (M):** `device_commands`, delivery in heartbeat/SSE, ack endpoint, expiry job, tests.
3. **Watchdogs (M, one platform at a time):** Windows heartbeat file + scheduled task + log rotation;
   Android alive message, escalation ladder, crash handler, exit reasons, boot receivers.
4. **`restart_player` (S)** end to end on both clients.
5. **`collect_logs` (M):** endpoint, storage, retention, admin download, both clients.
   **-- first release ends here --**
6. **Command signing (M):** key made at setup, public key pinned at pairing (fingerprint shown to the
   admin), verification on the device, replay protection.
7. **Reboot (M):** Android Device Owner `reboot`; Windows `shutdown /r` behind a self-test; rate limits.
8. **Updates (L):** release catalog, vendor offline signing key (public key built into the apps), signed
   manifests, canary-first rollout with a health gate; Android Device Owner silent install first,
   Windows updater only after the certificate decision.
9. **Kiosk helpers (M):** a Windows script with undo; Android Device Owner hardening command.
10. **Optional (L):** a signed per-machine Windows service; display token rotation.

## Decisions for the owner (needed from phase 6 on, not before)

| Question | Recommendation |
|---|---|
| Buy a Windows code-signing certificate? | Yes before any Windows auto-update. Until then no Windows auto-update ships. |
| Is auto-update on by default, and who is the trust root? | Off by default, admin-triggered, one canary display first. A vendor-held **offline** Ed25519 release key, public key built into the apps (current + next for rotation), never stored on the customer's server. |
| Support Android Device Owner as the "managed" tier? | Yes: adb setup documented, capabilities reported per device; QR provisioning later only if asked. |
| Ship a Windows SYSTEM service? | No for the first release (scheduled task only). |
| Command signing key | Server-held operator key, public key pinned at pairing with the fingerprint shown to the admin. It protects against network attackers and stolen display tokens, not against a fully compromised server. |
| Windows kiosk helper | Documentation now, a local undoable script later, **never** a remote "set the password / replace the shell" command. |

## Status of the phases

| Phase | State |
|---|---|
| 1 Server foundation | Built: agent switches per display (`agent_watchdog`, `agent_allow_restart`, `agent_allow_logs`, `agent_allow_reboot`, `agent_allow_startup`, `agent_allow_update`), capability fields in `diag.agent`, audit actions, UI that disables what a device cannot do. `agent_update_channel` was not built. |
| 2 Durable commands | Built: `device_commands`, delivery with the heartbeat and `GET /commands`, ack, expiry job (30 days of history). Max 10 commands per display per hour. |
| 3 Watchdogs | Built for both clients (Windows scheduled task `AiSignXWatchdog`; Android escalation ladder). Never run on a PC; the Android part ran on the tablet. |
| 4 `restart_player` | Built on both. |
| 5 `collect_logs` | Built on both. Uploads are stored as files (at most 1 MB, 5 per display, 7 days), not in a `device_logs` table. |
| 6 Command signing | Built (Ed25519, see [signed-commands.md](signed-commands.md)). Differs from the plan, see below. |
| 7 Reboot | Built: Android Device Owner `reboot`; Windows `shutdown /r /t 30` (in the app, or through the privileged agent). Rate limits as designed. |
| 8 Updates | Partly built. Built: `update_app` with a signed size and SHA-256, silent install (Android Device Owner, Windows), the Downloads page as the source, **Update available** on the display page, rollback in the privileged agent. Not built: release catalog, vendor offline release key, canary rollout, health gate across displays. |
| 9 Kiosk helpers | Built: `setup-kiosk.ps1` (auto-login and shell for a user, with undo), the `set_startup` shell mode with an Explorer fallback, the Android lockdown and QR provisioning ([android-kiosk.md](android-kiosk.md)). |
| 10 Optional | Privileged Windows agent built ([windows-agent-service.md](windows-agent-service.md)). Display token rotation not built. |

## Decisions taken

These replace the recommendations in "Decisions for the owner" where they differ.

| Question | What was decided and built |
|---|---|
| Command signing key | A server-held Ed25519 key (`command_key` in the instance folder), made on first use. Devices pin the public key on first use (trust on first use, per server address). Two ways around the first-contact window: the Android QR code carries the key, and the Windows agent's install script compares the server's key with a fingerprint the admin copies from the page. The fingerprint is on Admin > System. A changed key is refused and reported as `command_key_changed`. |
| Sequence numbers | `seq` is time based: the server's clock in milliseconds, forced to rise per display. Restoring or rebuilding the database cannot make new commands look old. Replaces the plain counter. |
| Windows SYSTEM service | **Built**, as an optional **privileged agent**: Electron's binary in node mode run by a SYSTEM scheduled task, with its own token, enrollment code and local switches. It handles reboot, startup, update and automatic sign-in once enrolled; the player's own handling then stands down. |
| Windows code-signing certificate | A self-made, code-signing-only certificate (`New-DevCert.ps1`) is enough; the agent pins its thumbprint. Nothing has to be bought. A purchased certificate can replace it by reinstalling the agent. Without a signed installer the agent refuses updates. |
| Android Device Owner as the managed tier | Yes, via adb or the QR code. |
| Trust root for updates | The server signs the command that names the installer's hash and size; there is no separate vendor release key. The privileged agent adds a second key (the code-signing certificate). |
| Windows kiosk helper | Documentation, `setup-kiosk.ps1` and the signed `set_startup` shell mode. No remote "set the password" command through the player; the agent's auto-login command is the one exception and is admin-only, encrypted end to end and HTTPS only. |
| Auto-update default | Off. Admin-triggered per display. |

**Dropped or deferred:** release catalog and canary rollout, a vendor offline release key, `agent_update_channel`,
display token rotation, the agent updating itself (an admin re-runs the signed install script), any Linux agent, any
macOS support, rollback of a bad update on Android (not possible on the device), a remote shutdown.

**Power menu on Android.** With the Device Owner lockdown on, Power + Volume Down still opens the power menu on the
owner's Galaxy tablet, and shutting down from it works. No command can shut a device down remotely; only reboot exists.

## Unverified before building

- Whether native code can use the display credential as a bearer token on `/events` and
  `/heartbeat` (Electron main and Android), or only the page cookie. Settled for commands: both apps send it as a
  bearer token on `/commands`, `/command-key` and the package download.
- Multi-worker behaviour of the in-memory SSE broker (the server runs one process today). Still open.
- The real implementation of the admin/editor decorators for the new endpoints. Settled: every device command (restart,
  logs, reboot, startup, update, auto-login) and the agent enrollment need the admin role, not editor.
