"""全局搜索测试。

**最关键的用例是越权防护**：搜索消息时只能命中本人参与的会话，
否则任何用户都能搜到别人的私聊内容。
"""
from django.test import TestCase

from backend.tests_utils import (
    api_post,
    data_of,
    error_code,
    make_client,
    make_direct,
    make_group,
    make_user,
)


class SearchPermissionTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.outsider = make_user("mallory", "马洛里")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.outsider_client = make_client(self.outsider)

        # alice 与 bob 的私聊，含关键词
        self.conversation = make_direct(self.alice, self.bob)
        api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {
            "body": "今天讨论一下机密方案",
        })

        # outsider 与他人另有一个会话，同样含相同关键词
        self.other_conversation = make_direct(self.outsider, self.bob)
        api_post(self.outsider_client, f"/api/v1/conversations/{self.other_conversation.id}/messages/", {
            "body": "机密方案在另一个会话里",
        })

    def test_search_only_returns_own_conversations(self):
        response = self.alice_client.get("/api/v1/search/?q=机密方案")
        self.assertEqual(response.status_code, 200)
        messages = data_of(response)["messages"]
        conversation_ids = {row["conversation"]["id"] for row in messages}
        self.assertEqual(conversation_ids, {self.conversation.id})
        # 绝不能出现他人会话
        self.assertNotIn(self.other_conversation.id, conversation_ids)

    def test_outsider_cannot_see_alice_message(self):
        response = self.outsider_client.get("/api/v1/search/?q=机密方案")
        messages = data_of(response)["messages"]
        conversation_ids = {row["conversation"]["id"] for row in messages}
        self.assertNotIn(self.conversation.id, conversation_ids)
        self.assertEqual(conversation_ids, {self.other_conversation.id})

    def test_group_message_searchable_by_member(self):
        group = make_group(self.alice, [self.bob], title="项目群")
        api_post(self.alice_client, f"/api/v1/conversations/{group.id}/messages/", {
            "body": "群里的独特关键词ZZZ",
        })
        response = self.bob_client.get("/api/v1/search/?q=独特关键词ZZZ")
        ids = {row["conversation"]["id"] for row in data_of(response)["messages"]}
        self.assertIn(group.id, ids)

    def test_non_member_cannot_search_group_message(self):
        group = make_group(self.alice, [self.bob], title="项目群")
        api_post(self.alice_client, f"/api/v1/conversations/{group.id}/messages/", {
            "body": "群里的独有词QQQ",
        })
        response = self.outsider_client.get("/api/v1/search/?q=独有词QQQ")
        self.assertEqual(data_of(response)["messages"], [])


class SearchBehaviourTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)
        self.conversation = make_direct(self.alice, self.bob)
        for text in ["苹果很好吃", "香蕉也不错", "苹果派最爱"]:
            api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {
                "body": text,
            })

    def test_matches_multiple_messages(self):
        response = self.alice_client.get("/api/v1/search/?q=苹果")
        rows = data_of(response)["messages"]
        self.assertEqual(len(rows), 2)

    def test_empty_query_rejected(self):
        response = self.alice_client.get("/api/v1/search/?q=")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "missing_query")

    def test_overlong_query_rejected(self):
        response = self.alice_client.get(f"/api/v1/search/?q={'x' * 80}")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "invalid_query")

    def test_invalid_type_rejected(self):
        response = self.alice_client.get("/api/v1/search/?q=苹果&type=unknown")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "invalid_type")

    def test_type_filter_limits_sections(self):
        response = self.alice_client.get("/api/v1/search/?q=苹果&type=message")
        payload = data_of(response)
        self.assertIn("messages", payload)
        self.assertNotIn("users", payload)
        self.assertNotIn("conversations", payload)

    def test_limit_is_capped(self):
        response = self.alice_client.get("/api/v1/search/?q=苹果&limit=9999")
        self.assertEqual(response.status_code, 200)

    def test_user_search_excludes_self(self):
        response = self.alice_client.get("/api/v1/search/?q=alice&type=user")
        ids = [row["id"] for row in data_of(response)["users"]]
        self.assertNotIn(self.alice.id, ids)

    def test_requires_authentication(self):
        from django.test import Client

        self.assertEqual(Client().get("/api/v1/search/?q=x").status_code, 401)


class ReplyQuoteTests(TestCase):
    """引用回复必须展示真实内容，且被引用消息不可见时不能泄露原文。"""

    def setUp(self):
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.conversation = make_direct(self.alice, self.bob)

    def send(self, body, **extra):
        payload = {"body": body}
        payload.update(extra)
        return api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/messages/", payload)

    def test_reply_includes_excerpt_and_sender(self):
        first = self.send("被引用的原始内容")
        first_id = data_of(first)["message"]["id"]
        second = self.send("这是回复", reply_to_id=first_id)
        reply = data_of(second)["message"]["reply_to"]
        self.assertIsNotNone(reply)
        self.assertEqual(reply["excerpt"], "被引用的原始内容")
        self.assertEqual(reply["sender"]["id"], self.alice.id)
        self.assertFalse(reply["hidden"])

    def test_long_excerpt_is_truncated(self):
        first = self.send("长" * 120)
        first_id = data_of(first)["message"]["id"]
        second = self.send("回复", reply_to_id=first_id)
        excerpt = data_of(second)["message"]["reply_to"]["excerpt"]
        self.assertLessEqual(len(excerpt), 61)  # 60 字 + 省略号

    def test_image_reply_uses_placeholder(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (30, 30), (1, 2, 3)).save(buffer, format="PNG")
        upload = SimpleUploadedFile("pic.png", buffer.getvalue(), content_type="image/png")
        attachment = data_of(self.alice_client.post("/api/v1/attachments/", data={"file": upload}))["attachment"]
        image_msg = self.send("", attachment_id=attachment["id"])
        image_id = data_of(image_msg)["message"]["id"]
        reply = self.send("回复图片", reply_to_id=image_id)
        self.assertEqual(data_of(reply)["message"]["reply_to"]["excerpt"], "[图片]")

    def test_reply_to_revoked_message_hides_content(self):
        first = self.send("即将被撤回的内容")
        first_id = data_of(first)["message"]["id"]
        self.alice_client.post(f"/api/v1/messages/{first_id}/revoke/")
        second = self.send("回复已撤回的消息", reply_to_id=first_id)
        reply = data_of(second)["message"]["reply_to"]
        self.assertTrue(reply["hidden"])
        self.assertEqual(reply["excerpt"], "")
        self.assertNotIn("即将被撤回的内容", str(reply))

    def test_list_messages_includes_reply_summary(self):
        first = self.send("原始消息")
        first_id = data_of(first)["message"]["id"]
        self.send("回复消息", reply_to_id=first_id)
        response = self.alice_client.get(f"/api/v1/conversations/{self.conversation.id}/messages/")
        rows = data_of(response)["messages"]
        self.assertIsNotNone(rows[-1]["reply_to"])
        self.assertEqual(rows[-1]["reply_to"]["excerpt"], "原始消息")


class ReportTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.outsider = make_user("mallory", "马洛里")
        self.alice_client = make_client(self.alice)
        self.bob_client = make_client(self.bob)
        self.outsider_client = make_client(self.outsider)
        self.conversation = make_direct(self.alice, self.bob)
        self.message = data_of(api_post(
            self.alice_client,
            f"/api/v1/conversations/{self.conversation.id}/messages/",
            {"body": "可能违规的内容"},
        ))["message"]

    def report(self, client, reason="垃圾信息"):
        return api_post(client, f"/api/v1/messages/{self.message['id']}/report/", {"reason": reason})

    def test_member_can_report(self):
        response = self.report(self.bob_client)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(data_of(response)["report"]["status"], "pending")

    def test_duplicate_report_conflicts(self):
        self.report(self.bob_client)
        response = self.report(self.bob_client)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(error_code(response), "already_reported")

    def test_cannot_report_own_message(self):
        response = self.report(self.alice_client)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "self_report")

    def test_non_member_gets_404(self):
        response = self.report(self.outsider_client)
        self.assertEqual(response.status_code, 404)

    def test_report_does_not_change_message(self):
        """举报只是记录，不能自动处置内容。"""
        self.report(self.bob_client)
        response = self.alice_client.get(
            f"/api/v1/conversations/{self.conversation.id}/messages/"
        )
        row = data_of(response)["messages"][-1]
        self.assertEqual(row["body"], "可能违规的内容")
        self.assertFalse(row["removed"])
