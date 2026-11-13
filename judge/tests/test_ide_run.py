"""IDE Run (A22), the async submit JSON (A26) and the IDE page itself.

The judges are not running under test: judge_run / judge_request are patched,
and the bridge side is driven by feeding RunHandlerMixin the packets a judge
would send.
"""

import json
import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from judge.bridge.run_handler import RunHandlerMixin
from judge.models import CodeRun, Problem, Profile, Submission
from judge.tests.strict_helpers import (
    StrictContestMixin,
    make_group,
    make_language,
    make_online_judge,
)
from judge.utils.code_run import (
    build_init,
    prepare_run_dir,
    remove_run_dir,
    run_dir,
    sweep_stale_run_dirs,
)
from judge.views.ide import run_payload

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def make_user(name, language):
    user = User.objects.create_user(name, "%s@example.com" % name, "password")
    profile, _ = Profile.objects.get_or_create(
        user=user, defaults={"language": language}
    )
    return user, profile


class DataRootMixin:
    def setUp(self):
        super().setUp()
        self.data_root = tempfile.mkdtemp(prefix="hn-runs-")
        self.addCleanup(shutil.rmtree, self.data_root, True)
        patcher = override_settings(DMOJ_PROBLEM_DATA_ROOT=self.data_root)
        patcher.enable()
        self.addCleanup(patcher.disable)


@override_settings(LANGUAGE_CODE="en")
class IDEFixture(DataRootMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.language = make_language()
        cls.judge = make_online_judge(cls.language)
        cls.user, cls.profile = make_user("runner", cls.language)
        cls.other_user, cls.other_profile = make_user("other", cls.language)
        cls.problem = Problem.objects.create(
            code="ideprob",
            name="IDE Problem",
            description="Add two numbers.",
            time_limit=2,
            memory_limit=262144,
            points=100,
            partial=False,
            group=make_group(),
            is_public=True,
            date="2020-01-01T00:00:00Z",
        )
        cls.problem.allowed_languages.set([cls.language])
        cls.judge.problems.add(cls.problem)

    def post_run(self, cases=None, **extra):
        data = {
            "language": self.language.id,
            "source": "print(sum(map(int, input().split())))",
            "cases": json.dumps(
                cases if cases is not None else [{"input": "1 2\n", "expected": "3\n"}]
            ),
        }
        data.update(extra)
        return self.client.post(
            reverse("problem_run", args=[self.problem.code]), data, **AJAX
        )


class RunCreateTests(IDEFixture):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("judge.views.ide.judge_run", return_value=True)
        self.judge_run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_run_creates_a_run_and_no_submission(self):
        self.client.force_login(self.user)
        response = self.post_run()
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        run = CodeRun.objects.get(id=payload["id"])
        self.assertEqual(run.user, self.profile)
        self.assertEqual(run.cases, [{"input": "1 2\n", "expected": "3\n"}])
        self.assertEqual(run.status, "QU")
        self.assertFalse(Submission.objects.exists())
        self.judge_run.assert_called_once()
        # The throwaway problem holds the user's input and nothing else.
        directory = run_dir(run)
        self.assertEqual(sorted(os.listdir(directory)), ["1.in", "1.out", "init.yml"])
        with open(os.path.join(directory, "1.in")) as f:
            self.assertEqual(f.read(), "1 2\n")

    def test_anonymous_cannot_run(self):
        response = self.post_run()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CodeRun.objects.exists())

    def test_inaccessible_problem_is_404(self):
        Problem.objects.filter(id=self.problem.id).update(is_public=False)
        self.client.force_login(self.user)
        response = self.post_run()
        self.assertEqual(response.status_code, 404)
        self.assertFalse(CodeRun.objects.exists())

    def test_empty_source_is_rejected(self):
        self.client.force_login(self.user)
        response = self.post_run(source="   ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("source", response.json()["errors"])

    def test_language_not_allowed_is_rejected(self):
        self.problem.allowed_languages.clear()
        self.client.force_login(self.user)
        response = self.post_run()
        self.assertEqual(response.status_code, 400)
        self.assertIn("language", response.json()["errors"])

    def test_too_many_cases_is_rejected(self):
        self.client.force_login(self.user)
        response = self.post_run(cases=[{"input": "", "expected": None}] * 7)
        self.assertEqual(response.status_code, 400)
        self.assertIn("cases", response.json()["errors"])

    def test_malformed_cases_are_rejected(self):
        self.client.force_login(self.user)
        for bad in ("not json", "{}", "[]", '[{"input": 3}]', '["x"]'):
            response = self.client.post(
                reverse("problem_run", args=[self.problem.code]),
                {"language": self.language.id, "source": "x", "cases": bad},
                **AJAX,
            )
            self.assertEqual(response.status_code, 400, bad)

    @override_settings(DMOJ_RUN_MAX_INPUT_LENGTH=10)
    def test_oversized_input_is_rejected(self):
        self.client.force_login(self.user)
        response = self.post_run(cases=[{"input": "x" * 11, "expected": None}])
        self.assertEqual(response.status_code, 400)

    def test_blank_expected_means_no_check(self):
        self.client.force_login(self.user)
        response = self.post_run(cases=[{"input": "1", "expected": "  \n"}])
        self.assertEqual(response.status_code, 201)
        run = CodeRun.objects.get()
        self.assertIsNone(run.cases[0]["expected"])

    def test_file_names_are_sanitised(self):
        self.client.force_login(self.user)
        for name in ("../etc/passwd", "a/b", ".hidden", "x" * 70):
            response = self.post_run(io_mode="file", fileio_input=name)
            self.assertEqual(response.status_code, 400, name)
        self.assertFalse(CodeRun.objects.exists())

        response = self.post_run(
            io_mode="file", fileio_input="sum.inp", fileio_output="sum.out"
        )
        self.assertEqual(response.status_code, 201)
        run = CodeRun.objects.get()
        self.assertEqual(
            build_init(run)["file_io"], {"input": "sum.inp", "output": "sum.out"}
        )

    def test_standard_io_ignores_file_names(self):
        self.client.force_login(self.user)
        self.post_run(io_mode="std", fileio_input="sum.inp")
        run = CodeRun.objects.get()
        self.assertEqual(run.fileio_input, "")
        self.assertNotIn("file_io", build_init(run))

    def test_one_active_run_at_a_time(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post_run().status_code, 201)
        response = self.post_run()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(CodeRun.objects.count(), 1)

    @override_settings(DMOJ_RUN_PER_MINUTE=2)
    def test_per_minute_limit(self):
        self.client.force_login(self.user)
        for _ in range(2):
            self.assertEqual(self.post_run().status_code, 201)
            CodeRun.objects.update(status="D")
        self.assertEqual(self.post_run().status_code, 429)

    def test_limits_are_capped(self):
        Problem.objects.filter(id=self.problem.id).update(
            time_limit=30, memory_limit=4 * 1024 * 1024
        )
        self.client.force_login(self.user)
        self.post_run()
        run = CodeRun.objects.get()
        self.assertEqual(run.time_limit, 5.0)
        self.assertEqual(run.memory_limit, 524288)

    def test_new_run_sweeps_old_run_directories(self):
        stale = os.path.join(self.data_root, "__run_999_old")
        os.makedirs(stale)
        os.utime(stale, (0, 0))
        self.client.force_login(self.user)
        self.assertEqual(self.post_run().status_code, 201)
        self.assertFalse(os.path.exists(stale))

    def test_bridge_unreachable_reports_503_and_cleans_up(self):
        self.judge_run.return_value = False
        self.client.force_login(self.user)
        response = self.post_run()
        self.assertEqual(response.status_code, 503)
        run = CodeRun.objects.get()
        self.assertFalse(os.path.exists(run_dir(run)))

    @override_settings(DMOJ_RUN_ENABLED=False)
    def test_can_be_turned_off(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post_run().status_code, 403)


class RunAccessTests(IDEFixture):
    def make_run(self, profile, **fields):
        defaults = dict(
            user=profile,
            problem=self.problem,
            language=self.language,
            source="print(1)",
            cases=[{"input": "", "expected": None}],
            time_limit=1,
            memory_limit=65536,
        )
        defaults.update(fields)
        return CodeRun.objects.create(**defaults)

    def test_owner_reads_status(self):
        run = self.make_run(self.profile)
        self.client.force_login(self.user)
        response = self.client.get(reverse("code_run_status", args=[run.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], run.id)

    def test_other_user_cannot_read_or_abort(self):
        run = self.make_run(self.profile)
        self.client.force_login(self.other_user)
        self.assertEqual(
            self.client.get(reverse("code_run_status", args=[run.id])).status_code,
            404,
        )
        with mock.patch("judge.views.ide.abort_run") as abort:
            response = self.client.post(reverse("code_run_abort", args=[run.id]))
        self.assertEqual(response.status_code, 404)
        abort.assert_not_called()
        self.assertEqual(CodeRun.objects.get(id=run.id).status, "QU")

    def test_owner_aborts_a_queued_run(self):
        run = self.make_run(self.profile)
        prepare_run_dir(run)

        def fake_abort(r):
            CodeRun.objects.filter(id=r.id).update(status="AB")
            return True

        self.client.force_login(self.user)
        with mock.patch("judge.views.ide.abort_run", side_effect=fake_abort):
            response = self.client.post(reverse("code_run_abort", args=[run.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "AB")

    def test_payload_verdicts(self):
        run = self.make_run(
            self.profile,
            status="D",
            cases=[
                {"input": "1", "expected": "1"},
                {"input": "2", "expected": "3"},
                {"input": "3", "expected": None},
            ],
            results=[
                {
                    "position": 1,
                    "result": "AC",
                    "time": 0.01,
                    "memory": 1000,
                    "output": "1",
                },
                {
                    "position": 2,
                    "result": "WA",
                    "time": 0.01,
                    "memory": 1000,
                    "output": "2",
                },
                {
                    "position": 3,
                    "result": "WA",
                    "time": 0.01,
                    "memory": 1000,
                    "output": "4",
                },
            ],
        )
        payload = run_payload(run)
        self.assertEqual([c["verdict"] for c in payload["cases"]], ["AC", "WA", "OK"])
        self.assertEqual(payload["verdict"], "WA")

        run.results[1]["result"] = "AC"
        self.assertEqual(run_payload(run)["verdict"], "OK")
        run.cases[2]["expected"] = "4"
        run.results[2]["result"] = "AC"
        self.assertEqual(run_payload(run)["verdict"], "AC")

    def test_payload_strips_ansi_from_compile_errors(self):
        run = self.make_run(
            self.profile, status="CE", error="\x1b[01;31merror\x1b[0m: bad\r\n"
        )
        payload = run_payload(run)
        self.assertEqual(payload["verdict"], "CE")
        self.assertEqual(payload["error"], "error: bad\n")


class FakeJudge(RunHandlerMixin):
    """Just enough of JudgeHandler for the run packet handlers."""

    name = "fake-judge"

    def __init__(self):
        self.sent = []
        self.freed = []
        self._working = False
        self._working_data = {}
        self._no_response_job = None

    def send(self, packet):
        self.sent.append(packet)

    def close(self):
        pass

    def _kill_if_no_response(self):
        pass

    def _free_self(self, packet):
        self.freed.append(packet["submission-id"])
        self._working = False
        self._working_data = {}


class RunBridgeTests(IDEFixture):
    def setUp(self):
        super().setUp()
        self.run = CodeRun.objects.create(
            user=self.profile,
            problem=self.problem,
            language=self.language,
            source="print(1)",
            cases=[{"input": "a", "expected": "1"}, {"input": "b", "expected": None}],
            time_limit=1.5,
            memory_limit=65536,
        )
        prepare_run_dir(self.run)
        self.judge = FakeJudge()
        timer = mock.patch("judge.bridge.run_handler.threading.Timer")
        timer.start()
        self.addCleanup(timer.stop)

    def packet(self, name, **fields):
        fields.update({"name": name, "submission-id": self.run.judge_id})
        return fields

    def dispatch(self, packet):
        self.assertTrue(self.judge.is_run_packet(packet))
        self.judge._run_handlers()[packet["name"]](packet)

    def test_submit_sends_the_run_as_a_negative_submission(self):
        self.judge.submit_run(
            self.run.judge_id, self.run.problem_dir, "PY3", "print(1)"
        )
        packet = self.judge.sent[-1]
        self.assertEqual(packet["name"], "submission-request")
        self.assertEqual(packet["submission-id"], -self.run.id)
        self.assertEqual(packet["problem-id"], self.run.problem_dir)
        self.assertEqual(packet["time-limit"], 1.5)
        self.assertFalse(packet["short-circuit"])

    def test_full_run_lifecycle(self):
        self.judge.submit_run(
            self.run.judge_id, self.run.problem_dir, "PY3", "print(1)"
        )
        self.dispatch(self.packet("submission-acknowledged"))
        self.assertEqual(CodeRun.objects.get(id=self.run.id).status, "P")
        self.dispatch(self.packet("grading-begin", pretested=False))
        self.dispatch(
            self.packet(
                "test-case-status",
                cases=[
                    {
                        "position": 1,
                        "status": 0,
                        "time": 0.01,
                        "memory": 900,
                        "points": 1,
                        "total-points": 1,
                        "output": "1\n",
                    },
                ],
            )
        )
        self.dispatch(
            self.packet(
                "test-case-status",
                cases=[
                    {
                        "position": 2,
                        "status": 1,
                        "time": 0.02,
                        "memory": 950,
                        "points": 0,
                        "total-points": 1,
                        "output": "2\n",
                    },
                ],
            )
        )
        self.dispatch(self.packet("grading-end"))

        run = CodeRun.objects.get(id=self.run.id)
        self.assertEqual(run.status, "D")
        self.assertIsNotNone(run.finished)
        self.assertEqual([r["result"] for r in run.results], ["AC", "WA"])
        self.assertEqual(self.judge.freed, [self.run.judge_id])
        # Left for the next run's sweep, so the judges rescan once, not twice.
        self.assertTrue(os.path.exists(run_dir(run)))
        payload = run_payload(run)
        self.assertEqual([c["verdict"] for c in payload["cases"]], ["AC", "OK"])
        self.assertFalse(Submission.objects.exists())

    def test_runtime_error_flags(self):
        self.dispatch(
            self.packet(
                "test-case-status",
                cases=[
                    {
                        "position": 1,
                        "status": 2,
                        "time": 0,
                        "memory": 0,
                        "points": 0,
                        "total-points": 1,
                        "output": "",
                        "feedback": "segmentation fault",
                    }
                ],
            )
        )
        run = CodeRun.objects.get(id=self.run.id)
        self.assertEqual(run.results[0]["result"], "RTE")
        self.assertEqual(run.results[0]["feedback"], "segmentation fault")

    def test_compile_error_finishes_the_run(self):
        self.dispatch(self.packet("compile-error", log="boom"))
        run = CodeRun.objects.get(id=self.run.id)
        self.assertEqual(run.status, "CE")
        self.assertEqual(run.error, "boom")
        self.assertEqual(self.judge.freed, [self.run.judge_id])

    def test_internal_error(self):
        self.dispatch(self.packet("internal-error", message="traceback"))
        self.assertEqual(CodeRun.objects.get(id=self.run.id).status, "IE")

    def test_final_state_is_not_overwritten(self):
        self.dispatch(self.packet("submission-terminated"))
        self.dispatch(self.packet("grading-end"))
        self.assertEqual(CodeRun.objects.get(id=self.run.id).status, "AB")

    def test_positive_ids_are_not_run_packets(self):
        self.assertFalse(self.judge.is_run_packet({"submission-id": 5}))
        self.assertFalse(self.judge.is_run_packet({"name": "ping-response"}))


class RunDirectoryTests(DataRootMixin, TestCase):
    def test_remove_refuses_anything_but_a_run_directory(self):
        victim = os.path.join(self.data_root, "aplusb")
        os.makedirs(victim)
        remove_run_dir("aplusb")
        remove_run_dir("../" + os.path.basename(self.data_root))
        self.assertTrue(os.path.isdir(victim))

    def test_sweep_removes_only_old_run_directories(self):
        old = os.path.join(self.data_root, "__run_1_abc")
        new = os.path.join(self.data_root, "__run_2_def")
        problem = os.path.join(self.data_root, "aplusb")
        for path in (old, new, problem):
            os.makedirs(path)
        os.utime(old, (0, 0))
        os.utime(problem, (0, 0))
        self.assertEqual(sweep_stale_run_dirs(), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.exists(problem))


class AsyncSubmitTests(IDEFixture):
    def submit(self, **extra):
        data = {"source": "print(1)", "language": self.language.id, "judge": ""}
        data.update(extra)
        return self.client.post(
            reverse("problem_submit", args=[self.problem.code]), data, **AJAX
        )

    def test_async_submit_returns_json_and_creates_one_submission(self):
        self.client.force_login(self.user)
        with mock.patch("judge.judgeapi.judge_request") as judge_request:
            judge_request.side_effect = lambda packet, reply=True: {
                "name": "submission-received",
                "submission-id": packet["submission-id"],
            }
            response = self.submit()
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        submission = Submission.objects.get()
        self.assertEqual(payload["id"], submission.id)
        self.assertEqual(
            payload["url"], reverse("submission_status", args=[submission.id])
        )
        self.assertEqual(payload["problem"]["code"], self.problem.code)
        self.assertFalse(payload["graded"])
        self.assertIn("last_msg", payload)

    def test_plain_post_still_redirects(self):
        self.client.force_login(self.user)
        with mock.patch("judge.judgeapi.judge_request", side_effect=OSError):
            response = self.client.post(
                reverse("problem_submit", args=[self.problem.code]),
                {"source": "print(1)", "language": self.language.id, "judge": ""},
            )
        self.assertEqual(response.status_code, 302)

    def test_invalid_form_is_a_json_400(self):
        self.client.force_login(self.user)
        response = self.submit(source="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("source", response.json()["errors"])
        self.assertFalse(Submission.objects.exists())

    @override_settings(DMOJ_SUBMISSION_LIMIT=0)
    def test_queue_limit_is_a_json_429(self):
        self.client.force_login(self.user)
        response = self.submit()
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json()["ok"])

    def test_banned_user_is_a_json_403(self):
        self.problem.banned_users.add(self.profile)
        self.client.force_login(self.user)
        response = self.submit()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Submission.objects.exists())


class SubmissionJsonTests(IDEFixture):
    def make_submission(self, profile, **fields):
        defaults = dict(user=profile, problem=self.problem, language=self.language)
        defaults.update(fields)
        return Submission.objects.create(**defaults)

    def test_owner_sees_the_verdict(self):
        sub = self.make_submission(
            self.profile,
            status="D",
            result="AC",
            case_points=10,
            case_total=10,
            time=0.5,
            memory=2048,
        )
        self.client.force_login(self.user)
        payload = self.client.get(reverse("submission_json", args=[sub.id])).json()
        self.assertTrue(payload["graded"])
        self.assertEqual(payload["result"], "AC")
        self.assertEqual((payload["points"], payload["total"]), (10, 10))
        self.assertEqual(payload["time"], 0.5)

    def test_pending_submission_has_no_fake_zeroes(self):
        sub = self.make_submission(self.profile, status="QU")
        self.client.force_login(self.user)
        payload = self.client.get(reverse("submission_json", args=[sub.id])).json()
        self.assertFalse(payload["graded"])
        for key in ("result", "points", "total", "time", "memory"):
            self.assertIsNone(payload[key], key)

    def test_stranger_cannot_read_a_private_submission(self):
        Problem.objects.filter(id=self.problem.id).update(is_public=False)
        sub = self.make_submission(self.profile, status="D", result="AC")
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("submission_json", args=[sub.id]))
        self.assertEqual(response.status_code, 404)

    def test_hidden_result_is_not_sent(self):
        sub = self.make_submission(
            self.profile, status="D", result="WA", case_points=0, case_total=10
        )

        def hide(submissions, user):
            for s in submissions:
                s._is_result_hidden = True

        self.client.force_login(self.user)
        with mock.patch("judge.views.ide.mark_hidden_result_submissions", hide):
            payload = self.client.get(reverse("submission_json", args=[sub.id])).json()
        self.assertTrue(payload["result_hidden"])
        self.assertIsNone(payload["result"])
        self.assertIsNone(payload["points"])


class IDEPageTests(IDEFixture):
    def test_ide_renders_for_a_logged_in_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("problem_ide", args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="hn-ide"', html)
        self.assertIn('id="ide-run"', html)
        self.assertIn('data-async="1"', html)
        self.assertIn(reverse("problem_run", args=[self.problem.code]), html)

    @override_settings(DMOJ_RUN_ENABLED=False)
    def test_no_run_button_without_run(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("problem_ide", args=[self.problem.code]))
        self.assertNotIn('id="ide-run"', response.content.decode())

    def test_anonymous_sees_the_statement_and_a_login_link(self):
        response = self.client.get(reverse("problem_ide", args=[self.problem.code]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Add two numbers.", html)
        self.assertNotIn('id="problem_submit"', html)

    def test_back_link_rejects_other_hosts(self):
        self.client.force_login(self.user)
        url = reverse("problem_ide", args=[self.problem.code])
        response = self.client.get(url + "?from=https://evil.example/")
        self.assertEqual(
            response.context["ide_back_url"],
            reverse("problem_detail", args=[self.problem.code]),
        )
        response = self.client.get(url + "?from=/contest/x/problems/ideprob")
        self.assertRedirects(
            response, "/contest/x/ide/ideprob", fetch_redirect_response=False
        )

    def test_problem_page_links_to_the_ide_and_submits_async(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("problem_detail", args=[self.problem.code]))
        html = response.content.decode()
        self.assertIn(reverse("problem_ide", args=[self.problem.code]), html)
        self.assertIn('data-async="1"', html)
        self.assertIn('id="submit-results"', html)


@override_settings(LANGUAGE_CODE="en")
class StrictContestIDETests(DataRootMixin, StrictContestMixin, TestCase):
    """The IDE must never become a way out of a proctored contest."""

    def test_ide_page_redirects_to_the_contest_ide(self):
        self.join()
        self.login()
        response = self.client.get(reverse("problem_ide", args=[self.problem.code]))
        self.assertRedirects(
            response,
            reverse("contest_ide", args=[self.contest.key, self.problem.code]),
            fetch_redirect_response=False,
        )

    def test_run_needs_a_live_proctored_session(self):
        self.join(armed=False)
        self.login()
        with mock.patch("judge.views.ide.judge_run", return_value=True) as judge_run:
            response = self.client.post(
                reverse("problem_run", args=[self.problem.code]),
                {
                    "language": self.language.id,
                    "source": "print(1)",
                    "cases": json.dumps([{"input": "", "expected": None}]),
                },
                **AJAX,
            )
        self.assertEqual(response.status_code, 403)
        judge_run.assert_not_called()
        self.assertFalse(CodeRun.objects.exists())

    def contest_ide(self):
        return self.client.get(
            reverse("contest_ide", args=[self.contest.key, self.problem.code])
        )

    def test_strict_ide_is_the_online_ide_in_contest_mode(self):
        self.join()
        self.login()
        response = self.contest_ide()
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertTemplateUsed(response, "problem/ide.html")
        self.assertIn("hn-ide is-contest", body)
        # Submitting posts with fetch(): no navigation, so fullscreen holds.
        self.assertIn('data-async="1"', body)
        self.assertIn('id="ide-run"', body)
        self.assertIn('id="ide-problem-nav"', body)
        # Loading a file would be pasting by another name; a nested
        # fullscreen would confuse the exit detection.
        self.assertNotIn('id="ide-upload"', body)
        self.assertNotIn('id="ide-fullscreen"', body)

    def test_strict_ide_config_is_per_problem(self):
        self.join()
        self.login()
        config = self.contest_ide().context["ide_problem_config"]
        self.assertEqual(config["problem"], self.problem.code)
        self.assertEqual(
            config["runUrl"], reverse("problem_run", args=[self.problem.code])
        )
        self.assertIn(":%s:" % self.contest.id, config["draftScope"])

    def test_strict_ide_lists_only_this_contests_submissions(self):
        self.join()
        self.login()
        practice = Submission.objects.create(
            user=self.participant, problem=self.problem, language=self.language
        )
        in_contest = Submission.objects.create(
            user=self.participant,
            problem=self.problem,
            language=self.language,
            contest_object=self.contest,
        )
        recent = [s["id"] for s in self.contest_ide().context["ide_recent_submissions"]]
        self.assertIn(in_contest.id, recent)
        self.assertNotIn(practice.id, recent)

    def test_strict_async_submit_without_a_live_session_is_json_403(self):
        # The IDE now posts with fetch(); the strict gate must still hold and
        # say why in JSON instead of a page the contestant never sees.
        self.join(armed=False)
        self.login()
        response = self.client.post(
            reverse("problem_submit", args=[self.problem.code]),
            {"source": "print(1)", "language": self.language.id, "judge": ""},
            **AJAX,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(Submission.objects.filter(user=self.participant).exists())
