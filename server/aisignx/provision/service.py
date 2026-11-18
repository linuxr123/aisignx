"""Build the Android Enterprise provisioning QR for a factory-reset device (docs/android-kiosk.md).

On a reset device, tapping the welcome screen six times opens a QR scanner. The code carries: where to
download the AiSignX app (this server, with a time-limited link), the SHA-256 of that file (the device
refuses a different one), which component to make the device owner, optional Wi-Fi, and an extras bundle for
the app itself: this server's address and a one-time pairing secret, so the screen appears in the admin
already paired.
"""
import base64
import hashlib
import json
import re
import socket
import zipfile
from pathlib import Path

from flask import current_app, url_for

from ..displays import signing
from itsdangerous import BadData, URLSafeTimedSerializer

PACKAGES = ("com.aisignx.player", "com.aisignx.player.debug")
RECEIVER = "com.aisignx.player.AisignxDeviceAdminReceiver"
LINK_MAX_AGE_S = 3 * 86400
_EXTRA = "android.app.extra.PROVISIONING_"
_HOST_OK = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+$")  # no path: the apps reject one


def detect_package(path: Path) -> str | None:
    """Which AiSignX package an APK is (release or debug), read from its binary manifest. The package name is
    stored in the manifest's string pool, as UTF-16 (usual) or UTF-8. None if it is neither."""
    try:
        with zipfile.ZipFile(path) as z:
            manifest = z.read("AndroidManifest.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    for name in sorted(PACKAGES, key=len, reverse=True):  # the longer (debug) name contains the shorter one
        if name.encode("utf-16-le") + b"\x00\x00" in manifest or name.encode("ascii") + b"\x00" in manifest:
            return name
    return None


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="provision-apk")


def apk_token(download_id: int, sha256: str | None = None) -> str:
    """A short, signed, time-limited link token. It names the download by id only (a new upload gets a new id);
    the device checks the file against the checksum carried in the QR code itself. Short matters: every
    character makes the QR code denser and harder for a tablet camera to read."""
    return _serializer().dumps({"d": int(download_id)})


def read_token(token: str):
    """{"d": id, "h": sha256} or None when the link is bad or expired."""
    try:
        data = _serializer().loads(token, max_age=LINK_MAX_AGE_S)
    except BadData:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("d"), int) else None


def lan_address(port: int) -> str:
    """A best guess at this computer's LAN address, for when the admin is looking at the page as localhost."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; it just picks the outgoing interface
            return f"http://{s.getsockname()[0]}:{port}"
    except OSError:
        return f"http://localhost:{port}"


def clean_base_url(value: str) -> str | None:
    value = (value or "").strip().rstrip("/")
    return value if _HOST_OK.match(value) else None


def checksum_b64url(sha256_hex: str) -> str:
    return base64.urlsafe_b64encode(bytes.fromhex(sha256_hex)).decode("ascii").rstrip("=")


def build_payload(*, base_url: str, download, package: str, pairing_secret: str, wifi: dict | None,
                  timezone: str | None) -> dict:
    link = f"{base_url}{url_for('provision.apk', token=apk_token(download.id, download.sha256))}"
    payload = {
        f"{_EXTRA}DEVICE_ADMIN_COMPONENT_NAME": f"{package}/{RECEIVER}",
        f"{_EXTRA}DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION": link,
        f"{_EXTRA}DEVICE_ADMIN_PACKAGE_CHECKSUM": checksum_b64url(download.sha256),
        f"{_EXTRA}SKIP_ENCRYPTION": True,
        f"{_EXTRA}LEAVE_ALL_SYSTEM_APPS_ENABLED": True,
        # command_key: the apps pin it now, so the first contact over plain http cannot be tampered with.
        f"{_EXTRA}ADMIN_EXTRAS_BUNDLE": {"server_url": base_url, "pairing_secret": pairing_secret,
                                         "command_key": signing.public_key_b64()},
    }
    if timezone:
        payload[f"{_EXTRA}TIME_ZONE"] = timezone
    if wifi and wifi.get("ssid"):
        payload[f"{_EXTRA}WIFI_SSID"] = wifi["ssid"]
        payload[f"{_EXTRA}WIFI_SECURITY_TYPE"] = wifi.get("security", "WPA")
        if wifi.get("password") and wifi.get("security", "WPA") != "NONE":
            payload[f"{_EXTRA}WIFI_PASSWORD"] = wifi["password"]
    return payload


# --- The install link: for phones that are already set up and for Android TV boxes -------------------------
# They cannot be provisioned by QR (that needs a factory reset), so they install the app like any other and
# pair with a short code. The link opens a small public page with the download button and the server address.
# It carries no secret (the app is public code) and works for a limited time; making a new one replaces it.
LINK_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LINK_LENGTH = 8
LINK_TTL_S = 7 * 86400


def make_install_link() -> str:
    import secrets as _secrets
    from datetime import timedelta

    from ..extensions import db
    from ..models import Setting
    from ..timeutil import utcnow

    code = "".join(_secrets.choice(LINK_ALPHABET) for _ in range(LINK_LENGTH))
    Setting.set("install_link.code", code)
    Setting.set_datetime("install_link.expires", utcnow() + timedelta(seconds=LINK_TTL_S))
    db.session.commit()
    return code


def install_link_valid(code: str) -> bool:
    import hmac

    from ..models import Setting
    from ..timeutil import utcnow

    stored = Setting.get("install_link.code") or ""
    expires = Setting.get_datetime("install_link.expires")
    return bool(stored) and hmac.compare_digest(stored, str(code or "").upper()) and expires is not None and expires > utcnow()


def qr_svg_text(text: str) -> str:
    return _svg(text)


def qr_svg(payload: dict) -> str:
    return _svg(json.dumps(payload, separators=(",", ":")))


def _svg(text: str) -> str:
    import segno

    code = segno.make(text, error="l", micro=False)
    from io import BytesIO

    buf = BytesIO()
    # Solid white background and black modules, whatever the page theme is (a transparent code on a dark page
    # cannot be scanned), a 4-module quiet zone, and a viewBox so CSS can scale it to fit its box.
    code.save(buf, kind="svg", scale=4, border=4, dark="#000000", light="#ffffff", xmldecl=False, svgns=True,
              nl=False)
    svg = buf.getvalue().decode("utf-8")
    m = re.search(r'<svg[^>]*?width="(\d+)" height="(\d+)"', svg)
    if m:
        w, h = m.groups()
        svg = re.sub(r' width="\d+" height="\d+"', "", svg, count=1)
        svg = svg.replace("<svg ", f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Provisioning QR code" ', 1)
    return svg
