from django.contrib import admin
from django.urls import reverse_lazy
from django.forms import ModelForm

from judge.forms import ParticipationSettingsForm
from judge.models import CourseRole
from judge.widgets import (
    AdminHeavySelect2MultipleWidget,
    AdminHeavySelect2Widget,
    HeavyPreviewAdminPageDownWidget,
    AdminSelect2Widget,
)


class CourseRoleInlineForm(ModelForm):
    class Meta:
        widgets = {
            "user": AdminHeavySelect2Widget(
                data_view="profile_select2", attrs={"style": "width: 100%"}
            ),
            "role": AdminSelect2Widget,
        }


class CourseRoleInline(admin.TabularInline):
    model = CourseRole
    extra = 1
    form = CourseRoleInlineForm


class CourseForm(ParticipationSettingsForm):
    class Meta:
        widgets = {
            "organizations": AdminHeavySelect2MultipleWidget(
                data_view="organization_select2"
            ),
            "about": HeavyPreviewAdminPageDownWidget(
                preview=reverse_lazy("blog_preview")
            ),
        }


class CourseAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    inlines = [
        CourseRoleInline,
    ]
    exclude = ("is_public", "is_open", "is_suspended")
    list_display = ("name", "is_featured", "is_public", "is_open", "is_suspended")
    list_editable = ("is_featured",)
    list_filter = ("is_featured", "is_suspended", "is_public", "is_open")
    search_fields = ("name", "slug")
    form = CourseForm

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, "is_featured")

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "is_featured" and not request.user.is_superuser:
            kwargs["disabled"] = True
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class CourseLessonPrerequisiteAdmin(admin.ModelAdmin):
    list_display = (
        "course",
        "source_order",
        "target_order",
        "required_percentage",
    )
    list_filter = ("course",)
    search_fields = ("course__name",)
    ordering = ("course", "target_order", "source_order")


class CourseLessonProgressAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "lesson",
        "is_unlocked",
        "percentage",
    )
    list_filter = ("is_unlocked", "lesson__course")
    search_fields = ("user__user__username", "lesson__title")
    ordering = ("lesson__course", "lesson__order", "user")
    raw_id_fields = ("user", "lesson")
