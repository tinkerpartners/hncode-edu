"""Organization.registrant: CASCADE -> SET_NULL, nullable.

Green carries ~700 organizations whose registrant_id points at a Profile that
does not exist (they came in with the imported LQDOJ data; their registrants'
accounts never did). The FK constraint is present but those rows violate it,
so a plain AlterField would fail when Django re-creates the constraint.

Hence three steps:
  1. make the column nullable with no DB constraint (drops the FK),
  2. NULL every registrant_id that has no Profile row,
  3. put the constraint back (now valid).

Step 2 discards the dangling ids. Snapshot them first if they matter
(see PATCHES.md 19 for the query). Reversing past this migration fails while
any organization has a NULL registrant; that is intended.
"""

import django.db.models.deletion
from django.db import migrations, models


def null_dangling_registrants(apps, schema_editor):
    Organization = apps.get_model("judge", "Organization")
    Profile = apps.get_model("judge", "Profile")
    Organization.objects.exclude(registrant_id__isnull=True).exclude(
        registrant_id__in=Profile.objects.values("id")
    ).update(registrant_id=None)


def field(db_constraint):
    return models.ForeignKey(
        db_constraint=db_constraint,
        help_text="User who registered this organization",
        null=True,
        on_delete=django.db.models.deletion.SET_NULL,
        related_name="registrant+",
        to="judge.profile",
        verbose_name="registrant",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("judge", "0273_merge_20260822_2310"),
    ]

    operations = [
        migrations.AlterField(
            model_name="organization", name="registrant", field=field(False)
        ),
        migrations.RunPython(null_dangling_registrants, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="organization", name="registrant", field=field(True)
        ),
    ]
