"""apiapp 的网页路由与 API 汇总路由。"""
from django.urls import include, path

from . import web_views

# 网页页面
web_urlpatterns = [
    path("", web_views.chat_page, name="web-app"),
    path("login/", web_views.login_page, name="web-login"),
    path("register/", web_views.register_page, name="web-register"),
    path("logout/", web_views.logout_page, name="web-logout"),
    path("api-docs/", web_views.api_docs, name="web-api-docs"),
]

# 全部 /api/v1/ 端点
api_urlpatterns = [
    path("", include("backend.accounts.api.urls")),
    path("", include("backend.contacts.api.urls")),
    path("", include("backend.chat.api.urls")),
    path("", include("backend.realtime.api.urls")),
    path("", include("backend.ops.api_urls")),
]
