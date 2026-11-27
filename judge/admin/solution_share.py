from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from judge.models import SolutionShareLink


@admin.register(SolutionShareLink)
class SolutionShareLinkAdmin(admin.ModelAdmin):
    """Review and revoke editorial share links. New links are made on the
    editorial page, where the length is chosen."""

    list_display = (
        "solution",
        "token_hint",
        "created_by",
        "created_at",
        "expires_at",
        "active",
    )
    list_filter = ("expires_at",)
    search_fields = ("solution__problem__code", "solution__problem__name")
    readonly_fields = ("solution", "token_hint", "created_by", "created_at")
    fields = ("solution", "token_hint", "created_by", "created_at", "expires_at")

    def has_add_permission(self, request):
        return False

    @admin.display(boolean=True, description=_("active"))
    def active(self, obj):
        return obj.is_active
