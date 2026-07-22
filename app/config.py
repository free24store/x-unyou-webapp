import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _normalize_db_url(url: str) -> str:
    """DATABASE_URL を SQLAlchemy が受理する形へ正規化する。

    - Render / Heroku 等は接頭辞 `postgres://` を渡すが、SQLAlchemy 1.4+ は
      これを拒否する（`postgresql://` でなければならない）。ここで吸収する。
    - `postgresql://` を明示指定して psycopg2 ドライバを使わせる
      （`postgresql+psycopg2://` は SQLAlchemy 既定なので postgresql:// のままでよい）。
    - SQLite など他方言・空文字はそのまま返す（既定フォールバックを壊さない）。
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me")
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(
        os.environ.get(
            "DATABASE_URL",
            f"sqlite:///{BASE_DIR / 'instance' / 'app.db'}",
        )
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
    MAIL_USE_TLS  = os.environ.get("MAIL_USE_TLS", "1") not in ("0", "false", "False", "")

    # 生成物（画像/動画）永続ストレージ（E1-3）
    # 既定は local（instance/ 保存＝Render では ephemeral）。
    # env が揃うと S3 互換 or Cloudinary へ退避。詳細は app/services/storage_service.py。
    STORAGE_PROVIDER    = os.environ.get("STORAGE_PROVIDER", "local")
    # S3 互換（Cloudflare R2 / Backblaze B2 / AWS S3 / MinIO）
    S3_BUCKET           = os.environ.get("S3_BUCKET", "")
    S3_ENDPOINT_URL     = os.environ.get("S3_ENDPOINT_URL", "")
    S3_ACCESS_KEY_ID    = os.environ.get("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "")
    S3_PUBLIC_BASE_URL  = os.environ.get("S3_PUBLIC_BASE_URL", "")
    S3_REGION           = os.environ.get("S3_REGION", "auto")
    # Cloudinary（任意経路。CLOUDINARY_URL 1本で設定）
    CLOUDINARY_URL      = os.environ.get("CLOUDINARY_URL", "")
