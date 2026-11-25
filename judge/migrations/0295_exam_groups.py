import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("judge", "0294_exam_library_navigation")]

    operations = [
        migrations.CreateModel(
            name="ExamGroup",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "title",
                    models.CharField(max_length=80, verbose_name="Tên nhóm bảng"),
                ),
                ("slug", models.SlugField(max_length=80, verbose_name="Đường dẫn")),
                (
                    "summary",
                    models.CharField(
                        blank=True, max_length=250, verbose_name="Giới thiệu"
                    ),
                ),
                (
                    "icon",
                    models.CharField(
                        choices=[
                            ("fa-puzzle-piece", "Mảnh ghép"),
                            ("fa-code", "Lập trình"),
                            ("fa-lightbulb", "Sáng tạo"),
                            ("fa-calculator", "Toán"),
                            ("fa-brain", "Tư duy"),
                            ("fa-laptop-code", "Máy tính"),
                            ("fa-user-graduate", "Sinh viên"),
                            ("fa-seedling", "Mầm cây"),
                            ("fa-trophy", "Cúp"),
                            ("fa-book-open", "Sách"),
                        ],
                        default="fa-book-open",
                        max_length=30,
                        verbose_name="Biểu tượng",
                    ),
                ),
                (
                    "color",
                    models.CharField(
                        choices=[
                            ("blue", "Xanh dương"),
                            ("green", "Xanh lá"),
                            ("orange", "Cam"),
                            ("purple", "Tím"),
                            ("red", "Đỏ"),
                            ("teal", "Xanh ngọc"),
                        ],
                        default="blue",
                        max_length=10,
                        verbose_name="Màu",
                    ),
                ),
                (
                    "order",
                    models.PositiveSmallIntegerField(default=0, verbose_name="Thứ tự"),
                ),
                (
                    "divisions",
                    models.ManyToManyField(
                        to="judge.examdivision", verbose_name="Gồm các bảng"
                    ),
                ),
                (
                    "series",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="groups",
                        to="judge.examseries",
                    ),
                ),
            ],
            options={
                "verbose_name": "Nhóm bảng",
                "verbose_name_plural": "Nhóm bảng",
                "ordering": ("order", "pk"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("series", "slug"), name="exam_group_series_slug"
                    )
                ],
            },
        ),
    ]
