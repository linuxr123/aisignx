# Signed device commands (protocol)

Status: **server side built; Windows and Android clients follow this document.** Extends
[device-agent-design.md](device-agent-design.md) ("Tier B" commands).

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
  `command_key` (owner read/write only, never in a backup of uploads, included in database backups of the
  instance). Rotating means deleting the file and re-pairing the devices.
- A device fetches the public key with `GET /api/player/v1/command-key` (display token) and **pins the first one
  it sees** (trust on first use), together with the server address. It later accepts only commands signed by the
  pinned key. If the server presents a different key the device keeps the old pin, refuses Tier B commands and
  reports `command_key_changed` in its diagnostics. Re-pairing the device (or "Forget server key" in its setup)
  clears the pin.
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
   clocks are often wrong);
4. check `seq` is greater than the highest `seq` it has accepted before, and store the new value **before**
   acting (a command runs at most once even if the device crashes mid-way);
5. check `action` is one it supports and the params match the typed schema below, and that the local setting that
   allows the action is on (`settings.agent.reboot` / `.startup` / `.update` in the manifest).

Any failure: do not act; acknowledge `failed` with a short reason (`bad_signature`, `wrong_display`, `expired`,
`replayed`, `not_allowed`, `unsupported`, `bad_params`).

## Actions and parameters

| action | params | notes |
|---|---|---|
| `reboot_device` | `{}` | Windows: `shutdown /r /t 30` after acking (so the ack gets out); Android: Device Owner only (`DevicePolicyManager.reboot`). Not more than once per 10 minutes (device-side too). |
| `set_startup` | `{"autostart": bool, "mode": "app"\|"shell"}` | `autostart`: start the player at sign-in (Windows: per-user Run key or scheduled task; Android: boot receiver). `mode`: `app` = normal program; `shell` = the player replaces the Windows shell for that user (Windows only; the device refuses `shell` unless it can undo it). Both optional; omitted keys are left alone. |
| `update_app` | `{"version": "3.1.1", "sha256": "<64 hex>", "size": 120158154}` | The device downloads the package from `GET /api/player/v1/commands/<request id>/package` (display token, only while the command is open), checks size and sha256 against the **signed** values, then installs: Windows runs the installer silently (`/S`), Android installs with `PackageInstaller` (silent for Device Owner, otherwise the system asks the user). |

Acknowledge with `POST /api/player/v1/commands/<id>/ack` `{"state": "done"|"failed", "result": "<=200 chars"}`
as for Tier A. For `reboot_device` and `update_app`, ack `done` **before** the reboot or the install starts
(`result`: "rebooting" / "installing 3.1.1"); the next heartbeat then shows the new state.

## Capabilities and settings

The device reports in `diag.agent`: `can_reboot`, `can_set_startup`, `can_update` (booleans), plus
`startup` = `{"autostart": bool, "mode": "app"|"shell", "shell_capable": bool}` so the admin page shows what is
set now. The admin enables each ability per display (Edit > Device agent); the manifest's `settings.agent`
carries `reboot`, `startup`, `update` booleans.

## Not in this protocol

**Auto-login** needs a Windows password and is designed separately (the password is encrypted for the device and
never stored in the clear on the server). Until then the Windows installer sets it up locally.
