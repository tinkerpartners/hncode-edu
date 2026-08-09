from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("judge", "0266_homeherosection"),
    ]

    operations = [
        migrations.AlterField(
            model_name="homeherosection",
            name="text",
            field=models.TextField(
                blank=True,
                help_text="Plain text.",
                verbose_name="banner text",
            ),
        ),
    ]
