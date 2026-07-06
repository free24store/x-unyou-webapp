from datetime import date
from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from . import bp
from ..extensions import db
from ..models import CalendarEntry, Draft, MetricEntry

ALLOWED_ROLES = ("user", "admin", "master")


@bp.before_request
@login_required
def check_user():
    if not current_user.is_authenticated or current_user.role not in ALLOWED_ROLES:
        abort(403)


def _client_id():
    return current_user.client_id


@bp.route("/calendar")
def calendar():
    entries = CalendarEntry.query.filter_by(client_id=_client_id()).order_by(CalendarEntry.generated_at.desc()).all()
    return render_template("user/calendar.html", entries=entries)


@bp.route("/drafts")
def drafts():
    all_drafts = Draft.query.filter_by(client_id=_client_id()).order_by(Draft.generated_at.desc()).all()
    return render_template("user/drafts.html", drafts=all_drafts)


@bp.route("/drafts/<int:draft_id>/review", methods=["POST"])
def draft_review(draft_id):
    draft = Draft.query.get_or_404(draft_id)
    if draft.client_id != _client_id():
        abort(403)
    draft.reviewed = not draft.reviewed
    db.session.commit()
    return redirect(url_for("user.drafts"))


@bp.route("/metrics/log", methods=["GET", "POST"])
def metrics_log():
    client_id = _client_id()
    if request.method == "POST":
        entry_date = request.form.get("date") or date.today().isoformat()
        existing = MetricEntry.query.filter_by(client_id=client_id, date=entry_date).first()
        if existing:
            m = existing
        else:
            m = MetricEntry(client_id=client_id, date=entry_date, logged_by_user_id=current_user.id)
            db.session.add(m)

        def _f(key):
            v = request.form.get(key, "").strip()
            return float(v) if v else None

        m.posts_per_day = _f("posts_per_day")
        m.avg_impressions = _f("avg_impressions")
        m.engagement_rate_pct = _f("engagement_rate_pct")
        m.followers_delta_per_day = _f("followers_delta_per_day")
        m.list_signups_per_day = _f("list_signups_per_day")
        m.meeting_rate_pct = _f("meeting_rate_pct")
        m.conversion_rate_pct = _f("conversion_rate_pct")
        db.session.commit()
        flash("指標を記録しました。", "success")
        return redirect(url_for("user.metrics_log"))
    recent = MetricEntry.query.filter_by(client_id=client_id).order_by(MetricEntry.date.desc()).limit(7).all()
    return render_template("user/metrics_log.html", recent=recent)
