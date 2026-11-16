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
from ..models import DOWNLOAD_KINDS, Download
from .routes import ALLOWED, _clean_name, downloads_dir


@click.group("downloads")
def downloads_cli():
    """Manage the files on the Downloads page."""


@downloads_cli.command("add")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--kind", type=click.Choice(list(DOWNLOAD_KINDS)), required=True)
@click.option("--version", default="", help="Shown next to the file.")
@click.option("--notes", default="")
@click.option("--replace/--keep", default=True, help="Remove older files of the same kind and name (default).")
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
    if replace:
        for old in Download.query.filter_by(kind=kind, filename=name).all():
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
