"""Radar sources (RainViewer, Iowa Environmental Mesonet NEXRAD) and US alerts.

Each source turns "the last N radar images" into a list of Frame objects. A frame names
the tile URL template of one radar image and a stable id, so a frame drawn once can be
reused on later refreshes. Every request goes through the plugin's guarded client
(ctx.http); nothing here opens a socket itself.

RainViewer (worldwide): api.rainviewer.com/public/weather-maps.json lists past images
every 10 minutes; tiles come from tilecache.rainviewer.com up to zoom 7.
IEM NEXRAD (United States): nexrad-n0q-900913 is the latest 5-minute composite and
-m05m ... -m50m the ones before it; n0q_0.json gives the time of the latest.
"""
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ...fetch import FetchError

RAINVIEWER = "rainviewer"
IEM = "iem-nexrad"
SOURCES = (RAINVIEWER, IEM)

RAINVIEWER_MAPS = "https://api.rainviewer.com/public/weather-maps.json"
RAINVIEWER_HOST = "https://tilecache.rainviewer.com"
RAINVIEWER_PATH_RE = re.compile(r"^/v2/radar/[A-Za-z0-9_]{1,40}$")
RAINVIEWER_COLOR = 2       # "Universal Blue", the scheme of the free API
RAINVIEWER_OPTIONS = "1_1"  # smoothed, snow shown
IEM_TILES = "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913{suffix}"
IEM_META = "https://mesonet.agron.iastate.edu/data/gis/images/4326/USCOMP/n0q_0.json"
IEM_STEP_S = 300
IEM_MAX_FRAMES = 11        # the latest image plus -m05m ... -m50m
NWS_ALERTS = "https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
MAX_ALERTS = 4
SEVERITY = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1}

ATTRIBUTION = {RAINVIEWER: "Radar: RainViewer",
               IEM: "Radar: NOAA NEXRAD via Iowa Environmental Mesonet"}
MAX_ZOOM = {RAINVIEWER: 7, IEM: 9}  # radar tiles above this zoom are scaled up


@dataclass(frozen=True)
class Frame:
    t: int             # epoch seconds of the radar image
    template: str      # tile URL with {z}, {x} and {y}
    reuse_id: str      # stable name for this image ("" when it cannot be reused)


def source_name(value) -> str:
    return value if value in SOURCES else RAINVIEWER


def max_zoom(source: str) -> int:
    return MAX_ZOOM[source_name(source)]


def frames(http, source: str, count: int, now: float | None = None) -> list[Frame]:
    """The newest `count` radar images of `source`, oldest first."""
    if source_name(source) == IEM:
        return iem_frames(http, count, now)
    return rainviewer_frames(http, count)


# --- RainViewer ------------------------------------------------------------------------

def rainviewer_frames(http, count: int) -> list[Frame]:
    body = http.get(RAINVIEWER_MAPS).raise_for_status().json()
    past = ((body or {}).get("radar") or {}).get("past") if isinstance(body, dict) else None
    if not isinstance(past, list):
        raise FetchError("parse_error")
    out = []
    for entry in past:
        if not isinstance(entry, dict):
            continue
        t, path = entry.get("time"), entry.get("path")
        if (isinstance(t, bool) or not isinstance(t, int) or not isinstance(path, str)
                or not RAINVIEWER_PATH_RE.match(path)):
            continue
        # The tile host is fixed: the "host" field of the answer is never followed.
        template = (f"{RAINVIEWER_HOST}{path}/256/{{z}}/{{x}}/{{y}}/"
                    f"{RAINVIEWER_COLOR}/{RAINVIEWER_OPTIONS}.png")
        out.append(Frame(t=t, template=template, reuse_id=f"rv:{path}"))
    out.sort(key=lambda f: f.t)
    if not out:
        raise FetchError("parse_error")
    return out[-max(1, int(count)):]


# --- Iowa Environmental Mesonet NEXRAD -------------------------------------------------

def _parse_time(value) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())


def iem_latest(http) -> int | None:
    """Time of the latest NEXRAD composite, or None when the metadata is unavailable."""
    try:
        body = http.get(IEM_META).raise_for_status().json()
    except FetchError as exc:
        if exc.code == "rate_limited":
            raise
        return None
    meta = body.get("meta") if isinstance(body, dict) else None
    return _parse_time(meta.get("valid")) if isinstance(meta, dict) else None


def iem_frames(http, count: int, now: float | None = None) -> list[Frame]:
    count = max(1, min(int(count), IEM_MAX_FRAMES))
    latest = iem_latest(http)
    known = latest is not None
    if not known:  # estimate; such frames are not reused, because the guess may be off
        now = time.time() if now is None else now
        latest = int(now // IEM_STEP_S * IEM_STEP_S) - IEM_STEP_S
    out = []
    for back in range(count - 1, -1, -1):
        suffix = f"-m{back * 5:02d}m" if back else ""
        t = latest - back * IEM_STEP_S
        out.append(Frame(t=t, template=IEM_TILES.format(suffix=suffix) + "/{z}/{x}/{y}.png",
                         reuse_id=f"iem:{t}" if known else ""))
    return out


# --- US alerts (api.weather.gov) -------------------------------------------------------

def _clip(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def alerts(http, lat: float, lon: float) -> list[dict]:
    """Active NWS alerts for the point, most severe first. Never raises: alerts are an
    extra, and points outside the US simply have none."""
    try:
        resp = http.get(NWS_ALERTS.format(lat=lat, lon=lon),
                        headers={"Accept": "application/geo+json"})
        if resp.status != 200:
            return []
        body = resp.json()
    except FetchError:
        return []
    features = body.get("features") if isinstance(body, dict) else None
    out = []
    for feature in features if isinstance(features, list) else []:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict) or not props.get("event"):
            continue
        ends = _parse_time(props.get("ends")) or _parse_time(props.get("expires"))
        out.append({"event": _clip(props.get("event"), 80),
                    "headline": _clip(props.get("headline"), 200),
                    "severity": props.get("severity") if props.get("severity") in SEVERITY
                    else "Unknown",
                    "ends_ms": ends * 1000 if ends else None})
    out.sort(key=lambda a: -SEVERITY.get(a["severity"], 0))
    seen, unique = set(), []
    for alert in out:
        if (alert["event"], alert["headline"]) not in seen:
            seen.add((alert["event"], alert["headline"]))
            unique.append(alert)
    return unique[:MAX_ALERTS]
