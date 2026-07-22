from datetime import datetime
from flask_login import UserMixin
from .extensions import db, login_manager


class Client(db.Model):
    __tablename__ = "client"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="client", lazy="dynamic")
    profile = db.relationship("ProfileConcept", uselist=False, backref="client")
    metrics = db.relationship("MetricEntry", backref="client", lazy="dynamic", order_by="MetricEntry.date")
    consult_notes = db.relationship("ConsultNote", backref="client", lazy="dynamic", order_by="ConsultNote.created_at")
    calendars = db.relationship("CalendarEntry", backref="client", lazy="dynamic")
    drafts = db.relationship("Draft", backref="client", lazy="dynamic")


class User(db.Model, UserMixin):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(256), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False)  # master / admin / user
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)
    display_name = db.Column(db.String(80), default="")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_id(self):
        return str(self.id)

    @property
    def is_master(self):
        return self.role == "master"

    @property
    def is_admin(self):
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class ProfileConcept(db.Model):
    __tablename__ = "profile_concept"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), unique=True, nullable=False)
    genre = db.Column(db.String(200), default="")
    who = db.Column(db.String(200), default="")
    what = db.Column(db.String(200), default="")
    how = db.Column(db.String(200), default="")
    display_name = db.Column(db.String(80), default="")
    position = db.Column(db.String(200), default="")
    achievement = db.Column(db.String(200), default="")
    current_phase = db.Column(db.String(16), default="player")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MetricEntry(db.Model):
    __tablename__ = "metric_entry"
    __table_args__ = (db.UniqueConstraint("client_id", "date"),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    posts_per_day = db.Column(db.Float, nullable=True)
    avg_impressions = db.Column(db.Float, nullable=True)
    engagement_rate_pct = db.Column(db.Float, nullable=True)
    followers_delta_per_day = db.Column(db.Float, nullable=True)
    list_signups_per_day = db.Column(db.Float, nullable=True)
    meeting_rate_pct = db.Column(db.Float, nullable=True)
    conversion_rate_pct = db.Column(db.Float, nullable=True)
    logged_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    logged_by = db.relationship("User", foreign_keys=[logged_by_user_id])


class ConsultNote(db.Model):
    __tablename__ = "consult_note"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    author_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    author = db.relationship("User", foreign_keys=[author_user_id])


class CalendarEntry(db.Model):
    __tablename__ = "calendar_entry"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    week_no = db.Column(db.Integer, nullable=False)
    phase_at_generation = db.Column(db.String(16), default="player")
    content_json = db.Column(db.JSON, nullable=False)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    generated_by = db.relationship("User", foreign_keys=[generated_by_user_id])


class Draft(db.Model):
    __tablename__ = "draft"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    batch_id = db.Column(db.String(32), nullable=False)
    n = db.Column(db.Integer, nullable=False)
    hook = db.Column(db.String(200), default="")
    target = db.Column(db.String(200), default="")
    reinforcement = db.Column(db.String(200), default="")
    education_name = db.Column(db.String(80), default="")
    source = db.Column(db.String(16), default="template")  # claude / template
    text = db.Column(db.Text, nullable=False)
    reviewed = db.Column(db.Boolean, default=False)
    # E3-4: 自己改善ループ用のタグ（生成時にルールベースで推定）。
    # 実績（インプ）と突き合わせて勝ち型を可視化するための分類軸。
    # 後方互換: 既存行は "" のまま（＝未分類）。捏造しない。
    hook_type = db.Column(db.String(40), default="")     # 問いかけ/数字/権威 等
    format_type = db.Column(db.String(40), default="")   # 列挙/ハウツー/ストーリー 等
    cta_type = db.Column(db.String(40), default="")      # オファー/フォロー誘導/なし 等
    video_path = db.Column(db.String(500), nullable=True)  # path to generated video file
    # E6-1: 投稿→オファー導線（CTA）
    cta_label = db.Column(db.String(120), default="")
    cta_url = db.Column(db.String(500), default="")
    offer_lp_id = db.Column(db.Integer, db.ForeignKey("landing_page.id"), nullable=True)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    generated_by = db.relationship("User", foreign_keys=[generated_by_user_id])


class StoryCampaign(db.Model):
    """期間指定ストーリーキャンペーン：X投稿をストーリーアークで自動生成・予約"""
    __tablename__ = "story_campaign"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    product_name = db.Column(db.String(200), default="")  # 訴求する商品（任意）
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    posts_per_day = db.Column(db.Integer, default=1)
    platform = db.Column(db.String(20), default="x")
    with_image = db.Column(db.Boolean, default=False)  # DALL-E画像生成するか
    status = db.Column(db.String(20), default="pending")  # pending / running / done / failed
    total_posts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    client = db.relationship("Client", backref=db.backref("story_campaigns", lazy="dynamic"))


class LandingPage(db.Model):
    __tablename__ = "landing_page"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    title = db.Column(db.String(200), default="")
    body_html = db.Column(db.Text, nullable=False, default="")
    line_url = db.Column(db.String(500), default="")
    is_published = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class SalesLetter(db.Model):
    __tablename__ = "sales_letter"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    title = db.Column(db.String(200), default="")
    product_name = db.Column(db.String(200), default="")
    price_jpy = db.Column(db.Integer, default=0)
    body_html = db.Column(db.Text, nullable=False, default="")
    stripe_link = db.Column(db.String(500), default="")
    contact_email = db.Column(db.String(200), default="")
    contact_phone = db.Column(db.String(50), default="")
    is_published = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class LineStepSet(db.Model):
    __tablename__ = "line_step_set"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    title = db.Column(db.String(200), default="")
    steps_json = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class ContactMessage(db.Model):
    __tablename__ = "contact_message"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), default="")
    body = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(80), default="lp")  # lp / sales_letter / cta / direct
    source_detail = db.Column(db.String(200), default="")  # 導線の詳細（例: LP名 / CTAラベル / 流入元）
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class StripeProduct(db.Model):
    __tablename__ = "stripe_product"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    product_name = db.Column(db.String(200), nullable=False)
    price_jpy = db.Column(db.Integer, nullable=False)
    stripe_price_id = db.Column(db.String(200), default="")
    payment_link_url = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])


class Purchase(db.Model):
    """E5-1: Stripe Webhook で受けた購入完了の記録。

    Payment Link / Checkout の決済成立（payment_intent.succeeded 等）を
    Webhook で受け、購入を1件記録する。stripe_event_id を unique にして
    冪等性を担保（Stripe は同一イベントを再送しうるため、二重記録しない）。
    承認ゲートの外側（外部＝Stripe が確定した事実の記録）なので status は
    既定 "paid"。返金等は将来 refunded を足す想定。
    """
    __tablename__ = "purchase"
    id = db.Column(db.Integer, primary_key=True)
    # 商品の紐付けが取れない Webhook もあるため client_id は nullable。
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True, index=True)
    stripe_event_id = db.Column(db.String(120), unique=True)      # 冪等キー
    stripe_payment_intent = db.Column(db.String(120), default="")
    amount = db.Column(db.Integer, nullable=True)                 # 最小通貨単位（JPYは円）
    currency = db.Column(db.String(10), default="")
    customer_email = db.Column(db.String(200), default="")
    product_name = db.Column(db.String(200), default="")
    status = db.Column(db.String(20), default="paid")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    client = db.relationship("Client", backref=db.backref("purchases", lazy="dynamic"))


class Testimonial(db.Model):
    """社会的証明（お客様の声 / 実績 / ロゴ）。kind別必須はルート側で検証。"""
    __tablename__ = "testimonial"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    kind = db.Column(db.String(16), default="voice")  # voice / result / logo
    author_name = db.Column(db.String(120), nullable=True)
    author_title = db.Column(db.String(200), nullable=True)
    quote = db.Column(db.Text, nullable=True)
    metric_label = db.Column(db.String(120), nullable=True)
    metric_value = db.Column(db.String(120), nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    logo_url = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    client = db.relationship("Client", backref=db.backref("testimonials", lazy="dynamic"))


class EngagementItem(db.Model):
    """E3-2: エンゲージメント・キュー（返信下書きの鮮度管理）。

    返信は「いいねの約13.5倍・会話成立で最大75倍」効く一方、リプ下書きは
    鮮度商品で、貯めてから承認すると対象が古びて失敗する（24h超で陳腐化）。
    → 鮮度順（対象ツイートの投稿時刻＞下書き作成時刻）に承認提示し、古い
    下書きは「鮮度切れ」として失効/差し替えを促す。

    承認ゲート: draft → pending → approved → sent（手動でXでリプ後に記録）。
    どの状態でも自動でXへ書き込みはしない（テンプレファースト・手動送信）。
    """
    __tablename__ = "engagement_item"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    target_url = db.Column(db.String(500), nullable=False)      # 返信先ツイートのURL
    target_author = db.Column(db.String(120), default="")       # 返信先の投稿者（任意）
    target_posted_at = db.Column(db.DateTime, nullable=True)     # 対象ツイートの投稿時刻（鮮度の基準）
    reply_text = db.Column(db.Text, nullable=False, default="")  # 返信下書き
    status = db.Column(db.String(20), default="draft")          # draft/pending/approved/sent/expired
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    client = db.relationship("Client", backref=db.backref("engagement_items", lazy="dynamic"))

    # 鮮度の基準時刻: 対象ツイートの投稿時刻があればそれ、無ければ下書き作成時刻。
    @property
    def freshness_at(self):
        return self.target_posted_at or self.created_at

    @property
    def status_label(self):
        return ENGAGE_STATUS_LABELS.get(self.status, self.status)


# E3-2: エンゲージメント・キューの状態（draft → pending → approved → sent / expired）
ENGAGE_STATUS_DRAFT = "draft"        # 下書き
ENGAGE_STATUS_PENDING = "pending"    # 承認待ち
ENGAGE_STATUS_APPROVED = "approved"  # 承認済み（＝Xで手動返信してよい）
ENGAGE_STATUS_SENT = "sent"          # 送信済み（手動でリプ後に記録）
ENGAGE_STATUS_EXPIRED = "expired"    # 鮮度切れで失効

ENGAGE_STATUSES = [
    ENGAGE_STATUS_DRAFT, ENGAGE_STATUS_PENDING, ENGAGE_STATUS_APPROVED,
    ENGAGE_STATUS_SENT, ENGAGE_STATUS_EXPIRED,
]

ENGAGE_STATUS_LABELS = {
    ENGAGE_STATUS_DRAFT: "下書き",
    ENGAGE_STATUS_PENDING: "承認待ち",
    ENGAGE_STATUS_APPROVED: "承認済み",
    ENGAGE_STATUS_SENT: "送信済み",
    ENGAGE_STATUS_EXPIRED: "鮮度切れ",
}

# 許可された状態遷移。ここに無い遷移はルート側で拒否する（承認ゲートの強制）。
ENGAGE_STATUS_TRANSITIONS = {
    ENGAGE_STATUS_DRAFT: {ENGAGE_STATUS_PENDING, ENGAGE_STATUS_EXPIRED},
    ENGAGE_STATUS_PENDING: {ENGAGE_STATUS_APPROVED, ENGAGE_STATUS_DRAFT, ENGAGE_STATUS_EXPIRED},
    ENGAGE_STATUS_APPROVED: {ENGAGE_STATUS_SENT, ENGAGE_STATUS_PENDING, ENGAGE_STATUS_EXPIRED},
    ENGAGE_STATUS_SENT: set(),
    ENGAGE_STATUS_EXPIRED: {ENGAGE_STATUS_DRAFT},  # 差し替え時に下書きへ戻せる
}


def can_engage_transition(current, target):
    """current → target の状態遷移が許可されているか（承認ゲートの強制）。"""
    return target in ENGAGE_STATUS_TRANSITIONS.get(current or ENGAGE_STATUS_DRAFT, set())


PLATFORMS = ["x", "instagram", "tiktok", "youtube"]
PLATFORM_LABELS = {"x": "X (Twitter)", "instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}

# E6-5: 予約投稿の承認ステータス（draft → pending → approved → posted / failed）
# 自動投稿の対象は approved のみ。draft / pending は絶対に外部送信しない。
POST_STATUS_DRAFT = "draft"          # 下書き（本人がまだ承認依頼していない）
POST_STATUS_PENDING = "pending"      # 承認待ち（既定値・後方互換：既存データはここ）
POST_STATUS_APPROVED = "approved"    # 承認済み（＝自動投稿の対象）
POST_STATUS_POSTED = "posted"        # 投稿済み
POST_STATUS_FAILED = "failed"        # 投稿失敗
POST_STATUS_CANCELLED = "cancelled"  # キャンセル
# E3-6: 低インプ投稿クリーンアップ。承認した分だけ削除し、履歴として残す。
POST_STATUS_DELETED = "deleted"      # 投稿後に削除した（不可逆・承認ゲート必須）

POST_STATUSES = [
    POST_STATUS_DRAFT, POST_STATUS_PENDING, POST_STATUS_APPROVED,
    POST_STATUS_POSTED, POST_STATUS_FAILED, POST_STATUS_CANCELLED,
    POST_STATUS_DELETED,
]

POST_STATUS_LABELS = {
    POST_STATUS_DRAFT: "下書き",
    POST_STATUS_PENDING: "承認待ち",
    POST_STATUS_APPROVED: "承認済み",
    POST_STATUS_POSTED: "投稿済み",
    POST_STATUS_FAILED: "失敗",
    POST_STATUS_CANCELLED: "キャンセル",
    POST_STATUS_DELETED: "削除済み",
}

# 許可された状態遷移。ここに無い遷移はルート側で拒否する（承認ゲートの強制）。
POST_STATUS_TRANSITIONS = {
    POST_STATUS_DRAFT: {POST_STATUS_PENDING, POST_STATUS_CANCELLED, POST_STATUS_POSTED},
    POST_STATUS_PENDING: {POST_STATUS_APPROVED, POST_STATUS_DRAFT, POST_STATUS_CANCELLED, POST_STATUS_POSTED},
    POST_STATUS_APPROVED: {POST_STATUS_PENDING, POST_STATUS_POSTED, POST_STATUS_FAILED, POST_STATUS_CANCELLED},
    POST_STATUS_FAILED: {POST_STATUS_PENDING, POST_STATUS_CANCELLED, POST_STATUS_POSTED},
    # E3-6: 投稿済みは「削除済み」へのみ遷移できる（承認ゲート付きクリーンアップ）。
    POST_STATUS_POSTED: {POST_STATUS_DELETED},
    POST_STATUS_CANCELLED: {POST_STATUS_PENDING},
    POST_STATUS_DELETED: set(),
}


def can_transition(current, target):
    """current → target の状態遷移が許可されているか。"""
    return target in POST_STATUS_TRANSITIONS.get(current or POST_STATUS_PENDING, set())


class SnsConnection(db.Model):
    """Stores API credentials per client per platform."""
    __tablename__ = "sns_connection"
    __table_args__ = (db.UniqueConstraint("client_id", "platform"),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    platform = db.Column(db.String(20), nullable=False)  # x / instagram / tiktok / youtube
    credentials_json = db.Column(db.JSON, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    client = db.relationship("Client", backref=db.backref("sns_connections", lazy="dynamic"))


class ScheduledPost(db.Model):
    """A draft queued for posting to one or more platforms at a specific time."""
    __tablename__ = "scheduled_post"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    draft_id = db.Column(db.Integer, db.ForeignKey("draft.id"), nullable=True)
    platform = db.Column(db.String(20), nullable=False)
    text = db.Column(db.Text, nullable=False)
    media_path = db.Column(db.String(500), nullable=True)  # local path to video/image
    scheduled_at = db.Column(db.DateTime, nullable=False)
    # E6-5: draft / pending / approved / posted / failed / cancelled（POST_STATUSES 参照）
    status = db.Column(db.String(20), default=POST_STATUS_PENDING)
    post_id = db.Column(db.String(200), nullable=True)     # platform-returned post ID
    error_msg = db.Column(db.Text, nullable=True)
    # E6-5: 承認ゲート＋人間的ペースの監査用。
    # approved_at: 承認した時刻（未承認は NULL）
    # approved_by_user_id: 承認者（誰が通したかを残す）
    # posted_at: 実際に投稿された時刻（最小投稿間隔の判定に使う。再起動しても効く）
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    posted_at = db.Column(db.DateTime, nullable=True)
    # E6-1: 投稿→オファー導線（CTA）。text には合成済み本文が入るが監査用に保持
    cta_label = db.Column(db.String(120), default="")
    cta_url = db.Column(db.String(500), default="")
    offer_lp_id = db.Column(db.Integer, db.ForeignKey("landing_page.id"), nullable=True)
    # E3-6: 低インプ投稿クリーンアップ。投稿済み(post_idあり)に対し、手入力/
    # ブックマークレットでインプを記録し、低インプ候補の検出に使う。
    # impressions は未計測なら NULL（＝削除候補にしない安全側）。
    impressions = db.Column(db.Integer, nullable=True)
    imp_checked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    draft = db.relationship("Draft", foreign_keys=[draft_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_user_id])
    client = db.relationship("Client", backref=db.backref("scheduled_posts", lazy="dynamic"))

    @property
    def status_label(self):
        return POST_STATUS_LABELS.get(self.status, self.status)

    @property
    def is_approved(self):
        return self.status == POST_STATUS_APPROVED
