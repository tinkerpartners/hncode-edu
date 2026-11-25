from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Problem, Profile, Submission
from judge.tasks.submission import rescore_problem
from judge.tests.strict_helpers import make_group, make_language


class ProblemPointsKeptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.language = make_language()
        cls.group = make_group()
        cls.admin, cls.admin_profile = cls.make_user("points_admin", is_superuser=True)
        cls.author, cls.author_profile = cls.make_user("points_author")
        cls.author.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("add_problem", "edit_own_problem", "clone_problem")
            )
        )

    @classmethod
    def make_user(cls, name, **fields):
        user = User.objects.create_user(name, password="pass", is_staff=True, **fields)
        profile, _ = Profile.objects.get_or_create(
            user=user, defaults={"language": cls.language}
        )
        return user, profile

    def make_problem(self, code="pointskept", points=100, **fields):
        problem = Problem(
            code=code,
            name=code,
            group=self.group,
            description="Statement",
            time_limit=1,
            memory_limit=65536,
            points=points,
            **fields,
        )
        problem._bypass_points_cap = True
        problem.save()
        problem.authors.add(self.author_profile)
        problem.allowed_languages.set(Language.objects.all())
        return Problem.objects.get(pk=problem.pk)

    def stored(self, problem):
        return Problem.objects.values_list("points", flat=True).get(pk=problem.pk)

    def edit(self, problem, **changes):
        url = reverse("problem_edit", args=[problem.code])
        data = {}
        for field in self.client.get(url).context["form"]:
            value = field.value()
            if value is None or value is False or value == "":
                continue
            if field.name == "date":
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            data[field.html_name] = value
        data.update(changes)
        return self.client.post(url, data)

    def test_saving_private_problem_keeps_stored_points(self):
        private = self.make_problem("keepprivate", is_public=False)
        group_only = self.make_problem(
            "keepgroup", is_public=True, is_organization_private=True
        )
        for problem in (private, group_only):
            with self.subTest(problem=problem.code):
                problem.name = "Renamed"
                problem.save()
                self.assertEqual(self.stored(problem), 100)

    def test_private_problem_points_cannot_be_raised(self):
        for index, (start, attempt, expected) in enumerate(
            ((1, 100, 1), (100, 500, 100), (0.5, 3, 1))
        ):
            with self.subTest(start=start, attempt=attempt):
                problem = self.make_problem(f"raise{index}", points=start)
                problem.points = attempt
                problem.save()
                self.assertEqual(self.stored(problem), expected)

    def test_points_can_be_lowered(self):
        problem = self.make_problem()
        problem.points = 40
        problem.save()
        self.assertEqual(self.stored(problem), 40)

    def test_new_private_problem_starts_at_one(self):
        problem = Problem.objects.create(
            code="pointsnew",
            name="x",
            group=self.group,
            time_limit=1,
            memory_limit=65536,
            points=100,
        )
        self.assertEqual(self.stored(problem), 1)

    def test_author_edit_keeps_points_and_cannot_set_them(self):
        problem = self.make_problem()
        self.client.force_login(self.author)
        response = self.edit(problem, name="Edited by author", points=500)
        self.assertEqual(response.status_code, 302)
        problem.refresh_from_db()
        self.assertEqual((problem.name, problem.points), ("Edited by author", 100))

    def test_superuser_edit_sets_points_and_rescores(self):
        problem = self.make_problem()
        self.client.force_login(self.admin)
        with patch("judge.views.problem.rescore_problem") as rescore:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.edit(problem, points=250)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored(problem), 250)
        rescore.s.assert_called_once_with(problem.pk)
        self.client.force_login(self.author)
        self.assertEqual(self.edit(problem, name="Later edit").status_code, 302)
        problem.refresh_from_db()
        self.assertEqual((problem.name, problem.points), ("Later edit", 250))

    def test_superuser_marks_private_without_losing_points(self):
        problem = self.make_problem(is_public=True)
        self.client.force_login(self.admin)
        with patch("judge.views.internal.rescore_problem") as rescore:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("internal_mark_problem_private"), {"id": problem.pk}
                )
        self.assertEqual(response.status_code, 200)
        problem.refresh_from_db()
        self.assertEqual((problem.is_public, problem.points), (False, 100))
        rescore.delay.assert_called_once_with(problem.pk)

    def add(self, code):
        response = self.client.post(
            reverse("problem_add"),
            {
                "code": code,
                "name": code,
                "description": "Statement",
                "points": 100,
                "time_limit": 1,
                "memory_limit": 64,
                "memory_unit": "MB",
                "group": self.group.pk,
                "authors": [self.author_profile.pk],
                "allowed_languages": [self.language.pk],
                "date": "2024-01-01 00:00:00",
            },
        )
        if response.status_code != 302:
            self.fail(response.context["form"].errors)
        return Problem.objects.get(code=code).points

    def test_only_superuser_sets_points_of_a_new_problem(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.add("pointsadmin"), 100)
        self.client.force_login(self.author)
        self.assertEqual(self.add("pointsauthor"), 1)

    def test_clone_by_author_starts_at_one(self):
        problem = self.make_problem()
        self.client.force_login(self.author)
        response = self.client.post(
            reverse("problem_clone", args=[problem.code]), {"code": "pointsclone"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Problem.objects.get(code="pointsclone").points, 1)
        self.assertEqual(self.stored(problem), 100)

    def test_rescore_updates_submissions_users_and_problem_stats(self):
        problem = self.make_problem(is_public=True)
        submission = Submission.objects.create(
            user=self.author_profile,
            problem=problem,
            language=self.language,
            status="D",
            result="AC",
            points=100,
            case_points=10,
            case_total=10,
        )
        Problem.objects.filter(pk=problem.pk).update(points=250)
        rescore_problem.apply(args=[problem.pk], throw=True)
        submission.refresh_from_db()
        problem.refresh_from_db()
        self.author_profile.refresh_from_db()
        self.assertEqual(submission.points, 250)
        self.assertEqual(self.author_profile.points, 250)
        self.assertEqual((problem.points, problem.user_count), (250, 1))
        self.assertEqual(problem.ac_rate, 100)
