"""Android QR provisioning: the payload, the time-limited app link, and the pre-approved pairing."""
import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.models import Display, Download, PairingCode
from aisignx.provision import service

from .conftest import login, make_user


def _apk(package="com.aisignx.player", wide=True):
    """A zip that looks enough like an APK: a manifest whose string pool holds the package name."""
    buf = io.BytesIO()
    name = package.encode("utf-16-le") + b"\x00\x00" if wide else package.encode("ascii") + b"\x00"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00" + b"x" * 40 + name + b"x" * 40)
        z.writestr("classes.dex", b"dex" * 50)
    return buf.getvalue()


def _download(app, package="com.aisignx.player", version="3.0.2", **kw):
    from aisignx.downloads.routes import downloads_dir
    data = _apk(package, **kw)
    with app.app_context():
        name = "apk" + hashlib.sha256(data).hexdigest()[:10]
        (downloads_dir() / name).write_bytes(data)
        row = Download(kind="android-player", version=version, filename="AiSignX-Player.apk", stored_name=name,
                       size=len(data), sha256=hashlib.sha256(data).hexdigest())
        db.session.add(row)
        db.session.commit()
        return row.id, data


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _csrf(client, path="/provision/android"):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def test_package_detection_release_debug_and_encodings(tmp_path):
    for pkg in ("com.aisignx.player", "com.aisignx.player.debug"):
        for wide in (True, False):
            f = tmp_path / "a.apk"
            f.write_bytes(_apk(pkg, wide=wide))
            assert service.detect_package(f) == pkg
    f = tmp_path / "b.apk"
    f.write_bytes(_apk("org.example.other"))
    assert service.detect_package(f) is None
    f.write_bytes(b"not a zip")
    assert service.detect_package(f) is None
    real = Path(__file__).resolve().parents[2] / "clients/android-client/app/build/outputs/apk/debug/app-debug.apk"
    if real.exists():
        assert service.detect_package(real) == "com.aisignx.player.debug"


def test_payload_has_what_android_needs(app):
    did, data = _download(app)
    with app.test_request_context():
        row = db.session.get(Download, did)
        p = service.build_payload(base_url="http://192.168.1.50:5000", download=row, package="com.aisignx.player",
                                  pairing_secret="SECRET", wifi={"ssid": "Office", "security": "WPA", "password": "pw"},
                                  timezone="America/New_York")
    k = "android.app.extra.PROVISIONING_"
    assert p[k + "DEVICE_ADMIN_COMPONENT_NAME"] == "com.aisignx.player/com.aisignx.player.AisignxDeviceAdminReceiver"
    assert p[k + "DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION"].startswith("http://192.168.1.50:5000/provision/apk/")
    want = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
    assert p[k + "DEVICE_ADMIN_PACKAGE_CHECKSUM"] == want and "=" not in want
    from aisignx.displays import signing
    with app.app_context():
        key = signing.public_key_b64()
    assert p[k + "ADMIN_EXTRAS_BUNDLE"] == {"server_url": "http://192.168.1.50:5000", "pairing_secret": "SECRET",
                                           "command_key": key}
    assert p[k + "WIFI_SSID"] == "Office" and p[k + "WIFI_PASSWORD"] == "pw" and p[k + "TIME_ZONE"] == "America/New_York"
    json.dumps(p)                                                   # must be JSON for the QR code
    svg = service.qr_svg(p)
    assert "<svg" in svg and 'viewBox="0 0 ' in svg                      # scales to fit its box
    assert " width=" not in svg.split(">")[0] and 'fill="#fff"' in svg        # solid white background, no fixed size
    assert len(p[k + "DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION"]) < 110    # a short link keeps the code easy to scan


def test_open_wifi_sends_no_password(app):
    did, _ = _download(app)
    with app.test_request_context():
        p = service.build_payload(base_url="http://x:1", download=db.session.get(Download, did),
                                  package="com.aisignx.player", pairing_secret="s",
                                  wifi={"ssid": "Guest", "security": "NONE", "password": "ignored"}, timezone=None)
    assert "android.app.extra.PROVISIONING_WIFI_PASSWORD" not in p


def test_apk_link_serves_only_the_signed_file_and_expires(app, client, configured):
    did, data = _download(app)
    with app.app_context():
        row = db.session.get(Download, did)
        token = service.apk_token(row.id, row.sha256)
    got = client.get(f"/provision/apk/{token}")
    assert got.status_code == 200 and got.data == data
    assert got.mimetype == "application/vnd.android.package-archive"
    assert client.get("/provision/apk/not-a-token").status_code == 404
    assert client.get(f"/provision/apk/{token[:-3]}abc").status_code == 404           # tampered
    with app.app_context():
        gone = service.apk_token(999999)                                                 # no such download
    assert client.get(f"/provision/apk/{gone}").status_code == 404
    service.LINK_MAX_AGE_S, saved = -1, service.LINK_MAX_AGE_S
    try:
        assert client.get(f"/provision/apk/{token}").status_code == 404                  # expired
    finally:
        service.LINK_MAX_AGE_S = saved


def test_page_needs_an_admin_and_an_apk(app, configured, client, admin):
    assert client.get("/provision/android").status_code in (302, 401)
    assert b"Upload the Android app" in admin.get("/provision/android").data
    _download(app, package="org.example.other")
    assert b"does not look like an AiSignX" in admin.get("/provision/android").data


def test_qr_creates_a_preapproved_display_that_the_device_can_claim(app, configured, admin, client):
    _download(app)
    r = admin.post("/provision/android", data={
        "csrf_token": _csrf(admin), "name": "Lobby tablet", "playlist": "0", "server_url": "http://192.168.1.50:5000",
        "lock": "y", "manage": "y", "wifi_ssid": "Office", "wifi_security": "WPA", "wifi_password": "pw"})
    assert r.status_code == 200 and b"<svg" in r.data and b"Scan this on the tablet" in r.data
    with app.app_context():
        d = Display.query.filter_by(name="Lobby tablet").one()
        assert d.kiosk_lock and d.agent_allow_reboot and d.agent_allow_update and d.agent_watchdog
        assert d.token_hash is None and PairingCode.query.filter_by(display_id=d.id).count() == 1
    assert b"pw</" not in r.data and b'value="pw"' not in r.data            # the Wi-Fi password is not echoed back


def test_a_bad_server_address_is_refused(app, configured, admin):
    _download(app)
    r = admin.post("/provision/android", data={"csrf_token": _csrf(admin), "name": "T", "playlist": "0",
                                               "server_url": "javascript:alert(1)", "wifi_security": "WPA"})
    assert r.status_code == 400
    assert service.clean_base_url("http://192.168.1.50:5000/signage") is None      # the apps reject a path
    assert service.clean_base_url("https://signs.example.com") == "https://signs.example.com"
    with app.app_context():
        assert Display.query.count() == 0


def test_preapproved_secret_pairs_through_the_normal_api(app, configured, client):
    from aisignx.displays import pairing
    with app.app_context():
        display, secret = pairing.preapprove("Tab", None, 3600)
        db.session.commit()
        did = display.id
    r = client.post("/api/player/v1/pair/status", json={"pairing_secret": secret})
    assert r.status_code == 200 and r.get_json()["status"] == "paired" and r.get_json()["display"]["id"] == did
    again = client.post("/api/player/v1/pair/status", json={"pairing_secret": secret})
    assert again.get_json().get("status") != "paired"                         # single use


def test_install_link_is_public_short_expires_and_admin_made(app, configured, admin, client):
    _download(app)
    page = admin.get("/provision/android").get_data(as_text=True)
    assert "Make an install link" in page and "/get/" not in page
    assert client.post("/provision/install-link").status_code in (302, 400, 401)       # not without a login
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    assert admin.post("/provision/install-link", data={"csrf_token": token}).status_code == 302
    page = admin.get("/provision/android").get_data(as_text=True)
    code = re.search(r"/get/([A-Z0-9]{8})", page).group(1)
    got = client.get(f"/get/{code}")                                                    # no login needed
    assert got.status_code == 200 and b"Download the app" in got.data and b"/provision/apk/" in got.data
    assert b"pairing_secret" not in got.data and b"command_key" not in got.data
    assert client.get("/get/AAAAAAAA").status_code == 404 and client.get(f"/get/{code[:-1]}X").status_code == 404
    assert client.get(f"/get/{code.lower()}").status_code == 200                       # typing on a remote: case does not matter
    href = re.search(r'href="(/provision/apk/[^"]+)"', got.get_data(as_text=True)).group(1)
    assert client.get(href).status_code == 200                                         # and the download itself works
    # a new link replaces the old one
    admin.post("/provision/install-link", data={"csrf_token": token})
    assert client.get(f"/get/{code}").status_code == 404
    # expiry
    from aisignx.models import Setting
    from aisignx.timeutil import utcnow
    from datetime import timedelta
    with app.app_context():
        newcode = Setting.get("install_link.code")
        Setting.set_datetime("install_link.expires", utcnow() - timedelta(seconds=1))
        db.session.commit()
    assert client.get(f"/get/{newcode}").status_code == 404


def test_install_page_without_an_app_says_so(app, configured, client):
    from aisignx.provision import service
    with app.app_context():
        code = service.make_install_link()
    assert b"no android app" in client.get(f"/get/{code}").data.lower()


def test_tv_script_is_gated_checked_and_free_of_secrets(app, configured, admin, client):
    import shutil
    import subprocess
    from aisignx.provision import tvscript
    did, data = _download(app)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', admin.get("/provision/android").get_data(as_text=True)).group(1)
    admin.post("/provision/install-link", data={"csrf_token": token})
    page = admin.get("/provision/android").get_data(as_text=True)
    assert "Download the PC script" in page and "Install-AiSignX-on-Android-TV.ps1" in page
    code = re.search(r"/get/([A-Z0-9]{8})", page).group(1)
    assert client.get("/get/AAAAAAAA/tv.ps1").status_code == 404                       # only the right code
    r = client.get(f"/get/{code}/tv.ps1")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    text = r.get_data(as_text=True)
    assert hashlib.sha256(data).hexdigest() in text                                    # it checks the app against this
    assert "/provision/apk/" in text and "com.aisignx.player" in text
    assert "adb" in text and "pairing_secret" not in text and "command_key" not in text and "secret" not in text.lower().replace("secrets", "")
    assert "\r\n" in text and "@@" not in text                                         # CRLF, every placeholder filled
    ps = shutil.which("powershell")
    if ps:                                                                             # it must be valid PowerShell
        path = Path(app.instance_path) / "tv.ps1"
        path.write_text(text, encoding="utf-8")
        out = subprocess.run([ps, "-NoProfile", "-Command",
                              f"$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$e);$e.Count"],
                             capture_output=True, text=True, timeout=60)
        assert out.stdout.strip() == "0", out.stdout + out.stderr


def test_tv_script_refuses_unsafe_values():
    from aisignx.provision import tvscript
    ok = dict(server="http://192.168.1.19:5000", apk_url="http://192.168.1.19:5000/provision/apk/abc.def-ghi_jk",
              apk_sha256="a" * 64, package="com.aisignx.player", version="3.0.5")
    assert "192.168.1.19" in tvscript.build(**ok)
    for bad in ({"server": "http://x'; Remove-Item C:\\ -Recurse #"}, {"apk_url": "http://x/`whoami`"}, {"apk_sha256": "zz"},
                {"package": "evil.package"}, {"server": "http://a b"}):
        with pytest.raises(ValueError):
            tvscript.build(**{**ok, **bad})
    text = tvscript.build(**{**ok, "version": "3.0.5'; calc"})                       # the version is reduced to safe characters
    assert "'; calc" not in text and "3.0.5calc" in text
