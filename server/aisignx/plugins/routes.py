"""Admin pages for plugins (/plugins).

    GET  /plugins/                              viewer  list; admins: toggles and settings
    POST /plugins/settings                      admin   contact for the User-Agent, locale
    POST /plugins/<key>/settings                admin   enabled flag and org settings
    POST /plugins/cache/clear                   admin   empty the data cache
    GET  /plugins/new                           editor  choose an enabled plugin
    GET/POST /plugins/new/<key>                 editor  create a plugin item
    GET/POST /plugins/items/<id>/edit           editor  edit a plugin item
    GET  /plugins/items/<id>/preview            viewer  preview page (16:9 / 9:16)
    GET  /plugins/items/<id>/data|assets/<id>   viewer  data for that preview
    POST /plugins/<key>/preview                 editor  validate an unsaved form for preview
    GET  /plugins/preview/<token>/data|assets   editor  data for an unsaved preview
    GET  /plugins/geocode?q=                    editor  place lookup for location fields
"""
import secrets
import threading
import time
from collections import OrderedDict, deque

from flask import abort, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..audit import service as audit
from ..auth.decorators import admin_required, editor_required
from ..extensions import db
from ..models import Media, Setting
from ..models.media import MEDIA_IMAGE
from ..player.api import api_error
from ..player.broker import get_broker
from . import bp, data_api, schema, service
from .registry import get_registry

PREVIEW_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self'; frame-src http: https:; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
MAX_DURATION = 86400
PICKER_MAX = 500
PREVIEW_TTL_S = 1800
PREVIEW_MAX = 256
GEOCODE_PER_USER_MIN = 20

_previews: OrderedDict = OrderedDict()  # token -> (plugin key, config, user id, created)
_previews_lock = threading.Lock()
_geo_calls: dict = {}  # user id -> deque of call times
_geo_lock = threading.Lock()


def _plugin_or_404(key: str, enabled_only: bool = False):
    plugin = get_registry().get(key)
    if plugin is None or (enabled_only and not service.is_enabled(plugin)):
        abort(404)
    return plugin


def _item_or_404(media_id: int):
    media = db.session.get(Media, media_id)
    plugin = service.plugin_for(media)
    if plugin is None:
        abort(404)
    return media, plugin


def _publish_manifest() -> None:
    broker = get_broker()
    if broker is not None:
        broker.publish_all("manifest", {})


def _with_csp(html: str):
    resp = make_response(html)
    resp.headers["Content-Security-Policy"] = PREVIEW_CSP
    return resp


# --- list and settings ------------------------------------------------------------------

def _contact_users(exclude_key: str | None = None) -> list[str]:
    """Plugins whose current settings need plugins.ua_contact (e.g. MET Norway)."""
    names = []
    for plugin in get_registry().all():
        if plugin.key == exclude_key:
            continue
        for fld in plugin.settings:
            if fld["type"] != "select":
                continue
            value = service.setting_value(plugin, fld["name"])
            if any(o.get("requires_contact") and o["value"] == value for o in fld["options"]):
                names.append(plugin.name)
    return names


@bp.get("/")
@login_required
def index():
    plugins = []
    for plugin in get_registry().all():
        hosts = list(plugin.data.hosts) if plugin.data else []
        plugins.append({
            "def": plugin, "enabled": service.is_enabled(plugin), "hosts": hosts,
            "secrets": service.secret_status(plugin),
            "settings": service.setting_values(plugin) if current_user.is_admin else {},
        })
    return render_template(
        "plugins/index.html", plugins=plugins, cache_size=service.cache_dir_size(),
        ua_contact=service.ua_contact(), locale=service.locale(),
        user_agent=service.user_agent())


@bp.post("/settings")
@admin_required
def global_settings():
    contact = (request.form.get("ua_contact") or "").strip()
    loc = (request.form.get("locale") or "").strip()
    if contact and not service.CONTACT_RE.match(contact):
        flash("Enter an e-mail address or a web address without spaces.", "danger")
        return redirect(url_for("plugins.index"), 303)
    if loc and not service.LOCALE_RE.match(loc):
        flash("Enter a language tag such as en, en-GB or nl-NL, or leave it empty.", "danger")
        return redirect(url_for("plugins.index"), 303)
    if not contact and _contact_users():
        flash("These plugins need the contact: " + ", ".join(_contact_users())
              + ". Change their provider first.", "danger")
        return redirect(url_for("plugins.index"), 303)
    new = {service.UA_CONTACT_KEY: contact, service.LOCALE_KEY: loc}
    changed = [k for k, v in new.items() if (Setting.get(k) or "") != v]
    for key, value in new.items():
        Setting.set(key, value)
    if changed:
        audit.record("plugin.settings", target_name="Plugins", detail={"changed": changed})
    db.session.commit()
    if changed:
        _publish_manifest()
    flash("Plugin settings saved.", "success")
    return redirect(url_for("plugins.index"), 303)


@bp.post("/<key>/settings")
@admin_required
def plugin_settings(key: str):
    plugin = _plugin_or_404(key)
    raw = schema.from_form(plugin.settings, request.form)
    values, errors = schema.validate(plugin.settings, raw, check_refs=False)
    for fld in plugin.settings:
        if fld["type"] == "select" and fld["name"] not in errors and not service.ua_contact():
            option = next((o for o in fld["options"] if o["value"] == values.get(fld["name"])), {})
            if option.get("requires_contact"):
                errors[fld["name"]] = (f"{option['label']} requires a contact e-mail or URL "
                                       "for the User-Agent. Set it above first.")
    if errors:
        for name, message in errors.items():
            label = next((f["label"] for f in plugin.settings if f["name"] == name), "Settings")
            flash(f"{plugin.name}: {label}: {message}", "danger")
        return redirect(url_for("plugins.index"), 303)
    new = {service.ENABLED_KEY.format(key=plugin.key): "1" if request.form.get("enabled") else "0"}
    for fld in plugin.settings:
        new[service.setting_key(plugin, fld["name"])] = schema.to_setting(fld, values[fld["name"]])
    changed = []
    for setting, value in new.items():
        current = Setting.get(setting)
        if current is None and setting.endswith(".enabled"):
            current = "1"
        if (current or "") != value:
            changed.append(setting)
            Setting.set(setting, value)
    if changed:
        audit.record("plugin.settings", target_name=plugin.name,
                     detail={"plugin": plugin.key, "changed": changed})
    db.session.commit()
    if changed:
        _publish_manifest()
    flash(f"{plugin.name}: settings saved.", "success")
    return redirect(url_for("plugins.index"), 303)


@bp.post("/cache/clear")
@admin_required
def clear_cache():
    from .cache import get_cache

    removed = get_cache().clear()
    audit.record("plugin.cache_clear", target_name="Plugin cache", detail={"files": removed})
    db.session.commit()
    flash(f"Plugin cache cleared ({removed} file{'s' if removed != 1 else ''}).", "success")
    return redirect(url_for("plugins.index"), 303)


# --- items ------------------------------------------------------------------------------

@bp.get("/new")
@editor_required
def choose():
    plugins = [p for p in get_registry().all() if service.is_enabled(p)]
    return render_template("plugins/choose.html", plugins=plugins)


def _display_values(plugin, config: dict, raw: dict, errors: dict) -> dict:
    """Values to show in the form: validated ones, else what was typed."""
    return {f["name"]: (raw.get(f["name"]) if f["name"] in errors else config.get(f["name"]))
            for f in plugin.schema}


def _read_form(plugin, check_refs: bool = True, geocode: bool = True):
    """(name, duration, config, display values, errors) from the submitted item form."""
    errors = {}
    name = (request.form.get("name") or "").strip()
    if not name:
        errors["name"] = "Enter a name."
    elif len(name) > 200:
        errors["name"] = "At most 200 characters."
    duration = None
    text = (request.form.get("duration") or "").strip()
    if text:
        try:
            duration = int(text)
        except ValueError:
            duration = 0
        if not 1 <= duration <= MAX_DURATION:
            errors["duration"] = f"Enter seconds from 1 to {MAX_DURATION}."
    raw = schema.from_form(plugin.schema, request.form)
    config, field_errors = service.validate_config(plugin, raw, check_refs=check_refs,
                                                   geocode=geocode)
    errors.update(field_errors)
    return name, duration, config, _display_values(plugin, config, raw, field_errors), errors


def _images() -> list[dict]:
    rows = (Media.query.filter(Media.media_type == MEDIA_IMAGE)
            .order_by(Media.created_at.desc(), Media.id.desc()).limit(PICKER_MAX).all())
    return [{"id": m.id, "name": m.name,
             "thumb": url_for("media.thumb", media_id=m.id, v=(m.thumbnail_name or "")[:8])
             if m.thumbnail_name else None} for m in rows]


def _render_form(plugin, *, media, name, duration, values, errors, status=200):
    types = {f["type"] for f in plugin.schema}
    entry = None
    if media is not None and not errors:
        entry = _stored_entry(media, plugin)
    html = render_template(
        "plugins/form.html", plugin=plugin, media=media, name=name, duration=duration,
        values=values, errors=errors, entry=entry,
        images=_images() if "media_multi" in types else [],
        timezones=schema.timezone_names() if "timezone" in types else [],
        hosts=list(plugin.data.hosts) if plugin.data else [],
        secrets=service.secret_status(plugin),
        preview_url=url_for("plugins.preview_form", key=plugin.key),
        server_time=int(time.time() * 1000))
    resp = _with_csp(html)
    resp.status_code = status
    return resp


@bp.route("/new/<key>", methods=["GET", "POST"])
@editor_required
def new_item(key: str):
    plugin = _plugin_or_404(key, enabled_only=True)
    if request.method == "POST":
        name, duration, config, values, errors = _read_form(plugin)
        if not errors:
            media = service.create_item(plugin, name, duration, config, current_user)
            audit.record("media.create_plugin", media, detail={"plugin": plugin.key})
            db.session.commit()
            flash(f"{plugin.name} item added.", "success")
            return redirect(url_for("media.index"), 303)
        return _render_form(plugin, media=None, name=name, duration=duration, values=values,
                            errors=errors, status=400)
    return _render_form(plugin, media=None, name=plugin.name, duration=None,
                        values=schema.defaults(plugin.schema), errors={})


@bp.route("/items/<int:media_id>/edit", methods=["GET", "POST"])
@editor_required
def edit_item(media_id: int):
    media, plugin = _item_or_404(media_id)
    if request.method == "POST":
        name, duration, config, values, errors = _read_form(plugin)
        if not errors:
            changed = service.update_item(media, plugin, name, duration, config)
            if changed:
                audit.record("media.update", media, detail={"changed": changed})
            db.session.commit()
            flash("Plugin item updated.", "success")
            return redirect(url_for("media.index"), 303)
        return _render_form(plugin, media=media, name=name, duration=duration, values=values,
                            errors=errors, status=400)
    stored = media.plugin_config if isinstance(media.plugin_config, dict) else {}
    values = {**schema.defaults(plugin.schema), **stored}
    return _render_form(plugin, media=media, name=media.name, duration=media.default_duration,
                        values=values, errors={})


# --- preview ----------------------------------------------------------------------------

def _preview_entry(plugin, config: dict, media: Media, data_url: str | None) -> dict:
    """A manifest-shaped item for plugin_preview.js."""
    fields = service.plugin_fields(plugin, config, media, data_url)
    return {"id": 0, "media_id": media.id or 0, "type": "plugin", "url": fields["url"],
            "mime": None, "sha256": None, "size": None, "width": None, "height": None,
            "duration_ms": (media.default_duration or plugin.default_duration) * 1000,
            "media_duration_ms": None, "transition": "fade", "fit": "fit",
            "muted": fields["muted"], "plugin": fields["plugin"]}


def _stored_entry(media: Media, plugin) -> dict | None:
    """The preview item of a saved plugin item (None when its config no longer validates)."""
    config = service.stored_config(media, plugin)
    if config is None:
        return None
    data_url = url_for("plugins.item_data", media_id=media.id) if plugin.data else None
    return _preview_entry(plugin, config, media, data_url)


@bp.get("/items/<int:media_id>/preview")
@login_required
def preview(media_id: int):
    media, plugin = _item_or_404(media_id)
    entry = _stored_entry(media, plugin)
    html = render_template("plugins/preview.html", plugin=plugin, media=media, entry=entry,
                           enabled=service.is_enabled(plugin),
                           server_time=int(time.time() * 1000))
    return _with_csp(html)


def _admin_found(media_id: int):
    media = db.session.get(Media, media_id)
    plugin = service.plugin_for(media)
    if plugin is None or plugin.data is None:
        return None
    config = service.stored_config(media, plugin)
    return None if config is None else (plugin, config)


@bp.get("/items/<int:media_id>/data")
@login_required
def item_data(media_id: int):
    found = _admin_found(media_id)
    if found is None:
        return api_error(404, "not_found", "No such plugin data.")
    return data_api.data_response(*found)


@bp.get("/items/<int:media_id>/assets/<asset_id>")
@login_required
def item_asset(media_id: int, asset_id: str):
    found = _admin_found(media_id)
    if found is None:
        return api_error(404, "not_found", "No such asset.")
    return data_api.asset_response(*found, asset_id)


def _remember_preview(plugin, config: dict) -> str:
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _previews_lock:
        for old in [t for t, v in _previews.items() if now - v[3] > PREVIEW_TTL_S]:
            del _previews[old]
        _previews[token] = (plugin.key, dict(config), current_user.id, now)
        while len(_previews) > PREVIEW_MAX:
            _previews.popitem(last=False)
    return token


def _preview_found(token: str):
    with _previews_lock:
        hit = _previews.get(token)
    if hit is None or time.time() - hit[3] > PREVIEW_TTL_S or hit[2] != current_user.id:
        return None
    plugin = get_registry().get(hit[0])
    return None if plugin is None or plugin.data is None else (plugin, hit[1])


@bp.post("/<key>/preview")
@editor_required
def preview_form(key: str):
    plugin = _plugin_or_404(key)
    name, _duration, config, _values, errors = _read_form(plugin, check_refs=False)
    errors.pop("name", None)
    errors.pop("duration", None)
    if errors:
        return jsonify(error="invalid", errors=errors), 400
    data_url = None
    if plugin.data:
        token = _remember_preview(plugin, config)
        data_url = url_for("plugins.preview_data", token=token)
    media = service.transient_item(plugin, name, config)
    public = service.public_config(plugin, config)
    return jsonify(config=public, data_url=data_url,
                   item=_preview_entry(plugin, config, media, data_url))


@bp.get("/preview/<token>/data")
@editor_required
def preview_data(token: str):
    found = _preview_found(token)
    if found is None:
        return api_error(404, "not_found", "This preview has expired. Refresh it.")
    return data_api.data_response(*found)


@bp.get("/preview/<token>/assets/<asset_id>")
@editor_required
def preview_asset(token: str, asset_id: str):
    found = _preview_found(token)
    if found is None:
        return api_error(404, "not_found", "No such asset.")
    return data_api.asset_response(*found, asset_id)


# --- geocoding --------------------------------------------------------------------------

def _geo_allowed(user_id: int) -> bool:
    now = time.monotonic()
    with _geo_lock:
        calls = _geo_calls.setdefault(user_id, deque())
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= GEOCODE_PER_USER_MIN:
            return False
        calls.append(now)
        return True


@bp.get("/geocode")
@editor_required
def geocode():
    from . import geo
    from .fetch import FetchError, RateLimited

    q = (request.args.get("q") or "").strip()
    if not q or len(q) > geo.MAX_QUERY:
        return api_error(400, "bad_request", f"Enter 1 to {geo.MAX_QUERY} characters.")
    if not _geo_allowed(current_user.id):
        return api_error(429, "rate_limited", "Too many lookups. Wait a minute.", retry_after=60)
    try:
        results = geo.geocode(q)
    except RateLimited:
        return api_error(429, "rate_limited", "One lookup per second. Try again.",
                         retry_after=1)
    except FetchError as exc:
        return api_error(502, exc.code, "The place lookup service did not answer.")
    return jsonify(results=results[:geo.MAX_RESULTS])
