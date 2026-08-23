from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _


class ContestViolationLogAdmin(admin.ModelAdmin):
    """Read-only: this is evidence, and evidence you can edit is not evidence."""

    list_display = (
        "created",
        "contest_link",
        "username",
        "action",
        "violation_number",
        "ip",
        "detail",
    )
    list_filter = ("action", "is_automated")
    search_fields = (
        "contest__key",
        "contest__name",
        "participation__user__user__username",
    )
    date_hierarchy = "created"
    list_select_related = ("contest", "participation__user__user")
    ordering = ("-created",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def contest_link(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:judge_contest_change", args=(obj.contest_id,)),
            obj.contest.key,
        )

    contest_link.short_description = _("contest")
    contest_link.admin_order_field = "contest__key"

    def username(self, obj):
        return obj.participation.user.username

    username.short_description = _("user")
    username.admin_order_field = "participation__user__user__username"
