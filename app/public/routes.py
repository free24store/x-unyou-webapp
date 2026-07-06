import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, abort

from . import bp
from ..extensions import db
from ..models import LandingPage, SalesLetter, ContactMessage, Client


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
    return render_template("public/lp.html", page=page)


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

    return render_template("public/sales_letter.html", letter=letter, client=client)


@bp.route("/contact/<int:client_id>", methods=["GET", "POST"])
def contact_view(client_id):
    client = Client.query.get_or_404(client_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        if not name or not email:
            flash("お名前とメールアドレスは必須です。", "danger")
            return redirect(url_for("public.contact_view", client_id=client_id))

        msg_obj = ContactMessage(
            client_id=client_id,
            name=name,
            email=email,
            phone=request.form.get("phone", "").strip(),
            body=request.form.get("body", "").strip(),
            source="lp",
        )
        db.session.add(msg_obj)
        db.session.commit()
        _send_contact_email(client, msg_obj)
        flash("お問い合わせを受け付けました。近日中にご連絡いたします。", "success")
        return redirect(url_for("public.contact_view", client_id=client_id))

    return render_template("public/contact.html", client=client)
