"""用户与访问令牌模型。"""
import random
import secrets

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

# 注册时分配随机昵称，避免用户名直接暴露
_NICK_PREFIX = [
    "安静", "明亮", "沉静", "晴朗", "温柔", "勇敢", "自由", "悠然",
    "追风", "听雨", "观星", "逐月", "清晨", "黄昏", "微光", "远山",
]
_NICK_SUFFIX = [
    "的猫", "的鹿", "的海", "的树", "的旅人", "的茶", "的云", "的风",
    "小站", "微光", "笔记", "信箱",
]


def random_nickname():
    return f"{random.choice(_NICK_PREFIX)}{random.choice(_NICK_SUFFIX)}{random.randint(10, 99)}"


def generate_token_key():
    """43 字符 urlsafe 随机串（256 bit 熵）。"""
    return secrets.token_urlsafe(32)


class User(AbstractUser):
    """自定义用户：在 Django 内置字段上补充聊天所需属性。"""

    nickname = models.CharField("昵称", max_length=32, blank=True)
    avatar = models.ImageField("头像", upload_to="avatars/%Y%m/", blank=True, null=True)
    bio = models.CharField("个性签名", max_length=140, blank=True)
    is_online = models.BooleanField("在线", default=False)
    last_seen = models.DateTimeField("最后活跃", default=timezone.now)

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        """优先展示昵称，其次用户名。"""
        return self.nickname or self.username

    def mark_seen(self):
        User.objects.filter(pk=self.pk).update(last_seen=timezone.now(), is_online=True)


class Token(models.Model):
    """不透明访问令牌，支持多端并存与单独撤销。

    选择自建模型而非第三方 JWT 库，是为了零额外依赖且可精确控制吊销语义。
    """

    key = models.CharField("令牌", max_length=48, primary_key=True, default=generate_token_key, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tokens", verbose_name="用户")
    created = models.DateTimeField("创建时间", default=timezone.now)
    last_used = models.DateTimeField("最后使用", default=timezone.now)
    revoked = models.BooleanField("已撤销", default=False)

    class Meta:
        verbose_name = "访问令牌"
        verbose_name_plural = "访问令牌"
        ordering = ["-created"]
        indexes = [models.Index(fields=["user", "revoked"])]

    def __str__(self):
        return f"{self.user_id}:{self.key[:8]}…"


# 偏好字段的合法取值：视图与前端共用同一份定义，避免两处漂移
THEME_CHOICES = [("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")]
FONT_SIZE_CHOICES = [("small", "小"), ("normal", "标准"), ("large", "大")]
BUBBLE_STYLE_CHOICES = [("classic", "经典"), ("compact", "紧凑")]


class UserPreference(models.Model):
    """用户偏好，先存服务端。

    放在服务端而不是 localStorage 的原因：后续 App 与网页端需要共享同一份设置，
    客户端各自持久化会导致两端表现不一致。
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="preference", verbose_name="用户"
    )
    theme = models.CharField("主题", max_length=16, choices=THEME_CHOICES, default="system")
    font_size = models.CharField("字号", max_length=16, choices=FONT_SIZE_CHOICES, default="normal")
    bubble_style = models.CharField(
        "气泡样式", max_length=16, choices=BUBBLE_STYLE_CHOICES, default="classic"
    )
    notify_sound = models.BooleanField("提示音", default=True)
    notify_desktop = models.BooleanField("桌面通知", default=False)
    updated_at = models.DateTimeField("更新时间", default=timezone.now)

    class Meta:
        verbose_name = "用户偏好"
        verbose_name_plural = "用户偏好"

    def __str__(self):
        return f"{self.user_id} 偏好"

    def as_dict(self):
        return {
            "theme": self.theme,
            "font_size": self.font_size,
            "bubble_style": self.bubble_style,
            "notify_sound": self.notify_sound,
            "notify_desktop": self.notify_desktop,
        }

    @classmethod
    def defaults(cls):
        """未落库时的默认值，供序列化与前端首屏使用。"""
        return {
            "theme": "system",
            "font_size": "normal",
            "bubble_style": "classic",
            "notify_sound": True,
            "notify_desktop": False,
        }

    @classmethod
    def for_user(cls, user):
        preference, _ = cls.objects.get_or_create(user=user)
        return preference
