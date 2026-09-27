"""会话、消息、撤回、已读、附件测试。"""
import io
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from backend.chat.models import REVOKE_WINDOW, Attachment, Conversation, Membership, Message, MessageRead
from backend.realtime import stream
from backend.tests_utils import (
    api_patch,
    api_post,
    befriend,
    data_of,
    error_code,
    make_client,
    make_direct,
    make_group,
    make_user,
)


def png_bytes(size=(64, 48), color=(7, 193, 96)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class ConversationCreationTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.carol = make_user("carol", "卡罗")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)

    def test_direct_conversation_requires_friendship(self):
        response = api_post(self.alice_client, "/api/v1/conversations/", {
            "type": "direct", "peer_id": self.bob.id,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(error_code(response), "not_friends")

    def test_direct_conversation_is_idempotent(self):
        befriend(self.alice, self.bob)
        first = api_post(self.alice_client, "/api/v1/conversations/", {
            "type": "direct", "peer_id": self.bob.id,
        })
        self.assertEqual(first.status_code, 201)
        first_id = data_of(first)["conversation"]["id"]

        second = api_post(self.alice_client, "/api/v1/conversations/", {
            "type": "direct", "peer_id": self.bob.id,
        })
        self.assertEqual(second.status_code, 200)
        self.assertFalse(data_of(second)["created"])
        self.assertEqual(data_of(second)["conversation"]["id"], first_id)
        self.assertEqual(Conversation.objects.count(), 1)

        # 反向发起也复用同一会话
        third = api_post(self.bob_client, "/api/v1/conversations/", {
            "type": "direct", "peer_id": self.alice.id,
        })
        self.assertEqual(data_of(third)["conversation"]["id"], first_id)

    def test_cannot_start_conversation_with_self(self):
        response = api_post(self.alice_client, "/api/v1/conversations/", {
            "type": "direct", "peer_id": self.alice.id,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "self_conversation")

    def test_group_creation_adds_owner_and_system_message(self):
        response = api_post(self.alice_client, "/api/v1/conversations/", {
            "type": "group", "title": "三人行", "member_ids": [self.bob.id, self.carol.id],
        })
        self.assertEqual(response.status_code, 201)
        conversation = Conversation.objects.get(type=Conversation.TYPE_GROUP)
        self.assertEqual(conversation.owner_id, self.alice.id)
        self.assertEqual(conversation.memberships.count(), 3)
        owner_membership = conversation.memberships.get(user=self.alice)
        self.assertEqual(owner_membership.role, Membership.ROLE_OWNER)
        # 创建群聊会写入一条系统消息
        self.assertTrue(conversation.messages.filter(kind=Message.KIND_SYSTEM).exists())

    def test_group_requires_title(self):
        response = api_post(self.alice_client, "/api/v1/conversations/", {
            "type": "group", "member_ids": [self.bob.id],
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "missing_title")

    def test_conversation_list_shows_unread_and_last_message(self):
        conversation = make_direct(self.alice, self.bob)
        api_post(self.bob_client, f"/api/v1/conversations/{conversation.id}/messages/", {
            "body": "在吗",
        })
        response = self.alice_client.get("/api/v1/conversations/")
        items = data_of(response)["conversations"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["unread_count"], 1)
        self.assertEqual(items[0]["last_message"]["body"], "在吗")
        self.assertEqual(items[0]["title"], "小鲍")
        self.assertEqual(data_of(response)["unread_total"], 1)


class MessageTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.mallory = make_user("mallory", "马洛里")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.mallory_client = make_client(self.mallory)
        self.conversation = make_direct(self.alice, self.bob)

    def send(self, client, body, **extra):
        payload = {"body": body}
        payload.update(extra)
        return api_post(client, f"/api/v1/conversations/{self.conversation.id}/messages/", payload)

    def test_send_message_assigns_increasing_seq(self):
        first = self.send(self.alice_client, "第一条")
        second = self.send(self.alice_client, "第二条")
        self.assertEqual(data_of(first)["message"]["seq"], 1)
        self.assertEqual(data_of(second)["message"]["seq"], 2)
        self.assertEqual(Message.objects.filter(conversation=self.conversation).count(), 2)

    def test_non_member_cannot_read_or_send(self):
        """非成员一律 404，不暴露会话是否存在。"""
        read = self.mallory_client.get(f"/api/v1/conversations/{self.conversation.id}/messages/")
        self.assertEqual(read.status_code, 404)
        write = self.send(self.mallory_client, "偷看")
        self.assertEqual(write.status_code, 404)
        detail = self.mallory_client.get(f"/api/v1/conversations/{self.conversation.id}/")
        self.assertEqual(detail.status_code, 404)

    def test_client_msg_id_is_idempotent(self):
        first = self.send(self.alice_client, "只发一次", client_msg_id="dup-1")
        second = self.send(self.alice_client, "只发一次", client_msg_id="dup-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(data_of(second)["created"])
        self.assertEqual(Message.objects.filter(conversation=self.conversation).count(), 1)

    def test_empty_message_rejected(self):
        response = self.send(self.alice_client, "   ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "empty_message")

    def test_message_pagination_before_and_after(self):
        for index in range(5):
            self.send(self.alice_client, f"消息{index + 1}")

        latest = self.alice_client.get(
            f"/api/v1/conversations/{self.conversation.id}/messages/?limit=2"
        )
        latest_items = data_of(latest)["messages"]
        self.assertEqual([item["body"] for item in latest_items], ["消息4", "消息5"])
        self.assertTrue(data_of(latest)["has_more"])

        older = self.alice_client.get(
            f"/api/v1/conversations/{self.conversation.id}/messages/?limit=2&before_seq=4"
        )
        self.assertEqual([item["body"] for item in data_of(older)["messages"]], ["消息2", "消息3"])

        # after_seq 用于断线补齐，返回升序
        delta = self.alice_client.get(
            f"/api/v1/conversations/{self.conversation.id}/messages/?after_seq=3"
        )
        self.assertEqual([item["body"] for item in data_of(delta)["messages"]], ["消息4", "消息5"])

    def test_reply_to_must_be_in_same_conversation(self):
        other = make_direct(self.alice, self.mallory)
        other_message = Message.objects.create(conversation=other, seq=1, sender=self.mallory, body="别的会话")
        response = self.send(self.alice_client, "引用跨会话", reply_to_id=other_message.id)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(error_code(response), "reply_not_found")

    def test_group_message_shows_sender(self):
        group = make_group(self.alice, [self.bob], title="群")
        api_post(self.bob_client, f"/api/v1/conversations/{group.id}/messages/", {"body": "群里说话"})
        response = self.alice_client.get(f"/api/v1/conversations/{group.id}/messages/")
        items = data_of(response)["messages"]
        self.assertEqual(items[0]["sender"]["id"], self.bob.id)


class ReadStateTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.conversation = make_direct(self.alice, self.bob)

    def test_unread_increments_then_resets(self):
        api_post(self.bob_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {"body": "1"})
        api_post(self.bob_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {"body": "2"})
        membership = Membership.objects.get(conversation=self.conversation, user=self.alice)
        self.assertEqual(membership.unread_count, 2)

        response = api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/read/", {
            "up_to_seq": 2,
        })
        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.unread_count, 0)
        self.assertEqual(membership.last_read_seq, 2)

    def test_sender_keeps_zero_unread(self):
        api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {"body": "hi"})
        membership = Membership.objects.get(conversation=self.conversation, user=self.alice)
        self.assertEqual(membership.unread_count, 0)

    def test_last_read_seq_never_goes_backwards(self):
        api_post(self.bob_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {"body": "1"})
        api_post(self.bob_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {"body": "2"})
        api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/read/", {"up_to_seq": 2})
        # 回退请求不应把水位降下来
        response = api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/read/", {
            "up_to_seq": 1,
        })
        self.assertEqual(data_of(response)["last_read_seq"], 2)

    def test_direct_read_receipt_created_for_peer_message(self):
        message = Message.objects.create(
            conversation=self.conversation, seq=1, sender=self.bob, body="读我"
        )
        api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/read/", {
            "up_to_seq": 1,
        })
        self.assertTrue(MessageRead.objects.filter(message=message, user=self.alice).exists())

    def test_read_messages_include_receipts(self):
        api_post(self.bob_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {"body": "在吗"})
        api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/read/", {"up_to_seq": 1})
        response = self.alice_client.get(f"/api/v1/conversations/{self.conversation.id}/messages/")
        item = data_of(response)["messages"][0]
        reader_ids = [row["user_id"] for row in item["reads"]]
        self.assertIn(self.alice.id, reader_ids)

    def test_group_read_reports_reader_count(self):
        group = make_group(self.alice, [self.bob], title="群")
        api_post(self.alice_client, f"/api/v1/conversations/{group.id}/messages/", {"body": "群消息"})
        # 先有系统消息占位，取最后一条
        api_post(self.bob_client, f"/api/v1/conversations/{group.id}/read/", {"up_to_seq": 99})
        response = self.alice_client.get(f"/api/v1/conversations/{group.id}/messages/")
        last = data_of(response)["messages"][-1]
        self.assertEqual(last["reader_count"], 1)


class RevokeTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.carol = make_user("carol", "卡罗")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.conversation = make_direct(self.alice, self.bob)

    def make_message(self, sender=None):
        return Message.objects.create(
            conversation=self.conversation, seq=1, sender=sender or self.alice, body="要被撤回"
        )

    def test_sender_can_revoke(self):
        message = self.make_message()
        response = api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data_of(response)["message"]["revoked"])
        message.refresh_from_db()
        self.assertIsNotNone(message.revoked_at)
        # 正文与附件必须被抹除
        self.assertEqual(message.body, "")
        self.assertIsNone(message.attachment)
        self.assertEqual(data_of(response)["message"]["body"], "")

    def test_revoke_twice_is_idempotent(self):
        message = self.make_message()
        api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        response = api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data_of(response)["changed"])

    def test_other_member_cannot_revoke(self):
        """单聊里对方不是管理员，不能撤回我的消息。"""
        message = self.make_message()
        response = api_post(self.bob_client, f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(error_code(response), "revoke_forbidden")

    def test_unauthorised_third_party_still_403_after_revoke(self):
        """消息已被撤回后，无权者调用依然是 403（幂等只对有资格者成立）。"""
        message = self.make_message()
        api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        response = api_post(self.bob_client, f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 403)

    def test_sender_revoke_is_idempotent_after_revoke(self):
        """发送者本人重复撤回应幂等成功，而不是 403。"""
        message = self.make_message()
        api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        response = api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data_of(response)["changed"])

    def test_revoke_after_window_rejected(self):
        message = self.make_message()
        from django.utils import timezone

        Message.objects.filter(pk=message.pk).update(
            created_at=timezone.now() - REVOKE_WINDOW - REVOKE_WINDOW
        )
        response = api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 403)

    def test_group_admin_can_revoke_others_message(self):
        group = make_group(self.alice, [self.bob], title="群")
        Membership.objects.filter(conversation=group, user=self.alice).update(
            role=Membership.ROLE_ADMIN
        )
        message = Message.objects.create(conversation=group, seq=99, sender=self.bob, body="群成员的错话")
        response = api_post(self.alice_client, f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data_of(response)["message"]["revoked"])

    def test_non_member_gets_404(self):
        message = self.make_message()
        response = api_post(make_client(self.carol), f"/api/v1/messages/{message.id}/revoke/")
        self.assertEqual(response.status_code, 404)


class MembershipTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.carol = make_user("carol", "卡罗")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.group = make_group(self.alice, [self.bob], title="原始群")

    def test_admin_can_rename_group(self):
        response = api_patch(self.alice_client, f"/api/v1/conversations/{self.group.id}/", {
            "title": "改名后的群",
        })
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(self.group.title, "改名后的群")

    def test_regular_member_cannot_rename(self):
        response = api_patch(self.bob_client, f"/api/v1/conversations/{self.group.id}/", {
            "title": "我要改名",
        })
        self.assertEqual(response.status_code, 403)

    def test_pin_and_mute_are_per_user(self):
        api_patch(self.bob_client, f"/api/v1/conversations/{self.group.id}/", {
            "is_pinned": True, "is_muted": True,
        })
        bob_membership = Membership.objects.get(conversation=self.group, user=self.bob)
        alice_membership = Membership.objects.get(conversation=self.group, user=self.alice)
        self.assertTrue(bob_membership.is_pinned)
        self.assertTrue(bob_membership.is_muted)
        self.assertFalse(alice_membership.is_pinned)

    def test_add_members(self):
        response = api_post(self.alice_client, f"/api/v1/conversations/{self.group.id}/members/", {
            "member_ids": [self.carol.id],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.group.memberships.filter(user=self.carol).exists())

    def test_leave_group_transfers_ownership(self):
        response = api_post(self.alice_client, f"/api/v1/conversations/{self.group.id}/leave/")
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(self.group.owner_id, self.bob.id)
        self.assertFalse(self.group.memberships.filter(user=self.alice).exists())
        self.assertEqual(
            self.group.memberships.get(user=self.bob).role, Membership.ROLE_OWNER
        )

    def test_leave_direct_deletes_conversation(self):
        conversation = make_direct(self.alice, self.bob)
        conversation_id = conversation.id
        response = api_post(self.alice_client, f"/api/v1/conversations/{conversation_id}/leave/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Conversation.objects.filter(pk=conversation_id).exists())

    def test_member_can_remove_self(self):
        response = self.bob_client.delete(
            f"/api/v1/conversations/{self.group.id}/members/{self.bob.id}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.group.memberships.filter(user=self.bob).exists())


class TypingTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.conversation = make_direct(self.alice, self.bob)

    def test_typing_sets_state_and_publishes_event(self):
        # bob 先建立游标
        bob_bootstrap = self.bob_client.post(
            "/api/v1/updates/",
            data=json.dumps({"cursor": 0, "timeout": 0}),
            content_type="application/json",
        )
        cursor = data_of(bob_bootstrap)["cursor"]

        response = api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/typing/")
        self.assertEqual(response.status_code, 200)

        # 输入状态对他人可见
        self.assertIn(self.alice.id, stream.store.typing_users(self.conversation.id))

        events = self.bob_client.post(
            "/api/v1/updates/",
            data=json.dumps({"cursor": cursor, "timeout": 0}),
            content_type="application/json",
        )
        payloads = [event for event in data_of(events)["events"] if event["type"] == "typing"]
        self.assertTrue(payloads)
        self.assertEqual(payloads[0]["payload"]["user_id"], self.alice.id)

    def test_non_member_cannot_report_typing(self):
        carol = make_user("carol", "卡罗")
        response = api_post(
            make_client(carol), f"/api/v1/conversations/{self.conversation.id}/typing/"
        )
        self.assertEqual(response.status_code, 404)


class AttachmentTests(TestCase):
    def setUp(self):
        stream.store.clear()
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.carol = make_user("carol", "卡罗")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.conversation = make_direct(self.alice, self.bob)

    def upload_image(self, client):
        upload = SimpleUploadedFile("照片.png", png_bytes(), content_type="image/png")
        return client.post("/api/v1/attachments/", data={"file": upload})

    def test_upload_image_generates_thumbnail_and_dimensions(self):
        response = self.upload_image(self.alice_client)
        self.assertEqual(response.status_code, 201)
        attachment = data_of(response)["attachment"]
        self.assertTrue(attachment["is_image"])
        self.assertEqual(attachment["width"], 64)
        self.assertEqual(attachment["height"], 48)
        self.assertIsNotNone(attachment["thumb_url"])
        self.assertTrue(Attachment.objects.get(pk=attachment["id"]).thumb)

    def test_fake_image_rejected(self):
        """扩展名伪装成 png 的文本文件必须被拒。"""
        upload = SimpleUploadedFile("伪装.png", b"this is not an image", content_type="image/png")
        response = self.alice_client.post("/api/v1/attachments/", data={"file": upload})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "invalid_image")

    def test_oversized_file_rejected(self):
        from django.test import override_settings

        with override_settings(MAX_UPLOAD_SIZE=10):
            upload = SimpleUploadedFile("大文件.bin", b"x" * 100)
            response = self.alice_client.post("/api/v1/attachments/", data={"file": upload})
        self.assertEqual(response.status_code, 413)

    def test_send_image_message_and_download(self):
        attachment = data_of(self.upload_image(self.alice_client))["attachment"]
        response = api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {
            "attachment_id": attachment["id"],
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(data_of(response)["message"]["kind"], "image")

        # 会话成员可下载
        download = self.bob_client.get(f"/api/v1/attachments/{attachment['id']}/download/")
        self.assertEqual(download.status_code, 200)
        download.close()

        # 缩略图可下载
        thumb = self.bob_client.get(
            f"/api/v1/attachments/{attachment['id']}/download/?thumb=1"
        )
        self.assertEqual(thumb.status_code, 200)
        thumb.close()

    def test_non_member_cannot_download(self):
        attachment = data_of(self.upload_image(self.alice_client))["attachment"]
        api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {
            "attachment_id": attachment["id"],
        })
        response = make_client(self.carol).get(
            f"/api/v1/attachments/{attachment['id']}/download/"
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_download_before_message(self):
        attachment = data_of(self.upload_image(self.alice_client))["attachment"]
        response = self.alice_client.get(f"/api/v1/attachments/{attachment['id']}/download/")
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_cannot_reference_others_attachment(self):
        attachment = data_of(self.upload_image(self.alice_client))["attachment"]
        response = api_post(self.bob_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {
            "attachment_id": attachment["id"],
        })
        self.assertEqual(response.status_code, 403)

    def test_uploaded_filename_is_randomised(self):
        attachment = data_of(self.upload_image(self.alice_client))["attachment"]
        stored = Attachment.objects.get(pk=attachment["id"])
        self.assertNotIn("照片", stored.file.name)
        self.assertTrue(stored.file.name.endswith(".png"))
