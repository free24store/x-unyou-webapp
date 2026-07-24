"""
お問い合わせ通知メールサービス（E5-2）— 標準ライブラリのみ・テンプレファースト。

公開の問い合わせフォーム（ContactMessage 作成）時に、管理者へ SMTP でメール通知する。

テンプレートファースト:
  SMTP 系 env（MAIL_SERVER / MAIL_USERNAME / MAIL_PASSWORD）と送信先（CONTACT_EMAIL）が
  揃っていない場合は **送信せず**、フォームは従来どおり保存だけで成功する。
  env 無しでも import・起動・保存はすべて壊れない。

統一IF（将来 LINE 通知等に拡張しやすいよう最小面で公開）:
  is_available() -> bool                      … 送信に必要な env が揃っているか
  send_contact_notification(msg) -> bool      … 新規問い合わせを CONTACT_EMAIL へ通知
        msg: ContactMessage 相当（name / email / phone / body / source / source_detail）
        戻り値: 送信できたら True、未設定・失敗なら False（フォームフローは変えない）

※ smtplib / email.mime は **関数内で遅延import**。
  例外は握ってログのみ（送信失敗でも問い合わせは DB 保存済みなので成功扱い）。
"""
import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# env 読み取り
# ---------------------------------------------------------------------------
def _mail_config() -> dict:
    return {
        "server": os.environ.get("MAIL_SERVER", ""),
        "port": os.environ.get("MAIL_PORT", "587"),
        "username": os.environ.get("MAIL_USERNAME", ""),
        "password": os.environ.get("MAIL_PASSWORD", ""),
        "contact_email": os.environ.get("CONTACT_EMAIL", ""),
        "use_tls": os.environ.get("MAIL_USE_TLS", "1") not in ("0", "false", "False", ""),
    }


def is_available() -> bool:
    """SMTP 送信に必要な env（サーバ・認証・送信先）が揃っているか。

    揃っていなければ呼び出し側は送信をスキップし、従来どおりの成功フローを保つ。
    """
    cfg = _mail_config()
    return bool(cfg["server"] and cfg["username"] and cfg["password"] and cfg["contact_email"])


def _port(cfg: dict) -> int:
    try:
        return int(cfg["port"])
    except (TypeError, ValueError):
        return 587


def send_contact_notification(msg) -> bool:
    """新規問い合わせ（msg）を管理者（CONTACT_EMAIL）へメール通知する。

    - env 未設定（is_available() が False）なら何もせず False を返す。
    - 送信失敗（接続不可・認証エラー等）は握ってログのみ、False を返す。
      問い合わせは既に DB 保存済みのため、ユーザーフローは変えない。
    - 成功時は True。
    """
    if not is_available():
        return False

    cfg = _mail_config()

    # 標準ライブラリだが方針に合わせ遅延import。
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.utils import formataddr
    except Exception:
        logger.exception("mail_service: 標準メールモジュールの import に失敗しました")
        return False

    name = (getattr(msg, "name", "") or "").strip()
    email = (getattr(msg, "email", "") or "").strip()
    phone = (getattr(msg, "phone", "") or "").strip()
    body = (getattr(msg, "body", "") or "").strip()
    source = (getattr(msg, "source", "") or "").strip()
    source_detail = (getattr(msg, "source_detail", "") or "").strip()

    subject = f"【新規お問い合わせ】{name or '（名前未記入）'} さんより"
    text = (
        "公開フォームより新しいお問い合わせを受け付けました。\n\n"
        f"お名前　：{name or '（未記入）'}\n"
        f"メール　：{email or '（未記入）'}\n"
        f"電話　　：{phone or '（未記入）'}\n"
        f"出所　　：{source or '（不明）'}\n"
        f"出所詳細：{source_detail or '（なし）'}\n\n"
        "----- お問い合わせ内容 -----\n"
        f"{body or '（本文なし）'}\n"
    )

    try:
        mime = MIMEMultipart()
        mime["Subject"] = subject
        mime["From"] = formataddr(("YB BOOST 問い合わせ通知", cfg["username"]))
        mime["To"] = cfg["contact_email"]
        # 問い合わせ者に直接返信できるよう Reply-To を設定（送信元アドレスは認証用）。
        if email:
            mime["Reply-To"] = email
        mime.attach(MIMEText(text, "plain", "utf-8"))

        with smtplib.SMTP(cfg["server"], _port(cfg), timeout=15) as server:
            if cfg["use_tls"]:
                server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["username"], [cfg["contact_email"]], mime.as_string())

        logger.info("mail_service: お問い合わせ通知を送信しました (to=%s)", cfg["contact_email"])
        return True
    except Exception:
        # 送信失敗でも問い合わせは DB 保存済み。ユーザーフローは成功のまま維持する。
        logger.exception("mail_service: お問い合わせ通知メールの送信に失敗しました")
        return False
