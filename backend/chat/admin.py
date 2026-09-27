from django.contrib import admin

from .models import Attachment, Conversation, Membership, Message, MessageRead


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "title", "owner", "last_seq", "last_message_at")
    list_filter = ("type",)
    search_fields = ("title",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "user", "role", "unread_count", "last_read_seq")
    list_filter = ("role", "is_muted", "is_pinned")
    search_fields = ("user__username",)


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "uploader", "size", "mime", "created")
    search_fields = ("original_name",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "seq", "sender", "kind", "created_at", "revoked_at")
    list_filter = ("kind",)
    search_fields = ("body",)


@admin.register(MessageRead)
class MessageReadAdmin(admin.ModelAdmin):
    list_display = ("id", "message", "user", "read_at")
