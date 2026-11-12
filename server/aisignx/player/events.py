"""Broadcast a "manifest" hint to every screen after any commit that touched content.

One set of SQLAlchemy session listeners covers every commit point (routes and
services alike), so no view has to remember to notify screens. Hot paths that must
not broadcast (heartbeats) use Core UPDATE statements, which never flush ORM objects.
Clients answer the hint with a conditional GET, so a no-op change costs a 304.
"""
from flask import has_app_context
from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models import Display, Media, Playlist, PlaylistItem
from .broker import get_broker

# Models whose changes can alter what a screen shows. Phase 4 adds schedules,
# groups and emergency messages here.
WATCHED = (Display, Media, Playlist, PlaylistItem)

_FLAG = "aisignx_player_changed"
_registered = False


def _after_flush(session, _flush_context):
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, WATCHED):
            session.info[_FLAG] = True
            return


def _after_commit(session):
    if not session.info.pop(_FLAG, False) or not has_app_context():
        return
    broker = get_broker()
    if broker is not None:
        broker.publish_all("manifest", {})


def _after_rollback(session):
    session.info.pop(_FLAG, None)


def register_listeners() -> None:
    """Install the listeners once per process (they apply to every Session)."""
    global _registered
    if _registered:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    _registered = True
