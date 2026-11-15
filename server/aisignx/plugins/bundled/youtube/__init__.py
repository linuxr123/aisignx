"""YouTube plugin hooks: check the links at save, build the embed URL for the manifest.

The player side is static/player/plugins/youtube/native.js (render mode "native"): it
builds a cross-origin youtube-nocookie iframe from the manifest item's `url` and talks to
it over the IFrame API's postMessage bridge. There is no server data. See urls.py for
the accepted link forms.
"""
from aisignx.plugins.schema import SchemaError

from . import urls

_FIELD_FOR = {"videos": "videos", "playlist": "playlist", "live": "live"}


def normalize(config: dict, ctx) -> dict:
    """Refuse links the player could not embed. The config is stored as typed (so the
    form shows the editor's own links), which keeps this idempotent."""
    source = config.get("source") or "videos"
    try:
        urls.build(config)
    except urls.UrlError as exc:
        raise SchemaError(str(exc), field=_FIELD_FOR.get(source, "videos")) from None
    return config


def playable(media, config: dict, ctx) -> bool:
    try:
        urls.build(config)
    except urls.UrlError:
        return False
    return True


def manifest_extra(media, config: dict, ctx) -> dict:
    """url (the embed URL), muted, and plugin.embed for native.js."""
    built = urls.build(config, getattr(ctx, "locale", "") or "")
    return {"url": built["url"], "muted": bool(config.get("mute", True)),
            "embed": built["embed"]}
