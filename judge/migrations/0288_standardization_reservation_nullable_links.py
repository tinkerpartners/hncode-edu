from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("judge", "0287_reservation_resolution"),
    ]

    operations = [
        migrations.AlterField(
            model_name="standardizationreservation",
            name="problem",
            field=models.ForeignKey(
                null=True, on_delete=models.SET_NULL, to="judge.problem"
            ),
        ),
        migrations.AlterField(
            model_name="standardizationreservation",
            name="batch",
            field=models.ForeignKey(
                null=True,
                on_delete=models.SET_NULL,
                to="judge.standardizationbatch",
            ),
        ),
    ]
