"""The online IDE (A20/A22/A26): the full-screen editor page, IDE "Run" with
the user's own input, and the JSON the IDE and the problem page poll.

Run is deliberately a separate pipeline from submitting. A run is a CodeRun,
never a Submission: it cannot change points, rankings, best submissions or
contest scores, and it never sees the problem's test data. See
judge/utils/code_run.py for how it reaches the judges.
"""

import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language, gettext as _, gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from judge.judgeapi import abort_run, judge_run
from judge.models import (
    CodeRun,
    Language,
    LanguageLimit,
    Problem,
    Submission,
)
from judge.utils.code_run import (
    is_valid_fileio_name,
    max_cases,
    max_input_length,
    max_memory_limit,
    max_time_limit,
    output_prefix_length,
    prepare_run_dir,
    remove_run_dir,
    run_setting,
    sweep_stale_run_dirs,
)
from judge.utils.contest_strict import is_strict_live, strict_session_ok
from judge.utils.hidden_results import (
    mark_hidden_result_submissions,
)
from judge.views.problem import ProblemDetail

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text):
    return ANSI_RE.sub("", text or "").replace("\r\n", "\n")


def run_enabled():
    return run_setting("ENABLED", True)


class IDEContextMixin:
    """The context templates/problem/ide.html needs, on top of ProblemDetail's.
    Shared by the practice IDE below and the strict contest IDE
    (judge/views/contests.py ContestIDE)."""

    template_name = "problem/ide.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["layout"] = "no_wrapper"
        context["run_enabled"] = run_enabled() and context.get("show_inline_submit")
        context["run_max_input"] = max_input_length()
        context["run_max_cases"] = max_cases()
        context["ide_recent_submissions"] = self.get_recent_submissions()
        context["ide_back_url"] = self.get_back_url()
        context["ide_navigation"] = self.get_problem_navigation(context)
        if context.get("show_inline_submit"):
            # Per-problem state, read again by ide.js when the contest IDE
            # switches problem in place (no script runs on a swapped-in page).
            context["ide_problem_config"] = {
                "problem": self.object.code,
                "problemId": self.object.id,
                "runUrl": reverse("problem_run", args=[self.object.code]),
                "fileio": {
                    "input": context.get("fileio_input") or "",
                    "output": context.get("fileio_output") or "",
                },
                "recent": context["ide_recent_submissions"],
                "draftScope": context.get("ide_draft_scope") or "",
                "submitBlocked": bool(
                    context.get("no_judges")
                    or (
                        self.request.in_contest
                        and context.get("submission_limit")
                        and context.get("submissions_left") is not None
                        and context["submissions_left"] <= 0
                    )
                ),
            }
        return context

    def get_problem_navigation(self, context):
        entries = (
            context.get("lesson_sidebar_problems")
            or context.get("contest_sidebar_problems")
            or []
        )
        if not entries:
            return []
        navigation = []
        for number, entry in enumerate(entries, 1):
            problem = entry["problem"]
            href = entry["url"]
            if context.get("lesson"):
                href = reverse(
                    "course_lesson_problem_ide",
                    args=(self.course.slug, self.lesson.id, problem.code),
                )
            navigation.append(
                {
                    "number": number,
                    "code": problem.code,
                    "name": problem.translated_name(self.request.LANGUAGE_CODE),
                    "state": entry["state"],
                    "url": href,
                    "is_current": problem.id == self.object.id,
                }
            )
        return navigation

    def get_recent_submissions_queryset(self):
        return Submission.objects.filter(user=self.profile, problem=self.object)

    def get_recent_submissions(self):
        if self.profile is None:
            return []
        submissions = (
            self.get_recent_submissions_queryset()
            .select_related("problem", "language")
            .order_by("-id")[:8]
        )
        return [submission_payload(s, self.request.user) for s in submissions]


class ProblemIDE(IDEContextMixin, ProblemDetail):
    """/problem/<code>/ide -- statement, editor and console on one screen.

    Everything about access is inherited from ProblemDetail, including the
    redirect that keeps a strict-contest participant inside the proctored
    contest IDE: this page must never become a way out of it.
    """

    def dispatch(self, request, *args, **kwargs):
        # Legacy Open IDE links carry their lesson/contest in `from`. Route to
        # the scoped view so its membership, prerequisite and contest checks
        # remain the single source of truth, including for forged `from` URLs.
        back = request.GET.get("from")
        if (
            request.method == "GET"
            and back
            and url_has_allowed_host_and_scheme(
                back,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            )
        ):
            try:
                source = resolve(urlsplit(back).path)
            except (Resolver404, ValueError):
                source = None
            routes = {
                "course_lesson_problem_detail": "course_lesson_problem_ide",
                "contest_problem_detail": "contest_ide",
            }
            if (
                source
                and source.url_name in routes
                and source.kwargs.get("problem") == kwargs.get("problem")
            ):
                return redirect(reverse(routes[source.url_name], kwargs=source.kwargs))
        return super().dispatch(request, *args, **kwargs)

    def get_back_url(self):
        back = self.request.GET.get("from")
        if back and url_has_allowed_host_and_scheme(
            back,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return back
        return reverse("problem_detail", args=[self.object.code])


class CodeRunForm(forms.Form):
    language = forms.ModelChoiceField(queryset=Language.objects.none())
    source = forms.CharField(max_length=65536, strip=False)
    # JSON: [{"input": "...", "expected": "..." or null}, ...]
    cases = forms.CharField(strip=False)
    io_mode = forms.ChoiceField(
        choices=(("std", "std"), ("file", "file")), required=False
    )
    fileio_input = forms.CharField(required=False, max_length=64)
    fileio_output = forms.CharField(required=False, max_length=64)

    def __init__(self, *args, problem, **kwargs):
        super().__init__(*args, **kwargs)
        self.problem = problem
        self.fields["language"].queryset = problem.allowed_languages.filter(
            file_only=False
        )

    def clean_source(self):
        source = self.cleaned_data["source"]
        if not source.strip():
            raise forms.ValidationError(_("Your source code is empty."))
        return source

    def clean_cases(self):
        try:
            raw = json.loads(self.cleaned_data["cases"])
        except ValueError:
            raise forms.ValidationError(_("Malformed test cases."))
        if not isinstance(raw, list) or not raw:
            raise forms.ValidationError(_("Add at least one test case."))
        if len(raw) > max_cases():
            raise forms.ValidationError(
                _("At most %(count)d test cases can be run at once.")
                % {"count": max_cases()}
            )
        cases = []
        for item in raw:
            if not isinstance(item, dict):
                raise forms.ValidationError(_("Malformed test cases."))
            text = item.get("input")
            expected = item.get("expected")
            if not isinstance(text, str) or not (
                expected is None or isinstance(expected, str)
            ):
                raise forms.ValidationError(_("Malformed test cases."))
            # An empty expected output means "just run it"; there is no
            # checking against nothing.
            if expected is not None and not expected.strip():
                expected = None
            cases.append({"input": text, "expected": expected})
        total_input = sum(len(c["input"]) for c in cases)
        total_expected = sum(len(c["expected"] or "") for c in cases)
        if max(total_input, total_expected) > max_input_length():
            raise forms.ValidationError(
                _("The test input is too long (at most %(count)d characters).")
                % {"count": max_input_length()}
            )
        return cases

    def clean(self):
        data = super().clean()
        if data.get("io_mode") == "file":
            for field in ("fileio_input", "fileio_output"):
                name = (data.get(field) or "").strip()
                if name and not is_valid_fileio_name(name):
                    self.add_error(
                        field,
                        _("Use a plain file name: letters, digits, '.', '_' or '-'."),
                    )
                data[field] = name
            if not data.get("fileio_input") and not data.get("fileio_output"):
                self.add_error(
                    "fileio_input", _("Give an input or an output file name.")
                )
        else:
            data["fileio_input"] = data["fileio_output"] = ""
        return data


def _json_error(status, message, errors=None):
    payload = {"ok": False, "error": str(message)}
    if errors:
        payload["errors"] = errors
    return JsonResponse(payload, status=status)


def _form_errors(form):
    return {field: [str(e) for e in errs] for field, errs in form.errors.items()}


def _limits_for(problem, language):
    try:
        time_limit, memory_limit = (
            LanguageLimit.objects.filter(problem=problem, language=language)
            .values_list("time_limit", "memory_limit")
            .get()
        )
    except ObjectDoesNotExist:
        time_limit, memory_limit = problem.time_limit, problem.memory_limit
    return (
        min(float(time_limit), max_time_limit()),
        min(int(memory_limit), max_memory_limit()),
    )


def _strict_denial(profile):
    participation = profile.current_contest
    if is_strict_live(participation):
        ok, reason = strict_session_ok(participation)
        if not ok:
            return reason
    return None


RUN_VERDICT_TEXT = {
    "OK": gettext_lazy("Finished"),
    "AC": gettext_lazy("Accepted"),
    "WA": gettext_lazy("Wrong Answer"),
    "TLE": gettext_lazy("Time Limit Exceeded"),
    "MLE": gettext_lazy("Memory Limit Exceeded"),
    "OLE": gettext_lazy("Output Limit Exceeded"),
    "IR": gettext_lazy("Invalid Return"),
    "RTE": gettext_lazy("Runtime Error"),
    "CE": gettext_lazy("Compile Error"),
    "IE": gettext_lazy("Internal Error"),
    "AB": gettext_lazy("Aborted"),
}


def case_verdict(result, has_expected):
    if result in ("AC", "WA"):
        # Without an expected output there is nothing to be right or wrong
        # about; the program simply ran to completion.
        return result if has_expected else "OK"
    return result


def run_verdict(run, case_verdicts):
    """One word for the whole run: the first thing that went wrong, else
    AC when every case was checked, else plain OK."""
    if run.status in ("CE", "IE", "AB"):
        return run.status
    if run.status != "D":
        return None
    for verdict in case_verdicts:
        if verdict not in ("AC", "OK"):
            return verdict
    if case_verdicts and all(v == "AC" for v in case_verdicts):
        return "AC"
    return "OK"


def _verdict_text(verdict):
    return str(RUN_VERDICT_TEXT.get(verdict, "")) if verdict else ""


def run_payload(run):
    results = {r.get("position"): r for r in (run.results or [])}
    limit = output_prefix_length()
    cases = []
    for i, case in enumerate(run.cases or [], 1):
        has_expected = case.get("expected") is not None
        result = results.get(i)
        entry = {
            "position": i,
            "input": case.get("input") or "",
            "expected": case.get("expected"),
            "done": result is not None,
            "verdict": None,
            "verdict_text": "",
        }
        if result is not None:
            verdict = case_verdict(result.get("result"), has_expected)
            output = result.get("output") or ""
            entry.update(
                {
                    "verdict": verdict,
                    "verdict_text": _verdict_text(verdict),
                    "time": result.get("time"),
                    "memory": result.get("memory"),
                    "output": output,
                    "output_truncated": len(output.encode("utf-8")) >= limit,
                    "feedback": result.get("feedback") or "",
                    "extended_feedback": result.get("extended_feedback") or "",
                }
            )
        cases.append(entry)

    verdict = run_verdict(run, [c["verdict"] for c in cases if c["done"]])
    return {
        "ok": True,
        "id": run.id,
        "status": run.status,
        "status_text": str(run.get_status_display()),
        "finished": run.is_finished,
        "verdict": verdict,
        "verdict_text": _verdict_text(verdict),
        "cases": cases,
        "error": strip_ansi(run.error),
        "url": reverse("code_run_status", args=[run.id]),
        "abort_url": reverse("code_run_abort", args=[run.id]),
    }


def _sweep(run):
    # Housekeeping piggybacks on run creation rather than needing a cron:
    # finished runs' directories (cheap: one listdir), and now and then the
    # rows nobody will look at again.
    sweep_stale_run_dirs()
    if run.id % 25:
        return
    CodeRun.objects.filter(
        created__lt=timezone.now() - timedelta(days=run_setting("KEEP_DAYS", 3))
    ).delete()


@login_required
@require_POST
def problem_run(request, problem):
    if not run_enabled():
        return _json_error(403, _("Running code is turned off on this site."))
    problem = get_object_or_404(Problem, code=problem)
    if not problem.is_accessible_by(request.user):
        return _json_error(404, _("No such problem."))

    profile = request.profile
    if (
        not request.user.is_superuser
        and problem.banned_users.filter(id=profile.id).exists()
    ):
        return _json_error(403, _("You are barred from this problem."))
    denial = _strict_denial(profile)
    if denial:
        return _json_error(403, denial)

    form = CodeRunForm(request.POST, problem=problem)
    if not form.is_valid():
        return _json_error(400, _("Please fix the errors below."), _form_errors(form))
    data = form.cleaned_data

    now = timezone.now()
    if not request.user.has_perm("judge.spam_submission"):
        # A run that never finishes (bridge down) must not lock the user out
        # forever, hence the window on the active check.
        active = CodeRun.objects.filter(
            user=profile,
            status__in=CodeRun.IN_PROGRESS_STATUS,
            created__gte=now - timedelta(minutes=2),
        ).exists()
        if active:
            return _json_error(
                429, _("Your previous run has not finished yet. Wait for it first.")
            )
        per_minute = run_setting("PER_MINUTE", 10)
        if (
            CodeRun.objects.filter(
                user=profile, created__gte=now - timedelta(minutes=1)
            ).count()
            >= per_minute
        ):
            return _json_error(429, _("Too many runs. Wait a minute and try again."))

    time_limit, memory_limit = _limits_for(problem, data["language"])
    run = CodeRun.objects.create(
        user=profile,
        problem=problem,
        language=data["language"],
        source=data["source"],
        cases=data["cases"],
        fileio_input=data["fileio_input"],
        fileio_output=data["fileio_output"],
        time_limit=time_limit,
        memory_limit=memory_limit,
    )
    try:
        prepare_run_dir(run)
    except OSError:
        CodeRun.objects.filter(id=run.id).update(
            status="IE", error="Could not prepare the run.", finished=now
        )
        return _json_error(
            503, _("The judging server is unavailable. Try again later.")
        )

    if not judge_run(run):
        remove_run_dir(run)
        return _json_error(
            503, _("The judging server is unavailable. Try again later.")
        )

    _sweep(run)
    run.refresh_from_db()
    return JsonResponse(run_payload(run), status=201)


def _own_run(request, run_id):
    # Someone else's run is indistinguishable from a missing one.
    return get_object_or_404(CodeRun, id=run_id, user=request.profile)


@login_required
@require_GET
def code_run_status(request, run_id):
    request.no_profile_update = True
    return JsonResponse(run_payload(_own_run(request, run_id)))


@login_required
@require_POST
def code_run_abort(request, run_id):
    run = _own_run(request, run_id)
    if not run.is_finished and abort_run(run):
        run.refresh_from_db()
    return JsonResponse(run_payload(run))


# ---------------------------------------------------------------- submissions


def submission_payload(submission, user):
    """What the problem page and the IDE may know about one submission while
    it is graded: the same facts the submission row shows, under the same
    hidden-result policy -- a hidden verdict is never sent, rather than sent
    and hidden in the browser."""
    mark_hidden_result_submissions([submission], user)
    hidden = getattr(submission, "_is_result_hidden", False)
    graded = submission.status not in Submission.IN_PROGRESS_GRADING_STATUS
    payload = {
        "ok": True,
        "id": submission.id,
        "status": submission.status,
        "status_text": str(submission.get_status_display()),
        "graded": graded,
        "result_hidden": hidden,
        "result": None,
        "result_text": "",
        "result_class": "",
        "points": None,
        "total": None,
        "time": None,
        "memory": None,
        "current_testcase": submission.current_testcase,
        "error": "",
        "problem": {
            "code": submission.problem.code,
            "name": submission.problem.translated_name(get_language()),
        },
        "language": submission.language.name,
        "url": reverse("submission_status", args=[submission.id]),
        "json_url": reverse("submission_json", args=[submission.id]),
        "channel": "sub_%s" % submission.id_secret,
    }
    if graded and not hidden:
        payload.update(
            {
                "result": submission.result,
                "result_text": str(
                    Submission.USER_DISPLAY_CODES.get(submission.result, "")
                    or submission.get_result_display()
                    or ""
                ),
                "time": submission.time,
                "memory": submission.memory,
            }
        )
        # Test-case points, exactly as the submission row shows them.
        if submission.case_total:
            payload["points"] = submission.case_points
            payload["total"] = submission.case_total
        payload["result_class"] = submission.result_class
        if submission.status == "CE":
            payload["error"] = strip_ansi(submission.error)
    elif graded and submission.status == "CE":
        # A compile error says nothing about the hidden tests, and without the
        # log the contestant cannot fix their code.
        payload["error"] = strip_ansi(submission.error)
    return payload


@login_required
@require_GET
def submission_json(request, submission):
    request.no_profile_update = True
    submission = get_object_or_404(
        Submission.objects.select_related("problem", "language", "user"),
        id=int(submission),
    )
    if not submission.is_accessible_by(request.profile):
        raise Http404()
    return JsonResponse(submission_payload(submission, request.user))
