from datetime import date, datetime, time
from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from . import bp
from ..extensions import db
from ..models import (Client, User, ProfileConcept, MetricEntry,
                      ConsultNote, CalendarEntry, Draft,
                      LandingPage, SalesLetter, LineStepSet,
                      ContactMessage, StripeProduct, StoryCampaign,
                      ScheduledPost, Testimonial, Purchase,
                      POST_STATUS_PENDING, POST_STATUS_APPROVED,
                      POST_STATUS_LABELS, can_transition)
from ..services.vocab import load_vocab
from ..services.diagnostics_service import infer_phase, funnel_diagnostics, PHASE_LABELS
from ..services.calendar_service import generate_calendar
from ..services.draft_service import (generate_drafts, generate_guda_drafts,
                                      generate_story_draft, generate_profile_bio,
                                      generate_display_name)
from ..services import batch_service


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


# ──────────────────────────────────────────────
# E3-5: 運用ダッシュボード統合（1画面集約・読み取りのみ）
# ──────────────────────────────────────────────
# 機能別に散らばった運用状況（承認待ち・KPI・相談リード・購入・勝ち型）を
# 当該clientについて1画面に集約する。schema変更なし・書き込みなし・捏造なし。
# 各セクションから該当画面（schedule/engage/contact-inbox/purchases/insights）へ遷移。
# 未計測・データ無しは「観測待ち」と明示する（数値を作らない）。

@bp.route("/ops-dashboard")
def ops_dashboard():
    import os
    from datetime import timedelta
    from ..models import (EngagementItem, ENGAGE_STATUS_DRAFT,
                          ENGAGE_STATUS_PENDING)
    from ..services.draft_service import (HOOK_TYPE_LABELS, FORMAT_TYPE_LABELS,
                                          CTA_TYPE_LABELS)
    client_id = _client_id()
    now = datetime.utcnow()

    # ── 承認待ちキュー ──────────────────────────────
    # 予約投稿（pending）: 承認ゲートの手前。件数＋直近数件（近い予約時刻順）。
    pending_q = ScheduledPost.query.filter_by(
        client_id=client_id, status=POST_STATUS_PENDING)
    pending_posts_count = pending_q.count()
    pending_posts_recent = (pending_q.order_by(ScheduledPost.scheduled_at.asc())
                            .limit(5).all())

    # エンゲージ・キュー（draft / pending）: 返信下書きは鮮度商品。
    engage_items = (EngagementItem.query
                    .filter_by(client_id=client_id)
                    .filter(EngagementItem.status.in_(
                        [ENGAGE_STATUS_DRAFT, ENGAGE_STATUS_PENDING]))
                    .all())
    engage_pending_count = len(engage_items)
    # 鮮度切れ（既定24h超）: sns 側と同じ既定値。読み取りのみで判定（失効はしない）。
    try:
        fresh_hours = int(os.environ.get("ENGAGE_FRESH_HOURS", "") or 24)
        if fresh_hours <= 0:
            fresh_hours = 24
    except (TypeError, ValueError):
        fresh_hours = 24
    engage_stale_count = sum(
        1 for i in engage_items
        if ((now - (i.created_at or now)) > timedelta(hours=fresh_hours)))

    # ── KPI（最新の MetricEntry）─────────────────────
    # E3-8: 主KPIは「成果に近い指標」= プロフクリック / DM相談 / エンゲージ率 を上に。
    # プロフクリックは計測列が無い → 捏造せず「観測待ち」（None）で提示する。
    latest = (MetricEntry.query.filter_by(client_id=client_id)
              .order_by(MetricEntry.date.desc()).first())

    def _latest(attr):
        return getattr(latest, attr, None) if latest is not None else None

    contact_total = ContactMessage.query.filter_by(client_id=client_id).count()
    contact_unread = ContactMessage.query.filter_by(
        client_id=client_id, is_read=False).count()

    primary_kpis = [
        {"label": "プロフクリック", "emoji": "🔎",
         "value": None, "unit": "回",
         "hint": "プロフィールを見に来た人＝濃い関心。数値連携は観測待ち"},
        {"label": "DM相談（問い合わせ）", "emoji": "📨",
         "value": contact_total, "unit": "件",
         "hint": "実際に相談が来た数＝成果に最も近い指標"},
        {"label": "エンゲージメント率", "emoji": "❤️",
         "value": _latest("engagement_rate_pct"), "unit": "%",
         "hint": "届いた人が反応したか＝到達の濃さ"},
    ]
    secondary_kpis = [
        {"label": "フォロワー増加", "emoji": "👥",
         "value": _latest("followers_delta_per_day"), "unit": "人/日",
         "hint": "増えても到達・相談に繋がらなければ参考値（vanity指標）"},
        {"label": "平均インプレッション", "emoji": "👁",
         "value": _latest("avg_impressions"), "unit": "回",
         "hint": "投稿がどれだけ見られたか＝認知の広さ"},
        {"label": "リスト/LINE登録", "emoji": "📩",
         "value": _latest("list_signups_per_day"), "unit": "件/日",
         "hint": "見込み客リストの獲得ペース"},
    ]
    metric_date = latest.date.isoformat() if latest else None

    # ── 相談リード（ContactMessage）出所別 上位 ──────────
    all_contacts = ContactMessage.query.filter_by(client_id=client_id).all()
    source_counts = {}
    for m in all_contacts:
        key = (m.source or "direct").strip() or "direct"
        source_counts[key] = source_counts.get(key, 0) + 1
    contact_sources = sorted(source_counts.items(),
                             key=lambda kv: kv[1], reverse=True)[:5]

    # ── 購入（Purchase）─────────────────────────────
    purchase_q = Purchase.query.filter_by(client_id=client_id)
    purchase_count = purchase_q.count()
    purchase_recent = (purchase_q.order_by(Purchase.created_at.desc())
                       .limit(5).all())

    # ── 勝ち型（E3-4のインサイト要約）───────────────────
    # 投稿済み（posted）かつインプ計測済みの投稿を、紐づくドラフトのタグと突き合わせ、
    # 型別の平均インプで最上位を提示。実データが無い軸は「観測待ち」（捏造しない）。
    rows = (db.session.query(ScheduledPost, Draft)
            .join(Draft, ScheduledPost.draft_id == Draft.id)
            .filter(ScheduledPost.client_id == client_id,
                    ScheduledPost.status == "posted",
                    ScheduledPost.impressions.isnot(None))
            .all())

    def _best(attr, labels):
        buckets = {}
        for sp, dr in rows:
            key = getattr(dr, attr) or "unclassified"
            b = buckets.setdefault(key, {"count": 0, "total_imp": 0})
            b["count"] += 1
            b["total_imp"] += sp.impressions or 0
        if not buckets:
            return None
        ranking = []
        for key, b in buckets.items():
            label = labels.get(key, "未分類" if key == "unclassified" else key)
            ranking.append({
                "label": label, "count": b["count"],
                "avg_imp": round(b["total_imp"] / b["count"], 1) if b["count"] else 0,
            })
        ranking.sort(key=lambda r: r["avg_imp"], reverse=True)
        return ranking[0]

    winning = {
        "measured": len(rows),
        "hook": _best("hook_type", HOOK_TYPE_LABELS),
        "format": _best("format_type", FORMAT_TYPE_LABELS),
        "cta": _best("cta_type", CTA_TYPE_LABELS),
    }

    return render_template(
        "admin/ops_dashboard.html",
        pending_posts_count=pending_posts_count,
        pending_posts_recent=pending_posts_recent,
        engage_pending_count=engage_pending_count,
        engage_stale_count=engage_stale_count,
        primary_kpis=primary_kpis, secondary_kpis=secondary_kpis,
        metric_date=metric_date, has_metrics=bool(latest),
        contact_total=contact_total, contact_unread=contact_unread,
        contact_sources=contact_sources,
        purchase_count=purchase_count, purchase_recent=purchase_recent,
        winning=winning,
    )


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
    # E6-1: CTA（オファー導線）はバッチ共通のフォーム値として受ける（無指定は空/None）
    cta_label = request.form.get("cta_label", "").strip()
    cta_url = request.form.get("cta_url", "").strip()
    offer_lp_id = request.form.get("offer_lp_id") or None
    # E3-4: フォームでCTAを付けた場合は cta_type を "offer" に補正（本文だけの
    # 推定より、明示されたオファー導線を優先する）。生成時タグは尊重する。
    from ..services.draft_service import classify_draft
    for d in raw_drafts:
        tags = classify_draft(d["text"], cta_label, cta_url)
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
            cta_label=cta_label,
            cta_url=cta_url,
            offer_lp_id=int(offer_lp_id) if offer_lp_id else None,
            hook_type=d.get("hook_type", tags["hook_type"]),
            format_type=d.get("format_type", tags["format_type"]),
            cta_type=tags["cta_type"],
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
    # E6-1: CTA（オファー導線）候補
    cta_lps = (LandingPage.query.filter_by(client_id=client_id, is_published=True)
               .order_by(LandingPage.created_at.desc()).all())
    cta_letters = (SalesLetter.query.filter_by(client_id=client_id, is_published=True)
                   .order_by(SalesLetter.created_at.desc()).all())
    cta_products = (StripeProduct.query.filter_by(client_id=client_id)
                    .order_by(StripeProduct.created_at.desc()).all())
    return render_template("admin/drafts.html", drafts=all_drafts,
                           edu_stages=edu_stages, guda_items=guda_items,
                           cta_lps=cta_lps, cta_letters=cta_letters,
                           cta_products=cta_products)


@bp.route("/drafts/<int:draft_id>/review", methods=["POST"])
def draft_review(draft_id):
    draft = Draft.query.get_or_404(draft_id)
    if draft.client_id != _client_id():
        abort(403)
    draft.reviewed = not draft.reviewed
    db.session.commit()
    return redirect(url_for("admin.drafts"))


# ──────────────────────────────────────────────
# E3-4: 勝ち型インサイト（自己改善ループ）
# ──────────────────────────────────────────────
# ドラフトの hook_type/format_type/cta_type と、投稿済み予約の実績（インプ）を
# 突き合わせ、型別の「平均インプ×件数」で勝ち型を可視化する。
# 実測ノウハウ「問いかけ＋CTA型は伸び、列挙・CTA無しは失速」を、当該clientの
# 実データで裏取りして次の生成に反映する。データが無い項目は「観測待ち」。
# 捏造しない（インプ未計測の投稿は集計に入れない）。

@bp.route("/insights")
def insights():
    client_id = _client_id()
    from ..services.draft_service import (HOOK_TYPE_LABELS, FORMAT_TYPE_LABELS,
                                          CTA_TYPE_LABELS)

    # 投稿済み（posted）かつインプ計測済み（impressions が NULL でない）の予約投稿を、
    # 紐づくドラフトのタグと突き合わせる。draft_id が無い投稿は集計対象外。
    rows = (db.session.query(ScheduledPost, Draft)
            .join(Draft, ScheduledPost.draft_id == Draft.id)
            .filter(ScheduledPost.client_id == client_id,
                    ScheduledPost.status == "posted",
                    ScheduledPost.impressions.isnot(None))
            .all())

    def _agg(attr, labels):
        buckets = {}
        for sp, dr in rows:
            key = getattr(dr, attr) or ""
            if not key:
                key = "unclassified"
            b = buckets.setdefault(key, {"count": 0, "total_imp": 0})
            b["count"] += 1
            b["total_imp"] += sp.impressions or 0
        ranking = []
        for key, b in buckets.items():
            label = labels.get(key, "未分類" if key == "unclassified" else key)
            ranking.append({
                "key": key, "label": label, "count": b["count"],
                "avg_imp": round(b["total_imp"] / b["count"], 1) if b["count"] else 0,
            })
        ranking.sort(key=lambda r: r["avg_imp"], reverse=True)
        return ranking

    hook_rank = _agg("hook_type", HOOK_TYPE_LABELS)
    format_rank = _agg("format_type", FORMAT_TYPE_LABELS)
    cta_rank = _agg("cta_type", CTA_TYPE_LABELS)

    total_measured = len(rows)
    total_drafts = Draft.query.filter_by(client_id=client_id).count()

    # 「次の生成で使うと良い型」の提示は、実データがある軸だけ（捏造しない）。
    best = {}
    if hook_rank:
        best["hook"] = hook_rank[0]
    if format_rank:
        best["format"] = format_rank[0]
    if cta_rank:
        best["cta"] = cta_rank[0]

    return render_template("admin/insights.html",
                           hook_rank=hook_rank, format_rank=format_rank,
                           cta_rank=cta_rank, total_measured=total_measured,
                           total_drafts=total_drafts, best=best)


# ──────────────────────────────────────────────
# E3-7: 日次バッチ生成（24本・時報型）＋同文重複検出
# ──────────────────────────────────────────────
# ドクトリン: 毎時24本・時間帯×フォーマット配分・全て別内容（同文=スパム判定）。
# ここでは本文を生成→重複検出→ScheduledPost を status="pending"（承認待ち）で積むだけ。
# 実投稿はしない（承認ゲートは E6-5 が担保）。同文（exact）は絶対に積まない。

@bp.route("/batch-generate", methods=["GET", "POST"])
def batch_generate():
    client_id = _client_id()
    profile = ProfileConcept.query.filter_by(client_id=client_id).first_or_404()
    vocab = load_vocab()
    profile_dict = {
        "genre": profile.genre, "who": profile.who,
        "what": profile.what, "how": profile.how,
        "position": profile.position, "achievement": profile.achievement,
    }

    if request.method == "POST":
        # 対象日（既定は当日）。過去日でも承認待ちで積むだけなので安全。
        raw_date = request.form.get("target_date", "").strip()
        try:
            target_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else date.today()
        except ValueError:
            flash("日付の形式が正しくありません（YYYY-MM-DD）。", "warning")
            return redirect(url_for("admin.batch_generate"))

        # Claude強化は任意（キーが無ければ自動でテンプレ）。既定はチェックボックス。
        use_claude = request.form.get("use_claude") == "1"

        plan = batch_service.build_batch_plan()
        items = batch_service.generate_batch(profile_dict, vocab, plan, use_claude=use_claude)
        report = batch_service.detect_duplicates(items)

        # 同文（exact）は絶対に積まない。近似（near）は警告しつつ積む（別内容として通しうる）。
        saved = 0
        for it in items:
            if it["dup_status"] == "exact":
                continue
            scheduled_at = datetime.combine(target_date, time(it["hour"], it["minute"]))
            post = ScheduledPost(
                client_id=client_id,
                platform="x",
                text=it["text"],
                scheduled_at=scheduled_at,
                status=POST_STATUS_PENDING,  # 承認待ち（外部書き込みしない）
                created_by_user_id=current_user.id,
            )
            db.session.add(post)
            saved += 1
        db.session.commit()

        flash(f"{target_date.isoformat()} 分として{saved}件を承認待ち（pending）で積みました。", "success")
        if report["exact"]:
            flash(f"同文（コピペ）を{report['exact']}件検出し、除外しました。同文は投稿しません。", "danger")
        if report["near"]:
            flash(f"酷似（近似重複）が{report['near']}件あります。内容を見直すと安全です。", "warning")
        return redirect(url_for("admin.batch_generate"))

    # GET: 時間帯配分プレビュー
    bands = batch_service.band_summary()
    plan = batch_service.build_batch_plan()
    claude_ok = batch_service.is_available()
    today = date.today()
    return render_template("admin/batch_generate.html",
                           bands=bands, plan=plan, claude_ok=claude_ok,
                           today=today, profile=profile)


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
        "scheduled": ScheduledPost.query.filter_by(client_id=client_id).filter(
            ScheduledPost.status.in_(["pending", "approved"])).count() > 0,
    }
    # SNS API連携・予約投稿は「任意」。プロフィール＋ドラフトが揃えば手動運用（コピー投稿）で
    # 使えるので、これらを満たせばセットアップ完了扱いにする（API連携を強制しない）。
    setup_done = setup_steps["profile"] and setup_steps["drafts"]

    # 今日投稿すべき予定を取得（承認待ち＋承認済みの両方）
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
    today_end   = today_start + timedelta(days=1)
    today_posts = ScheduledPost.query.filter_by(client_id=client_id).filter(
        ScheduledPost.status.in_(["pending", "approved"])).filter(
        ScheduledPost.scheduled_at >= today_start,
        ScheduledPost.scheduled_at < today_end
    ).all()

    # ── 客層フィット可視化（E6-4）─────────────────────────
    # 「リーチ指標（認知の広さ）」と「見込み客/成約指標（収益に近い）」を
    # 明確に分離して渡し、KPIの取り違えを防ぐ。schema変更なし・既存データのみ。
    def _latest(attr):
        return getattr(latest, attr, None) if latest is not None else None

    # 問い合わせ（ContactMessage）は読み取りのみで「相談リード」として集計
    contact_total = ContactMessage.query.filter_by(client_id=client_id).count()
    contact_unread = ContactMessage.query.filter_by(client_id=client_id, is_read=False).count()

    reach_metrics = [
        {"label": "平均インプレッション", "emoji": "👁",
         "value": _latest("avg_impressions"), "unit": "回",
         "hint": "投稿がどれだけ見られたか＝認知の広さ"},
        {"label": "フォロワー増加", "emoji": "👥",
         "value": _latest("followers_delta_per_day"), "unit": "人/日",
         "hint": "新しく届いた人の数"},
        {"label": "エンゲージメント率", "emoji": "❤️",
         "value": _latest("engagement_rate_pct"), "unit": "%",
         "hint": "いいね・リポストなどの反応率"},
        {"label": "投稿数", "emoji": "✍️",
         "value": _latest("posts_per_day"), "unit": "件/日",
         "hint": "リーチを生む発信量"},
    ]
    prospect_metrics = [
        {"label": "リスト/LINE登録", "emoji": "📩",
         "value": _latest("list_signups_per_day"), "unit": "件/日",
         "hint": "見込み客リストの獲得ペース"},
        {"label": "リスト→面談率", "emoji": "🤝",
         "value": _latest("meeting_rate_pct"), "unit": "%",
         "hint": "相談・面談につながった割合"},
        {"label": "面談→成約率", "emoji": "💰",
         "value": _latest("conversion_rate_pct"), "unit": "%",
         "hint": "成約＝収益に直結する最終指標"},
        {"label": "相談リード（問い合わせ）", "emoji": "📨",
         "value": contact_total, "unit": "件",
         "hint": "LP・セールスレターからの問い合わせ累計"
                 + ("（未読 {}件）".format(contact_unread) if contact_unread else "")},
    ]
    fit_warning = ("フォロワー数や表示回数（リーチ指標）だけを見て売上を判断しないでください。"
                   "バズる相手と実際に買う見込み客はズレやすいので、"
                   "相談・成約（見込み客/成約指標）と必ずセットで確認しましょう。")

    # ── 主KPIの付け替え（E3-8）──────────────────────────────
    # 実測: 相互狙いで増やしたフォロワーは "死んだ観客"（到達・相談に繋がらない）。
    # → 主KPIを「成果に近い指標」= プロフクリック / DM相談 / エンゲージ率 に格上げし、
    #   フォロワー数は "補助（vanity＝見栄え）指標" として降格表示する。schema変更なし。
    engagement_val = _latest("engagement_rate_pct")
    followers_delta_val = _latest("followers_delta_per_day")
    list_signups_val = _latest("list_signups_per_day")

    # プロフクリックは現状のスキーマに計測列が無い → 捏造せず「観測待ち」で表示。
    primary_kpis = [
        {"label": "プロフクリック", "emoji": "🔎",
         "value": None, "unit": "回",
         "hint": "プロフィールを見に来た人＝濃い関心。数値連携は観測待ち"},
        {"label": "DM相談（問い合わせ）", "emoji": "📨",
         "value": contact_total, "unit": "件",
         "hint": "実際に相談が来た数＝成果に最も近い指標"
                 + ("（未読 {}件）".format(contact_unread) if contact_unread else "")},
        {"label": "エンゲージメント率", "emoji": "❤️",
         "value": engagement_val, "unit": "%",
         "hint": "届いた人が反応したか＝到達の濃さ"},
    ]
    # フォロワーは vanity（見栄え）指標として降格。
    vanity_metrics = [
        {"label": "フォロワー増加", "emoji": "👥",
         "value": followers_delta_val, "unit": "人/日",
         "hint": "増えても到達・相談に繋がらなければ “死んだ観客”。あくまで参考値"},
    ]

    # ── "死んだ観客" インジケータ ──────────────────────────
    # フォロワーは増えているのに、到達（エンゲージ率）や見込み客（登録・相談）が
    # 伴っていない状態を検出。判定材料が無ければ「観測待ち」（捏造しない）。
    dead_audience = None          # None=観測待ち / False=問題なし / True=注意
    dead_audience_detail = ""
    if followers_delta_val is None:
        dead_audience = None
    elif followers_delta_val <= 0:
        dead_audience = False     # フォロワーが増えていない → この観点の懸念なし
    else:
        # フォロワーは増加中。到達・成果が伴っているか確認する。
        reach_ok = engagement_val is not None and engagement_val >= 1.0
        lead_ok = (list_signups_val is not None and list_signups_val > 0) or contact_total > 0
        if engagement_val is None and list_signups_val is None and contact_total == 0:
            dead_audience = None   # 判定材料が揃っていない → 観測待ち
        elif reach_ok or lead_ok:
            dead_audience = False
        else:
            dead_audience = True
            dead_audience_detail = (
                "直近でフォロワーは増えています（+{:.1f}人/日）が、"
                "エンゲージメント率や登録・相談がほとんど動いていません。"
                "増えたフォロワーが到達・相談に繋がっていない “死んだ観客” の可能性があります。"
                "相互狙いの数集めより、濃い見込み客に届く発信へ切り替えましょう。"
                .format(followers_delta_val))

    return render_template(
        "admin/analytics.html",
        phase=phase, phase_label=PHASE_LABELS[phase],
        diagnostics=diagnostics, notes=notes,
        metric_labels=metric_labels, metric_series=metric_series,
        setup_steps=setup_steps, setup_done=setup_done,
        today_posts=today_posts,
        reach_metrics=reach_metrics, prospect_metrics=prospect_metrics,
        fit_warning=fit_warning, has_metrics=bool(latest),
        primary_kpis=primary_kpis, vanity_metrics=vanity_metrics,
        dead_audience=dead_audience, dead_audience_detail=dead_audience_detail,
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
    names = generate_display_name(profile_dict, base_name=(profile.display_name or ""))
    from flask import jsonify
    return jsonify({"bio": bio, "names": names})


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
# 社会的証明マネージャ（お客様の声 / 実績 / ロゴ）
# ──────────────────────────────────────────────

TESTIMONIAL_KINDS = ("voice", "result", "logo")


@bp.route("/testimonials")
def testimonial_list():
    client_id = _client_id()
    items = (Testimonial.query
             .filter_by(client_id=client_id)
             .order_by(Testimonial.kind, Testimonial.sort_order, Testimonial.id)
             .all())
    return render_template("admin/testimonial_list.html", items=items, client_id=client_id)


@bp.route("/testimonials/new", methods=["GET", "POST"])
def testimonial_new():
    client_id = _client_id()

    if request.method == "POST":
        kind = request.form.get("kind", "voice").strip()
        if kind not in TESTIMONIAL_KINDS:
            kind = "voice"
        author_name = request.form.get("author_name", "").strip()
        author_title = request.form.get("author_title", "").strip()
        quote = request.form.get("quote", "").strip()
        metric_label = request.form.get("metric_label", "").strip()
        metric_value = request.form.get("metric_value", "").strip()
        image_url = request.form.get("image_url", "").strip()
        logo_url = request.form.get("logo_url", "").strip()
        try:
            sort_order = int(request.form.get("sort_order", "0") or 0)
        except ValueError:
            sort_order = 0

        # kind別必須検証
        form = dict(kind=kind, author_name=author_name, author_title=author_title,
                    quote=quote, metric_label=metric_label, metric_value=metric_value,
                    image_url=image_url, logo_url=logo_url, sort_order=sort_order)
        if kind == "voice" and not quote:
            flash("「お客様の声」には引用文（quote）が必須です。", "danger")
            return render_template("admin/testimonial_new.html", form=form, kinds=TESTIMONIAL_KINDS)
        if kind == "result" and not (metric_label and metric_value):
            flash("「実績」には指標ラベルと数値の両方が必須です。", "danger")
            return render_template("admin/testimonial_new.html", form=form, kinds=TESTIMONIAL_KINDS)
        if kind == "logo" and not logo_url:
            flash("「ロゴ」にはロゴURLが必須です。", "danger")
            return render_template("admin/testimonial_new.html", form=form, kinds=TESTIMONIAL_KINDS)

        item = Testimonial(
            client_id=client_id,
            kind=kind,
            author_name=author_name,
            author_title=author_title,
            quote=quote,
            metric_label=metric_label,
            metric_value=metric_value,
            image_url=image_url,
            logo_url=logo_url,
            is_active=True,
            sort_order=sort_order,
        )
        db.session.add(item)
        db.session.commit()
        flash("社会的証明を追加しました。", "success")
        return redirect(url_for("admin.testimonial_list"))

    return render_template("admin/testimonial_new.html", form=None, kinds=TESTIMONIAL_KINDS)


@bp.route("/testimonials/<int:item_id>/toggle", methods=["POST"])
def testimonial_toggle(item_id):
    item = Testimonial.query.get_or_404(item_id)
    if item.client_id != _client_id():
        abort(403)
    item.is_active = not item.is_active
    db.session.commit()
    return redirect(url_for("admin.testimonial_list"))


@bp.route("/testimonials/<int:item_id>/delete", methods=["POST"])
def testimonial_delete(item_id):
    item = Testimonial.query.get_or_404(item_id)
    if item.client_id != _client_id():
        abort(403)
    db.session.delete(item)
    db.session.commit()
    flash("社会的証明を削除しました。", "success")
    return redirect(url_for("admin.testimonial_list"))


@bp.route("/testimonials/<int:item_id>/order", methods=["POST"])
def testimonial_order(item_id):
    item = Testimonial.query.get_or_404(item_id)
    if item.client_id != _client_id():
        abort(403)
    try:
        item.sort_order = int(request.form.get("sort_order", "0") or 0)
    except ValueError:
        item.sort_order = 0
    db.session.commit()
    return redirect(url_for("admin.testimonial_list"))


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
    all_messages = (ContactMessage.query
                    .filter_by(client_id=client_id)
                    .order_by(ContactMessage.created_at.desc())
                    .all())

    # 出所別の集計（導線トラッキング）。空/未設定は "direct" とみなす。
    source_counts = {}
    for m in all_messages:
        key = (m.source or "direct").strip() or "direct"
        source_counts[key] = source_counts.get(key, 0) + 1
    # 件数の多い順に整列
    source_counts = dict(sorted(source_counts.items(),
                                key=lambda kv: kv[1], reverse=True))

    # 出所フィルタ（?src=lp など）。空なら全件。
    active_src = (request.args.get("src", "").strip())
    if active_src:
        messages = [m for m in all_messages
                    if ((m.source or "direct").strip() or "direct") == active_src]
    else:
        messages = all_messages

    unread = sum(1 for m in messages if not m.is_read)
    return render_template("admin/contact_inbox.html",
                           messages=messages,
                           unread=unread,
                           source_counts=source_counts,
                           total=len(all_messages),
                           active_src=active_src)


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


@bp.route("/purchases")
def purchases():
    """E5-1: Stripe Webhook で記録した購入履歴（当該clientの新しい順）。"""
    client_id = _client_id()
    items = (Purchase.query
             .filter_by(client_id=client_id)
             .order_by(Purchase.created_at.desc())
             .all())
    return render_template("admin/purchases.html", purchases=items)


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
             .filter_by(client_id=client_id)
             .filter(ScheduledPost.status.in_(["pending", "approved"]))
             .filter(ScheduledPost.scheduled_at >= datetime.combine(campaign.start_date, datetime.min.time()))
             .filter(ScheduledPost.scheduled_at <= datetime.combine(campaign.end_date, datetime.max.time()))
             .order_by(ScheduledPost.scheduled_at)
             .all())

    pending_count = sum(1 for p in posts if p.status == POST_STATUS_PENDING)
    approved_count = sum(1 for p in posts if p.status == POST_STATUS_APPROVED)

    return render_template("admin/story_campaign_detail.html",
                           campaign=campaign, posts=posts,
                           pending_count=pending_count,
                           approved_count=approved_count)


def _campaign_posts_by_status(campaign, client_id, statuses):
    """当該キャンペーン期間内・当該clientの ScheduledPost を status で絞って返す。"""
    return (ScheduledPost.query
            .filter_by(client_id=client_id)
            .filter(ScheduledPost.status.in_(statuses))
            .filter(ScheduledPost.scheduled_at >= datetime.combine(campaign.start_date, datetime.min.time()))
            .filter(ScheduledPost.scheduled_at <= datetime.combine(campaign.end_date, datetime.max.time()))
            .all())


@bp.route("/story-campaign/<int:campaign_id>/approve-all", methods=["POST"])
def story_campaign_approve_all(campaign_id):
    """一括承認: 当該キャンペーン期間内の pending 予約を approved に遷移。
    承認済みだけが自動投稿の対象になる（実投稿はここでは発生しない）。"""
    client_id = _client_id()
    campaign = StoryCampaign.query.get_or_404(campaign_id)
    if campaign.client_id != client_id:
        abort(403)

    posts = _campaign_posts_by_status(campaign, client_id, [POST_STATUS_PENDING])
    now = datetime.utcnow()
    approved = 0
    for post in posts:
        if not can_transition(post.status, POST_STATUS_APPROVED):
            continue
        post.status = POST_STATUS_APPROVED
        post.approved_at = now
        post.approved_by_user_id = current_user.id
        approved += 1
    db.session.commit()

    if approved:
        flash(f"{approved}件を一括承認しました。予約時刻になったら自動投稿されます。", "success")
    else:
        flash("承認待ちの予約はありませんでした。", "info")
    return redirect(url_for("admin.story_campaign_detail", campaign_id=campaign.id))


@bp.route("/story-campaign/<int:campaign_id>/unapprove-all", methods=["POST"])
def story_campaign_unapprove_all(campaign_id):
    """一括承認取消: 当該キャンペーン期間内の approved 予約を pending に引き戻す。
    投稿済みには手を出さない（POST_STATUS_TRANSITIONS で保証）。"""
    client_id = _client_id()
    campaign = StoryCampaign.query.get_or_404(campaign_id)
    if campaign.client_id != client_id:
        abort(403)

    posts = _campaign_posts_by_status(campaign, client_id, [POST_STATUS_APPROVED])
    reverted = 0
    for post in posts:
        if not can_transition(post.status, POST_STATUS_PENDING):
            continue
        post.status = POST_STATUS_PENDING
        post.approved_at = None
        post.approved_by_user_id = None
        reverted += 1
    db.session.commit()

    if reverted:
        flash(f"{reverted}件の承認を取り消しました。これらは自動投稿されません。", "warning")
    else:
        flash("承認済みの予約はありませんでした。", "info")
    return redirect(url_for("admin.story_campaign_detail", campaign_id=campaign.id))


@bp.route("/story-campaign/<int:campaign_id>/delete", methods=["POST"])
def story_campaign_delete(campaign_id):
    client_id = _client_id()
    campaign = StoryCampaign.query.get_or_404(campaign_id)
    if campaign.client_id != client_id:
        abort(403)

    # 関連する予約投稿も削除（承認待ち＋承認済み。投稿済みは履歴として残す）
    ScheduledPost.query.filter_by(client_id=client_id).filter(
        ScheduledPost.status.in_(["pending", "approved"]),
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


# --- メトリクス取込（ブックマークレット経由） -------------------------------
# 設計: ブックマークレットは X アナリティクス画面で数値を集めて
#       /admin/metrics/import?data=... に運ぶだけ（GET・書き込みなし）。
#       管理者が内容を確認し、通常の CSRF 付き POST で保存する。
#       → 外部オリジンから直接 DB に書き込ませない。

METRIC_IMPORT_FIELDS = [
    ("posts_per_day", "投稿数/日"),
    ("avg_impressions", "平均インプレッション"),
    ("engagement_rate_pct", "エンゲージメント率(%)"),
    ("followers_delta_per_day", "フォロワー増加/日"),
    ("list_signups_per_day", "LINE登録数/日"),
    ("meeting_rate_pct", "リスト→面談率(%)"),
    ("conversion_rate_pct", "面談→成約率(%)"),
]


def _parse_metric_import_data(raw):
    """ブックマークレットが渡す data= を安全にパースしてプリフィル用 dict を返す。

    受理する形式: URLエンコードされた JSON、または base64(JSON)。
    壊れた入力・想定外のキーは黙って捨てる（画面は必ず開ける）。
    """
    prefill = {}
    if not raw:
        return prefill

    import json
    import base64

    payload = None
    for loader in (lambda s: json.loads(s),
                   lambda s: json.loads(base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8"))):
        try:
            payload = loader(raw)
            if isinstance(payload, dict):
                break
            payload = None
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        return prefill

    allowed = {name for name, _ in METRIC_IMPORT_FIELDS}
    for key, value in payload.items():
        if key not in allowed or value in (None, ""):
            continue
        try:
            prefill[key] = float(str(value).replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            continue

    raw_date = payload.get("date")
    if raw_date:
        try:
            prefill["date"] = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    return prefill


@bp.route("/metrics/import", methods=["GET", "POST"])
def metrics_import():
    client_id = _client_id()

    if request.method == "POST":
        raw_date = request.form.get("date", "").strip()
        try:
            entry_date = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else date.today()
        except ValueError:
            flash("日付の形式が正しくありません（YYYY-MM-DD）。", "warning")
            return redirect(url_for("admin.metrics_import"))

        def _f(key):
            v = request.form.get(key, "").strip().replace(",", "").replace("%", "")
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                return None

        values = {name: _f(name) for name, _ in METRIC_IMPORT_FIELDS}
        if all(v is None for v in values.values()):
            flash("取り込む数値がありません。1つ以上入力してください。", "warning")
            return redirect(url_for("admin.metrics_import"))

        m = MetricEntry.query.filter_by(client_id=client_id, date=entry_date).first()
        created = m is None
        if created:
            m = MetricEntry(client_id=client_id, date=entry_date,
                            logged_by_user_id=current_user.id)
            db.session.add(m)
        for name, value in values.items():
            if value is not None:
                setattr(m, name, value)
        db.session.commit()
        flash("{} の指標を{}しました。".format(entry_date.isoformat(),
                                          "取り込み" if created else "更新"), "success")
        return redirect(url_for("admin.analytics"))

    prefill = _parse_metric_import_data(request.args.get("data", ""))
    prefill.setdefault("date", date.today().isoformat())
    recent = (MetricEntry.query.filter_by(client_id=client_id)
              .order_by(MetricEntry.date.desc()).limit(5).all())
    return render_template("admin/metrics_import.html",
                           fields=METRIC_IMPORT_FIELDS,
                           prefill=prefill,
                           has_data=bool(request.args.get("data", "")),
                           recent=recent)
