from django.db import migrations, models


class Migration(migrations.Migration):
    """Widen Language.key so executor names longer than 6 characters can be stored.

    Language.key must equal the judge executor's module name exactly -- the site sends the
    key and the judge looks it up in its executor registry. SCRATCH is 7 characters, so it
    could not be represented at all. common_name is widened alongside it for "Output Only".

    Both widths match the VNOJ schema that the tinhoctre.vn content is migrated from.
    Widening a varchar is non-destructive and needs no data migration.
    """

    dependencies = [
        ("judge", "0267_alter_homeherosection_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="language",
            name="key",
            field=models.CharField(
                help_text=(
                    "The identifier for this language; the same as its executor id "
                    "for judges."
                ),
                max_length=10,
                unique=True,
                verbose_name="short identifier",
            ),
        ),
        migrations.AlterField(
            model_name="language",
            name="common_name",
            field=models.CharField(
                help_text=(
                    "Common name for the language. For example, the common name for "
                    'C++03, C++11, and C++14 would be "C++"'
                ),
                max_length=20,
                verbose_name="common name",
            ),
        ),
    ]
