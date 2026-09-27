"""网页视图：登录、注册、聊天主界面、协议文档。

网页端走 Session Cookie；外部客户端走 ``/api/v1/`` 的 Bearer Token。
两者共用同一批 API 视图，因此行为天然一致。
"""
import json
import logging

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.views import redirect_to_login
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from backend.accounts.api.api import (
    client_ip,
    current_user,
    issue_token,
    user_brief,
    user_is_admin,
)
from backend.accounts.api.views import CAPABILITIES
from backend.accounts.models import User, UserPreference, random_nickname

# 登录失败审计
auth_logger = logging.getLogger("chat.auth")

MIN_PASSWORD_LENGTH = 6


def _home_redirect():
    return redirect("web-app")


@require_http_methods(["GET", "POST"])
def login_page(request):
    if request.user.is_authenticated:
        return _home_redirect()
    error = None
    username = ""
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        account = User.objects.filter(username__iexact=username).first()
        if account is None:
            account = User.objects.filter(nickname=username).first()
        if account is not None:
            authenticated = authenticate(request, username=account.username, password=password)
            if authenticated is not None and authenticated.is_active:
                login(request, authenticated)
                return _home_redirect()
        # 网页端登录失败也记审计：与 API 同一份线索
        auth_logger.warning(
            "网页登录失败 username=%s ip=%s", username[:32], client_ip(request)
        )
        error = "用户名或密码错误"
    return render(request, "auth/login.html", {"error": error, "username": username})


@require_http_methods(["GET", "POST"])
def register_page(request):
    if request.user.is_authenticated:
        return _home_redirect()
    error = None
    username = ""
    nickname = ""
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        nickname = (request.POST.get("nickname") or "").strip()
        password = request.POST.get("password") or ""
        confirm = request.POST.get("password_confirm") or ""

        if not username or not password:
            error = "用户名和密码均不能为空"
        elif len(username) > 30:
            error = "用户名不能超过 30 个字符"
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = f"密码至少需要 {MIN_PASSWORD_LENGTH} 位"
        elif password != confirm:
            error = "两次输入的密码不一致"
        elif User.objects.filter(username__iexact=username).exists():
            error = "该用户名已被注册"
        else:
            new_user = User(username=username, nickname=nickname or random_nickname())
            new_user.set_password(password)
            try:
                with transaction.atomic():
                    new_user.save()
            except IntegrityError:
                error = "该用户名已被注册"
            else:
                login(request, new_user)
                return _home_redirect()
    return render(
        request, "auth/register.html", {"error": error, "username": username, "nickname": nickname}
    )


@require_http_methods(["GET", "POST"])
def logout_page(request):
    logout(request)
    return redirect("web-login")


@require_http_methods(["GET"])
def chat_page(request):
    """聊天主界面。

    首屏由模板注入当前用户与初始令牌，避免前端额外一次鉴权往返；
    后续所有数据都经 ``/api/v1/`` 获取。

    身份解析与 API 保持一致（Bearer 令牌优先、Session 兜底）：
    浏览器用 Cookie，脚本/客户端可用令牌直接巡检同一页面。
    """
    resolved = current_user(request)
    if resolved is not None:
        request.user = resolved
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    token = issue_token(request.user)
    me = user_brief(request.user)
    is_admin = user_is_admin(request.user)
    # 偏好随首屏注入，省掉一次往返；未落库时用默认值
    preference = UserPreference.for_user(request.user)
    preferences = preference.as_dict()
    return render(
        request,
        "app/chat.html",
        {
            "me": me,
            # 用 json.dumps 注入，避免手工拼接造成转义问题
            "me_json": json.dumps(me, ensure_ascii=False),
            "preferences_json": json.dumps(preferences, ensure_ascii=False),
            "capabilities_json": json.dumps(CAPABILITIES, ensure_ascii=False),
            "api_token": token.key,
            # 管理员额外显示「管理后台」入口；普通用户看不到
            "is_admin": is_admin,
        },
    )


def api_docs(request):
    """协议文档：说明外部客户端如何接入。"""
    return render(
        request,
        "api_docs/docs.html",
        {
            "base_url": request.build_absolute_uri("/api/v1/"),
            "longpoll_timeout": 25,
        },
    )
