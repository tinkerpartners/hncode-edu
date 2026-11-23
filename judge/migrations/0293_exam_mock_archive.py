import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("judge", "0292_exam_library")]

    operations = [
        migrations.CreateModel(
            name="ExamMockArchive",
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
                    "contest",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="library_mock_archive",
                        to="judge.contest",
                    ),
                ),
                (
                    "paper",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mock_archives",
                        to="judge.exampaper",
                    ),
                ),
            ],
        ),
    ]
