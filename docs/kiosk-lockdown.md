# Kiosk lockdown

How to set up a screen that passers-by cannot leave. The display option **Lock player**
(admins only, under **Displays > Edit**) does the part the player apps can do on their own; the
operating system does the rest.

- Lock player needs an **unlock PIN**. The server refuses to turn the lock on without one, and
  refuses to remove the PIN while the lock is on.
- It works only in the Windows and Android apps. **A plain browser cannot be locked**: anyone
  with a keyboard can press Alt+F4 or Alt+Tab.
- Treat the PIN as a lock against passers-by, not as a password (see [clients.md](clients.md)).

Platform scope: Windows and Android are the supported platforms. Linux is best effort and not
covered here. macOS is dropped. None of this has been run on more than the owner's Samsung
Galaxy tablet and an NVIDIA Shield; the Windows steps below follow Microsoft's documentation and
have not been tried on a spare PC.

## Windows: a locked PC

1. **A dedicated local user** for the screen, with automatic logon: `netplwiz` (untick "Users
   must enter a user name and password") or Sysinternals **Autologon**, which stores the
   password encrypted.
2. **Autostart:** install the AiSignX Player and tick "Start at Windows logon" in its setup.
3. **Lock player + PIN:** set an unlock PIN for the display and turn on Lock player.
   (Displays > Edit; the PIN and the lock are admin-only options.)
4. **Optional, Windows Pro, Enterprise, Education or IoT:** replace the desktop with the app.
   - **Shell Launcher v2** (Enterprise, Education, IoT Enterprise) runs
     `AiSignX Player.exe` instead of `explorer.exe` for the screen user. There is no taskbar,
     Start menu or desktop to switch to.
   - **Assigned Access** (kiosk mode, Pro and up) does the same for a single app, with
     automatic restart if it closes.
5. **Group Policy or registry** for the screen user:
   - `NoWinKeys` = `1` (DWORD) in
     `HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer` turns off Windows-key
     shortcuts.
   - `DisableTaskMgr` = `1` (DWORD) in
     `HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System` turns off Task Manager.
   Sign out and in again to apply.
6. **Quiet the PC:** turn off notifications (Focus / Do not disturb), Windows Update restart
   prompts (active hours or managed updates), display sleep, computer sleep and screen savers.
7. **Windows IoT Enterprise:** **Keyboard Filter** can block Ctrl+Alt+Del, Alt+Tab, the
   Windows key and other combinations at the system level.

Steps 1, 2 and 4 can also be done from the admin with the optional **privileged Windows agent**
([windows-agent.md](windows-agent.md)): **Turn on automatic sign-in** (the password is kept as an
LSA secret), the **Startup** command (start the player at sign-in, or run it as the Windows
shell for the kiosk user, with an Explorer fallback) and remote reboot and update. Without the
agent the player app can do the startup and shell part for its own user (`mode: shell`, see
[clients/electron-client/README.md](../clients/electron-client/README.md)); automatic sign-in is
then the local helper `setup-kiosk.ps1`, run elevated on the PC.

### What the app can and cannot block

| Input | Locked app |
|---|---|
| Alt+F4, the close button, closing from the taskbar | Blocked |
| Minimize | Blocked (the window is restored at once) |
| Alt+Tab, the Windows key | Not blocked: the app stays on top and takes the focus back |
| Ctrl+Alt+Del | Not blocked (only Windows can): use Keyboard Filter or Shell Launcher |
| Ctrl+Alt+S / L / D / Q (setup, log, DevTools, quit) | Ask for the PIN |
| Windows sign-out or shutdown | Allowed |

While setup, the PIN prompt or DevTools are open, the player drops its always-on-top so they
can be used.

## Android: a locked device

### Screen pinning (no Device Owner)

With Lock player on, the app starts Android's lock task mode. Without Device Owner this is
**screen pinning**:

- **Settings > Security > App pinning** (the name varies by maker) must be on, or nothing
  happens. Android may ask once to confirm the pin.
- A person can unpin with **Back + Overview** (held together), so this stops casual use only.
- The display's Diagnostics show `lock: pinned`.
- Android shows **"App is pinned"** (and may ask "Pin this app?"). These are system security
  notices; no app can hide them. The only way to get rid of them is Device Owner, below.
  Device Owner needs a device with **no accounts signed in**, so on a personal phone you keep
  the message. Use Device Owner on dedicated signage devices and TV boxes.

### Device Owner: real lockdown

The easy way to get Device Owner on a tablet is a QR code on the factory-reset Welcome screen,
with no adb: see [android-kiosk.md](android-kiosk.md). The manual adb method follows.

As **Device Owner**, the app pins itself without asking and cannot be unpinned:

- no Home, Recents or status bar; the power menu still works (on the owner's Galaxy tablet
  **Power + Volume Down** opens it, and shutting down from there works; there is no remote
  shutdown, the admin can only reboot);
- no pin prompt;
- the app becomes the persistent Home app and starts after every boot;
- the status bar and keyguard are disabled, the screen stays on while plugged in, and safe boot,
  adding users, factory reset from Settings, USB file transfer and uninstalling the app are
  blocked. [android-kiosk.md](android-kiosk.md) has the full table.

The Diagnostics show `lock: owner`.

Setting it up needs a **fresh device, or one after a factory reset, with no Google (or other)
accounts added**:

1. Finish the device's first-run setup without adding an account.
2. Turn on **Developer options** and **USB debugging**.
3. Install the AiSignX Player and connect the device with USB (or `adb connect` over the
   network).
4. Run:

   ```powershell
   adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver
   ```

   For a debug build:

   ```powershell
   adb shell dpm set-device-owner com.aisignx.player.debug/com.aisignx.player.AisignxDeviceAdminReceiver
   ```

5. Turn off USB debugging again.

Devices already provisioned with the v2 app keep working: the receiver has the same component
name.

**To undo it:** open the hidden exit (long press MENU or OK, or 7 taps in a corner), enter the
PIN, and press **Remove Device Owner** on the setup screen. A factory reset also removes it.

**Leaving the lock on the tablet.** After the PIN the Setup screen opens and the lockdown stays
released for a **10 minute unlock window** (it survives an app restart; a reboot ends it). The
Setup screen has one button per Android settings page (Wi-Fi, Display, Date and time, Sound,
Apps, About the device, main Settings page) and, as Device Owner, **Factory reset this device**
(type `RESET`). **End the unlock now** locks again at once. If the app crashes at start five
times in ten minutes, it releases the whole lockdown by itself until it has fetched a manifest.
Details and what is not confirmed on a real tablet: [android-kiosk.md](android-kiosk.md).

## Remote unlock

On the display page of a locked, paired display, an admin can press **Unlock remotely** and
choose 5, 15 or 30 minutes or 1 hour. While unlocked:

- the lock is lifted (Windows: close and minimize work; Android: lock task stops);
- the admin shortcuts and the hidden exit skip the PIN.

**Lock again** ends it early. The unlock is sent live and never stored: a screen that is not
connected at that moment does not get it (the page says so), and restarting the app locks it
again. Both actions appear in the audit log.
