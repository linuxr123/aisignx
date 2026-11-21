"""Where the TLS files live and what the web page needs to say about them."""
from __future__ import annotations

import ipaddress
from pathlib import Path

from flask import current_app, request

from . import certs


def tls_dir() -> Path:
    return Path(current_app.config["TLS_DIR"])


def suggested_names(info: dict | None) -> str:
    """Names for the form: the ones in the stored certificate, else the address the admin is using now."""
    if info and info["names"]:
        return ", ".join(info["names"])
    host = request.host.rsplit(":", 1)[0].strip("[]") if request else ""
    return "" if host in ("localhost", "127.0.0.1", "::1") else host


def connection_state() -> dict:
    """How this very request reached the server: the three settings that must agree for HTTPS to work."""
    hops = int(current_app.config.get("PROXY_HOPS") or 0)
    return {"https": request.is_secure, "proxy_hops": hops,
            "secure_cookies": bool(current_app.config.get("SESSION_COOKIE_SECURE"))}


def notices(info: dict | None, state: dict) -> list[tuple[str, str]]:
    """(level, text) lines for the page."""
    out: list[tuple[str, str]] = []
    if info is None:
        out.append(("secondary", "No certificate yet. Make a self-signed one below, or add one you bought."))
    else:
        if info["days_left"] < 0:
            out.append(("danger", f"The certificate expired on {info['not_after']:%Y-%m-%d}. Screens and browsers will refuse it."))
        elif info["days_left"] <= certs.WARN_DAYS:
            out.append(("warning", f"The certificate expires in {info['days_left']} days ({info['not_after']:%Y-%m-%d}). Replace it before then."))
        if not info["has_key"]:
            out.append(("danger", "The private key file is missing next to the certificate."))
    if state["https"] and state["proxy_hops"] < 1:
        out.append(("warning", "You are connected over HTTPS but AISIGNX_PROXY_HOPS is 0: the server sees the proxy's address instead of the visitor's, and sign-in limits and the audit log record the wrong address."))
    if state["https"] and not state["secure_cookies"]:
        out.append(("warning", "Set AISIGNX_SECURE_COOKIES=1 so sign-in and screen cookies are only sent over HTTPS."))
    return out


def is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False
