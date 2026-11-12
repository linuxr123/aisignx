"""`flask --app wsgi serve`: run AiSignX under waitress with enough threads for screens.

Each screen keeps one live-update stream open, and each open stream holds one server
thread, so the pool is sized from PLAYER_MAX_STREAMS plus headroom for the admin and
API requests. Run one process: live push only reaches streams in the same process.
"""
import click
from flask import Flask, current_app
from flask.cli import with_appcontext

THREAD_HEADROOM = 16


@click.command("serve")
@click.option("--host", default="0.0.0.0", show_default=True,
              help="Interface to listen on (0.0.0.0 = all, so screens on the LAN can connect).")
@click.option("--port", default=5000, show_default=True, type=int, help="Port to listen on.")
@click.option("--threads", default=None, type=click.IntRange(min=4),
              help="Worker threads (default: PLAYER_MAX_STREAMS + 16).")
@with_appcontext
def serve_command(host: str, port: int, threads: int | None):
    """Serve AiSignX with waitress (production server)."""
    try:
        from waitress import serve
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise click.ClickException(
            "waitress is not installed. Run: pip install -r requirements.txt") from exc

    app = current_app._get_current_object()
    if threads is None:
        threads = int(app.config["PLAYER_MAX_STREAMS"]) + THREAD_HEADROOM
    click.echo(f"AiSignX listening on http://{host}:{port} with {threads} threads")
    serve(app, host=host, port=port, threads=threads, channel_timeout=120,
          connection_limit=max(100, threads + 50))


def register(app: Flask) -> None:
    app.cli.add_command(serve_command)
