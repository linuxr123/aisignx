# Windows privileged agent: design

Status: **built; not yet verified on real hardware** (see "As built" at the end). Extends [signed-commands.md](signed-commands.md) and
[device-agent-design.md](device-agent-design.md). Platform scope: Windows and Android supported, Linux best
effort with free means only, macOS dropped, nothing the owner has to buy (no paid certificates).

## Goal and threat model

The owner's rule: *the agent runs as SYSTEM so it can control the PC, but it obeys only signed updates.*
Concretely, a small separate program, `AiSignX Agent`, runs as SYSTEM and does the Tier B actions
(`reboot_device`, `set_startup`, `update_app`, plus the new `set_autologin`). The player app stays an ordinary,
unprivileged user program.

Attackers considered, and what stops each:

| Attacker | Capability | Stopped by |
|---|---|---|
| Normal user on the PC (or malware running as them, which includes a compromised player) | writes anything in their profile, `%TEMP%`, HKCU, their own processes' environment | Agent code, config, token, pins, keys and staging live where only SYSTEM/Administrators can write; the agent has no inbound channel (no port, pipe, file drop, registry trigger); it reads nothing from user-writable places. |
| Anyone on the network | can reach the server API, may steal the display token | Tier B commands are server-signed (Ed25519); the agent has its own token that the display token cannot substitute for; installer must also carry the pinned Authenticode signature. |
| Someone who replaces the installer bytes (server upload dir, network, disk) | swaps the exe | Signed command carries sha256+size of the exact file, and the file must verify against the pinned signer thumbprint. Both on the same bytes. |
| Someone who controls the server | can sign any command | **Not stopped** (stated limit), but they still cannot get code onto the PC without a file signed by the owner's code-signing key, and cannot make autologin for any account except the one fixed locally at install. |
| Someone who holds only the code-signing key (signs a trojan) | has a validly signed exe | Not enough: also needs a server-signed command naming that file's hash. Two independent keys. |

The agent never trusts "any validly signed file": it pins a certificate **thumbprint**.

## Decision summary

1. Agent = Electron's own binary run with `ELECTRON_RUN_AS_NODE=1`, copied to `%ProgramFiles%\AiSignX\Agent`,
   run by a scheduled task as SYSTEM at startup. No second Node shipped, no Windows service wrapper.
2. Its own credential `agt_...`, obtained by a one-time enrollment code at an elevated install; stored only in a
   SYSTEM/Administrators-only folder. The display token loses Tier B once an agent is enrolled.
3. Authenticode: the owner's public certificate is trusted **only in the SYSTEM account's own certificate
   stores** (not machine-wide) so `Status = Valid` is a real full verification, plus a mandatory thumbprint pin.
4. Update = download to protected staging, hash+size check against the signed values, Authenticode check, re-hash,
   run, health-check, roll back to the previously verified installer if the player is not healthy.
5. Auto-login: ECIES to the agent's X25519 key, password stored as an LSA secret, not a registry value, and only for
   the one account fixed at install.
6. The player is installed **per machine** (Program Files) when the agent is used, so a user cannot replace
   the player's code either.

## (a) The agent's own credential

### Why not reuse the display token, and why not a file handoff

The display token lives in the player's user-writable config. Anything the player (or the user) can read, the
agent must not accept as proof of identity, and anything the player can *write* must never reach the SYSTEM
agent. So: no "the app writes its token for the agent", no shared folder, no registry value the app sets.
The agent gets its credential from the **admin**, through an elevated action on the PC.

### Enrollment flow

1. Admin opens the display page > Device agent > **Enroll privileged agent** (role `admin`, not `editor`: this is
   permission to run code as SYSTEM on that PC). The server creates a one-time code: 16 characters, base32,
   about 80 bits, valid 15 minutes. Stored as `sha256` only (`Display.agent_enroll_hash`,
   `agent_enroll_expires`, `agent_enroll_fails`). The page shows a ready command line:

   ```
   powershell -ExecutionPolicy Bypass -File Install-Agent.ps1 -Server https://signage.example -Code ABCD-EFGH-JKLM-NPQR -KeyFingerprint "ab12 cd34 ef56 7890" -Thumbprint 386BFBC2... -Bundle <path>
   ```

   The fingerprint of the command key is in the command so there is **no trust-on-first-use window**: the script
   compares what the server returns with it and aborts on mismatch (attack prevented: a network attacker at
   enrollment time substituting their own key).
2. The elevated script runs `AiSignX-Agent.exe --init` (as admin), which makes the X25519 key pair (private key
   written to `keys\` in the protected folder), then `--enroll`: `POST /api/player/v1/agent/enroll`
   `{"code": "...", "public_key": "<b64 X25519>", "host": "<computer name, display only>", "version": "..."}`.
   The server checks the hash (constant time), expiry, failure counter (5 wrong codes kill the code; also a
   per-IP rate limit), then **atomically** burns the code and returns
   `{"agent_token": "agt_<43 chars urlsafe>", "display_id": 7, "command_key": "...", "fingerprint": "..."}`.
   Only `sha256(token)` is stored (`Display.agent_token_hash`), plus `agent_pubkey`, `agent_enrolled_at`.
   Re-enrolling replaces the token (old agent instantly locked out). A second code cannot be made while the
   first is valid (a new one replaces it).
3. The agent writes `agent.json` (server URL, token, display id, pinned command key, thumbprint, switches) into
   the protected folder and the script registers the task.

An **enrolled agent is removed** from the display page ("Remove agent": clears the hash, pubkey, diag; audited).
Uninstall on the PC does the local side.

### Scoping (decision)

New prefix `/api/player/v1/agent/`, authenticated by a new decorator `agent_required` that accepts only the
`Authorization: Bearer agt_...` form and looks up `agent_token_hash`. It is a different token namespace, so a
display token never works on these routes and an agent token never works on display routes (the existing
`display_required` is untouched and rejects `agt_` tokens).

| Route | Purpose |
|---|---|
| `POST /agent/enroll` | unauthenticated except the code; rate limited |
| `GET /agent/commands` | **Tier B commands only** (signed, same format as today), plus `server_time_ms` |
| `GET /agent/command-key` | public key (agent compares with its pin; never replaces it) |
| `POST /agent/commands/<id>/ack` | ack of Tier B commands only |
| `GET /agent/commands/<id>/package` | installer of the open `update_app` command only |
| `POST /agent/diagnostics` | capabilities, public key id, versions, `last_update` state, startup state (strictly cleaned like `diag.py`) |
| `GET /agent/status` | `{"player_version": "...", "player_last_seen_s": 12}` for the post-update health check |

**Exclusivity (decision):** once `agent_token_hash` is set, the display-token routes (`/commands`,
`/command-key`, `/commands/<id>/package`, `/ack`) no longer deliver, accept ack for, or serve packages of Tier B
actions. Tier A (restart player, collect logs) stays with the player. Attack prevented: a stolen or malicious
display token cannot fetch Tier B commands or installers; no double handling of one command by the in-app code
and the agent. If the agent dies, Tier B pauses (the display page shows "agent not seen for N min") until the task
restarts or the admin removes the agent, which makes the in-app fallback live again. That is deliberate: a
fail-closed default.

The agent's diagnostics, not the player's, are the source for `can_reboot / can_update / can_set_startup /
can_autologin` on the display page when an agent is enrolled (the player is unprivileged and its claims about
privileged abilities are not evidence).

Audit events: `display.agent_enroll_code`, `display.agent_enrolled`, `display.agent_removed`,
`display.agent_result` (reuses the existing name). Never with codes, tokens or passwords.

### Local switches (defaults off)

`agent.json` has `allow: {reboot, startup, update, autologin}`, set only by the install script flags
(`-AllowReboot -AllowStartup -AllowUpdate -AllowAutologin`, all off by default). They are the local, SYSTEM-owned
equivalent of the per-display switches; both must be on. The manifest's `settings.agent` is ignored by the agent.

## (b) Files and ACLs

Everything below is created by the elevated script; the agent re-checks it at every start.

| Path | Content | ACL |
|---|---|---|
| `%ProgramFiles%\AiSignX\Agent\` | `aisignx-agent.exe` (copy of the Electron binary and its DLLs, `resources\`), `app\` (agent JS: `main.js` and the pure modules), `ps\*.ps1` (helper scripts) | SYSTEM F, Administrators F, Users RX (read and run only). Inheritance off. |
| `%ProgramData%\AiSignXAgent\` | `agent.json` (URL, token, pins, thumbprint, switches), `state.json` (seq and server_time high-water, command ledger), `keys\x25519.key`, `staging\`, `previous\`, `logs\`, `public\status.json` | SYSTEM F, Administrators F. Nothing for Users, except `public\` which is Users RX (so the player can *show* agent status; it is output only and never read as input by anything privileged). |

Decisions and the attack each prevents:

* **A separate top-level folder `AiSignXAgent`, not `ProgramData\AiSignX\agent`.** By default any user can
  create a folder directly under `ProgramData`, and the creator owns it, so a user could pre-create
  `ProgramData\AiSignX` (the server installer also uses that name), keep ownership, and later swap the
  subfolders or add a junction. The script instead: if the folder exists, takes ownership
  (`icacls /setowner Administrators /T`), resets the ACL (`icacls /reset`, then `/inheritance:r` and the grants
  above), and **refuses to continue if any reparse point (junction or symlink) exists** inside it or on the path.
  (Attack prevented: pre-created folder, junction redirect of a SYSTEM write to elsewhere.)
* **Runtime self-check** (`config.js`, run at every start, not only at install): the data folder's owner must be
  SYSTEM or Administrators, the folders have no reparse points, and `agent.json` and `state.json` are not
  writable by Users (queried with `icacls`/`Get-Acl` through a fixed-argument call). If it fails the agent
  does nothing and writes `status.json` `{"state": "unsafe_acl"}`. This covers someone relaxing the ACLs later.
* **Code under Program Files**: standard users cannot write there, so cannot replace `app\main.js`, the DLLs
  or the `.ps1` helpers. The agent calls `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass
  -File <fixed path>`; `AllSigned` would add a failure mode on a certificate the machine may not trust, so the
  protection is the folder ACL, not a script signature.
* The agent runs staged files only from `staging\` (protected), never from `%TEMP%` or the user's Downloads.
* No inbound anything: no listening port, named pipe, scheduled-task trigger on a user-writable file, or
  registry watcher. The only input is the HTTPS response from the pinned server URL.
* Environment: the task is launched by Task Scheduler with its own environment (SYSTEM), so user environment
  variables (`NODE_OPTIONS`, `PATH` tricks) do not reach it. The agent additionally ignores `NODE_OPTIONS` by
  starting with `--no-warnings` and checking `process.execArgv`/env in `main.js`: if `NODE_OPTIONS` or
  `ELECTRON_*` other than `ELECTRON_RUN_AS_NODE` are set it logs and exits.
  (To verify: the Electron binary must have the `runAsNode` fuse enabled; see the test plan.)

### How the elevated installer places the program

The installer's input is an **agent bundle** `AiSignX-Agent-<version>.zip` made by `scripts\Build-Agent.ps1` from
the unpacked Electron output (`electron-builder --dir`) plus `src\agent-svc\*`, and copies of the pure modules
(`verify.js`, `package.js`, `logic.js`; one source of truth, copied at build time, with a test that the copies are
identical). Trust in the bundle comes from the admin running the script elevated, because "an elevated script
copying files from somewhere a user could write" is the classic escalation. Therefore:

1. `Install-Agent.ps1` and the bundle are produced together and the script **contains the bundle's SHA-256**
   (written in by `Build-Agent.ps1`, then the script is Authenticode-signed with the owner's key). The admin
   runs the signed script; the script verifies the signature thumbprint **on itself** before doing anything
   (`Get-AuthenticodeSignature $PSCommandPath`) and the bundle against the embedded hash.
2. It copies the bundle to a temporary folder **inside** the freshly protected `%ProgramData%\AiSignXAgent\work`
   (never `%TEMP%`), extracts there (zip-slip check: every entry must stay inside the target), verifies again,
   and only then moves into `Program Files`. A user who can swap the bundle on the share the admin copied it from
   is detected by the embedded hash.
3. The `-Thumbprint` default is read from the signed script (written by the build), so a normal install does
   not depend on typing it; `-Thumbprint` overrides it, for a later purchased certificate.

## (c) Verifying Authenticode on a self-signed certificate

Facts of `Get-AuthenticodeSignature` (Windows PowerShell 5.1): with a self-signed certificate that is not trusted
the result is `Status = UnknownError` (message: chain ended in an untrusted root), `SignerCertificate` is filled
in, and `Valid` is never returned. Whether a *tampered* file still reports a hash failure ahead of the chain
error is not guaranteed by documentation, so **"untrusted root, but the thumbprint matches" is not a
verification**: the thumbprint is read from the certificate embedded in the file, which an attacker can copy
onto a modified file. Only a status that includes a successful digest check proves the signature belongs to
those bytes.

Options:

| Option | Gives `Valid` | Risk |
|---|---|---|
| A. Import the owner's certificate into **LocalMachine** `Root` and `TrustedPublisher` | yes | Machine-wide: every program and user on the PC now trusts anything signed with that key (SmartScreen, AppLocker, "Run as" prompts). Widest effect. |
| B. Accept `UnknownError`/untrusted root plus thumbprint match | no | Not a verification, see above. **Rejected.** |
| C. Verify the PKCS#7 and the image digest ourselves (parse in Node/PowerShell, or .NET `SignedCms` with a `CustomTrustStore`) | n/a | Reimplementing PE Authenticode hashing is a security-critical custom parser; `CustomTrustStore` needs .NET 5+ (PowerShell 7), which is not installed by default. Rejected as too much new code. |
| **D (chosen). Import the certificate into the SYSTEM account's own stores** (`Cert:\CurrentUser\Root` and `Cert:\CurrentUser\TrustedPublisher` **as SYSTEM**) | yes, for processes running as SYSTEM | Only SYSTEM-run verification trusts the certificate; ordinary users and other programs do not. The agent's check is the only consumer. |

Choice: **D**, with the thumbprint pin mandatory on top:

* The script copies the owner's public certificate (`-CertFile owner.cer`, or exported from
  `Cert:\CurrentUser\My\<thumbprint>` by `Export-Certificate`, public part only) into the protected folder and
  checks that its thumbprint equals `-Thumbprint`. The agent imports it into SYSTEM's stores on every start
  (idempotent). Only the thumbprint in `agent.json` decides which certificate: a different certificate in the
  store, even one chaining to a real root, is not accepted.
* The verification script `ps\Verify-Signature.ps1 <file>` prints JSON `{status, thumbprint, notAfter,
  signedAt, eku}` and nothing else; the agent requires `status == "Valid"` **and** `thumbprint ==
  pinned` (case-insensitive hex compare against `^[0-9A-F]{40}$`), **and** the EKU contains Code Signing
  (1.3.6.1.5.5.7.3.3), **and** SHA-256 digest algorithm. Time: if timestamped, `Valid` holds after the certificate
  expires; if not timestamped, an expired certificate fails and the owner re-signs. Recommend timestamping with
  a free time stamp server (`http://timestamp.digicert.com`, free to use, no purchase).
* Fallback if SYSTEM's `CurrentUser` import is refused programmatically in a non-interactive session (root
  store adds can show a UI warning in interactive sessions; **unverified, see the test plan**): the script
  option `-TrustScope Machine` imports to LocalMachine (option A) after an explicit "this trusts the key on
  the whole PC" confirmation and logs it; default stays SystemOnly.
* Check that the owner's certificate is a leaf (Basic Constraints CA false) and has only the code signing EKU
  (`New-DevCert.ps1` to be checked). A certificate that is also a CA trusted as Root is much more dangerous
  than a code-signing-only leaf; the install script refuses `CA:TRUE` unless `-AllowCaCertificate`.
* Windows does not need to "trust" the player installer for it to run when started by SYSTEM; SmartScreen does
  not apply to a process started by a service, and the installer carries no Mark-of-the-Web because the agent
  wrote it. (Verify.)

Risk accepted: the signature proves "signed with the key whose thumbprint the install pinned" and nothing about
the key's custody. Keep the signing key in the owner's `CurrentUser\My` non-exportable and only on the build
PC. A purchased certificate replaces it later by reinstalling with a new `-Thumbprint` (admin action), never by
a server command.

## (d) Signing the build, and the Downloads page

* `clients\electron-client\scripts\Sign-File.ps1 -Path <file> -Thumbprint <t> [-Timestamp <url>]`: finds the cert
  in `Cert:\CurrentUser\My`, refuses if absent or if it has no code signing EKU, runs
  `Set-AuthenticodeSignature -HashAlgorithm SHA256 -TimestampServer <url>`, then re-reads the signature and
  fails the build unless the thumbprint matches and the file was changed.
* **In the build:** `package.json` `build.win.sign` points to a small hook, `scripts\sign-hook.js`, that
  electron-builder calls for every executable it produces (app exe, uninstaller, installer); the hook runs
  `Sign-File.ps1` through `execFile('powershell.exe', ['-NoProfile', '-File', ..., '-Path', file, '-Thumbprint', t])`,
  the thumbprint taken from `AISIGNX_SIGN_THUMBPRINT` in the build environment (never in a tracked file).
  Without the variable the build still works but unsigned (and the agent will refuse that file).
* **After the build, stand-alone:** `Sign-Installer.ps1` signs and verifies an already built
  `dist-build\*.exe` (for builds made elsewhere or when the hook is off).
* The NSIS installer must be signed **last** and not touched afterwards (a changed byte breaks the digest).
  The sha256 and size in the signed command are computed by the server on the **signed** file.
* **Downloads page flow: unchanged.** The admin uploads the signed `.exe` as today; the server stores it,
  computes sha256/size, and an `update_app` request signs `{version, sha256, size}`. The server does not
  inspect signatures (it has no Windows tools and does not need to); the agent does. The upload page should show
  a hint "must be signed with the key pinned on the screens".
* **Installer type (decision):** the player installer becomes the assisted NSIS form (`oneClick:false`,
  `perMachine:false`, `allowElevation:true`, `allowToChangeInstallationDirectory:false`) so one signed file
  honours `/S /allusers` (agent, runs elevated as SYSTEM, installs under `Program Files`) and `/S /currentuser`
  (the existing in-app fallback). A SYSTEM process running today's per-user, one-click installer would install
  into SYSTEM's own profile, which is wrong. A machine-installed player also cannot be altered by users.
  First agent-based update on a PC that has the per-user copy: the agent runs `/S /allusers` and the new
  installer is configured to remove the old per-user copy (`deleteAppDataOnUninstall` stays false, settings are
  kept). **Unverified; see the test plan.** If the machine/user hand-over proves unreliable, the fallback is to
  keep per-user installs and have the agent run the installer **as the kiosk user** (a `CreateProcessAsUser`
  helper); that is more code and is not preferred.

## (e) The update procedure and recovery

Pure logic lives in `src\agent-svc\update.js` (state machine, unit-tested); effects are injected.

1. Poll sees `update_app`. The unchanged checks of `verify.js`: signature with the **pinned** key, display id,
   request id and action equal, `exp_ms` vs `server_time_ms`, time mark, seq high-water, typed params,
   `allow.update`. Persist `seq` and `server_time` high-water to `state.json` **before** acting. The ledger id is
   recorded only after verification passes (a forged command can never burn a real id).
2. State `pending-update.json` is written first: `{id, from, to, sha256, size, startedAt, phase}`.
3. Download `GET /agent/commands/<id>/package` with the agent token into `staging\<id>.exe.part` through
   `package.js` `savePackage` (size cap from the signed size, streaming sha256, spawn/stream errors handled);
   rename to `staging\<id>.exe`. Any mismatch deletes the file and acks `failed`.
4. `Verify-Signature.ps1` on that file (section c). Fail: delete, ack `failed` (`bad_signature` / `wrong_signer`).
5. Re-hash `staging\<id>.exe` immediately before running (lesson from the in-app code); compare with the signed
   values. The staging folder is writable only by SYSTEM/Administrators, so these are the same bytes as verified.
   Copy the file to `previous\candidate-<to>.exe` only for the run; keep the logs.
6. **Ack `done` ("installing 3.1.1")** to the server *before* the installer starts, as the protocol says. Then
   write `phase: "installing"`.
7. Stop the player by exact image name (`taskkill /IM "AiSignX Player.exe" /F`, fixed arguments, no text from the
   server; the watchdog or autostart would otherwise restart it mid-install). Do **not** write any "stopped"
   marker that disables recovery (lesson): the in-app watchdog flag is left untouched.
8. Run `<staged>.exe /S /allusers` with `spawn` (argument array, no shell). **Listen for the `error` event** and
   for exit; a spawn error is a failure. Timeout 10 minutes. Exit code != 0: `phase: "installer-failed"`, report,
   and make sure the *old* app still starts (relaunch via the autostart path) rather than leaving a dead screen.
9. After exit 0: relaunch the player for the kiosk user's session (it was killed; the autostart mechanism of
   `set_startup` handles the normal case; if the kiosk user is logged in, the agent starts it in that session
   with a helper; otherwise it comes up at next sign-in). Phase `verifying`, deadline 10 minutes.
10. **Health check:** the installed exe's file version equals `to` **and** `GET /agent/status` shows
    `player_version == to` with `player_last_seen_s < 90` (the player's normal heartbeat). Success: phase
    `ok`, delete `pending-update.json`, keep `previous\<from>.exe` replaced by the new one.
11. **Rollback:** if the deadline passes unhealthy and `previous\` holds an installer: verify it again exactly as
    in 4 and 5 (signature thumbprint + the sha256 recorded when it was last installed by this agent), run it with
    `/S /allusers`, report `last_update: {id, state: "rolled_back"}` in diagnostics. Rollback happens **once per
    command id**; if it also fails, state `failed` and the agent stops touching the app (no loop), waiting for
    the admin. No previous installer (first update): state `failed_no_rollback`; the admin uploads a good build
    and sends a new command.

* **Previous installer:** the agent keeps exactly one: the signed installer it last installed successfully
  (copied into `previous\` after step 10). Disk cost is the installer size (about 120 MB). It is never taken
  from the server in a rollback; it must still verify against the pin.
* **Crash during an update:** at start the agent reads `pending-update.json`: `installing` with the installer
  not running means finish at step 9; `verifying` resumes the deadline; anything older than 1 hour is closed as
  `failed`. The seq already advanced, so the same command can never run twice; the admin issues a new one.
* **Anti-downgrade:** the agent reports the installed version; the server refuses an `update_app` to a version
  not newer unless the admin ticks "allow downgrade". Since downgrade means running older (signed) code, the
  agent additionally keeps `minVersion` in `state.json` (raised after each success; lowered only by the
  install script), and rejects older installers unless the signed command carries `"downgrade": true`.
  (A malicious server could set it; it still needs the signed installer.)
* Reboot after the update is not forced.

## (f) The elevated script

`scripts\Install-Agent.ps1` (also shipped inside the bundle as the entry point). Self-elevates like
`install-server.ps1` (asks Windows for administrator permission), `-DryRun` supported.

```
Install-Agent.ps1 -Server <url> -Code <enrollment code> -KeyFingerprint "<groups>"
                  [-Thumbprint <40 hex>] [-CertFile <owner.cer>] [-KioskUser <name>]
                  [-AllowReboot] [-AllowStartup] [-AllowUpdate] [-AllowAutologin]
                  [-TrustScope SystemOnly|Machine] [-AllowAdminKiosk] [-Bundle <zip>] [-Yes]
Install-Agent.ps1 -Uninstall [-KeepData]
```

Behaviour (idempotent: running it again repairs and upgrades, and re-enrolls only when `-Code` is given):

1. Verify own signature and the bundle hash (section b). Validate parameters: `-Server` must be `https://` or
   `http://` to a literal host and port (http gets a warning that command signing protects Tier B but not the
   download metadata); thumbprint format; code format; `-KioskUser` must exist locally (resolved to a SID now and
   stored; the agent only ever writes autologin and shell mode for that SID).
2. Create/repair the folders and ACLs of section b, reparse-point refusal included.
3. Stop the existing task, copy the program, import the certificate (into the stores per `-TrustScope`; SystemOnly
   is done by running `ps\Import-Cert.ps1` as SYSTEM through a one-time task, then deleting that task).
4. `--init` (key pair, only if absent) and `--enroll` (only with `-Code`), writing `agent.json` atomically.
   On enrollment failure nothing is registered and nothing is left half done.
5. Register the task **AiSignX Agent**: principal `S-1-5-18` (SYSTEM), `RunLevel Highest`, triggers: **at startup**
   and a repetition every 5 minutes (belt and braces), `MultipleInstancesPolicy IgnoreNew`,
   `RestartOnFailure` 999 times every 1 minute, `ExecutionTimeLimit PT0S`, `StartWhenAvailable`, runs on batteries,
   hidden. Action is a constant (no variable text):
   `cmd.exe /d /c "set ELECTRON_RUN_AS_NODE=1&& "%ProgramFiles%\AiSignX\Agent\aisignx-agent.exe" "%ProgramFiles%\AiSignX\Agent\app\main.js""`
   (the path is the script's own constant; the task XML is generated with the same `xmlEscape` approach as
   `task.js`). The single-instance guard in `main.js` is a lock file in the protected folder.
6. Start it, wait up to 30 s for `public\status.json` `state: "running"`, print the result and the Windows
   caveats that apply (section g).

`-Uninstall`: stops and deletes the task, removes the program folder, removes the SYSTEM-store certificate and
(if `-TrustScope Machine` was used) the LocalMachine one, **undoes autologin if the agent set it** (asks, `-Yes`
to skip), removes the Winlogon shell override and Run entry it created, and deletes the data folder unless
`-KeepData`. It tells the admin to press "Remove agent" on the display page (the script also tries
`POST /agent/unenroll` once with the token, best effort).

How it restarts: the task's own restart-on-failure plus the 5-minute repetition (a stopped task is started again
even if Task Scheduler did not count a non-zero exit as failure; the single-instance lock makes extra starts
harmless). The agent loop is itself defensive: every poll is wrapped, unhandled rejections are logged and the
process exits non-zero after 10 consecutive internal errors, so the task machinery restarts it clean.

## (g) Auto-login

New action `set_autologin` (added to `ACTIONS`/`checkParams` in `verify.js`, which is shared with the in-app code;
the in-app code lists it as unsupported and acks `failed`/`unsupported`, so it works **only** through the agent):

```
params: {"enabled": true, "user": "kiosk", "domain": ".", "key_id": "<16 hex>", "enc": "<b64 envelope>"}
        {"enabled": false}
```

### Key and encryption

* The agent's X25519 key pair is made by `--init`; the private key stays in `keys\x25519.key` (SYSTEM/Admins
  only). The public key and `key_id = sha256(pub)[:8]` are reported by `POST /agent/diagnostics` and stored in
  `Display.agent_pubkey`.
* The admin form (role `admin`, password re-confirmation, **HTTPS or loopback only**: the server refuses the
  request over plain http from another host, because the password would cross the network in the clear)
  accepts user (must equal the kiosk user the agent reported) and password (1..127 chars). The server encrypts
  in the request handler:
  `eph = X25519 generate`, `shared = X25519(eph_priv, agent_pub)`,
  `key = HKDF-SHA256(shared, salt = eph_pub || agent_pub, info = "aisignx-autologin-v1", 32 bytes)`,
  `AES-256-GCM(key, random 12-byte nonce, plaintext = password UTF-8, aad = "v1|" + display_id + "|" +
  request_id + "|" + user + "|" + domain)`. Envelope = `eph_pub(32) || nonce(12) || ciphertext||tag`, base64
  (about 250 characters, well inside the 4096-byte payload limit). The AAD binds the ciphertext to this display,
  this request and this account, so it cannot be replayed to another command or screen.
* Python: the `cryptography` library on the server (`X25519PrivateKey`, `HKDF`, `AESGCM`); check it is already a
  dependency (it signs with Ed25519 today, so probably; add to `requirements.txt` otherwise). Node: `node:crypto`
  (`diffieHellman` with X25519 keys, `hkdfSync`, `createDecipheriv('aes-256-gcm')`).
* The command row stores **only the envelope** in `params`. When the command is first delivered
  (`agent.deliver`) the server **sets `params` to `{"enabled": true, "user": ..., "delivered": true}` and
  clears the envelope in the same transaction**, so the database and backups hold the ciphertext only until
  delivery; the signed response is computed before the wipe. The plaintext password exists only in the request
  handler's local variable: not in the audit detail (only `user`, `display`, `action`), not in logs, not in
  flash messages, not in the form's re-render on error; the form field is never echoed, the route is excluded
  from any request-body logging, and the response is a redirect.
* A delivery lost in transit (agent crashed after the server wiped the envelope) is simply failed: the admin
  makes a new request. Delivered-once is the price of not keeping ciphertext.

### What the agent does

Checks as for every Tier B command (signature, expiry, seq, `allow.autologin`), then: `key_id` equals its own;
`user` equals the kiosk user fixed at install (**the agent never writes autologin for any other account**; the
install refuses an Administrators-member kiosk user unless `-AllowAdminKiosk`); decrypts (all failure paths
return the same `bad_params`); zeroes buffers after use; then `ps\Set-Autologon.ps1` configures:

* Registry `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon`: `AutoAdminLogon=1`,
  `DefaultUserName`, `DefaultDomainName`, `ForceAutoLogon=0`. Values built from the install-time fixed user, not
  from the server text.
* **Password: LSA secret `DefaultPassword`** (what Sysinternals Autologon does), written with
  `LsaStorePrivateData` from a small signed `Add-Type` P/Invoke block in the helper (needs only the .NET
  Framework that ships with Windows PowerShell 5.1, so free). The password goes to the helper through its
  **standard input**, never the command line, environment or a file. Reason: a registry `DefaultPassword` value
  is readable by any local user (the Winlogon key is world-readable), an LSA secret is readable only by
  SYSTEM-level code (protected, not "encrypted against admins"; a local administrator can still extract it,
  stated in the docs). If the LSA write fails the command fails; it **never falls back** to the registry value
  (config `allowRegistryPassword`, default false, is for a later owner decision only).
* Windows 10/11 quirk: on some builds password-less/Windows Hello enforcement
  (`DevicePasswordLessBuildVersion`) must be 0 for `AutoAdminLogon` to take effect. The helper sets it to 0 and
  records the old value for undo. **Unverified.**
* `result` is "autologin set for <user>", never including the password or its length.

**Undo:** `set_autologin {"enabled": false}`: `AutoAdminLogon=0`, remove `DefaultPassword` LSA secret and any
`DefaultPassword` value, restore `DevicePasswordLessBuildVersion`. Also done by `Install-Agent.ps1 -Uninstall`.
The agent's diagnostics report `autologin: {enabled, user}` (no secret) so the page shows state.

## (h) Out of scope

* No Linux or macOS agent in this phase; macOS is dropped. **Linux root service would reuse:** `verify.js`
  (signature, params, time mark, seq), `package.js` (stream, sha256, size), `logic.js`, the enrollment API, the
  agent token model, ECIES (`node:crypto`), the update state machine `update.js`; replaced: the scheduled task by a
  **systemd unit** (`User=root`, `Restart=always`, files in `/opt/aisignx-agent` and `/var/lib/aisignx-agent`,
  mode 0700 root-owned), Authenticode by a free detached **Ed25519 installer signature** (`.sig` beside the
  AppImage/deb, verified against a pinned public key with `node:crypto`; a separate key from the command key),
  the shell-mode/autostart by a `.desktop`/systemd user unit, and autologin by a display-manager config
  (`/etc/gdm3/custom.conf` or LightDM), which plugs into the same encrypted-password command.
* The agent updating **itself**. v1 changes agent code only through the admin re-running the signed
  `Install-Agent.ps1` (a remote self-update would need its own two-key design). Note this in the docs.
* Replacing a hostile or compromised server, SSO, multi-site, a Windows service (SCM) wrapper, and
  Microsoft-account or domain-joined autologin policies beyond the local kiosk account.
* Showing a "removal" UI for the agent inside the player. Agent status for the player is a read-only
  `public\status.json`.
* Changing the signing of the Android client: Android keeps its own mechanisms (Device Owner, `PackageInstaller`
  signature check) from the Android document.

## (i) Test plan

**Unit tests in node (pure, no Windows, no Electron)**, `clients\electron-client\test\`:

* `ecies.test.js`: round trip; wrong key, wrong AAD (other display id, request id, user), flipped bit, short or
  oversized envelope all return null; known vector shared with a Python test so server and agent agree.
* `authenticode.test.js`: parsing of `Verify-Signature.ps1` output: accepts only `Valid` + exact pinned
  thumbprint + code signing EKU; rejects `UnknownError`, `HashMismatch`, `NotSigned`, mixed case handled,
  garbage JSON, extra output, a thumbprint that differs by one character, expired certificate without timestamp.
* `update.test.js`: the state machine: every transition, crash resume, one rollback per id, no rollback loop,
  no previous installer, anti-downgrade, deadline calculation, "do not run if the re-hash differs".
* `config.test.js`: `agent.json` sanitising (types, URL shape, thumbprint format, switches default false,
  unknown keys dropped); path-safety helper (reparse and traversal rejection on made-up inputs); ACL result
  parser for `icacls` output (Users listed with write rights is unsafe).
* `autologin.test.js`: user must equal the configured SID/name, admin kiosk refused, params typed, the redaction of
  password, the stdin payload builder never puts the password in `argv`/`env`.
* `verify.test.js` extended: `set_autologin` params; same replay and time-mark cases for the agent state file;
  the agent copy of the pure modules equals the in-app copies (a hash comparison test).
* `task-xml.test.js`: the generated Task Scheduler XML (SYSTEM, highest, startup trigger, restart settings, the
  constant action, escaping), and a test that no path or setting from user input reaches the action.
* Quoting test: every registry path and PowerShell path in agent JS is checked by running the **read path** in a
  test (`reg query` call building) and by a regexp test that no single `\` precedes a letter inside a JS string
  literal that is meant to be a Windows path (backslashes doubled; lesson).
* Server pytest: enroll (code once, expiry, 5 failures, rate limit, re-enroll replaces token); agent token on
  each route, display token rejected on `/agent/*`, agent token rejected on display routes; Tier B hidden from
  display routes after enrollment and visible again after removal; ack and package scoping; diagnostics cleaning;
  autologin: HTTPS/loopback only, admin only, password confirmation, envelope decrypts with the test key, params
  wiped after delivery, password absent from audit rows, logs (`caplog`), and the response; migration 0020 up
  and down (`agent_token_hash`, `agent_enroll_hash`, `agent_enroll_expires`, `agent_enroll_fails`,
  `agent_pubkey`, `agent_diag`, `agent_seen_at`).
* A `Install-Agent.ps1` dry-run test in pytest or Pester (`-DryRun` prints what it would do; parameter validation).

**Only a real Windows PC (the owner's) can verify**, as a numbered checklist in `docs\windows-agent-test.md`
once built:

1. `electron-builder` output has the `runAsNode` fuse enabled and `ELECTRON_RUN_AS_NODE=1` runs `main.js`.
2. `Get-AuthenticodeSignature` as SYSTEM (use `schtasks` one-off) returns `Valid` for the owner-signed installer
   after the SystemOnly import, `HashMismatch` after changing one byte, and `UnknownError` when the certificate
   is not imported; the programmatic import into SYSTEM's `CurrentUser\Root` works without a UI prompt (else
   `-TrustScope Machine`).
3. The signing hook signs the app exe, uninstaller and installer, and the installer still runs; the NSIS
   installer honours `/S /allusers` and `/S /currentuser`, and migrates a per-user install.
4. ACLs: from a standard user try to write, rename and delete in both folders, plant a junction, and change the
   environment; the agent keeps working and refuses unsafe states.
5. The task starts at boot with nobody logged in, restarts after `taskkill /F`, and survives a non-zero exit.
6. Update end to end: success; tampered installer; wrong signer (a second self-signed cert); unsigned; the new
   version failing to start (rollback); power cut mid-install (resume).
7. `reboot_device`, `set_startup` (autostart with HKLM Run, shell mode for the kiosk user via loading its hive
   while logged out, and undo), and autologin: LSA secret really written, auto sign-in after reboot on Windows 11
   (including the passwordless setting), password not found in the registry, undo works, a wrong user refused.
8. SmartScreen or Defender do not block the agent's installer run; Defender does not quarantine the copied
   Electron binary or `Add-Type` helper (self-signed code can be flagged; whitelisting is an admin step to document).
9. Server on plain http: the autologin form refuses; on https it works.

## Build order (for the lead)

1. Server: migration 0020, `agent_required`, enroll, `/agent/*` routes, exclusivity, `set_autologin` encryption, admin
   page; pytest.
2. `src\agent-svc\` pure modules and tests (config, ecies, authenticode, update, autologin, task xml).
3. `main.js` loop and effects; `ps\*.ps1`; `Install-Agent.ps1`; `Build-Agent.ps1`; signing scripts and hook;
   installer type change.
4. Docs for admins (`windows-agent.md`: why elevated, what is trusted, the autologin password caveat) and the
   Windows test checklist; then the owner's test run.

## As built (differences from the design above)

* **Task action.** A constant `Run-Agent.cmd` in the program folder (`set ELECTRON_RUN_AS_NODE=1`, `set NODE_OPTIONS=`, the exe, `main.js`) is the task's only command and has no arguments, instead of a `cmd /c` line with nested quotes. The install script writes it; Users cannot edit it (read and execute only).
* **No copied modules.** The bundle carries `src\agent` and `src\agent-svc` as they are in the repository (one source of truth), so there is no copy-equality test; `verify.js`, `package.js`, `logic.js` and `task.js` are used by both the in-app code and the agent.
* **Where files are:** `resources\Install-Agent.ps1` (copied, constants filled in and signed by `scripts\Build-Agent.ps1` into `dist-build\agent\`), helpers in `resources\agent\ps\`, signing in `scripts\`.
* **Enrollment parameters** travel to the agent in a JSON file inside the protected data folder (deleted right after), not on a command line.
* **No `downgrade` flag.** Signed `update_app` params stay `{version, sha256, size}`; the agent refuses a version below `minVersion` and the server refuses a downgrade unless the admin ticks it. Lowering `minVersion` is only done by reinstalling.
* **Self-check of the install script:** when the script was built with a thumbprint it must itself be `Valid` for that thumbprint (trust the certificate for your own account on the admin PC first) or the admin passes `-SkipSelfCheck` after checking the files; the bundle's SHA-256 inside the script is checked in every case.
* The health check also treats a player that reports the new version but whose heartbeat is older than 90 seconds as unhealthy.
* The owner's certificate from `New-DevCert.ps1` was checked on the build PC: code-signing purpose only, sha256RSA, no CA flag (a leaf).
