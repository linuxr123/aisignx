"""`flask --app wsgi https ...`: certificates and the nginx configuration from the command line (used by setup-nginx)."""
from __future__ import annotations

from pathlib import Path

import click
from flask import Flask
from flask.cli import with_appcontext

from ..audit import service as audit
from ..extensions import db
from . import certs, nginx
from .service import tls_dir


@click.group("https")
def https_cli():
    """HTTPS for the web page: certificates and the nginx configuration."""


@https_cli.command("self-signed")
@click.option("--name", "names", multiple=True, required=True, help="Host name or IP address; repeat for several.")
@click.option("--days", type=click.IntRange(30, 3650), default=certs.SELF_SIGNED_DAYS, show_default=True)
@click.option("--force", is_flag=True, help="Replace a certificate that is already there.")
@with_appcontext
def self_signed(names, days, force):
    """Make a self-signed certificate (browsers warn until it is trusted; the apps can pin it)."""
    folder = tls_dir()
    if certs.current(folder) and not force:
        raise click.ClickException(f"There is already a certificate in {folder}. Use --force to replace it.")
    try:
        cert_pem, key_pem = certs.make_self_signed(list(names), days)
        chain, key, _ = certs.check_pair(cert_pem, key_pem)
    except certs.CertError as e:
        raise click.ClickException(str(e))
    certs.store(folder, chain, key)
    audit.record("https.self_signed", detail={"names": list(names), "days": days})
    db.session.commit()
    info = certs.current(folder)
    click.echo(f"Certificate written to {folder}")
    click.echo(f"  names: {', '.join(info['names'])}   valid until {info['not_after']:%Y-%m-%d}")
    click.echo(f"  SHA-256 fingerprint: {certs.fingerprint_text(info['fingerprint'])}")


@https_cli.command("import")
@click.option("--cert", "cert_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True,
              help="PEM file with your certificate first, then the intermediate certificate(s).")
@click.option("--key", "key_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--passphrase", default=None, help="If the key file is protected (it is stored without one for nginx).")
@with_appcontext
def import_cmd(cert_file, key_file, passphrase):
    """Use a certificate you bought (or got from Let's Encrypt)."""
    try:
        chain, key, problems = certs.check_pair(cert_file.read_bytes(), key_file.read_bytes(), passphrase)
    except certs.CertError as e:
        raise click.ClickException(str(e))
    certs.store(tls_dir(), chain, key)
    audit.record("https.import", detail={"subject": certs.describe(chain[0])["subject"]})
    db.session.commit()
    info = certs.current(tls_dir())
    click.echo(f"Certificate stored for {', '.join(info['names'])}, valid until {info['not_after']:%Y-%m-%d}.")
    for p in problems:
        click.echo(f"  warning: {p}")


@https_cli.command("config")
@click.option("--out", "out_file", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Write here instead of printing.")
@click.option("--name", "names", multiple=True, help="Default: the names in the stored certificate.")
@click.option("--standalone", is_flag=True, help="A whole nginx.conf (the Windows nginx folder) instead of only the server blocks.")
@click.option("--upstream", default="127.0.0.1:5000", show_default=True)
@click.option("--cert-path", default=None, help="Where nginx finds the certificate (default: the stored file).")
@click.option("--key-path", default=None)
@click.option("--max-upload-mb", type=int, default=None, help="Default: this server's upload limit.")
@click.option("--no-redirect", is_flag=True, help="Do not redirect port 80 to HTTPS.")
@click.option("--hsts", is_flag=True, help="Tell browsers to use HTTPS only for a year. Do not use with a self-signed certificate.")
@click.option("--acme-root", default=None, help="Folder for Let's Encrypt http-01 challenges on port 80.")
@with_appcontext
def config_cmd(out_file, names, standalone, upstream, cert_path, key_path, max_upload_mb, no_redirect, hsts, acme_root):
    """Print or write the nginx configuration."""
    from flask import current_app

    folder = tls_dir()
    info = certs.current(folder)
    if info is None:
        raise click.ClickException("There is no certificate yet: run `flask https self-signed` or `flask https import` first.")
    if hsts and info["self_signed"]:
        raise click.ClickException("HSTS with a self-signed certificate would lock browsers out of the site (they could no longer be told to continue). Use a certificate from a certificate authority.")
    mb = max_upload_mb or max(1, int(current_app.config["MAX_CONTENT_LENGTH"]) // (1024 * 1024))
    try:
        text = nginx.render(names=list(names) or info["names"], cert=cert_path or str(folder / certs.CERT_FILE),
                            key=key_path or str(folder / certs.KEY_FILE), upstream=upstream, max_upload_mb=mb,
                            redirect_http=not no_redirect, hsts=hsts, standalone=standalone, acme_root=acme_root)
    except certs.CertError as e:
        raise click.ClickException(str(e))
    if out_file:
        out_file.write_text(text, encoding="utf-8", newline="\n")
        click.echo(f"Wrote {out_file}")
    else:
        click.echo(text, nl=False)


@https_cli.command("status")
@with_appcontext
def status_cmd():
    """Show the stored certificate."""
    info = certs.current(tls_dir())
    if info is None:
        click.echo(f"No certificate in {tls_dir()}.")
        return
    click.echo(f"Folder     : {tls_dir()}")
    click.echo(f"Names      : {', '.join(info['names'])}")
    click.echo(f"Issuer     : {info['issuer']}{'  (self-signed)' if info['self_signed'] else ''}")
    click.echo(f"Valid until: {info['not_after']:%Y-%m-%d}  ({info['days_left']} days left)")
    click.echo(f"SHA-256    : {certs.fingerprint_text(info['fingerprint'])}")
    click.echo(f"Private key: {'present' if info['has_key'] else 'MISSING'}")


def register(app: Flask) -> None:
    app.cli.add_command(https_cli)
