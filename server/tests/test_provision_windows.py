"""'Set up a Windows PC': the page, the one-step script, the file links and the run mode that follows the agent."""
import base64
import datetime
import hashlib
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from aisignx.displays import agent, enroll
from aisignx.extensions import db
from aisignx.models import DeviceCommand, Display, Download
from aisignx.player import agent_api
from aisignx.provision import winscript

from .conftest import bearer, login, make_user

API = "/api/player/v1"


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    agent_api.reset_rate_limit()


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _cert_der():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AiSignX test signing")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime(2024, 1, 1))
            .not_valid_after(datetime.datetime(2034, 1, 1))
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
            .sign(key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    return der, hashlib.sha1(der).hexdigest().upper()


def _add(app, kind, filename, data, version=""):
    from aisignx.downloads.routes import downloads_dir
    with app.app_context():
        name = "w" + hashlib.sha256(data + filename.encode()).hexdigest()[:12]
        (downloads_dir() / name).write_bytes(data)
        row = Download(kind=kind, version=version, filename=filename, stored_name=name, size=len(data),
                       sha256=hashlib.sha256(data).hexdigest())
        db.session.add(row)
        db.session.commit()
        return row.id


def _upload_all(app):
    der, thumb = _cert_der()
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("aisignx-agent.exe", b"x" * 50)
    ids = {"player": _add(app, "windows-player", "AiSignX Player Setup 3.3.0.exe", b"MZ" + b"p" * 200, "3.3.0"),
           "zip": _add(app, "windows-agent", "AiSignX-Agent-3.3.0.zip", zbuf.getvalue(), "3.3.0"),
           "script": _add(app, "windows-agent", "Install-Agent.ps1", b"# agent installer\r\n", ""),
           "cert": _add(app, "windows-agent", "AiSignX-signing.cer", der, "")}
    return ids, thumb


def _csrf(client):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', client.get("/provision/windows").get_data(as_text=True)).group(1)


def _form(token, **kw):
    data = {"csrf_token": token, "name": "Front desk PC", "playlist": "0", "kiosk_user": "kiosk", "run_as": "shell",
            "server_url": "http://192.168.1.19:5000", "allow_reboot": "y", "allow_startup": "y", "allow_update": "y"}
    data.update(kw)
    return data


def test_page_needs_an_admin_and_all_four_files(app, configured, client, admin):
    assert client.get("/provision/windows").status_code in (302, 401)
    page = admin.get("/provision/windows").get_data(as_text=True)
    assert "Upload the Windows player installer" in page and "missing" in page
    _upload_all(app)
    page = admin.get("/provision/windows").get_data(as_text=True)
    assert "Make the setup script" in page and "AiSignX Player Setup 3.3.0.exe" in page


def test_download_kinds_are_detected(tmp_path):
    from aisignx.downloads import meta
    assert meta.guess("AiSignX-Agent-3.3.0.zip") == ("windows-agent", "3.3.0")
    assert meta.guess("Install-Agent.ps1") == ("windows-agent", "")
    assert meta.guess("AiSignX-signing.cer")[0] == "windows-agent"
    assert meta.guess("AiSignX Player Setup 3.3.0.exe") == ("windows-player", "3.3.0")


def test_certificate_thumbprint_der_pem_and_junk():
    der, thumb = _cert_der()
    assert winscript.certificate_thumbprint(der) == thumb
    pem = x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM)
    assert winscript.certificate_thumbprint(pem) == thumb
    assert winscript.certificate_thumbprint(b"not a certificate") is None


def test_the_script_is_made_secret_bearing_valid_and_the_secrets_work(app, configured, client, admin):
    ids, thumb = _upload_all(app)
    r = admin.post("/provision/windows", data=_form(_csrf(admin)))
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert r.headers["Content-Disposition"].endswith('Setup-AiSignX-Windows-PC.ps1"')
    text = r.get_data(as_text=True)
    assert "@@" not in text and "\r\n" in text
    for key in ("player", "zip", "script", "cert"):                                  # every file is checked against its hash
        with app.app_context():
            assert db.session.get(Download, ids[key]).sha256 in text
    assert thumb in text and "/provision/file/" in text and "kiosk" in text
    with app.app_context():
        d = Display.query.filter_by(name="Front desk PC").one()
        assert d.pending_run_as == "shell" and d.token_hash is None
        assert d.agent_allow_reboot and d.agent_allow_startup and d.agent_allow_update and d.agent_watchdog
        did = d.id
    secret = re.search(r"\$PairSecret\s*=\s*'([^']+)'", text).group(1)
    code = re.search(r"\$EnrollCode\s*=\s*'([^']+)'", text).group(1)
    # the secrets in the script really work, in the order the script uses them: claim the pairing, then enroll the agent
    claimed = client.post(f"{API}/pair/status", json={"pairing_secret": secret, "credential": "bearer"}).get_json()
    assert claimed["status"] == "paired" and claimed["token"] and claimed["display"]["id"] == did
    assert client.post(f"{API}/pair/status", json={"pairing_secret": secret}).get_json().get("status") != "paired"   # once
    pub = base64.b64encode(X25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    got = client.post(f"{API}/agent/enroll", json={"code": code, "public_key": pub, "host": "PC", "version": "1.0"})
    assert got.status_code == 200 and got.get_json()["agent_token"].startswith("agt_")
    # and the script is valid PowerShell
    ps = shutil.which("powershell")
    if ps:
        path = Path(app.instance_path) / "setup.ps1"
        path.write_text(text, encoding="utf-8")
        out = subprocess.run([ps, "-NoProfile", "-Command",
                              f"$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$e);$e.Count"],
                             capture_output=True, text=True, timeout=60)
        assert out.stdout.strip() == "0", out.stdout + out.stderr


def test_the_enrollment_code_in_the_script_lasts_hours_not_minutes(app, admin):
    _upload_all(app)
    admin.post("/provision/windows", data=_form(_csrf(admin)))
    with app.app_context():
        d = Display.query.filter_by(name="Front desk PC").one()
        left = (d.agent_enroll_expires.replace(tzinfo=datetime.timezone.utc) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        assert 3600 < left <= 2 * 3600 + 5


def test_plain_http_leaves_auto_login_off_and_is_declared(app, admin):
    _upload_all(app)
    r = admin.post("/provision/windows", data=_form(_csrf(admin), allow_autologin="y"))
    text = r.get_data(as_text=True)
    assert "$AllowAutologin = $false" in text and "$InsecureHttp = $true" in text
    r2 = admin.post("/provision/windows", data=_form(_csrf(admin), name="Secure PC", server_url="https://signs.example.com", allow_autologin="y"))
    t2 = r2.get_data(as_text=True)
    assert "$AllowAutologin = $true" in t2 and "$InsecureHttp = $false" in t2


def test_bad_account_or_address_makes_no_display(app, admin):
    _upload_all(app)
    r = admin.post("/provision/windows", data=_form(_csrf(admin), kiosk_user="x'; Remove-Item C:\\ -Recurse #"))
    assert r.status_code == 200 and "attachment" not in r.headers.get("Content-Disposition", "")
    r = admin.post("/provision/windows", data=_form(_csrf(admin), server_url="javascript:alert(1)"))
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    with app.app_context():
        assert Display.query.count() == 0


def test_the_script_builder_refuses_unsafe_values():
    ok = dict(server="http://192.168.1.19:5000", name="PC", run_text="Application", kiosk_user="kiosk",
              player={"url": "http://192.168.1.19:5000/provision/file/a.b.c", "sha256": "a" * 64},
              agent_zip={"url": "http://192.168.1.19:5000/provision/file/a.b.d", "sha256": "b" * 64},
              agent_script={"url": "http://192.168.1.19:5000/provision/file/a.b.e", "sha256": "c" * 64},
              cert={"url": "http://192.168.1.19:5000/provision/file/a.b.f", "sha256": "d" * 64},
              thumbprint="A" * 40, pair_secret="s" * 30, enroll_code="ABCD-EFGH-JKLM-NPQR",
              key_fingerprint="ab12 cd34 ef56 7890", allow_reboot=True, allow_startup=True, allow_update=True, allow_autologin=False)
    assert "192.168.1.19" in winscript.build(**ok)
    bad_cases = [{"server": "http://x'; calc #"}, {"kiosk_user": "a'b"}, {"thumbprint": "zz"}, {"pair_secret": "a b"},
                 {"enroll_code": "lower-case-code-1234"}, {"key_fingerprint": "nope"},
                 {"player": {"url": "http://h/`whoami`", "sha256": "a" * 64}}, {"cert": {"url": "http://h/x", "sha256": "xyz"}}]
    for bad in bad_cases:
        with pytest.raises(ValueError):
            winscript.build(**{**ok, **bad})


def test_file_link_serves_only_signed_unexpired_links(app, configured, client):
    from aisignx.provision import service
    did = _add(app, "windows-agent", "Install-Agent.ps1", b"# agent installer\r\n")
    with app.app_context():
        token = service.apk_token(did)
    got = client.get(f"/provision/file/{token}")
    assert got.status_code == 200 and got.data == b"# agent installer\r\n"
    assert "Install-Agent.ps1" in got.headers["Content-Disposition"]
    assert client.get("/provision/file/nonsense").status_code == 404
    assert client.get(f"/provision/file/{token[:-2]}zz").status_code == 404
    service.LINK_MAX_AGE_S, saved = -1, service.LINK_MAX_AGE_S
    try:
        assert client.get(f"/provision/file/{token}").status_code == 404
    finally:
        service.LINK_MAX_AGE_S = saved


def test_the_chosen_run_mode_goes_out_when_the_agent_reports(app, admin, client, paired_display):
    """The PC was set up with 'shell': once the enrolled agent says it can set the startup, the signed command appears."""
    did = paired_display[0]
    with app.app_context():
        d = db.session.get(Display, did)
        d.pending_run_as = "shell"
        d.agent_allow_startup = True
        d.diagnostics = json.dumps({"agent": {"version": "3.3.0", "can_restart": True}, "shell": {"name": "electron"}})
        db.session.commit()
    code = re.search(r"[A-Z2-9]{4}(?:-[A-Z2-9]{4}){3}", admin.post(f"/displays/{did}/agent-enroll/code").get_data(as_text=True)).group(0)
    pub = base64.b64encode(X25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    token = client.post(f"{API}/agent/enroll", json={"code": code, "public_key": pub, "host": "PC", "version": "1"}).get_json()["agent_token"]
    # the agent's first report says it cannot do it yet: nothing is sent, nothing is forgotten
    client.post(f"{API}/agent/diagnostics", json={"diag": {"version": "1", "state": "running", "can_set_startup": False}}, headers=bearer(token))
    with app.app_context():
        assert DeviceCommand.query.count() == 0 and db.session.get(Display, did).pending_run_as == "shell"
    client.post(f"{API}/agent/diagnostics", json={"diag": {"version": "1", "state": "running", "can_set_startup": True,
                                                            "startup": {"autostart": False, "mode": "app", "shell_capable": True}}}, headers=bearer(token))
    with app.app_context():
        cmd, = DeviceCommand.query.all()
        assert cmd.action == "set_startup" and cmd.params == {"autostart": True, "mode": "shell"}
        assert db.session.get(Display, did).pending_run_as is None                     # sent once, then forgotten
    client.post(f"{API}/agent/diagnostics", json={"diag": {"version": "1", "state": "running", "can_set_startup": True}}, headers=bearer(token))
    with app.app_context():
        assert DeviceCommand.query.count() == 1                                        # not sent again


def test_the_work_folder_is_only_locked_down_when_elevated():
    """Found by running -DownloadOnly for real: locking the folder to administrators made an unelevated run unable to
    write into its own folder."""
    text = winscript.build(server="http://192.168.1.19:5000", name="PC", run_text="Application", kiosk_user="kiosk",
                           player={"url": "http://h/a", "sha256": "a" * 64}, agent_zip={"url": "http://h/b", "sha256": "b" * 64},
                           agent_script={"url": "http://h/c", "sha256": "c" * 64}, cert={"url": "http://h/d", "sha256": "d" * 64},
                           thumbprint="A" * 40, pair_secret="s" * 30, enroll_code="ABCD-EFGH-JKLM-NPQR",
                           key_fingerprint="ab12 cd34 ef56 7890", allow_reboot=True, allow_startup=True, allow_update=True,
                           allow_autologin=False)
    assert "if ($elevated) { & icacls.exe $work" in text and "GetTempPath()" in text
    assert text.count("icacls.exe $work") == 1

