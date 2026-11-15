"""Weather plugin: current conditions, the next hours and a daily forecast.

The organization setting `provider` picks Open-Meteo (default; free for non-commercial
use, or with AISIGNX_OPENMETEO_KEY for commercial use) or MET Norway (free including
commercial use, but it needs plugins.ua_contact). Places are geocoded when the item is
saved (aisignx.plugins.geo), so a fetch only ever sends coordinates.

Items whose coordinates round to the same 2 decimals share one cache entry, so the
screen takes the place name from its own config (location.label), not from the data.
"""
from ...fetch import FetchError
from ...service import FetchResult, ua_contact
from . import providers

MET = "met-norway"


def _location(config: dict) -> tuple:
    loc = config.get("location") or {}
    return float(loc["lat"]), float(loc["lon"]), str(loc.get("label") or "")


def fetch(config: dict, ctx) -> FetchResult:
    lat, lon, label = _location(config)
    units = config.get("units") if config.get("units") in providers.UNITS else "metric"
    ttl = None
    if ctx.setting("provider") == MET:
        if not ua_contact():
            raise FetchError("missing_contact")
        tz = ctx.org_tz  # locationforecast has no time zone; days split in the org's

        def sun(when):
            key = f"sun:{providers.coord(lat)}:{providers.coord(lon)}:{when.isoformat()}"
            try:
                return ctx.cache(key, providers.SUN_TTL,
                                 lambda: providers.met_sun(ctx.http, lat, lon, when, tz))
            except FetchError:
                return None  # sunrise and sunset are optional

        data, expires_in = providers.met_norway(ctx.http, lat, lon, units, tz, sun)
        if expires_in:  # never ask MET again before its Expires
            ttl = max(int(config.get("refresh") or 0), expires_in)
    else:
        data = providers.open_meteo(ctx.http, lat, lon, units, ctx.secret("openmeteo_key"))
    data["location"].update(label=label, lat=lat, lon=lon)
    return FetchResult(data, ttl=ttl)


def playable(media, config: dict, ctx) -> bool:
    """MET Norway refuses requests without contact details in the User-Agent."""
    return ctx.setting("provider") != MET or bool(ua_contact())
