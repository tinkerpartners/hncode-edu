"""
Fill in the Blanks (FB) question type.

An FB question is a list of independent blanks. Within a blank, "answers" keeps
SA's logical-OR meaning (equivalent forms of that one blank); the blanks
themselves are ANDed and scored one at a time.
"""

import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from ai_features.quiz_import_service import (
    normalize_quiz_question_payload,
    parse_quiz_import_response,
)
from judge.models import Language, Profile
from judge.models.quiz import (
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    QuizQuestionAssignment,
    get_fill_blanks,
    parse_blank_values,
)
from judge.utils.quiz_grading import (
    auto_grade_quiz_attempt,
    count_correct_blanks,
    grade_answer,
    grade_fill_blank,
)


# The question that motivated the type: two blanks, two different answers.
ROBOT_CONTENT = (
    "Một rô-bốt liệt kê lần lượt các số thoả mãn rằng tổng các chữ số của nó "
    "chia hết cho $5$.\n\n"
    "- Theo quy tắc đó, số thứ mười mà rô-bốt liệt kê là số ______.\n"
    "- Trong các số mà rô-bốt liệt kê, có ______ số nhỏ hơn $100$."
)
ROBOT_ANSWERS = {
    "type": "exact",
    "case_sensitive": False,
    "blanks": [
        {"label": "Số thứ mười", "answers": ["50"]},
        {"label": "Số nhỏ hơn 100", "answers": ["19"]},
    ],
}


class FillBlankHelperTestCase(TestCase):
    """get_fill_blanks / parse_blank_values must never raise on stored data."""

    def test_get_fill_blanks_normalizes(self):
        blanks = get_fill_blanks(ROBOT_ANSWERS)
        self.assertEqual(len(blanks), 2)
        self.assertEqual(blanks[0]["label"], "Số thứ mười")
        self.assertEqual(blanks[1]["answers"], ["19"])

    def test_get_fill_blanks_drops_unusable_entries(self):
        blanks = get_fill_blanks(
            {
                "blanks": [
                    {"label": "ok", "answers": ["1"]},
                    {"label": "no answers", "answers": []},
                    {"label": "blank strings", "answers": ["", "  "]},
                    "not a dict",
                    {"label": "wrong type", "answers": 5},
                ]
            }
        )
        self.assertEqual([b["label"] for b in blanks], ["ok"])

    def test_get_fill_blanks_accepts_a_bare_string_answer(self):
        blanks = get_fill_blanks({"blanks": [{"answers": "50"}]})
        self.assertEqual(blanks, [{"label": "", "answers": ["50"]}])

    def test_get_fill_blanks_on_garbage(self):
        for value in (None, [], "text", {"answers": ["50"]}, {"blanks": "nope"}):
            self.assertEqual(get_fill_blanks(value), [])

    def test_get_fill_blanks_caps_at_the_limit(self):
        raw = {"blanks": [{"answers": [str(i)]} for i in range(50)]}
        self.assertEqual(len(get_fill_blanks(raw)), 20)

    def test_parse_blank_values_pads_and_truncates(self):
        self.assertEqual(parse_blank_values('["50", "19"]', 2), ["50", "19"])
        # Question gained a blank after the attempt was saved.
        self.assertEqual(parse_blank_values('["50"]', 2), ["50", ""])
        # Question lost a blank.
        self.assertEqual(parse_blank_values('["50", "19", "7"]', 2), ["50", "19"])

    def test_parse_blank_values_survives_non_json(self):
        # Legacy SA-shaped text, truncated JSON, JSON that is not a list.
        for raw in ("50, 19", '["50"', '{"a": 1}', "", None):
            self.assertEqual(parse_blank_values(raw, 2), ["", ""])

    def test_parse_blank_values_coerces_non_strings(self):
        self.assertEqual(parse_blank_values("[50, null]", 2), ["50", ""])


class FillBlankGradingTestCase(TestCase):
    """Per-blank scoring."""

    fixtures = ["language_small"]

    def setUp(self):
        self.user = User.objects.create_user(
            username="fbuser", email="fb@test.com", password="testpass"
        )
        self.profile, _ = Profile.objects.get_or_create(
            user=self.user,
            defaults={"language": Language.objects.first()},
        )

        self.question = QuizQuestion.objects.create(
            question_type="FB",
            title="Số có tổng chữ số chia hết cho 5",
            content=ROBOT_CONTENT,
            correct_answers=ROBOT_ANSWERS,
            grading_strategy="correct_only",
        )
        self.quiz = Quiz.objects.create(code="fbtest", title="FB Test Quiz")
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=self.question, points=2, order=1
        )
        self.attempt = QuizAttempt.objects.create(
            user=self.profile, quiz=self.quiz, attempt_number=1
        )

    def answer_with(self, values, question=None):
        return QuizAnswer.objects.create(
            attempt=self.attempt,
            question=question or self.question,
            answer=json.dumps(values) if isinstance(values, list) else values,
        )

    def test_all_blanks_correct(self):
        points, is_correct, needs_manual = grade_fill_blank(
            self.answer_with(["50", "19"])
        )
        self.assertEqual(points, 2)
        self.assertTrue(is_correct)
        self.assertFalse(needs_manual)

    def test_one_of_two_blanks_correct_earns_half(self):
        points, is_correct, _ = grade_fill_blank(self.answer_with(["50", "18"]))
        self.assertEqual(points, 1)
        self.assertFalse(is_correct)

    def test_blanks_are_not_interchangeable(self):
        """The SA bug this type exists to fix: order matters, each blank is its own."""
        points, is_correct, _ = grade_fill_blank(self.answer_with(["19", "50"]))
        self.assertEqual(points, 0)
        self.assertFalse(is_correct)

    def test_answering_only_one_blank_does_not_score_full(self):
        points, is_correct, _ = grade_fill_blank(self.answer_with(["50", ""]))
        self.assertEqual(points, 1)
        self.assertFalse(is_correct)

    def test_no_blanks_answered(self):
        points, is_correct, _ = grade_fill_blank(self.answer_with(["", ""]))
        self.assertEqual(points, 0)
        self.assertFalse(is_correct)

    def test_whitespace_and_case_are_forgiven(self):
        question = QuizQuestion.objects.create(
            question_type="FB",
            title="Words",
            content="___ and ___",
            correct_answers={
                "type": "exact",
                "case_sensitive": False,
                "blanks": [
                    {"label": "First", "answers": ["Paris"]},
                    {"label": "Second", "answers": ["5", "five"]},
                ],
            },
            grading_strategy="correct_only",
        )
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=question, points=2, order=2
        )
        points, is_correct, _ = grade_fill_blank(
            self.answer_with(["  paris ", "FIVE"], question=question)
        )
        self.assertEqual(points, 2)
        self.assertTrue(is_correct)

    def test_case_sensitive_blank(self):
        question = QuizQuestion.objects.create(
            question_type="FB",
            title="Case",
            content="___",
            correct_answers={
                "type": "exact",
                "case_sensitive": True,
                "blanks": [{"label": "City", "answers": ["Paris"]}],
            },
            grading_strategy="correct_only",
        )
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=question, points=1, order=3
        )
        _, is_correct, _ = grade_fill_blank(
            self.answer_with(["paris"], question=question)
        )
        self.assertFalse(is_correct)

    def test_all_or_nothing_strategy(self):
        self.question.grading_strategy = "all_or_nothing"
        self.question.save()

        answer = self.answer_with(["50", "18"])
        points, is_correct, _ = grade_fill_blank(answer)
        self.assertEqual(points, 0)
        self.assertFalse(is_correct)
        # partial_credit tracks awarded points, not blanks right, so it must
        # agree with the 0 above rather than report 0.50.
        self.assertTrue(answer.auto_grade())
        answer.refresh_from_db()
        self.assertEqual(answer.partial_credit, Decimal("0.00"))

        QuizAnswer.objects.all().delete()
        points, is_correct, _ = grade_fill_blank(self.answer_with(["50", "19"]))
        self.assertEqual(points, 2)
        self.assertTrue(is_correct)

    def test_question_without_blanks_needs_manual_grading(self):
        question = QuizQuestion.objects.create(
            question_type="FB",
            title="Unanswerable",
            content="___",
            correct_answers=None,
        )
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=question, points=1, order=4
        )
        points, is_correct, needs_manual = grade_fill_blank(
            self.answer_with(["50"], question=question)
        )
        self.assertEqual(points, 0)
        self.assertFalse(is_correct)
        self.assertTrue(needs_manual)

    def test_malformed_stored_answer_scores_zero_without_raising(self):
        for raw in ("50, 19", '["50"', '{"a": 1}', ""):
            QuizAnswer.objects.all().delete()
            points, is_correct, _ = grade_fill_blank(self.answer_with(raw))
            self.assertEqual(points, 0, raw)
            self.assertFalse(is_correct, raw)

    def test_count_correct_blanks(self):
        self.assertEqual(count_correct_blanks(self.answer_with(["50", "18"])), (1, 2))

    def test_grade_answer_dispatches_to_fb(self):
        points, is_correct, needs_manual = grade_answer(self.answer_with(["50", "19"]))
        self.assertEqual(points, 2)
        self.assertTrue(is_correct)
        self.assertFalse(needs_manual)

    def test_missing_assignment_falls_back_to_one_point(self):
        orphan_quiz = Quiz.objects.create(code="fborphan", title="Orphan")
        orphan_attempt = QuizAttempt.objects.create(
            user=self.profile, quiz=orphan_quiz, attempt_number=1
        )
        answer = QuizAnswer.objects.create(
            attempt=orphan_attempt,
            question=self.question,
            answer=json.dumps(["50", "19"]),
        )
        points, is_correct, _ = grade_fill_blank(answer)
        self.assertEqual(points, 1.0)
        self.assertTrue(is_correct)


class FillBlankAttemptTestCase(TestCase):
    """auto_grade_quiz_attempt and the partial_credit field."""

    fixtures = ["language_small"]

    def setUp(self):
        self.user = User.objects.create_user(
            username="fbattempt", email="fba@test.com", password="testpass"
        )
        self.profile, _ = Profile.objects.get_or_create(
            user=self.user,
            defaults={"language": Language.objects.first()},
        )

        self.fb_question = QuizQuestion.objects.create(
            question_type="FB",
            title="Robot",
            content=ROBOT_CONTENT,
            correct_answers=ROBOT_ANSWERS,
            grading_strategy="correct_only",
        )
        self.mc_question = QuizQuestion.objects.create(
            question_type="MC",
            title="MC",
            content="2+2?",
            choices=[{"id": "a", "text": "3"}, {"id": "b", "text": "4"}],
            correct_answers={"answers": "b"},
        )
        self.quiz = Quiz.objects.create(code="fbmixed", title="Mixed Quiz")
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=self.fb_question, points=2, order=1
        )
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=self.mc_question, points=5, order=2
        )
        self.attempt = QuizAttempt.objects.create(
            user=self.profile, quiz=self.quiz, attempt_number=1
        )

    def test_partial_credit_records_the_real_ratio(self):
        answer = QuizAnswer.objects.create(
            attempt=self.attempt,
            question=self.fb_question,
            answer=json.dumps(["50", "18"]),
        )
        self.assertTrue(answer.auto_grade())
        answer.refresh_from_db()
        self.assertEqual(answer.points, 1)
        self.assertFalse(answer.is_correct)
        self.assertEqual(answer.partial_credit, Decimal("0.50"))

    def test_partial_credit_is_quantized_for_the_field(self):
        """2/3 has to round to 2dp or the DecimalField rejects it."""
        question = QuizQuestion.objects.create(
            question_type="FB",
            title="Three blanks",
            content="___ ___ ___",
            correct_answers={
                "type": "exact",
                "case_sensitive": False,
                "blanks": [
                    {"answers": ["1"]},
                    {"answers": ["2"]},
                    {"answers": ["3"]},
                ],
            },
            grading_strategy="correct_only",
        )
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=question, points=3, order=3
        )
        answer = QuizAnswer.objects.create(
            attempt=self.attempt, question=question, answer=json.dumps(["1", "2", "x"])
        )
        self.assertTrue(answer.auto_grade())
        answer.refresh_from_db()
        self.assertEqual(answer.partial_credit, Decimal("0.67"))

    def test_auto_grade_quiz_attempt_totals(self):
        QuizAnswer.objects.create(
            attempt=self.attempt,
            question=self.fb_question,
            answer=json.dumps(["50", "18"]),
        )
        QuizAnswer.objects.create(
            attempt=self.attempt, question=self.mc_question, answer="b"
        )

        total = auto_grade_quiz_attempt(self.attempt)
        self.assertEqual(total, 6)  # 1 of 2 on FB, 5 on MC

        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.score, 6)
        self.assertEqual(self.attempt.max_score, 7)

    def test_get_blank_details(self):
        answer = QuizAnswer.objects.create(
            attempt=self.attempt,
            question=self.fb_question,
            answer=json.dumps(["50", "18"]),
        )
        details = answer.get_blank_details()
        self.assertEqual(len(details), 2)
        self.assertEqual(details[0]["number"], 1)
        self.assertEqual(details[0]["label"], "Số thứ mười")
        self.assertEqual(details[0]["value"], "50")
        self.assertTrue(details[0]["is_correct"])
        self.assertFalse(details[1]["is_correct"])

    def test_get_blank_details_labels_unlabelled_blanks(self):
        question = QuizQuestion.objects.create(
            question_type="FB",
            title="Unlabelled",
            content="___",
            correct_answers={"blanks": [{"answers": ["1"]}]},
        )
        answer = QuizAnswer.objects.create(
            attempt=self.attempt, question=question, answer=json.dumps(["1"])
        )
        # Translated at runtime (LANGUAGE_CODE is "vi"), so assert on the number.
        self.assertIn("1", answer.get_blank_details()[0]["label"])

    def test_get_blank_details_is_empty_for_other_types(self):
        answer = QuizAnswer.objects.create(
            attempt=self.attempt, question=self.mc_question, answer="b"
        )
        self.assertEqual(answer.get_blank_details(), [])

    def test_get_formatted_answer(self):
        answer = QuizAnswer.objects.create(
            attempt=self.attempt,
            question=self.fb_question,
            answer=json.dumps(["50", "19"]),
        )
        self.assertEqual(
            answer.get_formatted_answer(), "Số thứ mười: 50; Số nhỏ hơn 100: 19"
        )


class FillBlankSubmitViewTestCase(TestCase):
    """The blanks post under one repeated key and must survive round-trip."""

    fixtures = ["language_small"]

    def setUp(self):
        self.user = User.objects.create_user(
            username="fbsubmit", email="fbs@test.com", password="testpass"
        )
        self.profile, _ = Profile.objects.get_or_create(
            user=self.user,
            defaults={"language": Language.objects.first()},
        )
        self.question = QuizQuestion.objects.create(
            question_type="FB",
            title="Robot",
            content=ROBOT_CONTENT,
            correct_answers=ROBOT_ANSWERS,
            grading_strategy="correct_only",
        )
        self.quiz = Quiz.objects.create(code="fbsubmit", title="FB Submit Quiz")
        QuizQuestionAssignment.objects.create(
            quiz=self.quiz, question=self.question, points=2, order=1
        )
        self.attempt = QuizAttempt.objects.create(
            user=self.profile, quiz=self.quiz, attempt_number=1
        )
        self.client.force_login(self.user)

    def submit(self, values):
        return self.client.post(
            reverse(
                "quiz_submit",
                kwargs={"code": self.quiz.code, "attempt_id": self.attempt.id},
            ),
            {f"q_{self.question.id}": values},
        )

    def test_repeated_field_is_stored_as_an_ordered_array(self):
        self.submit(["50", "19"])

        answer = QuizAnswer.objects.get(attempt=self.attempt, question=self.question)
        self.assertEqual(json.loads(answer.answer), ["50", "19"])
        self.assertEqual(answer.points, 2)
        self.assertTrue(answer.is_correct)

    def test_empty_blank_keeps_the_positions_aligned(self):
        self.submit(["", "19"])

        answer = QuizAnswer.objects.get(attempt=self.attempt, question=self.question)
        self.assertEqual(json.loads(answer.answer), ["", "19"])
        self.assertEqual(answer.points, 1)
        self.assertFalse(answer.is_correct)

    def test_save_answer_endpoint_stores_the_array(self):
        response = self.client.post(
            reverse(
                "quiz_save_answer",
                kwargs={"code": self.quiz.code, "attempt_id": self.attempt.id},
            ),
            data=json.dumps(
                {"question_id": self.question.id, "answer": json.dumps(["50", ""])}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        answer = QuizAnswer.objects.get(attempt=self.attempt, question=self.question)
        self.assertEqual(json.loads(answer.answer), ["50", ""])


class FillBlankImportTestCase(TestCase):
    """AI import normalization for the FB payload shape."""

    def test_valid_payload_round_trips(self):
        choices, correct = normalize_quiz_question_payload("FB", None, ROBOT_ANSWERS)
        self.assertEqual(choices, [])
        self.assertEqual(correct["type"], "exact")
        self.assertFalse(correct["case_sensitive"])
        self.assertEqual(len(correct["blanks"]), 2)
        self.assertEqual(correct["blanks"][1]["answers"], ["19"])

    def test_choices_are_forced_empty(self):
        choices, _ = normalize_quiz_question_payload(
            "FB", [{"id": "a", "text": "x"}], ROBOT_ANSWERS
        )
        self.assertEqual(choices, [])

    def test_blanks_with_no_usable_answer_are_dropped(self):
        _, correct = normalize_quiz_question_payload(
            "FB",
            None,
            {"blanks": [{"answers": ["50"]}, {"answers": []}, {"answers": ["  "]}]},
        )
        self.assertEqual(len(correct["blanks"]), 1)

    def test_missing_or_empty_blanks_yields_no_answer_key(self):
        for payload in (None, {}, {"blanks": []}, {"blanks": "nope"}, {"answers": ["5"]}):
            _, correct = normalize_quiz_question_payload("FB", None, payload)
            self.assertIsNone(correct, payload)

    def test_blank_count_is_capped(self):
        _, correct = normalize_quiz_question_payload(
            "FB", None, {"blanks": [{"answers": [str(i)]} for i in range(40)]}
        )
        self.assertEqual(len(correct["blanks"]), 20)

    def test_case_sensitive_flag_is_coerced_to_bool(self):
        _, correct = normalize_quiz_question_payload(
            "FB", None, {"case_sensitive": "yes", "blanks": [{"answers": ["1"]}]}
        )
        self.assertFalse(correct["case_sensitive"])

    def test_fb_survives_the_full_response_parser(self):
        result = parse_quiz_import_response(
            json.dumps(
                {
                    "questions": [
                        {
                            "title": "Robot",
                            "question_type": "FB",
                            "content": ROBOT_CONTENT,
                            "choices": [],
                            "correct_answers": ROBOT_ANSWERS,
                        }
                    ]
                }
            )
        )
        self.assertTrue(result["success"])
        question = result["questions"][0]
        self.assertEqual(question["question_type"], "FB")
        self.assertEqual(len(question["correct_answers"]["blanks"]), 2)
        self.assertEqual(result["summary"]["type_counts"], {"FB": 1})
