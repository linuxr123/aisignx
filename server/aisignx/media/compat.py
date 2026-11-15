"""What a video's technical facts mean for playback on typical signage players.

Pure functions (no I/O) so they are easy to test. The facts come from probe.video_facts().
Players differ, so these are warnings, never refusals: a screen that can decode a file
smoothly simply plays it. See also the player's own check (static/player/device.js), which asks
the browser whether this screen can decode each video smoothly.
"""
from __future__ import annotations

MAX_COMFORT_PIXELS = 1920 * 1088     # Full HD; larger frames are heavy for TV boxes
MAX_COMFORT_BITRATE = 20_000_000     # bits per second
MAX_COMFORT_FPS = 60.0

# Profile names ffprobe prints -> (profile_idc) for the avc1 codec string.
_H264_PROFILES = {"constrained baseline": 66, "baseline": 66, "main": 77, "extended": 88,
                  "high": 100, "high 10": 110, "high 4:2:2": 122, "high 4:4:4 predictive": 244}
_HEVC_PROFILES = {"main": 1, "main 10": 2, "main still picture": 3}


def codec_string(facts: dict | None) -> str | None:
    """The RFC 6381 codec string for MediaCapabilities, e.g. "avc1.640033"; None if unknown."""
    if not facts:
        return None
    codec = str(facts.get("codec") or "").lower()
    level = facts.get("level")
    profile = str(facts.get("profile") or "").lower()
    if codec == "h264" and isinstance(level, int) and level > 0:
        idc = _H264_PROFILES.get(profile)
        if idc is None:
            return None
        return f"avc1.{idc:02x}00{level:02x}"
    if codec == "hevc" and isinstance(level, int) and level > 0:
        pid = _HEVC_PROFILES.get(profile)
        return f"hvc1.{pid}.6.L{level}.B0" if pid else None
    if codec == "vp9":
        return "vp09.00.41.08"
    if codec == "av1":
        return "av01.0.08M.08"
    return None


def _mbps(bits: int) -> str:
    return f"{bits / 1_000_000:.0f} Mbps"


def warnings(facts: dict | None, width: int | None = None, height: int | None = None) -> list[str]:
    """Short, plain-language reasons this video may not play smoothly on a TV box or small
    player. Empty when it looks fine or when nothing is known."""
    if not facts:
        return []
    out = []
    w = width or facts.get("width") or 0
    h = height or facts.get("height") or 0
    codec = str(facts.get("codec") or "").lower()
    if codec and codec != "h264":
        out.append(f"{codec.upper()} video: many players cannot decode it in hardware. "
                   "H.264 plays everywhere.")
    if w and h and w * h > MAX_COMFORT_PIXELS:
        out.append(f"{w}×{h} is larger than most screens. A 1080p copy looks the same on a "
                   "1080p TV and plays much more smoothly.")
    bitrate = facts.get("bitrate")
    if isinstance(bitrate, int) and bitrate > MAX_COMFORT_BITRATE:
        out.append(f"Very high bitrate ({_mbps(bitrate)}). Around 8-12 Mbps is plenty for 1080p.")
    fps = facts.get("fps")
    if isinstance(fps, (int, float)) and fps > MAX_COMFORT_FPS:
        out.append(f"{fps:.0f} frames per second is more than most players can decode.")
    pix = str(facts.get("pix_fmt") or "")
    if pix and pix != "yuv420p":
        out.append(f"Pixel format {pix} (10-bit or 4:2:2/4:4:4) is not supported by most hardware decoders.")
    return out


def is_heavy(facts: dict | None, width: int | None = None, height: int | None = None) -> bool:
    return bool(warnings(facts, width, height))
