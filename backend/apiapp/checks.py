"""生产配置校验。

把「能跑但危险」的配置在启动阶段就拦住，而不是等出事才发现：
* 生产模式（``DJANGO_DEBUG=0``）必须显式提供 ``DJANGO_SECRET_KEY``；
* 生产模式下 ``ALLOWED_HOSTS`` 不能是通配符；
* 生产模式必须开启 HTTPS Cookie 与代理协议识别。
"""
import os

from django.core.checks import Error, Warning, register

DEV_FALLBACK_SECRET = "dev-only-insecure-key-change-me-in-production-0123456789abcdef"


def is_production():
    """DEBUG 关闭即视为生产模式。"""
    return os.environ.get("DJANGO_DEBUG", "1") != "1"


@register()
def check_secret_key(app_configs, **kwargs):
    """生产环境必须注入独立密钥。"""
    issues = []
    if not is_production():
        return issues

    secret = os.environ.get("DJANGO_SECRET_KEY", "")
    if not secret or secret == DEV_FALLBACK_SECRET:
        issues.append(
            Error(
                "生产模式（DJANGO_DEBUG=0）必须通过环境变量 DJANGO_SECRET_KEY 提供独立密钥。",
                hint="生成方式示例：python -c \"import secrets; print(secrets.token_urlsafe(64))\"",
                id="chat.E001",
            )
        )
    elif len(secret) < 32:
        issues.append(
            Warning(
                "DJANGO_SECRET_KEY 长度偏短，建议至少 32 个字符。",
                id="chat.W001",
            )
        )
    return issues


@register()
def check_allowed_hosts(app_configs, **kwargs):
    """生产环境不允许用通配符接受任意 Host。"""
    from django.conf import settings

    if not is_production():
        return []
    if "*" in settings.ALLOWED_HOSTS:
        return [
            Error(
                "生产模式下 ALLOWED_HOSTS 不能包含通配符 '*'。",
                hint="请显式列出域名，或用 DSH_PUBLIC_ORIGIN 注入。",
                id="chat.E002",
            )
        ]
    return []


@register()
def check_https_cookies(app_configs, **kwargs):
    """对外 HTTPS 访问时 Cookie 必须是 Secure，否则会被明文回传。"""
    from django.conf import settings

    if not is_production():
        return []
    issues = []
    if not settings.SESSION_COOKIE_SECURE:
        issues.append(
            Warning("生产模式下 SESSION_COOKIE_SECURE 未开启，会话 Cookie 可能被明文传输。", id="chat.W002")
        )
    if not settings.CSRF_COOKIE_SECURE:
        issues.append(
            Warning("生产模式下 CSRF_COOKIE_SECURE 未开启。", id="chat.W003")
        )
    return issues


@register()
def check_debug_mode(app_configs, **kwargs):
    """在真实部署中保留 DEBUG 会泄露堆栈与配置。"""
    from django.conf import settings

    if settings.DEBUG:
        return [
            Warning(
                "当前 DEBUG=True，仅适合本地开发；公网部署请设置 DJANGO_DEBUG=0。",
                id="chat.W004",
            )
        ]
    return []
