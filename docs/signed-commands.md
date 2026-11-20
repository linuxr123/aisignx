# Signed device commands (protocol)

Status: **built** in the server (`server/aisignx/displays/signing.py`, `agent.py`), the Windows app
(`clients/electron-client/src/agent/verify.js`, `tierb.js`), the Android app (`shell/SignedCommand.kt`) and the privileged
Windows agent (`src/agent-svc`). **Never run on real devices except the owner's Galaxy tablet** (reboot, update and the
QR key pin were exercised there; the Windows parts have only unit tests). Extends
[device-agent-design.md](device-agent-design.md) ("Tier B" commands). Windows and Android are the supported platforms;
Linux would be best effort and has no client; macOS is dropped.

## Why

Restart-player and collect-logs (Tier A) can do little harm. **Reboot, startup changes and app updates (Tier B)
can take a screen out of service or run new code**, so a device must never obey one just because its display
token asked for it. Each Tier B command is **signed by the server's command key** and the device checks the
signature before acting.

This protects against: a stolen display token, anyone on the network who can reach the server's API, a replayed
or retargeted old command. It does **not** protect against someone who has full control of the server (they hold
the key). That is a deliberate, stated limit.

## Keys

- The server makes one Ed25519 key pair the first time it needs it, stored in the instance folder as
  `command_key` (owner read/write only, included in every backup archive and restored with it). Rotating means deleting the file and re-pairing the devices.
- A device fetches the public key with `GET /api/player/v1/command-key` (display token) and **pins the first one
  it sees** (trust on first use), together with the server address. A device set up by the Android QR code is
  given the key inside the code (`command_key` in the provisioning extras bundle, next to `server_url` and
  `pairing_secret`) and pins that, so there is no first-contact
  window. Plain trust-on-first-use over plain http can be fooled by someone on the network at that moment; the
  **Command key** fingerprint on Admin > System lets you check. (The Windows player app has no QR step, so it is
  always trust on first use; the privileged agent is not, see below.) It later accepts only commands signed by the
  pinned key. If the server presents a different key the device keeps the old pin, refuses Tier B commands and
  reports `command_key_changed` in its diagnostics. **Forget server** in the app's setup (PIN protected when a PIN is
  set), or changing the server address, clears the pin together with the stored `seq` and server-time mark.
- The fingerprint shown on the server (Admin > System) is the first 16 hex characters of the SHA-256 of the raw
  32-byte public key, in groups of four. An admin can compare it with the one the device shows.

```
GET /api/player/v1/command-key  ->  {"public_key": "<base64 of 32 raw bytes>", "fingerprint": "ab12 cd34 ef56 7890"}
```

## The command

`GET /api/player/v1/commands` (existing) returns, for each pending command:

```
{"id": "<request id>", "action": "reboot_device", "params": {...}, "expires_in_s": 120,
 "signed": {"payload": "<string>", "sig": "<base64 Ed25519 signature of the payload's UTF-8 bytes>"}}
```

and, once per response, `"server_time_ms": <epoch ms>`. Tier A commands have no `signed` member.

`payload` is a **string** (so nobody has to canonicalise JSON): the UTF-8 JSON text of

```
{"v":1,"display_id":7,"request_id":"<id>","action":"reboot_device","params":{},"seq":412,"exp_ms":1763300000000}
```

(keys sorted, no spaces). The device **must**:

1. verify `sig` over the exact payload bytes with the pinned public key;
2. parse the payload and check `v == 1`, `display_id` is its own, `request_id` and `action` equal the
   unsigned fields of the command (and ignore the unsigned `params`: **use only `payload.params`**);
3. check `exp_ms` is later than the server's time (use `server_time_ms` from the same response, because device
   clocks are often wrong). `server_time_ms` itself is not signed, so the device keeps the **highest
   `server_time_ms` it has seen per server** and treats any response with a lower value as untrustworthy (a real
   server clock does not go backwards). Use HTTPS where the network is not yours;
4. check `seq` is greater than the highest `seq` it has accepted before, and store the new value **before**
   acting (a command runs at most once even if the device crashes mid-way). `seq` is **time-based** (the
   server's clock in milliseconds, forced strictly increasing per display), so restoring or recreating the
   server's database never makes new commands look old; a device that sees a command with a lower `seq` than
   it has stored refuses it as `replayed`;
5. check `action` is one it supports and the params match the typed schema below, and that the local setting that
   allows the action is on (`settings.agent.reboot` / `.startup` / `.update` in the manifest).

Any failure: do not act; acknowledge `failed` with a short reason (`bad_signature`, `wrong_display`, `expired`,
`replayed`, `not_allowed`, `unsupported`, `bad_params`). The two apps differ in small ways: a payload with `v` other than
1 is `unsupported` on Windows and `bad_signature` on Android, and a response whose `server_time_ms` went backwards is
`bad_time` on Windows and `expired` on Android.

**Lifetime and limits on the server.** A signed command expires after 120 s (`reboot_device`), 300 s (`set_startup`,
`set_autologin`) or 900 s (`update_app`), judged with server time. A display gets at most 10 commands per hour and one
reboot per 10 minutes (the device enforces the same gap for reboots). A display can have only one open command per action.

## Actions and parameters

| action | params | notes |
|---|---|---|
| `reboot_device` | `{}` | Windows: `shutdown /r /t 30` after acking (so the ack gets out); Android: Device Owner only (`DevicePolicyManager.reboot`). Not more than once per 10 minutes (device-side too). |
| `set_startup` | `{"autostart": bool, "mode": "app"\|"shell"}` | `autostart`: start the player at sign-in (Windows player app: its login item; privileged agent: a machine Run entry for the kiosk account; Android: enables or disables the boot receiver). `mode`: `app` = normal program; `shell` = the player replaces the Windows shell for that user (Windows only; Android answers `unsupported`). Both optional; omitted keys are left alone. At least one must be given. |
| `update_app` | `{"version": "3.1.1", "sha256": "<64 hex>", "size": 120158154}` | The device downloads the package from `GET /api/player/v1/commands/<request id>/package` (display token, only while the command is open), checks size and sha256 against the **signed** values, then installs: the Windows player app runs the installer silently (`/S`), the privileged agent runs it with `/S /allusers` after checking its Authenticode signature, Android installs with `PackageInstaller` (silent for Device Owner, otherwise the system asks the user; the package must have the app's own ID and the installed app's signing key; at most 500 MB). The server offers the newest installer of the screen's kind on the Downloads page. |

Acknowledge with `POST /api/player/v1/commands/<id>/ack` `{"state": "done"|"failed", "result": "<=200 chars"}`
as for Tier A. For `reboot_device` and `update_app`, ack `done` **before** the reboot or the install starts
(`result`: "rebooting" / "installing 3.1.1"); the next heartbeat then shows the new state.

## Capabilities and settings

The device reports in `diag.agent`: `can_reboot`, `can_set_startup`, `can_update` (booleans), plus
`startup` = `{"autostart": bool, "mode": "app"|"shell", "shell_capable": bool}` so the admin page shows what is
set now. The admin enables each ability per display (Edit > Device agent); the manifest's `settings.agent`
carries `reboot`, `startup`, `update` booleans.

## The privileged Windows agent (agent token, enrollment, auto-login)

A second receiver of these same signed commands: a small SYSTEM program on the Windows PC
([windows-agent.md](windows-agent.md), design in [windows-agent-service.md](windows-agent-service.md)). Nothing in the
command format changes; the differences are the credential, the routes and one more action.

**Agent token.** A different credential from the display token: `agt_` plus 43 URL-safe characters, sent only as
`Authorization: Bearer agt_...`, accepted only on `/api/player/v1/agent/*`. The server keeps `sha256(token)`. A display
token never works on those routes and an agent token never works on the display routes.

**Enrollment.** An **admin** (not editor) presses *Enroll privileged agent* on the display page. The server makes a
one-time code (16 characters from a 32-symbol alphabet, about 80 bits, shown as `ABCD-EFGH-JKLM-NPQR`), stores only
its hash and keeps it valid for 15 minutes. Wrong codes are not counted against the real code any more (the code is long
enough); enroll attempts are limited to 10 per minute per address. On the PC the elevated install
script posts it:

```
POST /api/player/v1/agent/enroll   {"code": "...", "public_key": "<b64 X25519>", "host": "PC1", "version": "3.1.0"}
-> {"agent_token": "agt_...", "display_id": 7, "command_key": "<b64 Ed25519>", "fingerprint": "ab12 cd34 ef56 7890", "server_time_ms": ...}
```

The code burns atomically (works once). The script compares `command_key` with the fingerprint the admin typed (printed
on the same page) and stops on a mismatch, so there is no trust-on-first-use window. Enrolling again replaces the token;
*Remove agent* clears it. Both cancel the display's waiting Tier B commands. Unpairing the display also ends the agent.
The page marks the agent "not seen" after 10 minutes without a poll (the agent polls every 20 s).

| Route | Purpose |
|---|---|
| `GET /agent/commands` | only the signed (Tier B) commands, same signed format, plus `server_time_ms` |
| `GET /agent/command-key` | the public key (the agent compares it with its pin and never replaces it) |
| `POST /agent/commands/<id>/ack` | acknowledge a Tier B command |
| `GET /agent/commands/<id>/package` | the installer of the agent's own open `update_app` command |
| `POST /agent/diagnostics` | `{"diag": {...}}` whitelisted: versions, `can_*`, `key_id`, `kiosk_user`, `startup`, `autologin`, `last_update`, `command_key_fingerprint` |
| `GET /agent/status` | `{"player_version", "player_last_seen_s"}` for the post-update health check |
| `POST /agent/unenroll` | the uninstall script's courtesy call |

**Exclusivity.** Once an agent is enrolled the display token no longer receives, acknowledges or downloads Tier B
commands (Tier A, restart and logs, stays with the player), so nothing is handled twice and a stolen display token
cannot fetch an installer. If the agent stops, Tier B pauses (the page says "not seen for N min") until it runs again
or an admin removes it. The abilities the page shows (`can_reboot`, `can_update`, ...) come from the agent's
diagnostics, never from the player's.

**Action `set_autologin`** (the agent only; the in-app code answers `failed`, `unsupported`):

```
{"enabled": true, "user": "kiosk", "domain": ".", "key_id": "<16 hex>", "enc": "<b64 envelope>"}
{"enabled": false}
```

Switch name `autologin`. The agent acts only for the kiosk account fixed at install, whatever `user` says.

**Auto-login envelope** (ECIES-style; `server/aisignx/displays/ecies.py`, `clients/electron-client/src/agent-svc/ecies.js`).
The password is encrypted **in the admin's request**, to the agent's X25519 public key, so only the PC can open it:

```
eph      = fresh X25519 key pair                       agent_pub = the agent's public key (from its diagnostics / enrollment)
shared   = X25519(eph_private, agent_pub)
key      = HKDF-SHA256(shared, salt = eph_public || agent_pub, info = "aisignx-autologin-v1", length 32)
aad      = "v1|<display id>|<request id>|<user>|<domain>"          (UTF-8)
envelope = base64( eph_public (32) || nonce (12, random) || AES-256-GCM(key, nonce, password UTF-8, aad) || tag (16) )
key_id   = first 16 hex of sha256(agent_pub)
```

The associated data ties the ciphertext to one display, one command and one account, so it cannot be replayed to
another. The form is accepted only over HTTPS or a loopback address, for an admin who re-types their own password.
Only the envelope is stored (`params`), and it is **wiped from the row when the command is first delivered** (the
signed response is built first); the plaintext lives only in the request handler. The password is never written to the
audit log, application logs, flash messages or the command history; the audit row says who, which display and which
account. A delivery lost in transit simply fails and the admin asks again. Test vectors shared by the Python server
and the node agent: `clients/electron-client/test/vectors/` (`gen_vectors.py` writes them, both test suites check them).

## Not in this protocol

The agent updating **itself** (its own program files change only when an admin runs the signed `Install-Agent.ps1`
again), Linux and macOS (macOS is dropped; a Linux root service would reuse this protocol but nothing is built), vendor
release keys, canary rollout and a release catalog (the server signs the installer's hash and size itself; see
[device-agent-design.md](device-agent-design.md)).
