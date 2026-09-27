"""ops 的网页路由。"""
from django.urls import path

from backend.ops import web_views

web_urlpatterns = [
    path("manage/", web_views.dashboard, name="ops-dashboard"),
    path("manage/users/", web_views.users_page, name="ops-users-page"),
    path("manage/messages/", web_views.messages_page, name="ops-messages-page"),
    path("manage/conversations/", web_views.conversations_page, name="ops-conversations-page"),
    path("manage/reports/", web_views.reports_page, name="ops-reports-page"),
    path("manage/audit/", web_views.audit_page, name="ops-audit-page"),
]
