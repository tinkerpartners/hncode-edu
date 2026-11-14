"""A cheap "has anything on this grades table changed?" token (A16, #104).

The grades pages refresh themselves every 30 s. Rendering one costs up to
~0.3 s for a lesson and ~0.8 s for a big course, so a poll must not render:
it sends back the token it has, and the view answers 204 when the token is
unchanged. Only a real change pays for a render.

The token covers everything the tables are computed from:
  * the course's students;
  * BestSubmission rows of those students on the lessons' problems (new
    best, rejudge, points change) and BestQuizAttempt rows on the lessons'
    quizzes;
  * live participations of those students in the course's contests;
  * the configuration: lessons, their problems and quizzes, and the course
    contests, with their points.
Hash row identities and values, not just COUNT/SUM/MAX of scores: balanced
changes must invalidate the table. Large result sets are reduced in the DB,
so polling never transfers every student's best submission into Python.
"""

import hashlib
import logging
import time
from uuid import uuid4

from django.core.cache import cache, caches
from django.db import connections
from django.db.models import (
    Count,
    DecimalField,
    Exists,
    F,
    Func,
    OuterRef,
    Sum,
    TextField,
    Value,
)
from django.db.models.functions import Cast, JSONArray, MD5, Substr

from judge.models import (
    BestSubmission,
    ContestParticipation,
    ContestProblem,
    ContestSubmission,
    Profile,
    Submission,
)
from judge.models.course import (
    CourseLessonProblem,
    RoleInCourse,
    get_course_role_profile_ids,
)
from judge.models.quiz import BestQuizAttempt, CourseLessonQuiz, QuizQuestionAssignment


def _digest(parts):
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:32]


def _rows(queryset, *fields):
    return tuple(queryset.order_by("pk").values_list(*fields))


def _fingerprint(queryset, *fields):
    """Return a small multiset checksum including each row's PK and values.

    MariaDB/MySQL (the deployed DB) returns one aggregate row. Two independent
    64-bit halves of the per-row digest detect score swaps/balanced rejudges;
    JSON_ARRAY preserves nulls, types and boundaries. This is a change token,
    not an authentication checksum. Keep a streaming, bounded-memory fallback
    for other database engines used by development/test environments.
    """
    if connections[queryset.db].vendor == "mysql":
        digest = MD5(Cast(JSONArray(*(F(field) for field in fields)), TextField()))
        decimal = DecimalField(max_digits=38, decimal_places=0)
        aggregates = {"count": Count("pk")}
        for index, start in enumerate((1, 17)):
            value = Func(
                Substr(digest, start, 16),
                Value(16),
                Value(10),
                function="CONV",
                output_field=TextField(),
            )
            aggregates[f"sum{index}"] = Sum(Cast(value, decimal), default=0)
        return queryset.order_by().aggregate(**aggregates)
    digest = hashlib.sha256()
    count = 0
    for row in queryset.order_by("pk").values_list(*fields).iterator(chunk_size=2000):
        digest.update(repr(row).encode())
        digest.update(b"\n")
        count += 1
    return {"count": count, "digest": digest.hexdigest()}


def grades_version(course, lessons, course_contests=()):
    lessons = list(lessons)
    course_contests = list(course_contests)
    lesson_ids = [lesson.id for lesson in lessons]
    contest_ids = [cc.contest_id for cc in course_contests]
    student_ids = sorted(get_course_role_profile_ids(course.id, RoleInCourse.STUDENT))

    lesson_problems = _rows(
        CourseLessonProblem.objects.filter(lesson_id__in=lesson_ids),
        "id",
        "lesson_id",
        "problem_id",
        "score",
        "order",
        "problem__code",
        "problem__name",
    )
    lesson_quizzes = _rows(
        CourseLessonQuiz.objects.filter(lesson_id__in=lesson_ids),
        "id",
        "lesson_id",
        "quiz_id",
        "points",
        "order",
        "is_visible",
        "is_result_hidden",
        "quiz__code",
        "quiz__title",
    )

    parts = [
        _digest(student_ids),
        _rows(
            Profile.objects.filter(pk__in=student_ids),
            "id",
            "user__username",
            "user__first_name",
        ),
        tuple(
            (lesson.id, lesson.points, lesson.title, lesson.order, lesson.is_visible)
            for lesson in lessons
        ),
        tuple(
            (cc.id, cc.contest_id, cc.points, cc.order, cc.contest.name)
            for cc in course_contests
        ),
        lesson_problems,
        lesson_quizzes,
        _rows(
            QuizQuestionAssignment.objects.filter(
                quiz_id__in={q[2] for q in lesson_quizzes}
            ),
            "id",
            "quiz_id",
            "points",
        ),
    ]
    if contest_ids:
        parts.append(
            _rows(
                ContestProblem.objects.filter(contest_id__in=contest_ids),
                "id",
                "contest_id",
                "points",
                "is_result_hidden",
            )
        )
    if student_ids:
        problem_ids = sorted({p[2] for p in lesson_problems})
        best_queryset = BestSubmission.objects.filter(
            user_id__in=student_ids, problem_id__in=problem_ids
        )
        best = _fingerprint(
            best_queryset,
            "id",
            "user_id",
            "problem_id",
            "submission_id",
            "points",
            "case_total",
        )
        parts.append(best)
        # Hidden cached bests use visible submissions as fallback. Their
        # rejudge can change grades without changing BestSubmission itself.
        # Most bests come from practice: do not join every best to contest
        # submissions just to hash a hidden flag. Track hidden membership.
        hidden_best = best_queryset.filter(
            submission_id__in=ContestSubmission.objects.filter(
                is_result_hidden=True
            ).values("submission_id")
        )
        if hidden_best.exists():
            parts.append(_fingerprint(hidden_best, "id"))
            matching_hidden_best = hidden_best.filter(
                user_id=OuterRef("user_id"), problem_id=OuterRef("problem_id")
            )
            parts.append(
                _fingerprint(
                    Submission.objects.filter(
                        user_id__in=student_ids, problem_id__in=problem_ids
                    ).filter(Exists(matching_hidden_best)),
                    "id",
                    "user_id",
                    "problem_id",
                    "status",
                    "case_points",
                    "case_total",
                    "points",
                    "date",
                    "contest__is_result_hidden",
                )
            )
        parts.append(
            _fingerprint(
                BestQuizAttempt.objects.filter(
                    user_id__in=student_ids, lesson_quiz__lesson_id__in=lesson_ids
                ),
                "id",
                "user_id",
                "lesson_quiz_id",
                "attempt_id",
                "score",
                "max_score",
            )
        )
        if contest_ids:
            parts.append(
                _fingerprint(
                    ContestParticipation.objects.filter(
                        contest_id__in=contest_ids,
                        user_id__in=student_ids,
                        virtual=ContestParticipation.LIVE,
                    ),
                    "id",
                    "contest_id",
                    "user_id",
                    "score",
                    "cumtime",
                )
            )
    return _digest(tuple(parts))


VERSION_TTL = 10  # seconds
logger = logging.getLogger(__name__)


def _version_backend():
    # Cross-worker coordination must bypass the default request-local L0.
    return caches["primary"] if "primary" in caches else cache


def cached_grades_version(course, lessons, course_contests=()):
    lessons = list(lessons)
    course_contests = list(course_contests)
    key = "grades_version:v3:%d:%s" % (
        course.id,
        _digest(
            (
                tuple(lesson.id for lesson in lessons),
                tuple(cc.id for cc in course_contests),
            )
        ),
    )
    backend = _version_backend()
    try:
        version = backend.get(key)
        if version is not None:
            return version
        lease = uuid4().hex
        if not backend.add(key + ":lease", lease, VERSION_TTL):
            version = backend.get(key) or backend.get(key + ":stale")
            if version is not None:
                return version
            # A first page still renders current grades; this temporary token
            # guarantees a subsequent poll refreshes after the owner finishes.
            # Time-bounding also works when a backend silently drops writes.
            return "pending:%s:%d" % (
                backend.get(key + ":lease"),
                time.time() // VERSION_TTL,
            )
    except Exception as exc:
        # Keep the page usable during a cache outage without scanning all
        # grade rows for every viewer. A rotating token forces periodic render.
        logger.warning("Grades version cache unavailable (%s)", type(exc).__name__)
        return "uncached:%d" % (time.time() // VERSION_TTL)
    version = grades_version(course, lessons, course_contests)
    try:
        backend.set(key, version, VERSION_TTL)
        backend.set(key + ":stale", version, VERSION_TTL * 3)
    except Exception:
        logger.warning("Could not cache grades version")
    # Let the short lease expire naturally: this cannot delete another
    # worker's lease after a slow computation. Fresh token TTL starts later
    # than the lease, so normal refresh is not delayed by the lease.
    return version
