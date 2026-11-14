"""`flask --app wsgi backup ...`: create, list, verify, restore and prune archives.

FILE arguments accept a name from the backup folder or a path to any archive.
"""
from pathlib import Path

import click
from flask import Flask
from flask.cli import with_appcontext

from ..filters import filesize


def _resolve(file: str) -> Path:
    from . import service

    try:
        return service.resolve_name(file)
    except FileNotFoundError:
        path = Path(file)
        if not path.is_file():
            raise click.ClickException(f"No such backup: {file}") from None
        return path


@click.group("backup")
def backup_cli():
    """Create, list, verify and restore backups."""


@backup_cli.command("create")
@click.option("--no-uploads", is_flag=True, help="Leave uploaded media out (database only).")
@with_appcontext
def create_command(no_uploads):
    """Write a backup archive to the backup folder."""
    from ..audit import service as audit
    from . import service

    try:
        path = service.create("cli", include_uploads=False if no_uploads else None)
    except Exception as exc:
        raise click.ClickException(f"Backup failed: {exc}") from exc
    audit.record_now("backup.create", ("backup", None, path.name),
                     detail={"name": path.name, "include_uploads": not no_uploads})
    click.echo(f"Wrote {path} ({filesize(path.stat().st_size)})")


@backup_cli.command("list")
@with_appcontext
def list_command():
    """List the archives in the backup folder, newest first."""
    from . import service

    items = service.list_backups()
    if not items:
        click.echo(f"No backups in {service.backup_dir()}")
        return
    for item in items:
        when = item["created_at"] or item["mtime"]
        note = item["error"] or ("uploads" if item["include_uploads"] else "no uploads")
        click.echo(f"{when:%Y-%m-%d %H:%M}Z  {filesize(item['size']):>10}  "
                   f"{item['source']:<11}  {item['name']}  ({note})")


@backup_cli.command("verify")
@click.argument("file")
@with_appcontext
def verify_command(file):
    """Check an archive completely (manifest, checksums, version)."""
    from .restore import RestoreError, validate

    path = _resolve(file)
    try:
        manifest = validate(path)
    except RestoreError as exc:
        raise click.ClickException(f"{path.name}: {exc}") from exc
    totals = manifest.get("totals") or {}
    click.echo(f"OK  {path.name}: {totals.get('files')} files, "
               f"{filesize(totals.get('bytes') or 0)}, created {manifest.get('created_at')}, "
               f"database {'included' if manifest['db_included'] else 'not included'}")


@backup_cli.command("restore")
@click.argument("file")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@with_appcontext
def restore_command(file, yes):
    """Replace all data with an archive (a safety backup is written first)."""
    from .restore import restore

    path = _resolve(file)
    click.echo("Stop the server first: a running server keeps old sessions and its own "
               "copy of the secret key.")
    if not yes:
        click.confirm(f"Replace all current data with {path.name}?", abort=True)
    try:
        result = restore(path, actor_via="cli")
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Restored {result['name']}. Safety backup: {result['pre_restore']}.")
    for line in result["skipped"]:
        click.echo(f"  not replaced: {line}", err=True)


@backup_cli.command("prune")
@with_appcontext
def prune_command():
    """Delete daily (scheduled) backups beyond the number to keep."""
    from ..models import Setting
    from . import service

    keep = Setting.get_int("backup.keep", lo=1, hi=100)
    deleted = service.prune(keep)
    click.echo(f"Kept the newest {keep} daily backups; deleted {len(deleted)}.")
    for name in deleted:
        click.echo(f"  {name}")


def register(app: Flask) -> None:
    app.cli.add_command(backup_cli)
