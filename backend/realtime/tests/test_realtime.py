"""实时事件流（长轮询）测试。"""
import json
import time

from django.test import TestCase

from backend.realtime import stream
from backend.tests_utils import data_of, make_client, make_direct, make_user


def poll(client, cursor, timeout=0):
    return client.post(
        "/api/v1/updates/",
        data=json.dumps({"cursor": cursor, "timeout": timeout}),
        content_type="application/json",
    )


class StreamStoreTests(TestCase):
    """事件缓冲的单元测试：不经过 HTTP，直接验证游标与广播语义。"""

    def setUp(self):
        stream.store.clear()

    def test_publish_assigns_monotonic_event_ids(self):
        first = stream.publish(1, "message.new", {"body": "a"})
        second = stream.publish(1, "message.new", {"body": "b"})
        self.assertEqual(first[0]["event_id"], 1)
        self.assertEqual(second[0]["event_id"], 2)

    def test_fetch_returns_only_newer_events(self):
        stream.publish(1, "message.new", {"body": "a"})
        stream.publish(1, "message.new", {"body": "b"})
        events = stream.store.fetch(1, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["body"], "b")

    def test_events_are_isolated_per_user(self):
        stream.publish(1, "message.new", {"body": "给1号"})
        stream.publish(2, "message.new", {"body": "给2号"})
        self.assertEqual(len(stream.store.fetch(1, 0)), 1)
        self.assertEqual(stream.store.fetch(1, 0)[0]["payload"]["body"], "给1号")
        self.assertEqual(stream.store.fetch(2, 0)[0]["payload"]["body"], "给2号")

    def test_publish_many_fans_out_same_event_id(self):
        events = stream.publish_many([1, 2, 3], "message.new", {"body": "群发"})
        event_id = events[0]["event_id"]
        for user_id in (1, 2, 3):
            fetched = stream.store.fetch(user_id, 0)
            self.assertEqual(len(fetched), 1)
            self.assertEqual(fetched[0]["event_id"], event_id)

    def test_typing_has_ttl(self):
        stream.store.set_typing(10, 5, ttl=0.1)
        self.assertIn(5, stream.store.typing_users(10))
        time.sleep(0.15)
        self.assertEqual(stream.store.typing_users(10), [])

    def test_typing_excludes_self(self):
        stream.store.set_typing(10, 5)
        self.assertEqual(stream.store.typing_users(10, exclude_user_id=5), [])


class LongPollingTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)

    def test_cursor_defaults_to_current_watermark(self):
        """首次不带游标时不应回放历史事件。"""
        stream.publish(self.alice.id, "message.new", {"body": "旧事件"})
        response = self.alice_client.post(
            "/api/v1/updates/",
            data=json.dumps({"timeout": 0}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data_of(response)["events"], [])

    def test_timeout_returns_empty_events(self):
        response = poll(self.alice_client, 0, timeout=0)
        payload = data_of(response)
        self.assertEqual(payload["events"], [])
        self.assertTrue(payload["timed_out"])

    def test_new_event_returned_and_cursor_advances(self):
        stream.publish(self.alice.id, "message.new", {"body": "新事件"})
        response = poll(self.alice_client, 0, timeout=0)
        payload = data_of(response)
        self.assertFalse(payload["timed_out"])
        self.assertEqual(len(payload["events"]), 1)
        event = payload["events"][0]
        self.assertEqual(event["type"], "message.new")
        self.assertEqual(event["payload"]["body"], "新事件")
        # 游标推进后不应重复投递同一事件
        self.assertEqual(payload["cursor"], event["event_id"])
        again = poll(self.alice_client, payload["cursor"], timeout=0)
        self.assertEqual(data_of(again)["events"], [])

    def test_negative_cursor_rejected(self):
        response = poll(self.alice_client, -1, timeout=0)
        self.assertEqual(response.status_code, 400)

    def test_invalid_cursor_rejected(self):
        response = self.alice_client.post(
            "/api/v1/updates/",
            data=json.dumps({"cursor": "abc", "timeout": 0}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_requires_authentication(self):
        from django.test import Client

        response = Client().post(
            "/api/v1/updates/",
            data=json.dumps({"cursor": 0, "timeout": 0}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_message_send_reaches_peer_stream(self):
        """发消息后，对方的长轮询能看到 message.new 事件。"""
        conversation = make_direct(self.alice, self.bob)
        bootstrap = poll(self.bob_client, 0, timeout=0)
        cursor = data_of(bootstrap)["cursor"]

        self.alice_client.post(
            f"/api/v1/conversations/{conversation.id}/messages/",
            data=json.dumps({"body": "你好"}),
            content_type="application/json",
        )

        response = poll(self.bob_client, cursor, timeout=0)
        events = data_of(response)["events"]
        new_messages = [event for event in events if event["type"] == "message.new"]
        self.assertEqual(len(new_messages), 1)
        self.assertEqual(new_messages[0]["payload"]["body"], "你好")
        self.assertEqual(new_messages[0]["conversation_id"], conversation.id)

    def test_zero_timeout_returns_immediately(self):
        """timeout=0 必须立即返回。

        这是曾经踩过的坑：写成 ``body.get("timeout") or max_timeout`` 时，
        0 被当作假值回落到 25 秒上限，客户端就被挂住了。
        """
        started = time.monotonic()
        response = poll(self.alice_client, 0, timeout=0)
        elapsed = time.monotonic() - started
        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 2, "timeout=0 应立即返回，而不是等待默认上限")

    def test_negative_timeout_rejected(self):
        response = poll(self.alice_client, 0, timeout=-5)
        self.assertEqual(response.status_code, 400)

    def test_timeout_is_capped(self):
        """客户端请求超长等待时，服务端按配置上限截断。"""
        from django.test import override_settings

        with override_settings(LONGPOLL_TIMEOUT_SECONDS=0):
            started = time.monotonic()
            response = poll(self.alice_client, 0, timeout=30)
            elapsed = time.monotonic() - started
        self.assertEqual(response.status_code, 200)
        # 上限为 0，应立即返回而不是等 30 秒
        self.assertLess(elapsed, 5)

    def test_revoke_publishes_event(self):
        from backend.chat.models import Message

        conversation = make_direct(self.alice, self.bob)
        message = Message.objects.create(
            conversation=conversation, seq=1, sender=self.alice, body="撤回我"
        )
        cursor = data_of(poll(self.bob_client, 0, timeout=0))["cursor"]
        self.alice_client.post(f"/api/v1/messages/{message.id}/revoke/")
        events = data_of(poll(self.bob_client, cursor, timeout=0))["events"]
        revoked = [event for event in events if event["type"] == "message.revoked"]
        self.assertEqual(len(revoked), 1)
        self.assertTrue(revoked[0]["payload"]["message"]["revoked"])


class PresenceTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)

    def test_presence_lists_requested_users(self):
        stream.store.set_online(self.bob.id)
        response = self.alice_client.get(f"/api/v1/presence/?user_ids={self.alice.id},{self.bob.id}")
        self.assertEqual(response.status_code, 200)
        rows = {item["user_id"]: item for item in data_of(response)["presence"]}
        self.assertTrue(rows[self.bob.id]["is_online"])
        self.assertFalse(rows[self.alice.id]["is_online"])

    def test_presence_requires_ids(self):
        response = self.alice_client.get("/api/v1/presence/")
        self.assertEqual(response.status_code, 400)
