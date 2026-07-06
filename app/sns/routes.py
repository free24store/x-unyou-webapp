"""
SNS連携・予約投稿管理ブループリント
- /sns/settings        : API認証情報の設定（管理者のみ）
- /sns/schedule        : 予約投稿一覧
- /sns/schedule/new    : 新規予約投稿作成
- /sns/schedule/<id>/cancel : 予約キャンセル
"""
from datetime import datetime
from flask import render_template, redirect, url_for, flash, request, abort, send_from_directory, current_app
from flask_login import login_required, current_user

from . import bp
from ..extensions import db
from ..models import SnsConnection, ScheduledPost, Draft, PLATFORMS, PLATFORM_LABELS
from ..services.video_service import generate_story_video, generate_draft_video

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
            flash(f"{PLATFORM_LABELS[platform]} の認証情報が設定されていません。", "danger")
            return redirect(url_for("sns.settings"))

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

        post = ScheduledPost(
            client_id=client_id,
            draft_id=int(draft_id) if draft_id else None,
            platform=platform,
            text=text,
            media_path=media_path,
            scheduled_at=scheduled_at,
            status="pending",
            created_by_user_id=current_user.id,
        )
        db.session.add(post)
        db.session.commit()
        flash(f"{PLATFORM_LABELS[platform]} への予約投稿を登録しました（{scheduled_at_str}）。", "success")
        return redirect(url_for("sns.schedule_list"))

    # draft_idが指定されていたらテキストを事前入力
    draft_id = request.args.get("draft_id")
    pre_text = ""
    if draft_id:
        d = Draft.query.get(draft_id)
        if d and d.client_id == client_id:
            pre_text = d.text

    from datetime import timedelta
    return render_template("sns/schedule_new.html",
                           connections=connections, drafts=drafts,
                           platform_labels=PLATFORM_LABELS, platforms=PLATFORMS,
                           pre_text=pre_text, pre_draft_id=draft_id,
                           now=datetime.utcnow(), timedelta=timedelta)


@bp.route("/schedule/<int:post_id>/mark-posted", methods=["POST"])
def mark_posted(post_id):
    """手動投稿後に「投稿済み」としてマークする。"""
    post = ScheduledPost.query.get_or_404(post_id)
    if post.client_id != _client_id():
        abort(403)
    post.status = "posted"
    post.post_id = "manual"
    db.session.commit()
    flash("投稿済みにしました。", "success")
    return redirect(url_for("sns.schedule_list"))


@bp.route("/schedule/<int:post_id>/cancel", methods=["POST"])
def schedule_cancel(post_id):
    post = ScheduledPost.query.get_or_404(post_id)
    if post.client_id != _client_id():
        abort(403)
    if post.status == "pending":
        post.status = "cancelled"
        db.session.commit()
        flash("予約投稿をキャンセルしました。", "warning")
    return redirect(url_for("sns.schedule_list"))


@bp.route("/schedule/<int:post_id>/post-now", methods=["POST"])
def post_now(post_id):
    """今すぐ投稿（手動実行）。"""
    post = ScheduledPost.query.get_or_404(post_id)
    if post.client_id != _client_id():
        abort(403)
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


# ---------------------------------------------------------------------------
# Internal: execute a scheduled post
# ---------------------------------------------------------------------------

import os

def _execute_post(post: ScheduledPost):
    from ..models import SnsConnection
    conn = SnsConnection.query.filter_by(
        client_id=post.client_id, platform=post.platform, is_active=True
    ).first()
    if not conn:
        post.status = "failed"
        post.error_msg = "認証情報が見つかりません。SNS設定を確認してください。"
        db.session.commit()
        return

    from ..services.sns_service import post_to_platform
    try:
        post_id = post_to_platform(
            platform=post.platform,
            text=post.text,
            credentials=conn.credentials_json,
            media_path=post.media_path,
        )
        post.status = "posted"
        post.post_id = post_id
        flash(f"{PLATFORM_LABELS.get(post.platform, post.platform)} に投稿しました（ID: {post_id}）。", "success")
    except Exception as e:
        post.status = "failed"
        post.error_msg = str(e)
        flash(f"投稿に失敗しました: {e}", "danger")
    db.session.commit()
