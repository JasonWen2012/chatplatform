"""chat 的 API 路由。"""
from django.urls import path

from backend.chat.api import search_views, views

urlpatterns = [
    path("search/", search_views.search, name="api-search"),
    path("conversations/", views.conversations, name="api-conversations"),
    path("conversations/<int:conversation_id>/", views.conversation_detail, name="api-conversation-detail"),
    path(
        "conversations/<int:conversation_id>/members/",
        views.conversation_members,
        name="api-conversation-members",
    ),
    path(
        "conversations/<int:conversation_id>/members/<int:member_id>/",
        views.member_detail,
        name="api-conversation-member-detail",
    ),
    path(
        "conversations/<int:conversation_id>/leave/",
        views.conversation_leave,
        name="api-conversation-leave",
    ),
    path(
        "conversations/<int:conversation_id>/messages/",
        views.messages,
        name="api-messages",
    ),
    path(
        "conversations/<int:conversation_id>/read/",
        views.conversation_read,
        name="api-conversation-read",
    ),
    path(
        "conversations/<int:conversation_id>/typing/",
        views.conversation_typing,
        name="api-conversation-typing",
    ),
    path("messages/<int:message_id>/revoke/", views.message_revoke, name="api-message-revoke"),
    path("messages/<int:message_id>/report/", views.message_report, name="api-message-report"),
    path("attachments/", views.attachments, name="api-attachments"),
    path(
        "attachments/<int:attachment_id>/download/",
        views.attachment_download,
        name="api-attachment-download",
    ),
]
