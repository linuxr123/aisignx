# Installing the AiSignX server

Three ways, from easiest to most manual:

| You have | Use | Starts with the computer |
|---|---|---|
| Docker (any system) | [docs/docker.md](docker.md) | Yes (`--restart unless-stopped`) |
| Windows 10/11 or Windows Server | `scripts\install-server.ps1` | Yes (Scheduled Task) |
| Linux with systemd (Debian, Ubuntu, Fedora, ...) | `scripts/install-server.sh` | Yes (systemd service) |

macOS is not supported. `install-server.sh` still contains a launchd branch from earlier work; it is not
maintained or tested, and these docs do not cover it.

The installers need nothing but the AiSignX folder and an internet connection (for Python packages).
There are **no default passwords**: the first time you open the server in a browser, the setup page asks
you to create the administrator account.

> The scripts named `install.sh` and `install.ps1` set up a **development** copy inside the repository
> folder (Python environment, database, ffmpeg and a browser). The `install-server` scripts install the real
> thing as a service. Add `-Yes` (Windows) or `-y` (Linux) to skip the questions.

## One command (downloads it for you)

Windows, from an **administrator** PowerShell:

```powershell
irm https://raw.githubusercontent.com/linuxr123/aisignx/v3/scripts/get-aisignx.ps1 | iex
```

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/linuxr123/aisignx/v3/scripts/get-aisignx.sh | sudo bash
```

It downloads AiSignX from GitHub over HTTPS (checking the checksum when the release publishes one), runs the
installer below, and deletes the download. To pass options, save the script and run it with `-Https`, `-Port`,
`-Ref v3.0.0` (Windows) or `bash -s -- --https --port 8080` and `AISIGNX_REF=v3.0.0` (Linux).
Only run a command like this if you trust the source: read the script first (it is short).
These scripts have only been syntax-checked; a clean-machine run is still to do.

## Windows

1. Get the AiSignX folder (download and unzip it, or clone the repository).
2. Open **PowerShell** in that folder and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1
   ```

   Windows asks for administrator permission. The script then:
   - finds Python 3.10 or newer (and offers to install it for all users with winget; a Python installed
     for one user only cannot be used by the service),
   - offers ffmpeg (video length and thumbnails) and checks for Edge or Chrome (web page previews),
   - copies the server to `C:\Program Files\AiSignX\app` and builds its Python environment,
   - keeps your data apart in `C:\ProgramData\AiSignX` (database, uploads, backups, logs, settings),
   - registers the Scheduled Task **AiSignX Server**: it starts at boot as the low-privilege `LOCAL SERVICE`
     account and restarts after a failure,
   - opens the port (5000) in Windows Firewall for private and domain networks,
   - starts it and tells you the address to open.
3. Open the address it prints (for example `http://192.168.1.50:5000`) and create the administrator account.

Options: `-Port 8080`, `-NoFirewall`, `-NoStart`, `-Yes` (no questions), `-DryRun` (show what would happen),
`-InstallDir`, `-DataDir`, `-Source <folder>`.

**Upgrade:** get the newer AiSignX folder and run the same command. The database is backed up first (a normal
AiSignX backup, see [backup.md](backup.md); it contains `command_key` but not the downloads folder, which the
upgrade does not touch), your data is never touched, database migrations are applied, and the previous
version stays in `C:\Program Files\AiSignX\app.prev` until you delete it. Your `aisignx.env` is kept. If you
changed the firewall rule by hand, add `-NoFirewall`, or the script puts it back. Upgrading the server does
not update the apps on the screens; push those from the display page ([updates.md](updates.md)).

**Uninstall:** `... install-server.ps1 -Uninstall` removes the service and the program and keeps your data;
add `-Purge` to delete the data as well (database, uploads, backups, downloads and the instance keys,
including `command_key`; a reinstall then makes a new key that paired screens will not trust).

Useful: *Task Scheduler* shows **AiSignX Server** (right-click > Run, End). The task runs `run-server.ps1` in
the program folder. The start-up script's output goes to `C:\ProgramData\AiSignX\logs\service.log`; the
application log is `aisignx.log` in the same folder. Settings are in `C:\ProgramData\AiSignX\aisignx.env`
(every variable is listed in [deployment.md](deployment.md#settings-and-data-folders)); after editing, restart
the task. Under `C:\ProgramData\AiSignX` you find `instance` (database, `secret_key`, `command_key`),
`uploads`, `downloads`, `backups` and `logs`.

## Linux

```bash
sudo ./scripts/install-server.sh
```

It finds or installs Python 3.10+ (apt, dnf, pacman or zypper), offers ffmpeg and Chromium, creates the system
user `aisignx`, installs the program in `/opt/aisignx` and your data in `/var/lib/aisignx`, writes
`/etc/aisignx/aisignx.env`, and enables the systemd service **aisignx** (hardened: it can only write to its own
data folder). Options: `--port 8080`, `--host 127.0.0.1` (listen on this machine only, for a reverse proxy),
`--open-firewall` (ufw or firewalld), `--no-start`, `-y`, `--source DIR`, `--prefix`, `--data-dir`, `--user`.

- Status and logs: `systemctl status aisignx`, `journalctl -u aisignx -f`.
- Settings: edit `/etc/aisignx/aisignx.env`, then `sudo systemctl restart aisignx`.
- **Upgrade:** run the same command from the newer AiSignX folder (database backed up first; the old program
  stays in `/opt/aisignx/app.prev`; data and `aisignx.env` are kept). The data folder holds `instance`
  (database, `secret_key`, `command_key`), `uploads`, `downloads`, `backups` and `logs`.
- **Uninstall:** `sudo ./scripts/install-server.sh --uninstall` (keeps data; `--purge` deletes it too, including
  `command_key`).

## After installing

- **Screens and apps** connect to `http://<this computer's address>:5000` (see the player and app guides in
  [docs/clients.md](clients.md)).
- **HTTPS:** put a reverse proxy in front (see [docs/https.md](https.md)); then set `AISIGNX_SECURE_COOKIES=1`.
- **Backups:** the server makes them itself (Admin > Backups); the installer also makes one before every upgrade.
- **Firewall:** the Windows installer opens the port for private and domain networks only. On Linux use
  `--open-firewall` or open the port yourself.
- **Android and Windows apps:** upload the installers on the **Downloads** page ([downloads.md](downloads.md)).
- **All settings:** [docs/deployment.md](deployment.md).

## Troubleshooting

| Problem | What to do |
|---|---|
| "Python 3.10 or newer was not found" | Install it for all users (Windows: python.org, tick *Install for all users*) and run again. |
| The server does not answer after install | Look at the log (see above). A port already in use? Run again with `--port` / `-Port`. |
| Can open it on the server but not from a screen | The firewall: open the port (`--open-firewall`, or the Windows rule *AiSignX server*; it only covers private and domain networks, so set the network profile to *Private*). |
| "No Edge/Chrome" or "ffmpeg is missing" notes | Optional. Install them later and restart the service. |
| An upgrade went wrong | The old program is in `app.prev` and the database backup is in the data folder's `backups`. Swap the folders back and restore (docs/backup.md). |

## What has been tested

The Windows script was run on Windows 11 for everything that does not need administrator rights (copy, Python
environment, database migrations, backup before upgrade, starting the installed server). Registering the
Scheduled Task, the firewall rule and the folder permissions need elevation and were reviewed but **not yet run**.
The Linux script was syntax-checked and its file copy tested, but has **not been run on a real Linux machine**.
Please try it on a spare machine first and report what you see.

## HTTPS in the same run

Add `-Https` (Windows) or `--https` (Linux) to the install command and the installer finishes by running the nginx script
(`setup-nginx`): certificate, nginx, firewall and the secure settings. Without winget, the Windows installer downloads the
official Python 3.12 installer from python.org and checks that it is signed by the Python Software Foundation before running it.
Details: [https.md](https.md).
