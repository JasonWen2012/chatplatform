from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend.accounts"
    verbose_name = "账号"

    def ready(self):
        # 注册 SQLite 连接 PRAGMA（WAL / 外键 / busy_timeout）信号
        from backend.config import db_pragmas  # noqa: F401
