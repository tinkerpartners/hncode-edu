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
All of it is aggregated in SQL -- a handful of indexed COUNT/SUM/MAX queries,
no per-student work. On a 61-lesson, 85-student course that is still ~0.2 s
(it reads ~12k BestSubmission rows), so the polling view uses
cached_grades_version(): a whole class polling the same table costs one
computation per VERSION_TTL, at the price of up to VERSION_TTL extra delay.
"""

import hashlib

from django.core.cache import cache
from django.db.models import Count, Max, Sum

from judge.models import BestSubmission, ContestParticipation
from judge.models.course import (
    CourseLessonProblem,
    RoleInCourse,
    get_course_role_profile_ids,
)
from judge.models.quiz import BestQuizAttempt, CourseLessonQuiz


def _digest(parts):
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:20]


def grades_version(course, lessons, course_contests=()):
    lessons = list(lessons)
    course_contests = list(course_contests)
    lesson_ids = [lesson.id for lesson in lessons]
    contest_ids = [cc.contest_id for cc in course_contests]
    student_ids = sorted(get_course_role_profile_ids(course.id, RoleInCourse.STUDENT))

    lesson_problems = CourseLessonProblem.objects.filter(lesson_id__in=lesson_ids)
    lesson_quizzes = CourseLessonQuiz.objects.filter(lesson_id__in=lesson_ids)

    parts = [
        _digest(student_ids),
        tuple((lesson.id, lesson.points) for lesson in lessons),
        tuple((cc.id, cc.contest_id, cc.points) for cc in course_contests),
        tuple(
            lesson_problems.aggregate(
                n=Count("id"), s=Sum("score"), m=Max("id")
            ).values()
        ),
        tuple(
            lesson_quizzes.aggregate(
                n=Count("id"), s=Sum("points"), m=Max("id")
            ).values()
        ),
    ]
    if student_ids:
        parts.append(
            tuple(
                BestSubmission.objects.filter(
                    user_id__in=student_ids,
                    # A literal id list: as a subquery MariaDB took ~0.2 s on
                    # a 61-lesson course, as a list ~5 ms.
                    problem_id__in=sorted(
                        set(lesson_problems.values_list("problem_id", flat=True))
                    ),
                )
                .aggregate(
                    n=Count("id"),
                    m=Max("submission_id"),
                    p=Sum("points"),
                    t=Sum("case_total"),
                )
                .values()
            )
        )
        parts.append(
            tuple(
                BestQuizAttempt.objects.filter(
                    user_id__in=student_ids, lesson_quiz__lesson_id__in=lesson_ids
                )
                .aggregate(n=Count("id"), m=Max("attempt_id"), s=Sum("score"))
                .values()
            )
        )
        if contest_ids:
            parts.append(
                tuple(
                    ContestParticipation.objects.filter(
                        contest_id__in=contest_ids,
                        user_id__in=student_ids,
                        virtual=ContestParticipation.LIVE,
                    )
                    .aggregate(
                        n=Count("id"), s=Sum("score"), c=Sum("cumtime"), m=Max("id")
                    )
                    .values()
                )
            )
    return _digest(tuple(parts))


VERSION_TTL = 10  # seconds


def cached_grades_version(course, lessons, course_contests=()):
    lessons = list(lessons)
    course_contests = list(course_contests)
    key = "grades_version:%d:%s" % (
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
