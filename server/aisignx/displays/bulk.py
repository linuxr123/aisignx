"""Change several screens at once. Every field starts at "No change": only what you fill in is applied, to every ticked screen.
The kiosk lock is left out on purpose (it needs an unlock PIN on each screen), and so is everything that is per screen (name, notes,
the PIN)."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import NumberRange, Optional

from ..models import Display
from .forms import FIT_CHOICES, ORIENTATION_CHOICES, group_choices, playlist_choices

KEEP = ""
YES_NO = [SelectChoice(KEEP, "No change"), SelectChoice("1", "On"), SelectChoice("0", "Off")]
ADMIN_SWITCHES = (("allow_input", "Player controls (touch, mouse, keyboard, remote)"),
                  ("agent_watchdog", "Watchdog: restart the player when it stops responding"),
                  ("agent_allow_restart", 'Allow "Restart the player" from the display page'),
                  ("agent_allow_logs", 'Allow "Collect logs" from the display page'),
                  ("agent_allow_reboot", 'Allow "Reboot the device" from the display page (signed command)'),
                  ("agent_allow_startup", "Allow changing the startup settings (signed command)"),
                  ("agent_allow_update", "Allow pushing app updates (signed command)"))


def _keep_first(choices, as_text=True):
    return [SelectChoice(KEEP, "No change")] + [SelectChoice(str(c.value), c.label) for c in choices]


class BulkForm(FlaskForm):
    playlist = SelectField("Default playlist", default=KEEP)
    group = SelectField("Group", default=KEEP)
    orientation = SelectField("Orientation", default=KEEP)
    fit_override = SelectField("Fit", default=KEEP)
    volume = IntegerField("Volume (0-100)", validators=[Optional(), NumberRange(0, 100)], render_kw={"min": 0, "max": 100, "placeholder": "No change"})
    show_offline_banner = SelectField("Small banner when the screen is offline", choices=YES_NO, default=KEEP)
    resume_after_min = IntegerField("Resume after a pause (minutes)", validators=[Optional(), NumberRange(1, 60)], render_kw={"min": 1, "max": 60, "placeholder": "No change"})

    def __init__(self, *args, admin: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_admin_form = admin
        self.playlist.choices = _keep_first(playlist_choices())
        self.group.choices = _keep_first(group_choices())
        self.orientation.choices = _keep_first(ORIENTATION_CHOICES)
        fit = [SelectChoice(str(c.value) if c.value else "default", c.label) for c in FIT_CHOICES]
        self.fit_override.choices = [SelectChoice(KEEP, "No change")] + fit
        self.switch_names = [n for n, _l in ADMIN_SWITCHES] if admin else []

    def switch_choices(self):
        return YES_NO


def apply(displays: list[Display], form: BulkForm, post) -> dict[int, list[str]]:
    """Writes what was filled in. -> {display id: [changed field names]} (only screens where something changed)."""
    plan: dict[str, object] = {}
    if form.playlist.data != KEEP:
        plan["playlist_id"] = int(form.playlist.data) or None
    if form.group.data != KEEP:
        plan["group_id"] = int(form.group.data) or None
    if form.orientation.data != KEEP:
        plan["orientation"] = int(form.orientation.data)
    if form.fit_override.data != KEEP:
        plan["fit_override"] = None if form.fit_override.data == "default" else form.fit_override.data
    if form.volume.data is not None:
        plan["volume"] = form.volume.data
    if form.show_offline_banner.data != KEEP:
        plan["show_offline_banner"] = form.show_offline_banner.data == "1"
    if form.is_admin_form:
        if form.resume_after_min.data is not None:
            plan["resume_after_s"] = form.resume_after_min.data * 60
        for name in form.switch_names:
            value = post.get(name, KEEP)
            if value in ("0", "1"):
                plan[name] = value == "1"
    changed: dict[int, list[str]] = {}
    for d in displays:
        names = []
        for field, value in plan.items():
            if getattr(d, field) != value:
                setattr(d, field, value)
                names.append(field)
        if names:
            changed[d.id] = names
    return changed
