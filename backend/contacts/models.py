"""好友关系模型。

设计取舍
--------
* ``Friendship`` 只存一行（id 小者为 user_low），避免双向双行的不一致；
  查询好友时用 ``Q(user_low=me) | Q(user_high=me)``。
* ``FriendRequest`` 用 status 流转而非删除，保留申请历史；
  「不可重复发起 pending 申请」由视图校验 + 部分唯一索引意图保证。
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class FriendRequest(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_PENDING, "待处理"),
        (STATUS_ACCEPTED, "已接受"),
        (STATUS_REJECTED, "已拒绝"),
        (STATUS_BLOCKED, "已拉黑"),
    ]

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_friend_requests",
        verbose_name="发起人",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_friend_requests",
        verbose_name="接收人",
    )
    message = models.CharField("验证消息", max_length=140, blank=True)
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created = models.DateTimeField("创建时间", default=timezone.now)
    handled_at = models.DateTimeField("处理时间", null=True, blank=True)

    class Meta:
        verbose_name = "好友申请"
        verbose_name_plural = "好友申请"
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["to_user", "status"]),
            models.Index(fields=["from_user", "status"]),
        ]

    def __str__(self):
        return f"{self.from_user_id} → {self.to_user_id} [{self.status}]"


class Friendship(models.Model):
    """一条记录代表一对好友关系（无方向）。"""

    user_low = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="friendships_as_low",
        verbose_name="用户A",
    )
    user_high = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="friendships_as_high",
        verbose_name="用户B",
    )
    created = models.DateTimeField("成为好友时间", default=timezone.now)

    class Meta:
        verbose_name = "好友关系"
        verbose_name_plural = "好友关系"
        constraints = [
            models.UniqueConstraint(fields=["user_low", "user_high"], name="uniq_friendship_pair"),
            models.CheckConstraint(check=models.Q(user_low__lt=models.F("user_high")), name="friendship_low_lt_high"),
        ]

    def __str__(self):
        return f"{self.user_low_id} ↔ {self.user_high_id}"

    @staticmethod
    def pair(a, b):
        """把两个用户 id 归一化为 (小, 大)，保证存储唯一。"""
        low, high = sorted([a, b])
        return low, high

    @staticmethod
    def are_friends(a, b):
        if a == b:
            return False
        low, high = Friendship.pair(a, b)
        return Friendship.objects.filter(user_low_id=low, user_high_id=high).exists()

    @staticmethod
    def friend_ids(user_id):
        """返回该用户的全部好友 id，一次查询完成双向覆盖。"""
        from django.db.models import Q

        rows = Friendship.objects.filter(Q(user_low_id=user_id) | Q(user_high_id=user_id)).values_list(
            "user_low_id", "user_high_id"
        )
        return {high if low == user_id else low for low, high in rows}
