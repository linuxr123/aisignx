"""API token pages: your own tokens (any signed-in user) and every token (admin)."""
from flask import abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from wtforms.fields.choices import SelectChoice

from ..auth.decorators import admin_required
from ..extensions import db
from ..models import ApiToken, User
from . import bp, service
from .forms import RevokeForm, TokenForm

SCOPE_HELP = {"media:read": "List and read media",
              "media:write": "Upload and replace media (PowerPoint add-in)",
              "emergency:send": "Send and clear emergency messages (buttons, scripts, webhooks)"}


def _token_form() -> TokenForm:
    form = TokenForm()
    form.scopes.choices = [SelectChoice(s, f"{s}: {SCOPE_HELP.get(s, '')}")
                           for s in service.allowed_scopes(current_user)]
    if request.method == "GET" and not form.scopes.data:
        form.scopes.data = [c.value for c in form.scopes.choices]
    return form


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    form = _token_form()
    if form.validate_on_submit():
        try:
            token, raw = service.create(current_user, form.name.data, form.scopes.data,
                                        form.expires_days())
        except ValueError as exc:
            db.session.rollback()
            form.scopes.errors = list(form.scopes.errors) + [str(exc)]
        else:
            db.session.commit()
            resp = make_response(render_template("tokens/created.html", token=token, raw=raw))
            resp.headers["Cache-Control"] = "no-store"  # the raw token is shown only here
            return resp
    tokens = (ApiToken.query.filter_by(user_id=current_user.id)
              .order_by(ApiToken.created_at.desc(), ApiToken.id.desc()).all())
    return render_template("tokens/index.html", form=form, tokens=tokens,
                           revoke_form=RevokeForm(), status=service.status)


@bp.route("/all")
@admin_required
def admin_index():
    tokens = (ApiToken.query.join(User, ApiToken.user_id == User.id)
              .order_by(ApiToken.revoked_at.is_not(None), User.username,
                        ApiToken.created_at.desc()).all())
    return render_template("tokens/all.html", tokens=tokens, revoke_form=RevokeForm(),
                           status=service.status)


@bp.route("/<int:token_id>/revoke", methods=["POST"])
@login_required
def revoke(token_id: int):
    if not RevokeForm().validate_on_submit():
        abort(400)
    token = db.session.get(ApiToken, token_id)
    if token is None or (token.user_id != current_user.id and not current_user.is_admin):
        abort(404)
    if service.revoke(token):
        db.session.commit()
        flash(f"Revoked token {token.name}. Programs using it can no longer connect.", "success")
    back = "tokens.admin_index" if request.form.get("back") == "all" and current_user.is_admin \
        else "tokens.index"
    return redirect(url_for(back))
