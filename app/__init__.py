import getpass
import os
from dotenv import load_dotenv

load_dotenv()

from flask import Flask
from .config import Config
from .extensions import db, login_manager, csrf


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(os.path.join(app.instance_path, "videos"), exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from .master import bp as master_bp
    app.register_blueprint(master_bp)

    from .admin import bp as admin_bp
    app.register_blueprint(admin_bp)

    from .user import bp as user_bp
    app.register_blueprint(user_bp)

    from .sns import bp as sns_bp
    app.register_blueprint(sns_bp)

    from .public import bp as public_bp
    app.register_blueprint(public_bp)

    # APScheduler: 予約投稿を1分ごとにチェック
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone="Asia/Tokyo")

    def _run_scheduled_posts():
        with app.app_context():
            from datetime import datetime
            from .models import ScheduledPost
            from .sns.routes import _execute_post
            now = datetime.utcnow()
            pending = ScheduledPost.query.filter(
                ScheduledPost.status == "pending",
                ScheduledPost.scheduled_at <= now,
            ).all()
            for post in pending:
                _execute_post(post)

    scheduler.add_job(_run_scheduled_posts, "interval", minutes=1, id="scheduled_posts")
    scheduler.start()
    app.scheduler = scheduler

    @app.context_processor
    def inject_base_url():
        return {"BASE_URL": app.config["BASE_URL"]}

    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        print("DBを初期化しました。")

    @app.cli.command("seed-master")
    def seed_master():
        from .models import User
        from werkzeug.security import generate_password_hash
        if User.query.filter_by(role="master").first():
            print("マスターユーザーはすでに存在します。")
            return
        email = input("マスターのメールアドレス: ").strip()
        password = getpass.getpass("パスワード: ")
        display_name = input("表示名（任意）: ").strip() or "マスター"
        user = User(
            email=email,
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
            role="master",
            display_name=display_name,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        print(f"マスターユーザー '{email}' を作成しました。")

    return app
