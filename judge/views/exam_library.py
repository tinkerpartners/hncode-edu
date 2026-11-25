import re
import unicodedata
from collections import Counter, defaultdict
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, Q, Window, prefetch_related_objects
from django.db.models.functions import RowNumber
from django.http import Http404, HttpResponseBadRequest
from django.templatetags.static import static
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext, gettext_lazy, ngettext
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from judge.models import (
    BestQuizAttempt,
    Contest,
    CourseContest,
    ExamGroup,
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
    prefetch_items,
    source_items,
    start_mock,
)
from judge.utils.exam_covers import cover_thumbnail_url
from judge.utils.hidden_results import exclude_hidden_result_submissions
from judge.views.comment.mixins import CommentableMixin
from judge.views.course import bulk_max_case_points_per_problem

FILTERS = (
    ("category", gettext_lazy("Exam type")),
    ("region", gettext_lazy("Region")),
    ("level", gettext_lazy("School level")),
    ("year", gettext_lazy("Year")),
    ("round", gettext_lazy("Round")),
    ("division", gettext_lazy("Division")),
)
# Filters that pick exams; the others pick papers inside one exam.
SERIES_FILTERS = ("category", "region", "level")
TABS = {
    "all": gettext_lazy("All"),
    "saved": gettext_lazy("Saved"),
    "ongoing": gettext_lazy("In practice"),
    "complete": gettext_lazy("Completed papers"),
    "revisit": gettext_lazy("To revisit"),
}


def prime_paper_access(access, papers):
    access.prime_problem_access(
        problem
        for paper in papers
        for _, _, problem, _, _, _ in source_items(paper)
        if problem
    )


def available_papers(access, series=None, *, include_items=True):
    queryset = paper_queryset(include_items=False)
    if series:
        queryset = queryset.filter(series=series)
    if not access.editor:
        queryset = queryset.filter(is_published=True, series__is_published=True)
    rows = []
    for paper in queryset:
        allowed = access.allows_source(paper)
        if access.listed(paper, allowed):
            paper.source_allowed = allowed
            rows.append(paper)
    if include_items:
        sources = [paper for paper in rows if paper.source_allowed]
        prefetch_items(sources)
        prime_paper_access(access, sources)
    for paper in rows:
        paper.items = (
            access.visible_items(paper)
            if paper.source_allowed and include_items
            else []
        )
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
            entry = {
                "points": points,
                "earned": 0,
                "complete": False,
                "state": gettext("Not attempted"),
                "url": None,
            }
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
                entry["complete"] = True
                entry["state"] = gettext("Full score")
                paper.completed += 1
            elif ratio > 0:
                entry["state"] = gettext("Partial score")
            elif last:
                entry["state"] = gettext("Submitted")
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


def fold(text):
    """Lower-case, accent-free form used to compare search terms and facets."""
    text = unicodedata.normalize(
        "NFD", str(text or "").replace("đ", "d").replace("Đ", "D")
    )
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


def words(text):
    """Accent-free words of a text, without punctuation."""
    return re.findall(r"[^\W_]+", fold(text))


def term_matches(term, text_words):
    """Short terms such as "A" or "C1" must be whole words.

    Otherwise "Bảng A" would match every paper that has an "a" in its name.
    """
    if len(term) == 1 or (len(term) == 2 and not term.isdigit()):
        return term in text_words
    return any(term in word for word in text_words)


def paper_page_title(paper):
    """Paper title with the exam name, unless the title already starts that name.

    Titles such as "2024" mean little in a browser tab or a shared link.
    """
    name = " ".join(fold(paper.series.title).split()[:2])
    if name in fold(paper.title):
        return paper.title
    return f"{paper.series.title} · {paper.title}"


def paper_search_text(paper):
    values = [
        paper.title,
        paper.series.title,
        paper.series.category,
        paper.series.region,
        paper.series.level,
        paper.round_name,
        paper.year_label,
        str(paper.year),
        *(division.name for division in paper.divisions.all()),
    ]
    return words(" ".join(value for value in values if value))


def item_search(paper, terms):
    """Each term must match the paper's metadata or one of its visible problems."""
    metadata = paper_search_text(paper)
    remaining = [term for term in terms if not term_matches(term, metadata)]
    paper.matches = []
    if not remaining:
        return True
    for _, _, problem, _, _, _ in paper.items:
        if not problem:
            continue
        text = words(f"{problem.name} {problem.code}")
        if all(term_matches(term, text) for term in remaining):
            paper.matches.append(f"{problem.name} ({problem.code})")
    return bool(paper.matches)


def facet_options(papers, selections):
    """Options for each filter, limited by the other active filters.

    Values that differ only by case or accents share one option, shown with
    their most common spelling.
    """
    selected = {key: fold(value) for key, value in selections.items() if value}
    options = []
    for name, label in FILTERS:
        spellings = defaultdict(Counter)
        for paper in papers:
            if all(
                key == name or value in paper.facet_keys[key]
                for key, value in selected.items()
            ):
                for value in paper.facets[name]:
                    if value:
                        spellings[fold(value)][value] += 1
        if name in selected and selected[name] not in spellings:
            spellings[selected[name]][selections[name]] += 1
        values = [
            (key, counter.most_common(1)[0][0]) for key, counter in spellings.items()
        ]
        if name == "year":
            values.sort(key=lambda value: value[1], reverse=True)
        else:
            values.sort(key=lambda value: value[0])
        options.append(
            (
                name,
                label,
                [display for _, display in values],
                {key: display for key, display in values}.get(selected.get(name)),
            )
        )
    return options


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
            "title": gettext("Mock exams"),
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
                ("name", gettext("Paper name (A–Z)")),
                ("-name", gettext("Paper name (Z–A)")),
                ("-year", gettext("Newest year")),
                ("year", gettext("Oldest year")),
            ),
            "other_contest": other_contest,
            "active": active,
        },
    )


def paper_totals(papers):
    """Distinct problems of several papers and how many have a full score."""
    progress = {}
    for paper in papers:
        progress.update(paper.problem_progress)
    count = len(progress)
    completed = sum(progress.values())
    return {
        "papers": len(papers),
        "problems": count,
        "complete": completed,
        "percent": round(100 * completed / count) if count else 0,
    }


def group_papers(group, papers):
    wanted = {division.pk for division in group.divisions.all()}
    return [
        paper
        for paper in papers
        if any(division.pk in wanted for division in paper.divisions.all())
    ]


def matching_sections(papers, terms):
    """Exams, or sections of an exam, named by a search in the whole library."""
    series_rows = {paper.series_id: paper.series for paper in papers}
    groups = defaultdict(list)
    for group in ExamGroup.objects.filter(series__in=series_rows):
        group.series = series_rows[group.series_id]
        groups[group.series_id].append(group)
    found = []
    for series in sorted(series_rows.values(), key=lambda row: row.title):
        name = words(f"{series.title} {series.category} {series.region} {series.level}")
        if all(term_matches(term, name) for term in terms):
            found.append((series.title, series.get_absolute_url()))
            continue
        for group in groups[series.pk]:
            group_name = words(group.title)
            if all(
                term_matches(term, name) or term_matches(term, group_name)
                for term in terms
            ):
                found.append(
                    (f"{series.title} · {group.title}", group.get_absolute_url())
                )
    return found


def index(request, slug=None, group_slug=None):
    access = LibraryAccess(request)
    series = get_object_or_404(ExamSeries, slug=slug) if slug else None
    if series and not access.series_visible(series):
        raise Http404
    groups = (
        list(series.groups.prefetch_related("divisions").order_by("order", "pk"))
        if series
        else []
    )
    for item in groups:
        item.series = series
    group = None
    if group_slug:
        group = next((item for item in groups if item.slug == group_slug), None)
        if group is None:
            raise Http404
    selections = {name: request.GET.get(name, "").strip()[:100] for name, _ in FILTERS}
    query = request.GET.get("q", "").strip()[:120]
    tab = request.GET.get("tab", "all")
    if tab not in TABS or not request.profile:
        tab = "all"
    show_groups = bool(groups and not group)
    if show_groups:
        # Papers of a divided exam are only listed inside its sections.
        query, tab = "", "all"
        selections = dict.fromkeys(selections, "")
    terms = words(query)
    show_cards = (
        not series
        and not query
        and tab == "all"
        and not any(
            value for name, value in selections.items() if name not in SERIES_FILTERS
        )
    )
    include_items = (
        show_cards or show_groups or bool(query) or tab in ("ongoing", "complete")
    )
    papers = available_papers(access, series, include_items=include_items)
    if group:
        papers = group_papers(group, papers)
        group_divisions = {division.name for division in group.divisions.all()}
    for paper in papers:
        paper.link = paper.get_absolute_url()
        if group and sum(bool(group_papers(item, [paper])) for item in groups) > 1:
            # Keeps the way back for papers shared by several groups.
            paper.link += "?" + urlencode({"group": group.slug})
        paper.facets = {
            "category": [paper.series.category],
            "region": [paper.series.region],
            "level": [paper.series.level],
            "year": [str(paper.year)],
            "round": [paper.round_name],
            "division": [
                division.name
                for division in paper.divisions.all()
                if not group or division.name in group_divisions
            ],
        }
        paper.facet_keys = {
            name: {fold(value) for value in values if value}
            for name, values in paper.facets.items()
        }
    sections = matching_sections(papers, terms) if terms and not series else []
    options = facet_options(papers, selections)
    # A filter with a single value cannot narrow anything.
    shown_options = [
        option
        for option in options
        if option[3]
        or (len(option[2]) > 1 and (series or option[0] in SERIES_FILTERS))
    ]
    selected = {key: fold(value) for key, value in selections.items() if value}
    filtered = [
        paper
        for paper in papers
        if all(value in paper.facet_keys[key] for key, value in selected.items())
        and (not terms or item_search(paper, terms))
    ]
    if query and not series:
        # Keep results from one exam series together instead of mixing them by year.
        filtered.sort(key=lambda paper: (paper.series.title, paper.series_id))
    if tab in ("saved", "revisit"):
        field = "saved" if tab == "saved" else "revisit"
        marked = set(
            ExamPaperMark.objects.filter(
                user=request.profile, **{field: True}
            ).values_list("paper_id", flat=True)
        )
        filtered = [paper for paper in filtered if paper.pk in marked]
    needs_all_progress = show_cards or show_groups or tab in ("ongoing", "complete")
    if needs_all_progress:
        prepare_progress(request, filtered)
    if tab == "ongoing":
        filtered = [p for p in filtered if p.attempted and not p.finished]
    elif tab == "complete":
        filtered = [p for p in filtered if p.finished]
    cards = []
    if show_cards:
        by_series = defaultdict(list)
        for paper in filtered:
            by_series[paper.series_id].append(paper)
        for rows in by_series.values():
            cards.append(
                {
                    "series": rows[0].series,
                    "cover": cover_thumbnail_url(rows[0].series.cover_url),
                    **paper_totals(rows),
                }
            )
        cards.sort(key=lambda card: card["series"].title)
    group_cards = []
    if show_groups:
        for item in groups:
            group_cards.append(
                {"group": item, **paper_totals(group_papers(item, filtered))}
            )
    page = Paginator(filtered, 20).get_page(request.GET.get("page"))
    if not include_items:
        sources = [paper for paper in page if paper.source_allowed]
        prefetch_items(sources)
        prime_paper_access(access, sources)
        for paper in page:
            paper.items = access.visible_items(paper) if paper.source_allowed else []
    if not needs_all_progress:
        prepare_progress(request, page.object_list)
    if not cards and not show_groups and not request.in_contest:
        prepare_mock_links(request, access, page.object_list)
    params = {key: value for key, value in selections.items() if value}
    if query:
        params["q"] = query
    page_title = series.title if series else gettext("Exam library")
    if group:
        page_title = f"{series.title} · {group.title}"
    return render(
        request,
        "exam_library/index.html",
        {
            "title": page_title,
            "meta_description": (
                series.summary
                if series and series.summary and not group
                else (
                    gettext(
                        "Papers of %(name)s by year, with practice and mock exams on %(site)s."
                    )
                    % {"name": page_title, "site": settings.SITE_NAME}
                    if series
                    else gettext(
                        "Library of informatics olympiad and contest papers: practice by paper and take timed mock exams."
                    )
                )
            ),
            "og_image": (
                series.cover_url
                if series
                else next(
                    (
                        card["series"].cover_url
                        for card in cards
                        if card["series"].is_published and card["series"].cover_url
                    ),
                    None,
                )
            )
            or static("icons/android-chrome-512x512.png"),
            "series": series,
            "group": group,
            "groups": groups,
            "group_cards": group_cards,
            "ungrouped": (
                sum(
                    not any(group_papers(item, [paper]) for item in groups)
                    for paper in papers
                )
                if show_groups and access.editor
                else 0
            ),
            "sections": sections,
            "has_papers": bool(papers),
            "result_count": len(filtered),
            "by_year": bool(group and len(papers) > 1),
            "base_url": (group or series).get_absolute_url() if series else None,
            "cards": cards,
            "page": page,
            "options": options,
            "shown_options": shown_options,
            "selections": selections,
            "query": query,
            "has_filters": bool(query or any(selections.values())),
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
    page_title = paper_page_title(paper)
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
    # Back to the section the reader came from, or the only one listing this paper.
    back_groups = [
        item
        for item in paper.series.groups.prefetch_related("divisions")
        if group_papers(item, [paper])
    ]
    back_group = next(
        (item for item in back_groups if item.slug == request.GET.get("group")),
        back_groups[0] if len(back_groups) == 1 else None,
    )
    facts = [paper.series.title]
    if paper.duration_minutes:
        facts.append(
            gettext("duration %(count)s minutes") % {"count": paper.duration_minutes}
        )
    if paper.source_allowed and paper.entries:
        count = len(paper.entries)
        facts.append(
            ngettext("%(count)s problem", "%(count)s problems", count)
            % {"count": count}
        )
    context = {
        "title": page_title,
        "meta_description": gettext(
            "Paper %(name)s (%(facts)s): practice and mock exams on %(site)s."
        )
        % {"name": page_title, "facts": ", ".join(facts), "site": settings.SITE_NAME},
        "og_image": paper.series.cover_url or None,
        "paper": paper,
        "back_group": back_group,
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
            message = gettext("You are taking a mock exam for another paper.")
        elif current and (not active or active.participation_id != current.pk):
            message = gettext("You are taking part in another contest.")
        elif active and (
            not active.participation.contest.is_accessible_by(request.user)
            or mock_user_is_banned(request.profile, paper, active.participation.contest)
        ):
            message = gettext("You no longer have access to the current mock exam.")
        elif not active and not (
            mock_entry_available(request, paper) and mock_configuration_matches(paper)
        ):
            message = gettext("A mock exam is not available for this paper right now.")
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
                "title": gettext("Confirm mock exam"),
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
                "title": gettext("Mock exam"),
                "message": exc.messages[0],
                "paper": paper,
            },
            status=409,
        )
    return redirect("contest_problems", contest=session.participation.contest.key)
