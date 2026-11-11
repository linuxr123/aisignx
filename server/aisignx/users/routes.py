from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user

from ..auth.decorators import admin_required
from ..extensions import db
from ..models import User
from ..models.user import ROLE_ADMIN
from . import bp
from .forms import DeleteForm, UserCreateForm, UserEditForm


def _get_user_or_404(user_id: int) -> User:
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return user


def _other_active_admins(user: User) -> int:
    return User.query.filter(
        User.role == ROLE_ADMIN, User.is_active_flag.is_(True), User.id != user.id
    ).count()


@bp.route("/")
@admin_required
def index():
    users = User.query.order_by(User.username).all()
    return render_template("users/index.html", users=users, delete_form=DeleteForm())


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    form = UserCreateForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            form.username.errors.append("That username is already taken.")
        else:
            user = User(username=username, email=(form.email.data or "").strip() or None,
                        role=form.role.data)
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash(f"User {user.username} created.", "success")
            return redirect(url_for("users.index"))
    return render_template("users/form.html", form=form, user=None)


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(user_id: int):
    user = _get_user_or_404(user_id)
    form = UserEditForm(obj=user)
    if not form.is_submitted():
        form.active.data = user.is_active

    if form.validate_on_submit():
        losing_admin = user.is_admin and (form.role.data != ROLE_ADMIN or not form.active.data)
        if user.id == current_user.id and (form.role.data != user.role or not form.active.data):
            flash("You cannot change your own role or deactivate yourself.", "danger")
        elif losing_admin and _other_active_admins(user) == 0:
            flash("At least one active admin is required.", "danger")
        else:
            user.email = (form.email.data or "").strip() or None
            user.role = form.role.data
            user.is_active_flag = form.active.data
            if form.password.data:
                user.set_password(form.password.data)
            db.session.commit()
            flash(f"User {user.username} updated.", "success")
            return redirect(url_for("users.index"))
    return render_template("users/form.html", form=form, user=user)


@bp.route("/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete(user_id: int):
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    user = _get_user_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
    elif user.is_admin and _other_active_admins(user) == 0:
        flash("At least one active admin is required.", "danger")
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f"User {user.username} deleted.", "success")
    return redirect(url_for("users.index"))
