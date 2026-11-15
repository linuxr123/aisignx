"""Weather providers: fetch through the plugin's guarded client and normalize.

Both providers return the same shape (see normalized()):

    {"location": {"label", "lat", "lon", "tz"}, "units": {"temp", "wind", "precip"},
     "current": {"temp", "feels", "code", "is_day", "wind", "wind_dir", "humidity",
                 "precip"},
     "hourly": [{"t", "temp", "code", "pop", "is_day"}] (12),
     "daily": [{"date", "code", "tmax", "tmin", "pop", "sunrise", "sunset"}] (7),
     "provider", "attribution"}

Times ("t", "sunrise", "sunset") are epoch milliseconds; "date" is the local
YYYY-MM-DD; codes are WMO (wmo.py). Any value may be None when the provider lacks it.

Open-Meteo: free for non-commercial use; with a customer key (AISIGNX_OPENMETEO_KEY)
the customer host is used and the key, which must go in the query string, is redacted
from every logged URL. MET Norway: free including commercial use (CC BY 4.0), but it
requires a User-Agent with contact details (plugins.ua_contact), conditional requests
(If-Modified-Since) and respect for Expires.
"""
import json
import math
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from ...fetch import FetchError
from . import wmo

OPEN_METEO_HOST = "api.open-meteo.com"
OPEN_METEO_CUSTOMER_HOST = "customer-api.open-meteo.com"
MET_HOST = "api.met.no"
ATTRIBUTION = {
    "open-meteo": "Weather data: Open-Meteo.com (CC BY 4.0)",
    "met-norway": "Weather data: MET Norway (CC BY 4.0)",
}
UNITS = {
    "metric": {"temp": "°C", "wind": "km/h", "precip": "mm"},
    "imperial": {"temp": "°F", "wind": "mph", "precip": "in"},
}
HOURS = 12
DAYS = 7
MET_MAX_TTL = 3600          # never trust an Expires further out than this
MET_MEMO_MAX = 32           # last responses kept for If-Modified-Since
SUN_TTL = 30 * 86400        # sunrise times of a past or future date never change

_met_memo: OrderedDict = OrderedDict()   # (lat, lon) -> (last_modified, body)
_met_lock = threading.Lock()


# --- helpers ----------------------------------------------------------------------------

def _num(value, digits: int = 1):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), digits)


def _pct(value):
    number = _num(value, 0)
    return None if number is None else int(max(0, min(100, number)))


def coord(value: float) -> str:
    """At most 4 decimals, no trailing zeros (MET Norway asks for this)."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _parse_iso(text) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ms(dt: datetime | None):
    return None if dt is None else int(dt.timestamp() * 1000)


def normalized(units: str, provider: str) -> dict:
    return {"location": {}, "units": dict(UNITS[units]), "current": {}, "hourly": [],
            "daily": [], "provider": provider, "attribution": ATTRIBUTION[provider]}


# --- Open-Meteo -------------------------------------------------------------------------

def open_meteo_url(lat: float, lon: float, units: str, key: str | None) -> str:
    params = {
        "latitude": coord(lat), "longitude": coord(lon),
        "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m,"
                   "wind_direction_10m,relative_humidity_2m,precipitation",
        "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,sunrise,sunset",
        "timezone": "auto", "timeformat": "unixtime", "forecast_days": DAYS,
    }
    if units == "imperial":
        params.update(temperature_unit="fahrenheit", wind_speed_unit="mph",
                      precipitation_unit="inch")
    if key:
        params["apikey"] = key
    host = OPEN_METEO_CUSTOMER_HOST if key else OPEN_METEO_HOST
    return f"https://{host}/v1/forecast?{urlencode(params)}"


def open_meteo(http, lat: float, lon: float, units: str, key: str | None = None) -> dict:
    resp = http.get(open_meteo_url(lat, lon, units, key), redact=["apikey"])
    resp.raise_for_status()
    try:
        return parse_open_meteo(resp.json(), units)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        raise FetchError("parse_error") from None


def parse_open_meteo(raw: dict, units: str) -> dict:
    out = normalized(units, "open-meteo")
    offset = int(raw.get("utc_offset_seconds") or 0)
    out["location"]["tz"] = str(raw.get("timezone") or "UTC")
    cur = raw["current"]
    out["current"] = {
        "temp": _num(cur.get("temperature_2m")),
        "feels": _num(cur.get("apparent_temperature")),
        "code": wmo.clean(cur.get("weather_code")),
        "is_day": bool(cur.get("is_day", 1)),
        "wind": _num(cur.get("wind_speed_10m")),
        "wind_dir": _num(cur.get("wind_direction_10m"), 0),
        "humidity": _pct(cur.get("relative_humidity_2m")),
        "precip": _num(cur.get("precipitation"), 2),
    }
    now = int(cur.get("time") or time.time())
    hourly = raw.get("hourly") or {}
    times = hourly.get("time") or []
    for i, t in enumerate(times):
        if t <= now:
            continue
        out["hourly"].append({
            "t": int(t) * 1000,
            "temp": _num(_at(hourly, "temperature_2m", i)),
            "code": wmo.clean(_at(hourly, "weather_code", i)),
            "pop": _pct(_at(hourly, "precipitation_probability", i)),
            "is_day": bool(_at(hourly, "is_day", i, 1)),
        })
        if len(out["hourly"]) >= HOURS:
            break
    daily = raw.get("daily") or {}
    for i, t in enumerate((daily.get("time") or [])[:DAYS]):
        local = datetime.fromtimestamp(int(t) + offset, timezone.utc).date()
        sunrise, sunset = _at(daily, "sunrise", i), _at(daily, "sunset", i)
        out["daily"].append({
            "date": local.isoformat(),
            "code": wmo.clean(_at(daily, "weather_code", i)),
            "tmax": _num(_at(daily, "temperature_2m_max", i)),
            "tmin": _num(_at(daily, "temperature_2m_min", i)),
            "pop": _pct(_at(daily, "precipitation_probability_max", i)),
            "sunrise": int(sunrise) * 1000 if isinstance(sunrise, (int, float)) else None,
            "sunset": int(sunset) * 1000 if isinstance(sunset, (int, float)) else None,
        })
    return out


def _at(block: dict, name: str, i: int, default=None):
    values = block.get(name)
    if isinstance(values, list) and i < len(values) and values[i] is not None:
        return values[i]
    return default


# --- MET Norway -------------------------------------------------------------------------

def met_url(lat: float, lon: float) -> str:
    return (f"https://{MET_HOST}/weatherapi/locationforecast/2.0/complete?"
            f"{urlencode({'lat': coord(lat), 'lon': coord(lon)})}")


def met_norway(http, lat: float, lon: float, units: str, tz, sun=None,
               now: float | None = None) -> tuple:
    """(data, seconds until MET's Expires or None). tz: a ZoneInfo for the daily split.
    sun(date) -> (sunrise_ms, sunset_ms) or None fills sunrise and sunset."""
    now = time.time() if now is None else now
    memo_key = (coord(lat), coord(lon))
    with _met_lock:
        previous = _met_memo.get(memo_key)
    headers = {"Accept": "application/json"}
    if previous:
        headers["If-Modified-Since"] = previous[0]
    resp = http.get(met_url(lat, lon), headers=headers)
    if resp.status == 304 and previous:
        body = previous[1]
    else:
        resp.raise_for_status()
        body = resp.body
        if resp.headers.get("last-modified"):
            with _met_lock:
                _met_memo[memo_key] = (resp.headers["last-modified"], body)
                _met_memo.move_to_end(memo_key)
                while len(_met_memo) > MET_MEMO_MAX:
                    _met_memo.popitem(last=False)
    try:
        data = parse_met(json.loads(body.decode("utf-8")), units, tz, now)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        raise FetchError("parse_error") from None
    if sun is not None:
        for day in data["daily"]:
            times = sun(date.fromisoformat(day["date"]))
            if times:
                day["sunrise"], day["sunset"] = times
    return data, _expires_in(resp.headers.get("expires"), now)


def _expires_in(text, now: float) -> int | None:
    if not text:
        return None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    seconds = int(when.timestamp() - now)
    return min(seconds, MET_MAX_TTL) if seconds > 0 else None


def clear_memo() -> None:
    with _met_lock:
        _met_memo.clear()


def apparent_c(temp_c, rh, wind_ms):
    """Steadman's apparent temperature (shade), as the Australian BoM uses it."""
    if temp_c is None or rh is None or wind_ms is None:
        return temp_c
    vapour = rh / 100 * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return temp_c + 0.33 * vapour - 0.70 * wind_ms - 4.00


def _convert(units: str):
    imperial = units == "imperial"

    def temp(c):
        return None if c is None else _num(c * 9 / 5 + 32 if imperial else c)

    def wind(ms):
        return None if ms is None else _num(ms * (2.23694 if imperial else 3.6))

    def precip(mm):
        return None if mm is None else _num(mm / 25.4 if imperial else mm, 2)

    return temp, wind, precip


def _met_entry(item: dict) -> tuple:
    t = _parse_iso(item["time"])
    data = item.get("data") or {}
    details = (data.get("instant") or {}).get("details") or {}
    return t, data, details


def _symbol(block) -> tuple:
    if not isinstance(block, dict):
        return None, None
    return wmo.from_met((block.get("summary") or {}).get("symbol_code"))


def parse_met(raw: dict, units: str, tz, now: float) -> dict:
    temp, wind, precip = _convert(units)
    out = normalized(units, "met-norway")
    out["location"]["tz"] = getattr(tz, "key", None) or "UTC"
    entries = [_met_entry(item) for item in raw["properties"]["timeseries"]]
    entries = [e for e in entries if e[0] is not None]
    if not entries:
        raise ValueError("no timeseries")
    current = entries[0]
    for entry in entries:
        if entry[0].timestamp() <= now:
            current = entry
    t, data, details = current
    code, is_day = _symbol(data.get("next_1_hours"))
    if code is None:
        code, is_day = _symbol(data.get("next_6_hours"))
    air, rh = _num(details.get("air_temperature")), _num(details.get("relative_humidity"))
    wind_ms = _num(details.get("wind_speed"))
    out["current"] = {
        "temp": temp(air), "feels": temp(apparent_c(air, rh, wind_ms)), "code": code,
        "is_day": True if is_day is None else is_day, "wind": wind(wind_ms),
        "wind_dir": _num(details.get("wind_from_direction"), 0), "humidity": _pct(rh),
        "precip": precip(_num(((data.get("next_1_hours") or {}).get("details") or {})
                              .get("precipitation_amount"), 2)),
    }
    for t, data, details in entries:
        if t <= current[0] or "next_1_hours" not in data:
            continue
        code, is_day = _symbol(data["next_1_hours"])
        out["hourly"].append({
            "t": _ms(t), "temp": temp(_num(details.get("air_temperature"))), "code": code,
            "pop": _pct((data["next_1_hours"].get("details") or {})
                        .get("probability_of_precipitation")),
            "is_day": True if is_day is None else is_day,
        })
        if len(out["hourly"]) >= HOURS:
            break
    out["daily"] = _met_daily(entries, tz, temp)
    return out


def _met_daily(entries: list, tz, temp) -> list:
    days: dict = OrderedDict()
    for t, data, details in entries:
        local = t.astimezone(tz)
        day = days.setdefault(local.date(), {"temps": [], "codes": [], "day_codes": [],
                                             "pops": []})
        air = _num(details.get("air_temperature"))
        if air is not None:
            day["temps"].append(air)
        six = data.get("next_6_hours") or {}
        six_details = six.get("details") or {}
        for name in ("air_temperature_max", "air_temperature_min"):
            value = _num(six_details.get(name))
            if value is not None and (local + timedelta(hours=5)).date() == local.date():
                day["temps"].append(value)
        for block in (data.get("next_1_hours"), six):
            code, _ = _symbol(block)
            if code is None:
                continue
            day["codes"].append(code)
            if block is six and 5 <= local.hour <= 13:  # mostly daytime
                day["day_codes"].append(code)
            pop = _pct(((block or {}).get("details") or {}).get("probability_of_precipitation"))
            if pop is not None:
                day["pops"].append(pop)
    out = []
    for when, day in list(days.items())[:DAYS]:
        codes = day["day_codes"] or day["codes"]
        out.append({
            "date": when.isoformat(),
            "code": max(codes, key=wmo.severity) if codes else None,
            "tmax": temp(max(day["temps"])) if day["temps"] else None,
            "tmin": temp(min(day["temps"])) if day["temps"] else None,
            "pop": max(day["pops"]) if day["pops"] else None,
            "sunrise": None, "sunset": None,
        })
    return out


def met_sun_url(lat: float, lon: float, when: date, tz) -> str:
    noon = datetime(when.year, when.month, when.day, 12, tzinfo=tz)
    offset = noon.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    text = f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    query = urlencode({"lat": coord(lat), "lon": coord(lon), "date": when.isoformat(),
                       "offset": text})
    return f"https://{MET_HOST}/weatherapi/sunrise/3.0/sun?{query}"


def met_sun(http, lat: float, lon: float, when: date, tz) -> list:
    """[sunrise_ms, sunset_ms] from MET's sunrise/3.0 (either may be None: polar day or
    night)."""
    resp = http.get(met_sun_url(lat, lon, when, tz), headers={"Accept": "application/json"})
    resp.raise_for_status()
    props = resp.json().get("properties")
    if not isinstance(props, dict):
        raise FetchError("parse_error")
    return [_ms(_parse_iso((props.get("sunrise") or {}).get("time"))),
            _ms(_parse_iso((props.get("sunset") or {}).get("time")))]
