"""Quiz editor: the batch "add questions by ID" validation endpoint.

The create/edit pages POST the parsed ID list here and then append questions to
the quiz in the order of ``data.valid``. QuizQuestion's default ordering is
``-created_at``, so iterating the queryset used to hand a 24-50 range back as
50..24 (A01). The response order must follow the request order, duplicates
collapse to their first occurrence, and questions the caller may not use are
reported as invalid without leaking their title.
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Profile
from judge.models.quiz import QuizQuestion


class QuizValidateQuestionsOrderTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        lang = Language.objects.first()
        cls.user = User.objects.create_user("teacher", "t@x.com", "pw")
        cls.profile, _ = Profile.objects.get_or_create(
            user=cls.user, defaults={"language": lang}
        )
        other = User.objects.create_user("other", "o@x.com", "pw")
        cls.other_profile, _ = Profile.objects.get_or_create(
            user=other, defaults={"language": lang}
        )
        # Created in ascending ID order, so -created_at ordering is descending ID.
        cls.questions = [
            QuizQuestion.objects.create(
                question_type="SA",
                title=f"Q{i}",
                content=f"Question {i}",
                correct_answers=["x"],
                is_public=True,
            )
            for i in range(1, 7)
        ]
        cls.private = QuizQuestion.objects.create(
            question_type="SA",
            title="Secret title",
            content="private",
            correct_answers=["x"],
            is_public=False,
        )
        cls.private.authors.add(cls.other_profile)

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, ids):
        return self.client.post(
            reverse("quiz_validate_questions"),
            data=json.dumps({"ids": ids}),
            content_type="application/json",
        )

    def _ids(self, questions):
        return [q.id for q in questions]

    def test_ascending_range_keeps_input_order(self):
        ids = self._ids(self.questions)
        resp = self._post(ids)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([q["id"] for q in data["valid"]], ids)
        self.assertEqual(data["invalid_ids"], [])

    def test_arbitrary_order_is_not_resorted(self):
        q = self.questions
        ids = [q[5].id, q[1].id, q[3].id]  # "7,2,9"-style list
        data = self._post(ids).json()
        self.assertEqual([x["id"] for x in data["valid"]], ids)
        self.assertEqual([x["title"] for x in data["valid"]], ["Q6", "Q2", "Q4"])

    def test_duplicates_collapse_to_first_occurrence(self):
        q = self.questions
        ids = [q[2].id, q[0].id, q[2].id, "%d" % q[0].id]
        data = self._post(ids).json()
        self.assertEqual([x["id"] for x in data["valid"]], [q[2].id, q[0].id])
        self.assertEqual(data["invalid_ids"], [])

    def test_missing_and_forbidden_ids_are_invalid_in_input_order(self):
        q = self.questions
        missing = max(self._ids(self.questions)) + 1000
        ids = [self.private.id, q[0].id, missing, q[1].id]
        data = self._post(ids).json()
        self.assertEqual([x["id"] for x in data["valid"]], [q[0].id, q[1].id])
        self.assertEqual(data["invalid_ids"], [self.private.id, missing])
        self.assertNotIn("Secret title", data["valid"].__repr__())

    def test_superuser_sees_private_question_in_place(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        q = self.questions
        ids = [q[0].id, self.private.id, q[1].id]
        data = self._post(ids).json()
        self.assertEqual([x["id"] for x in data["valid"]], ids)

    def test_non_integer_tokens_are_dropped(self):
        q = self.questions
        data = self._post(["abc", q[1].id, None, q[0].id]).json()
        self.assertEqual([x["id"] for x in data["valid"]], [q[1].id, q[0].id])

    def test_all_garbage_is_a_400(self):
        self.assertEqual(self._post(["abc", None]).status_code, 400)
