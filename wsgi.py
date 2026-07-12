import sys
import traceback

try:
    from app import create_app
    from flask import redirect, url_for
    from flask_login import current_user

    app = create_app()

    # 起動時にDBテーブルを自動作成
    with app.app_context():
        from app.extensions import db
        import os
        os.makedirs(os.path.join(app.instance_path), exist_ok=True)
        try:
            db.create_all()
        except Exception as db_err:
            print(f"[wsgi] WARNING: db.create_all() failed: {db_err}", file=sys.stderr)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            if current_user.role == "master":
                return redirect(url_for("master.client_list"))
            if current_user.role == "admin":
                return redirect(url_for("admin.analytics"))
            return redirect(url_for("user.calendar"))
        return redirect(url_for("auth.login"))

except Exception:
    # Render のログにエラー全文を出力してから再 raise
    traceback.print_exc(file=sys.stderr)
    raise


if __name__ == "__main__":
    app.run()
