"""Work out what an uploaded file is and which version it is, so nobody has to type them.

Android apps (.apk) carry their package name and version inside the file (the binary AndroidManifest), which is
read exactly. Other installers are matched by name: electron-builder names the Windows installer
"AiSignX Player Setup 3.1.0.exe", and the PowerPoint add-in zip carries its version too.
"""
import re
import struct
import zipfile
from pathlib import Path

_VERSION = re.compile(r"(?<![\d.])(\d+\.\d+(?:\.\d+){0,2})(?![\d])")
_TYPE_STRING = 0x03


def _read_string(data: bytes, base: int, offset: int, utf8: bool) -> str:
    pos = base + offset
    if utf8:
        n = data[pos]
        pos += 2 if n & 0x80 else 1                    # characters length (1 or 2 bytes)
        n = data[pos]
        if n & 0x80:
            n = ((n & 0x7F) << 8) | data[pos + 1]
            pos += 2
        else:
            pos += 1
        return data[pos:pos + n].decode("utf-8", "replace")
    n = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    if n & 0x8000:
        n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0]
        pos += 2
    return data[pos:pos + n * 2].decode("utf-16-le", "replace")


def manifest_attrs(manifest: bytes) -> dict | None:
    """The attributes of the <manifest> element of a binary AndroidManifest.xml (strings and integers), or None."""
    try:
        if struct.unpack_from("<H", manifest, 0)[0] != 0x0003:
            return None
        pos = struct.unpack_from("<H", manifest, 2)[0]
        strings: list[str] = []
        while pos + 8 <= len(manifest):
            ctype, hsize, size = struct.unpack_from("<HHI", manifest, pos)
            if size < 8:
                return None
            if ctype == 0x0001:                                    # string pool
                count, _styles, flags, starts = struct.unpack_from("<IIII", manifest, pos + 8)
                utf8 = bool(flags & 0x100)
                offsets = struct.unpack_from(f"<{count}I", manifest, pos + hsize)
                strings = [_read_string(manifest, pos + starts, o, utf8) for o in offsets]
            elif ctype == 0x0102:                                  # start of an element
                name_i = struct.unpack_from("<i", manifest, pos + 20)[0]
                if 0 <= name_i < len(strings) and strings[name_i] == "manifest":
                    astart, asize, acount = struct.unpack_from("<HHH", manifest, pos + 24)
                    out = {}
                    for i in range(acount):
                        a = pos + 16 + astart + i * asize
                        _ns, an, raw = struct.unpack_from("<iii", manifest, a)
                        _sz, _r0, dtype, data = struct.unpack_from("<HBBI", manifest, a + 12)
                        if not 0 <= an < len(strings):
                            continue
                        if dtype == _TYPE_STRING and 0 <= raw < len(strings):
                            out[strings[an]] = strings[raw]
                        elif raw >= 0 and raw < len(strings) and dtype == 0:
                            out[strings[an]] = strings[raw]
                        else:
                            out[strings[an]] = data
                    return out
            pos += size
    except (struct.error, IndexError):
        return None
    return None


def apk_info(path: Path) -> dict | None:
    """{"package", "version", "code"} of an Android app, or None if the file is not one."""
    try:
        with zipfile.ZipFile(path) as z:
            attrs = manifest_attrs(z.read("AndroidManifest.xml"))
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    if not attrs or "package" not in attrs:
        return None
    return {"package": str(attrs["package"]), "version": str(attrs.get("versionName") or ""),
            "code": attrs.get("versionCode") if isinstance(attrs.get("versionCode"), int) else None}


def version_from_name(filename: str) -> str:
    found = _VERSION.findall(filename or "")
    return found[-1] if found else ""


def guess(filename: str, path: Path | None = None) -> tuple[str, str]:
    """(kind, version) for an uploaded file; kind is one of models.download.KINDS, version may be ''."""
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == "apk":
        info = apk_info(path) if path is not None else None
        return "android-player", (info or {}).get("version") or version_from_name(filename)
    if ext == "cer" or (ext in ("zip", "ps1") and "agent" in name):  # setup files of the privileged Windows agent
        return "windows-agent", version_from_name(filename)
    if ext in ("exe", "msi"):
        return ("windows-player" if "player" in name or "aisignx" in name else "other"), version_from_name(filename)
    if ext == "zip" and ("powerpoint" in name or "addin" in name or "add-in" in name):
        return "powerpoint-addin", version_from_name(filename)
    return "other", version_from_name(filename)
