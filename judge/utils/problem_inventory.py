"""Read-only inventory of problem content and judging configuration."""

import hashlib
import re
from collections import defaultdict
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.db.models import Count, Q
from django.urls import reverse

from judge.models import ContestProblem, CourseLessonProblem, Problem, Submission

IMAGE = re.compile(r"!\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^\s)]+)(?:\s+[^)]*)?\)")
FENCE = re.compile(r"^\s*```.*?^\s*```[^\n]*", re.M | re.S)
HEADINGS = {
    "input": re.compile(r"^#{1,6}\s*(?:input|dữ liệu vào|dữ liệu)\s*$", re.I | re.M),
    "output": re.compile(r"^#{1,6}\s*(?:output|kết quả|dữ liệu ra)\s*$", re.I | re.M),
    "example": re.compile(r"^#{1,6}\s*(?:example|sample|ví dụ)\s*$", re.I | re.M),
}

STATE_LABELS = {
    "missing": "Chưa có nguồn đề",
    "placeholder": "Nội dung tạm",
    "asset_review": "Cần đối chiếu ảnh/PDF",
    "format_review": "Cần chuẩn hóa cấu trúc",
    "text_review": "Cần duyệt nội dung Markdown",
    "translation_review": "Đề nằm trong bản dịch",
}
ISSUE_LABELS = {
    "statement_missing": "Thiếu nguồn đề",
    "statement_placeholder": "Đề đang là nội dung tạm",
    "no_judge_configuration": "Thiếu cấu hình chấm",
    "no_test_metadata": "Chưa có metadata test",
    "no_languages": "Chưa chọn ngôn ngữ",
    "no_tags": "Chưa gán dạng bài",
    "no_editorial": "Chưa có hướng dẫn thuật toán",
    "statement_format_v1": "Cần chuẩn hóa heading HNCode v1",
}


def content_inventory(description, pdf_name="", attachments=()):
    text = description or ""
    without_code = FENCE.sub("", text)
    html = BeautifulSoup(without_code, "html.parser")
    images = IMAGE.findall(without_code) + [
        tag.get("src", "") for tag in html.find_all("img")
    ]
    links = LINK.findall(without_code) + [
        tag.get("href", "") for tag in html.find_all("a")
    ]
    pdfs = ([pdf_name] if pdf_name else []) + [
        url for url in links if urlsplit(url).path.lower().endswith(".pdf")
    ]
    for name in attachments:
        suffix = urlsplit(name).path.lower().rsplit(".", 1)[-1]
        if suffix == "pdf":
            pdfs.append(name)
        elif suffix in ("png", "jpg", "jpeg", "webp", "gif", "svg"):
            images.append(name)
    plain = IMAGE.sub("", without_code)
    plain = LINK.sub("", plain)
    plain = BeautifulSoup(plain, "html.parser").get_text(" ", strip=True).strip()
    sources = []
    if plain:
        sources.append("html" if html.find() else "markdown")
    if images:
        sources.append("image")
    if pdfs:
        sources.append("pdf")
    if any(urlsplit(url).scheme in ("https", "http") for url in links):
        sources.append("external_link")
    headings = {
        key: bool(pattern.search(without_code)) for key, pattern in HEADINGS.items()
    }
    placeholder = bool(
        re.fullmatch(
            r"[\s#*]*(?:todo|tbd|chưa có đề|đang cập nhật|coming soon)[\s.!#*]*",
            plain,
            re.I,
        )
    )
    if not sources:
        state = "missing"
    elif placeholder and not (images or pdfs):
        state = "placeholder"
    elif pdfs or images:
        state = "asset_review"
    elif not all(headings.values()):
        state = "format_review"
    else:
        state = "text_review"
    return {
        "sources": sources,
        "state": state,
        "headings": headings,
        "image_count": len(images),
        "pdf_count": len(pdfs),
        "pdf_kind": "uninspected" if pdfs else None,
        "format_v1_headings": all(
            re.search(r"^###\s+" + title + r"\s*$", without_code, re.M)
            for title in ("Input", "Output", "Example")
        ),
    }


def inventory_queryset():
    return (
        Problem.objects.select_related("data_files", "solution")
        .prefetch_related(
            "types",
            "allowed_languages",
            "attachments",
            "translations",
            "language_limits",
        )
        .order_by("pk")
    )


def inventory_rows(problems):
    problems = list(problems)
    ids = [problem.pk for problem in problems]
    statistics = {
        row["problem_id"]: row
        for row in Submission.objects.filter(problem_id__in=ids)
        .values("problem_id")
        .annotate(
            total=Count("id"),
            accepted=Count("id", filter=Q(status="D", result="AC", is_pretested=False)),
        )
    }
    lessons = defaultdict(list)
    for row in (
        CourseLessonProblem.objects.filter(problem_id__in=ids)
        .order_by("id")
        .values("problem_id", "lesson_id", "lesson__title", "lesson__course__slug")
    ):
        lessons[row["problem_id"]].append(
            {
                "title": row["lesson__title"],
                "url": reverse(
                    "course_lesson_detail",
                    args=(row["lesson__course__slug"], row["lesson_id"]),
                ),
            }
        )
    contests = defaultdict(list)
    for row in (
        ContestProblem.objects.filter(problem_id__in=ids)
        .order_by("id")
        .values("problem_id", "contest__key", "contest__name")
    ):
        contests[row["problem_id"]].append(
            {
                "title": row["contest__name"],
                "url": reverse("contest_problems", args=(row["contest__key"],)),
            }
        )
    cases = dict(
        Problem.objects.filter(pk__in=ids)
        .values("pk")
        .annotate(n=Count("cases"))
        .values_list("pk", "n")
    )
    references = dict(
        Problem.objects.filter(pk__in=ids)
        .values("pk")
        .annotate(n=Count("solution_codes"))
        .values_list("pk", "n")
    )
    for problem in problems:
        data = getattr(problem, "data_files", None)
        editorial = getattr(problem, "solution", None)
        attachments = [attachment.file.name for attachment in problem.attachments.all()]
        statement = content_inventory(
            problem.description, problem.pdf_description.name, attachments
        )
        translations = [
            {
                "language": translation.language,
                **content_inventory(translation.description),
            }
            for translation in problem.translations.all()
        ]
        if statement["state"] == "missing" and any(
            t["state"] != "missing" for t in translations
        ):
            statement["state"] = "translation_review"
        issues = []
        if statement["state"] in ("missing", "placeholder"):
            issues.append("statement_" + statement["state"])
        if statement["state"] == "text_review" and not statement["format_v1_headings"]:
            issues.append("statement_format_v1")
        if not data:
            issues.append("no_judge_configuration")
        if (
            not cases[problem.pk]
            and not (data and data.zipfile)
            and not problem.is_manually_managed
        ):
            issues.append("no_test_metadata")
        if not problem.allowed_languages.all():
            issues.append("no_languages")
        if not problem.types.all():
            issues.append("no_tags")
        if not editorial or not editorial.content.strip():
            issues.append("no_editorial")
        row = {
            "id": problem.pk,
            "code": problem.code,
            "name": problem.name,
            "url": reverse("problem_detail", args=(problem.code,)),
            "edit_url": reverse("problem_edit", args=(problem.code,)),
            "statement": statement,
            "description_sha256": hashlib.sha256(
                problem.description.encode("utf-8")
            ).hexdigest(),
            "translations": translations,
            "attachment_count": len(attachments),
            "public": problem.is_public,
            "organization_private": problem.is_organization_private,
            "time_seconds": problem.time_limit,
            "memory_kib": problem.memory_limit,
            "languages": [language.key for language in problem.allowed_languages.all()],
            "language_limit_count": len(problem.language_limits.all()),
            "tags": [tag.name for tag in problem.types.all()],
            "completion_points": problem.points,
            "difficulty_rating": None,
            "manual_data": problem.is_manually_managed,
            "case_metadata_count": cases[problem.pk],
            "has_test_archive": bool(data and data.zipfile),
            "io": (
                {
                    "input": data.fileio_input or None,
                    "output": data.fileio_output or None,
                }
                if data
                else None
            ),
            "checker": data.checker if data else None,
            "output_only": bool(data and data.output_only),
            "function_task": bool(data and data.use_ioi_signature),
            "interactive": bool(
                data and (data.is_communication or data.interactive_judge)
            ),
            "reference_code_count": references[problem.pk],
            "submission_count": statistics.get(problem.pk, {}).get("total", 0),
            "accepted_submission_count": statistics.get(problem.pk, {}).get(
                "accepted", 0
            ),
            "has_editorial": bool(editorial and editorial.content.strip()),
            "lessons": lessons[problem.pk],
            "contests": contests[problem.pk],
            "issues": issues,
        }
        yield row
