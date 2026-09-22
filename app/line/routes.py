"""E5-3: LINE Messaging API の公開エンドポイント（認証なし・署名/シークレットで守る）。"""
import hmac
import json
import logging
import os

from flask import request, jsonify

from . import bp
from ..extensions import csrf
from ..models import Client
from ..services import line_service

logger = logging.getLogger(__name__)


@bp.route("/webhook/<int:client_id>", methods=["POST"])
@csrf.exempt
def webhook(client_id):
    """LINE プラットフォームからの Webhook。

    - 接続未設定なら 404（署名検証できないものは信頼しない）
    - 署名不一致は 400
    - それ以外は 200（処理中の例外もログに残して 200。LINE に無用な再送をさせない）
    """
    client = Client.query.get(client_id)
    creds = line_service.get_credentials(client_id) if client else None
    if not creds:
        return jsonify({"status": "not_configured"}), 404

    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")
    if not line_service.verify_signature(creds["channel_secret"], body, signature):
        logger.warning("LINE Webhook 署名検証に失敗しました client=%s", client_id)
        return jsonify({"status": "invalid_signature"}), 400

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except ValueError:
        return jsonify({"status": "invalid_json"}), 400

    # LINE Developers の「検証」ボタンは events=[] を送ってくる。そのまま 200。
    line_service.handle_events(client, creds, payload.get("events") or [])
    return jsonify({"status": "ok"}), 200


@bp.route("/cron/dispatch", methods=["GET", "POST"])
@csrf.exempt
def cron_dispatch():
    """外部cron（cron-job.org）から配信時刻に叩かれる。

    無料プランのスリープ中はアプリ内スケジューラが動かないため、配信の主経路はこちら。
    LINE_CRON_SECRET 未設定なら 503。ヘッダー X-Cron-Secret（推奨）か ?key= で認証。
    """
    secret = os.environ.get("LINE_CRON_SECRET", "")
    if not secret:
        return jsonify({"status": "cron_secret_unset"}), 503
    given = request.headers.get("X-Cron-Secret") or request.args.get("key") or ""
    if not hmac.compare_digest(given, secret):
        return jsonify({"status": "forbidden"}), 403
    summary = line_service.dispatch_due()
    return jsonify({"status": "ok", **summary}), 200
