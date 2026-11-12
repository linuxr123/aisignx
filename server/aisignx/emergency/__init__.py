"""Emergency override: send a message (and optionally a playlist) to screens at once."""
from flask import Blueprint
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

bp = Blueprint("emergency", __name__, url_prefix="/emergency")


@bp.app_context_processor
def inject_live_emergencies():
    """live_emergency_count for the red banner in base.html (signed-in users only)."""
    if not current_user or not current_user.is_authenticated:
        return {"live_emergency_count": 0}
    from ..extensions import db
    from ..models import EmergencyMessage

    try:
        count = EmergencyMessage.query.filter(EmergencyMessage.cleared_at.is_(None)).count()
    except SQLAlchemyError:  # DB not migrated yet
        db.session.rollback()
        count = 0
    return {"live_emergency_count": count}


from . import routes  # noqa: E402,F401
