"""统一 JSON 响应、错误封装与鉴权装饰器。

设计要点
--------
* 网页端用 Session Cookie，外部客户端用 ``Authorization: Bearer <token>``，
  两条通道复用同一批视图。
* 未登录且请求方期望 JSON 时返回 401 JSON，而不是 302 跳转登录页，
  保证 ``curl`` 等客户端行为可预期。
* 业务代码只需 ``raise ApiError("code", "message", status)``，
  由 ``api_view`` 统一转成标准错误结构。
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger("chat.api")
# 鉴权失败单独记一个 logger：在没有限流的情况下，这是发现暴力破解的唯一线索
auth_logger = logging.getLogger("chat.auth")

TOKEN_TTL = timedelta(days=7)


class ApiError(Exception):
    """可预期的业务错误，会被 api_view 转为标准 JSON 错误响应。"""

    def __init__(self, code, message, status=400, extra=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra


def ok(data=None, status=200, **extra):
    """成功响应。extra 会被合并进 data（data 必须是 dict 或 None）。"""
    payload = data if isinstance(data, dict) else ({"value": data} if data is not None else {})
    if extra:
        payload = {**payload, **extra}
    return JsonResponse({"ok": True, "data": payload}, status=status, json_dumps_params={"ensure_ascii": False})


def error(code, message, status=400, **extra):
    """失败响应。"""
    return JsonResponse(
        {"ok": False, "error": {"code": code, "message": message, **extra}},
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )


def json_body(request):
    """解析请求体 JSON；空体返回 {}。"""
    if not request.body:
        return {}
    try:
        parsed = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiError("invalid_json", "请求体不是合法的 JSON")
    if not isinstance(parsed, dict):
        raise ApiError("invalid_json", "请求体必须是 JSON 对象")
    return parsed


def bearer_token(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def client_ip(request):
    """取真实来源 IP。

    经 ngrok / 反向代理时 REMOTE_ADDR 是代理地址，
    必须优先读 X-Forwarded-For 的第一段，日志才有排查价值。
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def resolve_token_user(request):
    """按 Bearer Token 解析用户；无效返回 (None, None)。"""
    from backend.accounts.models import Token

    raw = bearer_token(request)
    if not raw:
        return None, None
    try:
        token = Token.objects.select_related("user").get(key=raw, revoked=False)
    except Token.DoesNotExist:
        return None, None
    if timezone.now() - token.created > TOKEN_TTL:
        return None, None
    Token.objects.filter(pk=token.pk).update(last_used=timezone.now())
    return token.user, token


def current_user(request):
    """按 Bearer Token 优先、Session 兜底的顺序解析当前用户。"""
    user, _ = resolve_token_user(request)
    if user is not None:
        return user
    session_user = getattr(request, "user", None)
    if session_user is not None and session_user.is_authenticated:
        return session_user
    return None


def user_is_admin(user):
    """是否服务器管理员（可进入 /manage/ 与 /api/v1/ops/）。

    ``is_active`` 一并校验：被禁用的管理员立即失去全部权限。
    Django 的 ``ModelBackend`` 在身份认证时也会检查该标志，
    但会话中的用户对象可能早于禁用动作建立，因此这里必须显式判断。
    """
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


def api_view(require_auth=True, admin_only=False):
    """API 视图装饰器：解析鉴权、捕获错误、序列化响应。

    被装饰函数签名保持 ``(request, ...)``，
    但会多收到关键字参数 ``user``（require_auth=True 时保证非空）。

    ``admin_only=True`` 时额外要求 ``is_staff`` 且 ``is_active``，
    否则返回 403（管理入口本就公开可探测，故不再伪装成 404）。
    """

    def decorator(func):
        @csrf_exempt
        def wrapper(request, *args, **kwargs):
            request.user = current_user(request)
            if require_auth and request.user is None:
                return error("unauthorized", "未登录或登录已过期", 401)
            if admin_only and not user_is_admin(request.user):
                auth_logger.warning(
                    "管理员接口越权访问 user=%s path=%s",
                    getattr(request.user, "id", None),
                    request.path,
                )
                return error("forbidden", "需要管理员权限", 403)
            try:
                response = func(request, *args, user=request.user, **kwargs)
            except ApiError as exc:
                return error(exc.code, exc.message, exc.status, **(exc.extra or {}))
            except ValidationError as exc:
                message = "；".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                return error("validation_error", message, 400)
            except Http404:
                # 会话不存在时统一 404，避免泄露资源是否存在
                return error("not_found", "资源不存在或无权访问", 404)
            except Exception:  # pragma: no cover - 兜底，记录完整堆栈
                logger.exception("未处理的服务端异常: %s %s", request.method, request.path)
                return error("server_error", "服务器内部错误", 500)
            if response is None:
                return ok()
            return response

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


def issue_token(user):
    from backend.accounts.models import Token

    return Token.objects.create(user=user)


# ---------------------------------------------------------------- 序列化辅助
def iso(dt):
    """时间转 ISO 8601（含时区），None 安全。"""
    return dt.isoformat() if dt else None


def user_brief(user):
    """用户公开信息（绝不含密码、邮箱等敏感字段）。"""
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.display_name,
        "avatar": user.avatar.url if user.avatar else None,
        "bio": user.bio or "",
        "is_online": bool(user.is_online),
        "last_seen": iso(user.last_seen),
    }
