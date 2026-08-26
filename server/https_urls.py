"""HTTPS-only URL helpers for client setup and public URLs."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def _host_netloc(parsed, default_port: int | None) -> str:
    host = parsed.hostname or parsed.netloc or ''
    if not host:
        return ''
    port = parsed.port
    if port is None or port == default_port:
        return host
    if default_port == 443 and port == 80:
        return host
    if default_port == 80 and port == 443:
        return host
    return f'{host}:{port}'


def normalize_https_base_url(url: str) -> str:
    """Return a base URL forced to https with no trailing slash."""
    raw = (url or '').strip()
    if not raw:
        return ''
    if not raw.startswith(('http://', 'https://')):
        raw = 'https://' + raw.lstrip('/')
    parsed = urlparse(raw)
    host = parsed.hostname or parsed.netloc or ''
    if not host:
        return ''
    # Admin UI on Waitress :5000 — public HTTPS URL is :443 (no port suffix).
    port = parsed.port
    if port == 5000:
        port = None
    if port in (None, 443, 80):
        netloc = host
    else:
        netloc = f'{host}:{port}'
    return urlunparse(('https', netloc, '', '', '', '')).rstrip('/')


def normalize_http_base_url(url: str) -> str:
    """Return a base URL forced to http with no trailing slash."""
    raw = (url or '').strip()
    if not raw:
        return ''
    if not raw.startswith(('http://', 'https://')):
        raw = 'http://' + raw.lstrip('/')
    parsed = urlparse(raw)
    netloc = _host_netloc(parsed, 80)
    if not netloc:
        return ''
    return urlunparse(('http', netloc, '', '', '', '')).rstrip('/')


def normalize_public_base_url(url: str, scheme: str = 'https') -> str:
    """Normalize a base URL to the given scheme (https or http)."""
    if (scheme or 'https').lower() == 'http':
        return normalize_http_base_url(url)
    return normalize_https_base_url(url)


def validate_https_client_url(url: str) -> tuple[bool, str]:
    """Validate a server URL for native/browser clients (HTTPS only)."""
    raw = (url or '').strip()
    if not raw:
        return False, 'Server URL is required.'
    if raw.lower().startswith('http://'):
        return False, 'HTTP is not supported. Use https:// (port 443, or https://host:8443).'
    normalized = normalize_https_base_url(raw)
    if not normalized.startswith('https://'):
        return False, 'URL must use https://'
    parsed = urlparse(normalized)
    if not parsed.hostname:
        return False, 'Invalid server hostname.'
    return True, normalized
