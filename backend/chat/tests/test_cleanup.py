"""附件回收命令测试。

要点：绝不能误删「已上传但尚未发出消息」的新附件，
也不能动仍被消息引用的附件。
"""
import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from backend.chat.models import Attachment, Message
from backend.tests_utils import api_post, data_of, make_client, make_direct, make_user


def quiet(*args, **kwargs):
    """执行管理命令并吞掉输出，保持测试日志干净。"""
    kwargs.setdefault("stdout", io.StringIO())
    kwargs.setdefault("stderr", io.StringIO())
    return call_command(*args, **kwargs)


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(user_client, name="f.png"):
    return data_of(user_client.post(
        "/api/v1/attachments/",
        data={"file": SimpleUploadedFile(name, png_bytes(), content_type="image/png")},
    ))["attachment"]


class CleanupAttachmentsTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice", "爱丽丝")
        self.bob = make_user("bob", "小鲍")
        self.alice_client = make_client(self.alice)
        self.conversation = make_direct(self.alice, self.bob)

    def age(self, attachment_id, hours):
        """把创建时间往前推，模拟历史数据。"""
        Attachment.objects.filter(pk=attachment_id).update(
            created=timezone.now() - timedelta(hours=hours)
        )

    def test_referenced_attachment_is_kept(self):
        attachment = upload(self.alice_client)
        api_post(self.alice_client, f"/api/v1/conversations/{self.conversation.id}/messages/", {
            "attachment_id": attachment["id"],
        })
        self.age(attachment["id"], 48)

        quiet("cleanup_attachments", verbosity=0)
        self.assertTrue(
            Attachment.objects.filter(pk=attachment["id"]).exists(),
            "被消息引用的附件必须保留",
        )

    def test_old_orphan_is_removed(self):
        attachment = upload(self.alice_client)
        self.age(attachment["id"], 48)

        quiet("cleanup_attachments", verbosity=0)
        self.assertFalse(Attachment.objects.filter(pk=attachment["id"]).exists())

    def test_recent_orphan_is_kept(self):
        """刚上传还没发送的附件不能被当成孤儿删掉。"""
        attachment = upload(self.alice_client)

        quiet("cleanup_attachments", verbosity=0)
        self.assertTrue(
            Attachment.objects.filter(pk=attachment["id"]).exists(),
            "24 小时内的附件必须保留，否则会误删用户刚要发送的文件",
        )

    def test_dry_run_does_not_delete(self):
        attachment = upload(self.alice_client)
        self.age(attachment["id"], 48)
        stored = Attachment.objects.get(pk=attachment["id"])
        file_name = stored.file.name

        quiet("cleanup_attachments", "--dry-run", verbosity=0)
        self.assertTrue(Attachment.objects.filter(pk=attachment["id"]).exists())
        self.assertTrue(stored.file.storage.exists(file_name), "dry-run 不应删除磁盘文件")

    def test_older_than_threshold_respected(self):
        attachment = upload(self.alice_client)
        self.age(attachment["id"], 3)

        # 阈值 1 小时：3 小时前的孤儿应被回收
        quiet("cleanup_attachments", "--older-than", "1", verbosity=0)
        self.assertFalse(Attachment.objects.filter(pk=attachment["id"]).exists())

    def test_orphan_file_sweep(self):
        """磁盘上存在但数据库无记录的文件应被清理。"""
        import os

        from django.conf import settings

        attachment = upload(self.alice_client)
        stored = Attachment.objects.get(pk=attachment["id"])
        uploads_dir = os.path.join(str(settings.MEDIA_ROOT), "uploads")
        orphan_path = os.path.join(uploads_dir, "orphan_test_file.bin")
        os.makedirs(uploads_dir, exist_ok=True)
        with open(orphan_path, "wb") as handle:
            handle.write(b"x")
        # 让它的 mtime 早于阈值
        old = (timezone.now() - timedelta(hours=48)).timestamp()
        os.utime(orphan_path, (old, old))

        quiet("cleanup_attachments", "--older-than", "1", "--orphan-files", verbosity=0)

        self.assertFalse(os.path.exists(orphan_path), "磁盘孤儿文件应被清理")
        self.assertTrue(stored.file.storage.exists(stored.file.name), "数据库有记录的文件必须保留")

    def test_cleaning_does_not_break_message(self):
        """删除孤儿附件不能影响消息本身的可见性。"""
        attachment = upload(self.alice_client)
        message = data_of(api_post(
            self.alice_client,
            f"/api/v1/conversations/{self.conversation.id}/messages/",
            {"attachment_id": attachment["id"]},
        ))["message"]
        self.age(attachment["id"], 48)

        quiet("cleanup_attachments", verbosity=0)

        row = Message.objects.get(pk=message["id"])
        self.assertIsNotNone(row.attachment_id, "引用中的附件不应被清空")
