"""contacts 的 API 路由。"""
from django.urls import path

from backend.contacts import views

urlpatterns = [
    path("friends/", views.friend_list, name="api-friends"),
    path("friends/<int:friend_id>/", views.friend_detail, name="api-friend-detail"),
    path("friend-requests/", views.friend_requests, name="api-friend-requests"),
    path(
        "friend-requests/<int:request_id>/<str:action>/",
        views.handle_request,
        name="api-friend-request-action",
    ),
]
