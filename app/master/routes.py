from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from . import bp
from ..auth.decorators import role_required
from ..extensions import db
from ..models import Client, User, ProfileConcept, MetricEntry, ConsultNote
from ..services.vocab import load_vocab
from ..services.diagnostics_service import infer_phase, funnel_diagnostics, PHASE_LABELS


def _master_required(f):
    return login_required(role_required("master")(f))


@bp.before_request
@login_required
def check_master():
    if not current_user.is_authenticated or current_user.role != "master":
        abort(403)


@bp.route("/")
def client_list():
    vocab = load_vocab()
    clients = Client.query.order_by(Client.created_at).all()
    summaries = []
    for c in clients:
        latest = MetricEntry.query.filter_by(client_id=c.id).order_by(MetricEntry.date.desc()).first()
        phase = infer_phase(latest, vocab)
        summaries.append({"client": c, "phase": phase, "phase_label": PHASE_LABELS[phase], "latest": latest})
    return render_template("master/client_list.html", summaries=summaries)


@bp.route("/clients/new", methods=["GET", "POST"])
def client_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        admin_email = request.form.get("admin_email", "").strip()
        admin_password = request.form.get("admin_password", "").strip()
        admin_display = request.form.get("admin_display", "管理者").strip()
        if not name or not admin_email or not admin_password:
            flash("全項目を入力してください。", "danger")
            return render_template("master/client_new.html")
        if User.query.filter_by(email=admin_email).first():
            flash("そのメールアドレスはすでに使われています。", "danger")
            return render_template("master/client_new.html")
        client = Client(name=name)
        db.session.add(client)
        db.session.flush()
        profile = ProfileConcept(client_id=client.id)
        admin = User(
            email=admin_email,
            password_hash=generate_password_hash(admin_password, method="pbkdf2:sha256"),
            role="admin",
            client_id=client.id,
            display_name=admin_display,
            is_active=True,
        )
        db.session.add(profile)
        db.session.add(admin)
        db.session.commit()
        flash(f"クライアント「{name}」を作成しました。", "success")
        return redirect(url_for("master.client_list"))
    return render_template("master/client_new.html")


@bp.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    vocab = load_vocab()
    metrics = MetricEntry.query.filter_by(client_id=client_id).order_by(MetricEntry.date).all()
    latest = metrics[-1] if metrics else None
    phase = infer_phase(latest, vocab)
    diagnostics = funnel_diagnostics(latest, vocab)
    notes = ConsultNote.query.filter_by(client_id=client_id).order_by(ConsultNote.created_at.desc()).all()
    profile = client.profile
    metric_labels = [m.date.isoformat() for m in metrics]
    metric_series = {
        "avg_impressions": [m.avg_impressions for m in metrics],
        "engagement_rate_pct": [m.engagement_rate_pct for m in metrics],
        "followers_delta_per_day": [m.followers_delta_per_day for m in metrics],
        "list_signups_per_day": [m.list_signups_per_day for m in metrics],
        "meeting_rate_pct": [m.meeting_rate_pct for m in metrics],
        "conversion_rate_pct": [m.conversion_rate_pct for m in metrics],
    }
    return render_template(
        "master/client_detail.html",
        client=client, profile=profile, phase=phase, phase_label=PHASE_LABELS[phase],
        diagnostics=diagnostics, notes=notes,
        metric_labels=metric_labels, metric_series=metric_series,
    )


@bp.route("/clients/<int:client_id>/notes", methods=["POST"])
def add_note(client_id):
    client = Client.query.get_or_404(client_id)
    body = request.form.get("body", "").strip()
    if not body:
        flash("ノート本文を入力してください。", "danger")
        return redirect(url_for("master.client_detail", client_id=client_id))
    note = ConsultNote(client_id=client_id, author_user_id=current_user.id, body=body)
    db.session.add(note)
    db.session.commit()
    flash("コンサルノートを追加しました。", "success")
    return redirect(url_for("master.client_detail", client_id=client_id))
