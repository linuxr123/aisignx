from flask import Blueprint, render_template
from flask_login import login_required
from models import Display, Media, Playlist, DisplayGroup, Schedule
from utils import is_online

main_bp = Blueprint('main', __name__)

@main_bp.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard page"""
    # Get counts
    displays = Display.query.all()
    display_count = len(displays)
    media_count = Media.query.count()
    playlist_count = Playlist.query.count()
    schedule_count = Schedule.query.count()
    
    # Calculate how many displays are online. 
    online_count = sum(1 for d in displays if is_online(d.last_ping))
    
    # Recent displays
    recent_displays = Display.query.order_by(Display.last_ping.desc()).limit(10).all()
    for display in recent_displays:
        display.is_online = is_online(display.last_ping)

    # Proof of Play summary (last 24h). Best-effort: hidden from the
    # template if PoP is disabled or the table is missing/empty.
    pop_enabled = False
    pop_24h = 0
    try:
        import proof_of_play as pop
        from datetime import datetime, timedelta
        pop_enabled = pop.is_enabled()
        if pop_enabled:
            since = datetime.utcnow() - timedelta(hours=24)
            pop_24h = len(pop.query_for_current_domain(since=since, limit=10000))
    except Exception:
        pass

    return render_template('dashboard.html',
                           display_count=display_count,
                           online_count=online_count,
                           media_count=media_count,
                           playlist_count=playlist_count,
                           schedule_count=schedule_count,
                           recent_displays=recent_displays,
                           pop_enabled=pop_enabled,
                           pop_24h=pop_24h)
