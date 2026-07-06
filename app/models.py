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
    video_path = db.Column(db.String(500), nullable=True)  # path to generated video file
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
    source = db.Column(db.String(20), default="lp")  # lp / sales_letter
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


PLATFORMS = ["x", "instagram", "tiktok", "youtube"]
PLATFORM_LABELS = {"x": "X (Twitter)", "instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}


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
    status = db.Column(db.String(20), default="pending")  # pending / posted / failed
    post_id = db.Column(db.String(200), nullable=True)     # platform-returned post ID
    error_msg = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    draft = db.relationship("Draft", foreign_keys=[draft_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    client = db.relationship("Client", backref=db.backref("scheduled_posts", lazy="dynamic"))
