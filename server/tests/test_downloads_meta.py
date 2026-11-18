"""Uploads read their kind and version from the file, so nobody has to type them."""
import io
import struct
import zipfile
from pathlib import Path

import pytest

from aisignx.downloads import meta
from aisignx.extensions import db
from aisignx.models import Download



def _csrf(client):
    import re
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', client.get("/downloads/").get_data(as_text=True)).group(1)


def _axml(attrs: dict, utf8: bool = False) -> bytes:
    """A minimal binary AndroidManifest.xml: one <manifest> element with the given string/int attributes."""
    names = ["manifest"] + list(attrs)
    values = [v for v in attrs.values() if isinstance(v, str)]
    pool = names + values
    def enc(s):
        if utf8:
            b = s.encode("utf-8")
            return bytes([len(s), len(b)]) + b + b"\x00"
        return struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\x00\x00"
    blobs, offs, pos = [], [], 0
    for s in pool:
        e = enc(s)
        offs.append(pos); blobs.append(e); pos += len(e)
    body = b"".join(blobs)
    body += b"\x00" * (-len(body) % 4)
    header_size = 28
    strings_start = header_size + 4 * len(pool)
    pool_chunk = struct.pack("<HHIIIIII", 1, header_size, strings_start + len(body), len(pool), 0, 0x100 if utf8 else 0,
                             strings_start, 0) + struct.pack(f"<{len(pool)}I", *offs) + body
    attr_bytes = b""
    for key, val in attrs.items():
        name_i = pool.index(key)
        if isinstance(val, str):
            raw = pool.index(val)
            attr_bytes += struct.pack("<iiiHBBI", -1, name_i, raw, 8, 0, 0x03, raw)
        else:
            attr_bytes += struct.pack("<iiiHBBI", -1, name_i, -1, 8, 0, 0x10, val)
    elem = struct.pack("<HHIII", 0x0102, 16, 16 + 20 + len(attr_bytes), 1, 0xFFFFFFFF)
    elem += struct.pack("<ii", -1, 0) + struct.pack("<HHHHHH", 20, 20, len(attrs), 0, 0, 0) + attr_bytes
    total = 8 + len(pool_chunk) + len(elem)
    return struct.pack("<HHI", 0x0003, 8, total) + pool_chunk + elem


def _apk(path, **attrs):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", _axml(attrs, utf8=attrs.pop("_utf8", False)))
        z.writestr("classes.dex", b"d" * 50)
    return path


@pytest.mark.parametrize("utf8", [False, True])
def test_reads_package_and_version_from_a_binary_manifest(tmp_path, utf8):
    f = tmp_path / "a.apk"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("AndroidManifest.xml", _axml({"package": "com.aisignx.player", "versionName": "3.0.1",
                                                  "versionCode": 301}, utf8=utf8))
    assert meta.apk_info(f) == {"package": "com.aisignx.player", "version": "3.0.1", "code": 301}


def test_not_an_apk_gives_none(tmp_path):
    f = tmp_path / "x.apk"
    f.write_bytes(b"not a zip")
    assert meta.apk_info(f) is None
    g = tmp_path / "y.zip"
    with zipfile.ZipFile(g, "w") as z:
        z.writestr("hello.txt", "hi")
    assert meta.apk_info(g) is None
    with zipfile.ZipFile(g, "w") as z:
        z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00junk")
    assert meta.apk_info(g) is None


def test_real_debug_build_if_present():
    real = Path(__file__).resolve().parents[2] / "clients/android-client/app/build/outputs/apk/debug/app-debug.apk"
    if not real.exists():
        pytest.skip("no built APK here")
    info = meta.apk_info(real)
    assert info["package"].startswith("com.aisignx.player") and info["version"].count(".") == 2


@pytest.mark.parametrize("name,kind,version", [
    ("AiSignX Player Setup 3.1.0.exe", "windows-player", "3.1.0"),
    ("AiSignX-PowerPoint-Addin-3.0.2.zip", "powerpoint-addin", "3.0.2"),
    ("notes-2026.txt", "other", ""),
    ("Setup-v2.5.exe", "other", "2.5"),
    ("AiSignX-Player-3.0.1-debug.apk", "android-player", "3.0.1"),
])
def test_guess_from_the_file_name(name, kind, version):
    assert meta.guess(name, None) == (kind, version)


def test_upload_detects_kind_and_version(admin_client, app, tmp_path):
    f = _apk(tmp_path / "a.apk", package="com.aisignx.player", versionName="3.4.5", versionCode=345)
    token = _csrf(admin_client)
    r = admin_client.post("/downloads/upload", data={"csrf_token": token, "file": (io.BytesIO(f.read_bytes()), "whatever.apk"),
                                                     "kind": "auto", "version": "", "notes": ""},
                          content_type="multipart/form-data", follow_redirects=True)
    assert b"3.4.5" in r.data
    with app.app_context():
        row = Download.query.one()
        assert (row.kind, row.version) == ("android-player", "3.4.5")


def test_a_typed_version_wins_and_a_missing_one_is_flagged(admin_client, app):
    token = _csrf(admin_client)
    admin_client.post("/downloads/upload", data={"csrf_token": token, "file": (io.BytesIO(b"MZ" + b"x" * 50), "Tool.exe"),
                                                 "kind": "other", "version": "9.9", "notes": ""},
                      content_type="multipart/form-data")
    r = admin_client.post("/downloads/upload", data={"csrf_token": token, "file": (io.BytesIO(b"MZ" + b"y" * 50), "Thing.exe"),
                                                     "kind": "auto", "version": "", "notes": ""},
                          content_type="multipart/form-data", follow_redirects=True)
    assert b"No version found" in r.data
    with app.app_context():
        assert {(d.filename, d.version) for d in Download.query.all()} == {("Tool.exe", "9.9"), ("Thing.exe", "")}
