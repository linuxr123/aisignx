"""Plugin data and asset endpoints (player API and the admin preview routes)."""
import json
import time

from aisignx.extensions import db
from aisignx.models import Media, Setting

from .conftest import bearer, login, make_user
from .plugin_fixtures import (add_item, echo_hooks, make_plugin_app, padmin, paired_with,  # noqa: F401
                              papp, pclient)

API = "/api/player/v1"


def _setup(app):
    make_user(app, "admin", "admin")
    media_id = add_item(app)
    return media_id, paired_with(app, media_id)


def test_requires_a_display(papp, pclient):
    media_id, _token = _setup(papp)
    resp = pclient.get(f"{API}/plugins/{media_id}/data")
    assert resp.status_code == 401 and resp.get_json()["error"] == "unauthorized"
    assert pclient.get(f"{API}/plugins/{media_id}/assets/{'a' * 32}").status_code == 401


def test_data_shape_etag_and_headers(papp, pclient):
    media_id, token = _setup(papp)
    resp = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    body = resp.get_json()
    assert set(body) == {"v", "fetched_at_ms", "stale", "error", "refresh_s", "data", "assets"}
    assert body["v"] == 1 and body["stale"] is False and body["error"] is None
    assert body["refresh_s"] == 60 and body["data"]["message"] == "Hello"
    assert abs(body["fetched_at_ms"] - time.time() * 1000) < 60000
    asset, = body["assets"]
    assert set(asset) == {"id", "mime", "size"} and asset["mime"] == "image/png"
    etag = resp.headers["ETag"]
    again = pclient.get(f"{API}/plugins/{media_id}/data",
                        headers={**bearer(token), "If-None-Match": etag})
    assert again.status_code == 304 and again.headers["ETag"] == etag


def test_not_found_cases(papp, pclient):
    media_id, token = _setup(papp)
    with papp.app_context():
        web = Media(name="W", media_type="web", url="https://example.com")
        db.session.add(web)
        db.session.commit()
        web_id = web.id
    native_id = add_item(papp, "echo_native", {"words": "x"})
    for mid in (web_id, native_id, 9999):
        resp = pclient.get(f"{API}/plugins/{mid}/data", headers=bearer(token))
        assert resp.status_code == 404 and resp.get_json()["error"] == "not_found"
    with papp.app_context():
        Setting.set("plugins.echo.enabled", "0")
        db.session.commit()
    assert pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token)).status_code == 404


def test_stale_copy_and_error_code_only(papp, pclient):
    media_id, token = _setup(papp)
    pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    echo_hooks(papp).FETCH_MODE["mode"] = "crash"
    store = papp.extensions["aisignx_plugin_cache"]
    for path in store.root.glob("*.json"):
        entry = json.loads(path.read_text())
        entry["expires_at"] = time.time() - 120
        path.write_text(json.dumps(entry))
    resp = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    body = resp.get_json()
    assert resp.status_code == 200 and body["stale"] is True
    assert body["error"] == "plugin_error" and body["data"]["message"] == "Hello"
    assert "internal.host" not in resp.get_data(as_text=True)


def test_cold_failure_is_503_with_retry_after(papp, pclient):
    media_id, token = _setup(papp)
    echo_hooks(papp).FETCH_MODE["mode"] = "fail"
    resp = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "upstream_unavailable"
    assert 55 <= int(resp.headers["Retry-After"]) <= 60


def test_warming_is_503_with_retry_after(tmp_path, monkeypatch):
    app = make_plugin_app(tmp_path, monkeypatch, PLUGIN_FETCH_SYNC=False)
    try:
        media_id, token = _setup(app)
        echo_hooks(app).FETCH_MODE["sleep"] = 2.5
        resp = app.test_client().get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
        assert resp.status_code == 503 and resp.get_json()["error"] == "warming"
        assert resp.headers["Retry-After"] == "5"
    finally:
        app.extensions["aisignx_plugin_worker"].stop()


def test_assets(papp, pclient):
    media_id, token = _setup(papp)
    other = add_item(papp, config={"message": "Other"})
    body = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token)).get_json()
    asset_id = body["assets"][0]["id"]
    resp = pclient.get(f"{API}/plugins/{media_id}/assets/{asset_id}", headers=bearer(token))
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert resp.data.startswith(b"\x89PNG")
    assert resp.headers["Cache-Control"] == "private, max-age=86400, immutable"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    for bad in ("A" * 32, "a" * 31, "../" + "a" * 29, "f" * 32):
        assert pclient.get(f"{API}/plugins/{media_id}/assets/{bad}",
                           headers=bearer(token)).status_code == 404
    # Another item that never fetched does not own this asset.
    assert pclient.get(f"{API}/plugins/{other}/assets/{asset_id}",
                       headers=bearer(token)).status_code == 404
    # A listed asset with a MIME type outside the allowlist is refused.
    store = papp.extensions["aisignx_plugin_cache"]
    for path in store.root.glob("*.json"):
        entry = json.loads(path.read_text())
        entry["assets"][0]["mime"] = "image/svg+xml"
        entry["prev_assets"] = []
        path.write_text(json.dumps(entry))
    assert pclient.get(f"{API}/plugins/{media_id}/assets/{asset_id}",
                       headers=bearer(token)).status_code == 404


def test_admin_preview_routes_need_login(papp, pclient):
    media_id, _token = _setup(papp)
    for url in (f"/plugins/items/{media_id}/data", f"/plugins/items/{media_id}/assets/{'a' * 32}",
                f"/plugins/items/{media_id}/preview", "/plugins/"):
        assert pclient.get(url).status_code == 302, url
    assert pclient.post("/plugins/echo/preview", data={"message": "x"}).status_code == 302


def test_viewer_preview_and_item_data(papp, pclient):
    media_id, _token = _setup(papp)
    make_user(papp, "viewer", "viewer")
    login(pclient, "viewer")
    page = pclient.get(f"/plugins/items/{media_id}/preview")
    assert page.status_code == 200 and b"data-plugin-preview" in page.data
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert b"keep-me-private" not in page.data
    assert f"/plugins/items/{media_id}/data".encode() in page.data
    body = pclient.get(f"/plugins/items/{media_id}/data").get_json()
    assert body["data"]["message"] == "Hello"
    asset = body["assets"][0]["id"]
    assert pclient.get(f"/plugins/items/{media_id}/assets/{asset}").status_code == 200
    assert pclient.post("/plugins/echo/preview", data={"message": "x"}).status_code == 403


def test_unsaved_form_preview(padmin, papp):
    resp = padmin.post("/plugins/echo/preview", data={"name": "", "message": "Draft",
                                                      "note": "draft-secret", "count": "2"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["config"]["message"] == "Draft" and "note" not in body["config"]
    assert "draft-secret" not in resp.get_data(as_text=True)
    item = body["item"]
    assert item["type"] == "plugin" and item["plugin"]["data"] == body["data_url"]
    assert body["data_url"].startswith("/plugins/preview/") and body["data_url"].endswith("/data")
    data = padmin.get(body["data_url"]).get_json()
    assert data["data"]["message"] == "Draft"
    asset = data["assets"][0]["id"]
    assert padmin.get(body["data_url"][:-4] + f"assets/{asset}").status_code == 200
    assert padmin.get("/plugins/preview/nope/data").status_code == 404
    bad = padmin.post("/plugins/echo/preview", data={"message": "", "count": "77"})
    assert bad.status_code == 400 and set(bad.get_json()["errors"]) == {"message", "count"}
    native = padmin.post("/plugins/echo_native/preview", data={"words": "yo"}).get_json()
    assert native["data_url"] is None and native["item"]["plugin"]["words"] == "yo"
