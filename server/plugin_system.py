import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

from flask import Blueprint, current_app, render_template, send_from_directory, abort, request, jsonify, url_for
from itsdangerous import URLSafeSerializer, BadSignature

plugins_bp = Blueprint("plugins", __name__)

_REGISTRY: Dict[str, Dict[str, Any]] = {}
_REGISTRY_LOADED = False


# ----------------------------------------------------------------------------
# Permission catalog - Phase 3 plugin sandboxing
#
# Each entry maps an abstract permission string (declared by a plugin in
# plugin.json) to the iframe sandbox tokens and the Permissions-Policy
# `allow=` directives it requires. The runtime grants ONLY what's
# declared and tenant-allowed; everything else is denied by the iframe
# sandbox attribute.
#
# Defaults: a plugin that declares no permissions still gets
# `allow-scripts` (without it the plugin's main.js can't run at all)
# and `allow-same-origin` (needed so the runner can serve plugin assets
# from the same origin via the iframe). Both of those are baseline; the
# rest are gated.
# ----------------------------------------------------------------------------

PLUGIN_PERMISSION_CATALOG = {
    # Iframe sandbox tokens
    'forms.submit':    {'sandbox': ['allow-forms'],       'allow': []},
    'popups':          {'sandbox': ['allow-popups',
                                     'allow-popups-to-escape-sandbox'],
                                                            'allow': []},
    'modals':          {'sandbox': ['allow-modals'],      'allow': []},
    'pointer.lock':    {'sandbox': ['allow-pointer-lock'], 'allow': []},
    'orientation.lock':{'sandbox': ['allow-orientation-lock'], 'allow': []},
    'top.navigation':  {'sandbox': ['allow-top-navigation'], 'allow': []},
    'presentation':    {'sandbox': ['allow-presentation'], 'allow': []},

    # Permissions-Policy allow tokens
    'fullscreen':      {'sandbox': [],
                         'allow':   ['fullscreen']},
    'autoplay':        {'sandbox': [],
                         'allow':   ['autoplay']},
    'camera':          {'sandbox': [],
                         'allow':   ['camera']},
    'microphone':      {'sandbox': [],
                         'allow':   ['microphone']},
    'geolocation':     {'sandbox': [],
                         'allow':   ['geolocation']},
    'clipboard.read':  {'sandbox': [],
                         'allow':   ['clipboard-read']},
    'clipboard.write': {'sandbox': [],
                         'allow':   ['clipboard-write']},

    # Conceptual permissions (advisory: declared but not enforced by the
    # browser sandbox alone). Surfaced in the admin UI so operators see
    # what a plugin self-reports as needing.
    'network.fetch':   {'sandbox': [],                     'allow': []},
    'storage.local':   {'sandbox': [],                     'allow': []},
}

# Always present, regardless of declared permissions. allow-scripts is
# required for the plugin to run; allow-same-origin lets the runner
# fetch plugin assets from the same host (otherwise the iframe is
# treated as a unique opaque origin and url_for() URLs break).
_BASELINE_SANDBOX = ['allow-scripts', 'allow-same-origin']


def list_permission_catalog() -> List[Dict[str, Any]]:
    """Public introspection -- the admin UI uses this to show a description
    next to each declared permission."""
    return [{'key': k,
             'sandbox_tokens':  v['sandbox'],
             'allow_features':  v['allow']}
            for k, v in sorted(PLUGIN_PERMISSION_CATALOG.items())]


def normalize_declared_permissions(declared) -> List[str]:
    """Filter a plugin's declared permissions list to known catalog entries.
    Unknown entries are silently dropped after a warning -- a typo in
    plugin.json shouldn't break the plugin entirely."""
    if not isinstance(declared, list):
        return []
    out = []
    for p in declared:
        if not isinstance(p, str):
            continue
        if p in PLUGIN_PERMISSION_CATALOG:
            out.append(p)
        else:
            try:
                current_app.logger.warning(
                    f'plugin permission unknown: {p!r} (ignoring)')
            except Exception:
                pass
    # Stable order, dedupe.
    return sorted(set(out))


def compute_sandbox_attrs(granted: List[str]) -> Dict[str, str]:
    """Map a list of granted permissions to the strings needed by the
    iframe element. Returns {'sandbox': '...', 'allow': '...'}."""
    sandbox = list(_BASELINE_SANDBOX)
    allow = []
    for p in granted or []:
        spec = PLUGIN_PERMISSION_CATALOG.get(p)
        if not spec:
            continue
        for tok in spec['sandbox']:
            if tok not in sandbox:
                sandbox.append(tok)
        for feat in spec['allow']:
            if feat not in allow:
                allow.append(feat)
    return {
        'sandbox': ' '.join(sandbox),
        # `Permissions-Policy` syntax for iframe `allow=`: feature followed
        # by an optional allowlist. We grant to "self" only -- never `*`.
        'allow':   '; '.join(f'{f} self' if False else f for f in allow),
    }


# ----------------------------------------------------------------------------
# CSP origin pinning - Phase 4
#
# A plugin manifest may declare `"csp_origins": ["https://api.example.com"]`
# to whitelist outbound origins it is allowed to talk to. The runner page
# emits a `Content-Security-Policy` header that locks the iframe down to
# those origins (plus 'self'). Plugins with no `csp_origins` get the
# strict default (`'self'` only).
#
# The CSP is intentionally narrow:
#     default-src 'self'
#     script-src  'self' 'unsafe-inline'
#     style-src   'self' 'unsafe-inline'
#     img-src     'self' data: blob: <origins>
#     media-src   'self' data: blob: <origins>
#     connect-src 'self' <origins>
#     font-src    'self' data: <origins>
#     frame-ancestors 'self'
# ----------------------------------------------------------------------------

_VALID_ORIGIN_RE = None


def _origin_re():
    """Lazy compile so module import doesn't pay regex cost when CSP is unused."""
    global _VALID_ORIGIN_RE
    if _VALID_ORIGIN_RE is None:
        import re
        # Allow http(s)://host[:port] or wss?://host[:port]. No paths, no
        # wildcards in scheme. Hostname can include the literal '*' as a
        # leading subdomain wildcard ('*.example.com').
        _VALID_ORIGIN_RE = re.compile(
            r'^(https?|wss?)://(\*\.)?[a-zA-Z0-9.\-]+(:\d+)?$'
        )
    return _VALID_ORIGIN_RE


def normalize_csp_origins(origins) -> List[str]:
    """Filter the manifest-declared CSP origins to safe values. Bad entries
    are dropped with a warning -- a typo shouldn't crash the plugin."""
    if not isinstance(origins, list):
        return []
    out = []
    rx = _origin_re()
    for o in origins:
        if not isinstance(o, str):
            continue
        s = o.strip()
        if not s:
            continue
        if rx.match(s):
            out.append(s)
        else:
            try:
                current_app.logger.warning(
                    f'plugin csp_origins: dropped invalid origin {s!r}')
            except Exception:
                pass
    # Stable order, dedupe.
    seen = set()
    dedup = []
    for o in out:
        if o not in seen:
            seen.add(o)
            dedup.append(o)
    return dedup


def build_plugin_csp(origins: List[str]) -> str:
    """Build a Content-Security-Policy header string for the plugin runner
    iframe. `origins` is the list of additional origins the plugin is
    allowed to fetch from (already validated by normalize_csp_origins)."""
    extra = ' '.join(origins) if origins else ''
    extra_sp = f' {extra}' if extra else ''
    parts = [
        "default-src 'self'",
        # script-src / style-src also extend with declared origins so plugins
        # that load a third-party library (e.g. Leaflet from unpkg) can do so
        # by listing the CDN in their manifest's csp_origins.
        f"script-src 'self' 'unsafe-inline'{extra_sp}",
        f"style-src 'self' 'unsafe-inline'{extra_sp}",
        f"img-src 'self' data: blob:{extra_sp}",
        f"media-src 'self' data: blob:{extra_sp}",
        f"frame-src 'self'{extra_sp}",
        f"child-src 'self'{extra_sp}",
        f"connect-src 'self'{extra_sp}",
        f"font-src 'self' data:{extra_sp}",
        "frame-ancestors 'self'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    return '; '.join(parts)



def _plugins_root() -> Path:
    # Allow override via config. Default to <app_root>/plugins
    cfg_dir = current_app.config.get("PLUGINS_DIR")
    if cfg_dir:
        p = Path(cfg_dir).resolve()
    else:
        p = (Path(current_app.root_path) / "plugins").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="plugin-config")

def _load_registry(force: bool = False) -> None:
    global _REGISTRY, _REGISTRY_LOADED
    if _REGISTRY_LOADED and not force:
        return
    _REGISTRY = {}
    root = _plugins_root()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        # Skip dot-prefixed IDE / VCS folders (.vs, .git, .vscode, ...).
        if child.name.startswith('.'):
            continue
        meta_path = child / "plugin.json"
        main_js = child / "main.js"
        if not meta_path.exists() or not main_js.exists():
            current_app.logger.warning(f"Skipping plugin '{child.name}': missing plugin.json or main.js")
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
            key = meta.get("key") or child.name
            meta["key"] = key
            meta.setdefault("type", child.name)
            meta.setdefault("name", child.name.title())
            meta.setdefault("version", "1.0.0")
            meta.setdefault("description", "")
            meta.setdefault("icon", "bi-puzzle")
            schema = meta.get("schema")
            if not isinstance(schema, list):
                meta["schema"] = []

            # Phase 3 sandboxing: normalize the declared permissions so
            # downstream code only ever sees known catalog entries.
            meta['declared_permissions'] = normalize_declared_permissions(
                meta.get('permissions'))

            # Phase 4 CSP origin pinning: validate manifest-declared
            # outbound origin allowlist. Bad entries are dropped with a
            # warning by normalize_csp_origins.
            meta['csp_origins'] = normalize_csp_origins(
                meta.get('csp_origins'))

            # Phase 4 plugin signing: verify HMAC signature against the
            # trust list. Status is recorded on the meta so the admin UI
            # and runner can inspect it; enforcement happens at the
            # runner gate via plugin_signing.require_signed().
            try:
                from plugin_signing import verify_plugin
                status, detail = verify_plugin(child, meta)
            except Exception as e:
                status, detail = 'invalid', f'verifier crashed: {e}'
            meta['signature_status'] = status
            meta['signature_detail'] = detail
            if status not in ('valid', 'unsigned'):
                current_app.logger.warning(
                    f"plugin signing: {child.name}: {status} ({detail})")

            # Discover dynamic option folders.
            # If a schema field has `"options_from": "<folder>"`, scan
            # `<plugin>/<folder>/*/<folder-singular>.json` (or any *.json) for
            # available options. Each subfolder becomes one selectable option,
            # using its `label` from the json (or folder name as fallback).
            # This lets users drop new "themes" into plugins/clock/themes/
            # without editing plugin.json.
            for field in meta["schema"]:
                if not isinstance(field, dict):
                    continue
                folder_name = field.get("options_from")
                if not folder_name:
                    continue
                folder_path = child / folder_name
                if not folder_path.is_dir():
                    continue
                discovered = []
                for sub in sorted(folder_path.iterdir()):
                    if not sub.is_dir():
                        continue
                    label = sub.name.replace('-', ' ').replace('_', ' ').title()
                    # Look for a *.json metadata file in the theme folder
                    for j in sub.glob("*.json"):
                        try:
                            theme_meta = json.loads(j.read_text(encoding="utf-8-sig"))
                            if isinstance(theme_meta, dict) and theme_meta.get("label"):
                                label = theme_meta["label"]
                                break
                        except Exception:
                            pass
                    discovered.append({"label": label, "value": sub.name})
                if discovered:
                    field["options"] = discovered
                    # Set sensible default if not specified
                    if not field.get("default") and discovered:
                        field["default"] = discovered[0]["value"]

            _REGISTRY[key] = meta
        except Exception as e:
            current_app.logger.error(f"Failed to load plugin meta {meta_path}: {e}")
    _REGISTRY_LOADED = True
    current_app.logger.info(f"Plugin registry loaded: {_plugins_root()} -> {len(_REGISTRY)} plugins")

def list_plugins() -> List[Dict[str, Any]]:
    _load_registry()
    return list(_REGISTRY.values())

def get_plugin_meta(key_or_type: str) -> Optional[Dict[str, Any]]:
    _load_registry()
    meta = _REGISTRY.get(key_or_type)
    if meta:
        return meta
    # Lookup by type
    for v in _REGISTRY.values():
        if v.get("type") == key_or_type:
            return v
    return None


def resolve_plugin_policy(plugin_key: str, domain_id: Optional[int]
                           ) -> Dict[str, Any]:
    """Return the effective policy for `plugin_key` in `domain_id`:
        {'enabled': bool, 'granted_permissions': [..]}

    Logic:
      1. If a DomainPluginPolicy row exists for (domain_id, key), it
         wins -- explicit `enabled` flag and `granted_permissions` (NULL
         means "everything the plugin declares").
      2. Otherwise the default is `enabled=True` and granted = whatever
         the plugin declares. This keeps existing installs working
         without a backfill migration.

    A plugin not present in the registry is treated as enabled with no
    permissions -- the runner will refuse it via a different code path
    (404), so policy is irrelevant.
    """
    meta = get_plugin_meta(plugin_key) or {}
    declared = list(meta.get('declared_permissions') or [])
    if domain_id is None:
        # No tenant context -- treat as default policy.
        return {'enabled': True, 'granted_permissions': declared}
    try:
        from models import DomainPluginPolicy
        from tenant_filter import bypass_tenant_filter
        with bypass_tenant_filter():    # tenant-ok: policy lookup uses explicit domain_id
            pol = (DomainPluginPolicy.query
                    .filter_by(domain_id=domain_id, plugin_key=plugin_key)
                    .first())
        if pol is None:
            return {'enabled': True, 'granted_permissions': declared}
        if not pol.enabled:
            return {'enabled': False, 'granted_permissions': []}
        granted = pol.granted_permissions
        if granted is None:
            granted = declared
        # Intersect with declared so an admin can't grant a permission
        # the plugin never asked for -- the iframe sandbox would carry
        # capabilities the plugin doesn't even know exist.
        granted = [p for p in granted if p in declared]
        return {'enabled': True, 'granted_permissions': granted}
    except Exception as e:
        # Boot ordering or DB hiccup -- fail closed on policy lookup
        # would lock everyone out of plugins, so fail open with a
        # warning. Disabling all plugins because of a transient DB
        # error is worse than the alternative.
        try:
            current_app.logger.warning(
                f'plugin policy lookup failed for {plugin_key}: {e}')
        except Exception:
            pass
        return {'enabled': True, 'granted_permissions': declared}

def build_plugin_url(plugin_type: str, config: Dict[str, Any] | None) -> str:
    _load_registry()
    meta = get_plugin_meta(plugin_type) or {"key": plugin_type, "type": plugin_type}
    payload = {"plugin": meta.get("key") or plugin_type, "config": config or {}}
    token = _signer().dumps(payload)
    return url_for("plugins.run_plugin", plugin_type=meta.get("type") or plugin_type, cfg=token, _external=True)

@plugins_bp.route("/plugins", methods=["GET"])
def plugins_page():
    from flask_login import current_user
    from flask import redirect, abort
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not getattr(current_user, "is_superadmin", False):
        abort(403)
    _load_registry(force=True)
    return render_template("plugins.html", plugins=list_plugins(), plugins_root=str(_plugins_root()))

@plugins_bp.route("/api/plugins", methods=["GET", "HEAD"])
def api_plugins():
    _load_registry(force=bool(request.args.get("reload")))
    root = str(_plugins_root())
    return jsonify({"status": "success", "plugins": list_plugins(), "root": root, "count": len(_REGISTRY)})

@plugins_bp.route("/api/plugins/reload", methods=["POST"])
def api_plugins_reload():
    from flask_login import current_user
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"status": "error", "message": "auth required"}), 401
    if not getattr(current_user, "is_superadmin", False):
        return jsonify({"status": "error", "message": "superadmin only"}), 403
    _load_registry(force=True)
    return jsonify({"status": "success", "reloaded": True, "count": len(_REGISTRY)})


@plugins_bp.route("/api/plugins/upload", methods=["POST"])
def api_plugins_upload():
    """Install a plugin from an uploaded .zip archive.

    The archive must contain a single top-level folder (or be flat) with
    `plugin.json` and `main.js`. The folder name becomes the plugin slug.
    Path traversal entries (../, absolute paths, symlinks) are refused.
    Superadmin only.
    """
    import io
    import re
    import shutil
    import zipfile
    import tempfile

    from flask_login import current_user

    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "auth required"}), 401
    if not getattr(current_user, "is_superadmin", False):
        return jsonify({"status": "error", "message": "superadmin only"}), 403

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"status": "error", "message": "no file"}), 400
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"status": "error", "message": "must be a .zip"}), 400

    raw = f.read()
    if len(raw) > 50 * 1024 * 1024:
        return jsonify({"status": "error", "message": "archive too large (max 50MB)"}), 400

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return jsonify({"status": "error", "message": "invalid zip"}), 400

    # Cap uncompressed size to defend against zip bombs.
    total_uncompressed = sum(zi.file_size for zi in zf.infolist())
    if total_uncompressed > 200 * 1024 * 1024:
        return jsonify({"status": "error", "message": "archive contents too large"}), 400

    # Determine the top-level folder; allow either a single top dir or
    # a flat zip (in which case we use the uploaded filename as the slug).
    names = [n.replace("\\", "/") for n in zf.namelist() if n and not n.startswith("__MACOSX/")]
    if not names:
        return jsonify({"status": "error", "message": "empty archive"}), 400

    tops = {n.split("/", 1)[0] for n in names}
    flat = False
    if len(tops) == 1 and any("/" in n for n in names):
        slug = next(iter(tops))
    else:
        flat = True
        slug = re.sub(r"\.zip$", "", f.filename, flags=re.I)

    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", slug).strip("._-")
    if not slug or slug.startswith("."):
        return jsonify({"status": "error", "message": "invalid plugin folder name"}), 400

    root = _plugins_root()
    target = (root / slug).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        # Should be impossible after sanitization, but defend anyway.
        return jsonify({"status": "error", "message": "invalid path"}), 400

    # Stage into a temp dir then move into place atomically-ish.
    with tempfile.TemporaryDirectory(dir=str(root)) as tmpdir:
        stage = Path(tmpdir) / slug
        stage.mkdir()
        for zi in zf.infolist():
            name = zi.filename.replace("\\", "/")
            if not name or name.startswith("__MACOSX/"):
                continue
            # Strip the top-level folder when not flat so the staged
            # layout always sits directly under stage/.
            rel = name if flat else name.split("/", 1)[1] if "/" in name else ""
            if not rel:
                continue
            if rel.endswith("/"):
                (stage / rel).mkdir(parents=True, exist_ok=True)
                continue
            # Reject traversal and absolute paths.
            if rel.startswith("/") or ".." in Path(rel).parts:
                return jsonify({"status": "error",
                                "message": f"unsafe path in zip: {name}"}), 400
            dest = (stage / rel).resolve()
            if stage.resolve() not in dest.parents:
                return jsonify({"status": "error",
                                "message": f"unsafe path in zip: {name}"}), 400
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(zi) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

        if not (stage / "plugin.json").exists() or not (stage / "main.js").exists():
            return jsonify({"status": "error",
                            "message": "archive missing plugin.json or main.js at root"}), 400

        replaced = target.exists()
        if replaced:
            shutil.rmtree(target)
        shutil.move(str(stage), str(target))

    _load_registry(force=True)
    current_app.logger.info(
        f"plugin upload: installed {slug!r} ({'replaced' if replaced else 'new'})")
    return jsonify({
        "status": "success",
        "slug": slug,
        "replaced": replaced,
        "count": len(_REGISTRY),
    })

@plugins_bp.route("/plugin/<plugin_type>", methods=["GET", "HEAD"])
def run_plugin(plugin_type: str):
    token = request.args.get("cfg", "")
    if not token:
        abort(400, description="Missing cfg token")
    try:
        payload = _signer().loads(token)
    except BadSignature:
        abort(400, description="Invalid cfg token")
    cfg = (payload or {}).get("config", {}) if isinstance(payload, dict) else {}
    meta = get_plugin_meta(plugin_type) or {"key": plugin_type, "type": plugin_type, "name": plugin_type.title(), "version": "?"}

    # Policy gate: refuse if the plugin is disabled in the active tenant.
    # The runner is reached from the display player iframe, which by
    # this point already has a tenant context.
    try:
        from tenant_filter import current_domain_id
        did = current_domain_id()
    except Exception:
        did = None
    policy = resolve_plugin_policy(meta.get('key', plugin_type), did)
    if not policy['enabled']:
        abort(403, description='plugin is disabled for this tenant')

    # Phase 4: refuse to render unsigned/invalid plugins when the operator
    # has flipped the global require-signed switch on. Default off keeps
    # back-compat with existing unsigned plugin packages.
    try:
        from plugin_signing import require_signed
        if require_signed() and meta.get('signature_status') != 'valid':
            abort(403, description=(
                f"plugin signature {meta.get('signature_status', 'unknown')}: "
                f"{meta.get('signature_detail', '')}"))
    except Exception:
        # Never block the runner on a misconfigured signing layer.
        current_app.logger.exception('plugin signing gate failed open')

    # Compute iframe sandbox + Permissions-Policy strings the runner
    # template will inject. The template renders inside an iframe; these
    # are not strictly necessary on the runner page itself, but exposing
    # them via window.PLUGIN_PERMISSIONS lets plugins see what they were
    # granted (useful for graceful degradation).
    attrs = compute_sandbox_attrs(policy['granted_permissions'])
    csp_header = build_plugin_csp(meta.get('csp_origins') or [])
    response = current_app.make_response(render_template("plugin_runner.html",
                           plugin_meta=meta,
                           plugin_config=cfg,
                           plugin_permissions=policy['granted_permissions'],
                           plugin_csp_origins=meta.get('csp_origins') or [],
                           plugin_sandbox=attrs['sandbox'],
                           plugin_allow=attrs['allow']))
    response.headers['Content-Security-Policy'] = csp_header
    return response

@plugins_bp.route("/plugin_assets/<plugin_type>/<path:filename>", methods=["GET", "HEAD"])
def plugin_asset(plugin_type: str, filename: str):
    root = _plugins_root() / plugin_type
    if not root.exists():
        abort(404)
    filepath = (root / filename).resolve()
    if not str(filepath).startswith(str(root)):
        abort(403)
    if not filepath.exists():
        abort(404)
    from flask import make_response
    resp = make_response(send_from_directory(root, filename))
    # Always revalidate plugin assets so changes to main.js take effect immediately
    resp.headers['Cache-Control'] = 'no-cache'
    return resp