from django.db import migrations, models


class Migration(migrations.Migration):
    """CourseLesson.hide_testcases (A04, #96). Default False: nothing changes
    until a teacher switches it on for a lesson."""

    dependencies = [
        ("judge", "0275_organization_is_visible"),
    ]

    operations = [
        migrations.AddField(
            model_name="courselesson",
            name="hide_testcases",
            field=models.BooleanField(
                default=False,
                help_text="Students cannot see test input, expected output or checker feedback for this lesson's problems, on any submission of them. Verdicts, time, memory and points stay visible. Teachers, assistants and problem editors still see everything.",
                verbose_name="hide test data",
            ),
        ),
    ]
