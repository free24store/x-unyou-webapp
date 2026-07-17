"""
SNS連携・予約投稿管理ブループリント
- /sns/settings        : API認証情報の設定（管理者のみ）
- /sns/schedule        : 予約投稿一覧
- /sns/schedule/new    : 新規予約投稿作成
- /sns/schedule/<id>/approve   : 承認（pending → approved）
- /sns/schedule/<id>/unapprove : 承認取消（approved → pending）
- /sns/schedule/<id>/cancel : 予約キャンセル

E6-5: 承認ゲート。自動投稿されるのは status == "approved" のものだけで、
作成直後は "pending"（承認待ち）。承認前に外部へ書き込むことはない。
"""
from datetime import datetime, timedelta
from flask import render_template, redirect, url_for, flash, request, abort, send_from_directory, current_app
from flask_login import login_required, current_user

from . import bp
from ..extensions import db
from ..models import (
    SnsConnection, ScheduledPost, Draft, PLATFORMS, PLATFORM_LABELS,
    POST_STATUS_PENDING, POST_STATUS_APPROVED, POST_STATUS_POSTED,
    POST_STATUS_FAILED, POST_STATUS_CANCELLED, POST_STATUS_LABELS,
    can_transition,
)
from ..services.video_service import generate_story_video, generate_draft_video
from ..services.offer_service import resolve_offer_url, compose_with_cta

VIDEO_DIR_NAME = "videos"


def _client_id():
    return current_user.client_id


@bp.before_request
@login_required
def check_access():
    if not current_user.is_authenticated or current_user.role not in ("admin", "master"):
        abort(403)


# ---------------------------------------------------------------------------
# SNS設定
# ---------------------------------------------------------------------------

CREDENTIAL_FIELDS = {
    "x": [
        ("api_key", "API Key (Consumer Key)"),
        ("api_secret", "API Secret (Consumer Secret)"),
        ("access_token", "Access Token"),
        ("access_token_secret", "Access Token Secret"),
    ],
    "instagram": [
        ("access_token", "Page Access Token（長期トークン）"),
        ("instagram_business_account_id", "Instagram Business Account ID"),
    ],
    "tiktok": [
        ("access_token", "Access Token（OAuth2）"),
    ],
    "youtube": [
        ("client_id", "OAuth2 Client ID"),
        ("client_secret", "OAuth2 Client Secret"),
        ("access_token", "Access Token"),
        ("refresh_token", "Refresh Token"),
    ],
}


@bp.route("/settings")
def settings():
    client_id = _client_id()
    connections = {c.platform: c for c in
                   SnsConnection.query.filter_by(client_id=client_id).all()}
    return render_template("sns/settings.html",
                           connections=connections,
                           platforms=PLATFORMS,
                           platform_labels=PLATFORM_LABELS,
                           credential_fields=CREDENTIAL_FIELDS)


@bp.route("/settings/<platform>", methods=["GET", "POST"])
def settings_platform(platform):
    if platform not in PLATFORMS:
        abort(404)
    client_id = _client_id()
    conn = SnsConnection.query.filter_by(client_id=client_id, platform=platform).first()
    fields = CREDENTIAL_FIELDS.get(platform, [])

    if request.method == "POST":
        creds = {}
        for key, _ in fields:
            val = request.form.get(key, "").strip()
            if val:
                creds[key] = val
            elif conn and conn.credentials_json.get(key):
                creds[key] = conn.credentials_json[key]  # 既存値を維持

        if not conn:
            conn = SnsConnection(client_id=client_id, platform=platform)
            db.session.add(conn)
        conn.credentials_json = creds
        conn.is_active = True
        conn.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f"{PLATFORM_LABELS[platform]} の認証情報を保存しました。", "success")
        return redirect(url_for("sns.settings"))

    return render_template("sns/settings_platform.html",
                           platform=platform,
                           platform_label=PLATFORM_LABELS[platform],
                           conn=conn, fields=fields)


@bp.route("/settings/<platform>/disconnect", methods=["POST"])
def disconnect_platform(platform):
    client_id = _client_id()
    conn = SnsConnection.query.filter_by(client_id=client_id, platform=platform).first()
    if conn:
        conn.is_active = False
        db.session.commit()
        flash(f"{PLATFORM_LABELS[platform]} の連携を解除しました。", "warning")
    return redirect(url_for("sns.settings"))


# ---------------------------------------------------------------------------
# 予約投稿
# ---------------------------------------------------------------------------

@bp.route("/schedule")
def schedule_list():
    client_id = _client_id()
    posts = (ScheduledPost.query
             .filter_by(client_id=client_id)
             .order_by(ScheduledPost.scheduled_at)
             .all())
    connections = {c.platform: c for c in
                   SnsConnection.query.filter_by(client_id=client_id, is_active=True).all()}
    return render_template("sns/schedule_list.html",
                           posts=posts, connections=connections,
                           platform_labels=PLATFORM_LABELS,
                           status_labels=POST_STATUS_LABELS,
                           min_interval=min_post_interval_minutes(),
                           pending_count=sum(1 for p in posts if p.status == POST_STATUS_PENDING),
                           now=datetime.utcnow())


@bp.route("/schedule/new", methods=["GET", "POST"])
def schedule_new():
    client_id = _client_id()
    connections = {c.platform: c for c in
                   SnsConnection.query.filter_by(client_id=client_id, is_active=True).all()}
    drafts = (Draft.query.filter_by(client_id=client_id)
              .order_by(Draft.generated_at.desc()).limit(50).all())

    if request.method == "POST":
        platform = request.form.get("platform")
        text = request.form.get("text", "").strip()
        draft_id = request.form.get("draft_id") or None
        scheduled_at_str = request.form.get("scheduled_at", "").strip()
        use_video = request.form.get("use_video") == "1"
        # E6-1: CTA（オファー導線）
        cta_label = request.form.get("cta_label", "").strip()
        cta_raw_url = request.form.get("cta_url", "").strip()
        offer_lp_id = request.form.get("offer_lp_id") or None

        if platform not in PLATFORMS:
            flash("プラットフォームを選択してください。", "danger")
            return render_template("sns/schedule_new.html",
                                   connections=connections, drafts=drafts,
                                   platform_labels=PLATFORM_LABELS, platforms=PLATFORMS)
        if not text:
            flash("投稿テキストを入力してください。", "danger")
            return render_template("sns/schedule_new.html",
                                   connections=connections, drafts=drafts,
                                   platform_labels=PLATFORM_LABELS, platforms=PLATFORMS)
        if not scheduled_at_str:
            flash("予約日時を入力してください。", "danger")
            return render_template("sns/schedule_new.html",
                                   connections=connections, drafts=drafts,
                                   platform_labels=PLATFORM_LABELS, platforms=PLATFORMS)
        if platform not in connections:
            # API未接続でも手動投稿モードで予約を許可（コピー→手動投稿→「投稿済み」で完了）
            flash(f"{PLATFORM_LABELS[platform]} のAPI連携が未設定です。手動でXに投稿し「投稿済み」にしてください。", "warning")

        scheduled_at = datetime.strptime(scheduled_at_str, "%Y-%m-%dT%H:%M")
        media_path = None

        # 動画生成
        if use_video and draft_id:
            draft = Draft.query.get(draft_id)
            if draft and draft.client_id == client_id:
                if draft.video_path and os.path.exists(draft.video_path):
                    media_path = draft.video_path
                else:
                    from ..models import ProfileConcept
                    profile = ProfileConcept.query.filter_by(client_id=client_id).first()
                    profile_dict = {
                        "genre": profile.genre if profile else "",
                        "display_name": profile.display_name if profile else "",
                        "position": profile.position if profile else "",
                    }
                    video_dir = os.path.join(current_app.instance_path, VIDEO_DIR_NAME)
                    media_path = generate_draft_video(draft.text, profile_dict, video_dir)
                    draft.video_path = media_path
                    db.session.commit()

        # E6-1: CTAリンク先を解決し、本文末尾にオファー導線を織り込む
        resolved_cta_url = resolve_offer_url(client_id, offer_lp_id, cta_raw_url)
        text = compose_with_cta(text, cta_label, resolved_cta_url)

        post = ScheduledPost(
            client_id=client_id,
            draft_id=int(draft_id) if draft_id else None,
            platform=platform,
            text=text,
            media_path=media_path,
            scheduled_at=scheduled_at,
            status=POST_STATUS_PENDING,  # E6-5: 既定は承認待ち。承認しないと投稿されない
            cta_label=cta_label,
            cta_url=resolved_cta_url,
            offer_lp_id=int(offer_lp_id) if offer_lp_id else None,
            created_by_user_id=current_user.id,
        )
        db.session.add(post)
        db.session.commit()
        flash(f"{PLATFORM_LABELS[platform]} への予約投稿を「承認待ち」で登録しました（{scheduled_at_str}）。"
              "一覧で「承認」するまで投稿されません。", "success")
        return redirect(url_for("sns.schedule_list"))

    # draft_idが指定されていたらテキストを事前入力
    draft_id = request.args.get("draft_id")
    pre_text = ""
    pre_cta_label = ""
    pre_cta_url = ""
    pre_offer_lp_id = None
    if draft_id:
        d = Draft.query.get(draft_id)
        if d and d.client_id == client_id:
            pre_text = d.text
            # E6-1: DraftのCTAを引き継ぐ
            pre_cta_label = d.cta_label or ""
            pre_cta_url = d.cta_url or ""
            pre_offer_lp_id = d.offer_lp_id

    # E6-1: CTA（オファー導線）候補
    from ..models import LandingPage, SalesLetter, StripeProduct
    cta_lps = (LandingPage.query.filter_by(client_id=client_id, is_published=True)
               .order_by(LandingPage.created_at.desc()).all())
    cta_letters = (SalesLetter.query.filter_by(client_id=client_id, is_published=True)
                   .order_by(SalesLetter.created_at.desc()).all())
    cta_products = (StripeProduct.query.filter_by(client_id=client_id)
                    .order_by(StripeProduct.created_at.desc()).all())

    from datetime import timedelta
    return render_template("sns/schedule_new.html",
                           connections=connections, drafts=drafts,
                           platform_labels=PLATFORM_LABELS, platforms=PLATFORMS,
                           pre_text=pre_text, pre_draft_id=draft_id,
                           pre_cta_label=pre_cta_label, pre_cta_url=pre_cta_url,
                           pre_offer_lp_id=pre_offer_lp_id,
                           cta_lps=cta_lps, cta_letters=cta_letters,
                           cta_products=cta_products,
                           now=datetime.utcnow(), timedelta=timedelta)


def _owned_post_or_403(post_id):
    """テナント整合を確認して ScheduledPost を返す。"""
    post = ScheduledPost.query.get_or_404(post_id)
    if post.client_id != _client_id():
        abort(403)
    return post


@bp.route("/schedule/<int:post_id>/approve", methods=["POST"])
def schedule_approve(post_id):
    """承認: pending → approved。ここを通った投稿だけが自動投稿の対象になる。"""
    post = _owned_post_or_403(post_id)
    if not can_transition(post.status, POST_STATUS_APPROVED):
        flash(f"この投稿は承認できません（現在: {POST_STATUS_LABELS.get(post.status, post.status)}）。", "warning")
        return redirect(url_for("sns.schedule_list"))
    post.status = POST_STATUS_APPROVED
    post.approved_at = datetime.utcnow()
    post.approved_by_user_id = current_user.id
    db.session.commit()
    flash("承認しました。予約時刻になったら自動投稿されます。", "success")
    return redirect(url_for("sns.schedule_list"))


@bp.route("/schedule/<int:post_id>/unapprove", methods=["POST"])
def schedule_unapprove(post_id):
    """承認取消: approved → pending。投稿前ならいつでも引き戻せる。"""
    post = _owned_post_or_403(post_id)
    if not can_transition(post.status, POST_STATUS_PENDING):
        flash(f"この投稿は承認取消できません（現在: {POST_STATUS_LABELS.get(post.status, post.status)}）。", "warning")
        return redirect(url_for("sns.schedule_list"))
    post.status = POST_STATUS_PENDING
    post.approved_at = None
    post.approved_by_user_id = None
    db.session.commit()
    flash("承認を取り消しました。この投稿は自動投稿されません。", "warning")
    return redirect(url_for("sns.schedule_list"))


@bp.route("/schedule/<int:post_id>/mark-posted", methods=["POST"])
def mark_posted(post_id):
    """手動投稿後に「投稿済み」としてマークする（外部書き込みは人間が行う）。"""
    post = _owned_post_or_403(post_id)
    if not can_transition(post.status, POST_STATUS_POSTED):
        flash(f"この投稿は投稿済みにできません（現在: {POST_STATUS_LABELS.get(post.status, post.status)}）。", "warning")
        return redirect(url_for("sns.schedule_list"))
    post.status = POST_STATUS_POSTED
    post.post_id = "manual"
    post.posted_at = datetime.utcnow()  # 手動投稿も人間的ペースの基準に含める
    db.session.commit()
    flash("投稿済みにしました。", "success")
    return redirect(url_for("sns.schedule_list"))


@bp.route("/schedule/<int:post_id>/cancel", methods=["POST"])
def schedule_cancel(post_id):
    post = _owned_post_or_403(post_id)
    if not can_transition(post.status, POST_STATUS_CANCELLED):
        flash(f"この投稿はキャンセルできません（現在: {POST_STATUS_LABELS.get(post.status, post.status)}）。", "warning")
        return redirect(url_for("sns.schedule_list"))
    post.status = POST_STATUS_CANCELLED
    post.approved_at = None
    post.approved_by_user_id = None
    db.session.commit()
    flash("予約投稿をキャンセルしました。", "warning")
    return redirect(url_for("sns.schedule_list"))


@bp.route("/schedule/<int:post_id>/post-now", methods=["POST"])
def post_now(post_id):
    """今すぐ投稿（手動実行）。承認済みのみ実行できる（_execute_post 側でも再確認）。"""
    post = _owned_post_or_403(post_id)
    _execute_post(post)
    return redirect(url_for("sns.schedule_list"))


@bp.route("/drafts/<int:draft_id>/generate-video", methods=["POST"])
def generate_video(draft_id):
    """指定ドラフトからショート動画を生成して保存する。"""
    client_id = _client_id()
    draft = Draft.query.get_or_404(draft_id)
    if draft.client_id != client_id:
        abort(403)

    from ..models import ProfileConcept
    profile = ProfileConcept.query.filter_by(client_id=client_id).first()
    profile_dict = {
        "genre": profile.genre if profile else "",
        "display_name": profile.display_name if profile else "",
        "position": profile.position if profile else "",
    }

    video_dir = os.path.join(current_app.instance_path, VIDEO_DIR_NAME)
    try:
        if draft.education_name == "ストーリー型運用":
            # ストーリー型ドラフトの起承転結を再抽出
            parts = draft.text.split("\n")
            story = {
                "ki": parts[0] if len(parts) > 0 else draft.text,
                "sho": parts[1] if len(parts) > 1 else "",
                "ten": parts[2] if len(parts) > 2 else "",
                "ketsu": parts[-1] if len(parts) > 3 else "",
            }
            video_path = generate_story_video(story, profile_dict, video_dir)
        else:
            video_path = generate_draft_video(draft.text, profile_dict, video_dir)

        draft.video_path = video_path
        db.session.commit()
        flash(f"動画を生成しました: {os.path.basename(video_path)}", "success")
    except Exception as e:
        flash(f"動画生成に失敗しました: {e}", "danger")

    return redirect(url_for("admin.drafts"))


@bp.route("/video/<path:filename>")
def serve_video(filename):
    """生成した動画ファイルをダウンロード提供する。"""
    video_dir = os.path.join(current_app.instance_path, VIDEO_DIR_NAME)
    return send_from_directory(video_dir, filename, as_attachment=True)


@bp.route("/video-studio")
def video_studio():
    """ブラウザ側（Canvas + MediaRecorder）でスライド動画を生成する画面。

    サーバはページを返すだけ。動画のレンダリングは全てブラウザ内で完結するため
    APIキー不要・サーバ負荷ゼロで動作する（テンプレートファースト）。
    """
    return render_template("sns/video_studio.html")


# ---------------------------------------------------------------------------
# Internal: 承認ゲート＋人間的ペース（E6-5）
# ---------------------------------------------------------------------------

import os

# 同一クライアントの前回投稿から空ける最小間隔（分）。人間的ペースの下限。
DEFAULT_MIN_POST_INTERVAL_MINUTES = 10


def min_post_interval_minutes():
    """最小投稿間隔（分）。環境変数で運用調整できるが、既定は必ず10分。"""
    raw = os.environ.get("SCHEDULER_MIN_POST_INTERVAL_MINUTES", "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_POST_INTERVAL_MINUTES
    return val if val >= 0 else DEFAULT_MIN_POST_INTERVAL_MINUTES


def last_posted_at(client_id):
    """そのクライアントが最後に自動/手動投稿した時刻（無ければ None）。"""
    from sqlalchemy import func
    return db.session.query(func.max(ScheduledPost.posted_at)).filter(
        ScheduledPost.client_id == client_id,
        ScheduledPost.status == POST_STATUS_POSTED,
    ).scalar()


def select_due_post(now: datetime):
    """このサイクルで投稿してよい予約投稿を **最大1件** 返す（無ければ None）。

    安全ゲート:
      1. status が approved のものだけが対象（draft / pending は絶対に対象外）
      2. scheduled_at が到来済みのものだけ
      3. 同一クライアントの前回投稿から min_post_interval_minutes() 以上経過
      4. 1回の実行で1件だけ（＝バースト不可）

    予約が溜まっていても1分1件＋間隔制限で自然にペース配分される。
    """
    due = (ScheduledPost.query
           .filter(ScheduledPost.status == POST_STATUS_APPROVED,
                   ScheduledPost.scheduled_at <= now)
           .order_by(ScheduledPost.scheduled_at)
           .all())

    interval = timedelta(minutes=min_post_interval_minutes())
    checked = {}
    for post in due:
        if post.client_id not in checked:
            checked[post.client_id] = last_posted_at(post.client_id)
        last = checked[post.client_id]
        if last is not None and (now - last) < interval:
            continue  # クールダウン中のクライアントは次サイクルへ回す
        return post
    return None


# ---------------------------------------------------------------------------
# Internal: execute a scheduled post
# ---------------------------------------------------------------------------

def _execute_post(post: ScheduledPost):
    from ..models import SnsConnection

    # 承認ゲート（多重防御）: 承認済み以外は外部書き込みを一切しない。
    # scheduler / post_now のどちらから来ても、ここで必ず止まる。
    if post.status != POST_STATUS_APPROVED:
        flash(f"未承認の投稿は送信できません（現在: {POST_STATUS_LABELS.get(post.status, post.status)}）。"
              "承認してから実行してください。", "warning")
        return

    conn = SnsConnection.query.filter_by(
        client_id=post.client_id, platform=post.platform, is_active=True
    ).first()
    if not conn:
        # API未接続 → 自動投稿をスキップ（approved のまま残す。手動投稿で対応）
        return

    from ..services.sns_service import post_to_platform
    try:
        post_id = post_to_platform(
            platform=post.platform,
            text=post.text,
            credentials=conn.credentials_json,
            media_path=post.media_path,
        )
        post.status = POST_STATUS_POSTED
        post.post_id = post_id
        post.posted_at = datetime.utcnow()  # 次回の最小間隔判定の基準になる
        flash(f"{PLATFORM_LABELS.get(post.platform, post.platform)} に投稿しました（ID: {post_id}）。", "success")
    except Exception as e:
        post.status = POST_STATUS_FAILED
        post.error_msg = str(e)
        flash(f"投稿に失敗しました: {e}", "danger")
    db.session.commit()
