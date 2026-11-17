from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("judge", "0282_standardization_policy")]

    operations = [
        migrations.AddField(
            model_name="contest",
            name="strict_allow_copy",
            field=models.BooleanField(
                default=True,
                help_text="Allow Ctrl+C or Cmd+C during a strict contest.",
                verbose_name="Allow copying in strict mode",
            ),
        ),
        migrations.AddField(
            model_name="contest",
            name="strict_allow_paste",
            field=models.BooleanField(
                default=False,
                help_text="Allow Ctrl+V or Cmd+V, including text copied from outside the contest.",
                verbose_name="Allow pasting in strict mode",
            ),
        ),
    ]
