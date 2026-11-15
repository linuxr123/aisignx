"""Update check: parsing, comparison, storage, banner, and signature verification."""
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aisignx.models import Setting
from aisignx.updates import service

REPO = "linuxr123/aisignx"


def release(tag="v3.1.0", **kw):
    d = {"tag_name": tag, "draft": False, "prerelease": False, "body": "Notes <b>here</b>",
         "html_url": f"https://github.com/{REPO}/releases/tag/{tag}", "published_at": "2026-12-01T00:00:00Z",
         "assets": [{"name": "a.zip", "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/a.zip", "size": 5},
                    {"name": "evil.zip", "browser_download_url": "https://evil.example/x.zip", "size": 1}]}
    d.update(kw)
    return d


def test_version_compare():
    assert service.is_newer("v3.0.1", "3.0.0.dev0")
    assert not service.is_newer("3.0.0.dev0", "3.0.0.dev0")
    assert not service.is_newer("2.9", "3.0.0")
    assert not service.is_newer("banana", "3.0.0")


def test_parse_release_filters_untrusted_parts():
    r = service.parse_release(release(html_url="https://evil.example/x"), REPO)
    assert r.version == "3.1.0" and r.url == f"https://github.com/{REPO}/releases/tag/v3.1.0"
    assert [a["name"] for a in r.assets] == ["a.zip"]
    assert service.parse_release(release(prerelease=True), REPO) is None
    assert service.parse_release(release(draft=True), REPO) is None
    assert service.parse_release(release(tag="nightly"), REPO) is None


def test_check_stores_result_and_banner(admin_client, app):
    with app.app_context():
        assert "newer" in service.check(lambda repo: release())
        st = service.status()
        assert st["available"] and st["latest"] == "3.1.0"
    page = admin_client.get("/dashboard").data
    assert b"3.1.0 is available" in page
    assert b"Notes &lt;b&gt;here" in admin_client.get("/updates/").data  # escaped, not HTML


def test_check_failure_is_recorded_not_raised(admin_client, app):
    def boom(repo):
        raise OSError("offline")
    with app.app_context():
        assert "could not reach" in service.check(boom)
        assert "could not reach" in service.status()["error"]


def test_disabled_check_does_nothing(admin_client, app):
    with app.app_context():
        Setting.set("updates.enabled", "0")
        from aisignx.extensions import db
        db.session.commit()
        assert service.check_job() == "update check is off"


def test_updates_page_admin_only(configured, client):
    assert client.get("/updates/").status_code in (302, 401)


def test_signature_verification():
    key = Ed25519PrivateKey.generate()
    data = b"release zip bytes"
    sig = base64.b64encode(key.sign(data)).decode()
    pub = key.public_key()
    assert service.verify(data, sig, pub)
    assert not service.verify(data + b"x", sig, pub)
    assert not service.verify(data, "not base64!!", pub)
    other = Ed25519PrivateKey.generate().public_key()
    assert not service.verify(data, sig, other)
    assert not service.verify(data, sig, None) or service.public_key() is not None
