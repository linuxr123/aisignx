# Installing the AiSignX server

Three ways, from easiest to most manual:

| You have | Use | Starts with the computer |
|---|---|---|
| Docker (any system) | [docs/docker.md](docker.md) | Yes (`--restart unless-stopped`) |
| Windows 10/11 or Windows Server | `scripts\install-server.ps1` | Yes (Scheduled Task) |
| Linux with systemd (Debian, Ubuntu, Fedora, ...) | `scripts/install-server.sh` | Yes (systemd service) |
| macOS | `scripts/install-server.sh` | Yes (launchd daemon) |

The installers need nothing but the AiSignX folder and an internet connection (for Python packages).
There are **no default passwords**: the first time you open the server in a browser, the setup page asks
you to create the administrator account.

> The scripts named `install.sh` and `install.ps1` set up a **development** copy inside the repository
> folder. The `install-server` scripts install the real thing as a service.

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

**Upgrade:** get the newer AiSignX folder and run the same command. The database is backed up first, your data
is never touched, and the previous version stays in `C:\Program Files\AiSignX\app.prev` until you delete it.

**Uninstall:** `... install-server.ps1 -Uninstall` removes the service and the program and keeps your data;
add `-Purge` to delete the data as well.

Useful: *Task Scheduler* shows **AiSignX Server** (right-click > Run, End). The log is
`C:\ProgramData\AiSignX\logs\service.log`. Settings are in `C:\ProgramData\AiSignX\aisignx.env`; after editing,
restart the task.

## Linux

```bash
sudo ./scripts/install-server.sh
```

It finds or installs Python 3.10+ (apt, dnf, pacman or zypper), offers ffmpeg and Chromium, creates the system
user `aisignx`, installs the program in `/opt/aisignx` and your data in `/var/lib/aisignx`, writes
`/etc/aisignx/aisignx.env`, and enables the systemd service **aisignx** (hardened: it can only write to its own
data folder). Options: `--port 8080`, `--open-firewall` (ufw or firewalld), `--no-start`, `-y`, `--source DIR`,
`--prefix`, `--data-dir`, `--user`.

- Status and logs: `systemctl status aisignx`, `journalctl -u aisignx -f`.
- Settings: edit `/etc/aisignx/aisignx.env`, then `sudo systemctl restart aisignx`.
- **Upgrade:** run the same command from the newer AiSignX folder (database backed up first; the old program
  stays in `/opt/aisignx/app.prev`).
- **Uninstall:** `sudo ./scripts/install-server.sh --uninstall` (keeps data; `--purge` deletes it too).

## macOS

```bash
sudo ./scripts/install-server.sh
```

Needs Python 3.10+ (`brew install python`). It creates a hidden service user `_aisignx`, installs into
`/usr/local/aisignx`, keeps data in `/Library/Application Support/AiSignX`, and registers the launchd daemon
`com.aisignx.server` (starts at boot, restarts if it stops). Logs: `/Library/Application Support/AiSignX/logs/`.
Restart after changing settings: `sudo launchctl kickstart -k system/com.aisignx.server`. Upgrade and uninstall
work like on Linux.

## After installing

- **Screens and apps** connect to `http://<this computer's address>:5000` (see the player and app guides in
  [docs/clients.md](clients.md)).
- **HTTPS:** put a reverse proxy in front (see [docs/https.md](https.md)); then set `AISIGNX_SECURE_COOKIES=1`.
- **Backups:** the server makes them itself (Admin > Backups); the installer also makes one before every upgrade.
- **Firewall:** the Windows installer opens the port for private and domain networks only. On Linux use
  `--open-firewall` or open the port yourself.
- **All settings:** [docs/deployment.md](deployment.md).

## Troubleshooting

| Problem | What to do |
|---|---|
| "Python 3.10 or newer was not found" | Install it for all users (Windows: python.org, tick *Install for all users*; macOS: `brew install python`) and run again. |
| The server does not answer after install | Look at the log (see above). A port already in use? Run again with `--port` / `-Port`. |
| Can open it on the server but not from a screen | The firewall: open the port (`--open-firewall`, or the Windows rule *AiSignX server*; it only covers private and domain networks, so set the network profile to *Private*). |
| "No Edge/Chrome" or "ffmpeg is missing" notes | Optional. Install them later and restart the service. |
| An upgrade went wrong | The old program is in `app.prev` and the database backup is in the data folder's `backups`. Swap the folders back and restore (docs/backup.md). |

## What has been tested

The Windows script was run on Windows 11 for everything that does not need administrator rights (copy, Python
environment, database migrations, backup before upgrade, starting the installed server). Registering the
Scheduled Task, the firewall rule and the folder permissions need elevation and were reviewed but **not yet run**.
The Linux and macOS script was syntax-checked and its file copy tested, but has **not been run on a real Linux or
macOS machine**. Please try it on a spare machine first and report what you see.
