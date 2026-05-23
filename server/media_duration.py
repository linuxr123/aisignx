import math

from logging_config import logger


def probe_video_metadata(path):
    """Best-effort ffprobe metadata for uploaded video files."""
    try:
        import ffmpeg
        data = ffmpeg.probe(path)
    except Exception as exc:
        logger.warning(f'video metadata probe failed for {path}: {exc}')
        return {}

    streams = data.get('streams') or []
    fmt = data.get('format') or {}
    video = next((s for s in streams if s.get('codec_type') == 'video'), None) or {}

    def _float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    duration = _float(video.get('duration')) or _float(fmt.get('duration'))
    return {
        'duration_seconds': duration if duration and duration > 0 else None,
        'width': _int(video.get('width')),
        'height': _int(video.get('height')),
        'codec': (video.get('codec_name') or '').lower() or None,
        'bitrate_bps': _int(video.get('bit_rate')) or _int(fmt.get('bit_rate')),
    }


def whole_seconds(seconds, default=10):
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return int(default)
    return max(1, int(math.ceil(value)))


def detected_media_duration(media, default=10):
    """Integer seconds for a media row's detected/default duration."""
    if not media:
        return int(default)
    detected = getattr(media, 'duration_seconds', None)
    if detected and detected > 0:
        return whole_seconds(detected, default=default)
    duration = getattr(media, 'duration', None)
    return whole_seconds(duration, default=default)


def playlist_item_duration_seconds(item, default=10):
    """Authoritative playlist slot length in seconds.

    For videos, item.duration == 0 means "play full detected video length" or
    "play clip_start -> clip_end" when a clip end is provided.
    """
    media = getattr(item, 'media', None)
    media_type = getattr(media, 'media_type', None)
    duration = getattr(item, 'duration', None)
    clip_start = getattr(item, 'clip_start', None) or 0
    clip_end = getattr(item, 'clip_end', None)

    if media_type == 'video':
        if clip_end not in (None, ''):
            try:
                clip_len = float(clip_end) - float(clip_start or 0)
                if clip_len > 0:
                    return whole_seconds(clip_len, default=default)
            except (TypeError, ValueError):
                pass
        try:
            duration_value = int(duration or 0)
        except (TypeError, ValueError):
            duration_value = 0
        if duration_value > 0:
            return duration_value
        return detected_media_duration(media, default=default)

    return whole_seconds(duration, default=default)


def item_dict_duration_ms(item, default=10):
    """Duration table helper for display-player payload dictionaries."""
    media = item.get('media') if isinstance(item, dict) else None
    item_type = item.get('type') if isinstance(item, dict) else None
    duration = item.get('duration') if isinstance(item, dict) else None
    clip_start = (item.get('clip_start') or 0) if isinstance(item, dict) else 0
    clip_end = item.get('clip_end') if isinstance(item, dict) else None

    if item_type == 'video':
        if clip_end not in (None, ''):
            try:
                clip_len = float(clip_end) - float(clip_start or 0)
                if clip_len > 0:
                    return whole_seconds(clip_len, default=default) * 1000
            except (TypeError, ValueError):
                pass
        try:
            duration_value = int(duration or 0)
        except (TypeError, ValueError):
            duration_value = 0
        if duration_value > 0:
            return duration_value * 1000
        if isinstance(media, dict):
            detected = media.get('duration_seconds') or media.get('duration')
            return whole_seconds(detected, default=default) * 1000
        return int(default) * 1000

    # Plugin items: prefer the larger of plugin video_duration and duration.
    if isinstance(item, dict):
        plugin = item.get('plugin') or {}
        cfg = (plugin.get('config') or {}) if isinstance(plugin, dict) else {}
        try:
            plugin_duration = int(cfg.get('video_duration') or 0)
        except (TypeError, ValueError):
            plugin_duration = 0
        try:
            item_duration = int(duration or 0)
        except (TypeError, ValueError):
            item_duration = 0
        if plugin_duration > item_duration:
            return whole_seconds(plugin_duration, default=default) * 1000

    return whole_seconds(duration, default=default) * 1000
