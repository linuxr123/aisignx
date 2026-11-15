"""The plugin registry: discovery and validation of plugin.json, built once per app.

Plugins ship with the app in aisignx/plugins/bundled/<key>/ (plugin.json plus optional
Python hooks in __init__.py); their player files live in static/player/plugins/<key>/.
PLUGIN_DIRS (tests only, never read from the environment) adds more folders laid out
the same way; such a plugin keeps its player files in <folder>/<key>/static/ when that
exists, else in static/player/plugins/<key>/.

A bad plugin.json fails app start with PluginLoadError: that is a packaging bug.
The registry lives in app.extensions["aisignx_plugins"] (no module global), so every
app (and every test) gets its own.
"""
import importlib
import importlib.util
import ipaddress
import json
import re
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from flask import Flask, current_app

from . import schema

EXT_KEY = "aisignx_plugins"
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
FILE_RE = re.compile(r"^[a-z0-9_\-./]+$")
HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                     r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
ENV_RE = re.compile(r"^AISIGNX_[A-Z0-9_]{1,60}$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,40}$")
HOOKS = ("normalize", "fetch", "manifest_extra", "playable", "auto_duration")
RENDER_MODES = ("frame", "native")
SETTING_KEY_MAX = 64  # Setting.key column length
MEMO_MAX = 1024
BUNDLED_DIR = Path(__file__).resolve().parent / "bundled"
_BOM = b"\xef\xbb\xbf"


class PluginLoadError(RuntimeError):
    """A plugin.json (or its files or hooks) is invalid; the app refuses to start."""


@dataclass(frozen=True)
class DataSpec:
    ttl: int
    min_ttl: int
    fetch_budget_s: int
    keys: tuple
    hosts: tuple
    rate: dict
    max_bytes: int


@dataclass
class PluginDef:
    key: str
    name: str
    version: str
    icon: str
    description: str
    render: str
    files: tuple
    player_script: str | None
    player_style: str | None
    default_duration: int
    ends_itself: bool
    data: DataSpec | None
    schema: list
    settings: list
    secrets: tuple
    attribution: str
    terms: str
    folder: Path
    static_dir: Path
    bundled: bool
    hooks: ModuleType | None = field(default=None, repr=False)
    audio: bool = False  # has sound: the playlist item sets mute and volume

    def hook(self, name: str):
        """The optional hook function `name`, or None."""
        func = getattr(self.hooks, name, None) if self.hooks is not None else None
        return func if callable(func) else None

    def setting_field(self, name: str) -> dict | None:
        return next((f for f in self.settings if f["name"] == name), None)

    def static_files(self) -> list[str]:
        """Every player file, as paths under static/ ("player/plugins/<key>/main.js")."""
        names = list(self.files)
        for extra in (self.player_script, self.player_style):
            if extra and extra not in names:
                names.append(extra)
        return [f"player/plugins/{self.key}/{name}" for name in names]


class Registry:
    def __init__(self, plugins: dict):
        self.plugins = plugins  # key -> PluginDef, in discovery order
        self._memo: OrderedDict = OrderedDict()
        self._memo_lock = threading.Lock()

    def get(self, key: str | None) -> PluginDef | None:
        return self.plugins.get(key or "")

    def all(self) -> list[PluginDef]:
        return list(self.plugins.values())

    def memo_get(self, key):
        with self._memo_lock:
            if key in self._memo:
                self._memo.move_to_end(key)
                return self._memo[key]
        return None

    def memo_put(self, key, value) -> None:
        with self._memo_lock:
            self._memo[key] = value
            self._memo.move_to_end(key)
            while len(self._memo) > MEMO_MAX:
                self._memo.popitem(last=False)


def get_registry(app: Flask | None = None) -> Registry:
    return (app or current_app).extensions[EXT_KEY]


def get_plugin(key: str | None) -> PluginDef | None:
    return get_registry().get(key)


# --- loading ----------------------------------------------------------------------------

def _fail(where, message: str):
    raise PluginLoadError(f"{where}: {message}")


def _read_json(path: Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith(_BOM):
        _fail(path, "must be UTF-8 without a byte-order mark")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        _fail(path, f"not valid UTF-8 JSON ({exc})")
    if not isinstance(data, dict):
        _fail(path, "must hold a JSON object")
    return data


def _int(where, raw: dict, name: str, default: int, lo: int, hi: int) -> int:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        _fail(where, f"{name} must be a whole number from {lo} to {hi}")
    return value


def _str(where, raw: dict, name: str, required: bool = False, limit: int = 500) -> str:
    value = raw.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        _fail(where, f"{name} must be a{' non-empty' if required else ''} string "
                     f"(at most {limit} characters)")
    return value.strip()


def _check_file(where, static_dir: Path, name) -> str:
    if (not isinstance(name, str) or not FILE_RE.match(name) or ".." in name
            or name.startswith("/") or name.endswith("/")):
        _fail(where, f"bad file name {name!r}")
    if not (static_dir / name).is_file():
        _fail(where, f"file {name!r} is missing from {static_dir}")
    return name


def _check_host(where, host) -> str:
    if not isinstance(host, str):
        _fail(where, f"bad host {host!r}")
    try:
        ipaddress.ip_address(host.strip("[]"))
        _fail(where, f"host {host!r}: IP literals are not allowed")
    except ValueError:
        pass
    if not HOST_RE.match(host):
        _fail(where, f"host {host!r}: use an exact lower-case host name (no wildcard or port)")
    return host


def _data_spec(where, raw, fields: list) -> DataSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _fail(where, "data must be an object")
    ttl = _int(where, raw, "ttl", 600, 10, 7 * 86400)
    min_ttl = _int(where, raw, "min_ttl", min(ttl, 60), 10, ttl)
    budget = _int(where, raw, "fetch_budget_s", 15, 1, 300)
    max_bytes = _int(where, raw, "max_bytes", 2 * 1024 * 1024, 1024, 50 * 1024 * 1024)
    names = {f["name"] for f in fields}
    keys = raw.get("keys", [])
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        _fail(where, "data.keys must be a list of field paths")
    for path in keys:
        if path.split(".", 1)[0] not in names:
            _fail(where, f"data.keys names unknown field {path!r}")
    hosts = raw.get("hosts", [])
    if not isinstance(hosts, list):
        _fail(where, "data.hosts must be a list")
    hosts = tuple(_check_host(where, h) for h in hosts)
    rate = raw.get("rate", {})
    if not isinstance(rate, dict):
        _fail(where, "data.rate must be an object")
    for host, per_min in rate.items():
        if host not in hosts:
            _fail(where, f"data.rate names host {host!r} that is not in data.hosts")
        if isinstance(per_min, bool) or not isinstance(per_min, int) or not 1 <= per_min <= 6000:
            _fail(where, f"data.rate[{host!r}] must be 1..6000 requests per minute")
    return DataSpec(ttl=ttl, min_ttl=min_ttl, fetch_budget_s=budget, keys=tuple(keys),
                    hosts=hosts, rate=dict(rate), max_bytes=max_bytes)


def _secrets(where, raw) -> tuple:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _fail(where, "secrets must be a list")
    out = []
    for item in raw:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not schema.NAME_RE.match(item["name"])
                or not isinstance(item.get("env"), str) or not ENV_RE.match(item["env"])):
            _fail(where, "each secret needs a name and an env (AISIGNX_...)")
        out.append({"name": item["name"], "env": item["env"],
                    "label": str(item.get("label") or item["name"])})
    return tuple(out)


def _load_hooks(where, folder: Path, key: str, bundled: bool) -> ModuleType | None:
    if not (folder / "__init__.py").is_file():
        return None
    try:
        if bundled:
            module = importlib.import_module(f"{__package__}.bundled.{key}")
        else:
            name = f"aisignx_test_plugin_{key}"
            spec = importlib.util.spec_from_file_location(
                name, folder / "__init__.py", submodule_search_locations=[str(folder)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
    except Exception as exc:  # a broken hook module must not start silently
        _fail(where, f"could not import hooks ({type(exc).__name__}: {exc})")
    return module


def load_plugin(folder: Path, static_root: Path, bundled: bool) -> PluginDef:
    """Read and validate one plugin folder. Raises PluginLoadError."""
    where = folder / "plugin.json"
    raw = _read_json(where)
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        _fail(where, f"bad key {key!r} (use ^[a-z][a-z0-9_]{{1,31}}$)")
    if key != folder.name:
        _fail(where, f"key {key!r} must equal the folder name {folder.name!r}")
    static_dir = static_root / key
    if not bundled and (folder / "static").is_dir():
        static_dir = folder / "static"
    try:
        fields = schema.check_fields(raw.get("schema"))
        settings = schema.check_fields(raw.get("settings"))
    except schema.SchemaError as exc:
        _fail(where, str(exc))
    for item in settings:
        if len(f"plugins.{key}.{item['name']}") > SETTING_KEY_MAX:
            _fail(where, f"setting {item['name']!r}: plugins.{key}.<name> must fit in "
                         f"{SETTING_KEY_MAX} characters")
    render = raw.get("render", "frame")
    if render not in RENDER_MODES:
        _fail(where, f"render must be one of {RENDER_MODES}")
    files = raw.get("files") or []
    if not isinstance(files, list):
        _fail(where, "files must be a list")
    files = tuple(_check_file(where, static_dir, name) for name in files)
    player_script = raw.get("player_script")
    player_style = raw.get("player_style")
    data = _data_spec(where, raw.get("data"), fields)
    if render == "frame":
        if "index.html" not in files:
            _fail(where, "a frame plugin must list index.html in files")
        if player_script or player_style:
            _fail(where, "player_script and player_style are for native plugins only")
    else:
        if not player_script:
            _fail(where, "a native plugin needs a player_script")
        if data is not None:
            _fail(where, "a native plugin cannot declare data")
        if not str(player_script).endswith(".js"):
            _fail(where, "player_script must be a .js file")
        _check_file(where, static_dir, player_script)
        if player_style:
            if not str(player_style).endswith(".css"):
                _fail(where, "player_style must be a .css file")
            _check_file(where, static_dir, player_style)
    icon = _str(where, raw, "icon", limit=40) or "puzzle"
    if not ICON_RE.match(icon):
        _fail(where, "icon must be a short lower-case name")
    hooks = _load_hooks(where, folder, key, bundled)
    plugin = PluginDef(
        key=key, name=_str(where, raw, "name", required=True, limit=80),
        version=_str(where, raw, "version", required=True, limit=32),
        icon=icon, description=_str(where, raw, "description"), render=render,
        files=files, player_script=player_script or None, player_style=player_style or None,
        default_duration=_int(where, raw, "default_duration", 10, 1, 86400),
        ends_itself=bool(raw.get("ends_itself", False)), data=data, schema=fields,
        settings=settings, secrets=_secrets(where, raw.get("secrets")),
        attribution=_str(where, raw, "attribution"), terms=_str(where, raw, "terms", limit=2000),
        folder=folder, static_dir=static_dir, bundled=bundled, hooks=hooks,
        audio=bool(raw.get("audio", False)))
    if not isinstance(raw.get("ends_itself", False), bool):
        _fail(where, "ends_itself must be true or false")
    if not isinstance(raw.get("audio", False), bool):
        _fail(where, "audio must be true or false")
    if data is not None and plugin.hook("fetch") is None:
        _fail(where, "a plugin with data needs a fetch() hook")
    if data is None and plugin.hook("fetch") is not None:
        _fail(where, "a fetch() hook needs a data section")
    return plugin


def _plugin_folders(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "plugin.json").is_file())


def discover(app: Flask) -> Registry:
    """Load every bundled plugin, then those in PLUGIN_DIRS (tests)."""
    static_root = Path(app.static_folder or "") / "player" / "plugins"
    plugins: dict = {}
    sources = [(BUNDLED_DIR, True)]
    sources += [(Path(d), False) for d in (app.config.get("PLUGIN_DIRS") or [])]
    for root, bundled in sources:
        for folder in _plugin_folders(root):
            plugin = load_plugin(folder, static_root, bundled)
            if plugin.key in plugins:
                _fail(folder / "plugin.json", f"duplicate plugin key {plugin.key!r}")
            plugins[plugin.key] = plugin
    return Registry(plugins)


def init_app(app: Flask) -> Registry:
    registry = discover(app)
    app.extensions[EXT_KEY] = registry
    return registry
