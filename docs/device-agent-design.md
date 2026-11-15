# Device agent and watchdog: design

Status: **design, approved to build in phases** (first release = phases 1-5). Written 2026-11-15 from
a read-only review of the Windows app, the Android app and the server.

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

## Unverified before building

- Whether native code can use the display credential as a bearer token on `/events` and
  `/heartbeat` (Electron main and Android), or only the page cookie.
- Multi-worker behaviour of the in-memory SSE broker (the server runs one process today).
- The real implementation of the admin/editor decorators for the new endpoints.
