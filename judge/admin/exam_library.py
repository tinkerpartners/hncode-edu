from types import SimpleNamespace
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import AutocompleteSelect, AutocompleteSelectMultiple
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Exists, OuterRef, Value, When
from django.forms import formset_factory
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from judge.models import (
    Contest,
    Course,
    CourseLesson,
    ExamDivision,
    ExamGroup,
    ExamPaper,
    ExamSeries,
    Submission,
)
from judge.utils.exam_covers import build_cover_thumbnail
from judge.utils.exam_library import (
    LibraryAccess,
    active_mock_sessions,
    create_mock_contest,
    mock_configuration_error,
    mock_entry_available,
    mock_publication_error,
    mock_user_is_banned,
)
from judge.utils.exam_import import import_sources


class ImportForm(forms.Form):
    series = forms.ModelChoiceField(ExamSeries.objects.all(), label="Kỳ thi")
    course = forms.ModelChoiceField(
        Course.objects.all(), required=False, label="Khóa học"
    )
    contests = forms.ModelMultipleChoiceField(
        Contest.objects.exclude(key__startswith="library_mock_"),
        required=False,
        label="Contest",
    )

    def __init__(self, *args, admin_site, **kwargs):
        super().__init__(*args, **kwargs)
        for name, widget, field in (
            ("series", AutocompleteSelect, ExamPaper._meta.get_field("series")),
            ("course", AutocompleteSelect, CourseLesson._meta.get_field("course")),
            (
                "contests",
                AutocompleteSelectMultiple,
                ExamPaper._meta.get_field("contest"),
            ),
        ):
            self.fields[name].widget = widget(
                field, admin_site, attrs={"style": "width: min(100%, 29rem)"}
            )
            self.fields[name].widget.choices = self.fields[name].choices

    def clean(self):
        data = super().clean()
        if not self.errors and bool(data.get("course")) == bool(data.get("contests")):
            raise forms.ValidationError(
                "Chọn một nguồn: Course hoặc danh sách Contest."
            )
        return data


class ImportPaperForm(forms.Form):
    source_id = forms.IntegerField(widget=forms.HiddenInput)
    selected = forms.BooleanField(required=False, label="Chọn bộ đề")
    title = forms.CharField(required=False, max_length=180, label="Tên bộ đề")
    year = forms.IntegerField(
        required=False, min_value=1900, max_value=2200, label="Năm"
    )
    academic_year = forms.BooleanField(required=False, label="Hiển thị theo năm học")
    round_name = forms.CharField(required=False, max_length=60, label="Vòng thi")
    divisions = forms.MultipleChoiceField(required=False, label="Bảng thi")

    def __init__(self, *args, division_choices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["divisions"].choices = division_choices
        self.fields["divisions"].widget.attrs["class"] = "exam-division-select"
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs["aria-label"] = field.label

    def clean(self):
        data = super().clean()
        if data.get("selected"):
            for field in ("title", "year"):
                if field not in self.errors and not data.get(field):
                    self.add_error(field, "Bổ sung thông tin cho bộ đề đã chọn.")
        return data


ImportPaperFormSet = formset_factory(
    ImportPaperForm, extra=0, max_num=200, validate_max=True, absolute_max=200
)


class ExamGroupInline(admin.TabularInline):
    model = ExamGroup
    extra = 0
    fields = ("title", "slug", "order", "divisions")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("divisions",)


@admin.register(ExamSeries)
class ExamSeriesAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "region", "level", "is_published")
    list_filter = ("category", "region", "level", "is_published")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("guide",)
    inlines = (ExamGroupInline,)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        transaction.on_commit(lambda: build_cover_thumbnail(obj.cover_url))


@admin.register(ExamDivision)
class ExamDivisionAdmin(admin.ModelAdmin):
    search_fields = ("name",)


class MockStatusFilter(admin.SimpleListFilter):
    title = "Thi thử"
    parameter_name = "mock"

    def lookups(self, request, model_admin):
        return (("configured", "Đã tạo Contest"), ("pending", "Chưa tạo Contest"))

    def queryset(self, request, queryset):
        if self.value() in ("configured", "pending"):
            return queryset.filter(mock_contest__isnull=self.value() == "pending")
        return queryset


@admin.register(ExamPaper)
class ExamPaperAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "series",
        "year",
        "source_link",
        "mock_status",
        "mock_overview_link",
    )
    list_filter = (
        "series",
        "year",
        "round_name",
        "divisions",
        "is_published",
        MockStatusFilter,
    )
    list_select_related = ("series", "lesson__course", "contest")
    search_fields = (
        "title",
        "slug",
        "series__title",
        "contest__key",
        "contest__name",
        "lesson__title",
        "lesson__course__slug",
    )
    autocomplete_fields = ("series", "contest")
    raw_id_fields = ("lesson", "guide")
    filter_horizontal = ("divisions",)
    readonly_fields = ("mock_contest", "source_link", "mock_configuration")
    fieldsets = (
        (
            "Thông tin bộ đề",
            {
                "fields": (
                    "series",
                    "title",
                    "slug",
                    ("year", "academic_year"),
                    "round_name",
                    "divisions",
                    ("original_date", "duration_minutes"),
                )
            },
        ),
        (
            "Nguồn danh sách bài",
            {
                "fields": ("lesson", "contest", "source_link"),
                "description": "Chọn một Lesson hoặc Contest. Sửa danh sách bài và điểm tại nguồn.",
            },
        ),
        (
            "Thi thử",
            {
                "fields": (
                    "mock_configuration",
                    "mock_minutes",
                    "mock_limit",
                    "mock_delayed",
                    "mock_contest",
                ),
                "description": "Nếu để trống thời lượng thi thử, hệ thống dùng thời lượng đề gốc khi bật. Mỗi học sinh có một đồng hồ riêng bắt đầu khi vào thi.",
            },
        ),
        (
            "Hiển thị và tài liệu",
            {
                "fields": (
                    "is_published",
                    "list_metadata",
                    "pdf_url",
                    "guide",
                    "discussion_enabled",
                )
            },
        ),
    )
    actions = ("publish_papers", "build_mock_contests")
    change_list_template = "admin/judge/exampaper/change_list.html"
    change_form_template = "admin/judge/exampaper/change_form.html"

    @admin.display(description="Nguồn")
    def source_link(self, paper):
        if paper.lesson_id:
            url = reverse(
                "edit_course_lessons_new",
                args=[paper.lesson.course.slug, paper.lesson_id],
            )
            return format_html('<a href="{}">Lesson · {}</a>', url, paper.lesson.title)
        if paper.contest_id:
            url = reverse("admin:judge_contest_change", args=[paper.contest_id])
            return format_html('<a href="{}">Contest · {}</a>', url, paper.contest.key)
        return "Chưa chọn nguồn"

    @admin.display(description="Thi thử", ordering="mock_contest_id")
    def mock_status(self, paper):
        issue = mock_publication_error(paper)
        if issue:
            return issue
        if paper.mock_contest_id and paper.mock_minutes:
            return f"Đã tạo · {paper.mock_minutes} phút"
        return "Chưa bật thi thử"

    @admin.display(description="Theo dõi")
    def mock_overview_link(self, paper):
        url = reverse("admin:judge_exampaper_mock", args=[paper.pk])
        return format_html('<a href="{}">Trạng thái và lượt thi</a>', url)

    @admin.display(description="Trạng thái thi thử")
    def mock_configuration(self, paper):
        if not paper.pk:
            return "Chưa bật thi thử."
        if paper.mock_contest_id:
            issue = mock_publication_error(paper) or mock_configuration_error(paper)
        else:
            issue = "Chưa bật thi thử."
        return format_html(
            "{} {}",
            issue or "Đã bật thi thử. Quyền vào thi phụ thuộc nguồn và tài khoản.",
            self.mock_overview_link(paper),
        )

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.mock_contest_id:
            return (*self.readonly_fields, "lesson", "contest")
        return self.readonly_fields

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("build_mock_contests", None)
        return actions

    @admin.action(description="Công bố bộ đề đã chọn")
    def publish_papers(self, request, queryset):
        queryset.update(is_published=True)

    @admin.action(description="Bật thi thử cho các bộ đề đã chọn")
    def build_mock_contests(self, request, queryset):
        if not request.user.is_superuser:
            raise PermissionDenied
        for paper in queryset.select_related("series", "lesson__course", "contest"):
            self.enable_mock(request, paper)

    def enable_mock(self, request, paper):
        try:
            create_mock_contest(paper, request.profile, use_original_duration=True)
        except ValidationError as error:
            self.message_user(
                request, f"{paper}: {error.messages[0]}", level=messages.ERROR
            )
        else:
            self.message_user(
                request, f"{paper}: đã bật thi thử.", level=messages.SUCCESS
            )

    @staticmethod
    def _mock_requested(request):
        return (
            request.user.is_superuser and request.POST.get("enable_mock_on_save") == "1"
        )

    def response_post_save_add(self, request, obj):
        if self._mock_requested(request):
            return self.response_post_save_change(request, obj)
        return super().response_post_save_add(request, obj)

    def response_post_save_change(self, request, obj):
        if self._mock_requested(request):
            self.enable_mock(request, obj)
            return redirect("admin:judge_exampaper_change", obj.pk)
        return super().response_post_save_change(request, obj)

    def get_urls(self):
        return [
            path(
                "import-sources/",
                self.admin_site.admin_view(self.import_view),
                name="judge_exampaper_import",
            ),
            path(
                "<int:object_id>/mock/",
                self.admin_site.admin_view(self.mock_overview),
                name="judge_exampaper_mock",
            ),
            path(
                "<int:object_id>/mock/sync/",
                self.admin_site.admin_view(self.sync_mock),
                name="judge_exampaper_mock_sync",
            ),
        ] + super().get_urls()

    @staticmethod
    def student_mock_status(paper, username, now):
        student = (
            User.objects.filter(username__iexact=username, is_active=True)
            .select_related("profile__current_contest")
            .first()
        )
        profile = getattr(student, "profile", None)
        if not profile or student.is_staff or student.is_superuser:
            return "Chọn một tài khoản học sinh đang hoạt động."
        request = SimpleNamespace(user=student, profile=profile)
        issue = mock_publication_error(paper)
        if issue:
            return issue
        if not LibraryAccess(request).allows_source(paper):
            return "Học sinh chưa có quyền vào nguồn hoặc chưa đạt điều kiện mở Lesson."
        current = profile.current_contest
        active = active_mock_sessions(profile, now).first()
        if (
            current
            and not current.ended
            and (not active or current.pk != active.participation_id)
        ):
            return "Học sinh đang tham gia một Contest khác."
        if active:
            if active.paper_id != paper.pk:
                return "Học sinh đang thi thử một bộ đề khác."
            contest = active.participation.contest
            if contest.is_accessible_by(student) and not mock_user_is_banned(
                profile, paper, contest
            ):
                return "Học sinh có thể tiếp tục lượt thi hiện tại."
            return "Học sinh không còn quyền vào lượt thi hiện tại."
        issue = mock_configuration_error(paper)
        if issue:
            return issue
        if not mock_entry_available(request, paper):
            return "Tài khoản bị giới hạn quyền vào Contest thi thử."
        return "Học sinh thấy nút Thi thử và có thể bắt đầu lượt mới."

    def mock_overview(self, request, object_id):
        paper = self.get_object(request, object_id)
        if not paper:
            raise Http404
        if not self.has_view_or_change_permission(request, paper):
            raise PermissionDenied
        now = timezone.now()
        pending = Submission.objects.filter(
            contest__participation_id=OuterRef("participation_id"),
            status__in=Submission.IN_PROGRESS_GRADING_STATUS,
        )
        sessions = paper.mock_sessions.select_related(
            "participation__user__user", "participation__contest"
        ).annotate(
            pending=Exists(pending),
            state=Case(
                When(participation__is_disqualified=True, then=Value("invalid")),
                When(participation__library_deadline__gt=now, then=Value("active")),
                When(pending=True, then=Value("pending")),
                default=Value("finished"),
            ),
        )
        states = {
            "active": "Đang thi",
            "pending": "Chờ chấm",
            "finished": "Đã kết thúc",
            "invalid": "Đã hủy",
        }
        username = request.GET.get("student", "").strip()[:150]
        search = request.GET.get("q", "").strip()[:150]
        status = request.GET.get("status", "")
        if search:
            sessions = sessions.filter(
                participation__user__user__username__icontains=search
            )
        if status in states:
            sessions = sessions.filter(state=status)
        page = Paginator(sessions, 50).get_page(request.GET.get("page"))
        for session in page.object_list:
            session.status_label = states[session.state]
        versions = [
            archive.contest
            for archive in paper.mock_archives.select_related("contest").order_by("-pk")
        ]
        if paper.mock_contest_id:
            versions.insert(0, paper.mock_contest)
        rankings = [
            (
                contest.key,
                reverse("contest_ranking", args=[contest.key]) + "?virtual=1",
                contest.id == paper.mock_contest_id,
            )
            for contest in versions
            if contest.can_see_full_scoreboard(request.user)
        ]
        return render(
            request,
            "admin/judge/exampaper/mock_overview.html",
            {
                **self.admin_site.each_context(request),
                "title": "Trạng thái và lượt thi thử",
                "paper": paper,
                "publication_issue": mock_publication_error(paper),
                "configuration_issue": mock_configuration_error(paper),
                "can_sync": request.user.is_superuser
                and paper.mock_contest_id is not None,
                "student": username,
                "student_status": (
                    self.student_mock_status(paper, username, now) if username else None
                ),
                "q": search,
                "status": status,
                "states": states.items(),
                "page": page,
                "rankings": rankings,
                "page_query": urlencode(
                    {"q": search, "status": status, "student": username}
                ),
                "can_view_participation": request.user.has_perm(
                    "judge.view_contestparticipation"
                ),
            },
        )

    @method_decorator(require_POST)
    def sync_mock(self, request, object_id):
        if not request.user.is_superuser:
            raise PermissionDenied
        paper = ExamPaper.objects.filter(pk=object_id).first()
        if not paper:
            raise Http404
        try:
            current = create_mock_contest(
                paper, request.profile, synchronize=True, use_original_duration=True
            )
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            message = (
                "Đã đồng bộ. Lượt thi mới sẽ dùng cấu hình mới."
                if current.pk != paper.mock_contest_id
                else "Cấu hình đã khớp với nguồn."
            )
            messages.success(request, message)
        return redirect("admin:judge_exampaper_mock", object_id)

    def import_view(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        form = ImportForm(request.POST or None, admin_site=self.admin_site)
        preview = []
        rows_formset = None
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            try:
                rows = import_sources(data["series"], data["course"], data["contests"])
                initial = [
                    dict(
                        row,
                        academic_year=bool(data["course"]),
                        selected=bool(row["year"]),
                    )
                    for row in rows
                    if row["status"] != "Đã có"
                ]
                apply = request.POST.get("apply") == "1"
                rows_formset = ImportPaperFormSet(
                    request.POST if apply else None,
                    initial=initial,
                    prefix="papers",
                    form_kwargs={
                        "division_choices": list(
                            ExamDivision.objects.values_list("id", "name")
                        )
                    },
                )
                pending = {
                    str(entry["source_id"].value()): entry for entry in rows_formset
                }
                preview = [(row, pending.get(str(row["source_id"]))) for row in rows]
                if apply and rows_formset.is_valid():
                    selected = {
                        row["source_id"]: row
                        for row in rows_formset.cleaned_data
                        if row.get("selected")
                    }
                    if not selected:
                        raise ValidationError("Hãy chọn ít nhất một bộ đề để tạo nháp.")
                    result = import_sources(
                        data["series"],
                        data["course"],
                        data["contests"],
                        apply=True,
                        selected=selected,
                    )
                    self.message_user(
                        request,
                        f"Đã thêm {sum(row['created'] for row in result)} bộ đề nháp.",
                    )
                    return redirect(reverse("admin:judge_exampaper_changelist"))
            except ValidationError as error:
                form.add_error(None, error)
        return render(
            request,
            "admin/judge/exampaper/import_sources.html",
            {
                **self.admin_site.each_context(request),
                "title": "Nhập bộ đề từ Lesson hoặc Contest",
                "form": form,
                "preview": preview,
                "rows_formset": rows_formset,
            },
        )
