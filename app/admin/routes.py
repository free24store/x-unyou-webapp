from datetime import date, datetime
from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from . import bp
from ..extensions import db
from ..models import (Client, User, ProfileConcept, MetricEntry,
                      ConsultNote, CalendarEntry, Draft,
                      LandingPage, SalesLetter, LineStepSet,
                      ContactMessage, StripeProduct, StoryCampaign,
                      ScheduledPost)
from ..services.vocab import load_vocab
from ..services.diagnostics_service import infer_phase, funnel_diagnostics, PHASE_LABELS
from ..services.calendar_service import generate_calendar
from ..services.draft_service import (generate_drafts, generate_guda_drafts,
                                      generate_story_draft, generate_profile_bio)


@bp.before_request
@login_required
def check_admin():
    if not current_user.is_authenticated or current_user.role not in ("admin", "master"):
        abort(403)


def _client_id():
    return current_user.client_id


@bp.route("/")
def dashboard():
    return redirect(url_for("admin.analytics"))


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()
    if request.method == "POST":
        profile.genre = request.form.get("genre", "").strip()
        profile.who = request.form.get("who", "").strip()
        profile.what = request.form.get("what", "").strip()
        profile.how = request.form.get("how", "").strip()
        profile.display_name = request.form.get("display_name", "").strip()
        profile.position = request.form.get("position", "").strip()
        profile.achievement = request.form.get("achievement", "").strip()
        profile.current_phase = request.form.get("current_phase", "player").strip()
        profile.updated_at = datetime.utcnow()
        db.session.commit()
        flash("プロフィールを保存しました。", "success")
        return redirect(url_for("admin.profile"))
    return render_template("admin/profile.html", profile=profile)


@bp.route("/calendar", methods=["GET", "POST"])
def calendar():
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()
    vocab = load_vocab()
    entries = CalendarEntry.query.filter_by(client_id=client_id).order_by(CalendarEntry.generated_at.desc()).all()
    if request.method == "POST":
        week_no = int(request.form.get("week_no", 1))
        days = generate_calendar(week_no, profile.current_phase, vocab)
        entry = CalendarEntry(
            client_id=client_id,
            week_no=week_no,
            phase_at_generation=profile.current_phase,
            content_json=days,
            generated_by_user_id=current_user.id,
        )
        db.session.add(entry)
        db.session.commit()
        flash(f"Week {week_no} のカレンダーを生成しました。", "success")
        return redirect(url_for("admin.calendar"))
    return render_template("admin/calendar.html", entries=entries, profile=profile)


def _save_drafts(raw_drafts, client_id, batch_id):
    for d in raw_drafts:
        draft = Draft(
            client_id=client_id,
            batch_id=batch_id,
            n=d["n"],
            hook=d["hook"],
            target=d["target"],
            reinforcement=d["reinforcement"],
            education_name=d["education_name"],
            source=d["source"],
            text=d["text"],
            generated_by_user_id=current_user.id,
        )
        db.session.add(draft)
    db.session.commit()


@bp.route("/drafts", methods=["GET", "POST"])
def drafts():
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()
    vocab = load_vocab()
    if request.method == "POST":
        draft_type = request.form.get("draft_type", "normal")
        batch_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        profile_dict = {
            "genre": profile.genre, "who": profile.who,
            "what": profile.what, "how": profile.how,
            "position": profile.position, "achievement": profile.achievement,
        }

        if draft_type == "guda":
            guda_ids = request.form.getlist("guda_ids")
            if not guda_ids:
                flash("グダ項目を1つ以上選択してください。", "danger")
                return redirect(url_for("admin.drafts"))
            raw_drafts = generate_guda_drafts(guda_ids, profile_dict, vocab)
            _save_drafts(raw_drafts, client_id, batch_id)
            flash(f"{len(raw_drafts)}件のグダ消しドラフトを生成しました。", "success")

        elif draft_type == "story":
            story = {
                "ki": request.form.get("ki", ""),
                "sho": request.form.get("sho", ""),
                "ten": request.form.get("ten", ""),
                "ketsu": request.form.get("ketsu", ""),
            }
            raw = generate_story_draft(story, profile_dict)
            _save_drafts([raw], client_id, batch_id)
            flash("ストーリー型ドラフトを生成しました。", "success")

        else:
            count = min(int(request.form.get("count", 3)), 10)
            edu_id = request.form.get("education_id") or None
            raw_drafts = generate_drafts(count, profile_dict, vocab, edu_id)
            _save_drafts(raw_drafts, client_id, batch_id)
            flash(f"{count}件のドラフトを生成しました。", "success")

        return redirect(url_for("admin.drafts"))

    all_drafts = Draft.query.filter_by(client_id=client_id).order_by(Draft.generated_at.desc()).all()
    edu_stages = vocab["education_stages_basic"] + vocab["education_stages_boost"]
    guda_items = vocab.get("guda_items", [])
    return render_template("admin/drafts.html", drafts=all_drafts,
                           edu_stages=edu_stages, guda_items=guda_items)


@bp.route("/drafts/<int:draft_id>/review", methods=["POST"])
def draft_review(draft_id):
    draft = Draft.query.get_or_404(draft_id)
    if draft.client_id != _client_id():
        abort(403)
    draft.reviewed = not draft.reviewed
    db.session.commit()
    return redirect(url_for("admin.drafts"))


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
        return redirect(url_for("admin.analytics"))
    recent = MetricEntry.query.filter_by(client_id=client_id).order_by(MetricEntry.date.desc()).limit(7).all()
    return render_template("admin/metrics_log.html", recent=recent)


@bp.route("/analytics")
def analytics():
    client_id = _client_id()
    vocab = load_vocab()
    metrics = MetricEntry.query.filter_by(client_id=client_id).order_by(MetricEntry.date).all()
    latest = metrics[-1] if metrics else None
    phase = infer_phase(latest, vocab)
    diagnostics = funnel_diagnostics(latest, vocab)
    notes = ConsultNote.query.filter_by(client_id=client_id).order_by(ConsultNote.created_at.desc()).all()
    metric_labels = [m.date.isoformat() for m in metrics]
    metric_series = {
        "avg_impressions": [m.avg_impressions for m in metrics],
        "engagement_rate_pct": [m.engagement_rate_pct for m in metrics],
        "followers_delta_per_day": [m.followers_delta_per_day for m in metrics],
        "list_signups_per_day": [m.list_signups_per_day for m in metrics],
        "meeting_rate_pct": [m.meeting_rate_pct for m in metrics],
        "conversion_rate_pct": [m.conversion_rate_pct for m in metrics],
    }
    # セットアップ進捗チェック
    from ..models import SnsConnection, ScheduledPost
    from datetime import timedelta
    profile_obj = ProfileConcept.query.filter_by(client_id=client_id).first()
    setup_steps = {
        "profile":   bool(profile_obj and profile_obj.who),
        "calendar":  CalendarEntry.query.filter_by(client_id=client_id).count() > 0,
        "drafts":    Draft.query.filter_by(client_id=client_id).count() > 0,
        "sns":       SnsConnection.query.filter_by(client_id=client_id, is_active=True).count() > 0,
        "scheduled": ScheduledPost.query.filter_by(client_id=client_id, status="pending").count() > 0,
    }
    setup_done = all(setup_steps.values())

    # 今日投稿すべき予定を取得
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
    today_end   = today_start + timedelta(days=1)
    today_posts = ScheduledPost.query.filter_by(client_id=client_id, status="pending").filter(
        ScheduledPost.scheduled_at >= today_start,
        ScheduledPost.scheduled_at < today_end
    ).all()

    return render_template(
        "admin/analytics.html",
        phase=phase, phase_label=PHASE_LABELS[phase],
        diagnostics=diagnostics, notes=notes,
        metric_labels=metric_labels, metric_series=metric_series,
        setup_steps=setup_steps, setup_done=setup_done,
        today_posts=today_posts,
    )


@bp.route("/profile/generate-bio", methods=["POST"])
def generate_bio():
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()
    profile_dict = {
        "genre": profile.genre, "who": profile.who, "what": profile.what,
        "how": profile.how, "position": profile.position, "achievement": profile.achievement,
    }
    bio = generate_profile_bio(profile_dict)
    from flask import jsonify
    return jsonify({"bio": bio})


@bp.route("/users")
def users():
    client_id = _client_id()
    members = User.query.filter_by(client_id=client_id).order_by(User.created_at).all()
    return render_template("admin/users.html", members=members)


@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    client_id = _client_id()
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        display_name = request.form.get("display_name", "メンバー").strip()
        if not email or not password:
            flash("メールアドレスとパスワードを入力してください。", "danger")
            return render_template("admin/user_new.html")
        if User.query.filter_by(email=email).first():
            flash("そのメールアドレスはすでに使われています。", "danger")
            return render_template("admin/user_new.html")
        member = User(
            email=email,
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
            role="user",
            client_id=client_id,
            display_name=display_name,
            is_active=True,
        )
        db.session.add(member)
        db.session.commit()
        flash(f"メンバー「{display_name}」を招待しました。", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_new.html")


# ──────────────────────────────────────────────
# LP（ランディングページ）管理
# ──────────────────────────────────────────────

@bp.route("/lp")
def lp_list():
    client_id = _client_id()
    pages = LandingPage.query.filter_by(client_id=client_id).order_by(LandingPage.created_at.desc()).all()
    return render_template("admin/lp_list.html", pages=pages, client_id=client_id)


@bp.route("/lp/new", methods=["GET", "POST"])
def lp_new():
    from ..services.lp_service import generate_lp_html
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()

    if request.method == "POST":
        line_url = request.form.get("line_url", "").strip()
        title = request.form.get("title", "LP").strip() or "LP"
        body_html = generate_lp_html(profile, line_url)
        page = LandingPage(
            client_id=client_id,
            title=title,
            body_html=body_html,
            line_url=line_url,
            is_published=True,
            created_by_user_id=current_user.id,
        )
        db.session.add(page)
        db.session.commit()
        flash(f"LP「{title}」を生成しました。", "success")
        return redirect(url_for("admin.lp_list"))

    return render_template("admin/lp_new.html", profile=profile)


@bp.route("/lp/<int:page_id>/toggle", methods=["POST"])
def lp_toggle(page_id):
    page = LandingPage.query.get_or_404(page_id)
    if page.client_id != _client_id():
        abort(403)
    page.is_published = not page.is_published
    db.session.commit()
    return redirect(url_for("admin.lp_list"))


@bp.route("/lp/<int:page_id>/delete", methods=["POST"])
def lp_delete(page_id):
    page = LandingPage.query.get_or_404(page_id)
    if page.client_id != _client_id():
        abort(403)
    db.session.delete(page)
    db.session.commit()
    flash("LPを削除しました。", "success")
    return redirect(url_for("admin.lp_list"))


# ──────────────────────────────────────────────
# セールスレター管理
# ──────────────────────────────────────────────

@bp.route("/sales-letter")
def sl_list():
    client_id = _client_id()
    letters = SalesLetter.query.filter_by(client_id=client_id).order_by(SalesLetter.created_at.desc()).all()
    products = StripeProduct.query.filter_by(client_id=client_id).order_by(StripeProduct.created_at.desc()).all()
    return render_template("admin/sl_list.html", letters=letters, products=products, client_id=client_id)


@bp.route("/sales-letter/new", methods=["GET", "POST"])
def sl_new():
    from ..services.lp_service import generate_sales_letter_html
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()
    products = StripeProduct.query.filter_by(client_id=client_id).order_by(StripeProduct.created_at.desc()).all()

    if request.method == "POST":
        product_name  = request.form.get("product_name", "").strip()
        price_jpy     = int(request.form.get("price_jpy", 0) or 0)
        benefits      = request.form.get("benefits", "").strip()
        deadline      = request.form.get("deadline", "").strip()
        stripe_link   = request.form.get("stripe_link", "").strip()
        contact_email = request.form.get("contact_email", "").strip()
        contact_phone = request.form.get("contact_phone", "").strip()
        title         = request.form.get("title", product_name).strip() or product_name

        body_html = generate_sales_letter_html(
            profile, product_name, price_jpy,
            benefits, deadline, stripe_link, contact_email, contact_phone,
        )
        letter = SalesLetter(
            client_id=client_id,
            title=title,
            product_name=product_name,
            price_jpy=price_jpy,
            body_html=body_html,
            stripe_link=stripe_link,
            contact_email=contact_email,
            contact_phone=contact_phone,
            is_published=True,
            created_by_user_id=current_user.id,
        )
        db.session.add(letter)
        db.session.commit()
        flash(f"セールスレター「{title}」を生成しました。", "success")
        return redirect(url_for("admin.sl_list"))

    return render_template("admin/sl_new.html", profile=profile, products=products)


@bp.route("/sales-letter/<int:letter_id>/toggle", methods=["POST"])
def sl_toggle(letter_id):
    letter = SalesLetter.query.get_or_404(letter_id)
    if letter.client_id != _client_id():
        abort(403)
    letter.is_published = not letter.is_published
    db.session.commit()
    return redirect(url_for("admin.sl_list"))


@bp.route("/sales-letter/<int:letter_id>/delete", methods=["POST"])
def sl_delete(letter_id):
    letter = SalesLetter.query.get_or_404(letter_id)
    if letter.client_id != _client_id():
        abort(403)
    db.session.delete(letter)
    db.session.commit()
    flash("セールスレターを削除しました。", "success")
    return redirect(url_for("admin.sl_list"))


# ──────────────────────────────────────────────
# LINEステップ配信管理
# ──────────────────────────────────────────────

@bp.route("/line-steps")
def line_steps_list():
    client_id = _client_id()
    step_sets = LineStepSet.query.filter_by(client_id=client_id).order_by(LineStepSet.created_at.desc()).all()
    return render_template("admin/line_steps_list.html", step_sets=step_sets)


@bp.route("/line-steps/new", methods=["GET", "POST"])
def line_steps_new():
    from ..services.lp_service import generate_line_steps
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()

    if request.method == "POST":
        product_name = request.form.get("product_name", "").strip()
        sl_url = request.form.get("sl_url", "").strip()
        title = request.form.get("title", "LINEステップ").strip() or "LINEステップ"

        steps = generate_line_steps(profile, product_name, sl_url)
        step_set = LineStepSet(
            client_id=client_id,
            title=title,
            steps_json=steps,
            created_by_user_id=current_user.id,
        )
        db.session.add(step_set)
        db.session.commit()
        flash(f"LINEステップ「{title}」を生成しました。", "success")
        return redirect(url_for("admin.line_steps_list"))

    letters = SalesLetter.query.filter_by(client_id=client_id).order_by(SalesLetter.created_at.desc()).all()
    return render_template("admin/line_steps_new.html", profile=profile, letters=letters)


@bp.route("/line-steps/<int:set_id>/delete", methods=["POST"])
def line_steps_delete(set_id):
    step_set = LineStepSet.query.get_or_404(set_id)
    if step_set.client_id != _client_id():
        abort(403)
    db.session.delete(step_set)
    db.session.commit()
    flash("LINEステップを削除しました。", "success")
    return redirect(url_for("admin.line_steps_list"))


# ──────────────────────────────────────────────
# お問い合わせ受信ボックス
# ──────────────────────────────────────────────

@bp.route("/contact-inbox")
def contact_inbox():
    client_id = _client_id()
    messages = ContactMessage.query.filter_by(client_id=client_id).order_by(ContactMessage.created_at.desc()).all()
    unread = sum(1 for m in messages if not m.is_read)
    return render_template("admin/contact_inbox.html", messages=messages, unread=unread)


@bp.route("/contact-inbox/<int:msg_id>/read", methods=["POST"])
def contact_mark_read(msg_id):
    msg = ContactMessage.query.get_or_404(msg_id)
    if msg.client_id != _client_id():
        abort(403)
    msg.is_read = True
    db.session.commit()
    return redirect(url_for("admin.contact_inbox"))


# ──────────────────────────────────────────────
# Stripe決済連携
# ──────────────────────────────────────────────

@bp.route("/stripe/products")
def stripe_products():
    client_id = _client_id()
    products = StripeProduct.query.filter_by(client_id=client_id).order_by(StripeProduct.created_at.desc()).all()
    return render_template("admin/stripe_products.html", products=products)


@bp.route("/stripe/products/new", methods=["GET", "POST"])
def stripe_product_new():
    import os
    client_id = _client_id()

    if request.method == "POST":
        product_name = request.form.get("product_name", "").strip()
        price_jpy    = int(request.form.get("price_jpy", 0) or 0)

        stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
        payment_link_url = ""
        stripe_price_id  = ""

        if stripe_key and product_name and price_jpy > 0:
            try:
                import stripe as _stripe
                _stripe.api_key = stripe_key
                price_obj = _stripe.Price.create(
                    currency="jpy",
                    unit_amount=price_jpy,
                    product_data={"name": product_name},
                )
                stripe_price_id = price_obj["id"]
                link_obj = _stripe.PaymentLink.create(
                    line_items=[{"price": stripe_price_id, "quantity": 1}]
                )
                payment_link_url = link_obj["url"]
            except Exception as e:
                flash(f"Stripe APIエラー: {e}", "danger")
        elif not stripe_key:
            flash("STRIPE_SECRET_KEY が未設定です。.env に追加してください。", "warning")

        prod = StripeProduct(
            client_id=client_id,
            product_name=product_name,
            price_jpy=price_jpy,
            stripe_price_id=stripe_price_id,
            payment_link_url=payment_link_url,
            created_by_user_id=current_user.id,
        )
        db.session.add(prod)
        db.session.commit()

        if payment_link_url:
            flash(f"決済リンクを作成しました。", "success")
        elif not stripe_key:
            pass
        else:
            flash(f"商品「{product_name}」をDBに保存しました（Stripe未連携）。", "info")

        return redirect(url_for("admin.stripe_products"))

    return render_template("admin/stripe_product_new.html")


# ──────────────────────────────────────────────
# ストーリーキャンペーン管理
# ──────────────────────────────────────────────

@bp.route("/story-campaign")
def story_campaign_list():
    client_id = _client_id()
    campaigns = StoryCampaign.query.filter_by(client_id=client_id).order_by(StoryCampaign.created_at.desc()).all()
    return render_template("admin/story_campaign_list.html", campaigns=campaigns)


@bp.route("/story-campaign/new", methods=["GET", "POST"])
def story_campaign_new():
    from ..services.story_campaign_service import create_campaign_posts
    from ..services.image_service import is_available as image_available
    import datetime as dt

    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()

    if request.method == "POST":
        title         = request.form.get("title", "ストーリーキャンペーン").strip()
        product_name  = request.form.get("product_name", "").strip()
        start_str     = request.form.get("start_date", "")
        end_str       = request.form.get("end_date", "")
        posts_per_day = max(1, min(5, int(request.form.get("posts_per_day", 1) or 1)))
        platform      = request.form.get("platform", "x")
        with_image    = request.form.get("with_image") == "1"
        generate_now  = request.form.get("generate_now") == "1"

        try:
            start_date = dt.date.fromisoformat(start_str)
            end_date   = dt.date.fromisoformat(end_str)
        except ValueError:
            flash("日付の形式が正しくありません。", "danger")
            return redirect(url_for("admin.story_campaign_new"))

        if end_date < start_date:
            flash("終了日は開始日より後に設定してください。", "danger")
            return redirect(url_for("admin.story_campaign_new"))

        total_days = (end_date - start_date).days + 1
        total_posts_estimate = total_days * posts_per_day
        if total_posts_estimate > 200:
            flash(f"投稿数が多すぎます（{total_posts_estimate}件）。期間を短くするか1日あたりの投稿数を減らしてください。", "danger")
            return redirect(url_for("admin.story_campaign_new"))

        campaign = StoryCampaign(
            client_id=client_id,
            title=title,
            product_name=product_name,
            start_date=start_date,
            end_date=end_date,
            posts_per_day=posts_per_day,
            platform=platform,
            with_image=with_image,
            status="running",
            created_by_user_id=current_user.id,
        )
        db.session.add(campaign)
        db.session.flush()  # IDを確定

        create_campaign_posts(campaign, profile, db.session, generate_texts=generate_now)

        flash(f"キャンペーン「{title}」を作成しました（{campaign.total_posts}件の投稿）。", "success")
        return redirect(url_for("admin.story_campaign_detail", campaign_id=campaign.id))

    today = date.today()
    image_ok = image_available()
    return render_template("admin/story_campaign_new.html",
                           profile=profile, today=today, image_ok=image_ok)


@bp.route("/story-campaign/<int:campaign_id>")
def story_campaign_detail(campaign_id):
    client_id = _client_id()
    campaign = StoryCampaign.query.get_or_404(campaign_id)
    if campaign.client_id != client_id:
        abort(403)

    posts = (ScheduledPost.query
             .filter_by(client_id=client_id, status="pending")
             .filter(ScheduledPost.scheduled_at >= datetime.combine(campaign.start_date, datetime.min.time()))
             .filter(ScheduledPost.scheduled_at <= datetime.combine(campaign.end_date, datetime.max.time()))
             .order_by(ScheduledPost.scheduled_at)
             .all())

    return render_template("admin/story_campaign_detail.html", campaign=campaign, posts=posts)


@bp.route("/story-campaign/<int:campaign_id>/delete", methods=["POST"])
def story_campaign_delete(campaign_id):
    client_id = _client_id()
    campaign = StoryCampaign.query.get_or_404(campaign_id)
    if campaign.client_id != client_id:
        abort(403)

    # 関連する予約投稿も削除
    ScheduledPost.query.filter_by(client_id=client_id, status="pending").filter(
        ScheduledPost.scheduled_at >= datetime.combine(campaign.start_date, datetime.min.time()),
        ScheduledPost.scheduled_at <= datetime.combine(campaign.end_date, datetime.max.time()),
    ).delete()

    db.session.delete(campaign)
    db.session.commit()
    flash("キャンペーンを削除しました。", "success")
    return redirect(url_for("admin.story_campaign_list"))


@bp.route("/story-campaign/post/<int:post_id>/edit", methods=["GET", "POST"])
def campaign_post_edit(post_id):
    post = ScheduledPost.query.get_or_404(post_id)
    if post.client_id != _client_id():
        abort(403)

    if request.method == "POST":
        post.text = request.form.get("text", post.text).strip()
        scheduled_str = request.form.get("scheduled_at", "")
        if scheduled_str:
            try:
                post.scheduled_at = datetime.fromisoformat(scheduled_str)
            except ValueError:
                pass
        db.session.commit()
        flash("投稿内容を更新しました。", "success")
        return redirect(request.referrer or url_for("admin.story_campaign_list"))

    return render_template("admin/campaign_post_edit.html", post=post)
