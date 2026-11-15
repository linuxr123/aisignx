"""Bundled plugins: one folder per plugin, named after its key.

    bundled/<key>/plugin.json    the definition (UTF-8 without a byte-order mark)
    bundled/<key>/__init__.py    optional hooks: normalize, fetch, manifest_extra,
                                 playable, auto_duration (see plugins/service.py)
    static/player/plugins/<key>/ the player files listed in plugin.json

The registry loads every subfolder that holds a plugin.json when the app starts.
"""
