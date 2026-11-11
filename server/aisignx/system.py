"""Checks for the external programs AiSignX relies on (browser, ffmpeg).

Used by `flask --app wsgi check`, the install scripts and the admin dashboard.
"""
from dataclasses import dataclass

import click
from flask import Flask
from flask.cli import with_appcontext

from .media import probe, webshot


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    purpose: str
    fix: str


def run_checks() -> list[Check]:
    browser = webshot.browser_path()
    return [
        Check(
            name="Web browser (Edge, Chrome or Chromium)",
            ok=browser is not None,
            detail=browser or "not found",
            purpose="Screenshots of web pages for previews.",
            fix="Run the installer, install Microsoft Edge or Google Chrome, "
                "or set AISIGNX_BROWSER to the browser's path.",
        ),
        Check(
            name="ffmpeg and ffprobe",
            ok=probe.ffmpeg_available(),
            detail="found" if probe.ffmpeg_available() else "not found",
            purpose="Video length, resolution and thumbnails.",
            fix="Run the installer, or install ffmpeg and make sure it is on the PATH "
                "(or set AISIGNX_FFMPEG / AISIGNX_FFPROBE).",
        ),
    ]


@click.command("check")
@with_appcontext
def check_command():
    """Check that required programs are installed. Exits with status 1 if any is missing."""
    checks = run_checks()
    for c in checks:
        mark = click.style("OK     ", fg="green") if c.ok else click.style("MISSING", fg="red")
        click.echo(f"[{mark}] {c.name}: {c.detail}")
        if not c.ok:
            click.echo(f"          Needed for: {c.purpose}")
            click.echo(f"          Fix: {c.fix}")
    if not all(c.ok for c in checks):
        raise SystemExit(1)


def register(app: Flask) -> None:
    app.cli.add_command(check_command)
