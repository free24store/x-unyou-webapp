import os
import sys

from app import create_app
from flask import redirect, url_for
from flask_login import current_user

app = create_app()

with app.app_context():
    from app.extensions import db
    os.makedirs(app.instance_path, exist_ok=True)
    db.create_all()


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
