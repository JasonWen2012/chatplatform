"""accounts 的 API 路由。"""
from django.urls import path

from . import views

urlpatterns = [
    path("capabilities/", views.capabilities, name="api-capabilities"),
    path("auth/register/", views.register, name="api-register"),
    path("auth/login/", views.login_view, name="api-login"),
    path("auth/logout/", views.logout_view, name="api-logout"),
    path("auth/me/", views.me, name="api-me"),
    path("auth/password/", views.change_password, name="api-change-password"),
    path("auth/token/refresh/", views.token_refresh, name="api-token-refresh"),
    path("users/search/", views.user_search, name="api-user-search"),
    # 必须排在 users/<int:user_id>/ 之前，否则 "me" 会被当成 id 尝试匹配
    path("users/me/avatar/", views.upload_avatar, name="api-user-avatar"),
    path("users/me/preferences/", views.preferences, name="api-user-preferences"),
    path("users/<int:user_id>/", views.user_detail, name="api-user-detail"),
]
