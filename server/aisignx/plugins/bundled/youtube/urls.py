"""YouTube links and ids: parsing what an editor types, and building the embed URL.

Accepted forms (any other host is refused):
    dQw4w9WgXcQ                                   a bare 11-character video id
    https://www.youtube.com/watch?v=ID&t=1m30s    also m.youtube.com and music.youtube.com
    https://youtu.be/ID?t=90
    https://www.youtube.com/shorts/ID   /live/ID   /embed/ID   /v/ID
    https://www.youtube-nocookie.com/embed/ID
    https://www.youtube.com/playlist?list=PL...   (playlists; also watch?v=..&list=..)
    https://www.youtube.com/channel/UC...         (live: a channel's current live stream)

The scheme may be left out ("youtu.be/ID"). The embed URL always points at
www.youtube-nocookie.com, the privacy-enhanced embed host.
"""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

EMBED_ORIGIN = "https://www.youtube-nocookie.com"
MAX_VIDEOS = 50
MAX_SECONDS = 24 * 3600

VIDEO_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LIST_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
CHANNEL_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_LANG_RE = re.compile(r"^[a-z]{2,3}$")
_HMS_RE = re.compile(r"^(?:(\d{1,2})h)?(?:(\d{1,4})m)?(?:(\d{1,6})s?)?$")
_CLOCK_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{1,4}):(\d{2})$")

WATCH_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com",
                         "music.youtube.com"})
SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
NOCOOKIE_HOSTS = frozenset({"youtube-nocookie.com", "www.youtube-nocookie.com"})
HOSTS = WATCH_HOSTS | SHORT_HOSTS | NOCOOKIE_HOSTS
_PATH_PREFIXES = ("shorts", "live", "embed", "v", "e")

# Player parameters every embed gets (autoplay and mute follow).
_BASE = (("playsinline", "1"), ("controls", "0"), ("rel", "0"), ("iv_load_policy", "3"),
         ("disablekb", "1"), ("fs", "0"))


class UrlError(ValueError):
    """Text that is not an accepted YouTube link or id (the message is for the editor)."""


def _url(text: str):
    """urlsplit() of a YouTube link, or None when the text is not a link at all.
    Raises UrlError for links to other hosts."""
    raw = text.strip()
    if "://" not in raw:
        head = raw.split("/", 1)[0].lower()
        if "." not in head or head.split(":", 1)[0].rstrip(".") not in HOSTS:
            return None
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise UrlError("This link cannot be read.") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlError("Only http and https links are allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if host not in HOSTS or parts.username or parts.password or port not in (None, 80, 443):
        raise UrlError("Only YouTube links are allowed.")
    return parts


def _query(parts) -> dict:
    return {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=False).items()}


def parse_time(text) -> int:
    """Seconds from "90", "90s", "1:30", "1:02:03", "1m30s" or "1h2m3s"."""
    raw = str(text or "").strip().lower()
    clock = _CLOCK_RE.match(raw)
    if clock:
        h, m, s = clock.groups()
        if int(s) > 59 or (h is not None and int(m) > 59):
            raise UrlError(f"{text!r} is not a time.")
        seconds = int(h or 0) * 3600 + int(m) * 60 + int(s)
    else:
        hms = _HMS_RE.match(raw)
        if not raw or not hms or not any(hms.groups()):
            raise UrlError(f"{text!r} is not a time (use seconds, m:ss or 1m30s).")
        h, m, s = (int(x or 0) for x in hms.groups())
        seconds = h * 3600 + m * 60 + s
    if seconds > MAX_SECONDS:
        raise UrlError(f"{text!r} is longer than 24 hours.")
    return seconds


def _path_id(parts) -> str | None:
    """The video id in a link's path or query, or None."""
    host = (parts.hostname or "").rstrip(".").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in SHORT_HOSTS:
        return segments[0] if segments else None
    if parts.path.rstrip("/") == "/watch":
        return _query(parts).get("v")
    if len(segments) >= 2 and segments[0] in _PATH_PREFIXES and segments[1] != "videoseries":
        return segments[1]
    return None


def parse_video(text: str) -> tuple[str, int | None, int | None]:
    """(video_id, start, end) from a link or a bare id; start/end from t=, start=, end=."""
    raw = str(text or "").strip()
    if VIDEO_RE.match(raw):
        return raw, None, None
    parts = _url(raw)
    if parts is None:
        raise UrlError("Not a YouTube link or video id.")
    video = _path_id(parts)
    if not video or not VIDEO_RE.match(video):
        raise UrlError("This YouTube link does not name a video.")
    query = _query(parts)
    start = query.get("t") or query.get("start")
    end = query.get("end")
    try:
        return (video, parse_time(start) if start else None, parse_time(end) if end else None)
    except UrlError:
        return video, None, None  # a garbled t= in a pasted link is not worth refusing


def parse_videos(text: str) -> list[tuple[str, int | None, int | None]]:
    """One video per line: "URL-or-ID [start] [end]". Blank lines and "#" lines are skipped."""
    videos = []
    for number, line in enumerate(str(text or "").splitlines(), start=1):
        tokens = line.split()
        if not tokens or tokens[0].startswith("#"):
            continue
        if len(tokens) > 3:
            raise UrlError(f"Line {number}: use \"link [start] [end]\" (start and end in "
                           "seconds or m:ss).")
        try:
            video, start, end = parse_video(tokens[0])
            if len(tokens) > 1:
                start = parse_time(tokens[1])
            if len(tokens) > 2:
                end = parse_time(tokens[2])
        except UrlError as exc:
            raise UrlError(f"Line {number}: {exc}") from None
        if start == 0:
            start = None
        if end is not None and end <= (start or 0):
            raise UrlError(f"Line {number}: the end must come after the start.")
        videos.append((video, start, end))
        if len(videos) > MAX_VIDEOS:
            raise UrlError(f"At most {MAX_VIDEOS} videos.")
    if not videos:
        raise UrlError("Add at least one video.")
    return videos


def parse_playlist(text: str) -> str:
    """A playlist id from a bare id or any link carrying list=."""
    raw = str(text or "").strip()
    if not raw:
        raise UrlError("Enter a playlist link or id.")
    if LIST_RE.match(raw) and not VIDEO_RE.match(raw):
        return raw
    parts = _url(raw)
    found = _query(parts).get("list") if parts is not None else None
    if not found or not LIST_RE.match(found):
        raise UrlError("Not a YouTube playlist link or id (it needs list=...).")
    return found


def parse_live(text: str) -> tuple[str, str]:
    """("video", id) for a live video, or ("channel", UC...) for a channel's live stream."""
    raw = str(text or "").strip()
    if not raw:
        raise UrlError("Enter a live video link, a video id or a channel id (UC...).")
    if CHANNEL_RE.match(raw):
        return "channel", raw
    if VIDEO_RE.match(raw):
        return "video", raw
    parts = _url(raw)
    if parts is None:
        raise UrlError("Not a YouTube link, video id or channel id.")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) >= 2 and segments[0] == "channel":
        if CHANNEL_RE.match(segments[1]):
            return "channel", segments[1]
        raise UrlError("This channel link has no valid channel id.")
    channel = _query(parts).get("channel")
    if channel and CHANNEL_RE.match(channel):
        return "channel", channel
    if segments and segments[0].startswith("@") or (segments[:1] in (["c"], ["user"])):
        raise UrlError("Use the channel id (it starts with UC; find it under the channel's "
                       "About > Share channel > Copy channel ID), not its @handle.")
    return "video", parse_video(raw)[0]


def _lang(locale: str) -> str | None:
    lang = str(locale or "").split("-", 1)[0].split("_", 1)[0].lower()
    return lang if _LANG_RE.match(lang) else None


def build(config: dict, locale: str = "") -> dict:
    """{"url": embed URL, "embed": {...}} for a validated config. Raises UrlError."""
    source = config.get("source") or "videos"
    mute = bool(config.get("mute", True))
    captions = bool(config.get("captions"))
    live = source == "live"
    play_to_end = bool(config.get("play_to_end", True)) and not live
    shuffle = bool(config.get("shuffle")) and not live
    params = [("autoplay", "1"), ("mute", "1" if mute else "0"), *_BASE,
              ("cc_load_policy", "1" if captions else "0")]
    lang = _lang(locale) if captions else None
    if lang:
        params += [("cc_lang_pref", lang), ("hl", lang)]
    embed = {"video_ids": [], "list": None, "channel": None, "shuffle": shuffle,
             "captions": captions, "play_to_end": play_to_end, "live": live,
             "start": None, "end": None}

    if source == "playlist":
        embed["list"] = parse_playlist(config.get("playlist"))
        path = "videoseries"
        params.append(("list", embed["list"]))
        if not play_to_end:
            params.append(("loop", "1"))
    elif live:
        kind, value = parse_live(config.get("live"))
        if kind == "channel":
            embed["channel"] = value
            path = "live_stream"
            params.append(("channel", value))
        else:
            embed["video_ids"] = [value]
            path = value
    else:
        videos = parse_videos(config.get("videos"))
        ids = [v[0] for v in videos]
        first, start, end = videos[0]
        embed.update(video_ids=ids, start=start, end=end)
        path = first
        if start:
            params.append(("start", str(start)))
        if end:
            params.append(("end", str(end)))
        if len(ids) > 1:
            params.append(("playlist", ",".join(ids[1:])))
            if not play_to_end:
                params.append(("loop", "1"))
        elif not play_to_end:
            params += [("loop", "1"), ("playlist", first)]
    return {"url": f"{EMBED_ORIGIN}/embed/{path}?{urlencode(params)}", "embed": embed}
