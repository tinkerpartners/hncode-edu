from django.contrib import admin
from django.db.models import OuterRef, Subquery
from django.forms import ModelForm
from django.urls import reverse_lazy
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy as _
from reversion.admin import VersionAdmin

from judge.models import Organization, Profile
from judge.widgets import (
    AdminHeavySelect2MultipleWidget,
    AdminHeavySelect2Widget,
    HeavyPreviewAdminPageDownWidget,
)


class OrganizationForm(ModelForm):
    class Meta:
        widgets = {
            "admins": AdminHeavySelect2MultipleWidget(data_view="profile_select2"),
            "moderators": AdminHeavySelect2MultipleWidget(data_view="profile_select2"),
            "registrant": AdminHeavySelect2Widget(data_view="profile_select2"),
        }
        if HeavyPreviewAdminPageDownWidget is not None:
            widgets["about"] = HeavyPreviewAdminPageDownWidget(
                preview=reverse_lazy("organization_preview")
            )


class RegistrantStateFilter(admin.SimpleListFilter):
    """Split the list by whether the registrant's Profile row still exists.

    Production carries hundreds of organizations whose ``registrant_id`` points
    at a Profile that was deleted outside the ORM (the FK constraint is there,
    the rows are not). This filter is how an admin finds them; see
    ``OrganizationAdmin.get_queryset`` for why they used to vanish instead.
    """

    title = _("registrant account")
    parameter_name = "registrant_state"

    def lookups(self, request, model_admin):
        return (
            ("exists", _("exists")),
            ("deleted", _("deleted")),
        )

    def queryset(self, request, queryset):
        if self.value() == "exists":
            return queryset.filter(registrant_username__isnull=False)
        if self.value() == "deleted":
            return queryset.filter(registrant_username__isnull=True)
        return queryset


class OrganizationAdmin(VersionAdmin):
    readonly_fields = ("creation_date",)
    fields = (
        "name",
        "slug",
        "short_name",
        "is_open",
        "is_community",
        "about",
        "slots",
        "registrant",
        "creation_date",
        "admins",
        "moderators",
    )
    list_display = (
        "name",
        "short_name",
        "is_open",
        "is_community",
        "creation_date",
        "registrant_name",
        "show_public",
    )
    # Never let the changelist ``select_related()`` the registrant. ``registrant``
    # is a non-nullable FK, so that is an INNER JOIN on judge_profile, and every
    # organization whose registrant row is gone silently drops out of the page
    # slice while ``paginator.count`` (no join) still counts it. With 700 such
    # rows on hncode.edu.vn the last 7 of 22 pages rendered empty (issue #97).
    # The username is fetched by correlated subquery instead (see get_queryset).
    list_select_related = ()
    list_filter = ("is_open", "is_community", RegistrantStateFilter)
    search_fields = ("name", "short_name", "registrant__user__username")
    prepopulated_fields = {"slug": ("name",)}
    actions_on_top = True
    actions_on_bottom = True
    form = OrganizationForm
    ordering = ("-creation_date", "-id")

    def registrant_name(self, obj):
        # None when the Profile row no longer exists -> empty_value_display.
        return obj.registrant_username

    registrant_name.short_description = _("registrant")
    registrant_name.admin_order_field = "registrant_username"

    def show_public(self, obj):
        return format_html(
            '<a href="{0}" style="white-space:nowrap;">{1}</a>',
            obj.get_absolute_url(),
            gettext("View on site"),
        )

    show_public.short_description = ""

    def get_readonly_fields(self, request, obj=None):
        fields = self.readonly_fields
        if not request.user.has_perm("judge.organization_admin"):
            return fields + ("registrant", "admins", "is_open", "slots")
        return fields

    def get_queryset(self, request):
        # A correlated subquery yields NULL for a missing registrant instead of
        # dropping the row the way a join would; the list and its count stay in
        # step whatever shape the data is in.
        queryset = Organization.objects.annotate(
            registrant_username=Subquery(
                Profile.objects.filter(id=OuterRef("registrant_id")).values(
                    "user__username"
                )[:1]
            )
        )
        if request.user.has_perm("judge.edit_all_organization"):
            return queryset
        else:
            return queryset.filter(admins=request.profile.id)

    def has_change_permission(self, request, obj=None):
        if not request.user.has_perm("judge.change_organization"):
            return False
        if request.user.has_perm("judge.edit_all_organization") or obj is None:
            return True
        return obj.admins.filter(id=request.profile.id).exists()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        obj.members.add(*obj.admins.all())


class OrganizationRequestAdmin(admin.ModelAdmin):
    list_display = ("username", "organization", "state", "time")
    readonly_fields = ("user", "organization")

    def username(self, obj):
        return obj.user.user.username

    username.short_description = _("username")
    username.admin_order_field = "user__user__username"
