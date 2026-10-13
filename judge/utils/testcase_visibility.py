"""Who may see a submission's test data (A04, #96).

"Test data" means the test input, the expected output and the checker's
feedback on each case. Verdicts, time, memory, points and the submitter's own
program output are not test data and are not governed here.

Owner decisions (2026-10-13):
  * The switch lives on CourseLesson.hide_testcases.
  * It applies to the PROBLEM everywhere: if any lesson containing the problem
    hides test data, every submission of that problem hides it -- submitted
    from the lesson, from the problem page or from a contest. Submissions do
    not record a lesson, and a per-source rule would leave the problem page
    as an open side door.
  * Exempt: superusers, editors of the problem, editors of the submission's
    contest, and the Teacher/Assistant of a course that has one of the hiding
    lessons. A Teacher/Assistant of an unrelated course is not exempt.

Every view that returns test data must ask testcases_hidden_for() first.
"""

from judge.models.course import EDITABLE_ROLES, CourseLesson, CourseRole


def hiding_lessons(problem):
    """Lessons that contain `problem` and hide test data."""
    return CourseLesson.objects.filter(
        hide_testcases=True, lesson_problems__problem=problem
    )


def is_testcase_manager(user, problem, contest=None):
    """May `user` see test data that a lesson hides for `problem`?"""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if problem.is_editable_by(user):
        return True
    if contest is not None and contest.is_editable_by(user):
        return True
    return CourseRole.objects.filter(
        user=user.profile,
        role__in=EDITABLE_ROLES,
        course__lessons__in=hiding_lessons(problem),
    ).exists()


def testcases_hidden_for(user, problem, contest=None):
    """True when a lesson policy hides `problem`'s test data from `user`."""
    if not hiding_lessons(problem).exists():
        return False
    return not is_testcase_manager(user, problem, contest)
