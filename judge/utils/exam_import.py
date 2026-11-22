import re

from django.core.exceptions import ValidationError
from django.db import transaction

from judge.models import ExamPaper, ExamSeries


@transaction.atomic
def import_sources(series, course, contests, *, apply=False, selected=None):
    """Preview the same validated import that will create the draft records."""
    sources = (
        [(item, "lesson", item.title) for item in course.lessons.order_by("order", "pk")]
        if course else [(item, "contest", item.name) for item in contests]
    )
    if len(sources) > 200:
        raise ValidationError("Một lần nhập hỗ trợ tối đa 200 nguồn.")
    source_ids = {source.pk for source, _, _ in sources}
    if selected is not None and not selected.keys() <= source_ids:
        raise ValidationError("Danh sách nguồn đã thay đổi. Hãy tải lại bản xem trước.")
    if apply:
        ExamSeries.objects.select_for_update().get(pk=series.pk)
    field = "lesson_id" if course else "contest_id"
    existing = set(ExamPaper.objects.filter(series=series, **{field + "__in": source_ids}).values_list(field, flat=True))
    rows = []
    for source, kind, title in sources:
        year_match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", title)
        year = int(year_match[0]) if year_match else None
        slug = f"{kind}-{source.pk}"
        source_label = f"Lesson #{source.pk} · {source.title}" if course else f"Contest · {source.key}"
        row = {"source_id": source.pk, "source": source_label, "title": title[:180], "year": year, "created": False}
        if source.pk in existing:
            row["status"] = "Đã có"
        elif apply and selected is not None and source.pk not in selected:
            row["status"] = "Không chọn"
        elif apply and selected is not None:
            values = selected[source.pk]
            paper, row["created"] = ExamPaper.objects.get_or_create(
                series=series, slug=slug, defaults={
                    "title": values["title"], "year": values["year"], "round_name": values["round_name"],
                    "academic_year": values["academic_year"], kind: source,
                },
            )
            if row["created"] and values["divisions"]:
                paper.divisions.set(values["divisions"])
            row["status"] = "Đã tạo nháp" if row["created"] else "Đã có"
        elif not year:
            row["status"] = "Cần nhập năm"
        else:
            row["status"] = "Sẽ tạo nháp"
            if apply:
                _, row["created"] = ExamPaper.objects.get_or_create(
                    series=series, slug=slug, defaults={
                        "title": row["title"], "year": year, "academic_year": kind == "lesson", kind: source,
                    },
                )
        rows.append(row)
    return rows
