import os
import sys

from app import create_app
from flask import redirect, url_for
from flask_login import current_user

app = create_app()

# 起動時のスキーマ準備。DBへ接続できなくても gunicorn の起動は止めない
# （本番の全断＝502を防ぐ）。実テーブル作成/差分適用は起動前の
# `flask ensure-schema`（render.yaml startCommand・|| true でガード）が担う。
try:
    with app.app_context():
        from app.extensions import db
        os.makedirs(app.instance_path, exist_ok=True)
        db.create_all()
except Exception as _e:  # noqa: BLE001
    import logging
    logging.getLogger("wsgi").exception(
        "起動時 db.create_all をスキップ（DB未接続の可能性）: %s", _e
    )


@app.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.role == "master":
            return redirect(url_for("master.client_list"))
        if current_user.role == "admin":
            return redirect(url_for("admin.analytics"))
        return redirect(url_for("user.calendar"))
    return redirect(url_for("auth.login"))


if __name__ == "__main__":
    app.run()
