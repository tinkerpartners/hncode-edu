from django.test import SimpleTestCase

from ai_features.quiz_import_service import (
    QUIZ_IMPORT_SYSTEM_PROMPT,
    normalize_quiz_question_payload,
)


class QuizImportPromptSemanticsTest(SimpleTestCase):
    """The import prompt must define the MEANING of correct_answers per type.

    These are prompt-invariant checks: they don't run the LLM, they assert the
    instruction text carries the anchors that prevent the composite-SA bug and
    its MA/MC siblings. They guard against future edits silently dropping them.
    """

    def test_prompt_defines_sa_or_semantics_and_single_answer_scope(self):
        p = QUIZ_IMPORT_SYSTEM_PROMPT
        self.assertIn("logical OR", p)
        self.assertIn("ALTERNATIVE", p)
        # SA is for ONE value; anything with several parts must route to FB.
        self.assertIn("Use SA ONLY when", p)
        self.assertIn("use FB", p)
        # The concrete RIGHT/WRONG example must be present, and it must now
        # contrast equivalent forms against genuinely different answers.
        self.assertIn("RIGHT:", p)
        self.assertIn("WRONG:", p)
        self.assertIn('["5", "five", "năm"]', p)
        self.assertIn('["50", "19"]', p)
        # Grading is normalized exact (whitespace/case ignored).
        self.assertIn("NORMALIZED EXACT", p)

    def test_prompt_defines_ma_and_mc_meaning(self):
        p = QUIZ_IMPORT_SYSTEM_PROMPT
        # MA is AND / complete set.
        self.assertIn("COMPLETE set", p)
        self.assertIn("logical AND", p)
        # MC single id must exist among the listed choices.
        self.assertIn("one of the ids", p)

    def test_prompt_defines_fb_blank_semantics(self):
        p = QUIZ_IMPORT_SYSTEM_PROMPT
        # Multi-part questions route to FB, one entry per blank, in order.
        self.assertIn("MORE THAN ONE blank", p)
        self.assertIn('"blanks"', p)
        self.assertIn("ONE entry in", p)
        self.assertIn("SAME ORDER", p)
        # Each blank is graded on its own, and within a blank the list keeps
        # SA's OR meaning rather than becoming a sequence.
        self.assertIn("graded on its own", p)
        self.assertIn("equivalent forms of THAT blank only", p)
        # A blank's label must not give the answer away.
        self.assertIn("MUST NOT reveal the answer", p)

    def test_prompt_no_longer_carries_the_composite_sa_workaround(self):
        """FB replaced it. Leaving both in makes the model pick either one."""
        p = QUIZ_IMPORT_SYSTEM_PROMPT
        self.assertNotIn("REQUIRED ANSWER FORMAT", p)
        self.assertNotIn("Chloe: 5, Leo: 8", p)
        self.assertNotIn("invented values", p)


class QuizImportTitleRulesTest(SimpleTestCase):
    """TITLE RULES must instruct non-spoiler, thematic titles."""

    def test_prompt_forbids_spoiler_titles(self):
        p = QUIZ_IMPORT_SYSTEM_PROMPT
        self.assertIn("MUST NOT", p)
        self.assertIn("solution", p)
        self.assertIn("approach", p)
        # Thematic/neutral guidance present.
        self.assertIn("THEMATIC", p)
        # The old spoiler-inducing instruction is gone.
        self.assertNotIn("brief, descriptive title", p)


class NormalizeCompositeSATest(SimpleTestCase):
    """Lock the write-path guarantee: normalize does NOT split a composite SA
    answer on commas — one entry stays one entry (defaults exact/insensitive)."""

    def test_normalize_keeps_single_composite_sa_answer(self):
        choices, correct = normalize_quiz_question_payload(
            "SA",
            None,
            {"answers": ["Chloe: 5, Leo: 8, Emma: 13, Lily: 15"]},
        )
        self.assertEqual(correct["answers"], ["Chloe: 5, Leo: 8, Emma: 13, Lily: 15"])
        self.assertEqual(correct["type"], "exact")
        self.assertFalse(correct["case_sensitive"])
        self.assertEqual(choices, [])
