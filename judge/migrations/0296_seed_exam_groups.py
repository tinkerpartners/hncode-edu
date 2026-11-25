from django.db import migrations

A = (
    "Bảng A",
    "bang-a",
    ("Bảng A",),
    "fa-puzzle-piece",
    "blue",
    "Thi kỹ năng lập trình dành cho học sinh Tiểu học.",
)
YOUTH_TAIL = (
    (
        "Bảng D1",
        "bang-d1",
        ("Bảng D1",),
        "fa-lightbulb",
        "orange",
        "Thi sản phẩm sáng tạo theo chủ đề cho trước, dành cho học sinh Tiểu học.",
    ),
    (
        "Bảng M1",
        "bang-m1",
        ("Bảng M1",),
        "fa-calculator",
        "purple",
        "Thi toán tư duy và tư duy thuật toán dành cho học sinh Tiểu học.",
    ),
    (
        "Bảng M2",
        "bang-m2",
        ("Bảng M2",),
        "fa-brain",
        "red",
        "Thi toán tư duy và tư duy thuật toán dành cho học sinh THCS.",
    ),
)
GROUPS = {
    "tin-hoc-tre-ha-noi": (
        A,
        (
            "Bảng B & C",
            "bang-b-c",
            ("Bảng B", "Bảng C"),
            "fa-code",
            "green",
            "Thi kỹ năng lập trình. Bảng B dành cho học sinh THCS, "
            "Bảng C dành cho học sinh THPT.",
        ),
        *YOUTH_TAIL,
    ),
    "tin-hoc-tre-toan-quoc": (
        A,
        (
            "Bảng B & C",
            "bang-b-c",
            ("Bảng B", "Bảng C", "Bảng C1", "Bảng C2"),
            "fa-code",
            "green",
            "Thi kỹ năng lập trình. Bảng B dành cho học sinh THCS, "
            "Bảng C1 cho THPT khối chuyên, Bảng C2 cho THPT không chuyên.",
        ),
        *YOUTH_TAIL,
    ),
    "olympic-tin-hoc-sinh-vien-toan-quoc": (
        (
            "Khối Cao đẳng",
            "cao-dang",
            ("Cao đẳng",),
            "fa-seedling",
            "teal",
            "Dành cho sinh viên các trường cao đẳng.",
        ),
        (
            "Khối Không Chuyên Tin",
            "khong-chuyen-tin",
            ("Không chuyên",),
            "fa-user-graduate",
            "blue",
            "Dành cho sinh viên không học chuyên ngành Công nghệ thông tin.",
        ),
        (
            "Khối Chuyên Tin",
            "chuyen-tin",
            ("Chuyên tin",),
            "fa-laptop-code",
            "purple",
            "Dành cho sinh viên chuyên ngành Công nghệ thông tin.",
        ),
        (
            "Khối Siêu Cup",
            "sieu-cup",
            ("Siêu cúp",),
            "fa-trophy",
            "orange",
            "Dành cho sinh viên đã có giải cao về tin học trong nước, quốc tế "
            "hoặc ở các kỳ Olympic trước.",
        ),
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
        for order, (title, slug, names, icon, color, summary) in enumerate(rows, 1):
            group = groups.create(
                series=series,
                title=title,
                slug=slug,
                summary=summary,
                icon=icon,
                color=color,
                order=order,
            )
            group.divisions.set(
                [divisions.get_or_create(name=name)[0] for name in names]
            )


class Migration(migrations.Migration):
    dependencies = [("judge", "0295_exam_groups")]
    operations = [migrations.RunPython(create_groups, migrations.RunPython.noop)]
