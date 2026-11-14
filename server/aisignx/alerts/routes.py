"""Alerts page (everyone signed in), acknowledge and mute (editors), settings (admins)."""
import sqlalchemy as sa
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..audit import service as audit
from ..auth.decorators import admin_required, editor_required
from ..extensions import db
from ..models import Alert, Display, Setting
from . import bp, mailer, service
from .forms import ActionForm, AlertSettingsForm

PER_PAGE = 50

# Form field -> Setting key.
_FIELDS = {
    "email_enabled": "alerts.email_enabled", "email_to": "alerts.email_to",
    "smtp_host": "smtp.host", "smtp_port": "smtp.port", "smtp_security": "smtp.security",
    "smtp_username": "smtp.username", "smtp_from": "smtp.from",
    "offline_minutes": "alerts.offline_minutes", "disk_min_free_gb": "alerts.disk_min_free_gb",
    "disk_min_free_pct": "alerts.disk_min_free_pct", "login_burst": "alerts.login_burst",
    "retention_days": "alerts.retention_days",
}


def _require_action_form() -> None:
    if not ActionForm().validate_on_submit():
        abort(400)


def _display_id(alert: Alert) -> int | None:
    if alert.kind != "display_offline" or not alert.key.startswith("display:"):
        return None
    try:
        return int(alert.key.split(":", 1)[1])
    except ValueError:
        return None


@bp.route("/")
@login_required
def index():
    tab = "history" if request.args.get("tab") == "history" else "open"
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    open_alerts = (Alert.query.filter(Alert.resolved_at.is_(None))
                   .order_by(Alert.acknowledged_at.is_not(None),
                             (Alert.severity == "critical").desc(),
                             Alert.opened_at.desc()).all())
    history = None
    if tab == "history":
        history = (Alert.query.filter(Alert.resolved_at.is_not(None))
                   .order_by(Alert.resolved_at.desc(), Alert.id.desc())
                   .paginate(page=page, per_page=PER_PAGE, error_out=False))
    muted = Display.query.filter(Display.alerts_muted.is_(True)).order_by(Display.name).all()
    return render_template("alerts/index.html", tab=tab, open_alerts=open_alerts,
                           history=history, muted=muted, display_id=_display_id,
                           action_form=ActionForm(),
                           email_on=Setting.get_bool("alerts.email_enabled"))


@bp.route("/<int:alert_id>/ack", methods=["POST"])
@editor_required
def ack(alert_id: int):
    _require_action_form()
    alert = db.session.get(Alert, alert_id) or abort(404)
    if service.ack(alert, current_user):
        audit.record("alert.ack", ("alert", alert.id, alert.title),
                     detail={"kind": alert.kind, "key": alert.key})
        db.session.commit()
        flash("Alert acknowledged.", "success")
    else:
        flash("This alert is already acknowledged or resolved.", "info")
    return redirect(url_for("alerts.index"), 303)


def _set_muted(display_id: int, muted: bool):
    _require_action_form()
    display = db.session.get(Display, display_id) or abort(404)
    # Core UPDATE: an ORM write to Display would broadcast a manifest hint to screens.
    table = Display.__table__
    db.session.execute(sa.update(table).where(table.c.id == display.id)
                       .values(alerts_muted=muted))
    audit.record("alert.mute" if muted else "alert.unmute", display)
    db.session.commit()
    if muted:
        service.resolve("display_offline", f"display:{display.id}")
        flash(f"Offline alerts for {display.name} are muted.", "success")
    else:
        flash(f"Offline alerts for {display.name} are on again.", "success")
    target = request.form.get("next") or ""
    # Same-site paths only: "//host" and "/\host" would leave the site.
    if target.startswith("/") and not target.startswith(("//", "/\\")) and "\\" not in target:
        return redirect(target, 303)
    return redirect(url_for("alerts.index"), 303)


@bp.route("/displays/<int:display_id>/mute", methods=["POST"])
@editor_required
def mute(display_id: int):
    return _set_muted(display_id, True)


@bp.route("/displays/<int:display_id>/unmute", methods=["POST"])
@editor_required
def unmute(display_id: int):
    return _set_muted(display_id, False)


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    form = AlertSettingsForm()
    if form.validate_on_submit():
        changed = []
        for name, key in _FIELDS.items():
            data = getattr(form, name).data
            if name == "email_enabled":
                value = "1" if data else "0"
            elif name == "email_to":
                value = ", ".join(mailer.parse_recipients(data))
            else:
                value = str(data).strip() if data is not None else ""
            if value != str(Setting.get(key) or ""):
                changed.append(key)
                Setting.set(key, value)
        if changed:
            audit.record("alerts.settings", detail={"changed": changed})
        db.session.commit()
        flash("Alert settings saved.", "success")
        return redirect(url_for("alerts.settings"))
    if not form.is_submitted():
        for name, key in _FIELDS.items():
            field = getattr(form, name)
            if name == "email_enabled":
                field.data = Setting.get_bool(key)
            elif field.type == "IntegerField":
                field.data = Setting.get_int(key)
            else:
                field.data = Setting.get(key) or ""
    return render_template("alerts/settings.html", form=form, action_form=ActionForm(),
                           password_set=bool(current_app.config.get("SMTP_PASSWORD")))


@bp.route("/settings/test", methods=["POST"])
@admin_required
def test_email():
    _require_action_form()
    cfg = mailer.settings()
    if not cfg.configured:
        flash("Save an SMTP server, a from address and at least one recipient first.",
              "warning")
        return redirect(url_for("alerts.settings"), 303)
    org = Setting.get("org_name") or "AiSignX"
    try:
        mailer.send(f"[AiSignX] {org}: test e-mail",
                    "This is a test e-mail from AiSignX. Alert e-mails will look like this.",
                    cfg.recipients, cfg)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
        audit.record_now("smtp.test", detail={"ok": False, "error": error,
                                              "recipients": len(cfg.recipients)})
        flash(f"The test e-mail failed: {error}", "danger")
        return redirect(url_for("alerts.settings"), 303)
    audit.record_now("smtp.test", detail={"ok": True, "recipients": len(cfg.recipients)})
    flash(f"Test e-mail sent to {', '.join(cfg.recipients)}.", "success")
    return redirect(url_for("alerts.settings"), 303)
