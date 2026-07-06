from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import bp
from .forms import LoginForm, RegisterForm, ChangePasswordForm
from ..extensions import db
from ..models import User, Client, ProfileConcept


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    form = LoginForm()
    register_form = RegisterForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip()).first()
        if user and user.is_active and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            return _redirect_by_role(user)
        flash("メールアドレスまたはパスワードが正しくありません。", "danger")
    return render_template("auth/login.html", form=form, register_form=register_form)


@bp.route("/register", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    register_form = RegisterForm()
    form = LoginForm()
    if register_form.validate_on_submit():
        email = register_form.email.data.strip()
        if User.query.filter_by(email=email).first():
            flash("そのメールアドレスはすでに登録されています。", "danger")
            return render_template("auth/login.html", form=form, register_form=register_form)
        client = Client(name=register_form.display_name.data.strip())
        db.session.add(client)
        db.session.flush()
        profile = ProfileConcept(client_id=client.id)
        db.session.add(profile)
        user = User(
            email=email,
            password_hash=generate_password_hash(register_form.password.data, method="pbkdf2:sha256"),
            role="admin",
            client_id=client.id,
            display_name=register_form.display_name.data.strip(),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("アカウントを作成しました。まず「アカウント設定」から発信コンセプトを入力しましょう。", "success")
        return redirect(url_for("admin.profile"))
    for field, errors in register_form.errors.items():
        for e in errors:
            flash(f"{e}", "danger")
    return render_template("auth/login.html", form=form, register_form=register_form)


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not check_password_hash(current_user.password_hash, form.current_password.data):
            flash("現在のパスワードが正しくありません。", "danger")
            return render_template("auth/change_password.html", form=form)
        current_user.password_hash = generate_password_hash(form.new_password.data, method="pbkdf2:sha256")
        db.session.commit()
        flash("パスワードを変更しました。", "success")
        return _redirect_by_role(current_user)
    return render_template("auth/change_password.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


def _redirect_by_role(user):
    if user.role == "master":
        return redirect(url_for("master.client_list"), 303)
    if user.role == "admin":
        return redirect(url_for("admin.dashboard"), 303)
    return redirect(url_for("user.calendar"), 303)
