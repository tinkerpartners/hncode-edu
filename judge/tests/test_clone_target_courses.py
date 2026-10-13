"""A13 (#100): picking the target course when cloning a lesson or a contest.

- Courses the user teaches or assists come first, for regular users and
  superusers alike; the rest (superusers only) follow; stable by name/id.
- The picker is paged instead of cut at 20, so every valid target is
  reachable, and search matches name or slug.
- Valid targets are exactly what Course.is_editable_by accepts; a forged
  slug/id is refused by the form on submit.

Clone Problem has no target course (it only takes a new code), so the two
clone flows with a course target are Clone Lesson and Clone Contest.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from judge.forms import ContestCloneForm, LessonCloneForm
from judge.models import Course, CourseRole, Language, Profile


def make_profile(username, **kw):
    user = User.objects.create_user(username, f"{username}@x.com", "pw", **kw)
    profile, _ = Profile.objects.get_or_create(
        user=user, defaults={"language": Language.objects.first()}
    )
    return profile


class CloneTargetCoursesTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.teacher = make_profile("a13_teacher")
        cls.superuser = make_profile("a13_root", is_staff=True, is_superuser=True)

        def course(name, slug):
            return Course.objects.create(
                name=name, slug=slug, about="", is_public=False, is_open=False
            )

        # 25 courses taught by the teacher: more than one page of 20.
        cls.taught = [course(f"Taught {i:02d}", f"taught-{i:02d}") for i in range(25)]
        for c in cls.taught[:20]:
            CourseRole.objects.create(course=c, user=cls.teacher, role="TE")
        for c in cls.taught[20:]:
            CourseRole.objects.create(course=c, user=cls.teacher, role="AS")
        # Courses where the teacher is only a student, or has no role.
        cls.as_student = course("Aaa As Student", "as-student")
        CourseRole.objects.create(course=cls.as_student, user=cls.teacher, role="ST")
        cls.foreign = [course(f"Aaa Foreign {i}", f"foreign-{i}") for i in range(3)]
        # The superuser teaches two courses whose names sort last.
        cls.root_courses = [
            course("Zzz Root A", "zzz-root-a"),
            course("Zzz Root B", "zzz-root-b"),
        ]
        for c in cls.root_courses:
            CourseRole.objects.create(course=c, user=cls.superuser, role="TE")

    def pick(self, profile, **params):
        self.client.force_login(profile.user)
        return self.client.get(reverse("course_select2"), params).json()

    def all_pages(self, profile, **params):
        slugs, page = [], 1
        while True:
            data = self.pick(profile, page=page, **params)
            slugs += [r["id"] for r in data["results"]]
            if not data["more"]:
                return slugs, page
            page += 1

    # -- ordering ---------------------------------------------------------------

    def test_regular_user_sees_only_courses_they_teach_or_assist(self):
        slugs, pages = self.all_pages(self.teacher)
        self.assertEqual(slugs, [c.slug for c in self.taught])  # sorted by name
        self.assertEqual(pages, 2)
        self.assertNotIn("as-student", slugs)
        self.assertFalse(any(s.startswith("foreign") for s in slugs))

    def test_superuser_gets_own_courses_first_then_all_others(self):
        slugs, _ = self.all_pages(self.superuser)
        self.assertEqual(
            slugs[:2], ["zzz-root-a", "zzz-root-b"]
        )  # despite sorting last
        self.assertEqual(len(slugs), Course.objects.count())
        rest = slugs[2:]
        names = list(
            Course.objects.filter(slug__in=rest)
            .order_by("name", "id")
            .values_list("slug", flat=True)
        )
        self.assertEqual(rest, names)

    def test_order_is_stable_between_requests(self):
        self.assertEqual(
            self.pick(self.superuser, page=2), self.pick(self.superuser, page=2)
        )

    # -- paging and search ------------------------------------------------------

    def test_first_page_is_20_with_more(self):
        data = self.pick(self.teacher)
        self.assertEqual(len(data["results"]), 20)
        self.assertTrue(data["more"])
        data = self.pick(self.teacher, page=2)
        self.assertEqual(len(data["results"]), 5)
        self.assertFalse(data["more"])

    def test_search_reaches_a_course_beyond_the_first_20(self):
        data = self.pick(self.teacher, term="Taught 24")
        self.assertEqual([r["id"] for r in data["results"]], ["taught-24"])
        data = self.pick(self.teacher, term="taught-23")  # by slug
        self.assertEqual([r["id"] for r in data["results"]], ["taught-23"])

    def test_out_of_range_page_is_empty_not_an_error(self):
        data = self.pick(self.teacher, page=99)
        self.assertEqual(data, {"results": [], "more": False})

    def test_anonymous_gets_nothing(self):
        data = self.client.get(reverse("course_select2")).json()
        self.assertEqual(data["results"], [])

    # -- backend still refuses invalid targets --------------------------------

    def test_lesson_clone_refuses_a_course_the_user_cannot_edit(self):
        for slug in ("as-student", "foreign-0"):
            with self.subTest(slug=slug):
                form = LessonCloneForm(
                    {"title": "x", "course": slug}, profile=self.teacher
                )
                self.assertFalse(form.is_valid())
                self.assertIn("course", form.errors)
        form = LessonCloneForm(
            {"title": "x", "course": "taught-24"}, profile=self.teacher
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_contest_clone_choices_and_refusal(self):
        choices = tuple(
            Course.get_clone_target_courses(self.teacher).values_list("id", "name")
        )
        self.assertEqual([n for _, n in choices], [c.name for c in self.taught])
        form = ContestCloneForm(
            {
                "key": "a13newcontest",
                "target_type": "course",
                "course": str(self.foreign[0].id),
            },
            course_choices=choices + ((self.foreign[0].id, "forged"),),
            profile=self.teacher,
        )
        self.assertFalse(form.is_valid())

    def test_contest_clone_view_uses_the_same_order(self):
        from judge.views.contests import ContestClone
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.superuser.user
        request.profile = self.superuser
        view = ContestClone()
        view.request = request
        view.kwargs = {}
        view.object = None
        kwargs = ContestClone.get_form_kwargs(view)
        ids = [i for i, _ in kwargs["course_choices"]]
        self.assertEqual(ids[:2], [c.id for c in self.root_courses])
        self.assertEqual(len(ids), Course.objects.count())
