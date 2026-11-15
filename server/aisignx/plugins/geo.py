"""Geocoding for `location` fields: place names through Open-Meteo, postal codes through
Nominatim (OpenStreetMap). "lat,lon" never needs a lookup.

Only editors trigger lookups (the Find button, or saving a typed place name), and the
results are cached in-process for a day. Nominatim allows one request per second for the
whole server; a lookup that would exceed it raises RateLimited instead of waiting.
Open-Meteo's free geocoder is for non-commercial use; with AISIGNX_OPENMETEO_KEY set the
customer endpoint is used and the key is redacted from logs.
"""
import os
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlencode

from flask import current_app

from . import schema
from .fetch import Buckets, Client, FetchError, RateLimited

OPEN_METEO = "geocoding-api.open-meteo.com"
OPEN_METEO_CUSTOMER = "customer-geocoding-api.open-meteo.com"
NOMINATIM = "nominatim.openstreetmap.org"
HOSTS = (OPEN_METEO, OPEN_METEO_CUSTOMER, NOMINATIM)
KEY_ENV = "AISIGNX_OPENMETEO_KEY"
MAX_QUERY = 100
MAX_RESULTS = 5
BUDGET_S = 8.0
CACHE_TTL_S = 86400
CACHE_MAX = 256
NOMINATIM_GAP_S = 1.0
_POSTAL = re.compile(r"^\s*(?=[A-Za-z0-9 -]*\d)([A-Za-z0-9][A-Za-z0-9 -]{1,9})"
                     r"\s*(?:,\s*([A-Za-z]{2}))?\s*$")

_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_nominatim_lock = threading.Lock()
_nominatim_last = [0.0]


def _cached(q: str):
    with _cache_lock:
        hit = _cache.get(q)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            _cache.move_to_end(q)
            return hit[1]
    return None


def _remember(q: str, results: list) -> None:
    with _cache_lock:
        _cache[q] = (time.time(), results)
        _cache.move_to_end(q)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
    _nominatim_last[0] = 0.0


def _client() -> Client:
    from .service import BUCKETS_KEY, user_agent

    app = current_app._get_current_object()
    return Client(hosts=HOSTS, rate={OPEN_METEO: 60, OPEN_METEO_CUSTOMER: 60, NOMINATIM: 60},
                  max_bytes=512 * 1024, deadline=time.monotonic() + BUDGET_S,
                  user_agent=user_agent(), proxy=app.config.get("PLUGIN_HTTP_PROXY"),
                  buckets=app.extensions.setdefault(BUCKETS_KEY, Buckets()))


def _language() -> str:
    from .service import locale

    return (locale().split("-", 1)[0] or "en").lower()


def _point(lat, lon, label: str) -> dict | None:
    try:
        lat = round(float(lat), 4)
        lon = round(float(lon), 4)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"label": label[:MAX_QUERY], "lat": lat, "lon": lon}


def _open_meteo(q: str) -> list[dict]:
    key = current_app.config.get(KEY_ENV) or os.environ.get(KEY_ENV) or ""
    params = {"name": q, "count": MAX_RESULTS, "language": _language(), "format": "json"}
    host = OPEN_METEO
    if key.strip():
        host, params["apikey"] = OPEN_METEO_CUSTOMER, key.strip()
    resp = _client().get(f"https://{host}/v1/search?{urlencode(params)}",
                         redact=["apikey"]).raise_for_status()
    body = resp.json()
    out = []
    for row in (body.get("results") or [] if isinstance(body, dict) else []):
        if not isinstance(row, dict):
            continue
        parts = [row.get("name"), row.get("admin1"), row.get("country")]
        label = ", ".join(str(p) for p in parts if p)
        point = _point(row.get("latitude"), row.get("longitude"), label or q)
        if point:
            out.append(point)
    return out[:MAX_RESULTS]


def _nominatim(postal: str, country: str | None) -> list[dict]:
    with _nominatim_lock:
        if time.monotonic() - _nominatim_last[0] < NOMINATIM_GAP_S:
            raise RateLimited()
        _nominatim_last[0] = time.monotonic()
    params = {"postalcode": postal, "format": "jsonv2", "limit": MAX_RESULTS}
    if country:
        params["countrycodes"] = country.lower()
    resp = _client().get(f"https://{NOMINATIM}/search?{urlencode(params)}").raise_for_status()
    body = resp.json()
    out = []
    for row in body if isinstance(body, list) else []:
        if isinstance(row, dict):
            point = _point(row.get("lat"), row.get("lon"),
                           str(row.get("display_name") or postal))
            if point:
                out.append(point)
    return out[:MAX_RESULTS]


def geocode(q: str) -> list[dict]:
    """Up to 5 candidates [{label, lat, lon}] for a place name, postal code or "lat,lon".
    Raises FetchError (RateLimited included) when the lookup service fails."""
    q = re.sub(r"\s+", " ", str(q or "")).strip()[:MAX_QUERY]
    if not q:
        return []
    pair = schema.parse_latlon(q)
    if pair is not None:
        return [{"label": f"{pair[0]:g}, {pair[1]:g}", "lat": pair[0], "lon": pair[1]}]
    hit = _cached(q.lower())
    if hit is not None:
        return hit
    results = []
    postal = _POSTAL.match(q)
    if postal:
        try:
            results = _nominatim(postal.group(1).strip(), postal.group(2))
        except RateLimited:
            raise
        except FetchError:
            results = []
    if not results:
        results = _open_meteo(q)
    _remember(q.lower(), results)
    return results
