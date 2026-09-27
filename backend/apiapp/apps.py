from django.apps import AppConfig


class ApiAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend.apiapp"
    verbose_name = "协议文档"

    def ready(self):
        # 注册生产配置校验（生产模式缺密钥、通配符 Host、Cookie 未加密等）
        from backend.apiapp import checks  # noqa: F401
