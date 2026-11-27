from django.db import migrations

# Featured practice courses of the exam library, grouped by exam and then by
# division, youngest pupils first. Steps of 10 leave room to slot courses in
# from the admin. Courses missing here are skipped, and any order set by hand
# (anything but the default 0) is kept.
ORDER = [
    # Tin học trẻ Toàn quốc, then Hà Nội
    "thd_tinhoctre_tq_a",
    "thd_tinhoctre",
    "thd_tinhoctre_tq_c1",
    "thd_tinhoctre_tq_c2",
    "thd_tinhoctre_tq_d1",
    "thd_tinhoctre_ha_noi",
    "thd_tinhoctre_hn_b",
    "thd_tinhoctre_hn_c",
    "thd_tinhoctre_hn_d1",
    # HSG: THCS Hà Nội, THPT Hà Nội, Quốc gia
    "thd_hsghn_thcs",
    "thd_hsghn_thpt_banga",
    "thd_hsghn_thpt_bangb",
    "thd_hsgqg_v1",
    # Olympic: Miền Trung và Tây Nguyên, then Sinh viên
    "thd_olp_mttn_kc",
    "thd_olp_mttn_ct",
    "thd_olp_mttn_sc",
    "thd_olympic_sv_cd",
    "thd_olympic_sv_kc",
    "thd_olympic_sv_ct",
    "thd_olympic_sv",
]


def set_order(apps, schema_editor):
    Course = apps.get_model("judge", "Course")
    for index, slug in enumerate(ORDER, 1):
        Course.objects.filter(slug=slug, featured_order=0).update(
            featured_order=index * 10
        )


class Migration(migrations.Migration):
    dependencies = [
        ("judge", "0299_course_tags"),
    ]

    operations = [
        migrations.RunPython(set_order, migrations.RunPython.noop),
    ]
