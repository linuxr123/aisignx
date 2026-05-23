"""HTTP vs HTTPS deployment presets for AISignX server config."""

from __future__ import annotations

MODES: dict[str, dict] = {
    'http': {
        'label': 'Direct HTTP (LAN / development)',
        'description': (
            'Run AISignX on plain HTTP (e.g. port 5000). '
            'No reverse proxy required.'
        ),
        'trust_proxy': False,
        'proxy_hops': 1,
        'preferred_url_scheme': 'http',
        'session_cookie_secure': False,
        'remember_cookie_secure': False,
        'client_url_template': 'http://{host}:5000',
    },
    'https': {
        'label': 'HTTPS via reverse proxy (nginx / Caddy / IIS)',
        'description': (
            'Terminate TLS at nginx, Caddy, or IIS. AISignX stays on '
            'http://127.0.0.1:5000 behind the proxy.'
        ),
        'trust_proxy': True,
        'proxy_hops': 1,
        'preferred_url_scheme': 'https',
        'session_cookie_secure': True,
        'remember_cookie_secure': True,
        'client_url_template': 'https://{host}',
    },
}


def normalize_mode(mode: str | None) -> str:
    value = (mode or 'http').strip().lower()
    if value not in MODES:
        allowed = ', '.join(sorted(MODES))
        raise ValueError(f"Unknown deploy mode {value!r}. Use one of: {allowed}")
    return value


def resolve_deploy_settings(
    mode: str | None,
    *,
    proxy_hops: int | None = None,
    server_name: str | None = None,
) -> dict:
    """Return config.py-ready settings for the chosen deployment mode."""
    key = normalize_mode(mode)
    preset = MODES[key]
    hops = int(proxy_hops if proxy_hops is not None else preset['proxy_hops'])
    hostname = (server_name or '').strip() or None
    return {
        'AISIGNX_DEPLOY_MODE': key,
        'TRUST_PROXY': preset['trust_proxy'],
        'TRUST_PROXY_HOPS': hops,
        'PREFERRED_URL_SCHEME': preset['preferred_url_scheme'],
        'SESSION_COOKIE_SECURE': preset['session_cookie_secure'],
        'REMEMBER_COOKIE_SECURE': preset['remember_cookie_secure'],
        'SERVER_NAME': hostname,
    }


def client_url_hint(mode: str | None, server_name: str | None = None) -> str:
    key = normalize_mode(mode)
    host = (server_name or '').strip() or 'YOUR_HOST'
    return MODES[key]['client_url_template'].format(host=host)


def describe_mode(mode: str | None) -> str:
    key = normalize_mode(mode)
    preset = MODES[key]
    return f"{key}: {preset['label']} - {preset['description']}"
