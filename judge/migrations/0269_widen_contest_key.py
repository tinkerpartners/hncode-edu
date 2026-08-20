import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """Widen Contest.key from 30 to 32 characters.

    The tinhoctre.vn content migrated from HNOJ includes 14 contests whose keys are 31 or
    32 characters (the source column is varchar(32)). Truncating them would change their
    URLs, so the column is widened to match instead. Widening a varchar is non-destructive.
    """

    dependencies = [
        ("judge", "0268_widen_language_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contest",
            name="key",
            field=models.CharField(
                max_length=32,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        "^[a-z0-9_]+$", "Contest id must be ^[a-z0-9_]+$"
                    )
                ],
                verbose_name="contest id",
            ),
        ),
    ]
