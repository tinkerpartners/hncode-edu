from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("judge", "0278_strict_lock"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="code_theme",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Default (follow light/dark)"),
                    ("white", "White"),
                    ("gray", "Light gray"),
                    ("dark", "Dark"),
                ],
                default="",
                max_length=10,
                verbose_name="code background",
            ),
        ),
    ]
