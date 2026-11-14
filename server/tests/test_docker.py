"""Docker packaging and HTTPS docs (Phase 6, WP8): static checks plus the browser sandbox flag."""
import re
import subprocess
from pathlib import Path

import pytest

from aisignx import config as config_module
from aisignx.media import webshot

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# --- media/webshot.py honours BROWSER_NO_SANDBOX --------------------------------------------

def _captured_cmd(app, monkeypatch, tmp_path, *, no_sandbox: bool, root: bool) -> list[str]:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(webshot, "browser_path", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(webshot, "_running_as_root", lambda: root)
    monkeypatch.setattr(webshot.subprocess, "run", fake_run)
    app.config["BROWSER_NO_SANDBOX"] = no_sandbox
    with app.app_context():
        assert webshot.capture("https://example.com", tmp_path / "shot.png") is False
    assert len(calls) == 1
    return calls[0]


@pytest.mark.parametrize("no_sandbox,root,expected", [
    (False, False, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
])
def test_webshot_no_sandbox_flag(app, monkeypatch, tmp_path, no_sandbox, root, expected):
    cmd = _captured_cmd(app, monkeypatch, tmp_path, no_sandbox=no_sandbox, root=root)
    assert ("--no-sandbox" in cmd) is expected
    assert cmd.count("--no-sandbox") <= 1
    assert cmd[-1] == "https://example.com"  # the URL stays the last argument


def test_browser_no_sandbox_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AISIGNX_SECRET_KEY", "test-only")
    monkeypatch.setenv("AISIGNX_BROWSER_NO_SANDBOX", "1")
    assert config_module.build_config(str(tmp_path))["BROWSER_NO_SANDBOX"] is True
    monkeypatch.delenv("AISIGNX_BROWSER_NO_SANDBOX")
    assert config_module.build_config(str(tmp_path))["BROWSER_NO_SANDBOX"] is False


# --- Dockerfile, entrypoint, compose, .dockerignore ------------------------------------------

def test_dockerfile_contract():
    text = _read("Dockerfile")
    assert re.search(r"^FROM python:3\.12-slim\b", text, re.M)
    for pkg in ("ffmpeg", "chromium", "fonts-dejavu-core", "tini", "--no-install-recommends"):
        assert pkg in text
    assert "rm -rf /var/lib/apt/lists/*" in text
    assert "pip install --no-cache-dir -r /app/requirements.txt" in text
    assert "COPY server/ /app/" in text
    assert re.search(r"useradd --uid 1000\b.* aisignx", text)
    assert re.search(r"^USER aisignx$", text, re.M)
    assert "VOLUME /data" in text and "EXPOSE 5000" in text
    assert "http://127.0.0.1:5000/healthz" in text
    assert 'ENTRYPOINT ["tini", "--", "/app/docker/entrypoint.sh"]' in text
    expected_env = {
        "AISIGNX_INSTANCE_DIR": "/data/instance", "AISIGNX_UPLOAD_DIR": "/data/uploads",
        "AISIGNX_BACKUP_DIR": "/data/backups", "AISIGNX_LOG_DIR": "/data/logs",
        "AISIGNX_BROWSER": "/usr/bin/chromium", "AISIGNX_BROWSER_NO_SANDBOX": "1",
        "PYTHONUNBUFFERED": "1",
    }
    for key, value in expected_env.items():
        assert re.search(rf"\b{key}={re.escape(value)}\b", text), key


def test_dockerfile_env_vars_are_real_settings():
    """Every AISIGNX_* variable the image or compose file sets is one the app reads."""
    documented = set(re.findall(r"AISIGNX_[A-Z_]+", config_module.__doc__))
    used = set(re.findall(r"\b(AISIGNX_[A-Z_]+)[=:]", _read("Dockerfile")))
    used |= set(re.findall(r"^\s+(AISIGNX_[A-Z_]+):", _read("docker-compose.yml"), re.M))
    used.discard("AISIGNX_DOMAIN")  # read by Caddy, not by AiSignX
    assert used and used <= documented, used - documented


def test_entrypoint_script():
    raw = (ROOT / "docker" / "entrypoint.sh").read_bytes()
    assert b"\r" not in raw  # CRLF would break the shebang inside the Linux image
    text = raw.decode("utf-8")
    assert text.startswith("#!/bin/sh\n")
    for var in ("AISIGNX_INSTANCE_DIR", "AISIGNX_UPLOAD_DIR", "AISIGNX_BACKUP_DIR",
                "AISIGNX_LOG_DIR"):
        assert f'"${var}"' in text
    upgrade = text.index("flask --app wsgi db upgrade")
    serve = text.index("exec flask --app wsgi serve --host 0.0.0.0 --port 5000")
    assert upgrade < serve


def test_compose_file():
    text = _read("docker-compose.yml")
    assert "aisignx-data:/data" in text
    assert '"5000:5000"' in text
    assert "restart: unless-stopped" in text
    assert "AISIGNX_SECRET_KEY: ${AISIGNX_SECRET_KEY:-}" in text
    assert "one replica only" in text.lower()
    assert "no default credentials" in text.lower()
    assert "profiles: [https]" in text
    assert "./docker/Caddyfile.example:/etc/caddy/Caddyfile:ro" in text
    assert "replicas" not in text  # never scaled


def test_caddyfile_points_at_service():
    text = _read("docker/Caddyfile.example")
    assert "reverse_proxy aisignx:5000" in text
    assert "tls internal" in text
    assert "AISIGNX_PROXY_HOPS=1" in text


def test_dockerignore_keeps_secrets_and_data_out():
    lines = {line.strip() for line in _read(".dockerignore").splitlines()}
    for pattern in (".git", "**/.venv", "**/__pycache__", "**/.env", "**/instance",
                    "**/uploads", "**/backups", "**/logs", "**/*.db", "clients/",
                    "server/tests"):
        assert pattern in lines, pattern


_SECRET_RE = re.compile(
    r"(PASSWORD|SECRET_KEY|TOKEN)\s*[:=]\s*(?!\$\{)[\"']?[A-Za-z0-9+/_-]{4,}", re.I)


@pytest.mark.parametrize("rel", [
    "Dockerfile", "docker-compose.yml", "docker/entrypoint.sh", "docker/Caddyfile.example",
])
def test_no_credentials_in_docker_files(rel):
    assert not _SECRET_RE.search(_read(rel)), rel


# --- docs ------------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["docs/https.md", "docs/docker.md", "docs/deployment.md"])
def test_doc_links_resolve(rel):
    text = _read(rel)
    for target in re.findall(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)", text):
        assert (ROOT / "docs" / target).is_file(), f"{rel} links to missing {target}"


def test_https_doc_covers_the_essentials():
    text = _read("docs/https.md")
    for needle in ("tls internal", "proxy_buffering off", "proxy_read_timeout 3600s",
                   "X-Accel-Buffering", "client_max_body_size", "AISIGNX_MAX_UPLOAD_MB",
                   "AISIGNX_PROXY_HOPS", "AISIGNX_SECURE_COOKIES", "100 MB", "Cloudflare",
                   "Application Request Routing", "One process only", "PowerPoint add-in"):
        assert needle in text, needle
    deployment = _read("docs/deployment.md")
    assert "(https.md)" in deployment and "proxy_pass" not in deployment
