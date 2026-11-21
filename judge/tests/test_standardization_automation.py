import io
from copy import deepcopy
from unittest import mock

from django.test import SimpleTestCase, TestCase

from judge.models import Language, Problem, ProblemGroup
from judge.models.problem_data import ProblemData
from judge.standardization import contracts, format_v1, integrity
from judge.standardization.hashing import compute_judge_hash
from judge.tests.test_standardization_steps import CONTENT_PAYLOAD


class ContentIntegrityTest(SimpleTestCase):
    def test_file_contract_is_visible_even_if_model_omits_filenames_in_body(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        payload["io_contract"] = {
            "kind": "file",
            "input_file": "PART.INP",
            "output_file": "PART.OUT",
        }
        result = format_v1.render(payload)
        self.assertIn("`PART.INP`", result)
        self.assertIn("`PART.OUT`", result)
        self.assertEqual(result.count("PART.INP"), 1)
        self.assertEqual(format_v1.validate(result).label, "STRICT_OK")

    def test_legacy_plain_and_html_samples_are_preserved(self):
        plain = "#### Example\n```\n  3 5\n  16 12 4\n```\n```text\n5\n```\n```cpp\nint main(){}\n```"
        expected = [{"input": "3 5\n16 12 4", "output": "5", "note": None}]
        self.assertEqual(
            integrity.source_examples(plain),
            [{**expected[0], "input": "  3 5\n  16 12 4"}],
        )
        self.assertEqual(
            integrity.source_examples("<pre>3 5\n16 12 4</pre><pre>5</pre>"), expected
        )

    def test_example_in_output_is_rejected_even_with_valid_headings(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        payload["output"] += "\n```sample\n1 2\n```"
        payload["examples"] = []
        self.assertTrue(integrity.content_issues(payload))

    def test_cosmetic_restructure_requires_independent_token_and_sample_proof(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        original = format_v1.render(payload)
        formatted = original.replace("### Input", "#### Input")
        self.assertTrue(integrity.can_auto_rewrite(formatted, original, payload))
        self.assertFalse(
            integrity.can_auto_rewrite(
                formatted.replace("10^9", "10^8"), original, payload
            )
        )
        changed = deepcopy(payload)
        changed["examples"][0]["output"] = "4"
        self.assertFalse(
            integrity.can_auto_rewrite(original, format_v1.render(changed), changed)
        )
        changed = deepcopy(payload)
        changed["warnings"] = ["Uncertain bound"]
        self.assertFalse(integrity.can_auto_rewrite(original, original, changed))

    def test_missing_or_unpaired_samples_never_prove_preservation(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        self.assertEqual(integrity.source_examples("```sample\n1 2\n```"), [])
        self.assertFalse(
            integrity.can_auto_rewrite("", format_v1.render(payload), payload)
        )

    def test_displayed_sample_cannot_diverge_from_structured_payload(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        original = format_v1.render(payload)
        displayed = original.replace("        3\n", "        4\n")
        self.assertFalse(integrity.can_auto_rewrite(original, displayed, payload))

    def test_constraint_in_admonition_title_cannot_disappear(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        original = '!!! note "n <= 1000"\n' + format_v1.render(payload)
        self.assertFalse(
            integrity.can_auto_rewrite(original, format_v1.render(payload), payload)
        )

    def test_reordered_or_changed_operators_are_not_cosmetic(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        original = format_v1.render(payload)
        self.assertFalse(
            integrity.can_auto_rewrite(
                original.replace("$a + b$", "$a - b$"), original, payload
            )
        )

    def test_sample_control_characters_are_held(self):
        payload = deepcopy(CONTENT_PAYLOAD)
        payload["examples"][0]["output"] = "\x1a"
        self.assertIn(
            "sample_contains_control_character", integrity.content_issues(payload)
        )


class FileAdapterTest(SimpleTestCase):
    def setUp(self):
        self.contract = contracts.Contract(
            kind="file", input_file="PSNUM.INP", output_file="PSNUM.OUT"
        )

    def test_adapter_changes_only_execution_and_is_idempotent(self):
        code = "#include <iostream>\nint main(){int n;std::cin>>n;std::cout<<n;}"
        adapted, changed = contracts.adapt_cpp_file_io(code, self.contract)
        self.assertTrue(changed)
        self.assertIn("std::cin>>n;std::cout<<n;", adapted)
        self.assertEqual(contracts.missing_files(self.contract, adapted), [])
        self.assertEqual(
            contracts.adapt_cpp_file_io(adapted, self.contract), (adapted, False)
        )

    def test_never_guess_existing_file_handling_or_raw_strings(self):
        for code in [
            'int main(){freopen("wrong.inp","r",stdin);}',
            'int main(){std::ifstream in("wrong.inp");}',
            'int main(){auto s=R"(int main(){)";}',
            "void solve(){}",
        ]:
            self.assertEqual(
                contracts.adapt_cpp_file_io(code, self.contract), (code, False)
            )

    def test_comments_and_string_literals_do_not_choose_injection_position(self):
        code = '// int main(){\nconst char* s="int main(){";\nint main(){return 0;}'
        adapted, changed = contracts.adapt_cpp_file_io(code, self.contract)
        self.assertTrue(changed)
        self.assertIn("int main(){\n    if (!std::freopen", adapted)
        self.assertIn("// int main(){\nconst char* s=", adapted)

    def test_conflicts_never_auto_adapt(self):
        self.contract.issues.append("database_init_io_conflict")
        self.assertEqual(
            contracts.adapt_cpp_file_io("int main(){}", self.contract),
            ("int main(){}", False),
        )

    def test_digit_separators_before_main_are_not_character_literals(self):
        code = "constexpr long long MOD = 1'000'000'007LL;\nint main(){char c='0';return 0;}"
        adapted, changed = contracts.adapt_cpp_file_io(code, self.contract)
        self.assertTrue(changed)
        self.assertIn("1'000'000'007LL", adapted)
        self.assertIn("char c='0'", adapted)


class GradingContractTest(TestCase):
    fixtures = ["language_small"]

    @classmethod
    def setUpTestData(cls):
        cls.problem = Problem.objects.create(
            code="contract_case",
            name="Contract",
            description="Tính tổng hai số.",
            group=ProblemGroup.objects.create(name="contract", full_name="Contract"),
            time_limit=1,
            memory_limit=262144,
            points=100,
        )

    def test_database_contract_works_when_statement_has_no_filenames(self):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="PSNUM.INP", fileio_output="PSNUM.OUT"
        )
        with mock.patch.object(
            contracts.problem_data_storage, "exists", return_value=False
        ):
            result = contracts.read_contract(self.problem, self.problem.description)
        self.assertEqual(result.kind, "file")
        self.assertEqual(result.input_file, "PSNUM.INP")
        self.assertEqual(result.issues, [])

    def test_init_contract_whitelists_metadata_and_flags_database_conflicts(self):
        ProblemData.objects.create(problem=self.problem)
        raw = b"file_io: {input: i.inp, output: i.out}\ntest_cases: [{in: secret.in, out: secret.out}]\n"
        with mock.patch.object(
            contracts.problem_data_storage, "exists", return_value=True
        ), mock.patch.object(
            contracts.problem_data_storage, "open", return_value=io.BytesIO(raw)
        ):
            result = contracts.read_contract(self.problem)
        self.assertEqual(result.input_file, "i.inp")
        self.assertIn("database_init_io_conflict", result.issues)
        self.assertNotIn("secret.in", result.prompt())
        self.assertNotIn("test_cases", result.prompt())

    def test_explicit_statement_conflict_is_held_not_silently_rewritten(self):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="PSNUM.INP", fileio_output="PSNUM.OUT"
        )
        with mock.patch.object(
            contracts.problem_data_storage, "exists", return_value=False
        ):
            result = contracts.read_contract(
                self.problem, "Use psnum.inp and psnum.out"
            )
        self.assertIn("statement_judge_io_conflict", result.issues)

    def test_judge_hash_includes_io_even_before_init_is_regenerated(self):
        data = ProblemData.objects.create(problem=self.problem)
        language = Language.objects.get(key="CPP17")
        before = compute_judge_hash(self.problem, language)
        data.fileio_input = "CASE.INP"
        data.save(update_fields=["fileio_input"])
        self.assertNotEqual(before, compute_judge_hash(self.problem, language))

    def test_path_traversal_filename_is_rejected(self):
        ProblemData.objects.create(
            problem=self.problem, fileio_input="../private", fileio_output="a.out"
        )
        with mock.patch.object(
            contracts.problem_data_storage, "exists", return_value=False
        ):
            result = contracts.read_contract(self.problem)
        self.assertIn("unsafe_or_invalid_filename", result.issues)
