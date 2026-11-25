from django.db import migrations

YOUTH_TAIL = (
    ("Bảng D1", "bang-d1", ("Bảng D1",)),
    ("Bảng M1", "bang-m1", ("Bảng M1",)),
    ("Bảng M2", "bang-m2", ("Bảng M2",)),
)
GROUPS = {
    "tin-hoc-tre-ha-noi": (
        ("Bảng A", "bang-a", ("Bảng A",)),
        ("Bảng B & C", "bang-b-c", ("Bảng B", "Bảng C")),
        *YOUTH_TAIL,
    ),
    "tin-hoc-tre-toan-quoc": (
        ("Bảng A", "bang-a", ("Bảng A",)),
        ("Bảng B & C", "bang-b-c", ("Bảng B", "Bảng C", "Bảng C1", "Bảng C2")),
        *YOUTH_TAIL,
    ),
    "olympic-tin-hoc-sinh-vien-toan-quoc": (
        ("Khối Cao đẳng", "cao-dang", ("Cao đẳng",)),
        ("Khối Không Chuyên Tin", "khong-chuyen-tin", ("Không chuyên",)),
        ("Khối Chuyên Tin", "chuyen-tin", ("Chuyên tin",)),
        ("Khối Siêu Cup", "sieu-cup", ("Siêu cúp",)),
    ),
}


def create_groups(apps, schema_editor):
    alias = schema_editor.connection.alias
    series_rows = apps.get_model("judge", "ExamSeries").objects.using(alias)
    divisions = apps.get_model("judge", "ExamDivision").objects.using(alias)
    groups = apps.get_model("judge", "ExamGroup").objects.using(alias)
    for series_slug, rows in GROUPS.items():
        series = series_rows.filter(slug=series_slug).first()
        if series is None or groups.filter(series=series).exists():
            continue
        for order, (title, slug, names) in enumerate(rows, 1):
            group = groups.create(series=series, title=title, slug=slug, order=order)
            group.divisions.set(
                [divisions.get_or_create(name=name)[0] for name in names]
            )


class Migration(migrations.Migration):
    dependencies = [("judge", "0295_exam_groups")]
    operations = [migrations.RunPython(create_groups, migrations.RunPython.noop)]
