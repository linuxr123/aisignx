"""WMO weather interpretation codes (the ones Open-Meteo uses) and the MET Norway
symbol_code table that maps onto them.

The player (static/player/plugins/weather/icons.js) draws an icon and a short text for
each code, so every provider must hand over one of CODES (or None when unknown).
"""

CODES = frozenset({0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75,
                   77, 80, 81, 82, 85, 86, 95, 96, 99})

# MET Norway symbol_code without its _day / _night / _polartwilight suffix.
# Sleet has no WMO code of its own; freezing rain (66/67) gets the same mixed icon.
MET_SYMBOLS = {
    "clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3, "fog": 45,
    "lightrain": 61, "rain": 63, "heavyrain": 65,
    "lightrainshowers": 80, "rainshowers": 81, "heavyrainshowers": 82,
    "lightsleet": 66, "sleet": 67, "heavysleet": 67,
    "lightsleetshowers": 66, "sleetshowers": 67, "heavysleetshowers": 67,
    "lightsnow": 71, "snow": 73, "heavysnow": 75,
    "lightsnowshowers": 85, "snowshowers": 85, "heavysnowshowers": 86,
}
THUNDER = 95


def clean(code):
    """A known WMO code as int, else None."""
    if isinstance(code, bool):
        return None
    try:
        value = int(code)
    except (TypeError, ValueError):
        return None
    return value if value in CODES else None


def from_met(symbol) -> tuple:
    """(wmo code or None, is_day or None) for a MET Norway symbol_code like
    "lightrainshowers_night" or "heavysnowandthunder"."""
    if not isinstance(symbol, str) or not symbol:
        return None, None
    base, _, period = symbol.partition("_")
    is_day = None
    if period == "day":
        is_day = True
    elif period in ("night", "polartwilight"):
        is_day = False
    if "andthunder" in base:
        return THUNDER, is_day
    return MET_SYMBOLS.get(base), is_day


def severity(code) -> int:
    """Rank codes so a day shows its most notable weather (fog above plain cloud)."""
    return -1 if code is None else int(code)
