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
Hash ordered rows rather than COUNT/SUM/MAX: balanced score or weight changes
must invalidate a table even when every aggregate remains unchanged. Read only
the required columns in bulk, without rendering or per-student queries. Pollers
share one computation per VERSION_TTL, adding at most that much cache delay.
"""

import hashlib

from django.core.cache import cache

from judge.models import (
    BestSubmission,
    ContestParticipation,
    ContestProblem,
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
        best = _rows(
            BestSubmission.objects.filter(
                user_id__in=student_ids,
                problem_id__in=sorted({p[2] for p in lesson_problems}),
            ),
            "id",
            "user_id",
            "problem_id",
            "submission_id",
            "points",
            "case_total",
            "submission__contest__is_result_hidden",
        )
        parts.append(best)
        # Hidden cached bests use visible submissions as fallback. Their
        # rejudge can change grades without changing BestSubmission itself.
        hidden_users = {b[1] for b in best if b[6]}
        hidden_problems = {b[2] for b in best if b[6]}
        if hidden_users:
            parts.append(
                _rows(
                    Submission.objects.filter(
                        user_id__in=hidden_users, problem_id__in=hidden_problems
                    ),
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
            _rows(
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
                _rows(
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


def cached_grades_version(course, lessons, course_contests=()):
    lessons = list(lessons)
    course_contests = list(course_contests)
    key = "grades_version:v2:%d:%s" % (
        course.id,
        _digest(
            (
                tuple(lesson.id for lesson in lessons),
                tuple(cc.id for cc in course_contests),
            )
        ),
    )
    version = cache.get(key)
    if version is None:
        version = grades_version(course, lessons, course_contests)
        cache.set(key, version, VERSION_TTL)
    return version
