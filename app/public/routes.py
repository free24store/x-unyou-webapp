import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from flask import (render_template, request, redirect, url_for, flash, abort,
                   current_app, send_from_directory)

from . import bp
from ..extensions import db, csrf
from ..services import mail_service
from ..models import (LandingPage, SalesLetter, ContactMessage, Client,
                      Testimonial, StripeProduct, Purchase)

logger = logging.getLogger(__name__)


def _send_contact_email(client, msg_obj):
    """お問い合わせ通知メールを管理者に送信する"""
    smtp_host = os.environ.get("MAIL_SERVER", "")
    smtp_user = os.environ.get("MAIL_USERNAME", "")
    smtp_pass = os.environ.get("MAIL_PASSWORD", "")
    to_addr = os.environ.get("CONTACT_EMAIL", smtp_user)

    if not (smtp_host and smtp_user and smtp_pass and to_addr):
        return

    subject = f"【お問い合わせ】{client.name} サイトより - {msg_obj.name}"
    body = (
        f"送信者：{msg_obj.name}\n"
        f"メール：{msg_obj.email}\n"
        f"電話：{msg_obj.phone or '未記入'}\n\n"
        f"内容：\n{msg_obj.body}"
    )

    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to_addr
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(smtp_host, int(os.environ.get("MAIL_PORT", 587))) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_addr, msg.as_string())
    except Exception:
        pass  # メール失敗でも問い合わせはDB保存済みなので握り潰す


@bp.route("/lp/<int:client_id>")
def lp_view(client_id):
    page = (LandingPage.query
            .filter_by(client_id=client_id, is_published=True)
            .order_by(LandingPage.created_at.desc())
            .first_or_404())
    testimonials = (Testimonial.query
                    .filter_by(client_id=page.client_id, is_active=True)
                    .order_by(Testimonial.sort_order, Testimonial.id)
                    .all())
    return render_template("public/lp.html", page=page, testimonials=testimonials)


@bp.route("/lp-image/<path:filename>")
def lp_image(filename):
    """LP見出し用に生成した画像を配信する（instance/images 配下）。

    画像は OPENAI_API_KEY 設定時に LP 作成時のみ生成される。Render の ephemeral
    ストレージ前提のため、ファイルが存在しなければ 404（LP本体はプレースホルダで
    描画され続けるので壊れない）。send_from_directory がパストラバーサルを防ぐ。
    """
    images_dir = os.path.join(current_app.instance_path, "images")
    try:
        return send_from_directory(images_dir, filename)
    except Exception:
        abort(404)


@bp.route("/sl/<int:client_id>", methods=["GET", "POST"])
def sl_view(client_id):
    letter = (SalesLetter.query
              .filter_by(client_id=client_id, is_published=True)
              .order_by(SalesLetter.created_at.desc())
              .first_or_404())
    client = Client.query.get_or_404(client_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        if not name or not email:
            flash("お名前とメールアドレスは必須です。", "danger")
            return redirect(url_for("public.sl_view", client_id=client_id))

        msg_obj = ContactMessage(
            client_id=client_id,
            name=name,
            email=email,
            phone=request.form.get("phone", "").strip(),
            body=request.form.get("body", "").strip(),
            source="sales_letter",
        )
        db.session.add(msg_obj)
        db.session.commit()
        _send_contact_email(client, msg_obj)
        flash("お問い合わせを受け付けました。近日中にご連絡いたします。", "success")
        return redirect(url_for("public.sl_view", client_id=client_id))

    testimonials = (Testimonial.query
                    .filter_by(client_id=letter.client_id, is_active=True)
                    .order_by(Testimonial.sort_order, Testimonial.id)
                    .all())
    return render_template("public/sales_letter.html", letter=letter, client=client,
                           testimonials=testimonials)


# ──────────────────────────────────────────────
# E5-1: Stripe Webhook（購入完了処理・通知）
# ──────────────────────────────────────────────

def _record_purchase(event_id, pi_id, amount, currency, email, product_name):
    """購入を冪等に1件記録する。既知の event_id は None を返す（二重記録しない）。

    stripe_event_id が unique のため、並行/再送で衝突しても IntegrityError を
    握って None を返す（＝安全に無視）。商品名から StripeProduct を引けたら
    client_id を補完する（best-effort）。
    """
    if event_id and Purchase.query.filter_by(stripe_event_id=event_id).first():
        return None

    client_id = None
    if product_name:
        prod = (StripeProduct.query
                .filter_by(product_name=product_name)
                .order_by(StripeProduct.created_at.desc())
                .first())
        if prod is not None:
            client_id = prod.client_id

    purchase = Purchase(
        client_id=client_id,
        stripe_event_id=event_id or "",
        stripe_payment_intent=pi_id or "",
        amount=amount,
        currency=(currency or "")[:10],
        customer_email=(email or "")[:200],
        product_name=(product_name or "")[:200],
        status="paid",
    )
    db.session.add(purchase)
    try:
        db.session.commit()
    except Exception:
        # unique 制約違反（同一 event_id の競合）等は握って無視する。
        db.session.rollback()
        return None
    return purchase


@bp.route("/webhook/stripe", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    """Stripe の Webhook を受ける。テンプレファースト＝キー無しでも壊れない。

    - STRIPE_WEBHOOK_SECRET 未設定なら実処理せず 503 を返す（署名検証できない
      ものは信頼しない）。
    - 署名検証に失敗したら 400。それ以外は 200 を返し、Stripe の無用な再送を招かない。
    - payment_intent.succeeded / checkout.session.completed で Purchase を冪等作成。
    """
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        # 未設定：実処理しない。監視ノイズを避けるため 503 で「未設定」を明示。
        return {"status": "webhook_secret_unset"}, 503

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    # stripe は遅延import。未インストールでもアプリ起動は壊さない。
    try:
        import stripe as _stripe
    except Exception:
        logger.warning("stripe パッケージが未インストールのため Webhook を処理できません")
        return {"status": "stripe_unavailable"}, 503

    try:
        event = _stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        # 署名不正・改ざん・パース失敗は 400（Stripe に再送させない）。
        logger.warning("Stripe Webhook 署名検証に失敗しました: %s", e)
        return {"status": "invalid_signature"}, 400

    event_id = event.get("id")
    event_type = event.get("type", "")
    obj = (event.get("data", {}) or {}).get("object", {}) or {}

    try:
        if event_type == "payment_intent.succeeded":
            charges = ((obj.get("charges", {}) or {}).get("data", []) or [])
            email = obj.get("receipt_email") or ""
            if not email and charges:
                email = (charges[0].get("billing_details", {}) or {}).get("email", "") or ""
            product_name = (obj.get("metadata", {}) or {}).get("product_name", "") or ""
            _record_purchase(
                event_id=event_id,
                pi_id=obj.get("id", ""),
                amount=obj.get("amount"),
                currency=obj.get("currency", ""),
                email=email,
                product_name=product_name,
            )
        elif event_type == "checkout.session.completed":
            details = obj.get("customer_details", {}) or {}
            email = obj.get("customer_email") or details.get("email", "") or ""
            product_name = (obj.get("metadata", {}) or {}).get("product_name", "") or ""
            _record_purchase(
                event_id=event_id,
                pi_id=obj.get("payment_intent", ""),
                amount=obj.get("amount_total"),
                currency=obj.get("currency", ""),
                email=email,
                product_name=product_name,
            )
        else:
            # 未対応イベントは記録せず 200 で受理（Stripe の再送を招かない）。
            logger.info("Stripe Webhook: 未対応イベント %s を無視しました", event_type)
    except Exception:
        # 記録処理で想定外が起きても 200 を返す（DBは commit 時に rollback 済み）。
        db.session.rollback()
        logger.exception("Stripe Webhook の購入記録処理でエラーが発生しました")

    return {"status": "ok"}, 200


@bp.route("/contact/<int:client_id>", methods=["GET", "POST"])
def contact_view(client_id):
    client = Client.query.get_or_404(client_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        if not name or not email:
            flash("お名前とメールアドレスは必須です。", "danger")
            return redirect(url_for("public.contact_view", client_id=client_id))

        # 導線トラッキング: hidden入力の source / source_detail を採用。
        # 無指定は "direct"（直接流入）として扱う。
        source = (request.form.get("source", "").strip() or "direct")[:80]
        source_detail = request.form.get("source_detail", "").strip()[:200]

        msg_obj = ContactMessage(
            client_id=client_id,
            name=name,
            email=email,
            phone=request.form.get("phone", "").strip(),
            body=request.form.get("body", "").strip(),
            source=source,
            source_detail=source_detail,
        )
        db.session.add(msg_obj)
        db.session.commit()
        # 管理者へメール通知（E5-2）。SMTP env 未設定なら送信せず従来どおり成功。
        # 送信成否でユーザーフローは変えない（失敗しても DB 保存済み）。
        if mail_service.is_available():
            mail_service.send_contact_notification(msg_obj)
        flash("お問い合わせを受け付けました。近日中にご連絡いたします。", "success")
        return redirect(url_for("public.contact_view", client_id=client_id,
                                src=source, src_detail=source_detail))

    # GET: クエリ ?src=... / ?src_detail=... を hidden input へ反映して計測。
    source = (request.args.get("src", "").strip() or "direct")[:80]
    source_detail = request.args.get("src_detail", "").strip()[:200]
    return render_template("public/contact.html", client=client,
                           source=source, source_detail=source_detail)
