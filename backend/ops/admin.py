from django.contrib import admin

from .models import AdminActionLog


@admin.register(AdminActionLog)
class AdminActionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "created", "actor", "action", "target_type", "target_id", "detail")
    list_filter = ("action", "target_type")
    search_fields = ("detail", "actor__username")
    # 审计记录不可篡改
    readonly_fields = ("actor", "action", "target_type", "target_id", "detail", "created")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
