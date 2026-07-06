import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'app.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    # 本番ドメイン（LINEリッチメニュー等の外部リンクに使用）
    BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5050")
    # Stripe決済連携
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    # メール送信（お問い合わせ通知）
    MAIL_SERVER   = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT     = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
