from django.db import migrations

# HSG exams: one submission per problem, results after time is up. Tin học trẻ
# and Olympic keep the field defaults: any number of submissions, results at once.
# Papers are not touched here; "Áp mặc định thi thử" in the admin applies these.
ONE_SHOT = (
    "hsg-tin-hoc-thcs-ha-noi",
    "hsg-tin-hoc-thpt-ha-noi",
    "hsg-tin-hoc-quoc-gia",
)


def set_defaults(apps, schema_editor):
    ExamSeries = apps.get_model("judge", "ExamSeries")
    ExamSeries.objects.filter(
        slug__in=ONE_SHOT, mock_limit_default=0, mock_delayed_default=False
    ).update(mock_limit_default=1, mock_delayed_default=True)


class Migration(migrations.Migration):
    dependencies = [
        ("judge", "0301_exam_series_mock_defaults"),
    ]

    operations = [
        migrations.RunPython(set_defaults, migrations.RunPython.noop),
    ]
