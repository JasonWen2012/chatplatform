"""网页端管理员守卫。

API 视图用 ``api_view(admin_only=True)``；
``/manage/`` 这类服务端渲染页面需要的是「未登录跳登录页、非管理员 403」。

这里显式用 ``current_user()`` 解析身份，而不是只看 ``request.user``：
后者由 AuthenticationMiddleware 依据 Session 填充，**不认 Bearer 令牌**，
会导致用令牌访问管理页面时被误判为未登录。
支持令牌也便于脚本化巡检与后续 App 端复用。
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from backend.accounts.api.api import current_user, user_is_admin


def admin_required(view_func):
    """要求 ``is_staff`` 且 ``is_active``；未登录跳转登录页并带回跳地址。"""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # 与 API 一致：Bearer 令牌优先，Session 兜底
        resolved = current_user(request)
        if resolved is not None:
            request.user = resolved

        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_is_admin(request.user):
            raise PermissionDenied("需要管理员权限")
        return view_func(request, *args, **kwargs)

    return wrapper
