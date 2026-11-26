"""Tags that the exam library gives to the practice course of each exam or division."""

from django.utils.text import slugify

from judge.models import CourseTag, ExamGroup, ExamSeries


def tag_slug(kind, name):
    # slugify drops letters it cannot strip of accents, and đ has no accent to strip.
    plain = name.replace("đ", "d").replace("Đ", "D")
    return f"{kind}-{slugify(plain)}"[:100] or kind


def get_tag(kind, name, cache):
    key = (kind, name)
    if key not in cache:
        cache[key] = CourseTag.objects.get_or_create(
            kind=kind, name=name, defaults={"slug": tag_slug(kind, name)}
        )[0]
    return cache[key]


def library_tags(series, group=None):
    """(kind, name) pairs for the course of an exam, or of one of its divisions."""
    pairs = [(CourseTag.Kind.EXAM, series.title)]
    if group is not None:
        pairs.append((CourseTag.Kind.GROUP, group.title))
    if series.level:
        pairs.append((CourseTag.Kind.LEVEL, series.level))
    if series.region:
        pairs.append((CourseTag.Kind.REGION, series.region))
    return pairs


def sync_library_course_tags():
    """Give every course linked from the exam library the tags of its exam and division.

    Tags are only added, so tags set by hand stay. Running it again changes nothing.
    Returns how many tags were added to courses.
    """
    cache = {}
    links = [
        (group.course, library_tags(group.series, group))
        for group in ExamGroup.objects.filter(course__isnull=False).select_related(
            "series", "course"
        )
    ] + [
        (series.course, library_tags(series))
        for series in ExamSeries.objects.filter(course__isnull=False).select_related(
            "course"
        )
    ]
    added = 0
    for course, pairs in links:
        wanted = {get_tag(kind, name, cache) for kind, name in pairs}
        missing = wanted - set(course.tags.all())
        if missing:
            course.tags.add(*missing)
            added += len(missing)
    return added
