import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone


class ExamSeries(models.Model):
    title = models.CharField("Tên kỳ thi", max_length=180)
    slug = models.SlugField(max_length=100, unique=True)
    category = models.CharField("Loại kỳ thi", max_length=60, db_index=True)
    region = models.CharField("Địa bàn", max_length=80, db_index=True)
    level = models.CharField("Cấp học", max_length=60, blank=True, db_index=True)
    summary = models.TextField("Giới thiệu", blank=True)
    cover_url = models.URLField("URL ảnh đại diện", blank=True, max_length=500)
    guide = models.ForeignKey(
        "BlogPost", null=True, blank=True, on_delete=models.SET_NULL
    )
    is_published = models.BooleanField("Đã công bố", default=False, db_index=True)

    class Meta:
        ordering = ("title", "pk")
        verbose_name = "Kỳ thi trong thư viện"
        verbose_name_plural = "Kỳ thi trong thư viện"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("exam_series", args=[self.slug])


class ExamDivision(models.Model):
    name = models.CharField("Tên bảng", max_length=80, unique=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Bảng thi"
        verbose_name_plural = "Bảng thi"

    def __str__(self):
        return self.name


class ExamGroup(models.Model):
    """A section of an exam series that gathers one or more divisions."""

    series = models.ForeignKey(
        ExamSeries, on_delete=models.CASCADE, related_name="groups"
    )
    title = models.CharField("Tên nhóm bảng", max_length=80)
    slug = models.SlugField("Đường dẫn", max_length=80)
    order = models.PositiveSmallIntegerField("Thứ tự", default=0)
    divisions = models.ManyToManyField(ExamDivision, verbose_name="Gồm các bảng")

    class Meta:
        ordering = ("order", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("series", "slug"), name="exam_group_series_slug"
            )
        ]
        verbose_name = "Nhóm bảng"
        verbose_name_plural = "Nhóm bảng"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("exam_group", args=[self.series.slug, self.slug])


class ExamPaper(models.Model):
    series = models.ForeignKey(
        ExamSeries, on_delete=models.PROTECT, related_name="papers"
    )
    title = models.CharField("Tên bộ đề", max_length=180)
    slug = models.SlugField(max_length=100)
    year = models.PositiveSmallIntegerField(
        "Năm",
        db_index=True,
        validators=[MinValueValidator(1900), MaxValueValidator(2200)],
    )
    academic_year = models.BooleanField("Hiển thị dạng năm học", default=True)
    round_name = models.CharField("Vòng thi", max_length=60, blank=True, db_index=True)
    divisions = models.ManyToManyField(
        ExamDivision, blank=True, verbose_name="Bảng thi"
    )
    original_date = models.DateField("Ngày thi gốc", blank=True, null=True)
    duration_minutes = models.PositiveSmallIntegerField(
        "Thời lượng gốc (phút)",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    lesson = models.ForeignKey(
        "CourseLesson", null=True, blank=True, on_delete=models.PROTECT
    )
    contest = models.ForeignKey(
        "Contest", null=True, blank=True, on_delete=models.PROTECT
    )
    pdf_url = models.URLField(
        "URL PDF đề gốc được phép chia sẻ", blank=True, max_length=500
    )
    guide = models.ForeignKey(
        "BlogPost", null=True, blank=True, on_delete=models.SET_NULL
    )
    comments = GenericRelation("Comment")
    discussion_enabled = models.BooleanField("Cho phép thảo luận", default=True)
    is_published = models.BooleanField("Đã công bố", default=False, db_index=True)
    list_metadata = models.BooleanField(
        "Công bố tên bộ đề cho khách",
        default=False,
        help_text="Chỉ công bố thông tin tra cứu. Nội dung vẫn theo quyền Lesson hoặc Contest.",
    )
    mock_contest = models.OneToOneField(
        "Contest",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="library_mock_paper",
    )
    mock_minutes = models.PositiveSmallIntegerField(
        "Thời lượng thi thử (phút)",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
    )
    mock_limit = models.PositiveSmallIntegerField(
        "Lượt nộp mỗi bài (0 là không giới hạn)", default=0
    )
    mock_delayed = models.BooleanField("Chấm và trả kết quả sau hết giờ", default=False)

    class Meta:
        ordering = ("-year", "round_name", "title", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("series", "slug"), name="exam_paper_series_slug"
            ),
            models.CheckConstraint(
                condition=(
                    Q(lesson__isnull=False, contest__isnull=True)
                    | Q(lesson__isnull=True, contest__isnull=False)
                ),
                name="exam_paper_one_source",
            ),
        ]
        verbose_name = "Bộ đề thư viện"
        verbose_name_plural = "Bộ đề thư viện"

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.mock_delayed:
            if not settings.EXAM_LIBRARY_DELAYED_GRADING_ENABLED:
                raise ValidationError(
                    {
                        "mock_delayed": "Quản trị hệ thống cần xác nhận bộ lập lịch trước khi bật chấm sau giờ."
                    }
                )
            if not self.mock_limit:
                raise ValidationError(
                    {
                        "mock_limit": "Chấm sau giờ cần giới hạn lượt nộp mỗi bài lớn hơn 0."
                    }
                )

    @property
    def year_label(self):
        return f"{self.year}–{self.year + 1}" if self.academic_year else str(self.year)

    @property
    def year_in_title(self):
        year = (
            rf"{self.year}\s*[-–—/]\s*{self.year + 1}"
            if self.academic_year
            else str(self.year)
        )
        return re.search(rf"(?<!\d){year}(?!\d)", self.title) is not None

    def get_absolute_url(self):
        return reverse("exam_paper", args=[self.series.slug, self.slug])

    def get_author_ids(self):
        # Library papers have no assigned authors. Site moderators handle
        # discussion moderation through the usual change_comment permission.
        return ()


class ExamSeriesInterest(models.Model):
    series = models.ForeignKey(ExamSeries, on_delete=models.CASCADE)
    user = models.ForeignKey("Profile", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("series", "user"), name="exam_series_user_interest"
            )
        ]


class ExamPaperMark(models.Model):
    paper = models.ForeignKey(ExamPaper, on_delete=models.CASCADE)
    user = models.ForeignKey("Profile", on_delete=models.CASCADE)
    saved = models.BooleanField(default=False)
    revisit = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("paper", "user"), name="exam_paper_user_mark"
            )
        ]


class ExamMockSession(models.Model):
    paper = models.ForeignKey(
        ExamPaper, on_delete=models.PROTECT, related_name="mock_sessions"
    )
    participation = models.OneToOneField(
        "ContestParticipation",
        on_delete=models.CASCADE,
        related_name="library_mock_session",
    )
    delayed = models.BooleanField()
    submission_limit = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("-pk",)

    @property
    def deadline(self):
        return self.participation.library_deadline

    @property
    def active(self):
        return self.deadline > timezone.now() and not self.participation.is_disqualified

    def clean(self):
        if self.participation_id and self.participation.virtual <= 0:
            raise ValidationError("Phiên thi thử cần dùng lượt tham gia ảo.")


class ExamMockArchive(models.Model):
    paper = models.ForeignKey(
        ExamPaper, on_delete=models.PROTECT, related_name="mock_archives"
    )
    contest = models.OneToOneField(
        "Contest", on_delete=models.PROTECT, related_name="library_mock_archive"
    )


class ExamDeferredSubmission(models.Model):
    submission = models.OneToOneField(
        "Submission", on_delete=models.CASCADE, related_name="exam_deferred"
    )
    ready_at = models.DateTimeField(db_index=True)
    dispatched_at = models.DateTimeField(null=True)
    claim_until = models.DateTimeField(null=True)
