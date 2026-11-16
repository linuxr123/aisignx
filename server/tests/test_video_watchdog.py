"""The frozen-video watchdog (static/player/render.js watchStep), run in Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).parent / "js"
PLAYER = Path(__file__).resolve().parents[1] / "aisignx" / "static" / "player"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_watch_step():
    out = subprocess.run(["node", str(JS / "video_watchdog.js"), str(PLAYER)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr + out.stdout
    assert out.stdout.strip().endswith("OK")
