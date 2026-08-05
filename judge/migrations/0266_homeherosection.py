import django.core.validators
from django.db import migrations, models

import judge.models.interface


class Migration(migrations.Migration):

    dependencies = [
        ("judge", "0265_alter_contest_key_alter_course_slug"),
    ]

    operations = [
        migrations.CreateModel(
            name="HomeHeroSection",
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
                    "enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Show the hero section on the home page.",
                        verbose_name="enabled",
                    ),
                ),
                (
                    "text",
                    models.TextField(
                        blank=True,
                        help_text="Markdown is supported.",
                        verbose_name="banner text",
                    ),
                ),
                (
                    "background_color",
                    models.CharField(
                        default="#00007d",
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                                "Enter a hex color like #1a2b3c.",
                            )
                        ],
                        verbose_name="banner background color",
                    ),
                ),
                (
                    "text_color",
                    models.CharField(
                        default="#ffffff",
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                                "Enter a hex color like #1a2b3c.",
                            )
                        ],
                        verbose_name="banner text color",
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        blank=True,
                        help_text="Large image displayed below the text banner.",
                        null=True,
                        upload_to=judge.models.interface.home_hero_image_path,
                        verbose_name="hero image",
                    ),
                ),
            ],
            options={
                "verbose_name": "home page hero section",
                "verbose_name_plural": "home page hero section",
            },
        ),
    ]
