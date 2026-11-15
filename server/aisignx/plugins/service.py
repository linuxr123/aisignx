"""Plugin items and the plugin hook context.

A plugin item is a Media row (media_type "plugin") holding a registry key and a config
validated against that plugin's schema. Playlist items, ordering, transitions, proof of
play and per-item durations then work unchanged.

Hooks (optional functions in bundled/<key>/__init__.py):

    normalize(config, ctx) -> dict             at save, after schema validation; may raise
                                               schema.SchemaError(message, field). Must be
                                               idempotent: stored configs are re-validated
                                               by the schema only.
    fetch(config, ctx) -> FetchResult          on the worker only (see cache.py)
    manifest_extra(media, config, ctx) -> dict extra keys for the manifest "plugin" block;
                                               "url" (native: the URL to load, before the
                                               #c= fragment) and "muted" go to the item.
                                               media is a transient row in a preview.
    playable(media, config, ctx) -> bool       cheap, no network (the resolver calls it)
    auto_duration(config) -> int | None        a default duration computed at save

Settings: plugins.<key>.enabled ("1" by default), plugins.<key>.<setting> (declared in
plugin.json "settings"), plugins.ua_contact and plugins.locale. Secrets come from the
environment only (app.config first, so tests can inject them) and never leave the server.
"""
import functools
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

from flask import current_app, url_for

from ..extensions import db
from ..models import Media, Setting
from ..models.media import MEDIA_PLUGIN, THUMB_FAILED, THUMB_OK
from ..timeutil import org_tz
from . import schema
from .registry import HOST_RE, PluginDef, get_plugin, get_registry

log = logging.getLogger(__name__)

ENABLED_KEY = "plugins.{key}.enabled"
UA_CONTACT_KEY = "plugins.ua_contact"
LOCALE_KEY = "plugins.locale"
PRIVATE_HOSTS_SETTING = "allow_private_hosts"  # a plugin setting the HTTP client honours
BUCKETS_KEY = "aisignx_plugin_buckets"
PROJECT_URL = "https://github.com/linuxr123/aisignx"
LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,4}$")
CONTACT_RE = re.compile(r"^[A-Za-z0-9@.+_:/~%#?=&-]{3,200}$")
_TRUE = {"1", "true", "yes", "on"}


# --- settings and secrets ---------------------------------------------------------------

def is_enabled(plugin: PluginDef) -> bool:
    value = Setting.get(ENABLED_KEY.format(key=plugin.key))
    return value is None or str(value).strip().lower() in _TRUE


def setting_key(plugin: PluginDef, name: str) -> str:
    return f"plugins.{plugin.key}.{name}"


def setting_value(plugin: PluginDef, name: str):
    """An organization setting of the plugin: the stored value if valid, else its default."""
    fld = plugin.setting_field(name)
    if fld is None:
        raise KeyError(name)
    raw = schema.from_setting(fld, Setting.get(setting_key(plugin, name)))
    if raw is not None:
        value, error = schema.validate_value(fld, raw, check_refs=False)
        if error is None:
            return value
    return schema.defaults([fld])[name]


def setting_values(plugin: PluginDef) -> dict:
    return {f["name"]: setting_value(plugin, f["name"]) for f in plugin.settings}


def secret_value(plugin: PluginDef, name: str) -> str | None:
    spec = next((s for s in plugin.secrets if s["name"] == name), None)
    if spec is None:
        raise KeyError(name)
    value = current_app.config.get(spec["env"]) or os.environ.get(spec["env"]) or ""
    return str(value).strip() or None


def secret_status(plugin: PluginDef) -> list[dict]:
    """[{name, label, env, configured}] (never the value)."""
    return [{**s, "configured": secret_value(plugin, s["name"]) is not None}
            for s in plugin.secrets]


def ua_contact() -> str:
    return (Setting.get(UA_CONTACT_KEY) or "").strip()


def locale() -> str:
    return (Setting.get(LOCALE_KEY) or "").strip()


def user_agent() -> str:
    from .. import __version__

    contact = ua_contact()
    return f"AiSignX/{__version__} (+{PROJECT_URL}{'; ' + contact if contact else ''})"


def private_hosts(plugin: PluginDef) -> tuple:
    """Exact host names an admin allowed on private addresses (intranet feeds)."""
    if plugin.setting_field(PRIVATE_HOSTS_SETTING) is None:
        return ()
    text = str(setting_value(plugin, PRIVATE_HOSTS_SETTING) or "")
    names = (h.strip().lower() for h in re.split(r"[\s,]+", text))
    return tuple(h for h in names if h and HOST_RE.match(h))


# --- hook context -----------------------------------------------------------------------

@dataclass
class Asset:
    id: str
    mime: str
    data: bytes = field(repr=False)


@dataclass
class FetchResult:
    data: object
    assets: list = field(default_factory=list)
    ttl: int | None = None


class Context:
    """What hooks get: http, secret(), setting(), org_tz, asset(), cache(), deadline."""

    def __init__(self, plugin: PluginDef, deadline: float | None = None):
        self.plugin = plugin
        budget = plugin.data.fetch_budget_s if plugin.data else 15
        self.deadline = deadline if deadline is not None else time.monotonic() + budget
        self._http = None

    @property
    def org_tz(self):
        return org_tz()

    @property
    def locale(self) -> str:
        return locale()

    def setting(self, name: str):
        return setting_value(self.plugin, name)

    def secret(self, name: str) -> str | None:
        return secret_value(self.plugin, name)

    @property
    def http(self):
        if self._http is None:
            from .fetch import Buckets, Client

            app = current_app._get_current_object()
            buckets = app.extensions.setdefault(BUCKETS_KEY, Buckets())
            spec = self.plugin.data
            self._http = Client(
                hosts=spec.hosts if spec else (), rate=spec.rate if spec else {},
                max_bytes=spec.max_bytes if spec else 2 * 1024 * 1024,
                deadline=self.deadline, user_agent=user_agent(),
                proxy=app.config.get("PLUGIN_HTTP_PROXY"),
                private_hosts=private_hosts(self.plugin), buckets=buckets,
                allow_loopback=bool(app.config.get("TESTING")
                                    and app.config.get("PLUGIN_FETCH_TEST_LOOPBACK")))
        return self._http

    def asset(self, data: bytes, mime: str) -> Asset:
        from .cache import ASSET_MIMES

        if mime not in ASSET_MIMES or not isinstance(data, (bytes, bytearray)):
            raise ValueError("assets must be PNG, JPEG or WebP bytes")
        data = bytes(data)
        return Asset(hashlib.sha256(data).hexdigest()[:32], mime, data)

    def cache(self, subkey: str, ttl: int, fn):
        """A value shared by every item of this plugin (one quote per symbol, say).
        A fresh stored value is returned as is; otherwise fn() runs. When fn() raises
        and an older value exists, that value is returned instead."""
        from .cache import get_cache

        store = get_cache()
        key = _digest([self.plugin.key, self.plugin.version, "sub", str(subkey)])
        value, fresh = store.sub_get(key)
        if fresh:
            return value
        try:
            new = fn()
        except Exception:
            if value is not None:
                return value
            raise
        store.sub_put(key, new, max(1, int(ttl)))
        return new

    @staticmethod
    def file_entry(media: Media) -> dict:
        """The manifest file fields of an image or video (needs a request context)."""
        from ..player.manifest import _file_entry

        return _file_entry(media)


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- config -----------------------------------------------------------------------------

def _geocode_missing(plugin: PluginDef, raw: dict) -> None:
    """Fill lat/lon of location fields typed as a place name without using Find."""
    from . import geo

    for fld in plugin.schema:
        value = raw.get(fld["name"])
        if fld["type"] != "location" or not isinstance(value, dict):
            continue
        q = str(value.get("q") or "").strip()
        if not q or str(value.get("lat") or "").strip() or schema.parse_latlon(q):
            continue
        try:
            found = geo.geocode(q)
        except Exception:  # an error message for the field follows from validation
            log.info("geocoding %r failed", q[:100], exc_info=True)
            continue
        if found:
            value.update(lat=found[0]["lat"], lon=found[0]["lon"],
                         label=value.get("label") or found[0]["label"])


def validate_config(plugin: PluginDef, raw, *, check_refs: bool = True,
                    geocode: bool = False) -> tuple[dict, dict]:
    """(config, errors) for submitted values: schema validation, then normalize()."""
    raw = dict(raw) if isinstance(raw, dict) else {}
    if geocode:
        _geocode_missing(plugin, raw)
    config, errors = schema.validate(plugin.schema, raw, check_refs=check_refs)
    hook = plugin.hook("normalize")
    if errors or hook is None:
        return config, errors
    try:
        config = hook(dict(config), Context(plugin))
    except schema.SchemaError as exc:
        errors[exc.field if exc.field in {f["name"] for f in plugin.schema}
               else schema.GLOBAL_ERROR] = str(exc)
        return config, errors
    if not isinstance(config, dict):
        raise TypeError(f"plugin {plugin.key}: normalize() must return a dict")
    if schema.config_size(config) > schema.MAX_CONFIG_BYTES:
        errors[schema.GLOBAL_ERROR] = "These settings are too large (16 KB at most)."
    return config, errors


def stored_config(media: Media, plugin: PluginDef) -> dict | None:
    """The item's config if it still validates (memoized per media id and config)."""
    config = media.plugin_config if isinstance(media.plugin_config, dict) else None
    if config is None:
        return None
    registry = get_registry()
    memo_key = (media.id, plugin.key, plugin.version, _digest(config))
    hit = registry.memo_get(memo_key)
    if hit is not None:
        return hit[0]
    valid, errors = schema.validate(plugin.schema, config, check_refs=False)
    result = None if errors else valid
    registry.memo_put(memo_key, (result,))
    return result


def plugin_for(media: Media | None) -> PluginDef | None:
    if media is None or media.media_type != MEDIA_PLUGIN:
        return None
    return get_plugin(media.plugin_key)


def is_playable_plugin(media: Media) -> bool:
    """Installed, enabled, valid config and playable(). No url_for (the resolver uses it)."""
    plugin = plugin_for(media)
    if plugin is None or not is_enabled(plugin):
        return False
    config = stored_config(media, plugin)
    if config is None:
        return False
    hook = plugin.hook("playable")
    if hook is None:
        return True
    try:
        return bool(hook(media, dict(config), Context(plugin)))
    except Exception:
        log.exception("plugin %s: playable() failed for media %s", plugin.key, media.id)
        return False


def public_config(plugin: PluginDef, config: dict) -> dict:
    """What screens may see: private fields removed, an empty time zone resolved."""
    out = schema.public_config(plugin.schema, config)
    for fld in plugin.schema:
        if fld["type"] == "timezone" and fld["name"] in out and not out[fld["name"]]:
            out[fld["name"]] = Setting.get("timezone") or "UTC"
    return out


# --- data -------------------------------------------------------------------------------

def _dig(config: dict, path: str):
    value = config
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def cache_key(plugin: PluginDef, config: dict) -> str:
    """Shared by every item whose data keys match; coordinates rounded to 2 decimals."""
    types = {f["name"]: f["type"] for f in plugin.schema}
    values = []
    for path in plugin.data.keys:
        value = _dig(config, path)
        if types.get(path.split(".", 1)[0]) == "location" and isinstance(value, float):
            value = round(value, 2)
        values.append(value)
    return _digest([plugin.key, plugin.version, values])


def refresh_seconds(plugin: PluginDef, config: dict) -> int:
    ttl = plugin.data.ttl
    refresh = config.get("refresh") if isinstance(config, dict) else None
    if isinstance(refresh, int) and not isinstance(refresh, bool) and refresh > 0:
        ttl = refresh
    return max(ttl, plugin.data.min_ttl)


def run_fetch(plugin_key: str, config: dict, key: str) -> None:
    """Worker task: run the plugin's fetch() and store the result or the failure."""
    from .cache import get_cache
    from .fetch import FetchError

    plugin = get_plugin(plugin_key)
    store = get_cache()
    if plugin is None or plugin.data is None:
        return
    with store.lock(key):
        entry = store.read(key)
        if entry is not None and time.time() < float(entry.get("expires_at") or 0):
            return  # refreshed by someone else meanwhile
        try:
            result = plugin.hook("fetch")(dict(config), Context(plugin))
            if not isinstance(result, FetchResult):
                result = FetchResult(result)
            json.dumps(result.data)
            if not all(isinstance(a, Asset) for a in result.assets):
                raise TypeError("assets must come from ctx.asset()")
            ttl = refresh_seconds(plugin, config)
            if isinstance(result.ttl, int) and result.ttl > 0:
                ttl = max(result.ttl, plugin.data.min_ttl)
        except FetchError as exc:
            store.store_failure(key, exc.code)
            log.info("plugin %s: fetch failed (%s)", plugin_key, exc.code)
            return
        except Exception as exc:
            store.store_failure(key, "plugin_error")
            log.warning("plugin %s: fetch failed (%s)", plugin_key, type(exc).__name__,
                        exc_info=current_app.debug)
            return
        store.store_success(key, result.data, [(a.id, a.mime, a.data) for a in result.assets],
                            ttl)


def serve_data(plugin: PluginDef, config: dict):
    """(Served, refresh_s) for the data endpoints; never blocks for more than ~2 s."""
    from .cache import get_cache

    key = cache_key(plugin, config)
    served = get_cache().serve(key, functools.partial(run_fetch, plugin.key, dict(config), key))
    return served, refresh_seconds(plugin, config)


def cached_entry(plugin: PluginDef, config: dict) -> dict | None:
    from .cache import get_cache

    return get_cache().read(cache_key(plugin, config))


# --- manifest ---------------------------------------------------------------------------

def plugin_fields(plugin: PluginDef, config: dict, media: Media,
                  data_url: str | None) -> dict:
    """{url, muted, plugin} for a manifest (or preview) item. Needs a request context."""
    public = public_config(plugin, config)
    data_url = data_url if plugin.data else None
    extra = {}
    hook = plugin.hook("manifest_extra")
    if hook is not None:
        extra = dict(hook(media, dict(config), Context(plugin)) or {})
    base_url = extra.pop("url", None)
    muted = extra.pop("muted", True)
    for reserved in ("key", "version", "render", "config", "data"):
        extra.pop(reserved, None)
    digest = _digest([public, data_url, plugin.version, extra])[:12]
    if plugin.render == "frame":
        url = url_for("static", filename=f"player/plugins/{plugin.key}/index.html")
        url += f"#c={digest}"
    elif isinstance(base_url, str) and base_url:
        url = f"{base_url.split('#', 1)[0]}#c={digest}"
    else:
        url = f"about:blank#aisignx-plugin={plugin.key}&c={digest}"
    block = {
        "key": plugin.key, "version": plugin.version, "render": plugin.render,
        "config": public, "data": data_url,
        "refresh_s": refresh_seconds(plugin, config) if data_url else None,
        "ends_itself": plugin.ends_itself, "locale": locale(),
        "attribution": plugin.attribution or None,
    }
    block.update(extra)
    return {"url": url, "muted": bool(muted), "plugin": block}


def manifest_fields(media: Media) -> dict | None:
    """plugin_fields() for a stored, playable item, wired to the player data endpoint.
    None when the plugin's manifest_extra() fails (the item is then left out)."""
    plugin = plugin_for(media)
    config = stored_config(media, plugin)
    data_url = url_for("player_api.plugin_data", media_id=media.id) if plugin.data else None
    try:
        return plugin_fields(plugin, config, media, data_url)
    except Exception:
        log.exception("plugin %s: manifest entry failed for media %s", plugin.key, media.id)
        return None


# --- items ------------------------------------------------------------------------------

def _thumbnail(media: Media, subtitle: str) -> None:
    from ..media import storage
    from ..media.thumbnails import title_card

    name = storage.new_stored_name("jpg")
    try:
        title_card(media.name, subtitle, storage.thumbs_dir() / name)
    except OSError:
        log.warning("could not draw the title card of %s", media.name, exc_info=True)
        media.thumbnail_status = THUMB_FAILED
        return
    old = media.thumbnail_name
    media.thumbnail_name, media.thumbnail_status = name, THUMB_OK
    if old and old != name:
        storage.remove_file(storage.thumbs_dir(), old)


def draw_thumbnail(media: Media) -> None:
    """Title card "name / plugin name" (used by media/thumbnails.regenerate too)."""
    plugin = plugin_for(media)
    _thumbnail(media, plugin.name if plugin else (media.plugin_key or "Plugin"))


def _duration(plugin: PluginDef, config: dict, duration: int | None) -> int:
    hook = plugin.hook("auto_duration")
    if hook is not None:
        auto = hook(dict(config))
        if isinstance(auto, int) and not isinstance(auto, bool) and auto > 0:
            return min(auto, 86400)
    return duration or plugin.default_duration


def create_item(plugin: PluginDef, name: str, duration: int | None, config: dict,
                user) -> Media:
    """A new plugin Media row with a title-card thumbnail (no commit)."""
    media = Media(name=name.strip()[:200], media_type=MEDIA_PLUGIN, plugin_key=plugin.key,
                  plugin_config=dict(config),
                  default_duration=_duration(plugin, config, duration),
                  uploaded_by_id=getattr(user, "id", None))
    _thumbnail(media, plugin.name)
    db.session.add(media)
    return media


def update_item(media: Media, plugin: PluginDef, name: str, duration: int | None,
                config: dict) -> list[str]:
    """Apply an edit (no commit). Returns the changed keys (never values)."""
    old_config = media.plugin_config if isinstance(media.plugin_config, dict) else {}
    changed = [f"config.{k}" for k in sorted(set(old_config) | set(config))
               if old_config.get(k) != config.get(k)]
    new_name = name.strip()[:200]
    new_duration = _duration(plugin, config, duration)
    if new_name != media.name:
        changed.insert(0, "name")
        media.name = new_name
    if new_duration != media.default_duration:
        changed.insert(1 if changed[:1] == ["name"] else 0, "default_duration")
        media.default_duration = new_duration
    if any(c.startswith("config.") for c in changed):
        media.plugin_config = dict(config)  # a new dict, so the JSON change is detected
    if "name" in changed:
        _thumbnail(media, plugin.name)
    return changed


def transient_item(plugin: PluginDef, name: str, config: dict) -> Media:
    """An unsaved Media row for previews (never added to the session)."""
    return Media(id=None, name=name or plugin.name, media_type=MEDIA_PLUGIN,
                 plugin_key=plugin.key, plugin_config=dict(config),
                 default_duration=plugin.default_duration)


def cache_dir_size() -> int:
    from .cache import get_cache

    return get_cache().size_bytes()

