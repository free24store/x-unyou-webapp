"""E5-3: LINE Messaging API 実配信のテスト。

実行: python3 -m unittest discover -s tests -v
LINE への通信は line_service._api を差し替えて行わない（外部には一切つながない）。
"""
import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from datetime import datetime, date, time, timedelta
from unittest import mock

_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ.pop("LINE_CRON_SECRET", None)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (Client, User, SnsConnection, LineStepSet, LineFriend, LineDelivery,  # noqa: E402
                        ContactMessage)
from app.services import line_service as ls  # noqa: E402

SECRET = "test-channel-secret"
TOKEN = "test-access-token"


def utc_from_jst(y, m, d, hh, mm):
    return ls.to_utc_naive(datetime(y, m, d, hh, mm, tzinfo=ls.JST))


def sign(body):
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


STEPS = [
    {"day": 0, "send_at": "即時", "message": "{Nickname}さん、はじめまして。{AccountName}です。締切は{deadline}",
     "quick_replies": [{"label": "A", "tag": "A"}, {"label": "B", "tag": "B"}]},
    {"day": 0, "send_at": "20:30", "message": "2通目"},
    {"day": 1, "send_at": "20:30", "message": "3通目",
     "video_url": "https://example.com/v.mp4", "preview_url": "https://example.com/v.jpg"},
]


class FakeLine:
    """_api の差し替え。呼び出しを記録し、push を失敗させることもできる。"""

    def __init__(self):
        self.calls = []
        self.fail_push = False

    def __call__(self, method, path, token, payload=None, retry_key=None):
        self.calls.append({"method": method, "path": path, "payload": payload, "retry_key": retry_key})
        if path.startswith("/profile/"):
            return 200, {"displayName": "テスト太郎"}
        if path == "/message/push" and self.fail_push:
            raise ls.LineApiError(500, "boom")
        return 200, {}

    def of(self, path):
        return [c for c in self.calls if c["path"] == path]


class PureFunctionTests(unittest.TestCase):
    def test_signature(self):
        body = b'{"events":[]}'
        self.assertTrue(ls.verify_signature(SECRET, body, sign(body)))
        self.assertFalse(ls.verify_signature(SECRET, body, sign(b"x")))
        self.assertFalse(ls.verify_signature(SECRET, body, ""))

    def test_started_on_cutoff_1930_jst(self):
        self.assertEqual(ls.started_on_for(utc_from_jst(2026, 9, 21, 19, 29)), date(2026, 9, 21))
        self.assertEqual(ls.started_on_for(utc_from_jst(2026, 9, 21, 19, 30)), date(2026, 9, 22))
        # UTC では前日でも JST の日付で判定する
        self.assertEqual(ls.started_on_for(utc_from_jst(2026, 9, 21, 7, 0)), date(2026, 9, 21))

    def test_quiet_hours(self):
        self.assertTrue(ls.in_quiet_hours(utc_from_jst(2026, 9, 21, 22, 0)))
        self.assertTrue(ls.in_quiet_hours(utc_from_jst(2026, 9, 21, 6, 59)))
        self.assertFalse(ls.in_quiet_hours(utc_from_jst(2026, 9, 21, 7, 0)))
        self.assertFalse(ls.in_quiet_hours(utc_from_jst(2026, 9, 21, 20, 30)))

    def test_normalize_legacy_step(self):
        # コピー式時代の {day, timing, message} も読める
        self.assertEqual(ls.normalize_step({"day": 0, "timing": "登録直後", "message": "x"})["send_at"], "即時")
        self.assertEqual(ls.normalize_step({"day": 3, "timing": "3日後", "message": "x"})["send_at"], "20:30")

    def test_quick_reply_roundtrip(self):
        text = "A 売る物|tag:A\n相談する|text:相談希望\nはい"
        items = ls.parse_quick_replies(text)
        self.assertEqual(items, [{"label": "A 売る物", "tag": "A"},
                                 {"label": "相談する", "text": "相談希望"},
                                 {"label": "はい", "text": "はい"}])
        self.assertEqual(ls.format_quick_replies(items), text)

    def test_build_messages_video_and_quick_reply_on_last(self):
        step = ls.normalize_step(dict(STEPS[2], quick_replies=[{"label": "A", "tag": "A"}]))
        msgs = ls.build_messages(step, "本文")
        self.assertEqual([m["type"] for m in msgs], ["text", "video"])
        self.assertNotIn("quickReply", msgs[0])
        item = msgs[1]["quickReply"]["items"][0]["action"]
        self.assertEqual(item["type"], "postback")
        self.assertEqual(item["data"], "tag=A")

    def test_validate_step(self):
        ok = ls.normalize_step(STEPS[2])
        self.assertEqual(ls.validate_step(ok), [])
        bad = ls.normalize_step({"day": 1, "send_at": "25時", "message": "",
                                 "video_url": "http://x/v.mp4"})
        errs = ls.validate_step(bad)
        self.assertEqual(len(errs), 4)  # 時刻・本文・サムネ欠け・https

    def test_deadline_is_day6(self):
        self.assertEqual(ls.deadline_text(date(2026, 9, 21)), "9月27日(日) 23:59")

    def test_cron_times(self):
        ss = LineStepSet(steps_json=STEPS)
        self.assertEqual(ls.cron_times([ss]), ["20:30", "20:45"])


class LineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        if getattr(cls.app, "scheduler", None):
            cls.app.scheduler.shutdown(wait=False)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.client_obj = Client(name="SOMME")
        db.session.add(self.client_obj)
        db.session.commit()
        self.user = User(email="a@example.com", password_hash="x", role="admin",
                         client_id=self.client_obj.id, display_name="管理者")
        db.session.add(self.user)
        db.session.add(SnsConnection(client_id=self.client_obj.id, platform="line", is_active=True,
                                     credentials_json={"channel_secret": SECRET, "access_token": TOKEN,
                                                       "account_name": "SOMME事務局"}))
        self.step_set = LineStepSet(client_id=self.client_obj.id, title="v2", steps_json=STEPS,
                                    is_active=True, approved_at=datetime.utcnow())
        db.session.add(self.step_set)
        db.session.commit()
        self.fake = FakeLine()
        self.patcher = mock.patch.object(ls, "_api", self.fake)
        self.patcher.start()
        self.creds = ls.get_credentials(self.client_obj.id)

    def tearDown(self):
        self.patcher.stop()
        db.session.remove()
        self.ctx.pop()

    def follow(self, user_id="U1", at=None):
        at = at or utc_from_jst(2026, 9, 21, 12, 0)
        ls.handle_events(self.client_obj, self.creds,
                         [{"type": "follow", "replyToken": "r1", "source": {"userId": user_id}}], now=at)
        return LineFriend.query.filter_by(line_user_id=user_id).first()


class WebhookTests(LineTestCase):
    def post(self, events, client_id=None, signature=None):
        body = json.dumps({"events": events}).encode()
        return self.app.test_client().post(
            f"/line/webhook/{client_id or self.client_obj.id}", data=body,
            headers={"X-Line-Signature": signature if signature is not None else sign(body),
                     "Content-Type": "application/json"})

    def test_bad_signature_is_rejected(self):
        self.assertEqual(self.post([], signature="nope").status_code, 400)

    def test_unconfigured_client_is_404(self):
        self.assertEqual(self.post([], client_id=999).status_code, 404)

    def test_verify_button_empty_events(self):
        self.assertEqual(self.post([]).status_code, 200)

    def test_follow_via_http_replies_first_step_for_free(self):
        res = self.post([{"type": "follow", "replyToken": "rt", "source": {"userId": "U9"}}])
        self.assertEqual(res.status_code, 200)
        friend = LineFriend.query.filter_by(line_user_id="U9").one()
        self.assertEqual(friend.display_name, "テスト太郎")
        self.assertEqual(friend.next_step_index, 1)
        replies = self.fake.of("/message/reply")
        self.assertEqual(len(replies), 1)
        self.assertEqual(self.fake.of("/message/push"), [])  # 1通目は通数を消費しない
        text = replies[0]["payload"]["messages"][0]["text"]
        self.assertIn("テスト太郎さん", text)
        self.assertIn("SOMME事務局", text)
        self.assertNotIn("{deadline}", text)
        d = LineDelivery.query.one()
        self.assertEqual((d.via, d.status), ("reply", "sent"))


class DispatchTests(LineTestCase):
    def test_sends_on_time_once(self):
        friend = self.follow()
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 29))["sent"], 0)
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))["sent"], 1)
        # cron と APScheduler が重なっても二重送信しない
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 31))["sent"], 0)
        self.assertEqual(len(self.fake.of("/message/push")), 1)
        db.session.refresh(friend)
        self.assertEqual(friend.next_step_index, 2)
        # 3通目は翌日20:30。動画付き
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 22, 20, 30))["sent"], 1)
        last = self.fake.of("/message/push")[-1]["payload"]["messages"]
        self.assertEqual([m["type"] for m in last], ["text", "video"])
        db.session.refresh(friend)
        self.assertEqual(friend.status, "done")

    def test_concurrent_dispatch_does_not_resend(self):
        """別プロセスが先に送った／送信中の通は、進捗が古いままでも送らない。

        cron と APScheduler が同時に走ると、片方は「進捗が進む前の友だち」を読んでいる。
        そのとき頼りになるのは line_delivery の行だけ。
        """
        friend = self.follow()
        now = utc_from_jst(2026, 9, 21, 20, 30)
        # 相手プロセスが送信中
        d = LineDelivery(client_id=friend.client_id, friend_id=friend.id, step_set_id=self.step_set.id,
                         step_index=1, via="push", status="sending", attempts=1)
        db.session.add(d)
        db.session.commit()
        self.assertEqual(ls.dispatch_due(now)["sent"], 0)
        # 相手プロセスが送信完了したが、こちらが読んだ友だちの進捗はまだ 1 のまま
        d.status = "sent"
        db.session.commit()
        self.assertEqual(ls.dispatch_due(now)["sent"], 0)
        self.assertEqual(self.fake.of("/message/push"), [])
        db.session.refresh(friend)
        self.assertEqual(friend.next_step_index, 2)  # 不整合は直しておく

    def test_stale_sending_is_retried(self):
        """送信途中でプロセスが落ちて「送信中」のまま残った行は、時間が経てば再試行する。"""
        friend = self.follow()
        d = LineDelivery(client_id=friend.client_id, friend_id=friend.id, step_set_id=self.step_set.id,
                         step_index=1, via="push", status="sending", attempts=1,
                         updated_at=datetime.utcnow() - timedelta(minutes=30))
        db.session.add(d)
        db.session.commit()
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))["sent"], 1)
        db.session.refresh(d)
        self.assertEqual((d.status, d.attempts), ("sent", 2))

    def test_retry_key_is_stable(self):
        friend = self.follow()
        ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))
        self.assertEqual(self.fake.of("/message/push")[0]["retry_key"], ls.retry_key_for(friend.id, 1))

    def test_catch_up_sends_one_per_run_with_gap(self):
        self.follow()
        # 2日間止まっていた: 2通目・3通目とも期限切れだが、1回の実行では1通だけ
        now = utc_from_jst(2026, 9, 23, 20, 30)
        self.assertEqual(ls.dispatch_due(now)["sent"], 1)
        self.assertEqual(ls.dispatch_due(now + timedelta(minutes=5))["sent"], 0)  # 最小間隔
        self.assertEqual(ls.dispatch_due(now + timedelta(minutes=60))["sent"], 1)

    def test_quiet_hours(self):
        self.follow()
        res = ls.dispatch_due(utc_from_jst(2026, 9, 21, 23, 0))
        self.assertTrue(res["skipped_quiet"])
        self.assertEqual(self.fake.of("/message/push"), [])

    def test_unapproved_or_stopped_set_does_not_send(self):
        self.follow()
        self.step_set.is_active = False
        db.session.commit()
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))["sent"], 0)
        self.step_set.is_active = True
        self.step_set.approved_at = None
        db.session.commit()
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))["sent"], 0)

    def test_late_signup_starts_next_day(self):
        friend = self.follow(at=utc_from_jst(2026, 9, 21, 19, 45))
        self.assertEqual(friend.started_on, date(2026, 9, 22))
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))["sent"], 0)
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 22, 20, 30))["sent"], 1)

    def test_failure_retries_then_gives_up_and_moves_on(self):
        friend = self.follow()
        self.fake.fail_push = True
        t = utc_from_jst(2026, 9, 21, 20, 30)
        for i in range(3):
            ls.dispatch_due(t + timedelta(minutes=15 * i))
        d = LineDelivery.query.filter_by(friend_id=friend.id, step_index=1).one()
        self.assertEqual((d.status, d.attempts), ("gave_up", 3))
        db.session.refresh(friend)
        self.assertEqual(friend.next_step_index, 2)  # 1通の失敗で以降が止まらない
        self.fake.fail_push = False
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 22, 20, 30))["sent"], 1)

    def test_failed_first_reply_is_pushed_later(self):
        with mock.patch.object(ls, "reply", side_effect=ls.LineApiError(400, "expired")):
            friend = self.follow()
        self.assertEqual(friend.next_step_index, 0)
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 12, 5))["sent"], 1)
        d = LineDelivery.query.filter_by(friend_id=friend.id, step_index=0).one()
        self.assertEqual((d.via, d.status, d.attempts), ("push", "sent", 2))


class EventTests(LineTestCase):
    def event(self, ev, user_id="U1"):
        ev["source"] = {"userId": user_id}
        ls.handle_events(self.client_obj, self.creds, [ev], now=utc_from_jst(2026, 9, 21, 13, 0))

    def test_postback_tag(self):
        friend = self.follow()
        self.event({"type": "postback", "postback": {"data": "tag=A"}})
        self.event({"type": "postback", "postback": {"data": "tag=A"}})
        db.session.refresh(friend)
        self.assertEqual(friend.tags, ["A"])

    def test_consult_request_stops_steps_and_notifies(self):
        friend = self.follow()
        self.event({"type": "message", "replyToken": "r2", "message": {"type": "text", "text": "相談希望です"}})
        db.session.refresh(friend)
        self.assertEqual(friend.status, "converted")
        msg = ContactMessage.query.one()
        self.assertEqual(msg.source, "line")
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))["sent"], 0)
        # 自動返信は未設定なので返信しない（1通目の reply だけ）
        self.assertEqual(len(self.fake.of("/message/reply")), 1)
        # 2回送っても問い合わせは1件
        self.event({"type": "message", "message": {"type": "text", "text": "相談希望"}})
        self.assertEqual(ContactMessage.query.count(), 1)

    def test_other_text_is_ignored(self):
        friend = self.follow()
        self.event({"type": "message", "message": {"type": "text", "text": "こんにちは"}})
        db.session.refresh(friend)
        self.assertEqual(friend.status, "active")

    def test_unfollow_and_refollow_resumes_without_resending(self):
        friend = self.follow()
        self.event({"type": "unfollow"})
        db.session.refresh(friend)
        self.assertEqual(friend.status, "blocked")
        self.assertEqual(ls.dispatch_due(utc_from_jst(2026, 9, 21, 20, 30))["sent"], 0)
        self.follow()
        db.session.refresh(friend)
        self.assertEqual((friend.status, friend.next_step_index), ("active", 1))
        self.assertEqual(len(self.fake.of("/message/reply")), 1)  # 1通目を再送しない


class CronEndpointTests(LineTestCase):
    def test_secret_required(self):
        c = self.app.test_client()
        self.assertEqual(c.post("/line/cron/dispatch").status_code, 503)
        with mock.patch.dict(os.environ, {"LINE_CRON_SECRET": "s3cret"}):
            self.assertEqual(c.post("/line/cron/dispatch", headers={"X-Cron-Secret": "bad"}).status_code, 403)
            res = c.post("/line/cron/dispatch", headers={"X-Cron-Secret": "s3cret"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json()["status"], "ok")


class AdminTests(LineTestCase):
    def login(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(self.user.id)
            s["_fresh"] = True
        return c

    def test_pages_render(self):
        c = self.login()
        self.follow()
        for path in ("/admin/line-steps", "/admin/line-steps/settings", "/admin/line-friends",
                     f"/admin/line-steps/{self.step_set.id}/edit"):
            self.assertEqual(c.get(path).status_code, 200, path)

    def test_settings_keep_masked_secret(self):
        c = self.login()
        c.post("/admin/line-steps/settings", data={
            "channel_secret": ls.mask(SECRET), "access_token": "new-token", "account_name": "X"})
        creds = ls.get_credentials(self.client_obj.id)
        self.assertEqual(creds["channel_secret"], SECRET)
        self.assertEqual(creds["access_token"], "new-token")

    def test_edit_revokes_approval(self):
        c = self.login()
        data = {"title": "v2", "count": "3"}
        for i, st in enumerate(ls.normalized_steps(self.step_set)):
            data.update({f"day_{i}": st["day"], f"send_at_{i}": st["send_at"], f"message_{i}": st["message"],
                         f"video_url_{i}": st["video_url"], f"preview_url_{i}": st["preview_url"],
                         f"quick_replies_{i}": ls.format_quick_replies(st["quick_replies"])})
        data["message_1"] = "2通目（修正）"
        c.post(f"/admin/line-steps/{self.step_set.id}/edit", data=data)
        db.session.refresh(self.step_set)
        self.assertFalse(self.step_set.is_live)
        self.assertEqual(self.step_set.steps_json[1]["message"], "2通目（修正）")
        self.assertEqual(self.step_set.steps_json[0]["quick_replies"], STEPS[0]["quick_replies"])

    def test_approve_keeps_only_one_live_set(self):
        other = LineStepSet(client_id=self.client_obj.id, title="new", steps_json=STEPS)
        db.session.add(other)
        db.session.commit()
        self.login().post(f"/admin/line-steps/{other.id}/approve")
        db.session.refresh(other)
        db.session.refresh(self.step_set)
        self.assertTrue(other.is_live)
        self.assertFalse(self.step_set.is_active)

    def test_delete_blocked_while_assigned(self):
        self.follow()
        self.login().post(f"/admin/line-steps/{self.step_set.id}/delete")
        self.assertIsNotNone(LineStepSet.query.get(self.step_set.id))

    def test_test_send_goes_only_to_owner(self):
        c = self.login()
        c.post(f"/admin/line-steps/{self.step_set.id}/test/0")
        self.assertEqual(self.fake.of("/message/push"), [])  # 送信先未設定なら送らない
        friend = self.follow("U_OWNER")
        c.post(f"/admin/line-friends/{friend.id}/set-test")
        c.post(f"/admin/line-steps/{self.step_set.id}/test/0")
        pushes = self.fake.of("/message/push")
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["payload"]["to"], "U_OWNER")
        self.assertTrue(pushes[0]["payload"]["messages"][0]["text"].startswith("【テスト送信】"))
        self.assertEqual(LineDelivery.query.filter_by(via="push").count(), 0)  # 配信ログに残さない


if __name__ == "__main__":
    unittest.main()


class HealthzTests(LineTestCase):
    def test_reports_db_engine(self):
        """本番がSQLite（消える）かPostgres（消えない）かを外から確認できること。"""
        body = self.app.test_client().get("/healthz").get_json()
        self.assertEqual(body["db"], "ok")
        self.assertEqual(body["engine"], "sqlite")
        self.assertNotIn("DATABASE_URL", str(body))
