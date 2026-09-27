from django.contrib import admin

from .models import FriendRequest, Friendship


@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "from_user", "to_user", "status", "created", "handled_at")
    list_filter = ("status",)
    search_fields = ("from_user__username", "to_user__username")


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ("id", "user_low", "user_high", "created")
    search_fields = ("user_low__username", "user_high__username")
