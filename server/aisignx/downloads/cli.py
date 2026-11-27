"""`flask --app wsgi downloads add FILE`: publish an installer to the Downloads page from the command line."""
import hashlib
import secrets
import shutil
from pathlib import Path

import click
from flask import Flask
from flask.cli import with_appcontext

from ..audit import service as audit
from ..extensions import db
from ..models import DOWNLOAD_KINDS, DeviceCommand, Download
from ..models.device_command import ACTION_UPDATE_APP, OPEN_STATES
from . import apksign, meta
from .routes import ALLOWED, _clean_name, downloads_dir


@click.group("downloads")
def downloads_cli():
    """Manage the files on the Downloads page."""


@downloads_cli.command("add")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--kind", type=click.Choice(["auto", *DOWNLOAD_KINDS]), default="auto", show_default=True,
              help="auto reads it from the file.")
@click.option("--version", default="", help="Shown next to the file (default: read from the file).")
@click.option("--notes", default="")
@click.option("--replace/--keep", default=True,
              help="Remove older files of the same kind and name, and for an APK those signed with the same key (default).")
@with_appcontext
def add(file: Path, kind: str, version: str, notes: str, replace: bool):
    """Copy FILE into the downloads folder and list it."""
    name = _clean_name(file.name)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED:
        raise click.ClickException(f"File type .{ext} is not allowed here.")
    digest = hashlib.sha256()
    with open(file, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    size = file.stat().st_size
    if size == 0:
        raise click.ClickException("The file is empty.")
    stored = secrets.token_hex(16)
    shutil.copyfile(file, downloads_dir() / stored)
    guessed_kind, guessed_version = meta.guess(name, file)
    if kind == "auto":
        kind = guessed_kind
    version = version.strip() or guessed_version
    if replace:
        # An APK also replaces the older APKs signed with the same key, so the page keeps one Android installer per key
        # (a debug and a release build can be listed side by side; each screen gets the one with its own key).
        # A file that an open update_app command still names (by sha256) stays until that update is over.
        signer = apksign.signer_sha256(downloads_dir() / stored) if ext == "apk" else None
        in_use = {str((c.params or {}).get("sha256") or "") for c in
                  DeviceCommand.query.filter(DeviceCommand.action == ACTION_UPDATE_APP, DeviceCommand.state.in_(OPEN_STATES)).all()}
        for old in Download.query.filter_by(kind=kind).all():
            same_key = signer is not None and old.filename.lower().endswith(".apk") and \
                apksign.signer_sha256(downloads_dir() / old.stored_name) == signer
            if (old.filename == name or same_key) and old.sha256 in in_use:
                click.echo(f"Kept {old.filename}: an update that is still open uses it. Delete it on the Downloads page later.")
            elif old.filename == name or same_key:
                (downloads_dir() / old.stored_name).unlink(missing_ok=True)
                db.session.delete(old)
    row = Download(kind=kind, version=version.strip()[:32], filename=name, stored_name=stored, size=size,
                   sha256=digest.hexdigest(), notes=notes.strip()[:400], uploaded_by="cli")
    db.session.add(row)
    db.session.flush()
    audit.record("download.upload", row, detail={"file": name, "size": size, "kind": kind})
    db.session.commit()
    click.echo(f"Added {name} ({size} bytes, sha256 {row.sha256[:16]}...) to Downloads.")


def register(app: Flask) -> None:
    app.cli.add_command(downloads_cli)
