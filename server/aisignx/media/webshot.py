"""Screenshots of web pages using an installed Chromium-based browser (Edge, Chrome, Chromium).

No Python browser library is needed: the browser's own headless mode writes a PNG.
Set AISIGNX_BROWSER to choose a specific browser executable.
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import current_app

from . import storage

VIEWPORT = (1280, 720)

# Executable names to look for on the PATH, in order of preference.
_PATH_NAMES = (
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
    "microsoft-edge", "microsoft-edge-stable", "msedge", "chrome",
)


def _known_locations() -> list[Path]:
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        rel = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"Chromium\Application\chrome.exe")
        return [Path(root) / r for root in roots if root for r in rel]
    if sys.platform == "darwin":
        return [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    return [Path("/usr/bin/chromium"), Path("/usr/bin/google-chrome"), Path("/snap/bin/chromium")]


def find_browser(configured: str | None = None) -> str | None:
    """Return the path of a usable browser, or None."""
    if configured:
        found = shutil.which(configured) or (configured if Path(configured).is_file() else None)
        return found
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for path in _known_locations():
        if path.is_file():
            return str(path)
    return None


def browser_path() -> str | None:
    return find_browser(current_app.config.get("BROWSER_PATH"))


def capture(url: str, dest: Path, timeout: int | None = None) -> bool:
    """Save a VIEWPORT-sized PNG screenshot of url to dest. Returns True on success."""
    browser = browser_path()
    if not browser:
        return False
    timeout = timeout or current_app.config.get("WEBSHOT_TIMEOUT", 45)
    width, height = VIEWPORT
    # A throwaway profile per capture: no cookies or history carried between pages.
    with tempfile.TemporaryDirectory(prefix="profile-", dir=storage.work_dir(),
                                     ignore_cleanup_errors=True) as profile:
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--disable-sync", "--disable-background-networking", "--disable-dev-shm-usage",
            f"--user-data-dir={profile}", f"--window-size={width},{height}",
            "--virtual-time-budget=5000",  # let scripts run ~5s of page time before the shot
            f"--screenshot={dest}",
        ]
        if _running_as_root():
            cmd.append("--no-sandbox")  # Chromium refuses to start as root otherwise (e.g. Docker)
        cmd.append(url)
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            current_app.logger.warning("web screenshot failed for %s: %s", url, exc)
            return False
    return dest.exists() and dest.stat().st_size > 0


def _running_as_root() -> bool:
    return platform.system() != "Windows" and hasattr(os, "geteuid") and os.geteuid() == 0
