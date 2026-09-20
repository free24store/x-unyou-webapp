"""LINE Messaging API 連携（E5-3）。

テンプレートファースト: 認証情報（SnsConnection platform="line"）が無ければ何も送らない。
外部通信は _api() の1か所に集約し、テストではここを差し替える。

時刻の扱い:
  - DB は他モデルと同じく naive UTC（datetime.utcnow()）。
  - ステップの送信時刻（"20:30" 等）は JST。日本は夏時間が無いので固定オフセットで足りる。
"""
import base64
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone, date, time

from ..extensions import db
from ..models import (SnsConnection, LineStepSet, LineFriend, LineDelivery, ContactMessage,
                      LINE_FRIEND_ACTIVE, LINE_FRIEND_BLOCKED, LINE_FRIEND_CONVERTED,
                      LINE_FRIEND_DONE, LINE_DELIVERY_SENDING, LINE_DELIVERY_SENT,
                      LINE_DELIVERY_FAILED, LINE_DELIVERY_GAVE_UP)

logger = logging.getLogger(__name__)

API_BASE = "https://api.line.me/v2/bot"
JST = timezone(timedelta(hours=9))

SEND_IMMEDIATE = "即時"
DEFAULT_SEND_AT = "20:30"
# この時刻（JST）以降の登録は翌日を Day0 にする。即時の1通目と当日20:30の2通目の間を
# MIN_GAP 以上空けるため（設計v2の「20時以降は翌日」を、間隔確保のため 19:30 に前倒し）。
LATE_SIGNUP = time(19, 30)
# 同じ友だちへの Push の最小間隔。停止明けの取りこぼしを1通ずつ間隔を空けて送るため。
MIN_GAP = timedelta(minutes=55)
# この時間帯（JST）は送らない。22:00〜翌7:00。
QUIET_START = time(22, 0)
QUIET_END = time(7, 0)
MAX_ATTEMPTS = 3
# 「送信中」のまま この時間を過ぎた行は、送信途中でプロセスが落ちたとみなして再試行する。
STALE_SENDING = timedelta(minutes=10)
# 1回の dispatch で送る上限（暴走防止）。
DISPATCH_LIMIT = 200
DEFAULT_CONSULT_KEYWORD = "相談希望"
_RETRY_NS = uuid.UUID("5b0c1f0e-3c1a-4e0a-9d7a-1e5d0b7c2a11")
WEEKDAYS_JA = "月火水木金土日"


class LineApiError(Exception):
    def __init__(self, status, body=""):
        super().__init__(f"LINE API {status}: {body[:300]}")
        self.status = status
        self.body = body


# ──────────────────────────────────────────────
# 認証情報
# ──────────────────────────────────────────────

def get_connection(client_id):
    return SnsConnection.query.filter_by(client_id=client_id, platform="line").first()


def get_credentials(client_id):
    """有効な接続があれば credentials dict、無ければ None。"""
    conn = get_connection(client_id)
    if not conn or not conn.is_active:
        return None
    creds = conn.credentials_json or {}
    if not creds.get("channel_secret") or not creds.get("access_token"):
        return None
    return creds


def mask(value):
    if not value:
        return ""
    return "●" * 8 + value[-4:]


# ──────────────────────────────────────────────
# 署名検証・API
# ──────────────────────────────────────────────

def verify_signature(channel_secret, body, signature):
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def _api(method, path, token, payload=None, retry_key=None):
    """LINE API 呼び出し（唯一の外部通信点）。戻り値は (status, dict)。"""
    import requests

    headers = {"Authorization": f"Bearer {token}"}
    if retry_key:
        headers["X-Line-Retry-Key"] = retry_key
    resp = requests.request(method, API_BASE + path, headers=headers, json=payload, timeout=10)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 and resp.status_code != 409:
        raise LineApiError(resp.status_code, resp.text)
    return resp.status_code, data


def reply(token, reply_token, messages):
    """reply は月間の配信通数を消費しない。"""
    _api("POST", "/message/reply", token, {"replyToken": reply_token, "messages": messages})


def push(token, user_id, messages, retry_key=None):
    """409 は「同じ retry key で送信受付済み」＝送信成功として扱う。"""
    _api("POST", "/message/push", token, {"to": user_id, "messages": messages}, retry_key=retry_key)


def get_display_name(token, user_id):
    try:
        _, data = _api("GET", f"/profile/{user_id}", token)
        return (data or {}).get("displayName", "") or ""
    except Exception:
        logger.warning("LINEプロフィール取得に失敗しました", exc_info=True)
        return ""


def retry_key_for(friend_id, step_index):
    """同じ通の再送には同じキーを使う（LINE側でも二重送信を防ぐ）。"""
    return str(uuid.uuid5(_RETRY_NS, f"{friend_id}:{step_index}"))


# ──────────────────────────────────────────────
# ステップ定義（steps_json の後方互換な正規化）
# ──────────────────────────────────────────────

def normalize_step(raw):
    """{day, send_at, message, video_url, preview_url, quick_replies} に揃える。

    旧形式 {day, timing, message}（コピー式時代の生成物）もそのまま読める。
    send_at 未指定なら Day0 は即時、それ以外は 20:30。
    """
    raw = raw or {}
    try:
        day = int(raw.get("day", 0) or 0)
    except (TypeError, ValueError):
        day = 0
    send_at = (raw.get("send_at") or "").strip()
    if not send_at:
        send_at = SEND_IMMEDIATE if day == 0 else DEFAULT_SEND_AT
    return {
        "day": max(day, 0),
        "send_at": send_at,
        "timing": raw.get("timing", ""),
        "message": raw.get("message", "") or "",
        "video_url": (raw.get("video_url") or "").strip(),
        "preview_url": (raw.get("preview_url") or "").strip(),
        "quick_replies": list(raw.get("quick_replies") or []),
    }


def normalized_steps(step_set):
    return [normalize_step(s) for s in (step_set.steps_json or [])]


def parse_send_at(value):
    """"即時" → None、"HH:MM" → time。不正値は ValueError。"""
    value = (value or "").strip()
    if value in ("", SEND_IMMEDIATE, "immediate"):
        return None
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def parse_quick_replies(text):
    """管理画面の1行1ボタン記法をパースする。

    ラベル|tag:A        → postback（タグ付け）
    ラベル|text:相談希望 → そのテキストを本人が送信した扱い
    ラベルのみ           → ラベルをそのまま送信
    """
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        label, _, action = line.partition("|")
        label = label.strip()[:20]  # LINEのラベル上限は20文字
        action = action.strip()
        if action.startswith("tag:"):
            items.append({"label": label, "tag": action[4:].strip()})
        elif action.startswith("text:"):
            items.append({"label": label, "text": action[5:].strip()})
        else:
            items.append({"label": label, "text": label})
    return items[:13]  # quick reply の上限は13個


def format_quick_replies(items):
    lines = []
    for it in items or []:
        if it.get("tag"):
            lines.append(f"{it['label']}|tag:{it['tag']}")
        elif it.get("text") and it.get("text") != it.get("label"):
            lines.append(f"{it['label']}|text:{it['text']}")
        else:
            lines.append(it.get("label", ""))
    return "\n".join(lines)


def validate_step(step):
    """保存前の検証。問題があればメッセージのリストを返す。"""
    errors = []
    try:
        parse_send_at(step["send_at"])
    except (ValueError, TypeError):
        errors.append(f"送信時刻「{step['send_at']}」は HH:MM か「即時」で指定してください")
    if not step["message"].strip():
        errors.append("本文が空です")
    if len(step["message"]) > 5000:
        errors.append("本文は5000文字以内にしてください（LINEの上限）")
    if bool(step["video_url"]) != bool(step["preview_url"]):
        errors.append("動画を付けるときは動画URLとサムネイルURLの両方が必要です")
    for url in (step["video_url"], step["preview_url"]):
        if url and not url.startswith("https://"):
            errors.append("動画・サムネイルのURLは https:// で始まる必要があります")
    return errors


# ──────────────────────────────────────────────
# スケジュール計算
# ──────────────────────────────────────────────

def to_jst(dt_utc):
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(JST)


def to_utc_naive(dt_jst):
    return dt_jst.astimezone(timezone.utc).replace(tzinfo=None)


def started_on_for(followed_at_utc):
    local = to_jst(followed_at_utc)
    if local.time() >= LATE_SIGNUP:
        return local.date() + timedelta(days=1)
    return local.date()


def due_at_utc(friend, step):
    """そのステップを送ってよい時刻（naive UTC）。即時は友だち追加の時刻。"""
    at = parse_send_at(step["send_at"])
    if at is None:
        return friend.followed_at
    d = friend.started_on + timedelta(days=step["day"])
    return to_utc_naive(datetime.combine(d, at, tzinfo=JST))


def in_quiet_hours(now_utc):
    t = to_jst(now_utc).time()
    return t >= QUIET_START or t < QUIET_END


def deadline_text(started_on):
    """個人別締切＝受講7日目（Day6）の23:59。"""
    d = started_on + timedelta(days=6)
    return f"{d.month}月{d.day}日({WEEKDAYS_JA[d.weekday()]}) 23:59"


def cron_times(step_sets):
    """外部cronに登録すべき時刻（JST）。各送信時刻＋15分後の再試行枠。"""
    times = set()
    for ss in step_sets:
        for st in normalized_steps(ss):
            try:
                at = parse_send_at(st["send_at"])
            except (ValueError, TypeError):
                continue
            if at is None:
                continue
            times.add(at)
            times.add((datetime.combine(date(2000, 1, 1), at) + timedelta(minutes=15)).time())
    return [t.strftime("%H:%M") for t in sorted(times)]


# ──────────────────────────────────────────────
# メッセージ組み立て
# ──────────────────────────────────────────────

def render_text(message, friend, creds, client_name=""):
    name = (friend.display_name or "").strip() or "あなた"
    account = (creds or {}).get("account_name") or client_name or ""
    text = message.replace("{Nickname}", name).replace("{AccountName}", account)
    if friend.started_on:
        text = text.replace("{deadline}", deadline_text(friend.started_on))
    return text


def build_messages(step, text):
    """テキスト（＋任意で動画）。quick reply は最後のメッセージに付ける。"""
    messages = [{"type": "text", "text": text[:5000]}]
    if step.get("video_url") and step.get("preview_url"):
        messages.append({
            "type": "video",
            "originalContentUrl": step["video_url"],
            "previewImageUrl": step["preview_url"],
        })
    items = []
    for qr in step.get("quick_replies") or []:
        if qr.get("tag"):
            action = {"type": "postback", "label": qr["label"],
                      "data": f"tag={qr['tag']}", "displayText": qr["label"]}
        else:
            action = {"type": "message", "label": qr["label"], "text": qr.get("text") or qr["label"]}
        items.append({"type": "action", "action": action})
    if items:
        messages[-1]["quickReply"] = {"items": items}
    return messages


# ──────────────────────────────────────────────
# Webhook イベント処理
# ──────────────────────────────────────────────

def live_step_set(client_id):
    return (LineStepSet.query
            .filter_by(client_id=client_id, is_active=True)
            .filter(LineStepSet.approved_at.isnot(None))
            .order_by(LineStepSet.approved_at.desc())
            .first())


def handle_events(client, creds, events, now=None):
    now = now or datetime.utcnow()
    for ev in events or []:
        try:
            _handle_event(client, creds, ev, now)
        except Exception:
            db.session.rollback()
            logger.exception("LINEイベント処理に失敗しました type=%s", ev.get("type"))


def _handle_event(client, creds, ev, now):
    user_id = ((ev.get("source") or {}).get("userId")) or ""
    if not user_id:
        return
    etype = ev.get("type")
    friend = LineFriend.query.filter_by(client_id=client.id, line_user_id=user_id).first()

    if etype == "follow":
        _on_follow(client, creds, friend, user_id, ev.get("replyToken"), now)
    elif etype == "unfollow":
        if friend and friend.status != LINE_FRIEND_BLOCKED:
            friend.status = LINE_FRIEND_BLOCKED
            friend.blocked_at = now
            db.session.commit()
    elif etype == "postback" and friend:
        data = ((ev.get("postback") or {}).get("data")) or ""
        if data.startswith("tag="):
            _add_tag(friend, data[4:])
            db.session.commit()
    elif etype == "message" and friend:
        msg = ev.get("message") or {}
        if msg.get("type") == "text":
            _on_text(client, creds, friend, msg.get("text") or "", ev.get("replyToken"), now)


def _add_tag(friend, tag):
    tag = (tag or "").strip()[:40]
    if tag and tag not in (friend.tags or []):
        friend.tags = list(friend.tags or []) + [tag]


def _on_follow(client, creds, friend, user_id, reply_token, now):
    token = creds["access_token"]
    if friend is not None:
        # 再追加: 最初からやり直さず、止まっていた位置から再開する（1通目の重複を防ぐ）。
        if friend.status == LINE_FRIEND_BLOCKED:
            friend.status = LINE_FRIEND_ACTIVE
            friend.blocked_at = None
            db.session.commit()
        return

    step_set = live_step_set(client.id)
    friend = LineFriend(
        client_id=client.id,
        line_user_id=user_id,
        display_name=get_display_name(token, user_id),
        status=LINE_FRIEND_ACTIVE,
        tags=[],
        step_set_id=step_set.id if step_set else None,
        started_on=started_on_for(now),
        next_step_index=0,
        followed_at=now,
    )
    db.session.add(friend)
    db.session.commit()

    if not step_set or not reply_token:
        return
    steps = normalized_steps(step_set)
    if not steps or parse_send_at(steps[0]["send_at"]) is not None:
        return  # 1通目が即時でなければ dispatcher に任せる
    # 1通目は reply で返す（配信通数を消費しない）。失敗したら dispatcher が push で送り直す。
    delivery = LineDelivery(client_id=client.id, friend_id=friend.id, step_set_id=step_set.id,
                            step_index=0, via="reply", status=LINE_DELIVERY_SENDING, attempts=1)
    db.session.add(delivery)
    db.session.commit()
    try:
        reply(token, reply_token, build_messages(steps[0], render_text(steps[0]["message"], friend, creds, client.name)))
    except Exception as e:
        delivery.status = LINE_DELIVERY_FAILED
        delivery.error = str(e)[:500]
        db.session.commit()
        return
    delivery.status = LINE_DELIVERY_SENT
    delivery.sent_at = now
    friend.next_step_index = 1
    friend.last_sent_at = now
    if len(steps) == 1:
        friend.status = LINE_FRIEND_DONE
    db.session.commit()


def _on_text(client, creds, friend, text, reply_token, now):
    keyword = (creds.get("consult_keyword") or DEFAULT_CONSULT_KEYWORD).strip()
    if not keyword or keyword not in text:
        return
    first_time = friend.consult_requested_at is None
    if first_time:
        friend.consult_requested_at = now
    _add_tag(friend, keyword)
    if friend.status == LINE_FRIEND_ACTIVE:
        friend.status = LINE_FRIEND_CONVERTED
    db.session.commit()

    auto_reply = (creds.get("consult_reply") or "").strip()
    if auto_reply and reply_token:
        try:
            reply(creds["access_token"], reply_token,
                  [{"type": "text", "text": render_text(auto_reply, friend, creds, client.name)}])
        except Exception:
            logger.warning("相談希望への自動返信に失敗しました", exc_info=True)

    if not first_time:
        return
    msg = ContactMessage(
        client_id=client.id,
        name=friend.display_name or "LINE友だち",
        email="",
        body=text[:2000],
        source="line",
        source_detail=f"LINE 相談希望（{friend.next_step_index}通目まで受信・タグ: {', '.join(friend.tags or [])}）"[:200],
    )
    db.session.add(msg)
    db.session.commit()
    try:
        from . import mail_service
        mail_service.send_contact_notification(msg)
    except Exception:
        logger.warning("相談希望のメール通知に失敗しました", exc_info=True)


# ──────────────────────────────────────────────
# 定時配信（外部cron / APScheduler から呼ばれる）
# ──────────────────────────────────────────────

def dispatch_due(now=None, limit=DISPATCH_LIMIT):
    """期限の来たステップを送る。何度呼んでも同じ通は二度送らない（冪等）。

    - 承認済み かつ 稼働中 のステップセットだけ
    - 1友だちにつき1回の実行で1通まで、前回送信から MIN_GAP 以上空ける
    - 深夜帯は送らない
    """
    now = now or datetime.utcnow()
    summary = {"sent": 0, "failed": 0, "skipped_quiet": False, "checked": 0}
    if in_quiet_hours(now):
        summary["skipped_quiet"] = True
        return summary

    friends = (LineFriend.query
               .filter_by(status=LINE_FRIEND_ACTIVE)
               .filter(LineFriend.step_set_id.isnot(None))
               .order_by(LineFriend.id)
               .all())
    creds_cache, steps_cache, client_cache = {}, {}, {}
    for friend in friends:
        if summary["sent"] + summary["failed"] >= limit:
            break
        summary["checked"] += 1
        step_set = friend.step_set
        if step_set is None or not step_set.is_live:
            continue
        if friend.client_id not in creds_cache:
            creds_cache[friend.client_id] = get_credentials(friend.client_id)
        creds = creds_cache[friend.client_id]
        if not creds:
            continue
        if step_set.id not in steps_cache:
            steps_cache[step_set.id] = normalized_steps(step_set)
        steps = steps_cache[step_set.id]
        if friend.next_step_index >= len(steps):
            friend.status = LINE_FRIEND_DONE
            db.session.commit()
            continue
        idx = friend.next_step_index
        step = steps[idx]
        try:
            due = due_at_utc(friend, step)
        except (ValueError, TypeError):
            continue
        if due > now:
            continue
        if friend.last_sent_at and now - friend.last_sent_at < MIN_GAP:
            continue
        if friend.client_id not in client_cache:
            from ..models import Client
            client_cache[friend.client_id] = Client.query.get(friend.client_id)
        client = client_cache[friend.client_id]

        result = _send_step(friend, step_set, idx, step, creds, client.name if client else "", now)
        if result == LINE_DELIVERY_SENT:
            summary["sent"] += 1
        elif result in (LINE_DELIVERY_FAILED, LINE_DELIVERY_GAVE_UP):
            summary["failed"] += 1
    return summary


def _claim(friend, step_set, idx):
    """この通の送信権を取る。取れなければ None（送信済み・他プロセスが送信中）。"""
    from sqlalchemy.exc import IntegrityError

    existing = LineDelivery.query.filter_by(friend_id=friend.id, step_index=idx).first()
    if existing is None:
        d = LineDelivery(client_id=friend.client_id, friend_id=friend.id, step_set_id=step_set.id,
                         step_index=idx, via="push", status=LINE_DELIVERY_SENDING, attempts=1)
        db.session.add(d)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return None
        return d
    if existing.status == LINE_DELIVERY_SENT:
        # reply で送信済みなのに進捗が進んでいない等の不整合を直す。
        friend.next_step_index = idx + 1
        db.session.commit()
        return None
    stale = (existing.status == LINE_DELIVERY_SENDING and existing.updated_at
             and datetime.utcnow() - existing.updated_at > STALE_SENDING)
    if existing.status != LINE_DELIVERY_FAILED and not stale:
        return None  # sending（他プロセスが送信中）/ gave_up
    # 再試行: 条件付き更新で、同時実行でも1プロセスだけが取る。
    updated = (LineDelivery.query
               .filter_by(id=existing.id, status=existing.status, attempts=existing.attempts)
               .update({"status": LINE_DELIVERY_SENDING, "attempts": existing.attempts + 1,
                        "via": "push"}, synchronize_session=False))
    db.session.commit()
    if not updated:
        return None
    db.session.refresh(existing)
    return existing


def _send_step(friend, step_set, idx, step, creds, client_name, now):
    delivery = _claim(friend, step_set, idx)
    if delivery is None:
        return None
    try:
        push(creds["access_token"], friend.line_user_id,
             build_messages(step, render_text(step["message"], friend, creds, client_name)),
             retry_key=retry_key_for(friend.id, idx))
    except Exception as e:
        delivery.error = str(e)[:500]
        if delivery.attempts >= MAX_ATTEMPTS:
            # 上限到達: この通は飛ばして次へ（1通の失敗で以降が全部止まるのを防ぐ）。
            delivery.status = LINE_DELIVERY_GAVE_UP
            _advance(friend, step_set, idx, now, sent=False)
        else:
            delivery.status = LINE_DELIVERY_FAILED
        db.session.commit()
        logger.warning("LINE push 失敗 friend=%s step=%s: %s", friend.id, idx, e)
        return delivery.status
    delivery.status = LINE_DELIVERY_SENT
    delivery.sent_at = now
    delivery.error = ""
    _advance(friend, step_set, idx, now, sent=True)
    db.session.commit()
    return LINE_DELIVERY_SENT


def _advance(friend, step_set, idx, now, sent):
    friend.next_step_index = idx + 1
    if sent:
        friend.last_sent_at = now
    if friend.next_step_index >= len(step_set.steps_json or []):
        friend.status = LINE_FRIEND_DONE


# ──────────────────────────────────────────────
# 集計（管理画面）
# ──────────────────────────────────────────────

def step_stats(client_id, step_set):
    """ステップごとの 送信数 / 失敗数。"""
    from sqlalchemy import func

    rows = (db.session.query(LineDelivery.step_index, LineDelivery.status, func.count(LineDelivery.id))
            .filter(LineDelivery.client_id == client_id, LineDelivery.step_set_id == step_set.id)
            .group_by(LineDelivery.step_index, LineDelivery.status)
            .all())
    stats = {}
    for idx, status, n in rows:
        s = stats.setdefault(idx, {"sent": 0, "failed": 0})
        if status == LINE_DELIVERY_SENT:
            s["sent"] += n
        elif status in (LINE_DELIVERY_FAILED, LINE_DELIVERY_GAVE_UP):
            s["failed"] += n
    return stats
