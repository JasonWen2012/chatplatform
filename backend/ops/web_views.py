"""管理界面的服务端渲染视图（``/manage/``）。

页面只负责渲染骨架与首屏数据，后续交互全部经 ``/api/v1/ops/`` 由
``static/ops/manage.js`` 发起 —— 与聊天界面保持同一套「协议优先」结构，
二期 App 若有管理需求可直接复用 API。
"""
from django.shortcuts import render

from backend.accounts.api.api import user_brief
from backend.accounts.api.guards import admin_required

# 左侧导航：路径、标签、图标
NAV_ITEMS = [
    {"path": "/manage/", "label": "概览", "icon": "📊", "key": "dashboard"},
    {"path": "/manage/users/", "label": "用户管理", "icon": "👤", "key": "users"},
    {"path": "/manage/messages/", "label": "消息检索", "icon": "💬", "key": "messages"},
    {"path": "/manage/conversations/", "label": "会话浏览", "icon": "🗂️", "key": "conversations"},
    {"path": "/manage/reports/", "label": "举报处理", "icon": "🚩", "key": "reports"},
    {"path": "/manage/audit/", "label": "操作日志", "icon": "📜", "key": "audit"},
]


def _base_context(request, active):
    return {
        "me": user_brief(request.user),
        "nav_items": NAV_ITEMS,
        "active_nav": active,
    }


@admin_required
def dashboard(request):
    return render(request, "ops/dashboard.html", _base_context(request, "dashboard"))


@admin_required
def users_page(request):
    return render(request, "ops/users.html", _base_context(request, "users"))


@admin_required
def messages_page(request):
    return render(request, "ops/messages.html", _base_context(request, "messages"))


@admin_required
def conversations_page(request):
    return render(request, "ops/conversations.html", _base_context(request, "conversations"))


@admin_required
def reports_page(request):
    return render(request, "ops/reports.html", _base_context(request, "reports"))


@admin_required
def audit_page(request):
    return render(request, "ops/audit.html", _base_context(request, "audit"))
