from collections import defaultdict
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, Q, Window, prefetch_related_objects
from django.db.models.functions import RowNumber
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from judge.models import (
    BestQuizAttempt,
    Contest,
    CourseContest,
    ExamMockSession,
    ExamPaper,
    ExamPaperMark,
    ExamSeries,
    ProblemData,
    Submission,
)
from judge.utils.exam_library import (
    LibraryAccess,
    accessible_paper,
    active_mock_sessions,
    mock_configuration_matches,
    mock_entry_available,
    mock_publication_error,
    mock_user_is_banned,
    paper_queryset,
    start_mock,
)
from judge.utils.hidden_results import exclude_hidden_result_submissions
from judge.views.comment.mixins import CommentableMixin
from judge.views.course import bulk_max_case_points_per_problem

FILTERS = (
    ("category", "Loại kỳ thi"),
    ("region", "Địa bàn"),
    ("level", "Cấp học"),
    ("year", "Năm"),
    ("round", "Vòng thi"),
    ("division", "Bảng thi"),
)
TABS = {
    "all": "Tất cả",
    "saved": "Đã lưu",
    "ongoing": "Đang luyện",
    "complete": "Hoàn thành",
    "revisit": "Cần làm lại",
}


def available_papers(access, series=None, *, include_items=True):
    queryset = paper_queryset(include_items=include_items)
    if series:
        queryset = queryset.filter(series=series)
    if not access.editor:
        queryset = queryset.filter(is_published=True, series__is_published=True)
    rows = []
    for paper in queryset:
        allowed = access.allows_source(paper)
        if access.listed(paper, allowed):
            paper.source_allowed = allowed
            paper.items = (
                access.visible_items(paper) if allowed and include_items else []
            )
            rows.append(paper)
    return rows


def prepare_progress(request, papers, *, include_problem_details=False):
    profile = request.profile
    problems = {p.pk: p for paper in papers for _, _, p, _, _, _ in paper.items if p}
    file_io = {}
    if include_problem_details:
        file_io = {
            pk: (input_name, output_name)
            for pk, input_name, output_name in ProblemData.objects.filter(
                problem_id__in=problems
            ).values_list(
                "problem_id",
                "fileio_input",
                "fileio_output",
            )
        }
    lesson_quizzes = {
        row.pk
        for paper in papers
        if paper.lesson_id
        for _, _, _, quiz, _, row in paper.items
        if quiz and not row.is_result_hidden
    }
    best = {}
    attempts = {}
    quiz_attempts = {}
    marks = {}
    active_papers = set()
    if profile:
        best = bulk_max_case_points_per_problem(
            [profile.pk], problems, viewer=request.user
        ).get(profile.pk, {})
        attempts = dict(
            exclude_hidden_result_submissions(
                Submission.objects.filter(user=profile, problem_id__in=problems)
                .exclude(
                    status__in=("IE", "AB"),
                )
                .exclude(exam_deferred__ready_at__gt=timezone.now()),
                request.user,
            )
            .values("problem_id")
            .annotate(last=Max("pk"))
            .values_list("problem_id", "last")
        )
        quiz_attempts = {
            row.lesson_quiz_id: row
            for row in BestQuizAttempt.objects.filter(
                user=profile, lesson_quiz_id__in=lesson_quizzes
            )
        }
        marks = {
            mark.paper_id: mark
            for mark in ExamPaperMark.objects.filter(user=profile, paper__in=papers)
        }
        active_papers = set(
            ExamMockSession.objects.filter(
                paper__in=papers,
                participation__user=profile,
                participation__library_deadline__gt=timezone.now(),
                participation__is_disqualified=False,
            ).values_list("paper_id", flat=True)
        )
    for paper in papers:
        mark = marks.get(paper.pk)
        paper.saved = bool(mark and mark.saved)
        paper.revisit = bool(mark and mark.revisit)
        paper.mock_active = paper.source_allowed and paper.pk in active_papers
        paper.mock_available = bool(
            paper.source_allowed and paper.mock_contest_id and paper.mock_minutes
        )
        paper.entries = []
        paper.total_points = 0
        paper.earned = 0
        paper.attempted = paper.completed = 0
        paper.practice_url = ""
        paper.problem_progress = {}
        resume = []
        for _, _, problem, quiz, points, row in paper.items:
            entry = {"points": points, "earned": 0, "state": "Chưa làm", "url": None}
            if problem:
                entry.update(title=problem.name, code=problem.code)
                if include_problem_details:
                    input_name, output_name = file_io.get(problem.pk, (None, None))
                    entry.update(
                        input_name=input_name or "stdin",
                        output_name=output_name or "stdout",
                        time_limit=problem.time_limit,
                        memory_limit=problem.memory_limit,
                    )
                result = best.get(problem.pk, {})
                ratio = (
                    result.get("case_points", 0) / result["case_total"]
                    if result.get("case_total")
                    else 0
                )
                last = attempts.get(problem.pk)
                entry["url"] = (
                    reverse(
                        "course_lesson_problem_detail",
                        args=[paper.lesson.course.slug, paper.lesson_id, problem.code],
                    )
                    if paper.lesson_id
                    else problem.get_absolute_url()
                )
                paper.problem_progress[problem.pk] = ratio >= 1
            else:
                entry.update(title=quiz.title, code="")
                result = quiz_attempts.get(row.pk) if paper.lesson_id else None
                ratio = (
                    float(result.score / result.max_score)
                    if result and result.max_score
                    else 0
                )
                last = result.attempt_id if result else None
                if paper.lesson_id:
                    entry["url"] = reverse(
                        "lesson_quiz_detail",
                        args=[paper.lesson.course.slug, paper.lesson_id, quiz.code],
                    )
                else:
                    entry["url"] = quiz.get_absolute_url()
            if problem and paper.mock_available:
                entry["url"] = f'{entry["url"]}?exam_paper={paper.pk}'
            ratio = max(0, min(1, ratio))
            if ratio >= 1:
                entry["state"] = "Đạt tối đa"
                paper.completed += 1
            elif ratio > 0:
                entry["state"] = "Có điểm một phần"
            elif last:
                entry["state"] = "Đã nộp"
            paper.attempted += bool(last or ratio)
            if entry["url"] and ratio < 1:
                resume.append((last or 0, entry["url"]))
            entry["earned"] = round(ratio * points, 2)
            paper.earned += entry["earned"]
            paper.total_points += points
            paper.entries.append(entry)
        paper.earned = round(paper.earned, 2)
        paper.finished = bool(paper.entries and paper.completed == len(paper.entries))
        paper.percent = (
            round(100 * paper.completed / len(paper.entries)) if paper.entries else 0
        )
        if resume:
            paper.practice_url = max(resume, key=lambda item: item[0])[1]
        elif paper.lesson_id and paper.source_allowed and not paper.mock_available:
            paper.practice_url = paper.lesson.get_absolute_url()
        elif paper.entries:
            paper.practice_url = next(
                (entry["url"] for entry in paper.entries if entry["url"]), ""
            )
    return papers


def prepare_mock_links(request, access, papers):
    candidates = [paper for paper in papers if paper.mock_available]
    prefetch_related_objects(candidates, "mock_contest__contest_problems")
    if not candidates:
        return
    targets = [
        paper.mock_contest_id for paper in candidates if paper.mock_contest.is_in_course
    ]
    accessible_targets = set(
        CourseContest.objects.filter(
            contest_id__in=targets,
            course_id__in=access.course_ids,
        ).values_list("contest_id", flat=True)
    )
    banned = set()
    if request.profile:
        contest_ids = {
            pk
            for paper in candidates
            for pk in (paper.contest_id, paper.mock_contest_id)
            if pk
        }
        banned = set(
            Contest.objects.filter(
                pk__in=contest_ids, banned_users=request.profile
            ).values_list("pk", flat=True)
        )
    for paper in candidates:
        paper.mock_available = (
            (access.editor or not mock_publication_error(paper))
            and paper.contest_id not in banned
            and paper.mock_contest_id not in banned
            and (
                not paper.mock_contest.is_in_course
                or paper.mock_contest_id in accessible_targets
            )
            and mock_configuration_matches(paper)
        )


def item_search(paper, term):
    names = (paper.title, paper.series.title, paper.round_name, paper.year_label)
    if any(term in value.casefold() for value in names):
        return True
    paper.matches = [
        f"{problem.name} ({problem.code})"
        for _, _, problem, _, _, _ in paper.items
        if problem
        and (term in problem.name.casefold() or term in problem.code.casefold())
    ]
    return bool(paper.matches)


def mock_contest_list(request):
    access = LibraryAccess(request)
    now = timezone.now()
    query = request.GET.get("contest", "").strip()[:120]
    tabs = (
        ("active", "current", "future", "past")
        if request.profile
        else ("current", "future", "past")
    )
    tab = request.GET.get("tab", "past")
    if tab not in tabs:
        tab = "past"
    sort_fields = {"name": "title", "-name": "-title", "year": "year", "-year": "-year"}
    order = request.GET.get("order", "")
    if order not in sort_fields:
        order = ""
    candidates = ExamPaper.objects.select_related(
        "series", "lesson__course", "contest", "mock_contest"
    ).filter(
        is_published=True,
        series__is_published=True,
        mock_contest__is_visible=True,
        mock_minutes__gt=0,
    )
    if not request.profile:
        candidates = candidates.filter(mock_contest__is_in_course=False)
    active = (
        active_mock_sessions(request.profile, now).first() if request.profile else None
    )
    active_paper = candidates.filter(pk=active.paper_id).first() if active else None
    active_count = int(bool(active_paper and access.allows_source(active_paper)))
    if tab == "active":
        candidates = (
            candidates.filter(pk=active.paper_id) if active_count else candidates.none()
        )
    elif tab == "current":
        candidates = candidates.filter(
            mock_contest__start_time__lte=now, mock_contest__end_time__gte=now
        )
    elif tab == "future":
        candidates = candidates.filter(mock_contest__start_time__gt=now)
    else:
        candidates = candidates.filter(mock_contest__end_time__lt=now)
    if query:
        candidates = candidates.filter(
            Q(title__icontains=query)
            | Q(series__title__icontains=query)
            | Q(mock_contest__key__icontains=query),
        )
    if order:
        candidates = candidates.order_by(sort_fields[order], "pk")
    paper_ids = [paper.pk for paper in candidates if access.allows_source(paper)]
    page = Paginator(paper_ids, 20).get_page(request.GET.get("page"))
    papers_by_id = {
        paper.pk: paper
        for paper in paper_queryset()
        .filter(pk__in=page.object_list)
        .prefetch_related(
            "mock_contest__tags",
            "mock_contest__course__course",
        )
    }
    page.object_list = [
        papers_by_id[pk] for pk in page.object_list if pk in papers_by_id
    ]
    other_contest = request.in_contest and (
        not active or active.participation_id != request.participation.pk
    )
    for paper in page:
        paper.mock_available = bool(paper.mock_contest_id and paper.mock_minutes)
    prepare_mock_links(request, access, page.object_list)
    for paper in page:
        paper.mock_active = bool(active and paper.pk == active.paper_id)
        paper.preview_contest = (
            active.participation.contest if paper.mock_active else paper.mock_contest
        )
    page.object_list = [paper for paper in page.object_list if paper.preview_contest]
    participant_counts = dict(
        ExamMockSession.objects.filter(
            participation__contest_id__in=[paper.preview_contest.pk for paper in page],
            participation__is_disqualified=False,
        )
        .order_by()
        .values("participation__contest_id")
        .annotate(
            participants=Count("participation__user_id", distinct=True),
        )
        .values_list("participation__contest_id", "participants")
    )
    for paper in page:
        paper.participant_count = participant_counts.get(paper.preview_contest.pk, 0)
    params = {
        key: value for key, value in {"contest": query, "order": order}.items() if value
    }
    return render(
        request,
        "contest/mock-list.html",
        {
            "title": "Kỳ thi thử",
            "page_type": "mock",
            "nav_tab": "contest",
            "now": now,
            "page": page,
            "mock_only": True,
            "contest_query": query,
            "current_tab": tab,
            "active_count": active_count,
            "tab_urls": {key: "?" + urlencode({**params, "tab": key}) for key in tabs},
            "page_query": urlencode({**params, "tab": tab}),
            "selected_order": order,
            "all_sort_options": (
                ("name", "Tên bộ đề (A–Z)"),
                ("-name", "Tên bộ đề (Z–A)"),
                ("-year", "Năm mới nhất"),
                ("year", "Năm cũ nhất"),
            ),
            "other_contest": other_contest,
            "active": active,
        },
    )


def index(request, slug=None):
    access = LibraryAccess(request)
    series = get_object_or_404(ExamSeries, slug=slug) if slug else None
    if series and not access.series_visible(series):
        raise Http404
    selections = {name: request.GET.get(name, "").strip()[:100] for name, _ in FILTERS}
    query = request.GET.get("q", "").strip()[:120]
    tab = request.GET.get("tab", "all")
    if tab not in TABS or not request.profile:
        tab = "all"
    show_cards = (
        not series and not query and not any(selections.values()) and tab == "all"
    )
    include_items = show_cards or bool(query) or tab in ("ongoing", "complete")
    papers = available_papers(access, series, include_items=include_items)
    for paper in papers:
        paper.facets = {
            "category": [paper.series.category],
            "region": [paper.series.region],
            "level": [paper.series.level],
            "year": [str(paper.year)],
            "round": [paper.round_name],
            "division": [division.name for division in paper.divisions.all()],
        }
    options = [
        (
            name,
            label,
            sorted(
                {value for p in papers for value in p.facets[name] if value},
                reverse=name == "year",
            ),
        )
        for name, label in FILTERS
    ]
    filtered = [
        paper
        for paper in papers
        if all(
            not value or value in paper.facets[key] for key, value in selections.items()
        )
        and (not query or item_search(paper, query.casefold()))
    ]
    if tab in ("saved", "revisit"):
        field = "saved" if tab == "saved" else "revisit"
        marked = set(
            ExamPaperMark.objects.filter(
                user=request.profile, **{field: True}
            ).values_list("paper_id", flat=True)
        )
        filtered = [paper for paper in filtered if paper.pk in marked]
    needs_all_progress = show_cards or tab in ("ongoing", "complete")
    if needs_all_progress:
        prepare_progress(request, filtered)
    if tab == "ongoing":
        filtered = [p for p in filtered if p.attempted and not p.finished]
    elif tab == "complete":
        filtered = [p for p in filtered if p.finished]
    cards = []
    if show_cards:
        groups = defaultdict(list)
        for paper in filtered:
            groups[paper.series_id].append(paper)
        for group in groups.values():
            progress = {}
            for paper in group:
                progress.update(paper.problem_progress)
            count = len(progress)
            completed = sum(progress.values())
            cards.append(
                {
                    "series": group[0].series,
                    "papers": len(group),
                    "problems": count,
                    "complete": completed,
                    "percent": round(100 * completed / count) if count else 0,
                }
            )
        cards.sort(key=lambda card: card["series"].title)
    page = Paginator(filtered, 20).get_page(request.GET.get("page"))
    if not include_items:
        prefetch_related_objects(
            page.object_list,
            "lesson__lesson_problems__problem",
            "lesson__lesson_quizzes__quiz",
            "contest__contest_problems__problem",
            "contest__contest_problems__quiz",
        )
        for paper in page:
            paper.items = access.visible_items(paper) if paper.source_allowed else []
    if not needs_all_progress:
        prepare_progress(request, page.object_list)
    if not cards and not request.in_contest:
        prepare_mock_links(request, access, page.object_list)
    params = {key: value for key, value in selections.items() if value}
    if query:
        params["q"] = query
    return render(
        request,
        "exam_library/index.html",
        {
            "title": series.title if series else "Thư viện đề thi",
            "series": series,
            "cards": cards,
            "page": page,
            "options": options,
            "selections": selections,
            "query": query,
            "has_filters": any(selections.values()),
            "tabs": [
                (key, label, urlencode({**params, "tab": key}))
                for key, label in TABS.items()
            ],
            "tab": tab,
            "page_query": urlencode({**params, "tab": tab}),
        },
    )


class PaperComments(CommentableMixin):
    def __init__(self, request, paper):
        self.request = request
        self.object = paper


def paper_detail(request, slug, paper_slug):
    paper = get_object_or_404(paper_queryset(), series__slug=slug, slug=paper_slug)
    access = LibraryAccess(request)
    paper.source_allowed = access.allows_source(paper)
    if not access.listed(paper, paper.source_allowed):
        raise Http404
    paper.items = access.visible_items(paper) if paper.source_allowed else []
    prepare_progress(request, [paper], include_problem_details=True)
    current = getattr(request, "participation", None)
    sessions = []
    if request.profile and paper.source_allowed:
        sessions = list(
            ExamMockSession.objects.filter(
                paper=paper,
                participation__user=request.profile,
            ).select_related("participation__contest")[:10]
        )
    active = next((session for session in sessions if session.active), None)
    submissions = defaultdict(list)
    closed_ids = [
        session.participation_id for session in sessions if not session.active
    ]
    visible_logs = (
        exclude_hidden_result_submissions(
            Submission.objects.filter(
                contest__participation__in=closed_ids,
                user=request.profile,
            ),
            request.user,
        )
        .select_related("problem", "contest")
        .annotate(
            history_row=Window(
                RowNumber(),
                partition_by=[F("contest__participation_id")],
                order_by=F("pk").desc(),
            ),
        )
        .filter(history_row__lte=10)
        .order_by("-pk")
    )
    for submission in visible_logs:
        # Limit the history preview for each session; submission detail owns
        # the verdict and test-case access checks.
        participation_id = submission.contest.participation_id
        submissions[participation_id].append(submission)
    pending = set(
        Submission.objects.filter(
            contest__participation__in=[
                session.participation_id for session in sessions
            ],
            status__in=Submission.IN_PROGRESS_GRADING_STATUS,
        ).values_list("contest__participation_id", flat=True)
    )
    for session in sessions:
        session.pending = session.participation_id in pending
        session.recent_submissions = (
            submissions[session.participation_id] if not session.active else []
        )
    discussion = paper.source_allowed and paper.discussion_enabled and not active
    guide = None
    if paper.source_allowed and not active:
        guide = paper.guide
        if not guide or not guide.is_accessible_by(request.user):
            series_guide = paper.series.guide
            guide = (
                series_guide
                if series_guide and series_guide.is_accessible_by(request.user)
                else None
            )
    source_url = None
    if paper.lesson_id and paper.lesson.course.is_visible_to(request.profile):
        source_url = paper.lesson.course.get_absolute_url()
    elif paper.contest_id and paper.contest.is_accessible_by(request.user):
        source_url = paper.contest.get_absolute_url()
    context = {
        "title": paper.title,
        "paper": paper,
        "sessions": sessions,
        "active": active,
        "discussion": discussion,
        "mock_available": (
            paper.mock_available
            and mock_entry_available(request, paper)
            and mock_configuration_matches(paper)
        ),
        "guide": guide,
        "other_contest": bool(
            current and (not active or current.pk != active.participation_id)
        ),
        "stay_url": paper.get_absolute_url(),
        "source_url": source_url,
    }
    if discussion:
        PaperComments(request, paper).get_comment_context(context)
    return render(request, "exam_library/paper.html", context)


@login_required
@require_POST
@transaction.atomic
def mark(request, pk):
    paper = paper_queryset().filter(pk=pk).first()
    if paper is None or not LibraryAccess(request).listed(paper):
        raise Http404
    field = request.POST.get("field")
    if field not in ("saved", "revisit") or request.POST.get("value") not in ("0", "1"):
        return HttpResponseBadRequest()
    ExamPaperMark.objects.update_or_create(
        user=request.profile,
        paper=paper,
        defaults={field: request.POST["value"] == "1"},
    )
    next_url = request.POST.get("next", "")
    if next_url.startswith(reverse("exam_library")) and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(paper)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def mock_start(request, pk):
    paper = accessible_paper(request, pk)
    if paper is None:
        raise Http404
    if request.method == "GET":
        active = active_mock_sessions(request.profile, timezone.now()).first()
        current = request.participation
        message = None
        if active and active.paper_id != paper.pk:
            message = "Bạn đang thi thử một bộ đề khác."
        elif current and (not active or active.participation_id != current.pk):
            message = "Bạn đang tham gia một kỳ thi khác."
        elif active and (
            not active.participation.contest.is_accessible_by(request.user)
            or mock_user_is_banned(request.profile, paper, active.participation.contest)
        ):
            message = "Bạn không còn quyền vào lượt thi thử hiện tại."
        elif not active and not (
            mock_entry_available(request, paper) and mock_configuration_matches(paper)
        ):
            message = "Hiện chưa thể thi thử bộ đề này."
        fragment = request.headers.get("X-Exam-Confirmation") == "1"
        active_for_paper = active if active and active.paper_id == paper.pk else None
        contest = (
            active_for_paper.participation.contest
            if active_for_paper
            else paper.mock_contest
        )
        response = render(
            request,
            (
                "exam_library/mock-confirm-content.html"
                if fragment
                else "exam_library/mock-confirm.html"
            ),
            {
                "title": "Xác nhận thi thử",
                "paper": paper,
                "active": active if not message else None,
                "message": message,
                "fragment": fragment,
                "contest_url": (
                    reverse("contest_view", args=[contest.key])
                    if contest and contest.is_accessible_by(request.user)
                    else ""
                ),
            },
            status=409 if message else 200,
        )
        if fragment:
            response["X-Exam-Confirmation"] = "1"
        return response
    try:
        session = start_mock(request, paper)
    except ValidationError as exc:
        return render(
            request,
            "exam_library/message.html",
            {
                "title": "Thi thử",
                "message": exc.messages[0],
                "paper": paper,
            },
            status=409,
        )
    return redirect("contest_problems", contest=session.participation.contest.key)
