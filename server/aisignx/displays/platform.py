"""What kind of screen a display is (Windows PC, Android, Linux, a plain browser), from what its player reported, and the wording the
settings page uses for it. The same switch means different things on different systems, so each gets its own words."""
from __future__ import annotations

from . import options

_COMMON_AGENT = ("Restart the player", "Collect logs")

WORDS = {
    "windows": {
        "label": "Windows",
        "intro": "This screen is a Windows PC running the AiSignX Player.",
        "lock": "Lock the player (kiosk)",
        "lock_help": "The player cannot be closed or left without the unlock PIN. (A Windows kiosk is best a standard account with the player as its shell; see the run modes on the display page.)",
        "pin_help": "The PIN guards leaving the player on this PC.",
        "watchdog": "Restart the player if it crashes or stops responding",
        "watchdog_help": "The player's own watchdog (and the privileged agent, when installed). Off by default.",
        "restart": 'Allow "Restart the player" from this page',
        "logs": 'Allow "Collect logs" from this page',
        "reboot": 'Allow "Restart the PC" from this page (signed command)',
        "startup": "Allow changing how the player starts: application, auto-start or shell (signed command)",
        "update": "Allow installing player updates from this page (signed command)",
        "agent_help": "Reboot, startup and update are signed commands: the privileged agent checks this server's signature, and the installer's signature, before it acts. Each has its own switch because each is more serious.",
    },
    "android": {
        "label": "Android",
        "intro": "This screen is an Android device (tablet, phone or TV box) running the AiSignX app.",
        "lock": "Lock the device to the app (kiosk)",
        "lock_help": "Android lock-task mode: no way out of the app without the unlock PIN. Full lock-down (hidden status bar, no Home or Recents, no factory reset button) needs the app to be Device Owner; without it the box uses Home-app mode and screen pinning.",
        "pin_help": "The PIN guards leaving the app on this device.",
        "watchdog": "Restart the app if it crashes or stops responding",
        "watchdog_help": "Needs the app's watchdog; on a Device Owner device it also survives a reboot. Off by default.",
        "restart": 'Allow "Restart the app" from this page',
        "logs": 'Allow "Collect logs" from this page',
        "reboot": 'Allow "Reboot the device" from this page (Device Owner only, signed command)',
        "startup": "Allow changing how the app starts: Home app and start on boot (signed command)",
        "update": "Allow installing app updates from this page (signed command; silent only with Device Owner)",
        "agent_help": "Reboot, startup and update are signed commands: the app checks this server's signature before it acts. Without Device Owner, Android asks a person to confirm an update.",
    },
    "linux": {
        "label": "Linux",
        "intro": "This screen is a Linux machine (a browser or the AiSignX Player app).",
        "lock": "Lock the player (kiosk)",
        "lock_help": "Only an app can lock itself; a plain browser cannot. For a browser kiosk lock the desktop session instead (see the docs).",
        "pin_help": "The PIN guards leaving the player app, when there is one.",
        "watchdog": "Restart the player if it stops responding",
        "watchdog_help": "Supported by the player app only. Off by default.",
        "restart": 'Allow "Restart the player" from this page',
        "logs": 'Allow "Collect logs" from this page',
        "reboot": 'Allow "Reboot the machine" from this page (signed command)',
        "startup": "Allow changing the startup settings from this page (signed command)",
        "update": "Allow installing player updates from this page (signed command)",
        "agent_help": "Linux support is best effort: the player app reports which of these it can do, and the display page only offers those.",
    },
    "browser": {
        "label": "Browser",
        "intro": "This screen is a web browser showing the player page. A browser cannot be locked or controlled from here; use the Windows or Android app for that.",
        "lock": "Lock player (apps only)",
        "lock_help": "Has no effect in a plain browser.",
        "pin_help": "The PIN only matters in the Windows and Android apps.",
        "watchdog": "Watchdog (apps only)",
        "watchdog_help": "Has no effect in a plain browser.",
        "restart": 'Allow "Restart the player" (apps only)',
        "logs": 'Allow "Collect logs" (apps only)',
        "reboot": "Allow rebooting the device (apps only, signed command)",
        "startup": "Allow changing the startup settings (apps only, signed command)",
        "update": "Allow pushing app updates (apps only, signed command)",
        "agent_help": "These switches take effect once this screen runs the Windows or Android app.",
    },
    "unknown": {
        "label": "Device",
        "intro": "This screen has not reported what it is yet (it appears after its first heartbeat). Wording for every system is shown until then.",
        "lock": "Lock player",
        "lock_help": "Windows and Android apps only: no way out without the unlock PIN (Android: lock task mode; full lock-down needs Device Owner). A plain browser cannot be locked.",
        "pin_help": "The PIN guards leaving the Windows and Android apps.",
        "watchdog": "Watchdog: restart the player when it crashes or hangs",
        "watchdog_help": "A small helper in the Windows and Android apps. Off by default.",
        "restart": 'Allow "Restart the player" from this page',
        "logs": 'Allow "Collect logs" from this page',
        "reboot": 'Allow "Reboot the device" from this page (signed command)',
        "startup": "Allow changing the startup settings from this page (signed command)",
        "update": "Allow pushing app updates from this page (signed command)",
        "agent_help": "Reboot, startup and update are signed commands: the app checks the server's signature before it acts.",
    },
}


def of_diag(diag: dict | None) -> str:
    """"windows" | "android" | "linux" | "browser" | "unknown"."""
    if not isinstance(diag, dict) or not diag:
        return "unknown"
    shell = diag.get("shell") if isinstance(diag.get("shell"), dict) else {}
    ua = str(diag.get("ua") or "")
    name = str(shell.get("name") or "")
    if name == "android" or "Android" in ua:
        return "android"
    if "Windows" in ua:
        return "windows" if name else "browser"
    if name == "electron":
        return "linux" if ("Linux" in ua or "X11" in ua) else "windows"
    if "Linux" in ua or "X11" in ua or "CrOS" in ua:
        return "linux" if name else "browser"
    return "browser" if ua else "unknown"


def of_display(display) -> str:
    return of_diag(options.parse_diag(display.diagnostics))


def words(key: str) -> dict:
    return WORDS.get(key, WORDS["unknown"])
