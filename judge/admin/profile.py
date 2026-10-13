from django.contrib import messages
from django.template.response import TemplateResponse
from django.contrib.auth.models import User
from django.contrib import admin
from django.forms import ModelForm
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy as _, ngettext
from django.contrib.auth.admin import UserAdmin as OldUserAdmin
from django.contrib.auth.forms import UserChangeForm


from judge.models import Profile, ProfileInfo
from judge.validators import (
    USERNAME_ALLOWED_MESSAGE,
    clean_username as clean_username_value,
)
from judge.widgets import AdminPagedownWidget, AdminSelect2Widget

from reversion.admin import VersionAdmin


class ProfileForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super(ProfileForm, self).__init__(*args, **kwargs)
        if "current_contest" in self.base_fields:
            # form.fields['current_contest'] does not exist when the user has only view permission on the model.
            self.fields["current_contest"].queryset = (
                self.instance.contest_history.select_related("contest").only(
                    "contest__name", "user_id", "virtual"
                )
            )
            self.fields["current_contest"].label_from_instance = lambda obj: (
                "%s v%d" % (obj.contest.name, obj.virtual)
                if obj.virtual
                else obj.contest.name
            )

    class Meta:
        widgets = {
            "timezone": AdminSelect2Widget,
            "language": AdminSelect2Widget,
            "ace_theme": AdminSelect2Widget,
            "current_contest": AdminSelect2Widget,
        }
        if AdminPagedownWidget is not None:
            widgets["about"] = AdminPagedownWidget


class TimezoneFilter(admin.SimpleListFilter):
    title = _("timezone")
    parameter_name = "timezone"

    def lookups(self, request, model_admin):
        return (
            Profile.objects.values_list("timezone", "timezone")
            .distinct()
            .order_by("timezone")
        )

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset
        return queryset.filter(timezone=self.value())


class ProfileInfoInline(admin.StackedInline):
    model = ProfileInfo
    can_delete = False
    verbose_name_plural = "profile info"
    fk_name = "profile"


class ProfileAdmin(VersionAdmin):
    fields = (
        "user",
        "display_rank",
        "about",
        "organizations",
        "timezone",
        "language",
        "ace_theme",
        "last_access",
        "ip",
        "mute",
        "is_unlisted",
        "notes",
        "is_totp_enabled",
        "current_contest",
    )
    readonly_fields = ("user",)
    list_display = (
        "admin_user_admin",
        "email",
        "is_totp_enabled",
        "timezone_full",
        "date_joined",
        "last_access",
        "ip",
        "show_public",
    )
    ordering = ("user__username",)
    search_fields = ("user__username", "ip", "user__email")
    list_filter = ("language", TimezoneFilter)
    actions = ("recalculate_points",)
    actions_on_top = True
    actions_on_bottom = True
    form = ProfileForm
    inlines = (ProfileInfoInline,)

    def get_queryset(self, request):
        return super(ProfileAdmin, self).get_queryset(request).select_related("user")

    def get_fields(self, request, obj=None):
        if request.user.has_perm("judge.totp"):
            fields = list(self.fields)
            fields.insert(fields.index("is_totp_enabled") + 1, "totp_key")
            return tuple(fields)
        else:
            return self.fields

    def get_readonly_fields(self, request, obj=None):
        fields = self.readonly_fields
        if not request.user.has_perm("judge.totp"):
            fields += ("is_totp_enabled",)
        return fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def show_public(self, obj):
        return format_html(
            '<a href="{0}" style="white-space:nowrap;">{1}</a>',
            obj.get_absolute_url(),
            gettext("View on site"),
        )

    show_public.short_description = ""

    def admin_user_admin(self, obj):
        return obj.username

    admin_user_admin.admin_order_field = "user__username"
    admin_user_admin.short_description = _("User")

    def email(self, obj):
        return obj.email

    email.admin_order_field = "user__email"
    email.short_description = _("Email")

    def timezone_full(self, obj):
        return obj.timezone

    timezone_full.admin_order_field = "timezone"
    timezone_full.short_description = _("Timezone")

    def date_joined(self, obj):
        return obj.user.date_joined

    date_joined.admin_order_field = "user__date_joined"
    date_joined.short_description = _("date joined")

    def recalculate_points(self, request, queryset):
        count = 0
        for profile in queryset:
            profile.calculate_points()
            count += 1
        self.message_user(
            request,
            ngettext(
                "%d user have scores recalculated.",
                "%d users have scores recalculated.",
                count,
            )
            % count,
        )

    recalculate_points.short_description = _("Recalculate scores")


class UserForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = USERNAME_ALLOWED_MESSAGE

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if self.instance.pk and username == self.instance.username:
            return username
        return clean_username_value(username)


class UserAdmin(OldUserAdmin):
    # Customize the fieldsets for adding and editing users
    form = UserForm
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal Info", {"fields": ("first_name", "last_name", "email")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    readonly_fields = ("last_login", "date_joined")
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_active",
        "is_staff",
    )
    # A17 (#101): deactivate instead of delete. Deletion stays disabled
    # (has_delete_permission below); hard deletion is A24.
    actions = ("deactivate_users", "activate_users")

    def get_readonly_fields(self, request, obj=None):
        fields = self.readonly_fields
        if not request.user.is_superuser:
            fields += (
                "is_staff",
                "is_active",
                "is_superuser",
                "groups",
                "user_permissions",
            )
        return fields

    def has_deactivate_permission(self, request):
        return request.user.has_perm("judge.deactivate_user")

    @admin.action(
        description=_("Deactivate selected users"),
        permissions=("deactivate",),
    )
    def deactivate_users(self, request, queryset):
        return self._bulk_set_active(request, queryset, active=False)

    @admin.action(
        description=_("Reactivate selected users"),
        permissions=("deactivate",),
    )
    def activate_users(self, request, queryset):
        return self._bulk_set_active(request, queryset, active=True)

    def _bulk_set_active(self, request, queryset, active):
        """Shared body of both actions: confirm, apply, audit, revoke.

        Protected in both directions: superusers, staff (only a superuser may
        change those, on the user's own change page) and the acting account.
        Users already in the target state are left alone.
        """
        targets, protected, unchanged = [], [], []
        for user in queryset.order_by("username"):
            if user.pk == request.user.pk:
                protected.append((user, gettext("your own account")))
            elif user.is_superuser or user.is_staff:
                protected.append((user, gettext("staff or superuser")))
            elif user.is_active == active:
                unchanged.append(user)
            else:
                targets.append(user)

        if request.POST.get("post") != "yes":
            select_across = request.POST.get("select_across") == "1"
            context = {
                **self.admin_site.each_context(request),
                "title": (
                    gettext("Reactivate users")
                    if active
                    else gettext("Deactivate users")
                ),
                "opts": self.model._meta,
                "activating": active,
                "action_name": request.POST.get("action"),
                "targets": targets,
                "protected": protected,
                "unchanged_count": len(unchanged),
                "select_across": select_across,
                "selected_ids": [] if select_across else [u.pk for u in queryset],
                "first_selected_id": next(
                    iter(queryset.values_list("pk", flat=True)), None
                ),
                "shown_limit": 200,
            }
            return TemplateResponse(
                request, "admin/auth/user/bulk_active_confirmation.html", context
            )

        target_ids = [u.pk for u in targets]
        User.objects.filter(pk__in=target_ids).update(is_active=active)
        message = "Reactivated (bulk action)" if active else "Deactivated (bulk action)"
        for user in targets:
            self.log_change(request, user, message)

        if not active and target_ids:
            from judge.tasks.user import revoke_user_sessions

            try:
                revoke_user_sessions.delay(target_ids)
                revoke_note = gettext(
                    "Their open sessions are being revoked in the background."
                )
            except Exception:
                revoke_note = gettext(
                    "Could not queue session revocation; deactivated users are "
                    "still refused on every request."
                )
        else:
            revoke_note = ""

        summary = (
            gettext("Reactivated %(n)d user(s).")
            if active
            else gettext("Deactivated %(n)d user(s).")
        ) % {"n": len(targets)}
        if protected:
            summary += " " + gettext("Skipped %(n)d protected account(s).") % {
                "n": len(protected)
            }
        if unchanged:
            summary += " " + gettext("%(n)d were already in that state.") % {
                "n": len(unchanged)
            }
        if revoke_note:
            summary += " " + revoke_note
        self.message_user(
            request, summary, messages.SUCCESS if targets else messages.WARNING
        )
        return None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
