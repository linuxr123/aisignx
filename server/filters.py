def format_datetime(value, format='%Y-%m-%d %H:%M:%S'):
    """Format a datetime object to string"""
    if value is None:
        return ''
    return value.strftime(format)


def fromjson(value):
    """Parse a JSON string into a Python object inside a template. Used by
    smart-playlist editor to read ``Playlist.smart_rules`` (stored as TEXT).
    Returns an empty dict on any error so templates can still index keys."""
    import json as _json
    if value is None or value == '':
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return _json.loads(value)
    except Exception:
        return {}


def register_filters(app):
    app.template_filter('format_datetime')(format_datetime)
    app.template_filter('fromjson')(fromjson)