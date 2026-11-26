import os
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.urls import reverse

from judge.models import Problem, Contest
from judge.models.profile import Organization, Profile
from judge.caching import cache_wrapper

from judge.utils.files import generate_secure_filename
from judge.utils.identity import ImmutableIdentityMixin


def course_image_path(course, filename):
    new_filename = generate_secure_filename(filename, f"course_{course.id}")
    return os.path.join(settings.DMOJ_COURSE_IMAGE_ROOT, new_filename)


class RoleInCourse(models.TextChoices):
    STUDENT = "ST", _("Student")
    ASSISTANT = "AS", _("Assistant")
    TEACHER = "TE", _("Teacher")


EDITABLE_ROLES = (RoleInCourse.TEACHER, RoleInCourse.ASSISTANT)


class CourseTag(models.Model):
    """A label to filter courses by: the exam, division, school level or region."""

    class Kind(models.TextChoices):
        EXAM = "exam", _("Exam")
        GROUP = "group", _("Division")
        LEVEL = "level", _("School level")
        REGION = "region", _("Region")

    name = models.CharField(max_length=80, verbose_name=_("tag name"))
    slug = models.SlugField(max_length=100, unique=True, verbose_name=_("slug"))
    kind = models.CharField(
        max_length=10, choices=Kind.choices, db_index=True, verbose_name=_("tag kind")
    )
    order = models.PositiveSmallIntegerField(default=0, verbose_name=_("order"))

    class Meta:
        ordering = ("kind", "order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("kind", "name"), name="course_tag_kind_name"
            )
        ]
        verbose_name = _("course tag")
        verbose_name_plural = _("course tags")

    def __str__(self):
        return f"{self.get_kind_display()}: {self.name}"


MAX_COURSE_ITEM_POINTS = 1000


class Course(models.Model):
    name = models.CharField(
        max_length=128,
        verbose_name=_("course name"),
    )
    about = models.TextField(verbose_name=_("course description"))
    is_public = models.BooleanField(
        verbose_name=_("publicly visible"),
        default=False,
    )
    is_suspended = models.BooleanField(default=False, verbose_name="Tạm ẩn")
    is_featured = models.BooleanField(
        "Khoá học nổi bật",
        default=False,
        db_index=True,
        help_text="Đưa khoá học vào mục Nổi bật cho người có quyền xem.",
    )
    interested_users = models.ManyToManyField(
        Profile, related_name="interested_courses", blank=True, editable=False
    )
    organizations = models.ManyToManyField(
        Organization,
        blank=True,
        verbose_name=_("organizations"),
        help_text=_("If not empty, only these organizations may see the course"),
    )
    slug = models.SlugField(
        max_length=128,
        verbose_name=_("course slug"),
        help_text=_("Course name shown in URL"),
        unique=True,
        validators=[
            RegexValidator(
                "^[-a-zA-Z0-9_]+$", _("Only alphanumeric, hyphens and underscores")
            )
        ],
    )
    is_open = models.BooleanField(
        verbose_name=_("public registration"),
        default=False,
    )
    show_rankings = models.BooleanField(
        "Hiện bảng xếp hạng",
        default=True,
        help_text="Khi tắt, học viên chỉ xem điểm của mình trong Grades của khoá và từng bài học.",
    )
    invite_token = models.CharField(
        max_length=64, blank=True, default="", editable=False
    )
    course_image = models.ImageField(
        verbose_name=_("course image"),
        upload_to=course_image_path,
        null=True,
        blank=True,
    )
    tags = models.ManyToManyField(
        CourseTag, blank=True, related_name="courses", verbose_name=_("course tags")
    )
    featured_order = models.PositiveSmallIntegerField(
        default=0,
        verbose_name=_("featured order"),
        help_text=_("Featured courses are listed from the smallest number."),
    )

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("course_detail", args=(self.slug,))

    @classmethod
    def is_editable_by(cls, course, profile):
        # Admins can edit any course
        if profile and profile.user.is_superuser:
            return True

        try:
            course_role = CourseRole.objects.get(course=course, user=profile)
            return (
                course.allows_role(course_role.role)
                and course_role.role in EDITABLE_ROLES
            )
        except CourseRole.DoesNotExist:
            return False

    @classmethod
    def get_clone_target_courses(cls, profile, term=""):
        """Courses `profile` may clone content into, best targets first (A13).

        Valid targets are exactly what is_editable_by() accepts: courses the
        profile teaches or assists, and every course for a superuser. Courses
        the profile teaches or assists always come first -- for superusers
        too -- then the rest, each group by name and id so pages are stable.
        """
        if profile is None:
            return cls.objects.none()
        managed = CourseRole.objects.filter(
            course=OuterRef("pk"), user=profile, role__in=EDITABLE_ROLES
        )
        if not profile.user.is_superuser:
            managed = managed.filter(course__in=cls.get_accessible_courses(profile))
        queryset = cls.objects.annotate(is_managed=Exists(managed))
        if not profile.user.is_superuser:
            queryset = queryset.filter(is_managed=True)
        if term:
            queryset = queryset.filter(
                Q(name__icontains=term) | Q(slug__icontains=term)
            )
        return queryset.order_by("-is_managed", "name", "id")

    @classmethod
    def is_accessible_by(cls, course, profile):
        """Check if a user can access a course. Only enrolled users can access courses."""
        if not profile:
            return False

        if profile.user.is_superuser:
            return True

        role = (
            CourseRole.objects.filter(course=course, user=profile)
            .values_list("role", flat=True)
            .first()
        )
        return course.allows_role(role)

    @cached_property
    def access_paused(self):
        return (
            self.is_suspended or not self.active_courses().filter(pk=self.pk).exists()
        )

    def allows_role(self, role):
        return bool(role) and (role == RoleInCourse.TEACHER or not self.access_paused)

    @classmethod
    def active_courses(cls):
        links = cls.organizations.through.objects.filter(course_id=OuterRef("pk"))
        return cls.objects.filter(is_suspended=False).filter(
            ~Exists(links) | Exists(links.filter(organization__is_suspended=False))
        )

    @classmethod
    def is_joinable(cls, course, profile):
        """Check if a user can join an open course."""
        return course.is_open and course.can_apply(profile)

    def can_apply(self, profile):
        """Both self-enrollment modes share the same visibility and audience."""
        if not profile:
            return False

        if profile.user.is_superuser:
            return False  # Admins don't need to join courses

        # User must not already be enrolled
        if not self.is_public or self.is_suspended:
            return False
        if not self.is_in_registration_audience(profile):
            return False
        return not CourseRole.objects.filter(course=self, user=profile).exists()

    def is_in_registration_audience(self, profile):
        organizations = dict(self.organizations.values_list("id", "is_suspended"))
        return not organizations or bool(
            profile
            and any(
                organizations.get(org_id) is False
                for org_id in profile.get_organization_ids()
            )
        )

    @classmethod
    def get_accessible_courses(cls, profile):
        """Get courses that a user can access (only enrolled courses)."""
        # Admins can access all courses
        if profile and profile.user.is_superuser:
            return Course.objects.all()

        if not profile:
            return Course.objects.none()

        return cls.objects.filter(
            Q(courserole__role=RoleInCourse.TEACHER) | Q(pk__in=cls.active_courses()),
            courserole__user=profile,
        ).distinct()

    def is_visible_to(self, profile):
        """Discoverability of one course: the per-object twin of
        get_visible_courses() (A05). Enrolled users and superusers always see
        it; otherwise it must be public and either belong to no group or to a
        group the viewer is a member of. Opening lessons still requires
        enrolment (is_accessible_by)."""
        if self.is_accessible_by(self, profile):
            return True
        return (
            self.is_public
            and not self.is_suspended
            and self.is_in_registration_audience(profile)
        )

    @classmethod
    def get_visible_courses(cls, profile):
        """Get every course a user is allowed to see (enrolled + publicly visible)."""
        # Admins can see all courses
        if profile and profile.user.is_superuser:
            return Course.objects.all()

        public_courses = cls._public_courses_in_scope(profile)
        if not profile:
            return public_courses
        return cls.objects.filter(
            Q(pk__in=public_courses) | Q(pk__in=cls.get_accessible_courses(profile))
        )

    @classmethod
    def _public_courses_in_scope(cls, profile):
        audience = Q(organizations__isnull=True)
        if profile:
            audience |= Q(
                organizations__id__in=profile.get_organization_ids(),
                organizations__is_suspended=False,
            )
        return cls.objects.filter(
            audience, is_public=True, is_suspended=False
        ).distinct()

    @classmethod
    def get_joinable_courses(cls, profile):
        """Courses open to direct enrollment or an enrollment request."""
        queryset = cls._public_courses_in_scope(profile)
        return queryset.exclude(courserole__user=profile) if profile else queryset

    @classmethod
    def get_user_courses(cls, profile):
        """Get courses where user is enrolled (has a role)"""
        if not profile:
            return Course.objects.none()

        return (
            cls.get_accessible_courses(profile)
            .filter(courserole__user=profile)
            .distinct()
        )

    def _get_users_by_role(self, role):
        profile_ids = get_course_role_profile_ids(self.id, role)
        return Profile.get_cached_instances(*profile_ids)

    def get_students(self):
        return self._get_users_by_role(RoleInCourse.STUDENT)

    def get_assistants(self):
        return self._get_users_by_role(RoleInCourse.ASSISTANT)

    def get_teachers(self):
        return self._get_users_by_role(RoleInCourse.TEACHER)

    @classmethod
    def add_student(cls, course, profiles):
        for profile in profiles:
            CourseRole.make_role(course=course, user=profile, role="ST")

    @classmethod
    def add_teachers(cls, course, profiles):
        for profile in profiles:
            CourseRole.make_role(course=course, user=profile, role="TE")

    @classmethod
    def add_assistants(cls, course, profiles):
        for profile in profiles:
            CourseRole.make_role(course=course, user=profile, role="AS")

    def get_lessons(self):
        return self.lessons.filter(is_visible=True).order_by("order")

    def get_contests(self):
        return (
            self.contests.select_related("contest")
            .defer("contest__description")
            .filter(
                contest__is_visible=True,
                contest__library_mock_paper__isnull=True,
                contest__library_mock_archive__isnull=True,
            )
            .order_by("order")
        )


class CourseRole(models.Model):
    course = models.ForeignKey(
        Course,
        verbose_name=_("course"),
        on_delete=models.CASCADE,
        db_index=True,
    )
    user = models.ForeignKey(
        Profile,
        verbose_name=_("user"),
        on_delete=models.CASCADE,
        related_name="course_roles",
    )

    role = models.CharField(
        max_length=2,
        choices=RoleInCourse.choices,
        default=RoleInCourse.STUDENT,
    )

    needs_progress_recalculation = models.BooleanField(
        default=True,
        verbose_name=_("needs progress recalculation"),
        help_text=_(
            "Whether the user's lesson progress and unlock states need to be recalculated"
        ),
    )

    @classmethod
    def make_role(self, course, user, role):
        userqueryset = CourseRole.objects.filter(course=course, user=user)
        if userqueryset.exists():
            userqueryset[0].role = role
        else:
            couresrole = CourseRole()
            couresrole.course = course
            couresrole.user = user
            couresrole.role = role
            couresrole.save()

    class Meta:
        unique_together = ("course", "user")


class CourseJoinRequest(models.Model):
    class State(models.TextChoices):
        PENDING = "P", "Đang chờ duyệt"
        APPROVED = "A", "Đã duyệt"
        REJECTED = "R", "Bị từ chối"

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="join_requests"
    )
    user = models.ForeignKey(Profile, on_delete=models.CASCADE)
    state = models.CharField(max_length=1, choices=State.choices, default=State.PENDING)
    requested_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey(
        Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["course", "user"], name="unique_course_join_request"
            ),
        ]
        indexes = [models.Index(fields=["course", "state", "requested_at"])]


class CourseVisit(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    last_visit_time = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "course"], name="unique_course_visit"
            ),
        ]
        indexes = [
            models.Index(
                fields=["profile", "-last_visit_time", "-id"],
                name="course_visit_recent_idx",
            ),
        ]

    @classmethod
    def record(cls, profile_id, course_id):
        visits = cls.objects.filter(profile_id=profile_id).order_by(
            "-last_visit_time", "-pk"
        )
        if visits.values_list("course_id", flat=True).first() == course_id:
            return
        try:
            with transaction.atomic():
                cls.objects.update_or_create(
                    profile_id=profile_id,
                    course_id=course_id,
                    defaults={"last_visit_time": timezone.now},
                )
        except IntegrityError:
            # Two concurrent first visits (tabs, prefetch) race on the unique
            # (profile, course) row; the page already rendered, so losing the
            # race must not turn a 200 into a 500.
            cls.objects.filter(profile_id=profile_id, course_id=course_id).update(
                last_visit_time=timezone.now()
            )


class CourseLesson(models.Model):
    MAX_LESSONS_PER_COURSE = 100

    course = models.ForeignKey(
        Course,
        verbose_name=_("course"),
        related_name="lessons",
        on_delete=models.CASCADE,
    )
    title = models.TextField(verbose_name=_("lesson title"))
    content = models.TextField(verbose_name=_("lesson content"))
    order = models.IntegerField(verbose_name=_("order"), default=0)
    points = models.IntegerField(
        verbose_name=_("points"),
        validators=[MinValueValidator(0), MaxValueValidator(MAX_COURSE_ITEM_POINTS)],
    )
    is_visible = models.BooleanField(verbose_name=_("publicly visible"), default=True)
    exclude_from_grades = models.BooleanField(
        "Không tính vào Grades của khoá",
        default=False,
        help_text="Điểm riêng của bài học và điều kiện mở khoá vẫn được giữ.",
    )
    # A04: enforced server-side by judge/utils/testcase_visibility.py, for every
    # submission of this lesson's problems, wherever it was submitted from.
    hide_testcases = models.BooleanField(
        verbose_name=_("hide test data"),
        default=False,
        help_text=_(
            "Students cannot see test input, expected output or checker feedback "
            "for this lesson's problems, on any submission of them. Verdicts, "
            "time, memory and points stay visible. Teachers, assistants and "
            "problem editors still see everything."
        ),
    )

    def clean(self):
        super().clean()
        # Check if course is set before accessing it
        if not hasattr(self, "course_id") or not self.course_id:
            return

        # Limit the number of lessons per course
        if not self.pk:  # Only check on creation
            lesson_count = CourseLesson.objects.filter(course_id=self.course_id).count()
            if lesson_count >= self.MAX_LESSONS_PER_COURSE:
                raise ValidationError(
                    _("A course cannot have more than %(max)s lessons.")
                    % {"max": self.MAX_LESSONS_PER_COURSE}
                )

    def get_absolute_url(self):
        return reverse(
            "course_lesson_detail",
            args=(
                self.course.slug,
                self.id,
            ),
        )

    def get_problems(self):
        """
        Get all problem IDs for this lesson in order.
        Returns a list of problem IDs.
        """
        return Problem.get_cached_instances(
            *[p["problem_id"] for p in self.get_problems_and_scores()]
        )

    @cache_wrapper(prefix="CLgps", expected_type=list)
    def get_problems_and_scores(self):
        """
        Get all problems with their scores for this lesson in order.
        Returns a list of dictionaries with problem_id and score.
        """
        return list(
            self.lesson_problems.order_by("order").values("problem_id", "score")
        )

    def save(self, *args, **kwargs):
        is_new = not self.pk

        # For new lessons, set order to max + 1 if not specified or invalid
        if is_new and hasattr(self, "course_id") and self.course_id:
            if self.order < 1:
                max_order = (
                    CourseLesson.objects.filter(course_id=self.course_id).aggregate(
                        models.Max("order")
                    )["order__max"]
                    or 0
                )
                self.order = max_order + 1

        # Track if points changed (affects grades)
        if self.pk:
            try:
                old_instance = CourseLesson.objects.get(pk=self.pk)
                points_changed = old_instance.points != self.points
            except CourseLesson.DoesNotExist:
                points_changed = False
        else:
            points_changed = False

        super().save(*args, **kwargs)

        # Mark for recalculation if points changed
        if points_changed:
            from judge.utils.course_prerequisites import mark_course_for_recalculation

            mark_course_for_recalculation(self.course)

    def delete(self, *args, **kwargs):
        course = self.course
        super().delete(*args, **kwargs)
        # Mark all users for recalculation when lesson is deleted
        from judge.utils.course_prerequisites import mark_course_for_recalculation

        mark_course_for_recalculation(course)


class CourseLessonProblem(ImmutableIdentityMixin, models.Model):
    immutable_identity_fields = ("lesson_id", "problem_id")

    lesson = models.ForeignKey(
        CourseLesson, on_delete=models.CASCADE, related_name="lesson_problems"
    )
    problem = models.ForeignKey(
        Problem, verbose_name=_("problem"), on_delete=models.CASCADE
    )
    order = models.IntegerField(verbose_name=_("order"), default=0)
    score = models.IntegerField(
        verbose_name=_("score"),
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(MAX_COURSE_ITEM_POINTS)],
    )

    def save(self, *args, **kwargs):
        self.validate_immutable_identity()
        super().save(*args, **kwargs)
        # Mark all users for recalculation when lesson content changes
        from judge.utils.course_prerequisites import mark_course_for_recalculation

        mark_course_for_recalculation(self.lesson.course)

    def delete(self, *args, **kwargs):
        course = self.lesson.course
        super().delete(*args, **kwargs)
        # Mark all users for recalculation when lesson content changes
        from judge.utils.course_prerequisites import mark_course_for_recalculation

        mark_course_for_recalculation(course)


class CourseContest(models.Model):
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="contests"
    )
    contest = models.ForeignKey(
        Contest, unique=True, on_delete=models.CASCADE, related_name="course"
    )
    order = models.IntegerField(verbose_name=_("order"), default=0)
    exclude_from_grades = models.BooleanField(
        "Không tính vào Grades của khoá", default=False
    )
    points = models.IntegerField(
        verbose_name=_("points"),
        validators=[MinValueValidator(0), MaxValueValidator(MAX_COURSE_ITEM_POINTS)],
    )

    def get_course_of_contest(contest):
        course_contest = contest.course.get()
        course = course_contest.course
        return course


@cache_wrapper(prefix="CRubr", expected_type=list)
def get_course_role_profile_ids(course_id, role):
    """
    Get profile IDs for users with a specific role in a course.
    This function is cached and will be invalidated when CourseRole changes.
    """
    return list(
        CourseRole.objects.filter(course_id=course_id, role=role).values_list(
            "user_id", flat=True
        )
    )


class CourseLessonPrerequisite(models.Model):
    """
    Prerequisite edge: user needs required_percentage% in lesson at source_order
    to unlock lesson at target_order.
    Uses order (integer) instead of ForeignKey for simplicity.
    """

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="lesson_prerequisites",
        verbose_name=_("course"),
    )
    source_order = models.IntegerField(
        verbose_name=_("source lesson order"),
        help_text=_("Order of the prerequisite lesson"),
    )
    target_order = models.IntegerField(
        verbose_name=_("target lesson order"),
        help_text=_("Order of the lesson to unlock"),
    )
    required_percentage = models.FloatField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name=_("required percentage"),
        help_text=_("Minimum grade percentage required in source lesson (0-100)"),
    )

    class Meta:
        unique_together = ("course", "source_order", "target_order")
        verbose_name = _("Lesson Prerequisite")
        verbose_name_plural = _("Lesson Prerequisites")

    def clean(self):
        super().clean()
        if self.source_order >= self.target_order:
            raise ValidationError(
                _("Source lesson order must be less than target lesson order.")
            )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Mark all users for recalculation when prerequisites change
        from judge.utils.course_prerequisites import mark_course_for_recalculation

        mark_course_for_recalculation(self.course)

    def delete(self, *args, **kwargs):
        course = self.course
        super().delete(*args, **kwargs)
        # Mark all users for recalculation when prerequisite is deleted
        from judge.utils.course_prerequisites import mark_course_for_recalculation

        mark_course_for_recalculation(course)

    def __str__(self):
        return f"{self.course.slug}: Lesson {self.source_order} -> Lesson {self.target_order} ({self.required_percentage}%)"


class CourseLessonProgress(models.Model):
    """
    Tracks user (Profile) progress and unlock state for each lesson.
    """

    user = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="lesson_progress",
        verbose_name=_("user"),
    )
    lesson = models.ForeignKey(
        CourseLesson,
        on_delete=models.CASCADE,
        related_name="user_progress",
        verbose_name=_("lesson"),
    )
    is_unlocked = models.BooleanField(
        default=False,
        verbose_name=_("is unlocked"),
        help_text=_("Whether the user has unlocked this lesson"),
    )
    percentage = models.FloatField(
        default=0,
        verbose_name=_("percentage"),
        help_text=_("User's grade percentage for this lesson"),
    )

    class Meta:
        unique_together = ("user", "lesson")
        verbose_name = _("Lesson Progress")
        verbose_name_plural = _("Lesson Progress Records")

    def __str__(self):
        status = "unlocked" if self.is_unlocked else "locked"
        return f"{self.user.user.username} - {self.lesson.title} ({status}, {self.percentage:.1f}%)"
