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

    # E1-2: ヘルスチェック（Render監視用）。認証不要・依存最小。
    # DB疎通が落ちていても 200 + db:"ng" で返し、Renderのヘルスチェック自体は
    # 落とさない（無料プランでDB一時不調→再起動ループになるのを防ぐ）。
    @app.route("/healthz")
    def healthz():
        from flask import jsonify
        from sqlalchemy import text as _sql_text

        db_status = "ok"
        try:
            db.session.execute(_sql_text("SELECT 1"))
        except Exception:
            db_status = "ng"
        finally:
            db.session.remove()

        return jsonify({
            "status": "ok",
            "db": db_status,
            "version": os.environ.get("RENDER_GIT_COMMIT", os.environ.get("APP_VERSION", "dev"))[:12],
        }), 200

    # APScheduler: 予約投稿を1分ごとにチェック（失敗してもアプリは起動する）
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="Asia/Tokyo")

        def _run_scheduled_posts():
            # クロージャで捕捉した app を使い、コンテキストを張ってから実行する。
            # _execute_post() は flash() を呼ぶため app_context だけでは
            # "Working outside of request context" になる。test_request_context()
            # は app+request 両コンテキストを張る（flash 先の session は破棄される）。
            # ジョブ内は try/except で握り、失敗しても毎分ログを荒らさず・
            # スケジューラを止めない（失敗はログに残す）。session は context
            # teardown で自動 remove される。
            #
            # E6-5: 承認ゲート＋人間的ペース。
            # 対象は status == "approved" のみ（pending / draft は絶対に投稿しない）。
            # 1回の実行で最大1件・同一クライアントの前回投稿から最小間隔
            # （既定10分 / SCHEDULER_MIN_POST_INTERVAL_MINUTES）を空ける＝
            # バースト投稿を構造的に不可能にする。選定ロジックは
            # sns.routes.select_due_post() に集約（関数単位で検証可能）。
            try:
                with app.test_request_context():
                    from datetime import datetime
                    from .sns.routes import select_due_post, _execute_post
                    post = select_due_post(datetime.utcnow())
                    if post is not None:
                        _execute_post(post)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("予約投稿ジョブの実行に失敗しました")

        scheduler.add_job(_run_scheduled_posts, "interval", minutes=1, id="scheduled_posts")
        scheduler.start()
        app.scheduler = scheduler
    except Exception:
        app.scheduler = None

    @app.context_processor
    def inject_base_url():
        return {"BASE_URL": app.config["BASE_URL"]}

    @app.context_processor
    def inject_api_status():
        return dict(api_status={
            "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "stripe": bool(os.environ.get("STRIPE_SECRET_KEY")),
        })

    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        print("DBを初期化しました。")

    @app.cli.command("ensure-schema")
    def ensure_schema():
        """既存DBに不足している列を冪等に追加する（マイグレーション機構の代替）。

        create_all は既存テーブルへの列追加を行わないため、E6-1 で追加した
        CTA列（cta_label / cta_url / offer_lp_id）を ALTER TABLE で補う。
        新規DBは create_all 済みなので対象外（列存在チェックでスキップされる）。
        SQLite / Postgres の両方言に対応。
        """
        from sqlalchemy import text as _sql_text

        # 新規テーブル（例: testimonial）を既存DBに冪等生成する。
        # create_all は既存テーブルへの列追加は行わないが、未作成テーブルは作る。
        db.create_all()

        # (table, column, DDL型定義)
        wanted = [
            ("draft", "cta_label", "VARCHAR(120) DEFAULT ''"),
            ("draft", "cta_url", "VARCHAR(500) DEFAULT ''"),
            ("draft", "offer_lp_id", "INTEGER"),
            ("scheduled_post", "cta_label", "VARCHAR(120) DEFAULT ''"),
            ("scheduled_post", "cta_url", "VARCHAR(500) DEFAULT ''"),
            ("scheduled_post", "offer_lp_id", "INTEGER"),
            ("contact_message", "source_detail", "VARCHAR(200) DEFAULT ''"),
            # E6-5: 承認ゲート＋人間的ペース。既存行は NULL（＝未承認）のままでよい。
            # 既存の pending 行は承認するまで自動投稿されない（安全側の既定）。
            ("scheduled_post", "approved_at", "TIMESTAMP"),
            ("scheduled_post", "approved_by_user_id", "INTEGER"),
            ("scheduled_post", "posted_at", "TIMESTAMP"),
            # E3-6: 低インプ投稿クリーンアップ。既存行は NULL（未計測）のままでよい。
            ("scheduled_post", "impressions", "INTEGER"),
            ("scheduled_post", "imp_checked_at", "TIMESTAMP"),
        ]

        dialect = db.engine.dialect.name

        def _existing_columns(table):
            if dialect == "sqlite":
                rows = db.session.execute(
                    _sql_text(f"PRAGMA table_info({table})")
                ).fetchall()
                return {r[1] for r in rows}
            else:
                rows = db.session.execute(
                    _sql_text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                ).fetchall()
                return {r[0] for r in rows}

        added = 0
        cache = {}
        for table, column, ddl in wanted:
            if table not in cache:
                cache[table] = _existing_columns(table)
            if column in cache[table]:
                continue
            db.session.execute(
                _sql_text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            )
            db.session.commit()
            cache[table].add(column)
            added += 1
            print(f"追加: {table}.{column}")

        if added == 0:
            print("スキーマは最新です（追加なし）。")
        else:
            print(f"{added}列を追加しました。")

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
