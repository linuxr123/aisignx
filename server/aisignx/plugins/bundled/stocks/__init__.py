"""Stocks plugin: quotes from Finnhub (finnhub.io), fetched on the server only.

The API key comes from AISIGNX_FINNHUB_KEY and is sent in the X-Finnhub-Token header,
never in a URL. Screens only see the normalized data:

    {"quotes": [{"symbol", "name", "price", "change", "pct", "currency", "ts"}],
     "as_of_ms", "attribution": "Data: Finnhub", "delayed": bool}

Quotes (60 s) and company profiles (7 days, for the name and currency) are cached per
symbol through ctx.cache, so every item and screen shares them. finnhub.io has a bucket
of 30 requests per minute; a symbol that cannot be fetched yet keeps its last known
quote, or has price None (the screen shows a dash).
"""
import math
import re
import time
from urllib.parse import urlencode

from aisignx.plugins.fetch import FetchError, RateLimited
from aisignx.plugins.schema import SchemaError
from aisignx.plugins.service import FetchResult

API = "https://finnhub.io/api/v1"
SECRET = "finnhub_key"
ATTRIBUTION = "Data: Finnhub"
MAX_SYMBOLS = 20
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-:^]{1,15}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
QUOTE_TTL = 60
PROFILE_TTL = 7 * 86400
NAME_MAX = 80
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_AUTH_CODES = ("http_401", "http_403")


def parse_symbols(text) -> list[str]:
    """Upper-cased symbols from "aapl, msft brk.b", duplicates removed, order kept."""
    if not isinstance(text, str):
        return []
    out = []
    for part in re.split(r"[\s,]+", text.strip().upper()):
        if part and part not in out:
            out.append(part)
    return out


def _valid(symbols: list[str]) -> bool:
    return 0 < len(symbols) <= MAX_SYMBOLS and all(SYMBOL_RE.match(s) for s in symbols)


# --- hooks ------------------------------------------------------------------------------

def normalize(config, ctx):
    symbols = parse_symbols(config.get("symbols"))
    if not symbols:
        raise SchemaError("Enter at least one symbol.", "symbols")
    bad = next((s for s in symbols if not SYMBOL_RE.match(s)), None)
    if bad is not None:
        raise SchemaError(f"{bad[:20]} is not a valid symbol (letters, digits and . - : ^, "
                          "at most 15 characters).", "symbols")
    if len(symbols) > MAX_SYMBOLS:
        raise SchemaError(f"At most {MAX_SYMBOLS} symbols.", "symbols")
    config["symbols"] = ",".join(symbols)
    return config


def playable(media, config, ctx):
    """Only with an API key, so screens never show a slide that cannot get data."""
    return ctx.secret(SECRET) is not None and _valid(parse_symbols(config.get("symbols")))


def fetch(config, ctx):
    key = ctx.secret(SECRET)
    if key is None:
        raise FetchError("missing_secret")
    symbols = [s for s in parse_symbols(config.get("symbols")) if SYMBOL_RE.match(s)]
    symbols = symbols[:MAX_SYMBOLS]
    quotes, failure = {}, None
    for symbol in symbols:  # quotes first: they matter more than names
        try:
            quotes[symbol] = ctx.cache(f"quote:{symbol}", QUOTE_TTL,
                                       lambda s=symbol: _quote(ctx, key, s))
        except FetchError as exc:
            if exc.code in _AUTH_CODES:
                raise
            failure = exc
    if symbols and not quotes:
        raise failure or FetchError("no_data")
    profiles = {}
    for symbol in symbols:
        if ":" in symbol:  # exchange-prefixed crypto and forex symbols have no profile
            continue
        try:
            profiles[symbol] = ctx.cache(f"profile:{symbol}", PROFILE_TTL,
                                         lambda s=symbol: _profile(ctx, key, s))
        except FetchError as exc:
            if exc.code in _AUTH_CODES:
                raise
    return FetchResult(_data(symbols, quotes, profiles, bool(ctx.setting("delayed_label"))))


# --- Finnhub ----------------------------------------------------------------------------

def _get(ctx, path: str, symbol: str, key: str) -> dict:
    url = f"{API}/{path}?{urlencode({'symbol': symbol})}"
    resp = ctx.http.get(url, headers={"X-Finnhub-Token": key, "Accept": "application/json"})
    if resp.status == 429:
        raise RateLimited()
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise FetchError("parse_error")
    return body


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _quote(ctx, key: str, symbol: str) -> dict:
    """{price, change, pct, ts}; an unknown symbol comes back as all zeros: price None."""
    body = _get(ctx, "quote", symbol, key)
    price, ts = _num(body.get("c")), _num(body.get("t"))
    if price is None or (price == 0 and not ts):
        return {"price": None, "change": None, "pct": None, "ts": None}
    return {"price": price, "change": _num(body.get("d")), "pct": _num(body.get("dp")),
            "ts": int(ts) if ts and ts > 0 else None}


def _clean(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = _CONTROL.sub("", value).strip()
    return value[:limit] or None


def _profile(ctx, key: str, symbol: str) -> dict:
    """{name, currency}; Finnhub answers {} for symbols it has no profile for."""
    body = _get(ctx, "stock/profile2", symbol, key)
    currency = _clean(body.get("currency"), 3)
    currency = currency.upper() if currency else None
    return {"name": _clean(body.get("name"), NAME_MAX),
            "currency": currency if currency and CURRENCY_RE.match(currency) else None}


def _data(symbols, quotes: dict, profiles: dict, delayed: bool) -> dict:
    rows, stamps = [], []
    for symbol in symbols:
        quote = quotes.get(symbol) or {}
        profile = profiles.get(symbol) or {}
        ts = quote.get("ts")
        if ts:
            stamps.append(ts)
        rows.append({"symbol": symbol, "name": profile.get("name"),
                     "price": quote.get("price"), "change": quote.get("change"),
                     "pct": quote.get("pct"), "currency": profile.get("currency"), "ts": ts})
    as_of = max(stamps) * 1000 if stamps else int(time.time() * 1000)
    return {"quotes": rows, "as_of_ms": as_of, "attribution": ATTRIBUTION, "delayed": delayed}
