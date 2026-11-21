# The privileged Windows agent

An optional helper for Windows screens. It lets the server **reboot** the PC, change **startup** (autostart, Windows
shell mode), **update** the player and set **automatic Windows sign-in**, while nobody is logged in as an
administrator. Design and reasoning: [windows-agent-service.md](windows-agent-service.md). Protocol:
[signed-commands.md](signed-commands.md). Supported: Windows 10/11. Not built: Linux (best effort later), macOS (dropped).

**Status: built and unit-tested, never run on a real PC.** Treat everything below as what the scripts and code do, not
as something a device has confirmed. The open questions are listed at the end.

## What it is, and why it is elevated

A small program (the player's own Electron binary run in node mode, so no second runtime) started at boot by a
scheduled task **as SYSTEM**. It has to be SYSTEM to reboot, edit the machine registry, install the player for all
users and write the sign-in password. Because that is a lot of power, it accepts almost nothing:

| It does | Because |
|---|---|
| obey only commands **signed by the server's command key**, which it pinned at install | a stolen display token, or anyone on the network, cannot make it do anything |
| install only an installer that matches the **signed size and sha256** and whose **Authenticode signature is valid and made by the pinned certificate thumbprint** | someone who controls the server, or the download folder, still cannot put code on the PC without your code-signing key; someone who has only the code-signing key still needs a server-signed command for that exact file |
| read its settings only from `C:\ProgramData\AiSignXAgent` and `C:\Program Files\AiSignX\Agent`, writable only by SYSTEM and Administrators | the player (an ordinary program) cannot change the server address, the keys, the certificate or the agent's code |
| have no listening port, pipe, file drop or registry trigger | the only input is the HTTPS answer of the pinned server |
| act only on switches set at install (`-AllowReboot`, `-AllowStartup`, `-AllowUpdate`, `-AllowAutologin`, all off by default) and only on the one Windows account given as `-KioskUser` | a compromised server cannot widen what a PC accepts |

What it does **not** defend against: a hostile server that also holds the code-signing key (both keys together mean
you lost). Keep the signing key on the build PC only.

Once the agent is enrolled, the display's own player token no longer receives reboot/startup/update commands, and the
player's built-in handling of them stands down. If the agent is stopped, those commands wait (the display page shows
"not seen for N min"); remove the agent on the page to let the player handle them again.

## Before you start

1. **A code-signing certificate** on the PC where you build. The self-signed one from
   `clients\powerpoint-addin\tools\New-DevCert.ps1` works (code-signing purpose only, not a CA). A purchased one can
   replace it later: reinstall the agent with the new `-Thumbprint`. Nothing here needs buying.
2. A **standard** (non-administrator) Windows account that runs the screen: the kiosk account. The script refuses an
   administrator as the kiosk account unless you pass `-AllowAdminKiosk`.
3. The player installed for all users. The agent expects it at
   `C:\Program Files\AiSignX Player\AiSignX Player.exe`; if it is somewhere else (for example a per-user install),
   pass `-PlayerExe "<full path>"`. The player's assisted installer installs for the current user unless you choose
   otherwise; whether the agent's `/S /allusers` update migrates a per-user install is not verified.
4. HTTPS on the server is strongly recommended (see [https.md](https.md)). Over plain http the signed commands are
   still safe, but the enrollment code and the installer download travel unencrypted, and the auto-login form refuses.

## Build (on the build PC)

```powershell
cd clients\electron-client
$env:AISIGNX_SIGN_THUMBPRINT = '386BFBC220743F420E64AB481C4249EB1DE9C4BC'   # yours; optional: $env:AISIGNX_SIGN_TIMESTAMP = 'http://timestamp.digicert.com'
npm run dist:win     # the installer; scripts\sign-hook.js signs the app, uninstaller and installer (when the variable is set)
npm run agent:win    # dist-build\agent\AiSignX-Agent-<version>.zip and Install-Agent.ps1 (bundle hash and thumbprint inside, signed)
```

A timestamp keeps a signature valid after the certificate expires; without one the agent refuses installers once the
certificate has expired and you must re-sign. An installer that was built without signing can be signed afterwards:
`scripts\Sign-Installer.ps1`, which prints the SHA-256 and size of the signed file. Sign **last** and do not touch the file afterwards. Upload the signed installer on the
Downloads page as usual.

## Install (on the screen PC)

1. Copy `Install-Agent.ps1` and the `.zip` (both from `dist-build\agent\`) next to each other to the PC.
2. Display page > the **Privileged agent** card > **Enroll the privileged agent** (admin role; the display must be
   paired). You get a one-time code, valid 15 minutes and shown only once, and a ready command line with the server
   address and the command key fingerprint. It already has `-KioskUser <the Windows account that shows the screen>`
   and `-AllowUpdate`; replace the placeholder and add the other switches yourself.
3. Start PowerShell **as administrator**, run that line, adding the switches this PC should accept:

   ```powershell
   .\Install-Agent.ps1 -Server https://signage.example -Code ABCD-EFGH-JKLM-NPQR -KeyFingerprint "ab12 cd34 ef56 7890" `
       -KioskUser kiosk -AllowUpdate -AllowReboot -AllowStartup -AllowAutologin
   ```

   Add `-DryRun` first to check every parameter without changing anything. The script refuses to run unelevated, checks
   the bundle against the hash inside the (signed) script, creates the protected folders (refusing junctions), copies
   the program, exports your **public** certificate (from your certificate store, or pass `-CertFile owner.cer`),
   enrolls (and stops if the server's command key does not match the
   fingerprint), registers the tasks and waits for the agent to report "running".
   Use `-Thumbprint <40 hex>` only to override the certificate that was built into the script, and `-AllowInsecureHttp`
   only if you must enroll over plain http to another machine (the one-time code then travels unencrypted).
4. If the script says it is not signed by the pinned certificate, your admin account does not trust it yet: import the
   public certificate for your own account (or pass `-SkipSelfCheck` after checking the files yourself).

The agent trusts the signing certificate **only for itself** (in SYSTEM's own certificate store), not for the whole PC.
`-TrustScope Machine` makes the whole PC trust that key; the script asks before doing so.

Re-running the script repairs or upgrades the program files. It always enrolls again, so it needs a fresh enrollment
code each time; use that to change the switches or the kiosk account. The agent does not update itself: a new agent version means running the new signed
script again.

## How the player runs (set from the display page)

On the display page, **How the player runs** offers three choices, the same as on the Android app:

| Choice | On Windows | Sent as |
|---|---|---|
| **Application** | A normal program; it starts when someone opens it. | `autostart: false, mode: app` |
| **Application with auto-start** | Starts by itself when the kiosk user signs in. | `autostart: true, mode: app` |
| **Shell** | The player replaces the Windows desktop for the kiosk user (no taskbar or Start menu). If the player stops, Windows falls back to its normal desktop. | `autostart: true, mode: shell` |

The privileged agent carries the change out, so it works with nobody signed in as an administrator. Without the agent
the player app does the same itself with fewer rights (see the Electron README). Not yet tested on a real PC.

## Automatic Windows sign-in

Display page > Privileged agent > **Turn on automatic sign-in**: the Windows account (it must be the kiosk account from
install), its password, and your own AiSignX password. Over HTTPS (or from the server PC itself) and for admins only.

- The password is encrypted in that request to the PC's own key; the server keeps only the encrypted text, and deletes
  it when the agent has fetched the command. It is never written to the audit log, command history or logs.
- On the PC the password is stored as an **LSA secret** (like Sysinternals Autologon), not in the world-readable
  `DefaultPassword` registry value. A local administrator can still read an LSA secret. Anyone who can sign in as
  administrator on that PC can get the password, so use a kiosk account that has no other use.
- Windows 11 may need passwordless sign-in turned off for `AutoAdminLogon` to work; the helper sets the related value
  and remembers the old one.
- **Turn it off** removes the secret and the settings. Uninstalling the agent does too.

## Remove

On the PC, as administrator: `.\Install-Agent.ps1 -Uninstall` (add `-Yes` to skip the questions, `-KeepData` to keep
`ProgramData\AiSignXAgent`, `-Force` to delete the folders even when the agent's settings cannot be read; the startup,
auto-login and certificate changes are then not undone). It removes the tasks, the certificate trust, the startup and auto-login settings it
created, and both folders. Then press **Remove agent** on the display page if it still shows.

## When something goes wrong

| Symptom | Look at |
|---|---|
| Page says "not seen for N min" (the agent polls every 20 seconds; the page waits 10 minutes) | `C:\ProgramData\AiSignXAgent\logs\agent.log` (administrators only); Task Scheduler > AiSignX Agent |
| Status `unsafe_acl` | someone changed permissions or put a junction in the agent's folders; the agent does nothing until fixed (re-run the install script) |
| Update refused: `bad_signature` / `wrong_signer` | the installer is unsigned, was changed after signing, or was signed by another certificate than the pinned one |
| Update installs but the page shows `rolled_back` | the new version did not report in for 10 minutes; the previous verified installer was put back, once |
| Commands fail with `replayed` or `expired` | the server clock or database was reset; remove and enroll the agent again |
| Antivirus quarantines the agent or the helper | self-signed code can be flagged; add an exclusion for `C:\Program Files\AiSignX` |

## Not verified on hardware yet

See [testing-checklist.md](testing-checklist.md) section L. Known unknowns: whether SYSTEM can import the certificate
into its own store non-interactively (fallback `-TrustScope Machine`), whether the assisted NSIS installer migrates an
existing per-user install with `/allusers`, the LSA secret and the Windows 11 passwordless setting, and Defender or
SmartScreen reactions to self-signed code.
