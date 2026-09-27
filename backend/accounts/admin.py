from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Token, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("id", "username", "nickname", "is_online", "is_staff", "last_seen")
    list_filter = ("is_online", "is_staff", "is_superuser")
    search_fields = ("username", "nickname")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("聊天资料", {"fields": ("nickname", "avatar", "bio", "is_online", "last_seen")}),
    )


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ("key", "user", "created", "last_used", "revoked")
    list_filter = ("revoked",)
    search_fields = ("user__username", "key")
