"""realtime 的 API 路由。"""
from django.urls import path

from backend.realtime import views

urlpatterns = [
    path("updates/", views.updates, name="api-updates"),
    path("presence/", views.presence, name="api-presence"),
]
