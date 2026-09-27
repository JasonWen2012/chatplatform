"""聊天核心模型：会话、成员、附件、消息、已读回执。

设计取舍
--------
* **会话内单调序号 ``seq``**：每条消息在会话内递增，既是稳定排序键，
  也是长轮询游标与未读/已读计算的唯一依据（避免用时间戳造成的歧义）。
  分配时对 ``Conversation`` 行加 ``select_for_update`` 锁，防止并发重号。
* **撤回不删行**：``revoked_at`` 置位，正文与附件指针在下发时抹除，
  既满足「消息已撤回」的展示，也保留审计线索。
* **附件不直接暴露 MEDIA_URL**：一律经 ``/api/v1/attachments/<id>/download/``
  鉴权下发，防止猜路径越权读取。
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# 撤回时限（微信同为 2 分钟）
REVOKE_WINDOW = timedelta(minutes=2)


def random_upload_name(original_name: str) -> str:
    """随机化落盘文件名，规避路径穿越与重名覆盖。"""
    suffix = ""
    if "." in original_name:
        suffix = "." + original_name.rsplit(".", 1)[1].lower()[:10]
    return f"{secrets.token_hex(16)}{suffix}"


def attachment_upload_to(instance, filename):
    now = timezone.now()
    return f"uploads/{now:%Y%m}/{random_upload_name(filename)}"


def thumb_upload_to(instance, filename):
    now = timezone.now()
    return f"thumbs/{now:%Y%m}/{random_upload_name(filename)}"


class Conversation(models.Model):
    TYPE_DIRECT = "direct"
    TYPE_GROUP = "group"
    TYPE_CHOICES = [(TYPE_DIRECT, "单聊"), (TYPE_GROUP, "群聊")]

    type = models.CharField("类型", max_length=16, choices=TYPE_CHOICES, default=TYPE_DIRECT)
    title = models.CharField("名称", max_length=64, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_conversations",
        verbose_name="群主",
    )
    avatar = models.ImageField("会话头像", upload_to="conversation_avatars/%Y%m/", blank=True, null=True)
    created = models.DateTimeField("创建时间", default=timezone.now)
    last_message_at = models.DateTimeField("最后消息时间", default=timezone.now)
    # 会话内消息序号水位；分配下一条消息时 +1
    last_seq = models.BigIntegerField("当前最大序号", default=0)

    class Meta:
        verbose_name = "会话"
        verbose_name_plural = "会话"
        ordering = ["-last_message_at"]
        indexes = [models.Index(fields=["-last_message_at"])]

    def __str__(self):
        return f"[{self.type}] {self.title or self.pk}"


class Membership(models.Model):
    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [(ROLE_OWNER, "群主"), (ROLE_ADMIN, "管理员"), (ROLE_MEMBER, "成员")]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="memberships", verbose_name="会话"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships", verbose_name="用户"
    )
    role = models.CharField("角色", max_length=16, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    joined_at = models.DateTimeField("加入时间", default=timezone.now)
    # 已读水位：小于等于该序号的消息视为已读
    last_read_seq = models.BigIntegerField("已读序号", default=0)
    # 冗余未读数，避免每次列表页做聚合查询
    unread_count = models.IntegerField("未读数", default=0)
    is_muted = models.BooleanField("免打扰", default=False)
    is_pinned = models.BooleanField("置顶", default=False)

    class Meta:
        verbose_name = "会话成员"
        verbose_name_plural = "会话成员"
        constraints = [
            models.UniqueConstraint(fields=["conversation", "user"], name="uniq_membership"),
        ]
        indexes = [
            models.Index(fields=["user", "-is_pinned"]),
            models.Index(fields=["conversation", "role"]),
        ]

    def __str__(self):
        return f"{self.user_id}@{self.conversation_id}({self.role})"

    @property
    def is_manager(self):
        """群主或管理员具备管理权限。"""
        return self.role in (self.ROLE_OWNER, self.ROLE_ADMIN)


class Attachment(models.Model):
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attachments", verbose_name="上传者"
    )
    file = models.FileField("文件", upload_to=attachment_upload_to)
    thumb = models.ImageField("缩略图", upload_to=thumb_upload_to, blank=True, null=True)
    original_name = models.CharField("原始文件名", max_length=255)
    size = models.BigIntegerField("大小(字节)", default=0)
    mime = models.CharField("MIME 类型", max_length=100, blank=True)
    width = models.IntegerField("宽", null=True, blank=True)
    height = models.IntegerField("高", null=True, blank=True)
    sha256 = models.CharField("SHA256", max_length=64, blank=True)
    created = models.DateTimeField("上传时间", default=timezone.now)

    class Meta:
        verbose_name = "附件"
        verbose_name_plural = "附件"
        ordering = ["-created"]

    def __str__(self):
        return f"{self.original_name} ({self.size}B)"

    @property
    def is_image(self):
        return bool(self.mime and self.mime.startswith("image/"))


class Message(models.Model):
    KIND_TEXT = "text"
    KIND_IMAGE = "image"
    KIND_FILE = "file"
    KIND_SYSTEM = "system"
    KIND_CHOICES = [
        (KIND_TEXT, "文本"),
        (KIND_IMAGE, "图片"),
        (KIND_FILE, "文件"),
        (KIND_SYSTEM, "系统消息"),
    ]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages", verbose_name="会话"
    )
    seq = models.BigIntegerField("会话内序号")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_messages",
        verbose_name="发送者",
    )
    kind = models.CharField("类型", max_length=16, choices=KIND_CHOICES, default=KIND_TEXT)
    body = models.TextField("正文", blank=True)
    attachment = models.ForeignKey(
        Attachment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        verbose_name="附件",
    )
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
        verbose_name="引用消息",
    )
    # 客户端生成的幂等键，网络重试不会产生重复消息
    client_msg_id = models.CharField("客户端消息ID", max_length=64, blank=True)
    created_at = models.DateTimeField("发送时间", default=timezone.now)
    revoked_at = models.DateTimeField("撤回时间", null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_messages",
        verbose_name="撤回人",
    )
    # 管理员移除（治理动作）：与「用户撤回」语义独立，两者互不覆盖
    removed_at = models.DateTimeField("管理员移除时间", null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="removed_messages",
        verbose_name="移除操作人",
    )

    class Meta:
        verbose_name = "消息"
        verbose_name_plural = "消息"
        ordering = ["conversation_id", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["conversation", "seq"], name="uniq_message_seq"),
        ]
        indexes = [
            models.Index(fields=["conversation", "seq"]),
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self):
        return f"#{self.conversation_id}:{self.seq} {self.kind}"

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    @property
    def is_removed(self):
        return self.removed_at is not None

    @property
    def is_hidden(self):
        """内容不可见：无论用户撤回还是管理员移除。"""
        return self.is_revoked or self.is_removed

    @property
    def revoke_deadline(self):
        return self.created_at + REVOKE_WINDOW

    def can_revoke(self, user, membership=None):
        """发送者本人随时可撤回（限时），群管理员在限时内也可撤回。"""
        if self.is_revoked or user is None:
            return False
        if timezone.now() > self.revoke_deadline:
            return False
        if self.sender_id == user.id:
            return True
        return bool(membership and membership.is_manager)

    def can_remove(self):
        """管理员移除的**业务**前置条件：已撤回的消息视为终态，不再移除。"""
        return not self.is_removed and not self.is_revoked


class MessageRead(models.Model):
    """单聊已读回执：每条消息每个读者一行。

    群聊不逐条记录（会随人数×消息数膨胀），改用 ``Membership.last_read_seq``
    推算「N 人已读」。
    """

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reads", verbose_name="消息")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_reads", verbose_name="读者"
    )
    read_at = models.DateTimeField("已读时间", default=timezone.now)

    class Meta:
        verbose_name = "已读回执"
        verbose_name_plural = "已读回执"
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="uniq_message_read"),
        ]
        indexes = [models.Index(fields=["message", "user"])]

    def __str__(self):
        return f"{self.message_id} read by {self.user_id}"


class MessageReport(models.Model):
    """用户对消息的举报，供管理界面处理。

    只做记录与流转，不自动处置内容：
    是否移除由管理员在 /manage/reports/ 判断，避免误伤。
    """

    STATUS_PENDING = "pending"
    STATUS_RESOLVED = "resolved"
    STATUS_DISMISSED = "dismissed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "待处理"),
        (STATUS_RESOLVED, "已处理"),
        (STATUS_DISMISSED, "已驳回"),
    ]

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports_filed",
        verbose_name="举报人",
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="reports", verbose_name="被举报消息"
    )
    reason = models.CharField("理由", max_length=200, blank=True)
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created = models.DateTimeField("举报时间", default=timezone.now)
    handled_at = models.DateTimeField("处理时间", null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports_handled",
        verbose_name="处理人",
    )

    class Meta:
        verbose_name = "消息举报"
        verbose_name_plural = "消息举报"
        ordering = ["-created"]
        constraints = [
            # 同一人对同一消息只能举报一次，避免刷举报
            models.UniqueConstraint(fields=["reporter", "message"], name="uniq_report_per_user_message"),
        ]
        indexes = [
            models.Index(fields=["status", "-created"]),
            models.Index(fields=["message"]),
        ]

    def __str__(self):
        return f"report#{self.pk} by {self.reporter_id} on msg {self.message_id} [{self.status}]"
