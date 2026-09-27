"""ops 的 API 路由。"""
from django.urls import path

from backend.ops import api_views

urlpatterns = [
    path("ops/overview/", api_views.overview, name="ops-overview"),
    path("ops/users/", api_views.users, name="ops-users"),
    path("ops/users/<int:user_id>/", api_views.user_detail, name="ops-user-detail"),
    path(
        "ops/users/<int:user_id>/revoke-tokens/",
        api_views.user_revoke_tokens,
        name="ops-user-revoke-tokens",
    ),
    path("ops/conversations/", api_views.conversations, name="ops-conversations"),
    path(
        "ops/conversations/<int:conversation_id>/",
        api_views.conversation_detail,
        name="ops-conversation-detail",
    ),
    path("ops/messages/", api_views.messages, name="ops-messages"),
    path(
        "ops/messages/<int:message_id>/remove/",
        api_views.message_remove,
        name="ops-message-remove",
    ),
    path("ops/reports/", api_views.reports, name="ops-reports"),
    path(
        "ops/reports/<int:report_id>/<str:action>/",
        api_views.report_action,
        name="ops-report-action",
    ),
    path("ops/audit/", api_views.audit, name="ops-audit"),
]
