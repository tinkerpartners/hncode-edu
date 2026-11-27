from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Prefetch, prefetch_related_objects
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.models import (
    Contest,
    ContestParticipation,
    ContestProblem,
    Course,
    CourseContest,
    CourseLessonProblem,
    ExamDeferredSubmission,
    ExamMockArchive,
    ExamMockSession,
    ExamPaper,
    Organization,
    Problem,
    Profile,
)
from judge.utils.course_prerequisites import get_lesson_lock_status

ITEM_LOOKUPS = (
    "lesson__lesson_problems__problem",
    "lesson__lesson_quizzes__quiz",
    "contest__contest_problems__problem",
    "contest__contest_problems__quiz",
)


def prefetch_items(papers):
    """Load the items of many papers for list pages.

    Creating a ``Problem`` reads its shared cache entry, so the entries are
    fetched in one batch first instead of one round trip per problem. List pages
    never show statements, so those stay in the database.
    """
    papers = list(papers)
    if not papers:
        return
    lesson_ids = {paper.lesson_id for paper in papers if paper.lesson_id}
    contest_ids = {paper.contest_id for paper in papers if paper.contest_id}
    problem_ids = set()
    if lesson_ids:
        problem_ids.update(
            CourseLessonProblem.objects.filter(lesson_id__in=lesson_ids)
            .order_by()
            .values_list("problem_id", flat=True)
        )
    if contest_ids:
        problem_ids.update(
            ContestProblem.objects.filter(
                contest_id__in=contest_ids, problem_id__isnull=False
            )
            .order_by()
            .values_list("problem_id", flat=True)
        )
    if problem_ids:
        Problem.prefetch_cache(*problem_ids)
    problems = Problem.objects.defer("description")
    prefetch_related_objects(
        papers,
        *(
            (
                Prefetch(lookup, queryset=problems)
                if lookup.endswith("problem")
                else lookup
            )
            for lookup in ITEM_LOOKUPS
        ),
    )


def paper_queryset(*, include_items=True):
    queryset = ExamPaper.objects.select_related(
        "series",
        "lesson__course",
        "contest",
        "mock_contest",
        "guide",
    ).prefetch_related("divisions")
    if include_items:
        queryset = queryset.prefetch_related(*ITEM_LOOKUPS)
    return queryset


class LibraryAccess:
    def __init__(self, request):
        self.request = request
        self.profile = request.profile
        self.course_ids = set(
            Course.get_accessible_courses(self.profile).values_list("pk", flat=True)
        )
        self.locks = {}
        self.source_access = {}
        self.problem_access = {}
        self.course_edits = {}
        self.editor = request.user.has_perm("judge.change_exampaper")

    def series_visible(self, series):
        return series.is_published or self.editor

    def allows_source(self, paper):
        user = self.request.user
        if paper.contest_id:
            if paper.contest_id not in self.source_access:
                self.source_access[paper.contest_id] = paper.contest.is_accessible_by(
                    user
                ) and paper.contest.can_see_problems(user)
            return self.source_access[paper.contest_id]
        lesson = paper.lesson
        if lesson.course_id not in self.course_ids:
            return False
        course = lesson.course
        if course.pk not in self.course_edits:
            self.course_edits[course.pk] = Course.is_editable_by(course, self.profile)
        if self.course_edits[course.pk]:
            return True
        if not lesson.is_visible:
            return False
        if course.pk not in self.locks:
            self.locks[course.pk] = get_lesson_lock_status(self.profile, course)
        return not self.locks[course.pk].get(lesson.pk, False)

    def listed(self, paper, source_allowed=None):
        if self.editor:
            return True
        if not (paper.series.is_published and paper.is_published):
            return False
        if paper.list_metadata:
            return True
        return self.allows_source(paper) if source_allowed is None else source_allowed

    def prime_problem_access(self, problems):
        """Fill the access cache for many problems with a few queries.

        Mirrors ``Problem.is_accessible_by(user, in_contest=False)`` so the
        catalog does not run several queries for every private problem.
        """
        user = self.request.user
        pending = {}
        for problem in problems:
            if problem.pk in self.problem_access or problem.pk in pending:
                continue
            if problem.is_public and not problem.is_organization_private:
                self.problem_access[problem.pk] = True
            else:
                pending[problem.pk] = problem
        if not pending:
            return
        if not user.is_authenticated:
            self.problem_access.update((pk, False) for pk in pending)
            return
        if user.is_superuser:
            self.problem_access.update((pk, True) for pk in pending)
            return
        ids = list(pending)
        profile = self.profile
        allowed = set(
            Problem.organizations.through.objects.filter(
                problem_id__in=[
                    pk for pk, problem in pending.items() if problem.is_public
                ],
                organization_id__in=Organization.member_content_groups(profile),
            ).values_list("problem_id", flat=True)
        )
        allowed.update(
            Problem.authors.through.objects.filter(
                problem_id__in=ids, profile_id=profile.pk
            ).values_list("problem_id", flat=True)
        )
        allowed.update(
            Problem.curators.through.objects.filter(
                problem_id__in=ids, profile_id=profile.pk
            ).values_list("problem_id", flat=True)
        )
        allowed.update(
            Problem.testers.through.objects.filter(
                problem_id__in=ids, profile_id=profile.pk
            ).values_list("problem_id", flat=True)
        )
        allowed.update(
            CourseLessonProblem.objects.filter(
                problem_id__in=ids,
                lesson__course__in=Course.get_accessible_courses(profile),
            ).values_list("problem_id", flat=True)
        )
        self.problem_access.update((pk, pk in allowed) for pk in ids)

    def visible_items(self, paper):
        items = []
        for item in source_items(paper):
            _, _, problem, quiz, _, _ = item
            if problem:
                if problem.pk not in self.problem_access:
                    self.problem_access[problem.pk] = problem.is_accessible_by(
                        self.request.user, in_contest=False
                    )
                if not self.problem_access[problem.pk]:
                    continue
            if (
                quiz
                and not paper.lesson_id
                and not quiz.is_accessible_by(self.request.user, in_contest=False)
            ):
                continue
            items.append(item)
        return items


def mock_user_is_banned(profile, paper, contest):
    return bool(
        profile
        and (
            contest.banned_users.filter(pk=profile.pk).exists()
            or (
                paper.contest_id
                and paper.contest.banned_users.filter(pk=profile.pk).exists()
            )
        )
    )


def mock_entry_available(request, paper):
    return bool(
        paper.mock_contest_id
        and paper.mock_minutes
        and paper.mock_contest.is_accessible_by(request.user)
        and not paper.mock_contest.access_code
        and not mock_user_is_banned(request.profile, paper, paper.mock_contest)
    )


def mock_publication_error(paper):
    if not paper.series.is_published:
        return "Kỳ thi trong thư viện chưa công bố."
    if not paper.is_published:
        return "Bộ đề chưa công bố."
    if paper.lesson_id:
        if not paper.lesson.is_visible:
            return "Lesson nguồn đang ẩn."
        if paper.lesson.course.is_suspended:
            return "Khóa học nguồn đang tạm ẩn."
    elif not paper.contest.is_visible:
        return "Contest nguồn đang ẩn."
    return None


def source_items(paper):
    if paper.lesson_id:
        rows = [
            (item.order, item.pk, item.problem, None, item.score, item)
            for item in paper.lesson.lesson_problems.all()
        ]
        rows.extend(
            (item.order, item.pk, None, item.quiz, item.points, item)
            for item in paper.lesson.lesson_quizzes.all()
            if item.is_visible
        )
    else:
        rows = [
            (item.order, item.pk, item.problem, item.quiz, item.points, item)
            for item in paper.contest.contest_problems.all()
        ]
    return sorted(rows, key=lambda row: (row[0], row[2] is None, row[1]))


def mock_configuration_error(paper):
    if not (paper.mock_contest_id and paper.mock_minutes):
        return "Chưa bật thi thử."
    contest = paper.mock_contest
    if (
        not contest.ended
        or contest.is_rated
        or contest.run_pretests_only
        or contest.is_strict
        or not contest.is_visible
        or contest.is_private
        or contest.is_organization_private
        or contest.public_scoreboard
        or contest.access_code
        or contest.scoreboard_visibility != Contest.SCOREBOARD_ALWAYS_HIDDEN
    ):
        return "Cấu hình Contest thi thử không hợp lệ."
    rows = sorted(contest.contest_problems.all(), key=lambda row: (row.order, row.pk))
    signature = [(row.problem_id, row.quiz_id, row.points, row.partial) for row in rows]
    source = [
        (
            problem.pk if problem else None,
            quiz.pk if quiz else None,
            points,
            getattr(row, "partial", True),
        )
        for _, _, problem, quiz, points, row in source_items(paper)
    ]
    if not signature or any(row.quiz_id for row in rows) or signature != source:
        return "Danh sách bài, thứ tự hoặc cách tính điểm đã thay đổi. Hãy đồng bộ thi thử."
    return None


def apply_series_mock_defaults(papers):
    """Give papers the mock settings of their exam. Returns how many changed.

    The submission limit applies to attempts started afterwards. Hiding results
    applies to the mock contest at once, so also to attempts in progress.
    """
    changed = 0
    for paper in papers.select_related("series", "mock_contest"):
        defaults = paper.series.mock_defaults()
        if any(getattr(paper, field) != value for field, value in defaults.items()):
            for field, value in defaults.items():
                setattr(paper, field, value)
            paper.save(update_fields=list(defaults))
            changed += 1
    return changed


def mock_configuration_matches(paper):
    return mock_configuration_error(paper) is None


@transaction.atomic
def create_mock_contest(
    paper, owner, *, use_original_duration=False, synchronize=False
):
    paper = (
        ExamPaper.objects.select_for_update()
        .select_related(
            "lesson__course",
            "contest",
            "mock_contest",
        )
        .get(pk=paper.pk)
    )
    paper.clean()
    if not paper.mock_minutes and use_original_duration and paper.duration_minutes:
        paper.mock_minutes = paper.duration_minutes
        paper._meta.get_field("mock_minutes").run_validators(paper.mock_minutes)
    previous = paper.mock_contest
    if previous and mock_configuration_matches(paper):
        if use_original_duration:
            paper.save(update_fields=["mock_minutes"])
        return previous
    if previous and not synchronize:
        raise ValidationError(mock_configuration_error(paper))
    if not paper.mock_minutes:
        raise ValidationError("Hãy nhập thời lượng thi thử trước.")
    items = source_items(paper)
    if not items:
        raise ValidationError("Bộ đề chưa có bài.")
    if any(quiz for _, _, _, quiz, _, _ in items):
        raise ValidationError("Tạo thi thử từ thư viện hiện hỗ trợ bộ đề lập trình.")
    source = paper.contest if paper.contest_id else None
    if source and source.key.startswith("library_mock_"):
        raise ValidationError("Hãy chọn Contest gốc làm nguồn.")
    course_link = (
        CourseContest.objects.filter(contest=source).first()
        if source and source.is_in_course
        else None
    )
    if source and (
        source.access_code
        or (source.is_in_course and not course_link)
        or source.is_private
        or source.is_organization_private
    ):
        raise ValidationError(
            "Bản này chỉ tạo thi thử từ Lesson hoặc Contest không giới hạn theo mã hay nhóm."
        )
    if source and source.is_strict:
        raise ValidationError(
            "Contest nguồn dùng giám sát, cần tạo cấu hình thi thử riêng."
        )
    course = (
        paper.lesson.course
        if paper.lesson_id
        else course_link.course if course_link else None
    )
    if (
        any(
            (not problem.is_public or problem.is_organization_private)
            for _, _, problem, _, _, _ in items
        )
        and not course
    ):
        raise ValidationError("Bộ đề chứa bài riêng tư. Hãy dùng nguồn Course phù hợp.")
    now = timezone.now()
    key = f"library_mock_{paper.pk}"
    if previous:
        key += f"_v{previous.pk}"
    target = Contest(
        key=key,
        name=f"Thi thử · {paper.title}"[:150],
        start_time=now - timedelta(days=2),
        end_time=now - timedelta(days=1),
        time_limit=timedelta(minutes=paper.mock_minutes),
        is_visible=True,
        is_rated=False,
        is_in_course=bool(course),
        is_private=bool(source and source.is_private),
        is_organization_private=bool(source and source.is_organization_private),
        scoreboard_visibility=Contest.SCOREBOARD_ALWAYS_HIDDEN,
        use_clarifications=False,
        format_name="ioi",
    )
    target.save()
    target.authors.add(owner)
    if course:
        order = course.contests.aggregate(last=Max("order"))["last"] or 0
        CourseContest.objects.create(
            course=course,
            contest=target,
            points=0,
            exclude_from_grades=True,
            order=order + 1,
        )
    ContestProblem.objects.bulk_create(
        [
            ContestProblem(
                contest=target,
                problem=problem,
                points=points,
                order=index,
                partial=getattr(row, "partial", True),
                is_result_hidden=paper.mock_delayed,
            )
            for index, (_, _, problem, _, points, row) in enumerate(items, 1)
        ]
    )
    if previous:
        ExamMockArchive.objects.create(paper=paper, contest=previous)
    paper.mock_contest = target
    paper.save(update_fields=["mock_contest", "mock_minutes"])
    return target


@transaction.atomic
def start_mock(request, paper):
    # Serialize entry with publishing a new version of the mock.
    paper = (
        ExamPaper.objects.select_for_update()
        .select_related("lesson__course", "contest", "mock_contest")
        .get(pk=paper.pk)
    )
    profile = Profile.objects.select_for_update().get(pk=request.profile.pk)
    now = timezone.now()
    session = active_mock_sessions(profile, now).first()
    if session and session.paper_id != paper.pk:
        raise ValidationError(_("You are taking a mock exam for another paper."))
    current = profile.current_contest
    if (
        current
        and not current.ended
        and (not session or session.participation_id != current.pk)
    ):
        raise ValidationError(_("You are taking part in another contest."))
    target = session.participation.contest if session else paper.mock_contest
    if not target or not target.is_accessible_by(request.user):
        raise PermissionDenied
    if mock_user_is_banned(profile, paper, target):
        raise PermissionDenied
    if session is None:
        if target.access_code:
            raise ValidationError(
                _("This paper requires an access code. Open the contest to enter it.")
            )
        if not mock_configuration_matches(paper):
            raise ValidationError(
                _("The mock exam is not ready or its problem list has changed.")
            )
        last = ContestParticipation.objects.filter(
            contest=target, user=profile
        ).aggregate(last=Max("virtual"))["last"]
        participation = ContestParticipation.objects.create(
            contest=target,
            user=profile,
            virtual=max(last or 0, 0) + 1,
            real_start=now,
            library_deadline=now + timedelta(minutes=paper.mock_minutes),
        )
        session = ExamMockSession.objects.create(
            participation=participation,
            paper=paper,
            delayed=paper.mock_delayed,
            submission_limit=paper.mock_limit,
        )
    profile.current_contest_id = session.participation_id
    profile.save(update_fields=["current_contest"])
    return session


def mock_session_for(participation):
    if participation is None or participation.library_deadline is None:
        return None
    return getattr(participation, "library_mock_session", None)


def active_mock_sessions(profile, now):
    return ExamMockSession.objects.filter(
        participation__user=profile,
        participation__library_deadline__gt=now,
        participation__is_disqualified=False,
    ).select_related("participation__contest")


@transaction.atomic
def finish_mock(profile_id, contest, participation_id=None):
    profile = Profile.objects.select_for_update().get(pk=profile_id)
    now = timezone.now()
    session = (
        active_mock_sessions(profile, now)
        .filter(
            participation__contest=contest,
            participation_id=participation_id or profile.current_contest_id,
        )
        .first()
    )
    if session is None:
        return
    ContestParticipation.objects.filter(pk=session.participation_id).update(
        library_deadline=now
    )
    # Finishing early hands results back now rather than at the original deadline:
    # submissions held for after the attempt become due at once.
    ExamDeferredSubmission.objects.filter(
        submission__contest__participation_id=session.participation_id,
        dispatched_at__isnull=True,
        ready_at__gt=now,
    ).update(ready_at=now)
    if profile.current_contest_id == session.participation_id:
        profile.current_contest_id = None
        profile.save(update_fields=["current_contest"])
    # Results held back from the profile's points count again.
    from judge.tasks.submission import update_user_points

    transaction.on_commit(lambda: update_user_points.delay(profile_id))


def active_paper_session(profile, paper):
    if not profile:
        return None
    return active_mock_sessions(profile, timezone.now()).filter(paper=paper).first()


def mock_paper_for_contest(contest):
    if contest.key.startswith("library_mock_"):
        papers = ExamPaper.objects.select_related("series", "lesson__course", "contest")
        return (
            papers.filter(mock_contest=contest).first()
            or papers.filter(mock_archives__contest=contest).first()
        )
    return None


def mock_contest_is_listed(contest, user):
    """Apply the same source permissions to current and previous versions."""
    paper = mock_paper_for_contest(contest)
    if paper is None:
        return False
    if user.has_perm("judge.change_exampaper"):
        return True
    if not paper.is_published or not paper.series.is_published:
        return False
    profile = user.profile if user.is_authenticated else None
    if paper.lesson_id:
        course = paper.lesson.course
        if not Course.is_accessible_by(course, profile):
            return False
        if Course.is_editable_by(course, profile):
            return True
        return paper.lesson.is_visible and not get_lesson_lock_status(
            profile, course
        ).get(paper.lesson_id, False)
    source = paper.contest
    return (
        not source.key.startswith("library_mock_")
        and source.is_accessible_by(user)
        and source.can_see_problems(user)
    )


def accessible_paper(request, pk, *, discussion=False):
    paper = (
        ExamPaper.objects.select_related(
            "series", "lesson__course", "contest", "mock_contest"
        )
        .filter(pk=pk)
        .first()
    )
    if not paper:
        return None
    access = LibraryAccess(request)
    if not access.listed(paper) or not access.allows_source(paper):
        return None
    if discussion:
        if not paper.discussion_enabled:
            return None
        if active_paper_session(request.profile, paper):
            return None
    return paper


def practice_mock_paper(request, problem, lesson=None):
    """Resolve the originating paper of a practice link, if the problem belongs to it."""
    paper_id = request.GET.get("exam_paper", "")
    if (
        request.in_contest
        or not paper_id.isascii()
        or not paper_id.isdecimal()
        or len(paper_id) > 10
        or not 0 < int(paper_id) <= 2147483647
    ):
        return None
    paper = accessible_paper(request, int(paper_id))
    if not paper or paper.lesson_id != getattr(lesson, "pk", None):
        return None
    items = lesson.lesson_problems if lesson else paper.contest.contest_problems
    if not items.filter(problem=problem).exists():
        return None
    if mock_entry_available(request, paper) and mock_configuration_matches(paper):
        return paper
    return None
