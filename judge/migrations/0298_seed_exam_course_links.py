from django.db import migrations

# Practice course of each division (or of the whole exam when it is not divided),
# as set up on hncode.edu.vn. Rows whose exam, division or course does not exist
# are skipped, so the migration is safe on other databases.
GROUP_COURSES = {
    "tin-hoc-tre-toan-quoc": {
        "bang-a": "thd_tinhoctre_tq_a",
        "bang-b": "thd_tinhoctre",
        "bang-c1": "thd_tinhoctre_tq_c1",
        "bang-c2": "thd_tinhoctre_tq_c2",
        "bang-d1": "thd_tinhoctre_tq_d1",
    },
    "tin-hoc-tre-ha-noi": {
        "bang-a": "thd_tinhoctre_ha_noi",
        "bang-b": "thd_tinhoctre_hn_b",
        "bang-c": "thd_tinhoctre_hn_c",
        "bang-d1": "thd_tinhoctre_hn_d1",
    },
    "olympic-tin-hoc-sinh-vien-toan-quoc": {
        "cao-dang": "thd_olympic_sv_cd",
        "khong-chuyen-tin": "thd_olympic_sv_kc",
        "chuyen-tin": "thd_olympic_sv_ct",
        "sieu-cup": "thd_olympic_sv",
    },
    "hsg-tin-hoc-thpt-ha-noi": {
        "bang-a": "thd_hsghn_thpt_banga",
        "bang-b": "thd_hsghn_thpt_bangb",
    },
    "olympic-tin-hoc-mien-trung-tay-nguyen": {
        "khong-chuyen": "thd_olp_mttn_kc",
        "chuyen-tin": "thd_olp_mttn_ct",
        "sieu-cup": "thd_olp_mttn_sc",
    },
}
SERIES_COURSES = {
    "hsg-tin-hoc-thcs-ha-noi": "thd_hsghn_thcs",
    "hsg-tin-hoc-quoc-gia": "thd_hsgqg_v1",
}


def link_courses(apps, schema_editor):
    Course = apps.get_model("judge", "Course")
    ExamGroup = apps.get_model("judge", "ExamGroup")
    ExamSeries = apps.get_model("judge", "ExamSeries")
    slugs = set(SERIES_COURSES.values())
    for groups in GROUP_COURSES.values():
        slugs.update(groups.values())
    courses = dict(Course.objects.filter(slug__in=slugs).values_list("slug", "pk"))

    for series_slug, groups in GROUP_COURSES.items():
        for group_slug, course_slug in groups.items():
            if course_slug in courses:
                ExamGroup.objects.filter(
                    series__slug=series_slug, slug=group_slug, course__isnull=True
                ).update(course_id=courses[course_slug])
    for series_slug, course_slug in SERIES_COURSES.items():
        if course_slug in courses:
            ExamSeries.objects.filter(slug=series_slug, course__isnull=True).update(
                course_id=courses[course_slug]
            )


class Migration(migrations.Migration):
    dependencies = [
        ("judge", "0297_exam_course_links"),
    ]

    operations = [
        migrations.RunPython(link_courses, migrations.RunPython.noop),
    ]
