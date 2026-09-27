"""账号相关 API：注册、登录、登出、当前用户、令牌管理、头像与偏好。"""
import logging

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from backend.accounts.api.api import (
    ApiError,
    api_view,
    client_ip,
    issue_token,
    json_body,
    ok,
    user_brief,
)
from backend.accounts.avatar import normalize_avatar
from backend.accounts.models import Token, User, random_nickname

# 登录失败审计：没有限流时，这是发现暴力破解的唯一线索
auth_logger = logging.getLogger("chat.auth")

MIN_PASSWORD_LENGTH = 6


# 客户端能力声明：App / 第三方客户端据此选择连接方式与可用特性。
# 二期接入 WebSocket 后只需在 transport 列表中追加 "websocket"，协议不破坏。
CAPABILITIES = {
    "api_version": "v1",
    "transport": ["longpoll"],
    "features": [
        "groups",
        "attachments",
        "read_receipts",
        "revoke",
        "typing",
        "reports",
        "preferences",
        "global_search",
    ],
}


def _serialize_auth(user, token=None):
    payload = {"user": user_brief(user), "capabilities": CAPABILITIES}
    if token is not None:
        payload["token"] = token.key
        payload["expires_in"] = 7 * 24 * 3600
    return payload


@api_view(require_auth=False)
def capabilities(request, user=None):
    """GET /api/v1/capabilities/ — 客户端能力声明（无需鉴权）。

    App / 第三方客户端在决定连接方式前先读这里，
    二期接入 WebSocket 后 ``transport`` 会追加 ``"websocket"``，协议不破坏。
    """
    if request.method != "GET":
        raise ApiError("method_not_allowed", "仅支持 GET", 405)
    return ok({"capabilities": CAPABILITIES})


@api_view(require_auth=False)
def register(request, user=None):
    """POST /api/v1/auth/register/ — 用户名 + 密码注册。

    注册成功后同时建立 Session（网页端直接可用）并返回 Token（客户端用）。
    """
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    body = json_body(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    nickname = (body.get("nickname") or "").strip()

    if not username or not password:
        raise ApiError("missing_fields", "用户名和密码均不能为空")
    if len(username) > 30:
        raise ApiError("invalid_username", "用户名不能超过 30 个字符")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError("weak_password", f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    if User.objects.filter(username__iexact=username).exists():
        raise ApiError("username_taken", "该用户名已被注册", 409)

    new_user = User(username=username, nickname=nickname or random_nickname())
    try:
        validate_password(password, new_user)
    except Exception as exc:  # ValidationError
        messages = getattr(exc, "messages", [str(exc)])
        raise ApiError("weak_password", "；".join(messages))
    new_user.set_password(password)

    try:
        with transaction.atomic():
            new_user.save()
    except IntegrityError:
        # 并发注册同名用户时兜底
        raise ApiError("username_taken", "该用户名已被注册", 409)

    token = issue_token(new_user)
    login(request, new_user)
    return ok(_serialize_auth(new_user, token), status=201)


@api_view(require_auth=False)
def login_view(request, user=None):
    """POST /api/v1/auth/login/ — 支持用户名或昵称登录。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    body = json_body(request)
    identifier = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not identifier or not password:
        raise ApiError("missing_fields", "用户名和密码均不能为空")

    account = User.objects.filter(username__iexact=identifier).first()
    if account is None:
        # 允许用昵称登录
        account = User.objects.filter(nickname=identifier).first()
    if account is None:
        auth_logger.warning("登录失败（账号不存在）identifier=%s ip=%s", identifier[:32], client_ip(request))
        raise ApiError("invalid_credentials", "用户名或密码错误", 401)

    authenticated = authenticate(request, username=account.username, password=password)
    if authenticated is None:
        auth_logger.warning("登录失败（密码错误）username=%s ip=%s", account.username, client_ip(request))
        raise ApiError("invalid_credentials", "用户名或密码错误", 401)
    if not authenticated.is_active:
        auth_logger.warning("登录失败（账号已禁用）username=%s", account.username)
        raise ApiError("account_disabled", "账号已被停用", 403)

    login(request, authenticated)
    Token.objects.filter(user=authenticated, revoked=False).update(last_used=timezone.now())
    token = issue_token(authenticated)
    return ok(_serialize_auth(authenticated, token))


@api_view(require_auth=False)
def logout_view(request, user=None):
    """POST /api/v1/auth/logout/ — 撤销请求所用令牌并清除 Session。"""
    from backend.accounts.api.api import bearer_token

    raw = bearer_token(request)
    if raw:
        Token.objects.filter(key=raw).update(revoked=True)
    logout(request)
    return ok({"detail": "已登出"})


@api_view()
def me(request, user=None):
    """GET /api/v1/auth/me/ — 当前用户信息。"""
    if request.method == "GET":
        return ok({"user": user_brief(user)})
    if request.method == "PATCH":
        body = json_body(request)
        changed = []
        if "nickname" in body:
            nickname = (body.get("nickname") or "").strip()
            if len(nickname) > 32:
                raise ApiError("invalid_nickname", "昵称不能超过 32 个字符")
            user.nickname = nickname
            changed.append("nickname")
        if "bio" in body:
            bio = (body.get("bio") or "").strip()
            if len(bio) > 140:
                raise ApiError("invalid_bio", "个性签名不能超过 140 个字符")
            user.bio = bio
            changed.append("bio")
        if changed:
            user.save(update_fields=changed + ["last_seen"])
        return ok({"user": user_brief(user)})
    raise ApiError("method_not_allowed", "仅支持 GET/PATCH", 405)


@api_view()
def change_password(request, user=None):
    """POST /api/v1/auth/password/ — 修改密码，成功后撤销全部旧令牌。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    body = json_body(request)
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    if not old or not new:
        raise ApiError("missing_fields", "原密码和新密码均不能为空")
    if not user.check_password(old):
        raise ApiError("invalid_password", "原密码不正确", 401)
    if len(new) < MIN_PASSWORD_LENGTH:
        raise ApiError("weak_password", f"新密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    try:
        validate_password(new, user)
    except Exception as exc:
        messages = getattr(exc, "messages", [str(exc)])
        raise ApiError("weak_password", "；".join(messages))

    with transaction.atomic():
        user.set_password(new)
        user.save(update_fields=["password"])
        Token.objects.filter(user=user).update(revoked=True)
    # 密码变更后当前 Session 失效，让用户重新登录
    logout(request)
    return ok({"detail": "密码已修改，请重新登录"})


@api_view()
def token_refresh(request, user=None):
    """POST /api/v1/auth/token/refresh/ — 撤销当前令牌并签发新令牌。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    from backend.accounts.api.api import bearer_token

    raw = bearer_token(request)
    with transaction.atomic():
        if raw:
            Token.objects.filter(key=raw).update(revoked=True)
        token = issue_token(user)
    return ok({"token": token.key, "expires_in": 7 * 24 * 3600})


@api_view()
def upload_avatar(request, user=None):
    """POST /api/v1/users/me/avatar/ — 上传并裁剪头像。

    统一裁剪为 256×256 方形 JPEG，避免不同尺寸头像撑破界面；
    同时校正 EXIF 方向，否则手机竖拍照片会倒置。
    """
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    upload = request.FILES.get("file")
    if upload is None:
        raise ApiError("missing_file", "请通过 file 字段上传头像")
    limit = getattr(settings, "MAX_UPLOAD_SIZE", 20 * 1024 * 1024)
    if upload.size <= 0:
        raise ApiError("empty_file", "文件内容为空")
    if upload.size > limit:
        raise ApiError("file_too_large", f"文件大小超过上限 {limit // (1024 * 1024)}MB", 413)

    content, filename = normalize_avatar(upload)

    # 先把旧文件句柄与存储取出来。
    # 注意：直接保存 user.avatar 会就地改写同一个 FieldFile 对象，
    # 之后再比较名称必然相等，旧文件就永远删不掉。
    old_field = user.avatar if user.avatar else None
    old_name = old_field.name if old_field else None
    old_storage = old_field.storage if old_field else None

    user.avatar.save(filename, content, save=False)
    user.save(update_fields=["avatar"])

    # 新头像落库成功后再删旧文件，避免中途失败把原头像弄丢
    if old_name and old_name != user.avatar.name and old_storage is not None:
        try:
            old_storage.delete(old_name)
        except Exception:  # pragma: no cover - 磁盘异常不应阻断业务
            pass

    return ok({"user": user_brief(user)})


@api_view()
def preferences(request, user=None):
    """GET/PATCH /api/v1/users/me/preferences/ — 读取或更新用户偏好。

    偏好放服务端而非浏览器本地：二期 App 与网页端需要共享同一份设置。
    PATCH 只接受白名单字段，非法取值直接 400，未知字段忽略。
    """
    from backend.accounts.models import UserPreference

    preference = UserPreference.for_user(user)

    if request.method == "GET":
        return ok({"preferences": preference.as_dict()})

    if request.method != "PATCH":
        raise ApiError("method_not_allowed", "仅支持 GET/PATCH", 405)

    body = json_body(request)
    changed = []

    if "theme" in body:
        value = body["theme"]
        allowed = {key for key, _ in UserPreference._meta.get_field("theme").choices}
        if value not in allowed:
            raise ApiError("invalid_theme", f"theme 仅支持 {'/'.join(sorted(allowed))}")
        preference.theme = value
        changed.append("theme")

    if "font_size" in body:
        value = body["font_size"]
        allowed = {key for key, _ in UserPreference._meta.get_field("font_size").choices}
        if value not in allowed:
            raise ApiError("invalid_font_size", f"font_size 仅支持 {'/'.join(sorted(allowed))}")
        preference.font_size = value
        changed.append("font_size")

    if "bubble_style" in body:
        value = body["bubble_style"]
        allowed = {key for key, _ in UserPreference._meta.get_field("bubble_style").choices}
        if value not in allowed:
            raise ApiError("invalid_bubble_style", f"bubble_style 仅支持 {'/'.join(sorted(allowed))}")
        preference.bubble_style = value
        changed.append("bubble_style")

    for flag in ("notify_sound", "notify_desktop"):
        if flag in body:
            setattr(preference, flag, bool(body[flag]))
            changed.append(flag)

    if changed:
        preference.updated_at = timezone.now()
        preference.save(update_fields=changed + ["updated_at"])

    return ok({"preferences": preference.as_dict()})


@api_view()
def user_detail(request, user_id, user=None):
    """GET /api/v1/users/<id>/ — 查看他人公开资料。"""
    if request.method != "GET":
        raise ApiError("method_not_allowed", "仅支持 GET", 405)
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        raise ApiError("not_found", "用户不存在", 404)
    return ok({"user": user_brief(target)})


@api_view()
def user_search(request, user=None):
    """GET /api/v1/users/search/?q= — 按用户名或昵称搜索，最多返回 20 条。"""
    query = (request.GET.get("q") or "").strip()
    if not query:
        raise ApiError("missing_query", "请提供搜索关键词 q")
    if len(query) > 50:
        raise ApiError("invalid_query", "搜索关键词过长")

    from django.db.models import Q

    results = (
        User.objects.filter(Q(username__icontains=query) | Q(nickname__icontains=query))
        .exclude(pk=user.pk)
        .exclude(is_active=False)
        .order_by("id")[:20]
    )
    return ok({"results": [user_brief(item) for item in results]})
