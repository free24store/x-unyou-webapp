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
    POST_STATUS_FAILED, POST_STATUS_CANCELLED, POST_STATUS_DELETED,
    POST_STATUS_LABELS, can_transition,
    EngagementItem,
    ENGAGE_STATUS_DRAFT, ENGAGE_STATUS_PENDING, ENGAGE_STATUS_APPROVED,
    ENGAGE_STATUS_SENT, ENGAGE_STATUS_EXPIRED,
    ENGAGE_STATUS_LABELS, can_engage_transition,
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


# ---------------------------------------------------------------------------
# E3-6: 低インプ投稿クリーンアップ（承認ゲート付き・実削除は不可逆）
#
# 投稿後に伸びなかった（インプ低）ツイートを、人が選んで承認した分だけ削除する。
# 無差別・自動の一括削除は絶対にしない。実削除は X API(tweepy)の認証が
# 揃っているときだけ実施し、無ければ手動削除リンクを出す（テンプレファースト）。
# ---------------------------------------------------------------------------

# 検出の既定値（画面のフォームで調整できるが、既定はここ）。
DEFAULT_CLEANUP_MIN_HOURS = 3       # 投稿後この時間を過ぎた投稿だけを候補にする
DEFAULT_CLEANUP_IMP_THRESHOLD = 10  # インプがこの値未満なら「低インプ」


def _is_tweet_id(post_id):
    """実在するツイートIDらしいか（手動投稿の "manual" 等を除外）。"""
    return bool(post_id) and str(post_id).isdigit()


def _extract_post_id(raw):
    """ツイートURL or 数値文字列から post_id を取り出す。取れなければ None。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return raw
    # https://x.com/<user>/status/1234567890  や末尾クエリ付きにも対応
    import re
    m = re.search(r"status(?:es)?/(\d+)", raw)
    return m.group(1) if m else None


def _tweet_manual_url(post_id):
    """手動削除用のツイートURL（ユーザー名不明でも i/web/status で開ける）。"""
    return f"https://x.com/i/web/status/{post_id}"


def x_delete_available(client_id):
    """このクライアントで X API による実削除が可能か。

    条件（すべて満たすときのみ True）:
      1. tweepy がインストール済み（Render軽量化のため遅延import＋try/except）
      2. 有効な X 連携があり、4種の認証情報が揃っている
    False のときは実削除せず、手動削除リンクに倒す。
    """
    try:
        import tweepy  # noqa: F401
    except Exception:
        return False
    conn = SnsConnection.query.filter_by(
        client_id=client_id, platform="x", is_active=True
    ).first()
    if not conn:
        return False
    creds = conn.credentials_json or {}
    required = ("api_key", "api_secret", "access_token", "access_token_secret")
    return all(creds.get(k) for k in required)


def _delete_x_tweet(post_id, credentials):
    """tweepy で実ツイートを削除する。呼び出し側で認証の有無を必ず確認すること。"""
    import tweepy
    client = tweepy.Client(
        consumer_key=credentials["api_key"],
        consumer_secret=credentials["api_secret"],
        access_token=credentials["access_token"],
        access_token_secret=credentials["access_token_secret"],
    )
    client.delete_tweet(post_id)


def find_low_impression_candidates(client_id, hours, threshold, now):
    """低インプ削除候補を返す（検出のみ・削除はしない）。

    候補の条件（すべて満たすもの）:
      - status == posted（投稿済み。削除済み/未投稿は対象外）
      - post_id が実在するツイートID（"manual" 等は除外）
      - posted_at が hours 時間以上前
      - impressions が計測済み（NULL は対象外＝安全側）かつ threshold 未満
    """
    cutoff = now - timedelta(hours=hours)
    posts = (ScheduledPost.query
             .filter(ScheduledPost.client_id == client_id,
                     ScheduledPost.status == POST_STATUS_POSTED,
                     ScheduledPost.platform == "x",
                     ScheduledPost.posted_at.isnot(None),
                     ScheduledPost.posted_at <= cutoff,
                     ScheduledPost.impressions.isnot(None),
                     ScheduledPost.impressions < threshold)
             .order_by(ScheduledPost.impressions.asc(),
                       ScheduledPost.posted_at.asc())
             .all())
    return [p for p in posts if _is_tweet_id(p.post_id)]


def _cleanup_params():
    """フォーム/クエリから hours・threshold を安全に取り出す（既定にフォールバック）。"""
    def _int(name, default, lo, hi):
        try:
            val = int(request.values.get(name, ""))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, val))
    hours = _int("hours", DEFAULT_CLEANUP_MIN_HOURS, 0, 24 * 30)
    threshold = _int("threshold", DEFAULT_CLEANUP_IMP_THRESHOLD, 0, 10_000_000)
    return hours, threshold


@bp.route("/cleanup")
def cleanup():
    """低インプ投稿クリーンアップ画面（検出＋インプ記録＋承認削除の入口）。"""
    client_id = _client_id()
    hours, threshold = _cleanup_params()
    now = datetime.utcnow()

    # インプ記録用: X の投稿済み投稿（新しい順）。ここに手入力でインプを記録する。
    posted = (ScheduledPost.query
              .filter(ScheduledPost.client_id == client_id,
                      ScheduledPost.status == POST_STATUS_POSTED,
                      ScheduledPost.platform == "x")
              .order_by(ScheduledPost.posted_at.desc())
              .limit(100).all())

    candidates = find_low_impression_candidates(client_id, hours, threshold, now)

    return render_template("sns/cleanup.html",
                           posted=posted,
                           candidates=candidates,
                           hours=hours,
                           threshold=threshold,
                           default_hours=DEFAULT_CLEANUP_MIN_HOURS,
                           default_threshold=DEFAULT_CLEANUP_IMP_THRESHOLD,
                           api_delete_available=x_delete_available(client_id),
                           is_tweet_id=_is_tweet_id,
                           tweet_url=_tweet_manual_url,
                           now=now)


@bp.route("/cleanup/impressions", methods=["POST"])
def cleanup_record_impressions():
    """インプを手入力で記録する。

    - 行内フォーム: post_pk（ScheduledPost.id）で対象を特定
    - 紐付けフォーム: url_or_id（ツイートURL or post_id）で posted 投稿を検索
    どちらも impressions を必須とする。承認ゲートには関係しない（計測値の記録のみ）。
    """
    client_id = _client_id()
    hours, threshold = _cleanup_params()

    imp_raw = request.form.get("impressions", "").strip()
    try:
        impressions = int(imp_raw)
        if impressions < 0:
            raise ValueError
    except ValueError:
        flash("インプレッション数は0以上の整数で入力してください。", "danger")
        return redirect(url_for("sns.cleanup", hours=hours, threshold=threshold))

    post = None
    post_pk = request.form.get("post_pk")
    if post_pk:
        post = ScheduledPost.query.get(post_pk)
    else:
        pid = _extract_post_id(request.form.get("url_or_id"))
        if pid:
            post = (ScheduledPost.query
                    .filter_by(client_id=client_id, post_id=pid,
                               status=POST_STATUS_POSTED)
                    .first())

    if post is None or post.client_id != client_id:
        flash("対象の投稿が見つかりませんでした（ツイートURL/IDをご確認ください）。", "warning")
        return redirect(url_for("sns.cleanup", hours=hours, threshold=threshold))
    if post.status != POST_STATUS_POSTED:
        flash("投稿済みの投稿にのみインプを記録できます。", "warning")
        return redirect(url_for("sns.cleanup", hours=hours, threshold=threshold))

    post.impressions = impressions
    post.imp_checked_at = datetime.utcnow()
    db.session.commit()
    flash(f"インプレッションを記録しました（{impressions}）。", "success")
    return redirect(url_for("sns.cleanup", hours=hours, threshold=threshold))


@bp.route("/cleanup/delete", methods=["POST"])
def cleanup_delete():
    """承認付き削除。チェックした投稿だけを対象にする（無差別一括はしない）。

    - X API の認証が揃っていれば tweepy で実削除し status=deleted に更新。
    - 揃っていなければ実削除せず、手動削除リンクを表示（テンプレファースト）。
    人が選び・確認したものだけを処理する。承認前に外部書き込みしない。
    """
    client_id = _client_id()
    hours, threshold = _cleanup_params()
    selected = request.form.getlist("post_ids")
    if not selected:
        flash("削除する投稿が選択されていません。", "warning")
        return redirect(url_for("sns.cleanup", hours=hours, threshold=threshold))

    # 選択された投稿を、テナント整合＋状態＋実IDで厳格に絞る。
    posts = []
    for pk in selected:
        p = ScheduledPost.query.get(pk)
        if (p is None or p.client_id != client_id
                or p.status != POST_STATUS_POSTED or not _is_tweet_id(p.post_id)):
            continue
        posts.append(p)

    if not posts:
        flash("削除対象として有効な投稿がありませんでした。", "warning")
        return redirect(url_for("sns.cleanup", hours=hours, threshold=threshold))

    if not x_delete_available(client_id):
        # 実削除はしない。手動削除リンクを提示する（キー無しで tweepy を呼ばない）。
        manual = [{"post": p, "url": _tweet_manual_url(p.post_id)} for p in posts]
        flash(f"X API未接続のため自動削除は行いませんでした。下のリンクから手動で削除し、"
              f"「削除済みにする」を押してください（{len(manual)}件）。", "warning")
        return render_template("sns/cleanup.html",
                               posted=[], candidates=[],
                               manual_delete=manual,
                               hours=hours, threshold=threshold,
                               default_hours=DEFAULT_CLEANUP_MIN_HOURS,
                               default_threshold=DEFAULT_CLEANUP_IMP_THRESHOLD,
                               api_delete_available=False,
                               is_tweet_id=_is_tweet_id,
                               tweet_url=_tweet_manual_url,
                               now=datetime.utcnow())

    # ここに来るのは認証が揃っているときのみ。tweepy で実削除する。
    conn = SnsConnection.query.filter_by(
        client_id=client_id, platform="x", is_active=True
    ).first()
    creds = conn.credentials_json or {}
    deleted, failed = 0, 0
    for p in posts:
        if not can_transition(p.status, POST_STATUS_DELETED):
            continue
        try:
            _delete_x_tweet(p.post_id, creds)
            p.status = POST_STATUS_DELETED
            deleted += 1
        except Exception as e:
            p.error_msg = f"削除失敗: {e}"
            failed += 1
    db.session.commit()

    if deleted:
        flash(f"{deleted}件のツイートを削除しました。", "success")
    if failed:
        flash(f"{failed}件は削除に失敗しました（詳細はエラーを確認してください）。", "danger")
    return redirect(url_for("sns.cleanup", hours=hours, threshold=threshold))


@bp.route("/cleanup/<int:post_id>/mark-deleted", methods=["POST"])
def cleanup_mark_deleted(post_id):
    """手動でXから削除した投稿を「削除済み」にマークする（外部書き込みなし）。"""
    post = _owned_post_or_403(post_id)
    if not can_transition(post.status, POST_STATUS_DELETED):
        flash(f"この投稿は削除済みにできません（現在: {POST_STATUS_LABELS.get(post.status, post.status)}）。", "warning")
        return redirect(url_for("sns.cleanup"))
    post.status = POST_STATUS_DELETED
    db.session.commit()
    flash("削除済みにしました。", "success")
    return redirect(url_for("sns.cleanup"))


# ---------------------------------------------------------------------------
# E3-2: エンゲージメント・キュー（鮮度順・失効）
#
# 返信は「いいねの約13.5倍・会話成立で最大75倍」効く一方、リプ下書きは
# 鮮度商品。貯めてから承認すると対象ツイートが古びて失敗する（24h超で陳腐化）。
# → 鮮度順（対象ツイートの投稿時刻 ＞ 下書き作成時刻）に承認提示し、古い
#   draft / pending は「鮮度切れ」として失効/差し替えを促す。
#
# 承認ゲート: draft → pending → approved → sent。どの状態でも自動で X へ
# 書き込みはしない。approved は target_url への「Xで返信」リンクで手動送信に倒す。
# ---------------------------------------------------------------------------

# draft / pending がこの時間を過ぎたら「鮮度切れ」とみなす（既定24h）。
# 環境変数で運用調整できるが、既定は必ず24時間。
DEFAULT_ENGAGE_FRESH_HOURS = 24


def engage_fresh_hours():
    """リプ下書きの鮮度上限（時間）。既定24h。"""
    raw = os.environ.get("ENGAGE_FRESH_HOURS", "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_ENGAGE_FRESH_HOURS
    return val if val > 0 else DEFAULT_ENGAGE_FRESH_HOURS


def _is_engage_stale(item, now, fresh_hours):
    """draft / pending が鮮度上限を過ぎているか（＝陳腐化して差し替え推奨）。

    approved / sent / expired は判定対象外（承認済みは既に手を打つ段階、
    失効/送信済みは終端）。基準は created_at（下書きを作ってからの経過）。
    """
    if item.status not in (ENGAGE_STATUS_DRAFT, ENGAGE_STATUS_PENDING):
        return False
    base = item.created_at or now
    return (now - base) > timedelta(hours=fresh_hours)


def _owned_engage_or_403(item_id):
    """テナント整合を確認して EngagementItem を返す。"""
    item = EngagementItem.query.get_or_404(item_id)
    if item.client_id != _client_id():
        abort(403)
    return item


def _engage_tweet_reply_url(target_url):
    """対象ツイートを開くURL（手動で「返信」するための導線）。実送信はしない。"""
    return (target_url or "").strip()


@bp.route("/engage")
def engage_queue():
    """エンゲージ・キュー一覧（鮮度順）。

    並び順: 対象ツイートの投稿時刻（target_posted_at）が新しい順。無ければ
    下書き作成時刻（created_at）で代替（SQL の COALESCE で鮮度順に統一）。
    終端（sent / expired）は下に沈める。
    """
    from sqlalchemy import func, case

    client_id = _client_id()
    now = datetime.utcnow()
    fresh_hours = engage_fresh_hours()

    freshness = func.coalesce(EngagementItem.target_posted_at, EngagementItem.created_at)
    # 対応中（draft/pending/approved）を上に、終端（sent/expired）を下に。
    terminal_rank = case(
        (EngagementItem.status.in_((ENGAGE_STATUS_SENT, ENGAGE_STATUS_EXPIRED)), 1),
        else_=0,
    )
    items = (EngagementItem.query
             .filter_by(client_id=client_id)
             .order_by(terminal_rank.asc(), freshness.desc())
             .all())

    active = [i for i in items if i.status not in (ENGAGE_STATUS_SENT, ENGAGE_STATUS_EXPIRED)]
    stale_count = sum(1 for i in active if _is_engage_stale(i, now, fresh_hours))

    return render_template("sns/engage_queue.html",
                           items=items,
                           now=now,
                           fresh_hours=fresh_hours,
                           is_stale=lambda i: _is_engage_stale(i, now, fresh_hours),
                           reply_url=_engage_tweet_reply_url,
                           status_labels=ENGAGE_STATUS_LABELS,
                           pending_count=sum(1 for i in items if i.status == ENGAGE_STATUS_PENDING),
                           stale_count=stale_count)


@bp.route("/engage/new", methods=["POST"])
def engage_new():
    """リプ下書きを追加する（既定 draft）。実送信はしない。"""
    client_id = _client_id()
    target_url = request.form.get("target_url", "").strip()
    target_author = request.form.get("target_author", "").strip()
    reply_text = request.form.get("reply_text", "").strip()
    posted_at_str = request.form.get("target_posted_at", "").strip()

    if not target_url:
        flash("返信先ツイートのURLを入力してください。", "danger")
        return redirect(url_for("sns.engage_queue"))

    target_posted_at = None
    if posted_at_str:
        try:
            target_posted_at = datetime.strptime(posted_at_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("対象ツイートの投稿時刻の形式が不正です（無視して登録しました）。", "warning")

    item = EngagementItem(
        client_id=client_id,
        target_url=target_url,
        target_author=target_author,
        target_posted_at=target_posted_at,
        reply_text=reply_text,
        status=ENGAGE_STATUS_DRAFT,
        created_by_user_id=current_user.id,
    )
    db.session.add(item)
    db.session.commit()
    flash("リプ下書きを追加しました。鮮度が高いうちに承認・返信しましょう。", "success")
    return redirect(url_for("sns.engage_queue"))


@bp.route("/engage/<int:item_id>/edit", methods=["POST"])
def engage_edit(item_id):
    """下書き内容を編集する（draft / pending / expired のみ。approved/sent は不可）。"""
    item = _owned_engage_or_403(item_id)
    if item.status in (ENGAGE_STATUS_APPROVED, ENGAGE_STATUS_SENT):
        flash("承認済み・送信済みの項目は編集できません。", "warning")
        return redirect(url_for("sns.engage_queue"))

    target_url = request.form.get("target_url", "").strip()
    if not target_url:
        flash("返信先ツイートのURLは必須です。", "danger")
        return redirect(url_for("sns.engage_queue"))
    item.target_url = target_url
    item.target_author = request.form.get("target_author", "").strip()
    item.reply_text = request.form.get("reply_text", "").strip()

    posted_at_str = request.form.get("target_posted_at", "").strip()
    if posted_at_str:
        try:
            item.target_posted_at = datetime.strptime(posted_at_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("対象ツイートの投稿時刻の形式が不正です（変更しませんでした）。", "warning")
    else:
        item.target_posted_at = None
    db.session.commit()
    flash("下書きを更新しました。", "success")
    return redirect(url_for("sns.engage_queue"))


@bp.route("/engage/<int:item_id>/submit", methods=["POST"])
def engage_submit(item_id):
    """承認依頼: draft → pending。"""
    item = _owned_engage_or_403(item_id)
    if not can_engage_transition(item.status, ENGAGE_STATUS_PENDING):
        flash(f"この項目は承認依頼できません（現在: {ENGAGE_STATUS_LABELS.get(item.status, item.status)}）。", "warning")
        return redirect(url_for("sns.engage_queue"))
    if not (item.reply_text or "").strip():
        flash("返信本文が空です。下書きを書いてから承認依頼してください。", "warning")
        return redirect(url_for("sns.engage_queue"))
    item.status = ENGAGE_STATUS_PENDING
    db.session.commit()
    flash("承認待ちにしました。", "success")
    return redirect(url_for("sns.engage_queue"))


@bp.route("/engage/<int:item_id>/approve", methods=["POST"])
def engage_approve(item_id):
    """承認: pending → approved。承認済みは「Xで返信」リンクで手動送信に倒す。"""
    item = _owned_engage_or_403(item_id)
    if not can_engage_transition(item.status, ENGAGE_STATUS_APPROVED):
        flash(f"この項目は承認できません（現在: {ENGAGE_STATUS_LABELS.get(item.status, item.status)}）。", "warning")
        return redirect(url_for("sns.engage_queue"))
    item.status = ENGAGE_STATUS_APPROVED
    db.session.commit()
    flash("承認しました。鮮度が高いうちに X で手動返信し「送信済み」にしてください。", "success")
    return redirect(url_for("sns.engage_queue"))


@bp.route("/engage/<int:item_id>/expire", methods=["POST"])
def engage_expire(item_id):
    """失効: draft / pending / approved → expired（鮮度切れ・見送り）。"""
    item = _owned_engage_or_403(item_id)
    if not can_engage_transition(item.status, ENGAGE_STATUS_EXPIRED):
        flash(f"この項目は失効できません（現在: {ENGAGE_STATUS_LABELS.get(item.status, item.status)}）。", "warning")
        return redirect(url_for("sns.engage_queue"))
    item.status = ENGAGE_STATUS_EXPIRED
    db.session.commit()
    flash("鮮度切れとして失効にしました。", "warning")
    return redirect(url_for("sns.engage_queue"))


@bp.route("/engage/<int:item_id>/revise", methods=["POST"])
def engage_revise(item_id):
    """差し替え: expired → draft（内容を作り直して再挑戦する）。"""
    item = _owned_engage_or_403(item_id)
    if not can_engage_transition(item.status, ENGAGE_STATUS_DRAFT):
        flash(f"この項目は差し替えできません（現在: {ENGAGE_STATUS_LABELS.get(item.status, item.status)}）。", "warning")
        return redirect(url_for("sns.engage_queue"))
    item.status = ENGAGE_STATUS_DRAFT
    db.session.commit()
    flash("下書きに戻しました。内容を差し替えて再挑戦できます。", "success")
    return redirect(url_for("sns.engage_queue"))


@bp.route("/engage/<int:item_id>/mark-sent", methods=["POST"])
def engage_mark_sent(item_id):
    """送信済みマーク: approved → sent。X での手動返信後に記録する（外部書き込みなし）。"""
    item = _owned_engage_or_403(item_id)
    if not can_engage_transition(item.status, ENGAGE_STATUS_SENT):
        flash(f"この項目は送信済みにできません（現在: {ENGAGE_STATUS_LABELS.get(item.status, item.status)}）。", "warning")
        return redirect(url_for("sns.engage_queue"))
    item.status = ENGAGE_STATUS_SENT
    db.session.commit()
    flash("送信済みにしました。初速リプ回りの1件です。", "success")
    return redirect(url_for("sns.engage_queue"))
