"""The server installers copy the program but must skip only the top-level data folders.

A bare folder name in a copy filter matches at any depth: `downloads` once removed the `aisignx/downloads`
package, so a fresh install failed with "flask db upgrade failed".
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = ("downloads", "instance", "uploads", "backups", "logs", "plugin-cache", "tests", ".venv")


def test_the_windows_installer_excludes_data_folders_by_full_path():
    text = (ROOT / "scripts" / "install-server.ps1").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "robocopy.exe $serverSrc" in l)
    assert "Join-Path $serverSrc $_" in line
    assert not re.search(r"/XD\s+\.venv", line)          # the old bare-name list


def test_the_linux_installer_anchors_the_data_folder_excludes():
    text = (ROOT / "scripts" / "install-server.sh").read_text(encoding="utf-8")
    assert "--anchored" in text
    for name in EXCLUDED:
        assert f"--exclude='./{name}'" in text, name
        assert f"--exclude='{name}'" not in text, name   # unanchored: matches inside aisignx/


def test_no_excluded_name_hides_part_of_the_program():
    # What a name-only filter would have removed from the program itself:
    hidden = [p for p in (ROOT / "server" / "aisignx").rglob("*") if p.is_dir() and p.name in EXCLUDED]
    assert any(p.name == "downloads" for p in hidden)   # so the anchoring really is needed
