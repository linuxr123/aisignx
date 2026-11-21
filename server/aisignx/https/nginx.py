"""The nginx configuration for AiSignX: HTTPS in front, live updates and big uploads passed through.

`render()` is pure: it only builds text from checked values, so what ends up in a file that nginx runs as a privileged
process is never taken from a request as it is.
"""
from __future__ import annotations

import re

from .certs import CertError, clean_names

# Paths and addresses are put inside nginx directives: only plain characters get in. No quotes, braces, semicolons,
# dollar signs or line breaks, so a value can never end its directive and start another.
_PATH_RE = re.compile(r"^[A-Za-z0-9_./:\\ -]{1,300}$")
_UPSTREAM_RE = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\]|[A-Za-z0-9.-]{1,100}):(\d{1,5})$")


def _path(value: str, what: str) -> str:
    if not _PATH_RE.match(value or "") or ".." in value.replace("\\", "/").split("/"):
        raise CertError(f"The {what} path has characters that are not allowed.")
    return value.replace("\\", "/")


def _proxy_block(upstream: str) -> str:
    return f"""        proxy_pass http://{upstream};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
"""


def render(*, names, cert: str, key: str, upstream: str = "127.0.0.1:5000", max_upload_mb: int = 2048,
           redirect_http: bool = True, hsts: bool = False, standalone: bool = False,
           http_port: int = 80, https_port: int = 443, acme_root: str | None = None) -> str:
    """The nginx text. `standalone` gives a whole nginx.conf (the Windows nginx folder); otherwise only the `server`
    blocks, for a file in conf.d/ or sites-enabled/ of a system nginx."""
    names = clean_names(names)
    cert, key = _path(cert, "certificate"), _path(key, "key")
    m = _UPSTREAM_RE.match(upstream or "")
    if not m or not 1 <= int(m.group(2)) <= 65535:
        raise CertError("The server address must look like 127.0.0.1:5000.")
    for p in (http_port, https_port, max_upload_mb):
        if not isinstance(p, int) or not 1 <= p <= 65535 * 1024:
            raise CertError("A port or size is out of range.")
    acme = _path(acme_root, "ACME folder") if acme_root else None
    server_name = " ".join(names)
    hsts_line = '        add_header Strict-Transport-Security "max-age=31536000" always;\n' if hsts else ""
    out = []
    if standalone:
        out.append("""# AiSignX nginx.conf, made by `flask https config`. Do not edit by hand: it is rewritten (and checked with
# `nginx -t` first) when the settings change.
worker_processes 1;
error_log logs/error.log warn;
pid logs/nginx.pid;
events { worker_connections 1024; }
http {
    include mime.types;
    default_type application/octet-stream;
    access_log logs/access.log;
    sendfile on;
""")
    else:
        out.append("# AiSignX, made by `flask https config`. Rewritten when the settings change.\n")
    body = []
    if redirect_http:
        acme_loc = ("    location /.well-known/acme-challenge/ { root " + chr(34) + acme + chr(34) + "; }" + chr(10)) if acme else ""
        body.append(f"""server {{
    listen {http_port};
    listen [::]:{http_port};
    server_name {server_name};
{acme_loc}    location / {{ return 301 https://$host$request_uri; }}
}}
""")
    body.append(f"""server {{
    listen {https_port} ssl;
    listen [::]:{https_port} ssl;
    server_name {server_name};

    ssl_certificate "{cert}";
    ssl_certificate_key "{key}";
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    server_tokens off;
{hsts_line}
    # Videos can be big: match AISIGNX_MAX_UPLOAD_MB, and pass uploads through instead of spooling them to disk.
    client_max_body_size {max_upload_mb}m;
    client_body_timeout 300s;
    proxy_request_buffering off;

    location / {{
{_proxy_block(upstream)}        proxy_read_timeout 300s;
    }}

    # Live updates to the screens (Server-Sent Events): never buffered, and open for a long time.
    location /api/player/v1/events {{
{_proxy_block(upstream)}        proxy_buffering off;
        proxy_cache off;
        gzip off;
        proxy_read_timeout 3600s;
    }}
}}
""")
    if standalone:
        out.append("".join("    " + line if line.strip() else line for line in "".join(body).splitlines(True)))
        out.append("}\n")
    else:
        out.append("".join(body))
    return "".join(out)
