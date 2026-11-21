"""HTTPS: certificates (self-signed and bought), the nginx text, the CLI and the admin page."""
import datetime as dt
import re

import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from aisignx.https import certs, nginx


def _issued(cn="signs.example.com", days=90, ca_key=None, names=None, start=None):
    """A certificate "from a certificate authority": returns (leaf pem, key pem, ca pem)."""
    ca_key = ca_key or ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = start or dt.datetime.now(dt.timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(65537, 2048)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(2).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in (names or [cn])]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)  # noqa: E731
    keypem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem(leaf), keypem, pem(ca)


def test_names_are_checked_and_normalised():
    assert certs.clean_names("Signs.Example.com, 192.168.1.19 ;signs.example.com") == ["signs.example.com", "192.168.1.19"]
    assert certs.clean_names(["::1"]) == ["::1"]
    for bad in ("", "bad name!", "x" * 300, "-bad.example", "http://x", ",".join(f"h{i}" for i in range(25))):
        with pytest.raises(certs.CertError):
            certs.clean_names(bad)


def test_a_self_signed_certificate_has_the_names_and_the_key_matches():
    cert_pem, key_pem = certs.make_self_signed(["signs.example.lan", "192.168.1.19"])
    chain, key, problems = certs.check_pair(cert_pem, key_pem)
    info = certs.describe(chain[0])
    assert sorted(info["names"]) == ["192.168.1.19", "signs.example.lan"]
    assert info["self_signed"] and 1090 <= info["days_left"] <= 1095 and len(info["fingerprint"]) == 64 and problems == []
    assert certs.covers(info["names"], "SIGNS.example.lan") and not certs.covers(info["names"], "other.lan")


def test_a_bought_certificate_needs_its_own_key_and_a_valid_date_and_warns_without_the_chain():
    leaf, key, ca = _issued()
    chain, _, problems = certs.check_pair(leaf + ca, key)
    assert len(chain) == 2 and problems == []
    assert any("intermediate" in p for p in certs.check_pair(leaf, key)[2])
    other_key = _issued()[1]
    with pytest.raises(certs.CertError, match="does not belong"):
        certs.check_pair(leaf, other_key)
    old, oldkey, _ = _issued(days=10, start=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=100))
    with pytest.raises(certs.CertError, match="expired"):
        certs.check_pair(old, oldkey)
    with pytest.raises(certs.CertError, match="key"):
        certs.check_pair(key, key)                       # a key where the certificate belongs
    with pytest.raises(certs.CertError):
        certs.check_pair(b"junk", key)
    assert certs.covers(["*.example.com"], "signs.example.com") and not certs.covers(["*.example.com"], "a.b.example.com")


def test_a_key_with_a_passphrase_needs_it_and_is_stored_without_one(tmp_path):
    leaf, key_pem, _ = _issued()
    key = serialization.load_pem_private_key(key_pem, None)
    locked = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.BestAvailableEncryption(b"pw-123"))
    with pytest.raises(certs.CertError, match="passphrase"):
        certs.check_pair(leaf, locked)
    with pytest.raises(certs.CertError):
        certs.check_pair(leaf, locked, "wrong")
    chain, k, _ = certs.check_pair(leaf, locked, "pw-123")
    certs.store(tmp_path / "tls", chain, k)
    assert b"ENCRYPTED" not in (tmp_path / "tls" / certs.KEY_FILE).read_bytes()
    assert certs.current(tmp_path / "tls")["has_key"]


def test_nginx_text_has_what_aisignx_needs():
    t = nginx.render(names=["signs.example.com"], cert="C:\\ProgramData\\AiSignX\\tls\\server.crt",
                     key="C:/ProgramData/AiSignX/tls/server.key", max_upload_mb=2048)
    assert 'ssl_certificate "C:/ProgramData/AiSignX/tls/server.crt";' in t and "\\" not in t
    assert "listen 443 ssl;" in t and "return 301 https://$host$request_uri;" in t and "server_name signs.example.com;" in t
    assert "client_max_body_size 2048m;" in t and "proxy_request_buffering off;" in t
    events = t.split("location /api/player/v1/events", 1)[1]
    assert "proxy_buffering off;" in events and "proxy_read_timeout 3600s;" in events and "gzip off;" in events
    assert t.count("X-Forwarded-Proto $scheme") == 2 and "TLSv1.2 TLSv1.3" in t and "SSLv3" not in t
    assert "Strict-Transport-Security" not in t
    assert "Strict-Transport-Security" in nginx.render(names=["a.example"], cert="/c", key="/k", hsts=True)
    assert "return 301" not in nginx.render(names=["a.example"], cert="/c", key="/k", redirect_http=False)
    whole = nginx.render(names=["a.example"], cert="/c", key="/k", standalone=True)
    assert whole.startswith("# AiSignX nginx.conf") and "events {" in whole and "http {" in whole and whole.count("{") == whole.count("}")


def test_nothing_from_a_request_can_break_out_of_an_nginx_directive():
    for kw in ({"cert": 'x"; evil on; "'}, {"cert": "a;b"}, {"key": "a\nb"}, {"cert": "/a/../b"}, {"cert": "a{b}"}, {"key": "$x"},
               {"upstream": "127.0.0.1:5000; evil"}, {"upstream": "x:99999"}, {"upstream": "127.0.0.1"}):
        args = {"names": ["a.example"], "cert": "/c", "key": "/k"}
        args.update(kw)
        with pytest.raises(certs.CertError):
            nginx.render(**args)
    with pytest.raises(certs.CertError):
        nginx.render(names=["a.example; evil"], cert="/c", key="/k")


def test_the_cli_makes_replaces_and_describes_a_certificate(app, tmp_path):
    app.config["TLS_DIR"] = str(tmp_path / "tls")
    runner = app.test_cli_runner()
    r = runner.invoke(args=["https", "self-signed", "--name", "signs.example.lan", "--name", "192.168.1.19"])
    assert r.exit_code == 0 and "SHA-256 fingerprint" in r.output, r.output
    assert runner.invoke(args=["https", "self-signed", "--name", "x.example"]).exit_code != 0        # needs --force
    assert runner.invoke(args=["https", "self-signed", "--name", "x.example", "--force"]).exit_code == 0
    assert "x.example" in runner.invoke(args=["https", "status"]).output
    out = tmp_path / "nginx.conf"
    r = runner.invoke(args=["https", "config", "--out", str(out), "--standalone"])
    assert r.exit_code == 0 and "server_name x.example;" in out.read_text() and "Strict-Transport-Security" not in out.read_text()
    assert runner.invoke(args=["https", "config", "--cert-path", "a;b"]).exit_code != 0
    r = runner.invoke(args=["https", "config", "--hsts"])                      # not with a self-signed certificate
    assert r.exit_code != 0 and "self-signed" in r.output


def test_the_cli_imports_a_bought_certificate(app, tmp_path):
    app.config["TLS_DIR"] = str(tmp_path / "tls")
    leaf, key, ca = _issued()
    (tmp_path / "c.pem").write_bytes(leaf + ca)
    (tmp_path / "k.pem").write_bytes(key)
    r = app.test_cli_runner().invoke(args=["https", "import", "--cert", str(tmp_path / "c.pem"), "--key", str(tmp_path / "k.pem")])
    assert r.exit_code == 0 and "signs.example.com" in r.output, r.output
    (tmp_path / "k.pem").write_bytes(_issued()[1])
    assert app.test_cli_runner().invoke(args=["https", "import", "--cert", str(tmp_path / "c.pem"), "--key", str(tmp_path / "k.pem")]).exit_code != 0


def _csrf(client, path="/admin/https/"):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def test_the_page_is_admin_only_and_makes_and_imports_certificates(app, configured, admin_client, client, tmp_path):
    app.config["TLS_DIR"] = str(tmp_path / "tls")
    assert app.test_client().get("/admin/https/").status_code in (302, 401, 403)
    assert app.test_client().get("/admin/https/certificate.crt").status_code in (302, 401, 403)
    assert b"No certificate yet" in admin_client.get("/admin/https/").data
    tok = _csrf(admin_client)
    r = admin_client.post("/admin/https/self-signed", data={"csrf_token": tok, "names": "signs.example.lan"}, follow_redirects=True)
    assert r.status_code == 200 and b"signs.example.lan" in r.data and b"Self-signed" in r.data
    assert admin_client.post("/admin/https/self-signed", data={"csrf_token": tok, "names": "bad name!"}).status_code == 400
    crt = admin_client.get("/admin/https/certificate.crt")
    assert crt.status_code == 200 and b"BEGIN CERTIFICATE" in crt.data and b"PRIVATE KEY" not in crt.data
    conf = admin_client.post("/admin/https/nginx.conf", data={"csrf_token": tok, "names": "signs.example.lan", "port": "5000", "redirect": "y"})
    assert admin_client.post("/admin/https/nginx.conf", data={"csrf_token": tok, "names": "signs.example.lan", "hsts": "y"}).status_code == 400
    assert conf.status_code == 200 and b"server_name signs.example.lan;" in conf.data and b"return 301" in conf.data
    assert admin_client.post("/admin/https/nginx.conf", data={"csrf_token": tok, "names": "a.example", "cert_path": "x;y"}).status_code == 400
    leaf, key, ca = _issued()
    r = admin_client.post("/admin/https/import", data={"csrf_token": tok, "cert_text": (leaf + ca).decode(), "key_text": key.decode()},
                   follow_redirects=True)
    assert r.status_code == 200 and b"signs.example.com" in r.data and b"Issued by" in r.data
    r = admin_client.post("/admin/https/import", data={"csrf_token": tok, "cert_text": leaf.decode(), "key_text": _issued()[1].decode()})
    assert r.status_code == 400 and b"does not belong" in r.data
    body = key.decode().splitlines()[1].encode()       # a line of the real key: it is never shown again
    assert body not in r.data and body not in admin_client.get("/admin/https/").data
