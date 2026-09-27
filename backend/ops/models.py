"""运维域模型：管理员操作审计。

放在独立 app 而不是 chat 里，原因：
管理动作跨越用户、会话、消息多个领域，挂在任一业务 app 下都会造成反向依赖。
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


# 动作常量放在模块级：视图与测试都从这里导入，避免在类属性与模块常量间两处维护
ACTION_USER_BAN = "user.ban"
ACTION_USER_UNBAN = "user.unban"
ACTION_USER_REVOKE_TOKENS = "user.revoke_tokens"
ACTION_MESSAGE_REMOVE = "message.remove"
ACTION_REPORT_RESOLVE = "report.resolve"
ACTION_REPORT_DISMISS = "report.dismiss"
ACTION_CONVERSATION_VIEW = "conversation.view"


class AdminActionLog(models.Model):
    """管理员写操作审计流水。

    只记「谁在何时对什么做了什么」，不复制被操作对象的正文，
    既避免敏感内容二次留存，也避免日志表随消息增长而膨胀。
    """

    ACTION_CHOICES = [
        (ACTION_USER_BAN, "禁用用户"),
        (ACTION_USER_UNBAN, "解禁用户"),
        (ACTION_USER_REVOKE_TOKENS, "强制下线"),
        (ACTION_MESSAGE_REMOVE, "移除消息"),
        (ACTION_REPORT_RESOLVE, "举报结案"),
        (ACTION_REPORT_DISMISS, "举报驳回"),
        (ACTION_CONVERSATION_VIEW, "查看会话"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="admin_actions",
        verbose_name="操作人",
    )
    action = models.CharField("动作", max_length=32, choices=ACTION_CHOICES)
    target_type = models.CharField("对象类型", max_length=32, blank=True)
    target_id = models.BigIntegerField("对象 id", null=True, blank=True)
    detail = models.CharField("说明", max_length=300, blank=True)
    created = models.DateTimeField("时间", default=timezone.now)

    class Meta:
        verbose_name = "管理员操作日志"
        verbose_name_plural = "管理员操作日志"
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["actor", "-created"]),
            models.Index(fields=["-created"]),
            models.Index(fields=["action", "-created"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_id} on {self.target_type}:{self.target_id}"


def log_action(actor, action, target_type="", target_id=None, detail=""):
    """写一条审计记录。审计失败不应阻断业务动作。"""
    try:
        return AdminActionLog.objects.create(
            actor=actor,
            action=action,
            target_type=target_type or "",
            target_id=target_id,
            detail=(detail or "")[:300],
        )
    except Exception:  # pragma: no cover - 审计是旁路，不能影响主流程
        return None
